"""Separate USD spot crypto event study with exact UTC-calendar-day opens.

Never combine these calendar-day results with stock trading-session statistics.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import date, timedelta
from decimal import Decimal
import json
from pathlib import Path
import statistics

from scoring import clean_series, load_series_directory, publication_date_et
from statistical_summary import wilson_interval
from study_utils import safe_csv_cell

HORIZONS = (1, 7, 30, 90, 365)


def summarize_crypto(group: list[dict]) -> dict:
    counts = Counter(row["status"] for row in group)
    n = counts["hit"] + counts["miss"]
    values = [row["directionalReturnPct"] for row in group if row["status"] in {"hit", "miss", "neutral"}]
    return {"calls": len(group), "scored": len(values), "hits": counts["hit"], "misses": counts["miss"],
            "neutral": counts["neutral"], "pending": counts["pending"], "unscored": counts["unscored"],
            "binaryDenominator": n, "hitRatePct": 100 * counts["hit"] / n if n else None,
            "hitRateWilson95": wilson_interval(counts["hit"], n),
            "meanDirectionalReturnPct": statistics.mean(values) if values else None,
            "medianDirectionalReturnPct": statistics.median(values) if values else None}


def score_crypto(calls: list[dict], series: dict[str, list[dict]], *, cutoff: str,
                 horizons=HORIZONS, primary: int = 30) -> dict:
    last = date.fromisoformat(cutoff)
    horizons = tuple(sorted(set(horizons)))
    if not horizons or any(type(h) is not int or h < 1 for h in horizons):
        raise ValueError("Crypto horizons must be positive whole calendar-day counts.")
    if type(primary) is not int or primary not in horizons:
        raise ValueError("Crypto primary must be one of the configured horizons.")
    if not isinstance(calls, list) or not isinstance(series, dict):
        raise ValueError("Calls must be a list and crypto series a symbol-to-bars mapping.")
    if len({call.get("id") for call in calls}) != len(calls) or any(not isinstance(call.get("id"), str) or not call["id"] for call in calls):
        raise ValueError("Every call needs a unique nonempty ID.")
    by_symbol = {symbol: clean_series(bars, cutoff) for symbol, bars in series.items()}
    rows = []
    for call in calls:
        if call.get("assetClass") != "crypto":
            continue
        for horizon in horizons:
            row = {key: call.get(key) for key in ["postId", "publishedAt", "postUrl", "symbol", "instrument", "kind",
                                                 "callType", "direction", "thesis", "evidence", "confidence"]}
            row.update(id=f"{call['id']}:{horizon}", callId=call["id"], assetClass="crypto",
                       horizonCalendarDays=horizon, status="unscored", reason=None,
                       publicationDateET=None, entryDate=None, exitDate=None, entryOpen=None, exitOpen=None,
                       rawReturnPct=None, directionalReturnPct=None)
            rows.append(row)
            try:
                publication_day = publication_date_et(call["publishedAt"])
                row["publicationDateET"] = publication_day
            except (ValueError, KeyError, TypeError, AttributeError):
                row["reason"] = "Invalid or timezone-free publication timestamp."
                continue
            if call.get("excluded"):
                row["reason"] = "Excluded by review."
                continue
            if call.get("direction") not in {"bullish", "bearish"}:
                row["reason"] = "No unconditional bullish or bearish direction."
                continue
            if call.get("kind") not in {"explicit", "inferred"} or (call.get("reviewReason") and not call.get("reviewed")):
                row["reason"] = "Conditional, unclear, completed exit or source interpretation requiring review."
                continue
            # First UTC midnight strictly after the complete NY publication day.
            # This is intentionally later than the next UTC day: same-NY-day
            # classification batches must all end before measured returns start.
            entry = date.fromisoformat(publication_day) + timedelta(days=2)
            exit_day = entry + timedelta(days=horizon)
            row.update(entryDate=entry.isoformat(), exitDate=exit_day.isoformat())
            if exit_day > last:
                row.update(status="pending", reason="Calendar-day horizon not mature at frozen cutoff.")
                continue
            bars = by_symbol.get(call.get("symbol"))
            if bars is None:
                row["reason"] = "No verified USD spot history for this identified asset."
                continue
            if entry.isoformat() not in bars or exit_day.isoformat() not in bars:
                row["reason"] = "Missing exact required UTC-day USD candle; no substituted date."
                continue
            start, finish = bars[entry.isoformat()]["open"], bars[exit_day.isoformat()]["open"]
            raw = float((Decimal(str(finish)) / Decimal(str(start)) - 1) * 100)
            signed = raw * (1 if call["direction"] == "bullish" else -1)
            row.update(entryOpen=start, exitOpen=finish, rawReturnPct=raw, directionalReturnPct=signed,
                       status="hit" if signed > 0 else "miss" if signed < 0 else "neutral")
    summaries = {str(h): summarize_crypto([row for row in rows if row["horizonCalendarDays"] == h]) for h in horizons}
    by_direction = {direction: summarize_crypto([row for row in rows if row["horizonCalendarDays"] == primary and row["direction"] == direction])
                    for direction in ("bullish", "bearish")}
    return {"metadata": {"cutoff": cutoff, "horizonsCalendarDays": list(horizons), "primaryCalendarDays": primary,
                         "entryRule": "First UTC daily open strictly after the end of the post's New York publication day: NY date plus two days at 00:00 UTC. Same-NY-day source batches precede every measured return.",
                         "returnBasis": "USD spot price returns; exchange-specific opens; fees and borrow excluded; no SPY comparison; separate from equity trading sessions.",
                         "units": "All return fields ending Pct are percentage points."},
            "outcomes": rows, "summary": {"byHorizon": summaries, "primary": summaries[str(primary)], "byDirection": by_direction}}


def write_crypto(payload: dict, output_dir: str | Path) -> None:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "crypto-outcomes.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    rows = payload["outcomes"]
    with (destination / "crypto-outcomes.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["callId", "horizonCalendarDays", "status"])
        writer.writeheader()
        writer.writerows({key: safe_csv_cell(value) for key, value in row.items()} for row in rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calls", type=Path, required=True)
    parser.add_argument("--series-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cutoff", required=True)
    parser.add_argument("--horizons", default=",".join(map(str, HORIZONS)))
    parser.add_argument("--primary", type=int, default=30)
    args = parser.parse_args()
    raw = json.loads(args.calls.read_text(encoding="utf-8"))
    calls = raw["calls"] if isinstance(raw, dict) else raw
    result = score_crypto(calls, load_series_directory(args.series_dir), cutoff=args.cutoff,
                          horizons=tuple(int(text.strip()) for text in args.horizons.split(",")), primary=args.primary)
    write_crypto(result, args.output_dir)
    print(json.dumps(result["summary"]["primary"]))


if __name__ == "__main__":
    main()
