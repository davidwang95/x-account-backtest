# Offline evaluation

Every profile, post, security identity, issuer reference and price in these fixtures is **synthetic**. X-shaped source links test attribution syntax; they do not assert the existence of a real profile or post. Reference URLs use `example.invalid` and are never fetched.

Run `python evals/run_evals.py` from the skill directory. The runner creates an isolated temporary study, invokes the actual preparation, validation and backtest scripts, compares results with hand-calculated fixture expectations, and removes generated artifacts when finished. It makes no network requests and does not use an AI provider.

A pass requires correct event timing, exact-date price use, distinct raw and benchmark-relative denominators, neutral handling, explicit and inferred source treatment, preserved missing/pending states, complete post review, verified rename grouping, and rejection of changed inputs after classification freezes. Arithmetic checks use fixture constants rather than the production statistics functions. Existing script unit tests separately cover crypto timing, invalid candles, daylight-saving boundaries and spreadsheet exports.

The behavioral review should use the finished `SKILL.md` and a fresh request such as “Backtest @example over the last three years and give me the PDF.” Check that the workflow resolves the publication period separately from the measurement horizon, uses actual available data, reports coverage limitations, records source evidence before inspecting outcomes, and produces the specified Dave Wang Automation disclosure and CTA. Flag missing routing or unclear prerequisites; do not treat fabricated market history as an acceptable fallback.
