"""Render every report page and verify layout, automation attribution and CTA links.

This checks publication output. Numerical and source audits remain separate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

import fitz
from PIL import Image, ImageDraw


def validate(pdf: Path, destination: Path, *, render: bool = True) -> dict:
    destination.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf)
    pages, issues, thumbs, full_text = [], [], [], []
    if not len(doc):
        raise ValueError("The PDF has no pages.")
    for index, page in enumerate(doc):
        page_text = page.get_text()
        full_text.append(page_text)
        normalized = re.sub(r"\s+", " ", page_text)
        spans = [span for block in page.get_text("dict")["blocks"] if "lines" in block
                 for line in block["lines"] for span in line["spans"]]
        outside = [span["text"] for span in spans if span["bbox"][0] < -1 or span["bbox"][1] < -1
                   or span["bbox"][2] > page.rect.width + 1 or span["bbox"][3] > page.rect.height + 1]
        if outside:
            issues.append({"page": index + 1, "problem": "text-outside-page", "text": outside})
        if "\ufffd" in page_text:
            issues.append({"page": index + 1, "problem": "replacement-character"})
        if len(page_text.split()) < 10:
            issues.append({"page": index + 1, "problem": "nearly-empty-page"})
        body_words = sum(len(span["text"].split()) for span in spans
                         if page.rect.height * .085 < span["bbox"][1] < page.rect.height * .88)
        if body_words < 15:
            issues.append({"page": index + 1, "problem": "nearly-empty-body-or-orphan-overflow", "bodyWords": body_words})
        if not re.search(r"AI-generated backtest", normalized, re.I) or "X Account Backtest skill" not in normalized:
            issues.append({"page": index + 1, "problem": "missing-AI-skill-disclosure"})
        if "Follow Dave Wang" not in normalized or "www.davewang.ai" not in normalized:
            issues.append({"page": index + 1, "problem": "missing-follow-and-resource-CTA"})
        if not any(link.get("uri", "").rstrip("/") == "https://www.davewang.ai" for link in page.get_links()):
            issues.append({"page": index + 1, "problem": "missing-working-Dave-Wang-CTA-link"})
        if index > 0 and "Dave Wang Automation" not in normalized:
            issues.append({"page": index + 1, "problem": "missing-automation-brand"})
        footer_spans = [span for span in spans if "AI-generated backtest" in span["text"]]
        if index > 0 and not any(span["bbox"][1] >= page.rect.height * .85 for span in footer_spans):
            issues.append({"page": index + 1, "problem": "AI-disclosure-not-in-footer"})
        if index > 0:
            disclosure = [span for span in footer_spans if span["bbox"][1] >= page.rect.height * .85]
            cta = [span for span in spans if "Follow Dave Wang" in span["text"] and span["bbox"][1] >= page.rect.height * .85]
            website = [span for span in spans if "www.davewang.ai" in span["text"] and span["bbox"][1] >= page.rect.height * .85]
            for candidates in (disclosure, cta, website):
                candidates.sort(key=lambda span: span["bbox"][1], reverse=True)
            footer = disclosure + cta + website
            if disclosure and min(span["size"] for span in disclosure) < 9:
                issues.append({"page": index + 1, "problem": "footer-disclosure-too-small"})
            if footer and page.rect.height - max(span["bbox"][3] for span in footer) < 30:
                issues.append({"page": index + 1, "problem": "footer-too-close-to-paper-edge"})
            if cta and disclosure and abs(cta[0]["bbox"][0] - disclosure[0]["bbox"][0]) > 2:
                issues.append({"page": index + 1, "problem": "footer-left-edges-misaligned"})
            if cta and website and abs(cta[0]["origin"][1] - website[0]["origin"][1]) > 2:
                issues.append({"page": index + 1, "problem": "footer-CTA-and-website-on-different-rows"})
        detail = {"page": index + 1, "words": len(page_text.split()), "bodyWords": body_words, "firstLines": page_text.splitlines()[:5],
                  "outsideTextCount": len(outside)}
        if render:
            image_path = destination / f"page-{index + 1:02d}.png"
            page.get_pixmap(matrix=fitz.Matrix(1.67, 1.67), alpha=False).save(image_path)
            detail["render"] = image_path.name
            image = Image.open(image_path).convert("RGB")
            image.thumbnail((250, 325))
            card = Image.new("RGB", (270, 350), "#DEDCD9")
            card.paste(image, ((270 - image.width) // 2, 10))
            ImageDraw.Draw(card).text((12, 334), f"Page {index + 1}", fill="#262A33")
            thumbs.append(card)
        pages.append(detail)
    all_text = "\n\n".join(full_text)
    attribution_text = all_text + "\n" + json.dumps(doc.metadata)
    if re.search(r"wall\s*street\s*prompt|wallstreetprompt\.com|research\s+by", attribution_text, re.I):
        issues.append({"problem": "inherited-company-or-personal-research-attribution"})
    log_path = pdf.with_suffix(".log")
    overflows = []
    if log_path.exists():
        log = log_path.read_text(encoding="utf-8", errors="replace")
        overflows = [float(value) for value in re.findall(r"Overfull \\[hv]box \(([\d.]+)pt too (?:wide|high)\)", log)
                     if float(value) > 2]
        if overflows:
            issues.append({"problem": "LaTeX-overflow", "points": overflows})
    if render:
        columns = 4
        sheet = Image.new("RGB", (columns * 270, ((len(thumbs) + columns - 1) // columns) * 350), "white")
        for index, card in enumerate(thumbs):
            sheet.paste(card, ((index % columns) * 270, (index // columns) * 350))
        sheet.save(destination / "contact-sheet.png")
    report = {"pdf": pdf.name, "sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
              "pages": len(doc), "passed": not issues, "issues": issues, "pageDetails": pages,
              "reviewRequired": "Inspect every rendered page for chart-label collisions and readable tables before delivery.",
              "validationScope": "Layout and attribution only; no inference about numerical or source correctness."}
    (destination / "report-validation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (destination / "report-text.txt").write_text(all_text, encoding="utf-8")
    doc.close()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--no-render", action="store_true", help="Run text/attribution checks without page images.")
    args = parser.parse_args()
    result = validate(args.pdf, args.output_dir, render=not args.no_render)
    print(json.dumps({"pages": result["pages"], "passed": result["passed"], "issues": result["issues"]}))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
