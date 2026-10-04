"""Source-independent forward-price event study; no network/model calls.

Input series are split-adjusted US equity/ETF daily bars, with ET dates. The
SPY series supplies the observed trading-session calendar. Entry is the first
SPY session whose ET date is strictly later than the post's ET date. A horizon
of h means open-to-open, h session intervals after that entry. Missing required
instrument bars never move either date. All returns are percentage points.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
import csv
from datetime import date, datetime, timedelta
from decimal import Decimal
import json
import math
from pathlib import Path
import re
from zoneinfo import ZoneInfo
from study_utils import safe_csv_cell

HORIZONS = (1, 5, 21, 63, 126, 252)
NEW_YORK = ZoneInfo("America/New_York")
TICKER = re.compile(r"[A-Z][A-Z0-9.\-]{0,14}\Z")
RETURN_FIELDS = ("rawReturnPct", "directionalReturnPct", "spyReturnPct", "relativeReturnPct")

def exchange_sessions(start: str, cutoff: str) -> list[str]:
    """Build an independent NYSE calendar, including exceptional closures.

    A manually verified calendar can instead be supplied to score_calls. The
    version/source of any supplied calendar belongs in the run provenance.
    """
    first, last = date.fromisoformat(start), date.fromisoformat(cutoff)
    if first > last:
        raise ValueError("Calendar start must be on or before the cutoff.")
    try:
        import exchange_calendars as xcals
    except ImportError as exc:
        raise RuntimeError("Install exchange-calendars or supply an independently verified --calendar-file.") from exc
    # A one-day holiday interval has no sessions; pad construction bounds so
    # the calendar can still return an empty expected-session list for it.
    calendar = xcals.get_calendar("XNYS", start=(first - timedelta(days=14)).isoformat(),
                                   end=(last + timedelta(days=14)).isoformat())
    return [stamp.date().isoformat() for stamp in calendar.sessions_in_range(start, cutoff)]


def audit_spy_calendar(session_dates: list[str], start: str, cutoff: str, *,
                       expected_sessions: list[str] | None = None,
                       calendar_source: str | None = None) -> dict:
    """Independently check every expected session, including the cutoff session.

    Checking the calendar before counting SPY rows avoids shortening horizons
    or declaring old outcomes pending because a benchmark bar went missing.
    """
    first, last = date.fromisoformat(start), date.fromisoformat(cutoff)
    if first > last:
        raise ValueError("Calendar start must be on or before the cutoff.")
    if expected_sessions is None:
        expected_sessions = exchange_sessions(start, cutoff)
        import importlib.metadata
        calendar_source = "exchange-calendars " + importlib.metadata.version("exchange-calendars") + " / XNYS"
    if not isinstance(expected_sessions, list) or any(not isinstance(day, str) for day in expected_sessions):
        raise ValueError("Independent sessions must be a list of ISO dates.")
    for day in expected_sessions:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            raise ValueError("Independent sessions require YYYY-MM-DD dates.")
        date.fromisoformat(day)
    if len(set(expected_sessions)) != len(expected_sessions):
        raise ValueError("Duplicate independent calendar session.")
    expected = {day for day in expected_sessions if start <= day <= cutoff}
    observed = {text for text in session_dates if start <= text <= cutoff}
    missing, extra = sorted(expected - observed), sorted(observed - expected)
    return {"passed": not missing and not extra, "expectedSessions": len(expected), "observedSessions": len(observed),
            "missingSessions": missing, "unexpectedSessions": extra,
            "source": calendar_source or "Caller-supplied independently verified session calendar"}


def publication_date_et(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Publication timestamps require an explicit timezone.")
    return parsed.astimezone(NEW_YORK).date().isoformat()


def clean_series(rows: list[dict], cutoff: str) -> dict[str, dict]:
    """Reject corrupt rows instead of silently keeping one duplicate quote."""
    date.fromisoformat(cutoff)
    if not isinstance(rows, list):
        raise ValueError("Each ticker cache must contain a list of daily bars.")
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Daily bars must be objects.")
        day = row.get("date")
        if not isinstance(day, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            raise ValueError("Daily bars need an ISO ET calendar date.")
        date.fromisoformat(day)
        value = row.get("open")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError("Daily open prices must be positive finite numbers.")
        if day in result:
            raise ValueError(f"Duplicate daily bar: {day}.")
        if day <= cutoff:
            result[day] = {**row, "open": float(value)}
    return dict(sorted(result.items()))


def _empty_outcome(call: dict, horizon: int) -> dict:
    row = {key: call.get(key) for key in (
        "postId", "publishedAt", "symbol", "canonicalSymbol", "securityId", "assetClass", "instrument", "direction", "kind", "callType", "thesis", "sector",
        "confidence", "reviewReason", "reviewed", "excluded", "issuerMappingValidated", "postUrl", "evidence")}
    row.update(id=f"{call['id']}:{horizon}", callId=call["id"], horizon=horizon,
               publicationDateET=None, entryDate=None, exitDate=None, entrySessionIndex=None,
               entryOpen=None, exitOpen=None, spyEntryOpen=None, spyExitOpen=None,
               status="unscored", relativeStatus="unscored", reason=None)
    row.update({field: None for field in RETURN_FIELDS})
    row["canonicalSymbol"] = call.get("canonicalSymbol") or call.get("symbol")
    row["securityId"] = call.get("securityId") or row["canonicalSymbol"]
    # Source-cohort dates remain useful even for conditional or non-equity views.
    # Deriving them only after eligibility would group UTC New-Year overnight
    # posts into the wrong calendar year in coverage/exclusion breakdowns.
    try:
        row["publicationDateET"] = publication_date_et(call["publishedAt"])
    except (ValueError, TypeError, AttributeError, KeyError):
        pass
    return row


def _status(number: float) -> str:
    return "neutral" if number == 0 else "hit" if number > 0 else "miss"


def us_source_rejection(call: dict) -> str | None:
    """Eligibility depends on reviewed source/identity, never future prices."""
    if call.get("excluded"):
        return "Excluded by review."
    if call.get("direction") not in {"bullish", "bearish"}:
        return "No unconditional bullish or bearish direction."
    if call.get("assetClass") not in {"equity", "etf"}:
        return "This study's price backtest includes confirmed US equities and ETFs only; other asset classes are retained for review."
    if call.get("kind") not in {"explicit", "inferred"}:
        return "Conditional, exit or unclear statements are not unconditional directional calls."
    if call.get("reviewReason") and not call.get("reviewed"):
        return "Interpretation requires review: " + str(call["reviewReason"])
    symbol = call.get("symbol")
    if not isinstance(symbol, str) or not TICKER.fullmatch(symbol):
        return "No confirmed US equity or ETF ticker."
    if call.get("issuerMappingValidated") is not True:
        return "Dated US issuer/security mapping is unverified; ticker syntax alone is insufficient."
    try:
        publication_date_et(call["publishedAt"])
    except (ValueError, TypeError, AttributeError, KeyError):
        return "Invalid or timezone-free publication timestamp."
    return None


def score_calls(calls: list[dict], series_by_symbol: dict[str, list[dict]], *,
                cutoff: str, horizons=HORIZONS,
                expected_sessions: list[str] | None = None, calendar_source: str | None = None) -> dict:
    """Return metadata plus every call x horizon, including unscored outcomes."""
    if not isinstance(calls, list) or not isinstance(series_by_symbol, dict):
        raise ValueError("Calls must be a list and historical series a ticker-to-bars mapping.")
    date.fromisoformat(cutoff)
    horizons = tuple(sorted(set(horizons)))
    if not horizons or any(type(h) is not int or h <= 0 for h in horizons):
        raise ValueError("Horizons must be positive whole trading-session counts.")
    if len({call.get("id") for call in calls}) != len(calls) or any(not isinstance(call.get("id"), str) or not call["id"] for call in calls):
        raise ValueError("Every call needs a unique nonempty ID.")
    if not series_by_symbol.get("SPY") and all(us_source_rejection(call) is not None for call in calls):
        # Nothing is eligible for a US price measurement. Requiring a paid
        # benchmark feed here adds no information to an empty/macro-only book.
        outcomes = []
        for call in calls:
            for horizon in horizons:
                row = _empty_outcome(call, horizon)
                row["reason"] = us_source_rejection(call)
                outcomes.append(row)
        no_measurement = "No source-eligible US equity/ETF calls; no market-price measurements were made."
        return {"metadata": {
            "cutoff": None, "horizons": list(horizons), "callCount": len(calls), "outcomeCount": len(outcomes),
            "measurementMode": "source-only", "noMeasurementReason": no_measurement,
            "calendar": None, "calendarFirst": None, "calendarLast": None,
            "calendarSessions": 0, "sessionDates": [],
            "calendarAudit": {"passed": True, "notRequired": True, "reason": no_measurement,
                              "expectedSessions": 0, "observedSessions": 0,
                              "missingSessions": [], "unexpectedSessions": [], "source": None},
            "entryRule": "No entry prices measured; the configured stock method uses the first session after the NY publication day.",
            "exitRule": "No exit prices measured.", "returnBasis": "Source coverage and exclusions only; no price returns.",
            "executionLimitation": "No market execution or prices are inferred.",
            "units": "All return fields ending Pct are percentage points; unmeasured fields are null.",
            "horizonInterpretation": "Configured subsequent-performance windows; no eligible US outcomes were measured."
        }, "outcomes": outcomes}
    series = {symbol: clean_series(rows, cutoff) for symbol, rows in series_by_symbol.items()}
    spy = series.get("SPY", {})
    if not spy:
        raise ValueError("Full SPY daily bars are required to define the trading-session calendar.")
    sessions = list(spy)
    required_starts = [sessions[0]]
    for call in calls:
        if us_source_rejection(call) is None:
            try:
                required_starts.append((date.fromisoformat(publication_date_et(call["publishedAt"])) + timedelta(days=1)).isoformat())
            except (ValueError, TypeError, KeyError, AttributeError):
                pass
    audit_start = min(required_starts)
    calendar_audit = audit_spy_calendar(sessions, audit_start, cutoff,
                                      expected_sessions=expected_sessions, calendar_source=calendar_source)
    if not calendar_audit["passed"]:
        raise ValueError("SPY trading-session calendar is incomplete or contains unexpected dates: " + json.dumps(calendar_audit))
    outcomes = []
    for call in calls:
        for horizon in horizons:
            row = _empty_outcome(call, horizon)
            outcomes.append(row)
            rejection = us_source_rejection(call)
            if rejection is not None:
                row["reason"] = rejection
                continue
            symbol = call["symbol"]
            try:
                day = publication_date_et(call["publishedAt"])
            except (ValueError, TypeError, AttributeError, KeyError):
                row["reason"] = "Invalid or timezone-free publication timestamp."
                continue
            row["publicationDateET"] = day
            if day > cutoff:
                row.update(status="pending", relativeStatus="pending", reason="Publication is after available completed prices at the frozen outcome cutoff; the entry has not matured.")
                continue
            # A missing early SPY history cannot be treated as a late executable entry.
            if day < sessions[0] and (date.fromisoformat(sessions[0]) - date.fromisoformat(day)).days > 7:
                row["reason"] = "SPY session history starts too late to establish a timely entry."
                continue
            index = bisect_right(sessions, day)
            if index >= len(sessions):
                row.update(status="pending", relativeStatus="pending", reason="The next post-publication session is after available completed SPY history.")
                continue
            entry_day = sessions[index]
            row.update(entryDate=entry_day, entrySessionIndex=index, spyEntryOpen=spy[entry_day]["open"])
            ticker = series.get(symbol)
            if ticker is None:
                row["reason"] = "Historical price data is unavailable for this instrument."
                continue
            entry = ticker.get(entry_day)
            if entry is None:
                row["reason"] = "Missing instrument bar on the required entry session; no delayed entry substituted."
                continue
            row["entryOpen"] = entry["open"]
            exit_index = index + horizon
            if exit_index >= len(sessions):
                row.update(status="pending", relativeStatus="pending", reason="The selected horizon has not matured within completed SPY history at the cutoff.")
                continue
            exit_day = sessions[exit_index]
            row.update(exitDate=exit_day, spyExitOpen=spy[exit_day]["open"])
            exit_bar = ticker.get(exit_day)
            if exit_bar is None:
                row["reason"] = "Missing instrument bar on the required exit session; delisting, suspension or a data gap needs review."
                continue
            row["exitOpen"] = exit_bar["open"]
            # Decimal representations of provider values preserve exact equal
            # price ratios without creating spurious ~1e-14% wins from floating
            # cancellation. This is arithmetic precision, not an economic hurdle.
            raw_decimal = (Decimal(str(row["exitOpen"])) / Decimal(str(row["entryOpen"])) - 1) * 100
            spy_decimal = (Decimal(str(row["spyExitOpen"])) / Decimal(str(row["spyEntryOpen"])) - 1) * 100
            raw, benchmark = float(raw_decimal), float(spy_decimal)
            sign = 1 if call["direction"] == "bullish" else -1
            directional = sign * raw
            relative = float(sign * (raw_decimal - spy_decimal))
            row.update(rawReturnPct=raw, spyReturnPct=benchmark, directionalReturnPct=directional,
                       relativeReturnPct=relative, status=_status(directional), relativeStatus=_status(relative), reason=None)
    return {"metadata": {
        "cutoff": cutoff, "horizons": list(horizons), "callCount": len(calls), "outcomeCount": len(outcomes),
        "calendar": "Observed completed SPY daily-bar ET dates", "calendarFirst": sessions[0],
        "calendarLast": sessions[-1], "calendarSessions": len(sessions), "sessionDates": sessions, "calendarAudit": calendar_audit,
        "entryRule": "First SPY session whose ET date is strictly after the publication ET date; daily open.",
        "exitRule": "Same instrument daily open h SPY session intervals after entry, on the exact required date.",
        "returnBasis": "Split-adjusted price returns; dividends, costs and stock borrow excluded.",
        "executionLimitation": "Provider daily opens are a research price proxy, not verified 09:30 fills; some aggregate feeds include extended-hours qualifying trades.",
        "units": "All return fields ending Pct are percentage points.",
        "horizonInterpretation": "Standardized subsequent-performance windows, not necessarily the horizon stated by the author."
    }, "outcomes": outcomes}


def load_series_directory(directory: str | Path) -> dict[str, list[dict]]:
    result = {}
    for path in sorted(Path(directory).glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        symbol = path.stem.upper()
        if not TICKER.fullmatch(symbol):
            continue
        if isinstance(payload, list):
            bars = payload
        elif isinstance(payload, dict) and isinstance(payload.get("rows"), list):
            bars = payload["rows"]
        elif isinstance(payload, dict) and isinstance(payload.get("bars"), list):
            bars = payload["bars"]
        else:
            continue  # Separate metadata files are not price caches.
        result[symbol] = bars
    return result


def write_outcomes(payload: dict, output_dir: str | Path) -> None:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "outcomes.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    rows = payload["outcomes"]
    with (destination / "outcomes.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["callId", "horizon", "status", "reason"])
        writer.writeheader()
        writer.writerows({key: safe_csv_cell(value) for key, value in row.items()} for row in rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calls", type=Path, required=True)
    parser.add_argument("--series-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cutoff", required=True, help="Frozen last completed price date, YYYY-MM-DD.")
    parser.add_argument("--horizons", default=",".join(map(str, HORIZONS)), help="Comma-separated positive session counts.")
    parser.add_argument("--calendar-file", type=Path, help="Independently verified session list or {sessions: [...], source: ...}.")
    args = parser.parse_args()
    raw_calls = json.loads(args.calls.read_text(encoding="utf-8"))
    calls = raw_calls["calls"] if isinstance(raw_calls, dict) else raw_calls
    calendar = json.loads(args.calendar_file.read_text(encoding="utf-8")) if args.calendar_file else None
    expected = calendar.get("sessions") if isinstance(calendar, dict) else calendar
    source = calendar.get("source") if isinstance(calendar, dict) else None
    output = score_calls(calls, load_series_directory(args.series_dir), cutoff=args.cutoff,
                         horizons=tuple(int(text.strip()) for text in args.horizons.split(",")),
                         expected_sessions=expected, calendar_source=source)
    write_outcomes(output, args.output_dir)
    print(json.dumps({"callCount": len(calls), "outcomeCount": len(output["outcomes"]), "calendarSessions": output["metadata"]["calendarSessions"]}))
