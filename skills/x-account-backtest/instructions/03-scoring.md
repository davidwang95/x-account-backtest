# Deterministic prices, scoring and statistics

Read [methodology](../references/methodology.md) before measuring outcomes. The baseline implementation supports confirmed US equities/ETFs against SPY and separately identified cryptocurrency USD spot prices. It does not claim universal market support.

Check source eligibility first. If there are no core-eligible US views, do not request a stock-price key or retrieve SPY unnecessarily. Run the engine with an empty US price directory; it retains the candidates as unscored and records that no equity calendar/price cutoff was evaluated. Identified cryptocurrency views can still use the separate USD-price directory and crypto audit. A macro-only result is a coverage/eligibility report, with unavailable performance estimates.

Cache split-adjusted daily bars with source timestamps, units, venue, requested ranges, adjustments and hashes. The price adapter consumes validated dated security mappings. Review ticker changes and gaps explicitly; default adapter behavior does not invent alias history. If a verified rename needs stitching, construct a separate dated series with same-security proof and validate overlapping bars. Never fill a terminal delisting gap with zero or the acquirer's price.

Use completed available bars only. If the originally planned cutoff includes a session not yet supplied by the provider, freeze a revised cutoff before measurement and revalidate the plan, explaining the available date. The benchmark calendar must reconcile independently to NYSE sessions or an explicitly verified calendar. A missing benchmark bar cannot silently shorten the horizon.

Run `run_backtest.py` only after the source validation receipt passes. It emits every call × horizon, including unscored and pending rows, then statistics, a source-linked ledger, hashes and independent Decimal return reconciliation. Inspect `verification.json` and `metadata.calendarAudit` before report assembly.

Primary figures are hits/(hits+misses), neutral/pending/unscored counts, mean/median signed asset return, market-relative hit rate and mean/median relative return. Break down direction, source kind, action type, thesis and publication cohort. Report cell counts next to rates. Small cells are descriptive; avoid an unsupported ranking.

Include Wilson intervals as descriptive and seeded entry-month bootstrap intervals with residual-dependence limits. Compare the same call direction on SPY. Include explicit-only, classification-confidence, repeat-suppressed, equal-security and common-horizon-cohort sensitivities. Distinguish incomplete maturity from missing historical prices. If no eligible calls have measurable outcomes, report coverage and exclusions; the hit rate is unavailable, not 0% or 50%.

Crypto uses its own calendar-day horizons and conservative entry. Record venue-specific USD spot identity/price provenance; do not mix it into stock or benchmark statistics. Other asset/proxy studies need a frozen identity, market calendar, price and benchmark contract plus tests before scoring.
