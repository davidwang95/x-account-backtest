# Measurement specification v1

## Universe and interpretation

Study dates select authored public posts by inclusive New York publication date. The recoverable provider corpus is not a verified all-history census. Classify every recovered post, including no-call posts. Preserve original author text and exact supporting spans; linked/media/third-party content is outside the standard universe.

The core stock study includes unconditional explicit or inferred bullish/bearish views, with a verified publication-date US security identity and no unresolved source-review condition. Conditional recommendations, ambiguous mentions, past trades/exits, unsupported markets and nonspecific macro views remain in the ledger without core scores. A bearish view does not imply an initiated short or feasible borrow.

## Stock timing and returns

Let `d=+1` for bullish and `d=-1` for bearish. Entry is the first SPY/NYSE session with a New York date strictly later than the publication date, including posts before the open. Daily opens are research proxies, not verified executable 09:30 fills. An `h`-session horizon exits at the same instrument's daily open exactly `h` session intervals after entry. This conservative rule keeps same-publication-day classification batches before the measured period.

Asset price return in percentage points is `R=100*(P_exit/P_entry-1)`. Signed return is `d*R`. SPY return is `B=100*(SPY_exit/SPY_entry-1)`. Signed market-relative return is `d*(R-B)`. These are split-adjusted price returns, excluding dividends, costs, slippage, stock borrow and some corporate-action proceeds. Market-relative return is neither beta adjusted nor sector adjusted; do not label it alpha.

Positive signed return is a hit; negative is a miss; exactly zero is neutral. Binary accuracy is hits/(hits+misses). Report neutrals separately while including them in mean/median returns. Apply the analogous rule separately to market-relative outcomes. Apply each call's direction to SPY over identical dates as a same-bias market baseline.

Use Decimal representations of supplied prices to avoid float cancellation changing a true neutral into a tiny hit/miss. No epsilon threshold silently changes the metric. Horizon pending means required future sessions have not matured at the frozen cutoff. Required bars absent at a matured date are unscored, never date-shifted, forward-filled or zero-imputed. Do not splice acquirers or separate share classes. Verified aliases can share a security ID only with dated same-security proof.

Stock primary default is 21 trading sessions; sensitivities 1/5/21/63/126/252. These are standardized subsequent-performance windows, not necessarily the time horizon promised by the author. A separate target/timing forecast evaluation requires predeclared source horizons and executable rules.

## Cryptocurrency

The separate crypto study uses identified USD spot instruments and daily UTC candles. Entry is 00:00 UTC on `NY publication date + 2 days`, the first UTC midnight strictly after the New York publication day has ended. This intentionally creates a variable conservative delay. Exit is the exact UTC date `h` calendar days later. Default primary is 30 days, sensitivities 1/7/30/90/365. No SPY benchmark or pooling with stock sessions. Missing exact candles remain unscored. Venue, USD pair identity and costs limits must be explicit.

## Statistical analysis

Publish counts and mean/median returns beside rates, including bullish/bearish, explicit/inferred, action, thesis, year and concentration. The principal call-level average weights every scored event equally. Categories with few events have unstable estimates; category rankings are exploratory and subject to selection/composition/multiple comparisons.

Wilson 95% intervals describe independent binary observations; repeated sources and overlapping windows violate independence. Use 2,000 seeded entry-month cluster-bootstrap draws for primary hit rates and mean returns. Months are sampled with replacement; retain all observations in selected months. Report the number of clusters and residual correlation across months/securities. Neither interval proves causal forecasting skill.

Sensitivities: equal weight per verified security; retain the earliest security/direction call and suppress repeats until one primary-window interval later; explicit source kind only; confidence at least 0.90 (uncalibrated interpretation); and the exact intersection of fully priced, mature calls across all configured horizons. The common cohort is older and selected for complete observed prices, not representative of all recent calls.

For source-eligible matured calls missing prices, show arithmetic hit-rate extremes if every missing outcome were a miss vs a hit. Do not impute returns. Retain all pending calls separately. A high accuracy can coexist with poor signed returns; high raw accuracy in rising markets can coexist with weak market-relative performance.

## Reproducibility and limitations

Freeze dates, horizons, interpretation/taxonomy and adjudications before outcome inspection. Current AI models can have retrospective information in training despite source-only batches; do not describe the study as historically executable or free of hindsight bias. Save source-only audit records, source/price hashes, code/tool versions, configuration, raw exclusion counts and an independently reconciled return ledger. The report measures historical subsequent prices, not compounded investor wealth or live strategy performance.
