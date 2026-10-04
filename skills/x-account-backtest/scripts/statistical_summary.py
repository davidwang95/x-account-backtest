"""Descriptive statistics from frozen event-study outcomes.

Wilson intervals assume independent binary observations. The primary-horizon
calendar-month cluster bootstrap preserves within-month common shocks, but is
not a complete correction for cross-month repeated tickers or overlapping long
holding periods. No causal or predictive-skill significance claim is implied.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics

import numpy as np
from study_utils import safe_csv_cell

PRIMARY_HORIZON = 21
BOOTSTRAP_ITERATIONS = 2000
BOOTSTRAP_SEED = 0
SCORED = {"hit", "miss", "neutral"}


def security_identity(row: dict) -> str:
    """Validated same-security rename key, with literal-ticker fallback.

    The data adapter is responsible for verifying FIGI continuity. This helper
    never infers corporate relationships or groups acquirers with acquired stock.
    """
    return str(row.get("securityId") or row.get("canonicalSymbol") or row.get("symbol") or "unmapped")


def wilson_interval(hits: int, denominator: int, z: float = 1.959963984540054) -> list[float] | None:
    if denominator == 0:
        return None
    if not 0 <= hits <= denominator:
        raise ValueError("Wilson interval needs 0 <= hits <= denominator.")
    rate = hits / denominator
    scale = 1 + z * z / denominator
    center = (rate + z * z / (2 * denominator)) / scale
    half = z / scale * math.sqrt(rate * (1 - rate) / denominator + z * z / (4 * denominator ** 2))
    return [max(0, center - half) * 100, min(1, center + half) * 100]


def _percentile(values: list[float], quantile: float) -> float | None:
    return float(np.quantile(values, quantile)) if values else None


def summarize_group(rows: list[dict]) -> dict:
    counts = Counter(row["status"] for row in rows)
    eligible = [row for row in rows if row["status"] in SCORED]
    source_eligible = [row for row in rows if row.get("assetClass") in {"equity", "etf"}
                       and row.get("symbol") and row.get("direction") in {"bullish", "bearish"}
                       and row.get("kind") in {"explicit", "inferred"} and not row.get("excluded")
                       and row.get("issuerMappingValidated") is True
                       and (not row.get("reviewReason") or row.get("reviewed"))]
    returns = [row["directionalReturnPct"] for row in eligible]
    relative = [row["relativeReturnPct"] for row in eligible]
    hits, misses = counts["hit"], counts["miss"]
    denominator = hits + misses
    relative_counts = Counter(row["relativeStatus"] for row in eligible)
    relative_denominator = relative_counts["hit"] + relative_counts["miss"]
    winners = [row["directionalReturnPct"] for row in eligible if row["status"] == "hit"]
    losers = [row["directionalReturnPct"] for row in eligible if row["status"] == "miss"]
    raw = [row["rawReturnPct"] for row in eligible]
    baseline = [row["spyReturnPct"] * (1 if row["direction"] == "bullish" else -1) for row in eligible]
    baseline_hits, baseline_misses = sum(x > 0 for x in baseline), sum(x < 0 for x in baseline)
    cost_adjusted = [value - .25 for value in returns]
    cost_hits, cost_misses = sum(value > 0 for value in cost_adjusted), sum(value < 0 for value in cost_adjusted)
    mean = lambda values: statistics.mean(values) if values else None
    median = lambda values: statistics.median(values) if values else None
    return {
        "calls": len(rows), "sourceEligibleCalls": len(source_eligible), "sourceIneligibleCalls": len(rows) - len(source_eligible),
        "pendingEligibleCalls": sum(row["status"] == "pending" for row in source_eligible),
        "unscoredEligibleCalls": sum(row["status"] == "unscored" for row in source_eligible),
        "scored": len(eligible), "binaryDenominator": denominator,
        "hits": hits, "misses": misses, "neutral": counts["neutral"], "pending": counts["pending"], "unscored": counts["unscored"],
        "hitRatePct": hits / denominator * 100 if denominator else None,
        "hitRateWilson95": wilson_interval(hits, denominator),
        "meanDirectionalReturnPct": mean(returns), "medianDirectionalReturnPct": median(returns),
        "p10DirectionalReturnPct": _percentile(returns, .1), "p90DirectionalReturnPct": _percentile(returns, .9),
        "meanWinnerPct": mean(winners), "meanLoserPct": mean(losers),
        "meanRawReturnPct": mean(raw), "meanRelativeReturnPct": mean(relative), "medianRelativeReturnPct": median(relative),
        "relativeHits": relative_counts["hit"], "relativeMisses": relative_counts["miss"], "relativeNeutral": relative_counts["neutral"],
        "relativeBinaryDenominator": relative_denominator,
        "relativeHitRatePct": relative_counts["hit"] / relative_denominator * 100 if relative_denominator else None,
        "relativeHitRateWilson95": wilson_interval(relative_counts["hit"], relative_denominator),
        "sameDirectionSpyHitRatePct": baseline_hits / (baseline_hits + baseline_misses) * 100 if baseline_hits + baseline_misses else None,
        "sameDirectionSpyMeanReturnPct": mean(baseline),
        "uniqueTickers": len({security_identity(row) for row in eligible}),
        "uniqueSecurities": len({security_identity(row) for row in eligible}),
        "uniqueHistoricalTickers": len({row["symbol"] for row in eligible}),
        "entryMonths": len({row["entryDate"][:7] for row in eligible}),
        "meanMinus25bpPct": mean(cost_adjusted), "after25bpBinaryDenominator": cost_hits + cost_misses,
        "hitRateAfter25bpPct": cost_hits / (cost_hits + cost_misses) * 100 if cost_hits + cost_misses else None,
        "unscoredReasons": dict(Counter(row.get("reason") or "Unspecified" for row in rows if row["status"] == "unscored")),
    }


def month_cluster_bootstrap(rows: list[dict], *, iterations=BOOTSTRAP_ITERATIONS, seed=BOOTSTRAP_SEED) -> dict:
    eligible = [row for row in rows if row["status"] in SCORED]
    by_month = defaultdict(list)
    for row in eligible:
        by_month[row["entryDate"][:7]].append(row)
    months = sorted(by_month)
    result = {"method": "Resample observed entry-calendar-month clusters with replacement, preserving all calls within each sampled month.",
              "iterations": iterations, "seed": seed, "months": len(months), "confidenceLevel": .95,
              "limitations": "Does not fully account for repeated tickers across months or cross-month overlapping horizons; descriptive robustness only.",
              "hitRatePct95": None, "meanDirectionalReturnPct95": None,
              "relativeHitRatePct95": None, "meanRelativeReturnPct95": None, "reason": None}
    if len(months) < 2:
        result["reason"] = "Fewer than two observed entry-month clusters; bootstrap uncertainty is unidentified."
        return result
    if type(iterations) is not int or iterations < 100:
        raise ValueError("Use at least 100 bootstrap iterations.")
    # Columns: raw binary hits, binary n, relative binary hits, relative binary n,
    # directional sum, relative sum, scored n. Summing cluster sufficient statistics
    # exactly reproduces call-weighted resampling, without huge row-index arrays.
    cells = []
    for month in months:
        group = by_month[month]
        cells.append([
            sum(row["status"] == "hit" for row in group), sum(row["status"] in {"hit", "miss"} for row in group),
            sum(row["relativeStatus"] == "hit" for row in group), sum(row["relativeStatus"] in {"hit", "miss"} for row in group),
            sum(row["directionalReturnPct"] for row in group), sum(row["relativeReturnPct"] for row in group), len(group)])
    cells = np.asarray(cells, dtype=float)
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(months), size=(iterations, len(months)))
    totals = cells[draws].sum(axis=1)
    metrics = {"hitRatePct95": (0, 1, 100), "relativeHitRatePct95": (2, 3, 100),
               "meanDirectionalReturnPct95": (4, 6, 1), "meanRelativeReturnPct95": (5, 6, 1)}
    for key, (numerator, denominator, multiplier) in metrics.items():
        valid = totals[:, denominator] > 0
        values = totals[valid, numerator] / totals[valid, denominator] * multiplier
        result[key] = [float(x) for x in np.quantile(values, [.025, .975])] if len(values) else None
    if len(months) < 12:
        result["reason"] = "Few observed entry-month clusters; intervals may be unstable."
    return result


def ticker_balanced(rows: list[dict]) -> dict:
    by_symbol = defaultdict(list)
    for row in rows:
        if row["status"] in SCORED:
            by_symbol[security_identity(row)].append(row)
    metrics = [summarize_group(group) for group in by_symbol.values()]
    def average(key):
        values = [metric[key] for metric in metrics if metric[key] is not None]
        return statistics.mean(values) if values else None
    return {"method": "Compute each validated security's rate/mean, then equally weight securities with available outcomes. FIGI-validated rename aliases are unified; acquisitions across securities are not merged.",
            "tickers": len(metrics), "hitRatePct": average("hitRatePct"), "relativeHitRatePct": average("relativeHitRatePct"),
            "meanDirectionalReturnPct": average("meanDirectionalReturnPct"), "meanRelativeReturnPct": average("meanRelativeReturnPct")}


def episode_subset(rows: list[dict], spacing: int = 21) -> list[dict]:
    """First observation in each ticker-direction episode, separated by spacing.

    Membership uses event timing only, never the realized return or hit/miss.
    Unscored events with a known entry-session index still establish an episode;
    a later scoreable repeat cannot replace an earlier missing-price event.
    """
    last_index = {}
    retained = []
    if type(spacing) is not int or spacing < 1:
        raise ValueError("Episode spacing must be a positive session count.")
    def chronological_key(row):
        try:
            stamp = datetime.fromisoformat(row["publishedAt"].replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ValueError("Timezone-free timestamp.")
            return (stamp.astimezone(timezone.utc), row["callId"])
        except (ValueError, KeyError, TypeError, AttributeError):
            return (datetime.max.replace(tzinfo=timezone.utc), row["callId"])
    for row in sorted(rows, key=chronological_key):
        index = row.get("entrySessionIndex")
        if index is None:
            retained.append(row)
            continue
        key = (security_identity(row), row["direction"])
        previous = last_index.get(key)
        if previous is not None and index - previous < spacing:
            continue
        retained.append(row)
        last_index[key] = index
    return retained


def build_summary(payload: dict | list[dict], *, primary=PRIMARY_HORIZON, iterations=BOOTSTRAP_ITERATIONS,
                  seed=BOOTSTRAP_SEED, episode_spacing: int | None = None) -> dict:
    rows = payload["outcomes"] if isinstance(payload, dict) else payload
    identities = [(row["callId"], row["horizon"]) for row in rows]
    if len(set(identities)) != len(identities):
        raise ValueError("Duplicated call-horizon outcomes would inflate statistical denominators.")
    horizons = sorted({row["horizon"] for row in rows})
    if type(primary) is not int or primary < 1 or (horizons and primary not in horizons):
        raise ValueError("The primary horizon must be a positive configured horizon.")
    spacing = primary if episode_spacing is None else episode_spacing
    if type(spacing) is not int or spacing < 1:
        raise ValueError("Episode spacing must be a positive session count.")
    primary_rows = [row for row in rows if row["horizon"] == primary]
    summary = {"metadata": {
        "primaryHorizon": primary, "horizons": horizons, "bootstrapSeed": seed, "episodeSpacingSessions": spacing,
        "bootstrapIterations": iterations, "returnUnits": "percentage points",
        "hitRateDenominator": "Hits + misses; exact zero moves neutral and excluded.",
        "neutralMeanTreatment": "Neutral scored returns are included in return means/medians.",
        "wilsonInterpretation": "95% binomial Wilson interval assuming independent calls; descriptive, not evidence of predictive skill.",
        "bootstrapInterpretation": "95% percentile interval resampling entry-month clusters; cross-month dependencies remain.",
        "sourceConfidenceInterpretation": "Model source-interpretation confidence is uncalibrated; it is not a probability of forecast correctness.",
        "securityGrouping": "Use validated securityId, then canonicalSymbol, then historical symbol. Same-security rename aliases are unified for security-balanced/episode/concentration counts; acquisitions remain separate.",
        "coverageWarning": "Findings apply to retrieved and classified public X posts, not a verified complete archive of all calls.",
        "primarySelection": f"Configured primary window: {primary} sessions. Freeze this choice in the study contract before outcome inspection; other horizons and subgroups are descriptive sensitivities."
    }, "primary": summarize_group(primary_rows), "byHorizon": {}, "byGroup": {}, "tickerBalanced": ticker_balanced(primary_rows)}
    for horizon in horizons:
        horizon_rows = [row for row in rows if row["horizon"] == horizon]
        summary["byHorizon"][str(horizon)] = summarize_group(horizon_rows)
    summary["primary"]["monthClusterBootstrap"] = month_cluster_bootstrap(primary_rows, iterations=iterations, seed=seed)
    groupers = {
        "direction": lambda row: row.get("direction") or "unknown",
        "kind": lambda row: row.get("kind") or "unknown",
        "callType": lambda row: row.get("callType") or "unspecified",
        "thesis": lambda row: row.get("thesis") or "unspecified",
        "sector": lambda row: row.get("sector") or "unmapped",
        "year": lambda row: (row.get("publicationDateET") or row.get("publishedAt") or "unknown")[:4],
    }
    for dimension, grouper in groupers.items():
        groups = defaultdict(list)
        for row in primary_rows:
            groups[grouper(row)].append(row)
        summary["byGroup"][dimension] = {key: summarize_group(group) for key, group in sorted(groups.items())}
        # Detailed type- and bias-level uncertainty retains the same estimator.
        if dimension in {"direction", "kind", "callType", "thesis"}:
            for key, group in groups.items():
                group_seed = seed + int(hashlib.sha256(f"{dimension}:{key}".encode()).hexdigest()[:6], 16)
                summary["byGroup"][dimension][key]["monthClusterBootstrap"] = month_cluster_bootstrap(group, iterations=iterations, seed=group_seed)
    episodes = episode_subset(primary_rows, spacing)
    summary["episodeSensitivity"] = {"method": f"Retain earliest validated-security/direction event; suppress repeats until {spacing} SPY sessions after that retained event. Outcome-blind membership; historical rename aliases are unified.",
                                     "retainedCalls": len(episodes), "suppressedCalls": len(primary_rows) - len(episodes), "summary": summarize_group(episodes)}
    high_confidence = [row for row in primary_rows if isinstance(row.get("confidence"), (int, float)) and row["confidence"] >= .90]
    explicit_only = [row for row in primary_rows if row.get("kind") == "explicit"]
    summary["highConfidenceSensitivity"] = {"threshold": .90, "method": "Source-interpretation confidence at least 0.90; descriptive sensitivity selected before outcome review. Core eligibility/scoring rules remain unchanged.",
                                             "warning": "Confidence is uncalibrated and does not estimate forecast success.",
                                             "summary": summarize_group(high_confidence),
                                             "monthClusterBootstrap": month_cluster_bootstrap(high_confidence, iterations=iterations, seed=seed)}
    summary["explicitOnlySensitivity"] = {"method": "Source kind explicit only at the primary horizon, using unchanged core eligibility/scoring rules.",
                                            "summary": summarize_group(explicit_only),
                                            "monthClusterBootstrap": month_cluster_bootstrap(explicit_only, iterations=iterations, seed=seed)}
    complete_horizons = defaultdict(set)
    for row in rows:
        if row["status"] in SCORED:
            complete_horizons[row["callId"]].add(row["horizon"])
    common_ids = {identity for identity, complete in complete_horizons.items() if complete == set(horizons)}
    summary["commonMatureCohort"] = {
        "callCount": len(common_ids), "horizons": horizons,
        "method": "Fixed intersection of calls with hit/miss/neutral outcomes at every configured horizon; requires both horizon maturity and complete required prices.",
        "limitation": "An older, fully observed cohort; it can differ from recent calls and excludes names with any missing required prices.",
        "byHorizon": {str(horizon): summarize_group([row for row in rows if row["callId"] in common_ids and row["horizon"] == horizon]) for horizon in horizons}
    }
    calendar_sessions = payload.get("metadata", {}).get("calendarSessions") if isinstance(payload, dict) else None
    missing_sensitivity = {"method": "Among source-eligible, already-matured events, assign every price-unscored observation as a miss versus a hit. These arithmetic coverage bounds do not impute returns or reconstruct delisting proceeds.",
                           "neutralTreatment": "Observed exact-zero outcomes remain outside the binary denominator; missing observations could also be neutral, whose rate lies within these extremes.",
                           "byHorizon": {}}
    for horizon in horizons:
        selected = [row for row in rows if row["horizon"] == horizon]
        mature_missing = [row for row in selected if row["status"] == "unscored"
                          and row.get("assetClass") in {"equity", "etf"} and row.get("symbol")
                          and row.get("direction") in {"bullish", "bearish"} and row.get("kind") in {"explicit", "inferred"}
                          and not row.get("excluded") and (not row.get("reviewReason") or row.get("reviewed"))
                          and row.get("issuerMappingValidated") is True
                          and isinstance(row.get("entrySessionIndex"), int) and isinstance(calendar_sessions, int)
                          and row["entrySessionIndex"] + horizon < calendar_sessions]
        metric = summary["byHorizon"][str(horizon)]
        n = metric["binaryDenominator"] + len(mature_missing)
        missing_sensitivity["byHorizon"][str(horizon)] = {
            "observedHitRatePct": metric["hitRatePct"], "observedBinaryDenominator": metric["binaryDenominator"],
            "maturePriceUnscored": len(mature_missing) if isinstance(calendar_sessions, int) else None,
            "allMissingMissHitRatePct": 100 * metric["hits"] / n if n and isinstance(calendar_sessions, int) else None,
            "allMissingHitHitRatePct": 100 * (metric["hits"] + len(mature_missing)) / n if n and isinstance(calendar_sessions, int) else None,
            "assumption": "Maturity assessed from SPY session index even if a required stock entry/exit bar is absent."}
    summary["missingPriceSensitivity"] = missing_sensitivity
    summary["typeHorizon"] = {}
    for kind in sorted({row.get("callType") or "unspecified" for row in rows}):
        summary["typeHorizon"][kind] = {str(h): summarize_group([row for row in rows if (row.get("callType") or "unspecified") == kind and row["horizon"] == h]) for h in horizons}
    return summary


def write_summary(summary: dict, output_dir: str | Path) -> None:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    rows = []
    for horizon, metric in summary["byHorizon"].items():
        rows.append({"dimension": "horizon", "group": horizon, **metric})
    for dimension, groups in summary["byGroup"].items():
        for group, metric in groups.items():
            rows.append({"dimension": dimension, "group": group, **metric})
    fields = ["dimension", "group", "calls", "scored", "binaryDenominator", "hits", "misses", "neutral", "pending", "unscored",
              "hitRatePct", "meanDirectionalReturnPct", "medianDirectionalReturnPct", "relativeHitRatePct", "meanRelativeReturnPct", "uniqueTickers"]
    with (destination / "summary.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows({key: safe_csv_cell(value) for key, value in row.items()} for row in rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outcomes", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--primary", type=int, default=PRIMARY_HORIZON)
    parser.add_argument("--episode-spacing", type=int, help="Repeat-suppression spacing in sessions; defaults to primary.")
    parser.add_argument("--iterations", type=int, default=BOOTSTRAP_ITERATIONS)
    parser.add_argument("--seed", type=int, default=BOOTSTRAP_SEED)
    args = parser.parse_args()
    payload = json.loads(args.outcomes.read_text(encoding="utf-8"))
    summary = build_summary(payload, primary=args.primary, iterations=args.iterations,
                            seed=args.seed, episode_spacing=args.episode_spacing)
    write_summary(summary, args.output_dir)
    print(json.dumps({key: summary["primary"][key] for key in ("calls", "scored", "hitRatePct", "meanDirectionalReturnPct", "relativeHitRatePct")}))
