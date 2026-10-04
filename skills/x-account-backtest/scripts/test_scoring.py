"""Independent numerical/boundary fixtures, with no live-provider dependency."""
from copy import deepcopy
import math
import json
import csv
from pathlib import Path
import tempfile
import unittest

from scoring import audit_spy_calendar, clean_series, publication_date_et, score_calls, write_outcomes
from statistical_summary import build_summary, episode_subset, month_cluster_bootstrap, summarize_group, ticker_balanced, wilson_interval


def call(**kwargs):
    return {"id": "one", "postId": "101", "publishedAt": "2021-10-08T15:00:00Z", "symbol": "ABC", "assetClass": "equity", "instrument": "ABC Inc.",
            "direction": "bullish", "kind": "explicit", "callType": "buy", "thesis": "valuation",
            "evidence": "Buy ABC", "confidence": .9, "issuerMappingValidated": True, "reviewReason": None, **kwargs}


def bars(values):
    days = ["2021-10-08", "2021-10-11", "2021-10-12", "2021-10-13", "2021-10-14", "2021-10-15"]
    return [{"date": day, "open": value, "close": value} for day, value in zip(days, values)]


class ScoringTests(unittest.TestCase):
    def score(self, one=None, prices=None, horizons=(1,), cutoff="2021-10-15"):
        return score_calls([one or call()], prices or {"SPY": bars([99, 100, 105, 106, 107, 108]), "ABC": bars([99, 100, 110, 111, 112, 113])}, cutoff=cutoff, horizons=horizons)["outcomes"]

    def test_friday_next_monday_entry_and_one_session_tuesday_exit(self):
        row = self.score()[0]
        self.assertEqual(row["entryDate"], "2021-10-11")
        self.assertEqual(row["exitDate"], "2021-10-12")
        self.assertAlmostEqual(row["rawReturnPct"], 10)
        self.assertAlmostEqual(row["spyReturnPct"], 5)
        self.assertAlmostEqual(row["relativeReturnPct"], 5)
        self.assertEqual(row["status"], "hit")

    def test_bearish_sign_and_market_relative(self):
        row = self.score(call(direction="bearish", callType="avoid"))[0]
        self.assertAlmostEqual(row["directionalReturnPct"], -10)
        self.assertAlmostEqual(row["relativeReturnPct"], -5)
        self.assertEqual(row["status"], "miss")

    def test_bearish_can_raw_hit_but_relative_miss(self):
        row = self.score(call(direction="bearish"), prices={"SPY": bars([100, 100, 90, 90, 90, 90]), "ABC": bars([100, 100, 95, 95, 95, 95])})[0]
        self.assertEqual((row["status"], row["relativeStatus"]), ("hit", "miss"))
        self.assertAlmostEqual(row["directionalReturnPct"], 5)
        self.assertAlmostEqual(row["relativeReturnPct"], -5)

    def test_exact_zero_is_neutral(self):
        row = self.score(prices={"SPY": bars([100] * 6), "ABC": bars([100] * 6)})[0]
        self.assertEqual((row["status"], row["relativeStatus"]), ("neutral", "neutral"))

    def test_equal_decimal_returns_are_relative_neutral_without_float_noise(self):
        row = self.score(prices={"SPY": bars([100, 200, 210, 210, 210, 210]), "ABC": bars([100, 99, 103.95, 103.95, 103.95, 103.95])})[0]
        self.assertEqual(row["relativeStatus"], "neutral")
        self.assertEqual(row["relativeReturnPct"], 0)

    def test_missing_entry_does_not_shift(self):
        values = {"SPY": bars([99, 100, 105, 106, 107, 108]), "ABC": [row for row in bars([99, 100, 110, 111, 112, 113]) if row["date"] != "2021-10-11"]}
        row = self.score(prices=values)[0]
        self.assertEqual(row["status"], "unscored")
        self.assertEqual(row["entryDate"], "2021-10-11")
        self.assertIsNone(row["directionalReturnPct"])

    def test_missing_exit_delisted_does_not_substitute_last_quote(self):
        values = {"SPY": bars([99, 100, 105, 106, 107, 108]), "ABC": bars([99, 100])}
        row = self.score(prices=values)[0]
        self.assertEqual(row["status"], "unscored")
        self.assertEqual(row["exitDate"], "2021-10-12")
        self.assertIsNone(row["exitOpen"])

    def test_pending_long_horizon_and_cutoff(self):
        row = self.score(horizons=(5,))[0]
        self.assertEqual(row["status"], "pending")
        self.assertIsNone(row["exitDate"])
        row2 = self.score(cutoff="2021-10-11")[0]
        self.assertEqual(row2["status"], "pending")

    def test_post_on_last_observed_day_waits_for_next_session(self):
        row = self.score(call(publishedAt="2021-10-15T23:00:00Z"))[0]
        self.assertEqual(row["status"], "pending")
        self.assertIsNone(row["entryDate"])
        after_cutoff = self.score(call(publishedAt="2021-10-15T23:00:00Z"), cutoff="2021-10-14")[0]
        self.assertEqual(after_cutoff["status"], "pending")

    def test_et_conversion_daylight_saving_and_utc_midnight(self):
        self.assertEqual(publication_date_et("2022-11-06T03:59:00Z"), "2022-11-05")
        self.assertEqual(publication_date_et("2022-11-06T04:00:00Z"), "2022-11-06")
        self.assertEqual(publication_date_et("2022-11-07T04:59:00Z"), "2022-11-06")
        self.assertEqual(publication_date_et("2022-11-07T05:00:00Z"), "2022-11-07")
        self.assertEqual(self.score(call(publishedAt="2021-10-09T01:00:00Z"))[0]["entryDate"], "2021-10-11")

    def test_premarket_same_day_is_also_next_date_by_design(self):
        self.assertEqual(self.score(call(publishedAt="2021-10-11T10:00:00Z"))[0]["entryDate"], "2021-10-12")

    def test_conditional_and_review_calls_never_scored(self):
        self.assertEqual(self.score(call(kind="conditional"))[0]["status"], "unscored")
        self.assertEqual(self.score(call(reviewReason="Ambiguous sarcasm"))[0]["status"], "unscored")
        self.assertEqual(self.score(call(reviewReason="Mapped by reviewer", reviewed=True))[0]["status"], "hit")

    def test_no_timezone_invalid_direction_and_ticker(self):
        self.assertEqual(self.score(call(publishedAt="2021-10-08T10:00:00"))[0]["status"], "unscored")
        self.assertEqual(self.score(call(direction="neutral"))[0]["status"], "unscored")
        self.assertEqual(self.score(call(symbol="Unknown company"))[0]["status"], "unscored")

    def test_duplicate_calls_bars_and_invalid_prices_rejected(self):
        with self.assertRaises(ValueError):
            score_calls([call(), call()], {"SPY": bars([100] * 6)}, cutoff="2021-10-15")
        with self.assertRaises(ValueError):
            clean_series([{"date": "2021-10-08", "open": 100}] * 2, "2021-10-15")
        for value in (0, -1, math.nan, math.inf, True):
            with self.assertRaises(ValueError):
                clean_series([{"date": "2021-10-08", "open": value}], "2021-10-15")

    def test_non_equity_never_scores_even_when_symbol_resembles_a_stock(self):
        self.assertEqual(self.score(call(assetClass="crypto"))[0]["status"], "unscored")
        self.assertEqual(self.score(call(assetClass="macro"))[0]["status"], "unscored")
        self.assertEqual(self.score(call(assetClass="etf"))[0]["status"], "hit")

    def test_source_cohort_date_retained_for_unscored_non_equity(self):
        row = self.score(call(assetClass="macro", publishedAt="2022-01-01T02:00:00Z"))[0]
        self.assertEqual(row["status"], "unscored")
        self.assertEqual(row["publicationDateET"], "2021-12-31")

    def test_missing_spy_session_fails_instead_of_shortening_horizon(self):
        values = {"SPY": bars([99, 100, 105, 106, 107, 108]), "ABC": bars([99, 100, 110, 111, 112, 113])}
        values["SPY"] = [row for row in values["SPY"] if row["date"] != "2021-10-12"]
        with self.assertRaises(ValueError):
            self.score(prices=values)

    def test_unverified_issuer_does_not_score_a_ticker_guess(self):
        self.assertEqual(self.score(call(issuerMappingValidated=False))[0]["status"], "unscored")
        no_proof = call()
        del no_proof["issuerMappingValidated"]
        self.assertEqual(self.score(no_proof)[0]["status"], "unscored")

    def test_leading_spy_gap_cannot_shift_entry_by_one_day(self):
        prices = {"SPY": bars([99, 100, 105, 106, 107, 108])[2:], "ABC": bars([99, 100, 110, 111, 112, 113])}
        with self.assertRaises(ValueError):
            self.score(prices=prices)

    def test_supplied_calendar_is_independent_and_rejects_duplicates(self):
        days = [row["date"] for row in bars([100] * 6)]
        self.assertTrue(audit_spy_calendar(days, days[0], days[-1], expected_sessions=days)["passed"])
        self.assertFalse(audit_spy_calendar(days[1:], days[0], days[-1], expected_sessions=days)["passed"])
        with self.assertRaises(ValueError):
            audit_spy_calendar(days, days[0], days[-1], expected_sessions=days + days[:1])

    def test_noninteger_or_zero_horizons_rejected(self):
        for horizon in (True, 0, -1, 1.5):
            with self.assertRaises(ValueError):
                self.score(horizons=(horizon,))

    def test_zero_call_and_macro_only_produce_no_invented_hit_rate(self):
        prices = {"SPY": bars([100] * 6)}
        for calls in ([], [call(assetClass="macro", symbol=None, issuerMappingValidated=False)]):
            result = score_calls(calls, prices, cutoff="2021-10-15", horizons=(1,))
            summary = build_summary(result, primary=1, iterations=100)
            self.assertEqual(summary["primary"]["scored"], 0)
            self.assertIsNone(summary["primary"]["hitRatePct"])
            self.assertIsNone(summary["primary"]["meanDirectionalReturnPct"])

    def test_no_eligible_us_views_need_no_prices_or_invented_calendar(self):
        cases = ([], [call(assetClass="macro", symbol=None, issuerMappingValidated=False)],
                 [call(issuerMappingValidated=False)], [call(kind="conditional")])
        for calls in cases:
            result = score_calls(calls, {}, cutoff="2021-10-15", horizons=(1, 5))
            self.assertEqual(len(result["outcomes"]), len(calls) * 2)
            self.assertIsNone(result["metadata"]["cutoff"])
            self.assertIsNone(result["metadata"]["calendar"])
            self.assertEqual(result["metadata"]["sessionDates"], [])
            self.assertEqual(result["metadata"]["calendarSessions"], 0)
            self.assertTrue(result["metadata"]["calendarAudit"]["notRequired"])
            for row in result["outcomes"]:
                self.assertEqual(row["status"], "unscored")
                self.assertIsNone(row["entryDate"])
                self.assertIsNone(row["directionalReturnPct"])
                self.assertTrue(row["reason"])
            summary = build_summary(result, primary=5, iterations=100)
            self.assertEqual(summary["primary"]["scored"], 0)
            self.assertIsNone(summary["primary"]["hitRatePct"])
            self.assertIsNone(summary["primary"]["meanDirectionalReturnPct"])

    def test_one_eligible_us_view_still_requires_complete_spy(self):
        for calls in ([call()], [call(id="valid"), call(id="macro", assetClass="macro")]):
            with self.assertRaisesRegex(ValueError, "Full SPY daily bars are required"):
                score_calls(calls, {}, cutoff="2021-10-15", horizons=(1,))

    def test_export_quotes_spreadsheet_formulas_but_keeps_json_verbatim(self):
        result = score_calls([call(evidence="=1+1", callType="@SUM(A1)")],
                             {"SPY": bars([100] * 6), "ABC": bars([100] * 6)},
                             cutoff="2021-10-15", horizons=(1,))
        with tempfile.TemporaryDirectory() as directory:
            write_outcomes(result, directory)
            destination = Path(directory)
            with (destination / "outcomes.csv").open(encoding="utf-8-sig", newline="") as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual(row["evidence"], "'=1+1")
            self.assertEqual(row["callType"], "'@SUM(A1)")
            raw = json.loads((destination / "outcomes.json").read_text(encoding="utf-8"))
            self.assertEqual(raw["outcomes"][0]["evidence"], "=1+1")

    def test_calendar_reports_distinct_missing_and_unexpected_dates(self):
        audit = audit_spy_calendar(["2021-10-08", "2021-10-09"], "2021-10-08", "2021-10-11",
                                   expected_sessions=["2021-10-08", "2021-10-11"], calendar_source="Verified fixture")
        self.assertEqual(audit["missingSessions"], ["2021-10-11"])
        self.assertEqual(audit["unexpectedSessions"], ["2021-10-09"])
        self.assertEqual(audit["source"], "Verified fixture")

    def test_holiday_calendar_carter_new_years_eve_and_juneteenth(self):
        self.assertTrue(audit_spy_calendar(["2025-01-08", "2025-01-10"], "2025-01-08", "2025-01-10")["passed"])
        self.assertTrue(audit_spy_calendar(["2021-12-30", "2021-12-31"], "2021-12-30", "2021-12-31")["passed"])
        self.assertTrue(audit_spy_calendar(["2021-06-18"], "2021-06-18", "2021-06-18")["passed"])
        self.assertTrue(audit_spy_calendar([], "2022-06-20", "2022-06-20")["passed"])


class StatisticsTests(unittest.TestCase):
    def rows(self):
        base = ScoringTests().score()[0]
        result = []
        for index, (status, value) in enumerate((("hit", 10), ("miss", -10), ("neutral", 0), ("pending", None))):
            result.append({**base, "callId": str(index), "id": str(index) + ":21", "horizon": 21,
                           "status": status, "relativeStatus": status, "directionalReturnPct": value,
                           "relativeReturnPct": value, "rawReturnPct": value,
                           "entryDate": f"2021-{10 + index % 3:02d}-11", "entrySessionIndex": index * 5,
                           "publishedAt": f"2021-{10 + index % 3:02d}-08T15:00:00Z"})
        return result

    def test_denominator_includes_neither_neutral_nor_pending(self):
        summary = summarize_group(self.rows())
        self.assertEqual((summary["hits"], summary["misses"], summary["neutral"], summary["pending"]), (1, 1, 1, 1))
        self.assertEqual(summary["binaryDenominator"], 2)
        self.assertEqual(summary["scored"], 3)
        self.assertEqual(summary["hitRatePct"], 50)
        self.assertEqual(summary["meanDirectionalReturnPct"], 0)
        self.assertEqual(summary["meanWinnerPct"], 10)
        self.assertEqual(summary["meanLoserPct"], -10)

    def test_wilson_known_fixture(self):
        lower, upper = wilson_interval(5, 10)
        self.assertAlmostEqual(lower, 23.6593090513, places=8)
        self.assertAlmostEqual(upper, 76.3406909487, places=8)
        self.assertIsNone(wilson_interval(0, 0))

    def test_bootstrap_reproducible_and_does_not_treat_single_month_as_precision(self):
        self.assertEqual(month_cluster_bootstrap(self.rows(), iterations=200), month_cluster_bootstrap(self.rows(), iterations=200))
        one_month = [{**row, "entryDate": "2021-10-11"} for row in self.rows()]
        self.assertIsNone(month_cluster_bootstrap(one_month, iterations=200)["hitRatePct95"])

    def test_bootstrap_preserves_whole_common_shock_month(self):
        base = self.rows()[0]
        rows = []
        for index in range(20):
            rows.append({**base, "callId": str(index), "entryDate": "2021-10-11" if index < 10 else "2021-11-11",
                         "status": "hit" if index < 10 else "miss", "relativeStatus": "hit" if index < 10 else "miss",
                         "directionalReturnPct": 10 if index < 10 else -10, "relativeReturnPct": 10 if index < 10 else -10})
        result = month_cluster_bootstrap(rows, iterations=200)
        self.assertEqual(result["hitRatePct95"], [0, 100])
        self.assertEqual(result["meanDirectionalReturnPct95"], [-10, 10])

    def test_cost_sensitivity_net_zero_remains_neutral(self):
        base = self.rows()[0]
        rows = [{**base, "directionalReturnPct": .25}, {**base, "directionalReturnPct": 1},
                {**base, "status": "neutral", "directionalReturnPct": 0}]
        metric = summarize_group(rows)
        self.assertEqual(metric["after25bpBinaryDenominator"], 2)
        self.assertEqual(metric["hitRateAfter25bpPct"], 50)

    def test_ticker_balanced_avoids_averaging_call_counts(self):
        rows = self.rows()
        rows = [rows[0], {**rows[0], "callId": "extra"}, {**rows[1], "symbol": "OTHER", "canonicalSymbol": "OTHER", "securityId": "OTHER"}]
        self.assertAlmostEqual(summarize_group(rows)["hitRatePct"], 200 / 3)
        self.assertEqual(ticker_balanced(rows)["hitRatePct"], 50)

    def test_verified_rename_identity_unifies_counts_balance_and_episodes(self):
        base = self.rows()[0]
        rows = [{**base, "callId": "old", "symbol": "FB", "canonicalSymbol": "META", "securityId": "figi-meta", "entrySessionIndex": 10, "publishedAt": "2021-10-01T15:00:00Z"},
                {**base, "callId": "new", "symbol": "META", "canonicalSymbol": "META", "securityId": "figi-meta", "entrySessionIndex": 15, "publishedAt": "2021-10-08T15:00:00Z"}]
        metric = summarize_group(rows)
        self.assertEqual(metric["uniqueSecurities"], 1)
        self.assertEqual(metric["uniqueHistoricalTickers"], 2)
        self.assertEqual(ticker_balanced(rows)["tickers"], 1)
        self.assertEqual(len(episode_subset(rows)), 1)

    def test_episode_membership_independent_of_outcomes(self):
        rows = self.rows()
        rows = [{**rows[0], "callId": "first", "entrySessionIndex": 10, "publishedAt": "2021-10-08T00:00:00Z", "status": "unscored"},
                {**rows[0], "callId": "repeat", "entrySessionIndex": 20, "publishedAt": "2021-10-20T00:00:00Z"},
                {**rows[1], "callId": "later", "entrySessionIndex": 31, "publishedAt": "2021-11-09T00:00:00Z"}]
        kept = episode_subset(rows)
        self.assertEqual([row["callId"] for row in kept], ["first", "later"])
        altered = deepcopy(rows)
        for row in altered:
            row["status"] = "miss"
        self.assertEqual([row["callId"] for row in episode_subset(altered)], ["first", "later"])

    def test_empty_groups_and_duplicate_outcomes(self):
        result = build_summary([], iterations=200)
        self.assertIsNone(result["primary"]["hitRatePct"])
        self.assertEqual(result["primary"]["calls"], 0)
        rows = self.rows()
        with self.assertRaises(ValueError):
            build_summary(rows + [rows[0]], iterations=200)

    def test_all_horizon_and_group_counts_reconcile(self):
        rows = self.rows()
        result = build_summary(rows, iterations=200)
        self.assertEqual(result["primary"]["calls"], len(rows))
        for dimension in result["byGroup"].values():
            self.assertEqual(sum(group["calls"] for group in dimension.values()), len(rows))

    def test_source_confidence_and_explicit_sensitivities_leave_primary_unchanged(self):
        rows = self.rows()[:2]
        rows[0].update(confidence=.95, kind="inferred")
        rows[1].update(confidence=.89, kind="explicit")
        result = build_summary(rows, iterations=200)
        self.assertEqual(result["primary"]["hitRatePct"], 50)
        self.assertEqual(result["highConfidenceSensitivity"]["summary"]["hitRatePct"], 100)
        self.assertEqual(result["explicitOnlySensitivity"]["summary"]["hitRatePct"], 0)

    def test_common_complete_horizon_cohort_uses_fixed_ids(self):
        base = self.rows()
        rows = base + [{**row, "id": row["callId"] + ":252", "horizon": 252,
                        "status": "pending" if row["callId"] == "0" else row["status"]} for row in base]
        result = build_summary(rows, iterations=200)
        common = result["commonMatureCohort"]
        self.assertEqual(common["callCount"], 2)  # One miss and one neutral mature at both horizons.
        self.assertEqual(common["byHorizon"]["21"]["calls"], 2)
        self.assertEqual(common["byHorizon"]["252"]["calls"], 2)
        self.assertEqual(result["primary"]["calls"], 4)

    def test_mature_missing_price_bounds_do_not_count_future_horizons(self):
        rows = self.rows()[:2]
        rows += [{**rows[0], "callId": "missing", "status": "unscored", "entrySessionIndex": 10},
                 {**rows[0], "callId": "future_missing", "status": "unscored", "entrySessionIndex": 99}]
        result = build_summary({"metadata": {"calendarSessions": 100}, "outcomes": rows}, iterations=200)
        bound = result["missingPriceSensitivity"]["byHorizon"]["21"]
        self.assertEqual(bound["maturePriceUnscored"], 1)
        self.assertAlmostEqual(bound["allMissingMissHitRatePct"], 100/3)
        self.assertAlmostEqual(bound["allMissingHitHitRatePct"], 200/3)
        self.assertEqual(result["primary"]["hitRatePct"], 50)

    def test_configured_primary_seed_and_episode_spacing(self):
        rows = [{**row, "horizon": 5} for row in self.rows()]
        result = build_summary(rows, primary=5, episode_spacing=6, iterations=200, seed=42)
        self.assertEqual(result["metadata"]["primaryHorizon"], 5)
        self.assertEqual(result["metadata"]["episodeSpacingSessions"], 6)
        self.assertEqual(result["metadata"]["bootstrapSeed"], 42)
        self.assertEqual(result["primary"]["monthClusterBootstrap"]["seed"], 42)
        with self.assertRaises(ValueError):
            build_summary(rows, primary=21, iterations=200)

    def test_episodes_sort_real_timestamps_rather_than_offset_strings(self):
        base = self.rows()[0]
        rows = [{**base, "callId": "later", "publishedAt": "2021-10-08T15:00:00+00:00", "entrySessionIndex": 10},
                {**base, "callId": "earlier", "publishedAt": "2021-10-08T16:00:00+02:00", "entrySessionIndex": 10}]
        self.assertEqual([row["callId"] for row in episode_subset(rows)], ["earlier"])

    def test_share_classes_and_acquirers_remain_separate_without_proven_alias(self):
        base = self.rows()[0]
        rows = [{**base, "callId": str(i), "symbol": symbol, "canonicalSymbol": symbol, "securityId": None}
                for i, symbol in enumerate(("GOOG", "GOOGL", "ACQUIRED", "ACQUIRER"))]
        self.assertEqual(summarize_group(rows)["uniqueSecurities"], 4)
        self.assertEqual(len(episode_subset(rows)), 4)


if __name__ == "__main__":
    unittest.main()
