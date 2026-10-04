"""Offline provider regression fixtures. No credentials, user posts or network."""
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from retrieve_posts import Archive, ProviderError, month_ranges, normalize_post, read_study, request_json
from fetch_prices import fetch_symbol, mapping_record, normalize_bar, safe_next_url, select_symbols
from fetch_crypto_prices import fetch_crypto, public_request

UTC = timezone.utc


def raw_post(post_id="200", author="123", stamp="2026-01-05T14:00:00Z", text="Synthetic bullish view."):
    return {"id_str": post_id, "full_text": text, "tweet_created_at": stamp, "user": {"id_str": author}, "is_quote_status": True, "quoted_status": {"full_text": "Foreign bearish quote."}}


def mapped_call(**changes):
    call = {"id": "200:0", "symbol": "ACME", "publishedAt": "2026-01-05T14:00:00Z", "assetClass": "equity", "issuerMappingValidated": True, "securityId": "FIGI:SYNTHETIC",
            "issuerReference": {"asOf": "2026-01-05", "status": "ok", "sourceUrl": "https://api.massive.com/v3/reference/tickers/ACME?date=2026-01-05", "responseSha256": "a" * 64,
                                "metadata": {"ticker": "ACME", "market": "stocks", "locale": "us", "type": "CS", "composite_figi": "SYNTHETIC", "name": "Synthetic Company", "list_date": "2025-01-01"}}}
    return {**call, **changes}


def bar(day, opening=100):
    stamp = datetime.fromisoformat(day + "T05:00:00+00:00")
    return {"t": int(stamp.timestamp() * 1000), "o": opening, "c": opening + 1, "h": opening + 2, "l": opening - 1}


class ArchiveTests(unittest.TestCase):
    def test_month_boundaries_follow_new_york_dst(self):
        first, last = datetime(2026, 3, 1, 5, tzinfo=UTC), datetime(2026, 4, 1, 4, tzinfo=UTC)
        ranges = list(month_ranges(first, last))
        self.assertEqual(ranges, [("2026-03", first, last)])

    def test_own_quote_text_kept_foreign_text_ignored(self):
        p, reason = normalize_post(raw_post(), "fixture", "123", datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC))
        self.assertIsNone(reason)
        self.assertEqual(p["text"], "Synthetic bullish view.")
        self.assertTrue(p["isQuote"])

    def test_wrong_owner_retweet_and_truncation_rejected(self):
        first, last = datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC)
        for value, expected in ((raw_post(author="999"), "wrong_author_id"), ({**raw_post(), "retweeted_status": {}}, "native_retweet"), ({**raw_post(), "truncated": True}, "malformed_or_truncated")):
            self.assertEqual(normalize_post(value, "fixture", "123", first, last)[1], expected)

    def test_failure_resume_uses_checkpoint_cursor_without_duplication(self):
        with tempfile.TemporaryDirectory() as root:
            a = Archive(root, "fixture", datetime(2026, 1, 1, 5, tzinfo=UTC), datetime(2026, 2, 1, 5, tzinfo=UTC), author_id="123")
            responses = iter([{"tweets": [raw_post()], "next_cursor": "NEXT"}, ProviderError("entitlement_denied")])
            def first(params):
                result = next(responses)
                if isinstance(result, Exception):
                    raise result
                return result
            result = a.retrieve(first)
            self.assertEqual(result["months"][0]["state"], "failed")
            self.assertEqual(result["totals"]["posts"], 1)
            def resume(params):
                self.assertEqual(params["cursor"], "NEXT")
                return {"tweets": [], "next_cursor": None}
            result = a.retrieve(resume)
            self.assertEqual(result["totals"]["posts"], 1)
            self.assertTrue(result["searchExhaustedEveryMonth"])
            self.assertFalse(result["completeHistoricalCensus"])
            a.close()

    def test_cursor_exhaustion_uses_max_id_and_stall_is_partial(self):
        with tempfile.TemporaryDirectory() as root:
            a = Archive(root, "fixture", datetime(2026, 1, 1, 5, tzinfo=UTC), datetime(2026, 2, 1, 5, tzinfo=UTC), author_id="123")
            count = 0
            def request(params):
                nonlocal count
                count += 1
                if count > 1:
                    self.assertIn("max_id:199", params["query"])
                return {"tweets": [raw_post()], "next_cursor": None}
            result = a.retrieve(request)
            self.assertEqual(count, 2)
            self.assertEqual(result["months"][0]["state"], "pagination_stalled")
            self.assertEqual(result["totals"]["posts"], 1)

            a.close()

    def test_page_cap_is_resumable_not_claimed_complete(self):
        with tempfile.TemporaryDirectory() as root:
            a = Archive(root, "fixture", datetime(2026, 1, 1, 5, tzinfo=UTC), datetime(2026, 2, 1, 5, tzinfo=UTC), author_id="123")
            result = a.retrieve(lambda params: {"tweets": [raw_post()], "next_cursor": "A"}, page_cap=1)
            self.assertEqual(result["months"][0]["state"], "page_cap_paused")
            self.assertFalse(result["searchExhaustedEveryMonth"])
            a.close()

    def test_import_owner_failure_rolls_back_and_import_is_never_search_exhaustion(self):
        with tempfile.TemporaryDirectory() as root:
            a = Archive(root, "fixture", datetime(2026, 1, 1, 5, tzinfo=UTC), datetime(2026, 2, 1, 5, tzinfo=UTC), mode="import", author_id="123")
            normalized = {"id": "200", "text": "Synthetic view", "publishedAt": "2026-01-05T14:00:00Z", "authorId": "123"}
            with self.assertRaises(ValueError):
                a.import_posts([normalized, {**normalized, "id": "201", "authorId": "999"}])
            self.assertEqual(a.export()["totals"]["posts"], 0)
            result = a.import_posts([normalized])
            self.assertEqual(result["coverageStatus"], "imported")
            self.assertFalse(result["searchExhaustedEveryMonth"])

            a.close()

    def test_checkpoint_refuses_another_account(self):
        with tempfile.TemporaryDirectory() as root:
            a = Archive(root, "fixture", datetime(2026, 1, 1, 5, tzinfo=UTC), datetime(2026, 2, 1, 5, tzinfo=UTC), author_id="123")
            with self.assertRaises(ValueError):
                Archive(root, "another", datetime(2026, 1, 1, 5, tzinfo=UTC), datetime(2026, 2, 1, 5, tzinfo=UTC), author_id="999")
            a.close()

    def test_network_destination_rejected_before_credentials_sent(self):
        with self.assertRaises(ProviderError) as result:
            request_json("https://evil.example/twitter/search", "synthetic-secret")
        self.assertEqual(result.exception.code, "unsafe_destination")


class PriceTests(unittest.TestCase):
    def test_literal_ticker_or_wrong_date_is_not_mapping(self):
        self.assertIsNotNone(mapping_record({"symbol": "ACME", "issuerMappingValidated": True, "assetClass": "equity", "publishedAt": "2026-01-05T14:00:00Z"})[1])
        c = mapped_call()
        c["issuerReference"]["asOf"] = "2026-01-06"
        self.assertEqual(mapping_record(c)[1], "invalid_dated_issuer_proof")
        self.assertEqual(mapping_record(mapped_call(securityId="FIGI:WRONG"))[1], "security_identity_mismatch")

    def test_approved_mapping_and_listing_bounds(self):
        c = mapped_call()
        c["issuerReference"]["metadata"].update(list_date="2026-01-02", delisted_utc="2026-01-08T00:00:00Z")
        selected, excluded = select_symbols([c], {"startDate": "2026-01-01", "cutoff": "2026-01-30"})
        self.assertEqual((selected["ACME"]["start"], selected["ACME"]["end"]), ("2026-01-02", "2026-01-08"))
        self.assertIn("SPY", selected)
        self.assertFalse(excluded)

    def test_next_url_cannot_leak_key_or_change_host_ticker(self):
        clean = safe_next_url("https://api.massive.com/v2/aggs/ticker/ACME/range/1/day/2026-01-01/2026-01-31?cursor=A&apiKey=synthetic-secret", "ACME")
        self.assertNotIn("secret", clean)
        for value in ("https://evil.example/v2/aggs/ticker/ACME/range/1/day/a/b", "https://api.massive.com/v2/aggs/ticker/OTHER/range/1/day/a/b"):
            with self.assertRaises(ProviderError):
                safe_next_url(value, "ACME")

    def test_partial_price_failure_resumes_and_preserves_split_confirmation(self):
        with tempfile.TemporaryDirectory() as root:
            item = {"symbol": "ACME", "start": "2026-01-01", "end": "2026-01-10", "proofs": [], "benchmark": False}
            next_url = "https://api.massive.com/v2/aggs/ticker/ACME/range/1/day/2026-01-01/2026-01-10?cursor=N"
            count = 0
            def request(url):
                nonlocal count
                count += 1
                if count == 2:
                    raise ProviderError("network")
                return {"ticker": "ACME", "status": "OK", "adjusted": True, "results": [bar("2026-01-05")], "next_url": next_url}
            first = fetch_symbol(item, root, request)
            self.assertEqual(first["status"], "partial")
            self.assertEqual(len(first["rows"]), 1)
            def resume(url):
                self.assertEqual(url, next_url)
                return {"ticker": "ACME", "status": "DELAYED", "adjusted": True, "results": [bar("2026-01-06", 101)]}
            second = fetch_symbol(item, root, resume)
            self.assertEqual(second["status"], "ok")
            self.assertEqual(len(second["rows"]), 2)

    def test_unadjusted_history_and_duplicate_bars_fail(self):
        with tempfile.TemporaryDirectory() as root:
            item = {"symbol": "ACME", "start": "2026-01-01", "end": "2026-01-10", "proofs": [], "benchmark": False}
            bad = fetch_symbol(item, root, lambda url: {"ticker": "ACME", "status": "OK", "adjusted": False, "results": []})
            self.assertEqual(bad["status"], "failed")
            duplicate = fetch_symbol(item, root, lambda url: {"ticker": "ACME", "status": "OK", "adjusted": True, "results": [bar("2026-01-05"), bar("2026-01-05")]}, refresh=True)
            self.assertEqual(duplicate["error"], "duplicate_bar")

    def test_empty_history_remains_empty_and_same_day_live_bar_omitted(self):
        with tempfile.TemporaryDirectory() as root:
            item = {"symbol": "ACME", "start": "2026-01-01", "end": "2026-01-10", "proofs": [], "benchmark": False}
            result = fetch_symbol(item, root, lambda url: {"ticker": "ACME", "status": "OK", "adjusted": True, "results": []})
            self.assertEqual(result["rows"], [])
            self.assertTrue(result["emptyHistory"])
        self.assertIsNone(normalize_bar(bar("2026-01-05"), "2026-01-01", "2026-01-10", datetime(2026, 1, 5, 20, tzinfo=UTC)))


class CryptoPriceTests(unittest.TestCase):
    def test_product_identity_must_be_exact_usd(self):
        with tempfile.TemporaryDirectory() as root:
            doc = fetch_crypto("BTC", "2026-01-01", "2026-01-03", root, lambda path, params=None: {"id": "BTC-USDT", "base_currency": "BTC", "quote_currency": "USDT"})
            self.assertEqual(doc["error"], "unverified_usd_product")
            self.assertEqual(doc["rows"], [])

    def test_crypto_failure_resume_and_missing_calendar_day(self):
        with tempfile.TemporaryDirectory() as root:
            def fail(path, params=None):
                if params is None:
                    return {"id": "BTC-USD", "base_currency": "BTC", "quote_currency": "USD"}
                raise ProviderError("network")
            first = fetch_crypto("BTC", "2026-01-01", "2026-01-03", root, fail)
            self.assertEqual(first["status"], "failed")
            self.assertIsNotNone(first["productReference"])
            def resume(path, params=None):
                self.assertIsNotNone(params)
                self.assertEqual(params["granularity"], 86400)
                return [[int(datetime(2026, 1, d, tzinfo=UTC).timestamp()), 99, 102, 100, 101, 50] for d in (1, 3)]
            doc = fetch_crypto("BTC", "2026-01-01", "2026-01-03", root, resume)
            self.assertEqual(doc["status"], "ok")
            self.assertEqual(doc["missingDates"], ["2026-01-02"])
            self.assertFalse(doc["calendarComplete"])
            self.assertEqual(len(doc["rows"]), 2)

    def test_crypto_unsafe_product_path_rejected(self):
        with self.assertRaises(ProviderError):
            public_request("/products/BTC-USD/../../private")


if __name__ == "__main__":
    unittest.main()
