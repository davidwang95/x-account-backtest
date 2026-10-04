"""Build a Dave Wang Automation report from frozen backtest data and narrative.

No external reads or model calls. Statistics come directly from summary.json;
price cutoff comes from outcomes.json metadata. Full call ledgers remain CSV
companions rather than an unreadable appendix of thousands of source posts.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
from zoneinfo import ZoneInfo
from study_utils import safe_https_url


ROOT = Path(__file__).resolve().parent


def tx(value) -> str:
    value = "" if value is None else str(value)
    value = value.replace("−", "-").replace("≥", ">=").replace("≤", "<=").replace("→", " to ")
    value = value.replace("—", ": ").replace("–", " to ").replace("“", '"').replace("”", '"').replace("’", "'")
    chars = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
             "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
    return "".join(chars.get(char, char) for char in value)


def number(value, decimals=0, *, percent=False, signed=False):
    if value is None:
        return "---"
    fmt = f"{float(value):+,.{decimals}f}" if signed else f"{float(value):,.{decimals}f}"
    return tx(fmt) + (r"\%" if percent else "")


def rate(value):
    return number(value, 1, percent=True)


def return_pct(value):
    return number(value, 2, percent=True, signed=True)


def interval(value, *, signed=False):
    if not value or len(value) != 2:
        return "Unavailable"
    return number(value[0], 1, percent=True, signed=signed) + " to " + number(value[1], 1, percent=True, signed=signed)


def href(url, display):
    if not url:
        return tx(display)
    url = str(url).replace("\\", "/")
    if not safe_https_url(url):
        raise ValueError("Report links must use HTTPS without credentials or secret query parameters.")
    return r"\href{" + tx(url) + "}{" + tx(display) + "}"


def paragraphs(value):
    if not value:
        return ""
    if isinstance(value, str):
        value = [value]
    return "\n\n".join(tx(item) for item in value if isinstance(item, str)) + "\n\n"


def rows_table(headers, rows, spec=None):
    if spec is None:
        spec = "L{0.43\\textwidth}" + "R{0.13\\textwidth}" * (len(headers) - 1)
    result = [r"\begin{longtable}{" + spec + "}", r"\toprule",
              " & ".join(r"\textbf{" + tx(h) + "}" for h in headers) + r" \\ \midrule",
              r"\endfirsthead", r"\toprule",
              " & ".join(r"\textbf{" + tx(h) + "}" for h in headers) + r" \\ \midrule",
              r"\endhead", r"\bottomrule\endfoot", r"\bottomrule\endlastfoot"]
    result.extend(" & ".join(str(cell) for cell in row) + r" \\" for row in rows)
    result.append(r"\end{longtable}")
    return "\n".join(result) + "\n\n"


def grouped_unscored_reasons(reasons: dict) -> dict[str, int]:
    """Collapse verbose interpretation messages without losing their counts.

    The detailed, source-specific reason is retained in the companion ledger.
    Fixed scoring reasons determine these buckets, not subsequent returns.
    """
    groups = Counter()
    for reason, count in reasons.items():
        text = str(reason).lower()
        if "interpretation requires review" in text or "confirmed us equity or etf ticker" in text:
            category = "Source or ticker mapping needing review"
        elif any(token in text for token in ("conditional", "unclear", "not unconditional", "no unconditional")):
            category = "Conditional, exit or unclear source statement"
        elif "other asset classes" in text or "includes confirmed us equities and etfs only" in text:
            category = "Outside the U.S. equity / ETF scope"
        elif "required entry session" in text:
            category = "Missing exact entry-session price"
        elif "required exit session" in text:
            category = "Missing exact exit-session price"
        elif "historical price data" in text or "history starts too late" in text:
            category = "Missing or insufficient price history"
        else:
            category = "Other exclusions"
        groups[category] += int(count)
    return dict(groups)


PREAMBLE = r"""\documentclass[11pt,letterpaper]{report}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage[scaled=0.95]{helvet}
\renewcommand{\familydefault}{\sfdefault}
\usepackage[scaled=0.85]{beramono}
\usepackage{textcomp}
\usepackage[margin=0.9in,top=1in,bottom=1.05in,headheight=28pt,footskip=42pt]{geometry}
\usepackage[english]{babel}
\usepackage{parskip,microtype,setspace}
\setstretch{1.22}
\emergencystretch=3em
\usepackage{graphicx,longtable,booktabs,array,tabularx,colortbl,enumitem,xcolor}
\setlist[itemize]{leftmargin=1.5em,itemsep=4pt,parsep=1pt,topsep=5pt}
\definecolor{brand}{RGB}{55,150,122}
\definecolor{darktext}{RGB}{40,40,40}
\definecolor{midgray}{RGB}{100,100,100}
\definecolor{lightgray}{RGB}{240,240,240}
\definecolor{riskred}{RGB}{180,30,30}
\colorlet{brandvlight}{brand!8}
\color{darktext}
\usepackage[bookmarks=true,bookmarksnumbered=true]{hyperref}
\hypersetup{colorlinks=true,linkcolor=brand,urlcolor=brand,citecolor=brand,
 pdftitle={__PDF_TITLE__},
 pdfauthor={Dave Wang Automation | X Account Backtest skill},
 pdfsubject={AI-generated analysis of public X calls and subsequent price performance}}
\usepackage{fancyhdr}
\pagestyle{fancy}\fancyhf{}
\fancyhead[L]{\small\color{midgray}__HEADER_TITLE__}
\fancyhead[R]{\small\bfseries\color{brand}Dave Wang Automation}
\newcommand{\automationfooter}{\begin{minipage}[b]{\textwidth}
 \setstretch{1}\setlength{\parskip}{0pt}\setlength{\parindent}{0pt}
 {\fontsize{10.5}{13}\selectfont\bfseries\color{brand}\href{https://www.davewang.ai}{Follow Dave Wang - AI for finance}}\hfill
 {\fontsize{10.5}{13}\selectfont\color{brand}\href{https://www.davewang.ai}{www.davewang.ai}}\par\vspace{4pt}
 {\fontsize{9.6}{12}\selectfont\color{midgray}AI-generated backtest using the X Account Backtest skill.}\hfill
 {\fontsize{9.6}{12}\selectfont\color{midgray}\thepage}
 \end{minipage}}
\fancyfoot[C]{\automationfooter}
\renewcommand{\headrulewidth}{0.3pt}\renewcommand{\footrulewidth}{0.3pt}
\renewcommand{\headrule}{\hbox to\headwidth{\color{brand!40}\leaders\hrule height \headrulewidth\hfill}}
\renewcommand{\footrule}{\hbox to\headwidth{\color{brand!40}\leaders\hrule height \footrulewidth\hfill}}
\fancypagestyle{plain}{\fancyhf{}
 \fancyfoot[C]{\automationfooter}
 \renewcommand{\headrulewidth}{0pt}\renewcommand{\footrulewidth}{0.3pt}}
\usepackage{titlesec}
\titleformat{\chapter}[hang]{\normalfont\huge\bfseries\color{brand}}{}{0pt}{}
\titlespacing*{\chapter}{0pt}{-8pt}{18pt}
\titleformat{\section}{\normalfont\Large\bfseries\color{brand}}{}{0pt}{}
\titlespacing*{\section}{0pt}{16pt}{8pt}
\titleformat{\subsection}{\normalfont\large\bfseries\color{darktext}}{}{0pt}{}
\titlespacing*{\subsection}{0pt}{12pt}{6pt}
\newcolumntype{L}[1]{>{\raggedright\arraybackslash\hspace{0pt}}p{#1}}
\newcolumntype{R}[1]{>{\raggedleft\arraybackslash\hspace{0pt}}p{#1}}
\setlength{\tabcolsep}{4pt}\renewcommand{\arraystretch}{1.15}
\setlength{\LTpre}{8pt}\setlength{\LTpost}{8pt}
\usepackage{tcolorbox}\tcbuselibrary{skins,breakable}
\newtcolorbox{findingbox}{enhanced,breakable,colback=brandvlight,colframe=brand,
 boxrule=.7pt,left=10pt,right=10pt,top=8pt,bottom=8pt,arc=1pt}
\newcommand{\source}[1]{{\small\color{midgray}\textbf{Source:} #1\par}}
\newcommand{\reportpage}[1]{\clearpage\chapter*{#1}\thispagestyle{fancy}\markboth{#1}{}\addcontentsline{toc}{chapter}{#1}}
\newcommand{\exhibit}[2][.60]{\begin{center}\includegraphics[width=\textwidth,height=#1\textheight,keepaspectratio]{#2}\end{center}}
\begin{document}
"""


class ReportBuilder:
    def __init__(self, *, data_dir: Path, figures_dir: Path, narrative: dict, output_dir: Path):
        self.data_dir, self.figures_dir, self.narrative, self.output_dir = data_dir, figures_dir, narrative, output_dir
        self.study = self.read("study.json", required=True)
        account = self.study.get("account", {})
        if isinstance(account, str):
            account = {"handle": account.lstrip("@")}
        self.handle = str(account.get("handle", "")).lstrip("@")
        if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", self.handle):
            raise ValueError("study.json must identify a valid X handle in account.handle.")
        self.account_name = str(account.get("displayName") or "@" + self.handle)
        self.profile_url = str(account.get("profileUrl") or "https://x.com/" + self.handle)
        self.start_date = self.study["startDate"]
        self.end_date = self.study["endDate"]
        if datetime.fromisoformat(self.start_date).date() > datetime.fromisoformat(self.end_date).date():
            raise ValueError("study.json startDate must not follow endDate.")
        if self.study.get("benchmark", "SPY") != "SPY":
            raise ValueError("This report engine supports SPY-session equity backtests; validate another benchmark methodology before substituting it.")
        self.title = str(self.study.get("title") or self.account_name + " X account backtest")
        self.subtitle = str(self.study.get("subtitle") or "Directional accuracy and subsequent price performance")
        self.source_text = str(self.study.get("sourceText") or "Archived X posts; split-adjusted prices; X Account Backtest skill calculations.")
        # Output attribution is automation branding, not personally authored research.
        report_input = json.dumps({"study": self.study, "narrative": narrative}, ensure_ascii=False)
        if re.search(r"wall\s*street\s*prompt|research\s+by|wallstreetprompt\.com", report_input, re.I):
            raise ValueError("Remove inherited research attribution and company branding from the report narrative and study metadata.")
        self.summary = self.read("summary.json", required=True)
        self.outcomes = self.read("outcomes.json", required=True)
        self.audit = self.read("archive-audit.json") or {}
        self.archive = self.read("archive-coverage.json") or {}
        self.calls = self.read("calls.json")
        if isinstance(self.calls, dict):
            self.calls = self.calls.get("calls", [])
        self.calls = self.calls or []
        self.primary = self.summary["primary"]
        self.horizon = int(self.summary["metadata"]["primaryHorizon"])
        primary_rows = [row for row in self.outcomes["outcomes"] if int(row["horizon"]) == self.horizon]
        if len({row["callId"] for row in primary_rows}) != len(primary_rows):
            raise ValueError("Duplicate primary-horizon call rows would inflate the report.")
        statuses = Counter(row.get("status") for row in primary_rows)
        relative_statuses = Counter(row.get("relativeStatus") for row in primary_rows if row.get("status") in {"hit", "miss", "neutral"})
        expected_counts = {"calls": len(primary_rows), "hits": statuses["hit"], "misses": statuses["miss"],
                           "neutral": statuses["neutral"], "pending": statuses["pending"], "unscored": statuses["unscored"],
                           "binaryDenominator": statuses["hit"] + statuses["miss"],
                           "relativeHits": relative_statuses["hit"], "relativeMisses": relative_statuses["miss"],
                           "relativeBinaryDenominator": relative_statuses["hit"] + relative_statuses["miss"]}
        for field, measured in expected_counts.items():
            if field in self.primary and self.primary[field] != measured:
                raise ValueError(f"Primary summary {field} disagrees with the outcome ledger.")
        for field, numerator, denominator in (("hitRatePct", "hits", "binaryDenominator"),
                                               ("relativeHitRatePct", "relativeHits", "relativeBinaryDenominator")):
            measured = expected_counts[numerator] / expected_counts[denominator] * 100 if expected_counts[denominator] else None
            supplied = self.primary.get(field)
            if (measured is None) != (supplied is None) or (measured is not None and not math.isclose(measured, float(supplied), abs_tol=1e-9)):
                raise ValueError(f"Primary summary {field} disagrees with the outcome ledger.")
        source_rows = self.calls or [row for row in self.outcomes["outcomes"] if int(row["horizon"]) == self.horizon]
        self.source_call_count = len(source_rows)
        self.equity_call_count = sum(row.get("assetClass") in {"equity", "etf"} for row in source_rows)
        self.cutoff = self.outcomes.get("metadata", {}).get("cutoff")
        if not self.cutoff and self.primary["scored"]:
            raise ValueError("The frozen outcomes metadata must supply the price cutoff.")
        self.report_date = self.study.get("reportDate") or narrative.get("reportDate") or datetime.now(ZoneInfo("America/New_York")).date().isoformat()
        self.chart_manifest = json.loads((figures_dir / "chart-manifest.json").read_text(encoding="utf-8"))
        self.chart_lookup = {x["stem"]: x for x in self.chart_manifest["figures"]}
        self.document, self.pages = [], []

    def read(self, filename, required=False):
        path = self.data_dir / filename
        if not path.exists():
            if required:
                raise FileNotFoundError(path)
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def add(self, content):
        self.document.append(content)

    def page(self, title):
        self.pages.append(title)
        self.add(r"\reportpage{" + tx(title) + "}\n")

    def prose(self, key):
        self.add(paragraphs(self.narrative.get("sections", {}).get(key)))

    def figure(self, stem):
        entry = self.chart_lookup.get(stem)
        if not entry or entry.get("skipped"):
            reason = entry.get("reason") if entry else "Reviewed chart data were unavailable."
            self.add(paragraphs("Figure unavailable: " + reason))
            return
        filename = self.figures_dir / entry["pdf"]
        if not filename.exists():
            raise FileNotFoundError(filename)
        caps = {"01_archive_coverage": ".54", "02_horizon_hit_rates": ".48",
                "03_call_type_hit_rates": ".60", "04_thesis_horizon_heatmap": ".62",
                "05_year_bias_hit_rates": ".58", "06_return_distributions": ".54",
                "07_ticker_concentration": ".58", "08_sensitivity": ".54"}
        self.add(r"\exhibit[" + caps.get(stem, ".60") + r"]{\detokenize{" + self.graphic_path(filename) + "}}\n")

    def graphic_path(self, path):
        return Path(os.path.relpath(path.resolve(), self.output_dir.resolve())).as_posix()

    def source_line(self, content=None):
        self.add(r"\source{" + tx(content or self.source_text) + "}\n")

    def cover(self):
        self.add(PREAMBLE.replace("__PDF_TITLE__", tx(self.title)).replace("__HEADER_TITLE__", tx("@" + self.handle + " | X account backtest")))
        branding = r"{\Large\bfseries\color{brand}Dave Wang Automation\par}"
        self.add(r"\hypersetup{pageanchor=false}\begin{titlepage}\centering\vspace*{0.6cm}" + branding + r"\vspace{1.3cm}" + "\n")
        self.add(r"{\color{brand}\rule{\textwidth}{2.5pt}}\vspace{0.7cm}" + "\n")
        self.add(r"{\Huge\bfseries\color{brand}" + tx(self.title) + r"\par}\vspace{0.6cm}" + "\n")
        self.add(r"{\Large " + tx(self.subtitle) + r"\par}\vspace{0.5cm}" + "\n")
        self.add(r"{\normalsize " + tx(self.start_date + " to " + self.end_date) + r"\par}\vspace{0.7cm}" + "\n")
        self.add(r"{\color{brand}\rule{\textwidth}{2.5pt}}\vspace{0.9cm}" + "\n")
        self.add(r"{\large\bfseries AI-generated backtest produced with the X Account Backtest skill\par}\vspace{0.5cm}" + "\n")
        self.add(r"{\normalsize " + href(self.profile_url, "Source account: @" + self.handle) + r"\par}\vspace{0.3cm}" + "\n")
        report_date = datetime.fromisoformat(self.report_date).strftime("%B %d, %Y").replace(" 0", " ")
        self.add(r"{\normalsize " + tx(report_date) + r"\par}\vspace{0.4cm}" + "\n")
        price_note = "Historical prices through " + self.cutoff if self.cutoff else "No scored U.S. price outcomes; equity price cutoff not evaluated."
        self.add(r"{\small\color{midgray}" + tx(price_note) + r"\par}" + "\n")
        self.add(r"\vfill\begin{minipage}{.9\textwidth}\small\color{midgray}" + "\n")
        self.add(paragraphs("Automated analysis of subsequent price changes following recoverable public posts from " + self.account_name + "'s X account. The report measures individual call outcomes. Source recovery and AI interpretation can be incomplete; the archive does not establish a complete record of historical calls."))
        self.add(r"\end{minipage}\vspace{0.6cm}" + "\n")
        self.add(r"\begin{findingbox}\centering{\Large\bfseries\href{https://www.davewang.ai}{Follow Dave Wang - AI for finance}\par}\vspace{0.2cm}" + "\n")
        self.add(r"{\normalsize AI for finance content and resources\par}{\large\bfseries\href{https://www.davewang.ai}{www.davewang.ai}\par}\end{findingbox}" + "\n")
        self.add(r"\end{titlepage}\pagenumbering{arabic}\hypersetup{pageanchor=true}" + "\n")

    def executive(self):
        self.page("Executive summary")
        self.add(paragraphs(self.narrative.get("executiveSummary")))
        m = self.primary
        self.add(rows_table([f"Primary window: {self.horizon} sessions", "Result"], [
            ["Asset views identified (all classes)", number(self.source_call_count)],
            ["Stock / ETF views identified", number(self.equity_call_count)],
            ["U.S. equity / ETF calls scored", number(m["scored"])],
            ["Directional hit rate", rate(m["hitRatePct"])],
            ["Market-relative hit rate", rate(m["relativeHitRatePct"])],
            ["Mean signed price return", return_pct(m["meanDirectionalReturnPct"])],
            ["Mean signed return versus SPY", return_pct(m["meanRelativeReturnPct"])],
        ], "L{.68\\textwidth}R{.25\\textwidth}"))
        findings = self.narrative.get("keyFindings", [])
        if findings:
            self.add(r"\begin{findingbox}\textbf{Principal findings}\par" + "\n")
            self.add(r"\begin{itemize}" + "\n")
            for finding in findings:
                self.add(r"\item " + tx(finding) + "\n")
            self.add(r"\end{itemize}\end{findingbox}" + "\n")
        self.add(paragraphs(self.narrative.get("verdict")))
        self.source_line()

    def coverage(self):
        self.page("Source coverage")
        self.prose("coverage")
        self.figure("01_archive_coverage")
        if self.audit:
            searched_months = len(self.archive.get("months", [])) or sum(self.audit.get("monthStates", {}).values())
            states = self.audit.get("monthStates", {})
            complete = int(states.get("search_exhausted", 0))
            search_claim = ("All searches reached the end of available results. "
                            if searched_months and complete == searched_months else
                            f"{complete:,} searches reached the end of available results. ")
            self.add(paragraphs(f"The source audit reconciled {self.audit.get('normalizedPosts', 0):,} public posts across {searched_months:,} monthly searches. " + search_claim + "Retrieved results do not establish historical completeness. Deleted and inaccessible text remain unverifiable."))

    def methodology(self):
        self.page("Backtest methodology")
        md = self.outcomes["metadata"]
        self.add(rows_table(["Choice", "Rule"], [
            ["Entry", tx("Daily opening price on the first SPY trading session after the post's New York publication date.")],
            ["Exit", tx("Same instrument's daily open h SPY session intervals after entry, on the exact required date.")],
            ["Prices", tx("Split-adjusted price returns, excluding dividends, transaction costs and stock borrow.")],
            ["Primary horizon", tx(f"{self.horizon} SPY trading-session intervals, chosen before inspecting returns.")],
            ["Directional hit", tx("A positive signed price return: a price increase for a bullish call or a decline for a bearish call.")],
            ["Market-relative hit", tx("A positive difference between signed asset and signed SPY returns over the same dates.")],
            ["Rate denominator", tx("Hits plus misses. Exact-zero moves are neutral and excluded from rates.")],
            ["Return summaries", tx("Means and medians include scored neutral returns.")],
            ["Price cutoff", tx(self.cutoff or "Not assessed; no scored price outcomes")],
        ], "L{.24\\textwidth}L{.69\\textwidth}"))
        self.add(r"\[r_i=d_i\left(\frac{P_{i,\mathrm{exit}}}{P_{i,\mathrm{entry}}}-1\right),\qquad r_i^{\mathrm{rel}}=d_i(r_{i,\mathrm{asset}}-r_{i,\mathrm{SPY}})\]" + "\n")
        self.add(paragraphs("The direction multiplier is +1 for bullish calls and -1 for bearish calls. Returns are expressed as percentages. Standardized holding periods measure subsequent price performance independently of the author's stated forecast horizon."))
        self.prose("methodology")
        self.source_line(self.study.get("archiveSource", "Archived public X text") + "; " + self.study.get("priceSource", "Frozen daily prices and historical issuer records") + ".")

    def scoreability(self):
        self.page("Eligibility and exclusions")
        m = self.primary
        self.add(paragraphs(f"The archive contains {self.source_call_count:,} identified asset views, including {self.equity_call_count:,} stock and ETF views. The primary analysis covers U.S. equities and ETFs on the SPY trading calendar. Cryptocurrency results are reported separately."))
        self.add(rows_table([f"Status at {self.horizon} sessions", "Calls"], [
            ["Hit", number(m["hits"])], ["Miss", number(m["misses"])],
            ["Neutral", number(m["neutral"])], ["Pending horizon", number(m["pending"])],
            ["Unscored or outside equity scope", number(m["unscored"])],
            [r"\textbf{Records in the primary scoring ledger}", number(m["calls"])],
        ], "L{.68\\textwidth}R{.25\\textwidth}"))
        reasons = grouped_unscored_reasons(m.get("unscoredReasons", {}))
        if reasons:
            if sum(reasons.values()) != m["unscored"]:
                raise ValueError("Collapsed exclusion reasons must reconcile to the exact unscored total.")
            self.add(r"\section*{Exclusion categories}" + "\n")
            self.add(rows_table(["Reason", "Calls"], [[tx(k), number(v)] for k, v in sorted(reasons.items(), key=lambda x: x[1], reverse=True)],
                                "L{.78\\textwidth}R{.15\\textwidth}"))
            self.add(paragraphs("Record-level exclusions appear in all-calls.csv and outcomes.csv and do not enter the hit-rate denominator."))
        self.prose("scoreability")
        self.source_line()

    def headline(self):
        self.page("Directional and market-relative performance")
        self.figure("02_horizon_hit_rates")
        self.prose("performance")
        m = self.primary
        self.add(paragraphs(f"At {self.horizon} sessions, directional hits total {m['hits']:,} of {m['binaryDenominator']:,}; market-relative hits total {m['relativeHits']:,} of {m['relativeBinaryDenominator']:,}. SPY accuracy using the same directions is {m.get('sameDirectionSpyHitRatePct'):.1f}%." if m.get("sameDirectionSpyHitRatePct") is not None else "The same-direction SPY comparison is unavailable."))
        self.add(r"\section*{Benchmark interpretation}" + "\n")
        self.add(paragraphs("Directional accuracy does not require market outperformance. A bullish stock can rise but lag SPY. A bearish call can fail directionally yet succeed relatively when the stock rises less than SPY. A 2% stock gain against a 5% SPY gain produces -2% signed price return and +3% signed relative return for a bearish call. Both measures use the original direction and identical dates."))
        self.source_line()

    def horizons(self):
        self.page("Holding-period analysis")
        data = []
        for horizon, metric in sorted(self.summary["byHorizon"].items(), key=lambda x: int(x[0])):
            data.append([tx(horizon), number(metric["binaryDenominator"]), number(metric["relativeBinaryDenominator"]), rate(metric["hitRatePct"]),
                         rate(metric["relativeHitRatePct"]), return_pct(metric["meanDirectionalReturnPct"]), return_pct(metric["meanRelativeReturnPct"])])
        self.add(rows_table(["Sessions", "Dir. n", "Rel. n", "Hit", "Vs SPY", "Mean", "Mean vs SPY"], data,
                            "L{.10\\textwidth}R{.11\\textwidth}R{.11\\textwidth}R{.12\\textwidth}R{.12\\textwidth}R{.14\\textwidth}R{.16\\textwidth}"))
        self.add(paragraphs("Sample size varies with maturity and price availability. Price reversals can also change a call's outcome over longer windows. Results across horizons measure different holding periods and are not independent replications."))
        common = self.summary.get("commonMatureCohort", {})
        if common.get("byHorizon"):
            self.add(r"\section*{Common observation cohort}" + "\n")
            common_rows = []
            for horizon, metric in sorted(common["byHorizon"].items(), key=lambda x: int(x[0])):
                common_rows.append([tx(horizon), number(metric["binaryDenominator"]), number(metric["relativeBinaryDenominator"]),
                                    rate(metric["hitRatePct"]), rate(metric["relativeHitRatePct"])])
            self.add(rows_table(["Sessions", "Dir. n", "Rel. n", "Hit", "Vs SPY"], common_rows,
                                "L{.18\\textwidth}R{.16\\textwidth}R{.16\\textwidth}R{.19\\textwidth}R{.19\\textwidth}"))
            self.add(paragraphs(f"The common cohort contains {common.get('callCount', 0):,} calls with complete required prices at every horizon. It favors older observations and excludes calls with any required price missing. Dir. n and Rel. n count directional and market-relative hits plus misses separately; each rate excludes exact-zero outcomes."))
        self.prose("horizons")
        self.source_line()

    def types(self):
        self.page("Performance by recommendation type")
        self.figure("03_call_type_hit_rates")
        self.prose("callTypes")
        self.source_line()
        self.page("Performance by investment thesis")
        self.figure("04_thesis_horizon_heatmap")
        self.prose("theses")
        self.add(paragraphs("Cells with fewer than 20 binary outcomes show their sample size without a rate."))

    def yearly(self):
        self.page("Performance by publication year")
        self.figure("05_year_bias_hit_rates")
        self.prose("regimes")
        self.add(paragraphs("Annual comparisons reflect differences in security selection, market conditions, call frequency and archive coverage. They describe observed results without identifying the effect of a particular economic regime."))

    def distribution(self):
        self.page("Return distribution")
        self.figure("06_return_distributions")
        self.prose("distribution")
        m = self.primary
        self.add(rows_table([f"{self.horizon}-session signed returns", "Value"], [
            ["Median", return_pct(m["medianDirectionalReturnPct"])],
            ["Mean", return_pct(m["meanDirectionalReturnPct"])],
            ["10th percentile", return_pct(m["p10DirectionalReturnPct"])],
            ["90th percentile", return_pct(m["p90DirectionalReturnPct"])],
        ], "L{.68\\textwidth}R{.25\\textwidth}"))

    def concentration(self):
        self.page("Security concentration")
        self.figure("07_ticker_concentration")
        self.prose("concentration")
        self.add(paragraphs("Calls on the same security may share forward price movements. Equal-security weighting measures sensitivity to the greater influence of frequently cited securities in a call-weighted average."))

    def sensitivity(self):
        self.page("Sensitivity analysis")
        self.figure("08_sensitivity")
        self.prose("robustness")
        method = self.summary.get("episodeSensitivity", {}).get("method")
        if method:
            spacing = int(self.summary.get("metadata", {}).get("episodeSpacingSessions", self.horizon))
            self.add(paragraphs(f"The repeat-call test retains the earliest observed call on a security in a given direction, then suppresses additional calls in that group for {spacing} SPY sessions. Verified ticker renames remain a single security."))
        self.add(paragraphs("The equal-security test averages each verified security's rate once, grouping verified ticker aliases under one identity."))

    def uncertainty(self):
        self.page("Statistical uncertainty")
        m = self.primary
        bootstrap = m.get("monthClusterBootstrap", {})
        data = [
            ["Directional hit rate", rate(m["hitRatePct"]), interval(m["hitRateWilson95"]), interval(bootstrap.get("hitRatePct95"))],
            ["Market-relative hit rate", rate(m["relativeHitRatePct"]), interval(m["relativeHitRateWilson95"]), interval(bootstrap.get("relativeHitRatePct95"))],
            ["Mean signed price return", return_pct(m["meanDirectionalReturnPct"]), "Not applicable", interval(bootstrap.get("meanDirectionalReturnPct95"), signed=True)],
            ["Mean signed relative return", return_pct(m["meanRelativeReturnPct"]), "Not applicable", interval(bootstrap.get("meanRelativeReturnPct95"), signed=True)],
        ]
        self.add(rows_table(["Metric", "Estimate", "Wilson 95%", "Month-cluster 95%"], data,
                            "L{.32\\textwidth}R{.14\\textwidth}L{.22\\textwidth}L{.23\\textwidth}"))
        self.add(paragraphs(f"The cluster bootstrap resamples {bootstrap.get('months', 0):,} entry months over {bootstrap.get('iterations', 0):,} iterations and retains calls within each sampled month."))
        self.add(paragraphs("Wilson intervals assume independent calls and provide descriptive sampling ranges; they do not establish predictive skill. Clustering by entry month does not fully account for securities repeated across months or overlapping holding periods across month boundaries. The bootstrap is a descriptive robustness measure."))
        self.prose("uncertainty")
        self.add(r"\section*{Study limitations}" + "\n")
        limitations = self.narrative.get("sections", {}).get("limitations", [])
        self.add(paragraphs(limitations))
        self.add(paragraphs("Subgroup rankings and additional horizons are exploratory. The reported returns are hypothetical price outcomes, without evidence of executed trades or an implementable strategy."))

    def examples(self):
        self.page("Selected call reviews")
        examples = self.narrative.get("examples", [])
        lookup = {row["callId"]: row for row in self.outcomes["outcomes"] if int(row["horizon"]) == self.horizon}
        if not examples:
            self.add(paragraphs("The full call-level ledger accompanies this report, with source URLs, classifications, dates and outcomes. Frozen classification files retain the evidence for source review."))
        quoted_sources = Counter()
        for item in examples[:6]:
            call_id = item.get("callId")
            if call_id not in lookup:
                raise ValueError(f"Narrative example does not match an observed primary-horizon call: {call_id}")
            row = lookup[call_id]
            heading = f"{row['publicationDateET']} · {row['symbol']} · {row['direction']}"
            self.add(r"\subsection*{" + tx(heading) + "}\n")
            evidence = row.get("evidence") or ""
            words = evidence.split()
            quote_budget = max(0, 25 - quoted_sources[row.get("postUrl") or row["callId"]])
            quote_length = min(22, quote_budget)
            if words and quote_length:
                excerpt = " ".join(words[:quote_length]) + (" ..." if len(words) > quote_length else "")
                quoted_sources[row.get("postUrl") or row["callId"]] += min(len(words), quote_length)
                self.add(r"\textit{``" + tx(excerpt) + r"''}\par" + "\n")
            self.add(paragraphs(item.get("commentary")))
            self.add(tx(f"Status: {row['status']}") + "; signed return: " + return_pct(row.get("directionalReturnPct")) + "; signed return versus SPY: " + return_pct(row.get("relativeReturnPct")) + ".\n\n")
            self.add(r"\source{" + href(row.get("postUrl"), "Original X post") + "}\n")
        self.add(paragraphs("These cases illustrate individual outcomes, without establishing aggregate performance. The accompanying ledger contains all classified views, including unscored and pending records."))

    def crypto(self):
        supplemental = self.read("crypto-summary.json")
        if not supplemental:
            raw = self.read("crypto-outcomes.json")
            if raw:
                supplemental = {**raw.get("summary", {}), "metadata": raw.get("metadata", {}), "notes": raw.get("notes", [])}
        if not supplemental:
            return
        metric = supplemental.get("primary", {})
        if metric.get("scored", 0) == 0:
            return
        self.page("Cryptocurrency results")
        self.prose("crypto")
        self.add(paragraphs(supplemental.get("metadata", {}).get("methodology")))
        calendar_window = supplemental.get("metadata", {}).get("primaryCalendarDays")
        if calendar_window is not None:
            self.add(paragraphs(f"The reported cryptocurrency primary window is {calendar_window} calendar days."))
        crypto_cutoff = supplemental.get("metadata", {}).get("cutoff")
        if not self.cutoff and crypto_cutoff:
            self.add(paragraphs(f"Cryptocurrency prices are evaluated through {crypto_cutoff}; no U.S. equity price cutoff is reported."))
        self.add(paragraphs("Entry uses the first UTC daily open strictly after the New York publication day ends: the publication date plus two calendar days at 00:00 UTC. This delay places the measured return after all source text in that day's classification batch."))
        self.add(paragraphs("Returns use exchange-specific USD spot opening prices and exclude fees and borrowing costs. Cryptocurrency outcomes use calendar days and have no SPY comparison."))
        self.add(rows_table(["Metric", "Result"], [
            ["Scored crypto calls", number(metric.get("scored"))],
            ["Directional hit rate", rate(metric.get("hitRatePct"))],
            ["Calls in the hit-rate denominator", number(metric.get("binaryDenominator"))],
            ["Mean signed price return", return_pct(metric.get("meanDirectionalReturnPct"))],
            ["Median signed price return", return_pct(metric.get("medianDirectionalReturnPct"))],
        ], "L{.68\\textwidth}R{.25\\textwidth}"))
        self.add(paragraphs(supplemental.get("notes")))
        self.add(paragraphs("Cryptocurrency trades continuously. Its calendar-day observations remain separate from the primary U.S. equity study and are excluded from the SPY-session hit rate."))
        self.source_line(self.study.get("cryptoPriceSource", "Frozen USD spot daily candles") + "; X Account Backtest skill calculations, recorded in crypto-outcomes.json.")

    def sources(self):
        self.page("Sources and verification")
        self.prose("sources")
        sources = self.narrative.get("sourceItems", [])
        if not sources and not self.narrative.get("sections", {}).get("sources"):
            self.add(paragraphs("Source references were not supplied in the report narrative. The provided study files identify the account and requested publication window. Archive completeness and source interpretation remain limitations. Review the frozen input records before drawing a performance conclusion."))
        if sources:
            self.add(r"\begin{itemize}" + "\n")
            for item in sources:
                content = href(item.get("url"), item.get("label", "Source"))
                if item.get("description"):
                    content += ": " + tx(item["description"])
                self.add(r"\item " + content + "\n")
            self.add(r"\end{itemize}" + "\n")
        self.data_and_methods()

    def data_and_methods(self):
        self.page("Data and methods")
        self.add(paragraphs("The dataset retains all classified asset views, including excluded and pending observations. Separate horizon ledgers contain the numerical results. Method files and audit records document the calculations and validation."))
        files = [("all-calls.csv", "Full classified dataset across all asset classes; source links, interpretation and primary-window results."),
                 ("outcomes.csv", "Primary U.S. equity scoring ledger, one row per call and SPY-session horizon, including exclusions."),
                 ("crypto-outcomes.csv", "Separate cryptocurrency ledger using UTC calendar-day horizons; not pooled into the equity statistic."),
                 ("summary.json", "Full aggregate and subgroup statistics, interval definitions and robustness results."),
                 ("calls.json", "Finalized asset-direction classifications and source-text evidence."),
                 ("archive-audit.json", "Archive identity checks and coverage limitations."),
                 ("archive-month-counts.csv", "Monthly retrieval counts and completed search status."),
                 ("scoring.py", "Entry and exit rules and outcome calculations."),
                 ("statistical_summary.py", "Summary estimators, cluster bootstrap and sensitivity calculations."),
                 ("charts.py", "Reproducible source-driven figures."),
                 ("figures/chart-manifest.json", "Figure definitions and the data behind plotted rates.")]
        files = [(name, description) for name, description in files
                 if (self.data_dir / name).exists() or (ROOT / name).exists()]
        self.add(rows_table(["File", "Contents"], [[tx(name), tx(description)] for name, description in files],
                            "L{.30\\textwidth}L{.63\\textwidth}"))
        self.add(paragraphs("Price returns exclude dividends, transaction costs and stock borrow. Coverage is limited to recoverable public text authored by the source account. Appearances, broadcasts, images, videos and deleted posts are outside the verified source record. Source coverage and AI classification uncertainty are distinct from validation of numerical price calculations."))
        self.add(r"\begin{findingbox}\textbf{Follow Dave Wang - AI for finance}\par AI for finance content, automation skills and resources: " + href("https://www.davewang.ai", "www.davewang.ai") + r"\par This report is AI-generated using the X Account Backtest skill.\end{findingbox}" + "\n")
        self.add(r"\end{document}" + "\n")

    def no_scored_results(self):
        self.page("Coverage and eligibility")
        m = self.primary
        self.add(paragraphs(f"No U.S. equity or ETF calls have an observed outcome at the configured {self.horizon}-session window. Directional accuracy, market-relative accuracy and average returns cannot be estimated. This result is an absence of scored evidence, not a measured hit rate of zero."))
        self.add(rows_table(["Source and outcome status", "Count"], [
            ["Identified views across all asset classes", number(self.source_call_count)],
            ["Stock and ETF views identified", number(self.equity_call_count)],
            ["Scored U.S. equity / ETF outcomes", number(m["scored"])],
            ["Pending primary horizon", number(m["pending"])],
            ["Unscored or outside equity scope", number(m["unscored"])],
        ], "L{.68\\textwidth}R{.25\\textwidth}"))
        self.prose("coverage")
        self.prose("scoreability")
        self.figure("01_archive_coverage")
        reasons = grouped_unscored_reasons(m.get("unscoredReasons", {}))
        if reasons:
            self.add(paragraphs("Exclusions by category: " + "; ".join(f"{reason}: {count:,}" for reason, count in reasons.items()) + ". Record-level reasons remain in the call ledger."))
        self.source_line()

    def build(self):
        if self.primary["scored"]:
            methods = (self.cover, self.executive, self.coverage, self.methodology, self.scoreability,
                       self.headline, self.horizons, self.types, self.yearly, self.distribution,
                       self.concentration, self.sensitivity, self.uncertainty, self.examples, self.crypto, self.sources)
        else:
            methods = (self.cover, self.no_scored_results, self.methodology, self.crypto, self.sources)
        for method in methods:
            method()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / "x-account-backtest.tex"
        path.write_text("\n".join(self.document), encoding="utf-8")
        manifest = {"source": path.name, "account": "@" + self.handle, "reportDate": self.report_date, "priceCutoff": self.cutoff,
                    "primaryHorizon": self.horizon, "plannedSections": self.pages,
                    "statisticsSourceSha256": hashlib.sha256((self.data_dir / "summary.json").read_bytes()).hexdigest(),
                    "outcomesSourceSha256": hashlib.sha256((self.data_dir / "outcomes.json").read_bytes()).hexdigest(),
                    "figures": self.graphic_path(self.figures_dir), "compiled": False,
                    "attribution": "AI-generated using the X Account Backtest skill", "brand": "Dave Wang Automation"}
        manifest["reportMode"] = "full" if self.primary["scored"] else "coverage-only"
        (self.output_dir / "report-build-manifest.json").write_text(json.dumps(manifest, indent=2, allow_nan=False), encoding="utf-8")
        return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path.cwd())
    parser.add_argument("--figures-dir", type=Path)
    parser.add_argument("--narrative", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--compile", action="store_true", help="Compile the generated LaTeX to PDF twice.")
    parser.add_argument("--latex-engine", help="pdflatex executable or absolute executable path.")
    parser.add_argument("--pdf-engine", choices=("auto", "latex", "reportlab"), default="auto",
                        help="PDF backend. Auto uses pdflatex when available, otherwise Python ReportLab.")
    args = parser.parse_args()
    narrative = json.loads(args.narrative.read_text(encoding="utf-8"))
    builder = ReportBuilder(data_dir=args.data_dir, figures_dir=args.figures_dir or args.data_dir / "figures",
                            narrative=narrative, output_dir=args.output_dir or args.data_dir / "report")
    manifest = builder.build()
    if args.compile or args.pdf_engine == "reportlab":
        if args.pdf_engine == "reportlab" or (args.pdf_engine == "auto" and not shutil.which(args.latex_engine or "pdflatex")):
            from report_pdf import build_pdf
            manifest.update(build_pdf(builder))
        else:
            manifest.update(compile_report(builder.output_dir, args.latex_engine))
        (builder.output_dir / "report-build-manifest.json").write_text(json.dumps(manifest, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


def compile_report(output_dir: Path, engine: str | None = None) -> dict:
    """Compile without a shell, leaving diagnostics beside the editable source."""
    executable = shutil.which(engine or "pdflatex")
    if not executable:
        raise RuntimeError("pdflatex is unavailable. The editable TeX and figures are saved. Use --pdf-engine reportlab for the Python PDF backend, or pass --latex-engine with an existing compiler executable.")
    path = output_dir / "x-account-backtest.tex"
    for pass_number in (1, 2):
        result = subprocess.run([executable, "-interaction=nonstopmode", "-halt-on-error", "-no-shell-escape", path.name],
                                cwd=output_dir.resolve(), capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=180, check=False)
        (output_dir / f"compile-pass-{pass_number}.txt").write_text(result.stdout + "\n" + result.stderr, encoding="utf-8")
        if result.returncode:
            raise RuntimeError(f"LaTeX compilation failed on pass {pass_number}. Read compile-pass-{pass_number}.txt and fix the saved source.")
    pdf = output_dir / "x-account-backtest.pdf"
    if not pdf.exists():
        raise RuntimeError("The compiler reported success without producing the requested PDF.")
    return {"compiled": True, "pdfEngine": "latex", "pdf": pdf.name, "pdfSha256": hashlib.sha256(pdf.read_bytes()).hexdigest()}


if __name__ == "__main__":
    main()
