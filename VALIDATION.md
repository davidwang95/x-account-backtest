# Validation record

Validated locally on October 3, 2026.

- Skill frontmatter/discovery validation: passed using the Codex skill-creator validator.
- Offline Python unit suite: 94 passing tests covering source attribution, time zones, provider pagination/resumption, dated identities, price gaps, neutral denominators, bootstrap/repeated-call behavior, mixed stock/crypto ledger units, formula-safe CSVs, crypto-only reporting and source-only studies without stock-price access.
- Actual-script end-to-end evaluation: 105 assertions across eight synthetic offline cases, no network requests.
- Fresh source-only agent review: nine invented posts, expected routing matched. No outcome data or credentials were available to the reviewer. This does not estimate real-account precision/recall.
- Frozen Jim Cramer regression: 2,069 source views and 12,414 stock call/horizon rows; 99,312 compared date/status/price/return fields matched the original study. Primary results remain 1,283 scored stock/ETF calls, 646 hits, 637 misses, 50.4% directional accuracy and 47.9% market-relative accuracy. Separate crypto results remain 14 scored, five hits and nine misses.
- Report: the new Dave Wang Automation example was rebuilt with eight charts, checked for count/rate consistency, and rendered for every-page layout/attribution/link review. The report has no research-firm attribution and discloses skill-generated AI output.
- Footer revision: two aligned rows, approximately 10 pt CTA/website and at least 9 pt AI disclosure, with more than 30 pt clearance from the page edge. Both PDF backends passed the updated layout checks and the ten reporting contracts. The 18-page example's body text and source-link targets are unchanged.
- Public release: all 94 unit tests and 105 offline evaluation assertions rerun successfully. Official skill-installer downloaded the public GitHub skill into an isolated directory; all 42 source files matched the release commit. Metadata validation and study preparation for @DaveWangMIA succeeded from an unrelated working directory. Runtime imports were verified without a live provider request or account backtest.

The regression uses an existing frozen study. No fresh paid provider request was required for these maintenance checks. The installable package contains no actual recovered-post corpus, provider secrets or historical-price caches. The example PDF contains aggregate results and limited source-linked excerpts.

Unit/evaluation coverage cannot establish source completeness, historical executability, calibrated AI extraction accuracy or future investment performance. Free-form narrative still requires reconciliation against the frozen study.
