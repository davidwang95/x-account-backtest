#!/usr/bin/env python3
"""Separate public Coinbase USD spot candles; exact UTC daily buckets, no API key."""
from __future__ import annotations
import argparse
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener

from retrieve_posts import NoRedirect, ProviderError, read_study, write_json

UTC = timezone.utc
DOC = "https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles"


def public_request(path, params=None):
    if not re.fullmatch(r"/products/[A-Z0-9]{2,15}-USD(?:/candles)?", path):
        raise ProviderError("unsafe_destination")
    url = "https://api.exchange.coinbase.com" + path
    if params:
        url += "?" + urlencode(params)
    opener = build_opener(NoRedirect())
    for attempt in range(4):
        try:
            req = Request(url, headers={"Accept": "application/json", "User-Agent": "DaveWang-XBacktest/1.0"})
            with opener.open(req, timeout=45) as response:
                return json.loads(response.read(5_000_001))
        except HTTPError as error:
            if error.code not in {429, 500, 502, 503, 504} or attempt == 3:
                raise ProviderError("not_found" if error.code == 404 else "provider_response") from None
        except (URLError, TimeoutError, OSError):
            if attempt == 3:
                raise ProviderError("network") from None
        except (ValueError, UnicodeError):
            raise ProviderError("invalid_response") from None
        time.sleep(2 ** attempt)
    raise ProviderError("network")


def fetch_crypto(symbol, first, last, series_dir, request, *, page_cap=100, refresh=False, now=None):
    if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z0-9]{2,15}", symbol):
        raise ValueError("An identified crypto base symbol is required.")
    begin = datetime.combine(date.fromisoformat(first), datetime.min.time(), UTC)
    stop = datetime.combine(date.fromisoformat(last) + timedelta(days=1), datetime.min.time(), UTC)
    if begin >= stop:
        raise ValueError("Crypto price dates must be ordered.")
    root, product = Path(series_dir), symbol + "-USD"
    root.mkdir(parents=True, exist_ok=True)
    path = root / (symbol + ".json")
    product_path = "/products/" + product
    doc = {"symbol": symbol, "product": product, "provider": "Coinbase Exchange", "quoteCurrency": "USD", "dateConvention": "UTC daily buckets", "requestedStart": first, "requestedEnd": last,
           "sourceDocumentation": DOC, "status": "fetching", "rows": [], "responses": [], "productReference": None, "nextStart": begin.isoformat(), "error": None}
    if path.exists() and not refresh:
        old = json.loads(path.read_text(encoding="utf-8"))
        if old.get("requestedStart") == first and old.get("requestedEnd") == last and old.get("product") == product:
            doc = old
            if doc["status"] == "ok":
                return doc
    observed = {r["date"]: r for r in doc["rows"]}
    cursor = datetime.fromisoformat(doc["nextStart"])
    now = now or datetime.now(UTC)
    try:
        if not doc["productReference"]:
            reference = request(product_path)
            if not isinstance(reference, dict) or reference.get("id") != product or reference.get("base_currency") != symbol or reference.get("quote_currency") != "USD":
                raise ProviderError("unverified_usd_product")
            ref_path = root / "source" / (symbol + "-product.json")
            write_json(ref_path, reference)
            doc["productReference"] = {"sourceUrl": "https://api.exchange.coinbase.com" + product_path, "rawPath": str(ref_path.relative_to(root)), "responseSha256": hashlib.sha256(ref_path.read_bytes()).hexdigest()}
            write_json(path, doc)
        for _ in range(page_cap):
            if cursor >= stop:
                doc.update(status="ok", error=None)
                break
            following = min(cursor + timedelta(days=200), stop)
            params = {"granularity": 86400, "start": cursor.isoformat(), "end": following.isoformat()}
            payload = request(product_path + "/candles", params)
            if not isinstance(payload, list):
                raise ProviderError("invalid_response")
            additions = dict(observed)
            for row in payload:
                if not isinstance(row, list) or len(row) < 6 or type(row[0]) is not int or row[0] % 86400:
                    raise ProviderError("invalid_candle")
                stamp = datetime.fromtimestamp(row[0], UTC)
                if not cursor <= stamp < following or stamp + timedelta(days=1) > now:
                    continue
                numbers = row[1:6]
                if any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) for v in numbers) or min(numbers[:4]) <= 0 or numbers[4] < 0:
                    raise ProviderError("invalid_candle")
                low, high, opening, closing, volume = numbers
                if not low <= min(opening, closing) <= max(opening, closing) <= high:
                    raise ProviderError("invalid_candle")
                day = stamp.date().isoformat()
                if day in additions:
                    raise ProviderError("duplicate_candle")
                additions[day] = {"date": day, "open": opening, "close": closing, "high": high, "low": low, "volume": volume, "sourceProduct": product}
            raw_path = root / "source" / f"{symbol}-{len(doc['responses'])+1:03d}.json"
            write_json(raw_path, {"requestParams": params, "response": payload})
            observed, cursor = additions, following
            doc["responses"].append({"sourceUrl": "https://api.exchange.coinbase.com" + product_path + "/candles", "requestParams": params, "rawPath": str(raw_path.relative_to(root)), "responseSha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(), "returnedRows": len(payload)})
            doc.update(rows=[observed[d] for d in sorted(observed)], nextStart=cursor.isoformat(), status="ok" if cursor >= stop else "fetching", error=None)
            write_json(path, doc)
        else:
            if cursor < stop:
                doc.update(status="partial", error="page_cap_paused")
    except ProviderError as error:
        doc.update(status="partial" if observed else "failed", error=error.code)
    expected = {(begin + timedelta(days=i)).date().isoformat() for i in range((stop - begin).days)}
    doc.update(missingDates=sorted(expected - set(observed)), calendarComplete=not bool(expected - set(observed)), firstObserved=min(observed) if observed else None,
               lastObserved=max(observed) if observed else None, updatedAt=now.isoformat(), gapNote="No synthetic candles, USD substitutions, interpolation or borrowed-stock assumptions. Separate crypto calendar-day outcomes.")
    write_json(path, doc)
    return doc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--calls", type=Path, required=True)
    parser.add_argument("--series-dir", type=Path, required=True)
    parser.add_argument("--page-cap", type=int, default=100)
    parser.add_argument("--max-requests", type=int, default=500)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    try:
        study, _, _, _ = read_study(args.study)
        calls = json.loads(args.calls.read_text(encoding="utf-8"))
        calls = calls.get("calls") if isinstance(calls, dict) else calls
        if not isinstance(calls, list) or args.page_cap <= 0 or args.max_requests <= 0:
            raise ValueError("Calls and request caps are invalid.")
        symbols = sorted({c["symbol"] for c in calls if c.get("assetClass") == "crypto" and isinstance(c.get("symbol"), str) and re.fullmatch(r"[A-Z0-9]{2,15}", c["symbol"])
                          and c.get("kind") in {"explicit", "inferred"} and c.get("direction") in {"bullish", "bearish"} and not c.get("excluded") and (not c.get("reviewReason") or c.get("reviewed"))})
        count, results = 0, []
        def request(path, params=None):
            nonlocal count
            if count >= args.max_requests:
                raise ProviderError("request_budget_paused")
            count += 1
            time.sleep(0.35)
            return public_request(path, params)
        for symbol in symbols:
            data = fetch_crypto(symbol, study["startDate"], study["cutoff"], args.series_dir, request, page_cap=args.page_cap, refresh=args.refresh)
            results.append({"symbol": symbol, "status": data["status"], "bars": len(data["rows"]), "missingDates": len(data["missingDates"]), "error": data["error"], "cacheSha256": hashlib.sha256((args.series_dir / (symbol + ".json")).read_bytes()).hexdigest()})
        write_json(args.study.parent / "crypto-price-manifest.json", {"provider": "Coinbase Exchange public USD candles", "cutoff": study["cutoff"], "calendar": "UTC daily buckets", "series": results, "requestsThisInvocation": count, "separateFromEquity": True,
                  "identityNote": "The source-review agent identifies the named crypto asset. This adapter verifies the exact Coinbase base/USD product; broad or ambiguous crypto views remain unscored."})
        print(json.dumps({"symbols": len(results), "bars": sum(r["bars"] for r in results), "requests": count}))
        return 0
    except (ValueError, KeyError, OSError, ProviderError, TypeError) as error:
        print(json.dumps({"status": "failed", "code": error.code if isinstance(error, ProviderError) else "invalid_input_or_checkpoint"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
