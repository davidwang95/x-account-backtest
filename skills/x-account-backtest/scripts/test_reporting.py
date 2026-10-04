"""Publication contracts: ledger consistency, source links and per-page attribution."""
from __future__ import annotations

import json
from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import fitz
import pandas as pd
from PIL import Image as PillowImage

from build_report import ReportBuilder, href, main as report_main
from charts import StudyCharts, load_outcomes
from validate_report import validate
from statistical_summary import build_summary


def observation() -> dict:
    return {"callId": "fixture-call", "horizon": 5, "status": "hit", "relativeStatus": "neutral",
            "symbol": "TEST", "direction": "bullish", "callType": "buy", "thesis": "valuation",
            "publishedAt": "2024-01-02T14:00:00Z", "publicationDateET": "2024-01-02",
            "directionalReturnPct": 2.0, "relativeReturnPct": 0.0, "assetClass": "equity"}


def fixture(directory: Path, *, mode="tiny"):
    row = {**observation(), "rawReturnPct": 2.0, "spyReturnPct": 2.0, "entryDate": "2024-01-03",
           "exitDate": "2024-01-10", "entrySessionIndex": 0, "kind": "explicit", "confidence": .92,
           "issuerMappingValidated": True, "evidence": "Buy TEST", "postUrl": "https://x.com/marketalpha/status/1"}
    if mode == "unscored":
        row.update(status="unscored", relativeStatus="unscored", kind="conditional", reason="Conditional source statement",
                   directionalReturnPct=None, relativeReturnPct=None, rawReturnPct=None, spyReturnPct=None)
    rows = [] if mode == "empty" else [row]
    payload = {"metadata": {"cutoff": "2024-01-31" if mode == "tiny" else None, "calendarSessions": 21}, "outcomes": rows}
    summary = build_summary(payload, primary=5, iterations=100, seed=10)
    values = {"study.json": {"account": {"handle": "marketalpha", "displayName": "Fixture market account"},
                             "startDate": "2024-01-01", "endDate": "2024-01-31", "reportDate": "2024-02-01"},
              "calls.json": rows, "outcomes.json": payload, "summary.json": summary,
              "archive-audit.json": {"normalizedPosts": 2, "monthStates": {"search_exhausted": 1}}}
    for name, value in values.items():
        (directory / name).write_text(json.dumps(value), encoding="utf-8")
    chart_rows = pd.DataFrame(rows) if rows else pd.DataFrame(columns=list(observation()))
    archive = pd.DataFrame([{"month": "2024-01", "state": "search_exhausted", "authored": 2}])
    charts = StudyCharts(chart_rows, summary, archive=archive, calls=rows,
                        audit=values["archive-audit.json"], destination=directory / "figures",
                        source="Synthetic fixture data; X Account Backtest skill", study=values["study.json"])
    return charts


class ReportingContracts(unittest.TestCase):
    def test_duplicate_outcomes_cannot_inflate_a_chart(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "outcomes.json"
            row = observation()
            path.write_text(json.dumps({"outcomes": [row, row]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Duplicate call/horizon"):
                load_outcomes(path)

    def test_chart_rejects_summary_with_different_denominator(self):
        with tempfile.TemporaryDirectory() as temporary:
            summary = {"metadata": {"primaryHorizon": 5}, "byHorizon": {"5": {"binaryDenominator": 2}}}
            with self.assertRaisesRegex(ValueError, "binaryDenominator disagrees"):
                StudyCharts(pd.DataFrame([observation()]), summary, archive=None, calls=None,
                            audit=None, destination=Path(temporary), source="Fixture data")

    def test_neutral_relative_outcome_has_no_binary_denominator(self):
        with tempfile.TemporaryDirectory() as temporary:
            summary = {"metadata": {"primaryHorizon": 5}, "byHorizon": {"5": {
                "binaryDenominator": 1, "hits": 1, "misses": 0, "hitRatePct": 100.0,
                "relativeBinaryDenominator": 0, "relativeHits": 0, "relativeMisses": 0,
                "relativeHitRatePct": None}}}
            charts = StudyCharts(pd.DataFrame([observation()]), summary, archive=None, calls=None,
                                 audit=None, destination=Path(temporary), source="Fixture data")
            self.assertEqual(len(charts.scored), 1)

    def test_report_requires_explicit_account_instead_of_sample_default(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            (path / "study.json").write_text(json.dumps({"account": {}, "startDate": "2024-01-01", "endDate": "2024-01-31"}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "valid X handle"):
                ReportBuilder(data_dir=path, figures_dir=path / "figures", narrative={}, output_dir=path / "report")

    def test_executable_source_link_is_rejected(self):
        for unsafe in ("run:some-program", "http://x.com/account", "https://user:password@example.com",
                       "https://example.com/data?api_key=fixture-secret", "https://example.com:80"):
            with self.subTest(url=unsafe), self.assertRaisesRegex(ValueError, "HTTP"):
                href(unsafe, "Source")
        self.assertIn("https://x.com/account/status/1", href("https://x.com/account/status/1", "Original X post"))

    def test_every_pdf_content_page_requires_its_own_disclosure(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            pdf = path / "fixture.pdf"
            doc = fitz.open()
            for index in range(2):
                page = doc.new_page(width=612, height=792)
                page.insert_text((72, 50), "Dave Wang Automation", fontsize=10)
                page.insert_textbox(fitz.Rect(72, 100, 540, 280),
                                    "Fixture study. This paragraph contains enough ordinary report text to establish a visible body. "
                                    "Measured outcomes use explicit observation dates and source links. No actual trading recommendation is made.", fontsize=12)
                page.insert_text((72, 720), "Follow Dave Wang - AI for finance", fontsize=10)
                if index == 0:
                    page.insert_text((72, 735), "AI-generated backtest using the X Account Backtest skill.", fontsize=9)
                page.insert_text((72, 750), "AI for finance content and resources: www.davewang.ai", fontsize=9)
                page.insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(70, 710, 400, 752), "uri": "https://www.davewang.ai"})
            doc.save(pdf)
            doc.close()
            result = validate(pdf, path / "checks", render=False)
            self.assertFalse(result["passed"])
            self.assertTrue(any(issue.get("page") == 2 and issue["problem"] == "missing-AI-skill-disclosure" for issue in result["issues"]))
            self.assertFalse(any(issue.get("page") == 1 for issue in result["issues"]))

    def test_zero_and_unscored_studies_make_a_short_report_without_fake_rates(self):
        for mode in ("empty", "unscored"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                with patch("socket.create_connection", side_effect=AssertionError("Network access is not allowed")):
                    charts = fixture(directory, mode=mode)
                    chart_manifest = charts.render()
                    builder = ReportBuilder(data_dir=directory, figures_dir=directory / "figures",
                                            narrative={}, output_dir=directory / "report")
                    manifest = builder.build()
                    from report_pdf import build_pdf
                    result = build_pdf(builder)
                checks = validate(directory / "report" / result["pdf"], directory / "checks", render=False)
                self.assertTrue(checks["passed"], checks["issues"])
                self.assertLessEqual(checks["pages"], 5)
                self.assertEqual(manifest["reportMode"], "coverage-only")
                self.assertIsNone(manifest["priceCutoff"])
                self.assertEqual(chart_manifest["primaryHorizon"], 5)
                self.assertEqual(sum(not chart.get("skipped") for chart in chart_manifest["figures"]), 1)
                text = (directory / "checks" / "report-text.txt").read_text(encoding="utf-8")
                self.assertIn("cannot be estimated", text)
                self.assertNotIn("0.0%", text)
                self.assertNotIn("Cramer", text)
                self.assertIn("@marketalpha", text)

    def test_tiny_study_exports_source_metadata_and_python_pdf_without_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with patch("socket.create_connection", side_effect=AssertionError("Network access is not allowed")):
                charts = fixture(directory)
                chart_manifest = charts.render()
                builder = ReportBuilder(data_dir=directory, figures_dir=directory / "figures", narrative={}, output_dir=directory / "report")
                builder.build()
                from report_pdf import build_pdf
                result = build_pdf(builder)
            self.assertEqual(chart_manifest["primaryHorizon"], 5)
            self.assertTrue(next(chart for chart in chart_manifest["figures"] if chart["stem"] == "06_return_distributions")["skipped"])
            image_path = directory / "figures" / "02_horizon_hit_rates.png"
            with PillowImage.open(image_path) as image:
                self.assertGreater(image.width, 1000)
                self.assertAlmostEqual(image.info["dpi"][0], 300, places=1)
                self.assertEqual(image.info["Software"], "X Account Backtest skill")
            chart_pdf = fitz.open(directory / "figures" / "02_horizon_hit_rates.pdf")
            self.assertEqual(chart_pdf.metadata["author"], "Dave Wang Automation")
            self.assertIn("AI-generated", chart_pdf.metadata["subject"])
            chart_pdf.close()
            checks = validate(directory / "report" / result["pdf"], directory / "checks", render=False)
            self.assertTrue(checks["passed"], checks["issues"])
            text = (directory / "checks" / "report-text.txt").read_text(encoding="utf-8")
            self.assertIn("5 sessions", text)
            self.assertIn("100.0%", text)
            self.assertIn("Not estimated", text)  # relative outcomes are neutral, not zero-percent accuracy.
            self.assertNotIn("Cramer", text)

    def test_auto_pdf_backend_works_when_a_tex_compiler_is_absent(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            fixture(directory, mode="empty").render()
            narrative = directory / "narrative.json"
            narrative.write_text("{}", encoding="utf-8")
            arguments = ["build_report.py", "--data-dir", str(directory), "--narrative", str(narrative), "--compile"]
            with patch("sys.argv", arguments), patch("build_report.shutil.which", return_value=None), \
                    patch("build_report.compile_report", side_effect=AssertionError("No TeX process should run")), \
                    patch("socket.create_connection", side_effect=AssertionError("Network access is not allowed")), \
                    redirect_stdout(io.StringIO()):
                report_main()
            manifest = json.loads((directory / "report" / "report-build-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["pdfEngine"], "reportlab")
            self.assertTrue(manifest["compiled"])
            self.assertTrue((directory / "report" / "x-account-backtest.tex").exists())
            self.assertTrue((directory / "report" / "x-account-backtest.pdf").exists())

    def test_crypto_only_report_keeps_us_cutoff_and_rates_unestimated(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            charts = fixture(directory, mode="unscored")
            payload = json.loads((directory / "outcomes.json").read_text(encoding="utf-8"))
            row = payload["outcomes"][0]
            row.update(assetClass="crypto", symbol="BTC", kind="explicit", evidence="Buy BTC", reason="Outside U.S. equity scope")
            payload["metadata"].update(cutoff=None, calendarSessions=0)
            summary = build_summary(payload, primary=5, iterations=100, seed=10)
            (directory / "outcomes.json").write_text(json.dumps(payload), encoding="utf-8")
            (directory / "calls.json").write_text(json.dumps([row]), encoding="utf-8")
            (directory / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
            crypto = {"metadata": {"cutoff": "2024-02-03", "primaryCalendarDays": 30}, "summary": {"primary": {
                "scored": 1, "hits": 1, "misses": 0, "neutral": 0, "binaryDenominator": 1,
                "hitRatePct": 100.0, "meanDirectionalReturnPct": 5.0, "medianDirectionalReturnPct": 5.0}}}
            (directory / "crypto-outcomes.json").write_text(json.dumps(crypto), encoding="utf-8")
            charts.rows, charts.summary, charts.calls = pd.DataFrame([row]), summary, [row]
            charts.primary = charts.rows.copy()
            charts.scored = charts.primary.iloc[:0].copy()
            charts.render()
            with patch("socket.create_connection", side_effect=AssertionError("Network access is not allowed")):
                builder = ReportBuilder(data_dir=directory, figures_dir=directory / "figures", narrative={}, output_dir=directory / "report")
                manifest = builder.build()
                from report_pdf import build_pdf
                result = build_pdf(builder)
            source = (directory / "report" / "x-account-backtest.tex").read_text(encoding="utf-8")
            self.assertIn("No scored U.S. price outcomes; equity price cutoff not evaluated.", source)
            self.assertIn("Cryptocurrency results", source)
            self.assertIn("30 calendar days", source)
            self.assertIn("Cryptocurrency prices are evaluated through 2024-02-03", source)
            self.assertIsNone(manifest["priceCutoff"])
            self.assertEqual(manifest["reportMode"], "coverage-only")
            checks = validate(directory / "report" / result["pdf"], directory / "checks", render=False)
            self.assertTrue(checks["passed"], checks["issues"])
            text = (directory / "checks" / "report-text.txt").read_text(encoding="utf-8")
            self.assertIn("No scored U.S. price outcomes; equity price cutoff not evaluated.", text)
            self.assertIn("Cryptocurrency results", text)
            self.assertIn("30 calendar days", text)
            self.assertIn("Cryptocurrency prices are evaluated through 2024-02-03", text)
            self.assertIn("100.0%", text)  # separately observed crypto outcome.
            self.assertIn("cannot be estimated", text)  # stock/ETF accuracy remains absent.


if __name__ == "__main__":
    unittest.main()
