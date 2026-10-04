#!/usr/bin/env python3
"""Fetch split-adjusted daily US-stock/ETF bars for source-approved instruments.

This adapter never validates a company merely because its ticker matches. Dated
issuer approval comes from the source review; unavailable histories stay gaps.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, time as daytime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from retrieve_posts import ProviderError, digest, read_study, request_json, write_json

NY = ZoneInfo("America/New_York")
UTC = timezone.utc
TICKER = re.compile(r"[A-Z][A-Z0-9.\-]{0,14}\Z")
PRICE_DOC = "https://massive.com/docs/rest/stocks/aggregates/custom-bars"
IDENTITY_DOC = "https://massive.com/docs/rest/stocks/tickers/ticker-overview"
US_TYPES = {"CS", "ETF", "ADRC", "ADRP", "ADRR", "ADRW", "PFD", "REIT", "UNIT"}


def safe_https(value):
    try:
        p = urlsplit(value)
        return p.scheme == "https" and bool(p.hostname) and not p.username and not p.password and p.port in (None, 443) and not any(c.isspace() or ord(c) < 32 for c in value)
    except (ValueError, TypeError, AttributeError):
        return False


def mapping_record(call):
    """Recheck documentary metadata, not the issuer interpretation itself."""
    if call.get("issuerMappingValidated") is not True or call.get("assetClass") not in {"equity", "etf"}:
        return None, "source_mapping_unapproved"
    symbol = call.get("symbol")
    if not isinstance(symbol, str) or not TICKER.fullmatch(symbol):
        return None, "invalid_symbol"
    try:
        stamp = datetime.fromisoformat(call["publishedAt"].replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError
        as_of = stamp.astimezone(NY).date().isoformat()
    except (ValueError, KeyError, AttributeError, TypeError):
        return None, "invalid_publication_date"
    ref = call.get("issuerReference")
    if isinstance(ref, dict):
        meta = ref.get("metadata", {})
        if not isinstance(meta, dict):
            return None, "invalid_dated_issuer_proof"
        identity = meta.get("composite_figi") or meta.get("share_class_figi") or (str(meta.get("cik", "")) + ":" + str(meta.get("name", "")) if meta.get("cik") and meta.get("name") else None)
        if (ref.get("status") != "ok" or ref.get("asOf") != as_of or not safe_https(ref.get("sourceUrl"))
                or not isinstance(meta, dict) or meta.get("ticker") != symbol or meta.get("market") != "stocks" or meta.get("locale") != "us"
                or meta.get("type") not in US_TYPES or not identity or not re.fullmatch(r"[a-f0-9]{64}", str(ref.get("responseSha256", "")))):
            return None, "invalid_dated_issuer_proof"
        if str(call.get("securityId", "")).startswith("FIGI:") and call["securityId"][5:] not in {meta.get("composite_figi"), meta.get("share_class_figi")}:
            return None, "security_identity_mismatch"
        return {"symbol": symbol, "securityId": call.get("securityId") or identity, "asOf": as_of, "sourceUrl": ref["sourceUrl"], "metadata": meta}, None
    proof = call.get("issuerMappingEvidence")
    if (not isinstance(proof, dict) or not safe_https(proof.get("sourceUrl")) or proof.get("asOfDate") != as_of
            or not proof.get("securityId") or proof.get("securityId") != call.get("securityId") or not proof.get("issuerName")
            or proof.get("market") != "stocks" or proof.get("locale") != "us" or proof.get("type") not in US_TYPES):
        return None, "missing_dated_issuer_proof"
    return {"symbol": symbol, "securityId": proof["securityId"], "asOf": as_of, "sourceUrl": proof["sourceUrl"], "metadata": proof}, None


def select_symbols(calls, study):
    start = (date.fromisoformat(study["startDate"]) - timedelta(days=7)).isoformat()
    end = date.fromisoformat(study["cutoff"]).isoformat()
    selected = {"SPY": {"symbol": "SPY", "start": start, "end": end, "benchmark": True, "proofs": []}}
    excluded = Counter()
    identities = {}
    for call in calls:
        record, reason = mapping_record(call)
        if reason:
            excluded[reason] += 1
            continue
        symbol, identity, meta = record["symbol"], record["securityId"], record["metadata"]
        identities.setdefault(symbol, set()).add(identity)
        # Agent-reviewed listing/delisting dates constrain ticker reuse. Do not
        # infer successors, acquisitions or share-class substitutions.
        lower, upper = start, end
        listed, delisted = meta.get("list_date") or meta.get("listDate"), meta.get("delisted_utc") or meta.get("delistedDate")
        if listed:
            lower = max(lower, date.fromisoformat(str(listed)[:10]).isoformat())
        if delisted:
            upper = min(upper, date.fromisoformat(str(delisted)[:10]).isoformat())
        if lower > upper:
            excluded["outside_confirmed_listing_period"] += 1
            continue
        item = selected.setdefault(symbol, {"symbol": symbol, "start": lower, "end": upper, "benchmark": False, "proofs": []})
        item["start"], item["end"] = max(item["start"], lower), min(item["end"], upper)
        receipt = {"asOf": record["asOf"], "sourceUrl": record["sourceUrl"], "securityId": identity}
        if receipt not in item["proofs"]:
            item["proofs"].append(receipt)
    for symbol, ids in identities.items():
        if len(ids) > 1:
            # One symbol-to-bars series cannot represent multiple reused issuers.
            selected.pop(symbol, None)
            excluded["symbol_reused_by_different_securities"] += 1
    return selected, dict(excluded)


def safe_next_url(value, symbol):
    try:
        parts = urlsplit(value)
        prefix = f"/v2/aggs/ticker/{quote(symbol, safe='')}/range/1/day/"
        if (parts.scheme != "https" or parts.hostname != "api.massive.com" or parts.username or parts.password
                or parts.port not in (None, 443) or parts.fragment or not parts.path.startswith(prefix)
                or any(c.isspace() or ord(c) < 32 for c in value)):
            raise ValueError
        params = [(k, v) for k, v in parse_qsl(parts.query) if k in {"cursor", "adjusted", "sort", "limit"}]
        return urlunsplit(("https", "api.massive.com", parts.path, urlencode(params), ""))
    except (ValueError, TypeError, AttributeError):
        raise ProviderError("unsafe_pagination") from None


def normalize_bar(row, first, last, now):
    if not isinstance(row, dict) or type(row.get("t")) is not int or row.get("otc") is True:
        raise ProviderError("invalid_bar")
    try:
        day = datetime.fromtimestamp(row["t"] / 1000, UTC).astimezone(NY).date()
        values = {}
        for field, label in (("o", "open"), ("c", "close"), ("h", "high"), ("l", "low")):
            number = row.get(field)
            if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or number <= 0:
                raise ValueError
            values[label] = number
        if not values["low"] <= min(values["open"], values["close"]) <= max(values["open"], values["close"]) <= values["high"]:
            raise ValueError
    except (ValueError, OverflowError, OSError):
        raise ProviderError("invalid_bar") from None
    if not first <= day.isoformat() <= last or datetime.combine(day, daytime(20), NY) > now:
        return None
    return {"date": day.isoformat(), **values, "timestamp": row["t"]}


def fetch_symbol(item, series_dir, request, *, page_cap=30, refresh=False, now=None):
    root = Path(series_dir)
    root.mkdir(parents=True, exist_ok=True)
    symbol, first, last = item["symbol"], item["start"], item["end"]
    path = root / (symbol + ".json")
    source = f"https://api.massive.com/v2/aggs/ticker/{quote(symbol,safe='')}/range/1/day/{first}/{last}"
    initial_url = source + "?" + urlencode({"adjusted": "true", "sort": "asc", "limit": 50000})
    doc = {"symbol": symbol, "provider": "Massive", "requestedStart": first, "requestedEnd": last, "status": "fetching", "adjusted": True, "adjustment": "splits_only", "sourceDocumentation": PRICE_DOC,
           "priceConvention": "Daily aggregate open is a research proxy, potentially including qualifying extended-hours trades. Dividends, costs and borrow excluded.", "proofs": item["proofs"], "benchmark": item["benchmark"], "rows": [], "responses": [], "nextUrl": initial_url, "error": None}
    if path.exists() and not refresh:
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing.get("requestedStart") == first and existing.get("requestedEnd") == last and existing.get("adjusted") is True and existing.get("proofs") == item["proofs"]:
            doc = existing
            if doc.get("status") == "ok":
                return doc
    dates = {bar["date"]: bar for bar in doc["rows"]}
    seen = {r["sourceUrl"] for r in doc["responses"]}
    now = now or datetime.now(UTC)
    try:
        for _ in range(page_cap):
            url = safe_next_url(doc.get("nextUrl") or initial_url, symbol)
            if url in seen:
                raise ProviderError("pagination_stalled")
            payload = request(url)
            rows = payload.get("results", [])
            if payload.get("status") not in {"OK", "DELAYED"} or payload.get("adjusted") is not True or payload.get("ticker") != symbol or not isinstance(rows, list):
                raise ProviderError("unconfirmed_adjusted_history")
            next_url = safe_next_url(payload["next_url"], symbol) if payload.get("next_url") else None
            # Save only successful source payloads. Remove authentication query
            # parameters from provider pagination URLs before retaining bytes.
            retained = {**payload}
            if "next_url" in retained:
                retained["next_url"] = next_url
            raw_path = root / "source" / f"{symbol}-{len(doc['responses'])+1:03d}.json"
            new_dates = dict(dates)
            for raw in rows:
                bar = normalize_bar(raw, first, last, now)
                if bar is None:
                    continue
                if bar["date"] in new_dates:
                    raise ProviderError("duplicate_bar")
                new_dates[bar["date"]] = bar
            write_json(raw_path, retained)
            dates = new_dates
            seen.add(url)
            doc["responses"].append({"sourceUrl": url, "rawPath": str(raw_path.relative_to(root)), "responseSha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(), "status": payload["status"], "resultsCount": len(rows)})
            doc.update(nextUrl=next_url, rows=[dates[d] for d in sorted(dates)], status="ok" if not next_url else "fetching", error=None)
            write_json(path, doc)
            if not next_url:
                break
        else:
            doc.update(status="partial", error="page_cap_paused")
    except ProviderError as error:
        doc.update(status="partial" if dates else "failed", error=error.code)
    doc.update(firstObserved=min(dates) if dates else None, lastObserved=max(dates) if dates else None,
               emptyHistory=not bool(dates), updatedAt=now.isoformat(), gapNote="Missing entry/exit bars remain unscored; no forward fill, successor substitution or delisting terminal value.")
    write_json(path, doc)
    return doc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--calls", type=Path, required=True)
    parser.add_argument("--series-dir", type=Path, required=True)
    parser.add_argument("--page-cap", type=int, default=30, help="Maximum new aggregate pages per symbol in one invocation.")
    parser.add_argument("--max-requests", type=int, default=2000)
    parser.add_argument("--min-interval", type=float, default=12.1, help="Seconds between requests; conservative for restricted plans, lower only when entitlement permits.")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    try:
        study, _, _, _ = read_study(args.study)
        if args.page_cap <= 0 or args.max_requests <= 0 or args.min_interval < 0:
            raise ValueError("Request caps must be positive and interval nonnegative.")
        calls = json.loads(args.calls.read_text(encoding="utf-8"))
        calls = calls.get("calls") if isinstance(calls, dict) else calls
        if not isinstance(calls, list):
            raise ValueError("Calls must be a list.")
        selected, excluded = select_symbols(calls, study)
        key = os.environ.get("MASSIVE_API_KEY", "")
        if not key:
            raise ProviderError("missing_provider_access")
        requests, last_request, results = 0, None, []
        def request(url):
            nonlocal requests, last_request
            if requests >= args.max_requests:
                raise ProviderError("request_budget_paused")
            if last_request is not None:
                time.sleep(max(0, args.min_interval - (time.monotonic() - last_request)))
            last_request = time.monotonic()
            requests += 1
            return request_json(url, key, host="api.massive.com")
        for symbol in sorted(selected, key=lambda name: (name != "SPY", name)):
            document = fetch_symbol(selected[symbol], args.series_dir, request, page_cap=args.page_cap, refresh=args.refresh)
            results.append({"symbol": symbol, "status": document["status"], "bars": len(document["rows"]), "firstObserved": document.get("firstObserved"), "lastObserved": document.get("lastObserved"), "error": document.get("error"), "cacheSha256": hashlib.sha256((args.series_dir / (symbol + ".json")).read_bytes()).hexdigest()})
            manifest = {"schemaVersion": "1.0", "provider": "Massive", "cutoff": study["cutoff"], "basis": "split-adjusted price returns; no dividends", "requestedStudyStart": study["startDate"], "callsSha256": hashlib.sha256(args.calls.read_bytes()).hexdigest(), "excludedMappingCounts": excluded, "series": results, "requestsThisInvocation": requests,
                        "completeHistoryVerified": False, "coverageNote": "Successful pagination confirms delivered history, not absence of missing/delisted bars. Independently verify SPY sessions before scoring.", "identityNote": "Source-reviewed dated issuer mappings govern eligibility. This adapter does not infer issuer identity from literal ticker matches or stitch renamed/acquired/share-class securities."}
            write_json(args.study.parent / "price-manifest.json", manifest)
        print(json.dumps({"series": len(results), "bars": sum(r["bars"] for r in results), "states": dict(Counter(r["status"] for r in results)), "excludedMappings": excluded, "requests": requests}))
        return 0
    except (ProviderError, ValueError, OSError, KeyError, TypeError) as error:
        print(json.dumps({"status": "failed", "code": error.code if isinstance(error, ProviderError) else "invalid_input_or_checkpoint"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
