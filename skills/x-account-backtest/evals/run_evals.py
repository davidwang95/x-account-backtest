"""Run synthetic, offline end-to-end evaluations against the actual scripts.

No real profile/post/security/price claim is made by this test corpus.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import hashlib
import math
from pathlib import Path
import subprocess
import sys
import tempfile

SKILL = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL / "scripts"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "fixture-study.json"
sys.path.insert(0, str(SCRIPTS))
from validate_calls import validate  # Source validation only; expected statistics do not import the engine.


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def command(script, arguments, *, reject=None):
    result = subprocess.run([sys.executable, str(SCRIPTS / script), *map(str, arguments)],
                            cwd=SKILL, capture_output=True, text=True, encoding="utf-8")
    if reject is None:
        if result.returncode:
            diagnostic = ""
            if script == "run_backtest.py":
                verification = Path(arguments[arguments.index("--data-dir") + 1]) / "verification.json"
                if verification.exists():
                    diagnostic = "\nVerification errors: " + json.dumps(read(verification).get("errors"))
            raise AssertionError(script + " failed:\n" + result.stdout + result.stderr + diagnostic)
    else:
        if result.returncode == 0 or reject not in result.stdout + result.stderr:
            raise AssertionError(script + " did not reject the expected condition: " + reject + "\n" + result.stdout + result.stderr)
    return result


def materialize(spec, destination, *, subset=None, without_prices=False):
    """Construct complete source rows and dated, explicitly fictional proofs."""
    data_dir, price_dir = destination / "data", destination / "prices"
    data_dir.mkdir(parents=True)
    price_dir.mkdir()
    command("prepare_study.py", ["--account", spec["account"], "--start", spec["start"], "--end", spec["end"],
                                 "--cutoff", spec["cutoff"], "--primary", spec["primary"],
                                 "--horizons", ",".join(map(str, spec["horizons"])), "--seed", 42,
                                 "--output-dir", data_dir])
    study = read(data_dir / "study.json")
    study.update(synthetic=True, syntheticNotice=spec["notice"])
    study["account"]["id"] = "7890000000000000000"
    write(data_dir / "study.json", study)
    posts, calls, reviews = [], [], []
    selected = [post for post in spec["posts"] if subset is None or post["id"] in subset]
    for raw in selected:
        text = "SYNTHETIC FIXTURE: " + raw["text"]
        url = "https://x.com/" + spec["account"] + "/status/" + raw["id"]
        stamp = raw["day"] + "T15:00:00Z"
        posts.append({"id": raw["id"], "account": spec["account"], "authorId": study["account"]["id"], "publishedAt": stamp,
                      "text": text, "url": url, "isReply": False, "isQuote": False, "synthetic": True})
        has_call = "kind" in raw
        reviews.append({"postId": raw["id"], "reason": raw["reason"], "callCount": int(has_call), "synthetic": True})
        if not has_call:
            continue
        asset = raw.get("assetClass", "equity")
        symbol = raw.get("symbol")
        canonical = raw.get("canonicalSymbol", symbol)
        one = {"id": "synthetic-" + raw["id"], "postId": raw["id"], "publishedAt": stamp, "postUrl": url,
               "postText": text, "evidence": raw["text"], "assetClass": asset,
               "symbol": symbol, "canonicalSymbol": canonical, "instrument": "SYNTHETIC " + (symbol or "macro view"),
               "direction": raw["direction"], "kind": raw["kind"], "callType": raw["callType"], "thesis": raw["thesis"],
               "confidence": 0.99, "reviewReason": raw.get("reviewReason", ""), "synthetic": True,
               "issuerMappingValidated": asset in {"equity", "etf"}}
        if asset in {"equity", "etf"}:
            identity = "SYNTHETIC:" + canonical
            one["securityId"] = identity
            one["issuerReference"] = {"asOf": raw["day"], "status": "ok", "synthetic": True,
                                      "sourceUrl": "https://example.invalid/synthetic-security/" + symbol + "/" + raw["day"],
                                      "metadata": {"ticker": symbol, "name": "SYNTHETIC " + symbol, "market": "stocks",
                                                   "locale": "us", "type": "ETF" if asset == "etf" else "CS",
                                                   "composite_figi": identity, "synthetic": True}}
            one['issuerReference']['responseSha256'] = hashlib.sha256(json.dumps(one['issuerReference']['metadata'],sort_keys=True).encode()).hexdigest()
            if raw.get("sameIssuerChain"):
                one["sameIssuerChain"] = raw["sameIssuerChain"]
                one["sameSecurityProof"] = {"synthetic": True, "securityId": identity,
                                             "sourceUrl": "https://example.invalid/synthetic-renames/ALPHA-ALPHNEW",
                                             "asOf": raw["day"], "sameSecurity": True,
                                             "note": "Invented rename proof for an offline test; no acquisition or real issuer claim."}
        calls.append(one)
    write(data_dir / "posts.json", {"synthetic": True, "notice": spec["notice"], "posts": posts})
    write(data_dir / "calls.json", {"synthetic": True, "notice": spec["notice"], "calls": calls})
    write(data_dir / "post-reviews.json", {"synthetic": True, "posts": reviews})
    for symbol, values in (() if without_prices else spec["openingPrices"].items()):
        rows = [{"date": day, "open": value, "synthetic": True}
                for day, value in zip(spec["calendar"], values) if value is not None]
        write(price_dir / (symbol + ".json"), {"synthetic": True, "notice": spec["notice"], "rows": rows})
    calendar = destination / "calendar.json"
    write(calendar, {"synthetic": True, "source": "Hand-supplied independent synthetic fixture session calendar",
                     "sessions": spec["calendar"]})
    write(data_dir / "price-manifest.json", {"synthetic": True, "source": "Invented fixture prices; not market observations.",
                                             "cutoff": spec["cutoff"], "splitAdjusted": True})
    command("validate_calls.py", ["--calls", data_dir / "calls.json", "--posts", data_dir / "posts.json",
                                  "--reviews", data_dir / "post-reviews.json", "--study", data_dir / "study.json",
                                  "--output-dir", data_dir])
    return data_dir, price_dir, calendar, study, posts, calls, reviews


def score(data_dir, price_dir, calendar, *, reject=None):
    return command("run_backtest.py", ["--data-dir", data_dir, "--series-dir", price_dir,
                                       "--calendar-file", calendar], reject=reject)


def invalid_source(calls, posts, reviews, study, fragment):
    try:
        validate(calls, posts, reviews, study)
    except ValueError as exc:
        if fragment not in str(exc):
            raise AssertionError("Source validator failed for a different reason: " + str(exc)) from exc
    else:
        raise AssertionError("Source validator accepted invalid input: " + fragment)


def evaluate(spec):
    checks = 0
    passed_cases = []
    def check(condition, message):
        nonlocal checks
        checks += 1
        if not condition:
            raise AssertionError(message)
    def number(actual, expected, message):
        check(isinstance(actual, (int, float)) and math.isclose(actual, expected, rel_tol=0, abs_tol=1e-9), message)
    with tempfile.TemporaryDirectory(prefix="synthetic-x-backtest-") as temporary:
        workspace = Path(temporary)
        data, prices, calendar, study, posts, calls, reviews = materialize(spec, workspace / "primary")
        score(data, prices, calendar)
        result, outcomes = read(data / "summary.json"), read(data / "outcomes.json")
        expected, metric = spec["expected"], result["primary"]
        receipt = read(data / "classification-manifest.json")
        check(receipt["sourcePosts"] == expected["sourcePosts"], "Recovered source count differs.")
        check(receipt["classifiedPosts"] == expected["sourcePosts"], "Review coverage differs.")
        check(receipt["calls"] == expected["calls"], "Call count differs.")
        for key in ("scored", "hits", "misses", "neutral", "pending", "unscored", "binaryDenominator",
                    "relativeHits", "relativeMisses", "relativeNeutral", "relativeBinaryDenominator", "uniqueSecurities"):
            check(metric[key] == expected[key], "Primary statistic differs: " + key)
        check(metric["sourceEligibleCalls"] == expected["primarySourceEligible"], "Source eligibility differs.")
        number(metric["hitRatePct"], 400 / 7, "Raw hit rate differs from 4/7.")
        number(metric["relativeHitRatePct"], 50, "Relative hit rate differs from 4/8.")
        number(metric["meanDirectionalReturnPct"], 1.25, "Directional mean differs from by-hand +1.25%.")
        expected_relative_mean = sum(row["relativeReturnPct"] for row in expected["primaryRows"].values()
                                     if "relativeReturnPct" in row) / expected["scored"]
        number(metric["meanRelativeReturnPct"], expected_relative_mean, "Relative mean differs.")
        primary = {row["postId"]: row for row in outcomes["outcomes"] if row["horizon"] == spec["primary"]}
        for pid, expected_row in expected["primaryRows"].items():
            for key, expected_value in expected_row.items():
                if isinstance(expected_value, (int, float)):
                    number(primary[pid][key], expected_value, "Event return differs: " + pid + " " + key)
                else:
                    check(primary[pid][key] == expected_value, "Event field differs: " + pid + " " + key)
        check(result["episodeSensitivity"]["suppressedCalls"] == expected["episodeSuppressed"], "Rename repeat not suppressed.")
        check(result["episodeSensitivity"]["summary"]["scored"] == expected["episodeScored"], "Episode scored count differs.")
        number(result["tickerBalanced"]["hitRatePct"], 50, "Security-balanced raw hit rate differs.")
        number(result["episodeSensitivity"]["summary"]["hitRatePct"], 50, "Repeat-suppressed raw hit rate differs.")
        check(metric["monthClusterBootstrap"]["hitRatePct95"] is None, "Single-month fixture must not imply cluster precision.")
        check(read(data / "verification.json")["passed"], "Independent production verification failed.")
        passed_cases.extend(["known-event-study", "entry-and-missing-bars"])

        invalid_source(calls, posts, reviews[:-1], study, "cover every source ID")
        checks += 1
        invalid_source(calls, posts, reviews + reviews[:1], study, "cover every source ID")
        checks += 1
        passed_cases.append("source-review-completeness")
        altered = deepcopy(calls)
        altered[0]["evidence"] = "This sentence is absent from the synthetic post."
        invalid_source(altered, posts, reviews, study, "exact nonempty substring")
        checks += 1
        altered = deepcopy(calls)
        altered[0]["postText"] += " invented supplement"
        invalid_source(altered, posts, reviews, study, "preserve the full authored post")
        checks += 1
        passed_cases.append("exact-source-evidence")
        altered = deepcopy(calls)
        altered[0]["issuerReference"]["asOf"] = "2024-01-01"
        invalid_source(altered, posts, reviews, study, "dated US security evidence")
        checks += 1
        altered = deepcopy(calls)
        altered[-1].pop("sameSecurityProof")
        invalid_source(altered, posts, reviews, study, "same-security proof")
        checks += 1
        passed_cases.append("dated-security-identity")

        for filename in ("calls.json", "posts.json", "post-reviews.json", "study.json"):
            path = data / filename
            frozen = path.read_bytes()
            payload = read(path)
            payload["syntheticTamperProbe"] = "Edited after source classification froze."
            write(path, payload)
            try:
                score(data, prices, calendar, reject="Frozen source/classification changed: " + filename)
                checks += 1
            finally:
                path.write_bytes(frozen)
        passed_cases.append("frozen-input-integrity")
        spy_path = prices / "SPY.json"
        frozen = spy_path.read_bytes()
        payload = read(spy_path)
        payload["rows"] = [row for row in payload["rows"] if row["date"] != "2024-01-04"]
        write(spy_path, payload)
        try:
            score(data, prices, calendar, reject="SPY trading-session calendar is incomplete")
            checks += 1
        finally:
            spy_path.write_bytes(frozen)
        passed_cases.append("missing-benchmark-session")

        for label, subset in (("empty", {"1013"}), ("macro-only", {"1010"})):
            empty_data, empty_prices, empty_calendar, *_ = materialize(spec, workspace / label, subset=subset, without_prices=True)
            score(empty_data, empty_prices, empty_calendar)
            empty = read(empty_data / "summary.json")["primary"]
            check(empty["scored"] == 0, label + " invented scored observations.")
            check(empty["hitRatePct"] is None, label + " invented an accuracy rate.")
            check(empty["meanDirectionalReturnPct"] is None, label + " invented a return mean.")
            metadata = read(empty_data / "outcomes.json")["metadata"]
            check(metadata["cutoff"] is None, label + " invented a measured-price cutoff.")
            check(metadata["sessionDates"] == [], label + " invented a market calendar.")
            check(metadata["calendarAudit"].get("notRequired") is True, label + " required unnecessary benchmark access.")
        passed_cases.append("macro-only-and-empty")
    return {"synthetic": True, "passed": True, "networkRequests": 0, "checks": checks,
            "passedCases": passed_cases, "note": "Only invented offline fixtures were used; behavioral skill review is separate."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Optional destination for the evaluation receipt.")
    args = parser.parse_args()
    spec = read(FIXTURE)
    if spec.get("synthetic") is not True:
        raise ValueError("The offline evaluation accepts only the marked synthetic fixture.")
    result = evaluate(spec)
    if args.output:
        write(args.output, result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
