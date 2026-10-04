"""Crypto timing, eligibility and missing-price fixtures; no live providers."""
import math
import unittest

from crypto_scoring import score_crypto


def call(**changes):
    return {"id": "crypto-1", "postId": "1", "publishedAt": "2022-01-01T20:00:00Z", "symbol": "BTC",
            "assetClass": "crypto", "direction": "bullish", "kind": "explicit", "reviewReason": None, **changes}


def bars():
    return [{"date": "2022-01-03", "open": 100}, {"date": "2022-01-04", "open": 120},
            {"date": "2022-01-05", "open": 130}]


class CryptoTests(unittest.TestCase):
    def score(self, one=None, series=None, cutoff="2022-01-05"):
        return score_crypto([one or call()], series if series is not None else {"BTC": bars()},
                            cutoff=cutoff, horizons=(1,), primary=1)

    def test_entry_is_after_complete_new_york_publication_day(self):
        early = self.score(call(publishedAt="2022-01-01T05:01:00Z"))["outcomes"][0]
        late = self.score(call(publishedAt="2022-01-02T04:59:00Z"))["outcomes"][0]
        for row in (early, late):
            self.assertEqual(row["entryDate"], "2022-01-03")
            self.assertEqual(row["exitDate"], "2022-01-04")
            self.assertEqual(row["directionalReturnPct"], 20)

    def test_dst_boundary_uses_local_publication_day(self):
        row = self.score(call(publishedAt="2022-03-13T03:30:00Z"), cutoff="2022-03-15")["outcomes"][0]
        self.assertEqual(row["publicationDateET"], "2022-03-12")
        self.assertEqual(row["entryDate"], "2022-03-14")

    def test_bearish_sign(self):
        row = self.score(call(direction="bearish"))["outcomes"][0]
        self.assertEqual((row["status"], row["directionalReturnPct"]), ("miss", -20))

    def test_invalid_direction_never_defaults_to_short(self):
        self.assertEqual(self.score(call(direction="unknown"))["outcomes"][0]["status"], "unscored")

    def test_conditional_excluded_and_review_required_stay_unscored(self):
        for change in ({"kind": "conditional"}, {"excluded": True}, {"reviewReason": "Could be sarcasm"}):
            self.assertEqual(self.score(call(**change))["outcomes"][0]["status"], "unscored")
        self.assertEqual(self.score(call(reviewReason="Reviewed source", reviewed=True))["outcomes"][0]["status"], "hit")

    def test_timezone_free_timestamp_is_unscored(self):
        self.assertEqual(self.score(call(publishedAt="2022-01-01T20:00:00"))["outcomes"][0]["status"], "unscored")

    def test_exact_required_bar_dates_no_forward_fill(self):
        for kept in (bars()[1:], bars()[:1] + bars()[2:]):
            row = self.score(series={"BTC": kept})["outcomes"][0]
            self.assertEqual(row["status"], "unscored")
            self.assertIsNone(row["rawReturnPct"])

    def test_pending_horizon_not_misreported_as_data_gap(self):
        row = self.score(series={}, cutoff="2022-01-03")["outcomes"][0]
        self.assertEqual(row["status"], "pending")

    def test_corrupt_candles_and_duplicate_ids_rejected(self):
        with self.assertRaises(ValueError):
            self.score(series={"BTC": bars() + bars()[:1]})
        for value in (True, 0, -1, math.nan, math.inf):
            with self.assertRaises(ValueError):
                self.score(series={"BTC": [{"date": "2022-01-03", "open": value}]})
        with self.assertRaises(ValueError):
            score_crypto([call(), call()], {"BTC": bars()}, cutoff="2022-01-05", horizons=(1,), primary=1)

    def test_primary_and_horizons_are_configured(self):
        for horizons, primary in (((1,), 30), ((True,), 1), ((0,), 0)):
            with self.assertRaises(ValueError):
                score_crypto([call()], {"BTC": bars()}, cutoff="2022-01-05", horizons=horizons, primary=primary)

    def test_neutral_price_move_excluded_from_binary_denominator(self):
        same = [{"date": row["date"], "open": 100} for row in bars()]
        result = self.score(series={"BTC": same})
        self.assertEqual(result["summary"]["primary"]["neutral"], 1)
        self.assertEqual(result["summary"]["primary"]["binaryDenominator"], 0)
        self.assertEqual(result["summary"]["primary"]["meanDirectionalReturnPct"], 0)
        self.assertIsNone(result["summary"]["primary"]["hitRatePct"])

    def test_stock_views_not_pooled_into_crypto(self):
        result = self.score(call(assetClass="equity"))
        self.assertEqual(result["outcomes"], [])
        self.assertEqual(result["summary"]["primary"]["calls"], 0)


if __name__ == "__main__":
    unittest.main()
