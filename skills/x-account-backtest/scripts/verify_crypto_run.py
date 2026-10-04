#!/usr/bin/env python3
"""Independent crypto source/date/Decimal-return reconciliation; no scorer imports."""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import statistics
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def reconcile_crypto(data_dir, series_dir):
    """Audit every crypto call/horizon, including gaps, maturity and zero outcomes."""
    data_dir, series_dir = Path(data_dir), Path(series_dir)
    payload, study = read(data_dir / "crypto-outcomes.json"), read(data_dir / "study.json")
    calls = read(data_dir / "calls.json")
    calls = calls.get("calls") if isinstance(calls, dict) else calls
    source = {c["id"]: c for c in calls if c.get("assetClass") == "crypto"}
    rows, metadata = payload["outcomes"], payload["metadata"]
    horizons, primary, cutoff = metadata["horizonsCalendarDays"], metadata["primaryCalendarDays"], date.fromisoformat(metadata["cutoff"])
    errors, checks = [], 0
    def check(ok, message):
        nonlocal checks
        checks += 1
        if not ok:
            errors.append(message)
    def close(actual, expected):
        if actual is None or isinstance(actual, bool):
            return False
        try:
            value = Decimal(str(actual))
            return value.is_finite() and abs(value - expected) < Decimal("1e-10")
        except (InvalidOperation, TypeError, ValueError):
            return False
    check(horizons == study["cryptoHorizons"], "Crypto horizons differ from frozen study")
    check(primary == study["cryptoPrimary"], "Crypto primary differs from frozen study")
    check(metadata["cutoff"] == study["cutoff"], "Crypto cutoff differs from frozen study")
    expected_pairs = {(cid, h) for cid in source for h in horizons}
    actual_pairs = [(row.get("callId"), row.get("horizonCalendarDays")) for row in rows]
    check(len(actual_pairs) == len(set(actual_pairs)), "Duplicate crypto call/horizon rows")
    check(set(actual_pairs) == expected_pairs, "Crypto call/horizon coverage mismatch")
    prices, cache_hashes = {}, {}
    for path in sorted(series_dir.glob("*.json")):
        value = read(path)
        bars = value if isinstance(value, list) else value.get("rows", value.get("bars")) if isinstance(value, dict) else None
        if not isinstance(bars, list):
            continue
        per_day = {}
        for bar in bars:
            try:
                day, opening = bar["date"], Decimal(str(bar["open"]))
                date.fromisoformat(day)
                valid = opening.is_finite() and opening > 0 and day not in per_day and not isinstance(bar["open"], bool)
                check(valid, "Invalid/duplicate crypto source candle: " + path.stem)
                if valid and day <= metadata["cutoff"]:
                    per_day[day] = opening
            except (KeyError, TypeError, ValueError, InvalidOperation):
                check(False, "Malformed crypto source candle: " + path.stem)
        prices[path.stem.upper()] = per_day
        cache_hashes[path.name] = file_hash(path)
    for row in rows:
        label = str(row.get("callId")) + "/" + str(row.get("horizonCalendarDays"))
        call = source.get(row.get("callId"))
        check(call is not None, "Unknown crypto source call: " + label)
        if call is None:
            continue
        for field in ("postId", "publishedAt", "postUrl", "symbol", "direction", "kind", "assetClass"):
            check(row.get(field) == call.get(field), "Crypto source attribution differs: " + label + "/" + field)
        try:
            stamp = datetime.fromisoformat(call["publishedAt"].replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ValueError
            publication = stamp.astimezone(NY).date()
        except (ValueError, TypeError, KeyError, AttributeError):
            check(row["status"] == "unscored", "Invalid crypto timestamp was scored: " + label)
            continue
        check(row.get("publicationDateET") == publication.isoformat(), "Crypto New York publication day mismatch: " + label)
        eligible = (not call.get("excluded") and call.get("direction") in {"bullish", "bearish"}
                    and call.get("kind") in {"explicit", "inferred"} and (not call.get("reviewReason") or call.get("reviewed")))
        if not eligible:
            check(row["status"] == "unscored", "Ineligible crypto view was measured: " + label)
            continue
        entry = publication + timedelta(days=2)
        horizon = row["horizonCalendarDays"]
        exit_day = entry + timedelta(days=horizon)
        check(row.get("entryDate") == entry.isoformat(), "Crypto entry does not follow full New York day: " + label)
        check(row.get("exitDate") == exit_day.isoformat(), "Crypto exact calendar exit mismatch: " + label)
        if exit_day > cutoff:
            check(row["status"] == "pending", "Immature crypto horizon not pending: " + label)
            continue
        bars = prices.get(call.get("symbol"), {})
        if entry.isoformat() not in bars or exit_day.isoformat() not in bars:
            check(row["status"] == "unscored", "Missing crypto required candle was measured: " + label)
            continue
        opening, closing = bars[entry.isoformat()], bars[exit_day.isoformat()]
        raw = (closing / opening - 1) * 100
        signed = raw if call["direction"] == "bullish" else -raw
        status = "hit" if signed > 0 else "miss" if signed < 0 else "neutral"
        check(close(row.get("entryOpen"), opening), "Crypto entry price mismatch: " + label)
        check(close(row.get("exitOpen"), closing), "Crypto exit price mismatch: " + label)
        check(close(row.get("rawReturnPct"), raw), "Crypto raw return mismatch: " + label)
        check(close(row.get("directionalReturnPct"), signed), "Crypto signed return mismatch: " + label)
        check(row["status"] == status, "Crypto outcome status mismatch: " + label)

    def audit_summary(selected, metric, label):
        counts = Counter(r["status"] for r in selected)
        check(metric["calls"] == len(selected), label + " call count mismatch")
        for field in ("hits", "misses", "neutral", "pending", "unscored"):
            status = {"hits": "hit", "misses": "miss"}.get(field, field)
            check(metric[field] == counts[status], label + " " + field + " mismatch")
        observed = [r["directionalReturnPct"] for r in selected if r["status"] in {"hit", "miss", "neutral"}]
        n = counts["hit"] + counts["miss"]
        check(metric["scored"] == len(observed), label + " scored count mismatch")
        check(metric["binaryDenominator"] == n, label + " binary denominator mismatch")
        check(close(metric.get("hitRatePct"), Decimal(100) * counts["hit"] / n) if n else metric.get("hitRatePct") is None, label + " hit rate mismatch")
        for field, expected in (("meanDirectionalReturnPct", statistics.mean(observed) if observed else None), ("medianDirectionalReturnPct", statistics.median(observed) if observed else None)):
            check(close(metric.get(field), Decimal(str(expected))) if expected is not None else metric.get(field) is None, label + " " + field + " mismatch")
    for horizon in horizons:
        audit_summary([r for r in rows if r["horizonCalendarDays"] == horizon], payload["summary"]["byHorizon"][str(horizon)], f"Crypto horizon {horizon}")
    primary_rows = [r for r in rows if r["horizonCalendarDays"] == primary]
    audit_summary(primary_rows, payload["summary"]["primary"], "Crypto primary")
    for direction in ("bullish", "bearish"):
        audit_summary([r for r in primary_rows if r["direction"] == direction], payload["summary"]["byDirection"][direction], "Crypto " + direction)
    return {"passed": not errors, "checks": checks, "errors": errors,
            "method": "Independent source attribution, full crypto call/horizon coverage, New York-day-plus-two UTC entry, exact calendar exit and Decimal price/sign reconciliation; horizon, primary and direction counts.",
            "cryptoOutcomesSHA256": file_hash(data_dir / "crypto-outcomes.json"), "cryptoPriceCacheHashes": cache_hashes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--series-dir", type=Path, required=True)
    args = parser.parse_args()
    result = reconcile_crypto(args.data_dir, args.series_dir)
    path = args.data_dir / "crypto-verification.json"
    path.write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "checks": result["checks"], "mismatches": len(result["errors"])}))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
