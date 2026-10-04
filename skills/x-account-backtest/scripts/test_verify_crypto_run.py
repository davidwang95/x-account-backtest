"""Independent audit fixtures covering valid, tampered, missing and pending crypto."""
from collections import Counter
import copy
import json
from pathlib import Path
import statistics
import tempfile
import unittest

from verify_crypto_run import reconcile_crypto


def metric(rows):
    counts = Counter(r["status"] for r in rows)
    values = [r["directionalReturnPct"] for r in rows if r["status"] in {"hit", "miss", "neutral"}]
    binary = counts["hit"] + counts["miss"]
    return {"calls": len(rows), "scored": len(values), "hits": counts["hit"], "misses": counts["miss"],
            "neutral": counts["neutral"], "pending": counts["pending"], "unscored": counts["unscored"],
            "binaryDenominator": binary, "hitRatePct": 100 * counts["hit"] / binary if binary else None,
            "meanDirectionalReturnPct": statistics.mean(values) if values else None,
            "medianDirectionalReturnPct": statistics.median(values) if values else None}


def fixture():
    calls = []
    for cid, symbol, stamp, direction, kind in [
        ("bull", "BTC", "2026-01-02T02:00:00Z", "bullish", "explicit"),
        ("bear", "BTC", "2026-01-02T02:00:00Z", "bearish", "inferred"),
        ("zero", "BTC", "2026-01-03T14:00:00Z", "bullish", "explicit"),
        ("gap", "ETH", "2026-01-02T02:00:00Z", "bullish", "explicit"),
        ("conditional", "BTC", "2026-01-02T02:00:00Z", "bullish", "conditional"),
        ("recent", "BTC", "2026-01-05T14:00:00Z", "bullish", "explicit"),
    ]:
        calls.append({"id": cid, "postId": cid, "publishedAt": stamp, "postUrl": "https://x.com/fixture/status/" + cid,
                      "symbol": symbol, "direction": direction, "kind": kind, "assetClass": "crypto", "reviewReason": ""})
    by_id = {c["id"]: c for c in calls}
    rows = []
    # Gold rows are explicit fixtures rather than output from the production scorer.
    for cid, horizon, publication, entry, exit_day, status, opening, closing, raw, signed in [
        ("bull", 1, "2026-01-01", "2026-01-03", "2026-01-04", "hit", 100, 110, 10, 10),
        ("bull", 3, "2026-01-01", "2026-01-03", "2026-01-06", "neutral", 100, 100, 0, 0),
        ("bear", 1, "2026-01-01", "2026-01-03", "2026-01-04", "miss", 100, 110, 10, -10),
        ("bear", 3, "2026-01-01", "2026-01-03", "2026-01-06", "neutral", 100, 100, 0, 0),
        ("zero", 1, "2026-01-03", "2026-01-05", "2026-01-06", "neutral", 100, 100, 0, 0),
        ("zero", 3, "2026-01-03", "2026-01-05", "2026-01-08", "pending", None, None, None, None),
        ("gap", 1, "2026-01-01", "2026-01-03", "2026-01-04", "unscored", None, None, None, None),
        ("gap", 3, "2026-01-01", "2026-01-03", "2026-01-06", "hit", 50, 60, 20, 20),
        ("conditional", 1, "2026-01-01", None, None, "unscored", None, None, None, None),
        ("conditional", 3, "2026-01-01", None, None, "unscored", None, None, None, None),
        ("recent", 1, "2026-01-05", "2026-01-07", "2026-01-08", "pending", None, None, None, None),
        ("recent", 3, "2026-01-05", "2026-01-07", "2026-01-10", "pending", None, None, None, None),
    ]:
        call = by_id[cid]
        rows.append({**{k: call[k] for k in ("postId", "publishedAt", "postUrl", "symbol", "direction", "kind", "assetClass")},
                     "id": cid + ":" + str(horizon), "callId": cid, "horizonCalendarDays": horizon,
                     "publicationDateET": publication, "entryDate": entry, "exitDate": exit_day,
                     "status": status, "entryOpen": opening, "exitOpen": closing, "rawReturnPct": raw, "directionalReturnPct": signed})
    primary = [r for r in rows if r["horizonCalendarDays"] == 1]
    payload = {"metadata": {"horizonsCalendarDays": [1, 3], "primaryCalendarDays": 1, "cutoff": "2026-01-06"}, "outcomes": rows,
               "summary": {"primary": metric(primary), "byHorizon": {str(h): metric([r for r in rows if r["horizonCalendarDays"] == h]) for h in (1, 3)},
                           "byDirection": {d: metric([r for r in primary if r["direction"] == d]) for d in ("bullish", "bearish")}}}
    prices = {"BTC": [{"date": d, "open": v} for d, v in (("2026-01-03", 100), ("2026-01-04", 110), ("2026-01-05", 100), ("2026-01-06", 100))],
              "ETH": [{"date": "2026-01-03", "open": 50}, {"date": "2026-01-06", "open": 60}]}
    return calls, payload, prices


class CryptoAuditTests(unittest.TestCase):
    def audit(self, change=None):
        calls, payload, prices = fixture()
        if change:
            change(calls, payload, prices)
        with tempfile.TemporaryDirectory() as root:
            data = Path(root)
            series = data / "prices"
            series.mkdir()
            for name, value in (("study.json", {"cryptoHorizons": [1, 3], "cryptoPrimary": 1, "cutoff": "2026-01-06"}),
                                ("calls.json", calls), ("crypto-outcomes.json", payload)):
                (data / name).write_text(json.dumps(value), encoding="utf-8")
            for symbol, bars in prices.items():
                (series / (symbol + ".json")).write_text(json.dumps({"rows": bars}), encoding="utf-8")
            return reconcile_crypto(data, series)

    def test_explicit_gold_rows_pass_with_neutral_missing_and_pending(self):
        result = self.audit()
        self.assertTrue(result["passed"], result["errors"])
        self.assertGreater(result["checks"], 200)
        self.assertEqual(set(result["cryptoPriceCacheHashes"]), {"BTC.json", "ETH.json"})

    def test_tampered_signed_return_is_detected(self):
        result = self.audit(lambda calls, payload, prices: payload["outcomes"][2].update(directionalReturnPct=10, status="hit"))
        self.assertFalse(result["passed"])
        self.assertTrue(any("signed return" in message for message in result["errors"]))

    def test_utc_day_shortcut_and_shifted_exit_are_detected(self):
        result = self.audit(lambda calls, payload, prices: payload["outcomes"][0].update(entryDate="2026-01-02", exitDate="2026-01-03"))
        self.assertFalse(result["passed"])
        self.assertTrue(any("full New York day" in message for message in result["errors"]))

    def test_suppressed_source_or_horizon_is_detected(self):
        result = self.audit(lambda calls, payload, prices: payload["outcomes"].pop())
        self.assertFalse(result["passed"])
        self.assertTrue(any("coverage mismatch" in message for message in result["errors"]))

    def test_missing_candle_cannot_be_called_a_loss(self):
        result = self.audit(lambda calls, payload, prices: payload["outcomes"][6].update(status="miss", directionalReturnPct=-100))
        self.assertFalse(result["passed"])
        self.assertTrue(any("Missing crypto required candle" in message for message in result["errors"]))

    def test_hit_denominator_includes_neutral_wrongly_is_detected(self):
        result = self.audit(lambda calls, payload, prices: payload["summary"]["primary"].update(binaryDenominator=3))
        self.assertFalse(result["passed"])
        self.assertTrue(any("binary denominator" in message for message in result["errors"]))


if __name__ == "__main__":
    unittest.main()
