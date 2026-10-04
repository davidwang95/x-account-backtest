# Report production

Read this reference when turning frozen backtest results into the final report.
The report describes automated historical measurements. Attribute it to the
X Account Backtest skill, under the Dave Wang Automation brand. It is not
personally authored investment research.

## Inputs

The report scripts accept a study directory containing:

- `study.json`: account identity, publication dates and source descriptions.
- `calls.json`: finalized interpretations, source URLs and evidence.
- `outcomes.json` and `summary.json`: scored equity results and statistics.
- `archive-month-counts.csv` and `archive-audit.json`, when available.
- `crypto-outcomes.json`, only when a separate cryptocurrency study exists.
- `narrative.json`: reviewed prose and selected, source-linked case reviews.

Required `study.json` fields are `account.handle`, `startDate` and `endDate`.
Account may instead be a handle string. Dates use ISO `YYYY-MM-DD` syntax.
Optional fields are `account.displayName`, `account.profileUrl`, `reportDate`,
`title`, `subtitle`, `sourceText`, `archiveSource`, `priceSource`, and
`cryptoPriceSource`. The report scripts support the SPY-session methodology;
`benchmark` must be `SPY` if supplied. Publication and price cutoffs remain
separate. `outcomes.json` supplies the verified price cutoff.

`narrative.json` contains:

```json
{
  "executiveSummary": ["A direct statement of the measured result and sample."],
  "keyFindings": ["A quantified finding with its practical limitation."],
  "sections": {
    "coverage": [], "methodology": [], "scoreability": [],
    "performance": [], "horizons": [], "callTypes": [], "theses": [],
    "regimes": [], "distribution": [], "concentration": [],
    "robustness": [], "uncertainty": [], "limitations": [],
    "crypto": [], "sources": []
  },
  "examples": [{"callId": "an-observed-call-id", "commentary": ["Explain the source and measured outcome."]}],
  "sourceItems": [{"label": "Provider documentation", "url": "https://provider.example/docs", "description": "What this source establishes."}]
}
```

Every numerical claim in prose must reconcile to the frozen statistics.
Selected case IDs must exist in the primary-horizon ledger. The renderer caps
quotations from each source post at 25 words across all selected cases. Do not
repeat the same source elsewhere to evade that cap.

## Page design and attribution

Use white pages, teal headings and positive values, charcoal text, and rust
for negative values. Preserve readable type, large charts, ample margins and
simple tables. Each chapter should answer a statistical question. Avoid
technical process labels in titles and unsupported statements about skill.

The cover states: **AI-generated backtest produced with the X Account Backtest
skill**. The running header says **Dave Wang Automation**. Every content page
includes the AI-generated skill disclosure and a linked call to action:
**Follow Dave Wang - AI for finance**, with **www.davewang.ai** for content and
resources. The cover and last page contain larger CTA boxes. These are
automation branding, not a byline or research attribution. Do not add a
company logo, company website or a "research by" label.

The running footer uses two aligned rows. Put the follow CTA at the left and
website at the right of the first row; put the AI/skill disclosure at the left
and page number at the right of the second row. Use approximately 10 pt CTA/link
text and at least 9 pt disclosure text, a light separator, and at least 30 pt
clearance from the paper edge. Do not mix centered lines with a third flush-left
resource sentence or squeeze footer content into the bottom printer margin.

Use direct institutional prose. Give estimates, denominators, dates and
limitations; avoid theatrical verdicts, hype, vague conclusions and repetitive
transitions. Subgroup comparisons are exploratory unless the study design
supports a stronger inference. Apply the user's Humanizer style when available;
the portable skill does not require another skill to run.

## Build and inspect

```bash
python scripts/charts.py --data-dir /path/to/study
python scripts/build_report.py --data-dir /path/to/study --narrative /path/to/study/narrative.json --compile
python scripts/validate_report.py /path/to/study/report/x-account-backtest.pdf --output-dir /path/to/study/report/preview
```

The chart builder exports vector PDF, 300 dpi PNG and plotted-data CSV for each
figure, plus `chart-manifest.json`. Missing data produces an explicit skipped
figure rather than invented marks. Small thesis cells display counts without
a rate. Wilson whiskers assume independent calls and are labeled descriptive.

The report builder writes editable `x-account-backtest.tex` and a build manifest.
With `--compile --pdf-engine auto` (the default engine), it uses `pdflatex` when
available and otherwise produces the PDF with Python ReportLab. No TeX
installation is required for the Python backend. Use `--pdf-engine reportlab`
to request that backend explicitly, or `--pdf-engine latex --compile` to require
LaTeX. Pass `--latex-engine` when the executable is outside `PATH`. An existing
compiler failure stops with saved diagnostics rather than hiding a broken
source file. A run without `--compile` saves TeX only unless the caller explicitly
selects ReportLab; do not present a source-only build as a finished PDF.

The Python runtime needs matplotlib, pandas, numpy, ReportLab, PyMuPDF, Pillow
and tzdata (the packaged requirements list them). The LaTeX backend additionally
uses Helvetica/Bera Mono, geometry, graphicx, longtable, booktabs, fancyhdr,
titlesec, hyperref and tcolorbox from a normal TeX distribution. Both backends
preserve the estimates, denominators, source links, exported charts and per-page
automation disclosure. ReportLab uses the 300 dpi PNG charts; the standalone
vector PDF figures and editable TeX remain available. The TeX uses relative
figure paths, so retain the `report/` and `figures/` sibling directories when
sharing sources. Report metadata records the actual backend.
The compact Python report includes up to three selected cases; the full TeX
report supports up to six. The manifest records cases included in the Python
PDF. This affects illustration selection, not the scored universe or estimates.

If no stock or ETF calls are scored at the primary horizon, produce a short
coverage, eligibility and methods report. Do not invent accuracy estimates,
zero-percent returns or a price cutoff when no prices were evaluated. A single
scored observation can support its observed outcome but cannot establish a
distribution or stable subgroup ranking. The chart builder skips a return
distribution with fewer than two observations and marks absent binary rates
as unavailable. Source links must use HTTPS without embedded credentials or
secret query parameters.

The validator checks page bounds, text integrity, PDF links, the CTA, per-page
disclosure, inherited attribution and material LaTeX overflows. It renders
every page and a contact sheet. Inspect all pages, including overflow pages;
automated checks cannot determine whether chart labels overlap or prose reads
well. Numerical/source audits are separate and must pass before delivery.

Deliver the PDF and concise source-linked call ledger. Provide editable TeX
and figure files when useful. Do not distribute API secrets, private caches or
the retrieved tweet corpus as part of the skill package.
