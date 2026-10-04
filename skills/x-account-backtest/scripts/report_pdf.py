"""Python-only PDF backend for the same frozen study, without a TeX dependency."""
from __future__ import annotations

from collections import Counter
import hashlib
from html import escape
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from study_utils import safe_https_url


TEAL = colors.HexColor("#37967A")
CHARCOAL = colors.HexColor("#262A33")
GRAY = colors.HexColor("#66605C")
PALE = colors.HexColor("#E4F0EB")
URL = "https://www.davewang.ai"


def plain(value) -> str:
    text = "" if value is None else str(value)
    for source, target in (("−", "-"), ("—", ": "), ("–", " to "), ("≥", ">="), ("≤", "<="),
                           ("→", " to "), ("’", "'"), ("“", '"'), ("”", '"'), ("·", "/")):
        text = text.replace(source, target)
    return text


def link_markup(url, label):
    if not url:
        return escape(plain(label))
    if not safe_https_url(url):
        raise ValueError("Report links must use HTTPS without credentials or secret query parameters.")
    return '<a href="' + escape(str(url), quote=True) + '" color="#37967A">' + escape(plain(label)) + '</a>'


def fmt(value, *, percentage=False, signed=False, decimals=0):
    if value is None:
        return "Not estimated"
    result = f"{float(value):+,.{decimals}f}" if signed else f"{float(value):,.{decimals}f}"
    return result + ("%" if percentage else "")


def rate(value):
    return fmt(value, percentage=True, decimals=1)


def ret(value):
    return fmt(value, percentage=True, signed=True, decimals=2)


class PythonReport:
    def __init__(self, builder):
        self.b = builder
        self.story = []
        self.sections = []
        self.width = letter[0] - 108
        styles = getSampleStyleSheet()
        self.body = ParagraphStyle("ReportBody", parent=styles["Normal"], fontName="Helvetica", fontSize=10.5,
                                   leading=14.5, textColor=CHARCOAL, spaceAfter=9)
        self.heading = ParagraphStyle("ReportHeading", parent=self.body, fontName="Helvetica-Bold", fontSize=20,
                                      leading=24, textColor=TEAL, spaceAfter=15, keepWithNext=True)
        self.small = ParagraphStyle("ReportSource", parent=self.body, fontSize=8.5, leading=11.5, textColor=GRAY)
        self.cell = ParagraphStyle("ReportCell", parent=self.body, fontSize=9.5, leading=12, spaceAfter=0)
        self.casebody = ParagraphStyle("ReportCase", parent=self.body, fontSize=10, leading=13, spaceAfter=5)
        self.sub = ParagraphStyle("ReportSubheading", parent=self.body, fontName="Helvetica-Bold", fontSize=11.5,
                                  leading=15, spaceAfter=6, keepWithNext=True)
        self.center = ParagraphStyle("ReportCenter", parent=self.body, alignment=TA_CENTER)
        self.title = ParagraphStyle("ReportTitle", parent=self.heading, alignment=TA_CENTER, fontSize=27, leading=32)

    def paragraph(self, value, style=None):
        values = [value] if isinstance(value, str) else (value or [])
        for text in values:
            if isinstance(text, str) and text:
                self.story.append(Paragraph(escape(plain(text)), style or self.body))

    def section(self, title):
        if self.story:
            self.story.append(PageBreak())
        self.sections.append(title)
        self.paragraph(title, self.heading)

    def prose(self, key):
        self.paragraph(self.b.narrative.get("sections", {}).get(key))

    def table(self, headings, rows, fractions=(.70, .30)):
        cells = [[Paragraph("<b>" + escape(plain(value)) + "</b>", self.cell) for value in headings]]
        cells += [[Paragraph(escape(plain(value)), self.cell) for value in row] for row in rows]
        widths = [self.width * fraction for fraction in fractions]
        table = Table(cells, colWidths=widths, repeatRows=1, hAlign="LEFT")
        table.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, 0), .7, CHARCOAL),
                                   ("LINEBELOW", (0, 0), (-1, 0), .5, GRAY),
                                   ("LINEBELOW", (0, -1), (-1, -1), .7, CHARCOAL),
                                   ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                   ("LEFTPADDING", (0, 0), (-1, -1), 5),
                                   ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                                   ("TOPPADDING", (0, 0), (-1, -1), 5),
                                   ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
        self.story.extend([table, Spacer(1, 11)])

    def source(self):
        self.paragraph("Source: " + self.b.source_text, self.small)

    def figure(self, stem, max_height=3.8 * inch):
        entry = self.b.chart_lookup.get(stem)
        if not entry or entry.get("skipped"):
            self.paragraph("Figure unavailable: " + (entry.get("reason", "No reviewed observations.") if entry else "No reviewed observations."))
            return
        filename = self.b.figures_dir / entry["png"]
        image = Image(str(filename))
        ratio = min(self.width / image.imageWidth, max_height / image.imageHeight)
        image.drawWidth, image.drawHeight = image.imageWidth * ratio, image.imageHeight * ratio
        image.hAlign = "CENTER"
        self.story.extend([image, Spacer(1, 10)])

    def cta(self):
        paragraph = Paragraph('<b><font color="#37967A">Follow Dave Wang - AI for finance</font></b><br/>'
                              'AI for finance content, automation skills and resources:<br/>'
                              '<a href="https://www.davewang.ai" color="#37967A"><b>www.davewang.ai</b></a><br/>'
                              'AI-generated backtest using the X Account Backtest skill.', self.center)
        box = Table([[paragraph]], colWidths=[self.width])
        box.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), PALE),
                                ("BOX", (0, 0), (-1, -1), .7, TEAL),
                                ("TOPPADDING", (0, 0), (-1, -1), 12),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 12)]))
        self.story.append(KeepTogether([Spacer(1, 12), box]))

    def cover(self):
        self.story.append(Spacer(1, 45))
        self.paragraph("Dave Wang Automation", self.center)
        self.story.append(Spacer(1, 40))
        self.paragraph(self.b.title, self.title)
        self.paragraph(self.b.subtitle, self.center)
        self.paragraph(self.b.start_date + " to " + self.b.end_date, self.center)
        self.story.append(Spacer(1, 35))
        self.paragraph("AI-generated backtest produced with the X Account Backtest skill", self.center)
        self.story.append(Paragraph(link_markup(self.b.profile_url, "Source account: @" + self.b.handle), self.center))
        self.paragraph("Report date: " + self.b.report_date, self.center)
        self.paragraph("Historical prices through " + self.b.cutoff if self.b.cutoff else "No scored U.S. price outcomes; equity price cutoff not evaluated.", self.center)
        self.story.append(Spacer(1, 40))
        self.paragraph("Automated analysis of subsequent price changes following recoverable public X posts. Source recovery and AI interpretation can be incomplete. The report does not establish a complete record of historical calls.")
        self.cta()

    def executive(self):
        self.section("Executive summary")
        self.paragraph(self.b.narrative.get("executiveSummary"))
        m = self.b.primary
        self.table([f"Primary window: {self.b.horizon} sessions", "Result"], [
            ["Scored stock / ETF calls", fmt(m["scored"])],
            ["Directional hits / binary denominator", f"{m['hits']:,} / {m['binaryDenominator']:,}"],
            ["Directional hit rate", rate(m["hitRatePct"])],
            ["Relative hits / binary denominator", f"{m['relativeHits']:,} / {m['relativeBinaryDenominator']:,}"],
            ["Market-relative hit rate", rate(m["relativeHitRatePct"])],
            ["Mean signed price return", ret(m["meanDirectionalReturnPct"])],
            ["Mean signed return versus SPY", ret(m["meanRelativeReturnPct"])],
        ])
        for finding in self.b.narrative.get("keyFindings", []):
            self.paragraph(finding)
        self.source()

    def coverage(self):
        self.section("Source coverage and measurement")
        self.prose("coverage")
        self.figure("01_archive_coverage", max_height=3.3 * inch)
        self.paragraph(f"The archive identifies {self.b.source_call_count:,} asset views. Entry is the daily open on the first SPY session after the post's New York publication date. Exit is the exact same-instrument open {self.b.horizon} SPY session intervals later.")
        self.paragraph("Returns are split-adjusted price changes, excluding dividends, costs and stock borrow. Positive signed returns count as directional hits. Relative returns subtract the same-direction SPY return over matching dates. Exact-zero moves are neutral and excluded from binary rates. Missing prices remain unscored; unmatured windows remain pending.")
        self.paragraph("Retrospective AI interpretation can reflect later information from model training. Daily opening prices are research proxies, not verified executable fills.")
        self.source()

    def performance(self):
        self.section("Directional and market-relative performance")
        self.figure("02_horizon_hit_rates", max_height=3.25 * inch)
        self.prose("performance")
        self.table(["Sessions", "Dir. n", "Rel. n", "Hit", "Vs SPY"], [
            [h, fmt(m["binaryDenominator"]), fmt(m["relativeBinaryDenominator"]), rate(m["hitRatePct"]), rate(m["relativeHitRatePct"])]
            for h, m in sorted(self.b.summary["byHorizon"].items(), key=lambda item: int(item[0]))],
            fractions=(.16, .18, .18, .24, .24))
        self.paragraph("Rates use hits plus misses. Horizon samples differ with maturity and exact-price availability. Directional success does not require outperformance of SPY; market-relative success does not require the predicted price direction.")

    def chart_section(self, title, stem, key, explanation):
        self.section(title)
        self.figure(stem, max_height=4.35 * inch)
        self.prose(key)
        self.paragraph(explanation)
        self.source()

    def sensitivity(self):
        self.section("Sensitivity and statistical uncertainty")
        self.figure("08_sensitivity", max_height=3.1 * inch)
        self.prose("robustness")
        m = self.b.primary
        cluster = m.get("monthClusterBootstrap", {})
        interval = lambda value: "Not estimated" if not value else rate(value[0]) + " to " + rate(value[1])
        self.table(["Metric", "Estimate", "Month-cluster 95%"], [
            ["Directional hit rate", rate(m["hitRatePct"]), interval(cluster.get("hitRatePct95"))],
            ["Market-relative hit rate", rate(m["relativeHitRatePct"]), interval(cluster.get("relativeHitRatePct95"))],
            ["Mean signed relative return", ret(m["meanRelativeReturnPct"]), interval(cluster.get("meanRelativeReturnPct95"))],
        ], fractions=(.44, .21, .35))
        self.paragraph(cluster.get("reason") or "Month clustering does not remove all dependence from repeated securities and overlapping holding periods. Intervals describe sampling uncertainty and do not establish forecasting skill.")
        self.source()

    def cases(self):
        self.section("Selected call reviews and limitations")
        lookup = {row["callId"]: row for row in self.b.outcomes["outcomes"] if int(row["horizon"]) == self.b.horizon}
        quoted = Counter()
        for case in self.b.narrative.get("examples", [])[:3]:
            row = lookup[case["callId"]]
            self.paragraph(f"{row['publicationDateET']} / {row['symbol']} / {row['direction']}", self.sub)
            words = (row.get("evidence") or "").split()
            source_id = row.get("postUrl") or row["callId"]
            n = min(len(words), 22, max(0, 25 - quoted[source_id]))
            if n:
                self.paragraph('"' + " ".join(words[:n]) + (' ...' if len(words) > n else '') + '"', self.casebody)
                quoted[source_id] += n
            self.paragraph(case.get("commentary"), self.casebody)
            self.paragraph(f"Status: {row['status']}. Signed return: {ret(row.get('directionalReturnPct'))}; versus SPY: {ret(row.get('relativeReturnPct'))}.", self.casebody)
            if row.get("postUrl"):
                self.story.append(Paragraph(link_markup(row["postUrl"], "Original X post"), self.small))
        if not self.b.narrative.get("examples"):
            self.paragraph("No individual cases were selected. Source-linked classifications and outcomes remain in the companion ledger.")
        self.prose("limitations")
        self.paragraph("Selected cases do not establish aggregate performance. Subgroup rankings are exploratory. Recoverable source coverage, AI interpretation and numerical price validation are separate limitations. Hypothetical price changes are not executed trades or compounded strategy returns.")

    def crypto(self):
        data = self.b.read("crypto-outcomes.json") or {}
        metric = data.get("summary", {}).get("primary", {})
        if not metric.get("scored"):
            return
        self.section("Cryptocurrency results")
        self.prose("crypto")
        self.paragraph(data.get("metadata", {}).get("methodology"))
        calendar_window = data.get("metadata", {}).get("primaryCalendarDays")
        if calendar_window is not None:
            self.paragraph(f"The reported cryptocurrency primary window is {calendar_window} calendar days.")
        crypto_cutoff = data.get("metadata", {}).get("cutoff")
        if not self.b.cutoff and crypto_cutoff:
            self.paragraph(f"Cryptocurrency prices are evaluated through {crypto_cutoff}; no U.S. equity price cutoff is reported.")
        self.table(["Metric", "Result"], [["Scored crypto calls", fmt(metric["scored"])],
                                           ["Directional hits / denominator", f"{metric['hits']} / {metric['binaryDenominator']}"],
                                           ["Directional hit rate", rate(metric["hitRatePct"])],
                                           ["Mean signed price return", ret(metric["meanDirectionalReturnPct"])],
                                           ["Median signed price return", ret(metric["medianDirectionalReturnPct"])]])
        self.paragraph("Cryptocurrency uses separate UTC calendar-day USD spot prices. Entry occurs after the full New York publication day ends. Calendar-day results exclude fees and borrow costs and are not pooled with SPY-session equity outcomes.")
        self.paragraph("Source: " + self.b.study.get("cryptoPriceSource", "Frozen USD spot daily candles") +
                       "; X Account Backtest skill calculations, recorded in crypto-outcomes.json.", self.small)

    def zero_results(self):
        self.section("Coverage and eligibility")
        m = self.b.primary
        self.paragraph(f"No U.S. stock or ETF calls have an observed outcome at the configured {self.b.horizon}-session window. Accuracy and average returns cannot be estimated. This is an absence of scored evidence, not a measured hit rate of zero.")
        self.table(["Source and outcome status", "Count"], [["Identified views, all classes", fmt(self.b.source_call_count)],
                                                              ["Stock / ETF views", fmt(self.b.equity_call_count)],
                                                              ["Scored equity calls", "0"],
                                                              ["Pending horizon", fmt(m["pending"])],
                                                              ["Unscored or outside equity scope", fmt(m["unscored"])]])
        if m.get("unscoredReasons"):
            from build_report import grouped_unscored_reasons
            reasons = grouped_unscored_reasons(m["unscoredReasons"])
            self.paragraph("Exclusion categories: " + "; ".join(f"{reason}: {count:,}" for reason, count in reasons.items()) + ". Detailed source-specific reasons remain in the ledger.")
        self.prose("coverage")
        self.prose("scoreability")
        self.figure("01_archive_coverage", max_height=3.1 * inch)
        self.section("Backtest methodology")
        self.paragraph(f"The configured equity window is {self.b.horizon} SPY trading-session intervals. Entry uses the next SPY session after the New York publication date; exit uses the same instrument's daily open on the exact required date. Missing prices are not delayed, filled forward or replaced with another security.")
        self.paragraph("A directional hit requires a positive signed price change. A market-relative hit requires a positive signed return difference versus SPY over identical dates. Exact-zero outcomes are neutral. Returns exclude dividends, transaction costs and borrow. These rules are specified for eligible future observations; no performance statistic is estimated from unscored records.")
        self.prose("methodology")
        self.prose("limitations")
        self.source()

    def sources(self):
        self.section("Sources, data and resources")
        self.prose("sources")
        for item in self.b.narrative.get("sourceItems", []):
            description = escape(plain(item.get("description", "")))
            self.story.append(Paragraph(link_markup(item.get("url"), item.get("label", "Source")) + ": " + description, self.body))
        self.paragraph("The editable TeX source and exported figures accompany the PDF. Frozen calls, horizon outcomes and statistical summaries retain the evidence behind the report. Public call ledgers provide source links; private retrieval caches and API credentials are not part of the skill package.")
        self.cta()

    def footer(self, canvas, doc):
        canvas.saveState()
        canvas.setFillColor(GRAY)
        canvas.setFont("Helvetica", 9)
        canvas.drawString(54, 756, "@" + self.b.handle + " | X account backtest")
        canvas.setFillColor(TEAL)
        canvas.setFont("Helvetica-Bold", 9)
        canvas.drawRightString(558, 756, "Dave Wang Automation")
        canvas.setStrokeColor(PALE)
        canvas.line(54, 748, 558, 748)
        canvas.line(54, 68, 558, 68)
        canvas.setFont("Helvetica-Bold", 10)
        canvas.drawString(54, 52, "Follow Dave Wang - AI for finance")
        canvas.linkURL(URL, (54, 49, 255, 63), relative=0)
        canvas.setFont("Helvetica", 10)
        canvas.drawRightString(558, 52, "www.davewang.ai")
        canvas.linkURL(URL, (479, 49, 558, 63), relative=0)
        canvas.setFillColor(GRAY)
        canvas.setFont("Helvetica", 9)
        canvas.drawString(54, 36, "AI-generated backtest using the X Account Backtest skill.")
        canvas.drawRightString(558, 36, str(doc.page))
        canvas.restoreState()

    def build(self):
        self.cover()
        if self.b.primary["scored"]:
            self.executive()
            self.coverage()
            self.performance()
            for args in (
                ("Performance by recommendation type", "03_call_type_hit_rates", "callTypes", "Recommendation categories describe source actions. Sell and avoid views do not prove that the author initiated a short position. Small groups have substantial ranking uncertainty."),
                ("Performance by investment thesis", "04_thesis_horizon_heatmap", "theses", "Thesis labels describe the source rationale. Cells below 20 binary observations do not display a rate. Comparisons do not adjust for multiple testing."),
                ("Performance by publication year", "05_year_bias_hit_rates", "regimes", "Annual comparisons combine differences in source coverage, security selection and market conditions. They do not identify causal market-regime effects."),
                ("Return distribution", "06_return_distributions", "distribution", "The figure retains the full observed return tails. Negative directional returns oppose the source call; negative relative returns lag same-direction SPY. Returns are hypothetical price outcomes."),
                ("Security concentration", "07_ticker_concentration", "concentration", "Repeated calls on the same security share return windows and can affect call-weighted results. Verified historical ticker aliases share one security identity; acquisitions remain distinct.")):
                self.chart_section(*args)
            self.sensitivity()
            self.cases()
        else:
            self.zero_results()
        self.crypto()
        self.sources()
        pdf = self.b.output_dir / "x-account-backtest.pdf"
        doc = SimpleDocTemplate(str(pdf), pagesize=letter, leftMargin=54, rightMargin=54,
                                topMargin=90, bottomMargin=90,
                                title=plain(self.b.title), author="Dave Wang Automation | X Account Backtest skill",
                                subject="AI-generated backtest using the X Account Backtest skill")
        doc.build(self.story, onFirstPage=self.footer, onLaterPages=self.footer)
        return {"compiled": True, "pdfEngine": "reportlab", "pdf": pdf.name,
                "pdfSha256": hashlib.sha256(pdf.read_bytes()).hexdigest(), "pdfSections": self.sections,
                "includedCaseIds": [case["callId"] for case in self.b.narrative.get("examples", [])[:3]] if self.b.primary["scored"] else []}


def build_pdf(builder) -> dict:
    return PythonReport(builder).build()
