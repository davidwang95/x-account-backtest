# Source-only call interpretation

Use the current agent to review the prepared source-only batches. Read [contracts](../references/data-contracts.md). Do not call a separately configured model API. Do not create a bullish/bearish rule from ticker keywords or sentiment words and label it AI inference.

For each post, record one `post-reviews.json` entry, including no-call posts. Extract each distinct investment view into the call book with an exact evidence substring and the full authored text. A multi-asset post may contain multiple calls. One source can support conflicting or conditional views; preserve the source qualifications instead of forcing a single direction.

Classify:

- `explicit`: prospective buy/add/hold/sell/trim/avoid/short recommendation.
- `inferred`: a clear prospective bullish/bearish investment view without an explicit action.
- `conditional`: depends on a price/event/condition not established at publication.
- `exit`: reports a completed position change without a new unconditional forward view.
- `unclear`: a plausible interpretation needing review, including unresolved sarcasm or ambiguous attribution.

Only source-qualified `explicit`/`inferred` unconditional views can enter the core, and only after supported dated instrument verification. Conditional, exit, unclear and unsupported macro records do not enter core accuracy. The binary denominator is measured hits + misses; scored exact-zero outcomes are neutral and shown separately.

For a completed exit, the required direction may describe the reported action only (a completed stock sale is bearish action; a closed short is bullish action). Keep `kind=exit` and state in `reviewReason` that the sign is an action annotation, not a forward forecast. If even the completed action's sign is unclear, keep a no-call retrospective/unclear review instead of inventing a direction. Never promote an exit to a forecast merely to satisfy the schema.

Company/product praise, past performance, a factual earnings announcement and personal complaints alone do not establish prospective stock views. A hold is eligible only if it expresses a continuing bullish investment stance. Sell/avoid do not imply a tradable short position. Macro and environmental views may be clear directional calls, but remain unscored without a defensible predeclared instrument.

Use source-based action and primary-thesis categories. Assign one principal thesis; do not choose a label after seeing returns. Use an empty `reviewReason` only when the interpretation is unambiguous. Confidence is an uncalibrated text interpretation score. For reviewed ambiguities, save `adjudicationReason`, reviewer/model identity and the frozen source version. Do not erase the original interpretation history.

Resolve ticker/security identity **as of the publication date**, using reference data or official issuer/exchange evidence. A current ticker lookup is insufficient for old names, reused tickers and delisted securities. Record security identity, historical dates and proof. Only verified identical securities may share rename chains; acquisitions and different share classes remain separate. Reject vague “tech,” “the Fed” and broad crypto references as specific instrument identities.

Before outcomes, independently review a seeded source-only sample across publication cohorts plus ambiguous/high-frequency/inferred cases. Record disagreement counts and corrections without presenting the convenience sample as calibrated population precision/recall. Preserve no-call reviews to make missed extraction auditable.

Run `validate_calls.py` after instrument mapping and source adjudication. It rejects missing post reviews, fabricated quotations, mismatched links/timestamps and unsupported mapping validation. Freeze this receipt. `run_backtest.py` rejects subsequent changes to the plan, sources, reviews or call book.
