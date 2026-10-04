"""Publication charts for a frozen X account backtest.

Only observed rows are drawn. Rates exclude exact-zero outcomes from their
binary denominator. Wilson intervals are descriptive and assume independence;
they do not establish investment skill. Small heatmap cells are suppressed.
PNG exports are 300 dpi; PDF exports preserve vector text and marks.
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter
from datetime import date
import json
import math
from pathlib import Path
import textwrap

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.ticker import FuncFormatter, MaxNLocator
import numpy as np
import pandas as pd

from statistical_summary import wilson_interval


ROOT = Path(__file__).resolve().parent
COLORS = {"positive": "#37967A", "negative": "#B4441E", "text": "#262A33",
          "secondary": "#66605C", "grid": "#DEDCD9", "pale": "#E4F0EB",
          "gray": "#B9BCBA", "blue": "#0F5499", "unavailable": "#F1F1EF"}
PRIMARY = 21
SCORED = {"hit", "miss", "neutral"}
DEFAULT_SOURCE = "Archived X text; split-adjusted prices; X Account Backtest skill calculations"

matplotlib.rcParams.update({
    "font.family": "sans-serif", "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans"],
    "font.size": 10.5, "text.color": COLORS["text"], "axes.labelcolor": COLORS["text"],
    "axes.edgecolor": COLORS["secondary"], "axes.facecolor": "white", "figure.facecolor": "white",
    "axes.spines.top": False, "axes.spines.right": False, "axes.spines.left": False,
    "axes.linewidth": .7, "axes.axisbelow": True, "axes.grid": False,
    "xtick.color": COLORS["secondary"], "ytick.color": COLORS["secondary"],
    "xtick.labelsize": 10, "ytick.labelsize": 10, "ytick.left": False,
    "legend.frameon": False, "legend.fontsize": 10, "lines.linewidth": 1.8,
    "savefig.dpi": 300, "pdf.fonttype": 42, "ps.fonttype": 42,
})


def label(value: str, width: int = 26) -> str:
    text = str(value).replace("_", " ").replace("-", " ").strip()
    text = {"bullish": "Bullish", "bearish": "Bearish", "unspecified": "Unspecified",
            "explicit": "Explicit", "inferred": "Inferred"}.get(text, text[:1].upper() + text[1:])
    return "\n".join(textwrap.wrap(text, width=width)) or "Unspecified"


def pct(value, decimals: int = 0) -> str:
    return "n/a" if value is None or not math.isfinite(float(value)) else f"{float(value):.{decimals}f}%"


def style_axis(ax, axis: str = "y") -> None:
    ax.grid(True, axis=axis, color=COLORS["grid"], linewidth=.65)
    ax.tick_params(axis="both", length=0, pad=6)
    for spine in ("left", "right", "top"):
        ax.spines[spine].set_visible(False)


def hit_metric(rows: pd.DataFrame, relative: bool = False) -> dict:
    field = "relativeStatus" if relative else "status"
    eligible = rows.loc[rows["status"].isin(SCORED)] if relative else rows
    counts = Counter(eligible[field].fillna("unscored"))
    n = counts["hit"] + counts["miss"]
    return {"hits": counts["hit"], "misses": counts["miss"], "n": n,
            "rate": counts["hit"] / n * 100 if n else None,
            "ci": wilson_interval(counts["hit"], n)}


def errors(rate_value: float, ci: list[float]) -> list[list[float]]:
    # At 0% or 100%, floating-point rounding can otherwise make an error
    # length a tiny negative number, which matplotlib correctly rejects.
    return [[max(0., rate_value - ci[0])], [max(0., ci[1] - rate_value)]]


def json_safe(value):
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, np.generic):
        return value.item()
    return value


class StudyCharts:
    def __init__(self, rows: pd.DataFrame, summary: dict, *, archive: pd.DataFrame | None,
                 calls: list[dict] | None, audit: dict | None, destination: Path, source: str,
                 study: dict | None = None):
        self.rows, self.summary, self.archive = rows, summary, archive
        self.calls, self.audit, self.destination, self.source = calls, audit or {}, destination, source
        self.study = study or {}
        self.primary_horizon = int(summary.get("metadata", {}).get("primaryHorizon", PRIMARY))
        self.episode_spacing = int(summary.get("metadata", {}).get("episodeSpacingSessions", self.primary_horizon))
        self.primary = rows.loc[rows["horizon"] == self.primary_horizon].copy()
        self.scored = self.primary.loc[self.primary["status"].isin(SCORED)].copy()
        # A stale summary must not silently disagree with the plotted ledger.
        for horizon, expected in summary.get("byHorizon", {}).items():
            group = rows.loc[rows["horizon"] == int(horizon)]
            for relative, rate_key, denominator_key, hit_key, miss_key in (
                    (False, "hitRatePct", "binaryDenominator", "hits", "misses"),
                    (True, "relativeHitRatePct", "relativeBinaryDenominator", "relativeHits", "relativeMisses")):
                measured = hit_metric(group, relative)
                for key, value in ((denominator_key, measured["n"]), (hit_key, measured["hits"]), (miss_key, measured["misses"])):
                    if key in expected and expected[key] != value:
                        raise ValueError(f"Summary {key} disagrees with the ledger at horizon {horizon}.")
                if rate_key in expected:
                    actual, given = measured["rate"], expected[rate_key]
                    if (actual is None) != (given is None) or (actual is not None and not math.isclose(actual, float(given), abs_tol=1e-9)):
                        raise ValueError(f"Summary {rate_key} disagrees with the ledger at horizon {horizon}.")
        self.manifest = []
        destination.mkdir(parents=True, exist_ok=True)

    def save(self, fig, stem: str, title: str, subtitle: str, note: str, *, left=.12,
             right=.96, top=.76, bottom=.17, evidence: dict | None = None):
        # Measure real font geometry before layout. Wrapping protects full claims
        # and labels without shrinking them or clipping at the export boundary.
        title_artist = fig.text(.035, .97, title, va="top", ha="left", fontsize=14.3,
                                fontweight="bold", linespacing=1.2)
        fig.canvas.draw()
        renderer = fig.canvas.get_renderer()
        available = fig.bbox.width * .925
        wrapped_title = title
        width = 70
        while title_artist.get_window_extent(renderer).width > available and width > 20:
            width -= 2
            wrapped_title = "\n".join(textwrap.wrap(title, width=width))
            title_artist.set_text(wrapped_title)
            fig.canvas.draw()
        title_height = title_artist.get_window_extent(renderer).height / fig.bbox.height
        subtitle_y = .97 - title_height - .025
        subtitle_artist = fig.text(.035, subtitle_y, subtitle, va="top", ha="left", fontsize=10.3,
                                   color=COLORS["secondary"], linespacing=1.3)
        fig.canvas.draw()
        width = 95
        while subtitle_artist.get_window_extent(renderer).width > available and width > 30:
            width -= 2
            subtitle_artist.set_text("\n".join(textwrap.wrap(subtitle, width=width)))
            fig.canvas.draw()
        subtitle_bottom = subtitle_y - subtitle_artist.get_window_extent(renderer).height / fig.bbox.height
        top = min(top, subtitle_bottom - .045)
        fig.subplots_adjust(left=left, right=right, top=top, bottom=bottom, wspace=.42, hspace=.55)
        fig.text(.035, .075, note, va="bottom", ha="left", fontsize=9.2,
                 color=COLORS["secondary"], linespacing=1.3)
        source_text = "\n".join(textwrap.wrap("Source: " + self.source, width=108))
        fig.text(.035, .018, source_text, va="bottom", ha="left",
                 fontsize=8.7, color=COLORS["secondary"])
        for suffix in ("png", "pdf"):
            metadata = ({"Title": title, "Software": "X Account Backtest skill"} if suffix == "png" else
                        {"Title": title, "Author": "Dave Wang Automation", "Subject": "AI-generated chart using the X Account Backtest skill"})
            fig.savefig(self.destination / f"{stem}.{suffix}", dpi=300, facecolor="white", metadata=metadata)
        plt.close(fig)
        self.manifest.append({"stem": stem, "title": title, "subtitle": subtitle, "note": note,
                              "png": f"{stem}.png", "pdf": f"{stem}.pdf",
                              "dataCsv": f"{stem}-data.csv",
                              "evidence": json_safe(evidence or {})})
        with (self.destination / f"{stem}-data.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["dataPath", "value"])
            def write_values(value, prefix=""):
                if isinstance(value, dict):
                    for key, nested in value.items():
                        write_values(nested, f"{prefix}.{key}" if prefix else str(key))
                elif isinstance(value, (list, tuple)):
                    for index, nested in enumerate(value):
                        write_values(nested, f"{prefix}[{index}]")
                else:
                    writer.writerow([prefix, value])
            write_values(json_safe(evidence or {}))

    def skipped(self, stem: str, reason: str):
        self.manifest.append({"stem": stem, "skipped": True, "reason": reason})

    def coverage(self):
        if self.archive is None or self.archive.empty:
            self.skipped("01_archive_coverage", "Archive month-count file unavailable.")
            return
        archive = self.archive.copy()
        audited_posts = self.audit.get("normalizedPosts")
        if audited_posts is not None and int(archive["authored"].sum()) != int(audited_posts):
            raise ValueError("Monthly source counts do not reconcile with the frozen archive audit.")
        archive["year"] = archive["month"].astype(str).str[:4]
        years = sorted(archive["year"].unique())
        posts = archive.groupby("year")["authored"].sum().reindex(years, fill_value=0)
        exhausted = archive.loc[archive["state"] == "search_exhausted"].groupby("year").size().reindex(years, fill_value=0)
        total_months = archive.groupby("year").size().reindex(years, fill_value=0)
        call_years = (pd.DataFrame(self.calls) if self.calls is not None else self.primary)
        if not call_years.empty:
            call_years = call_years.copy()
            if "publicationDateET" in call_years:
                dates = call_years["publicationDateET"].astype(str)
            else:
                dates = pd.to_datetime(call_years["publishedAt"], utc=True).dt.tz_convert(
                    "America/New_York").dt.strftime("%Y-%m-%d")
            call_years["year"] = dates.str[:4]
            identified = call_years.groupby("year").size().reindex(years, fill_value=0)
        else:
            identified = pd.Series(0, index=years)
        fig, axes = plt.subplots(2, 1, figsize=(6.7, 5.2), gridspec_kw={"height_ratios": [1.4, 1]})
        x = np.arange(len(years))
        for ax, values, color, heading in ((axes[0], posts, COLORS["gray"], "Authored posts retrieved"),
                                           (axes[1], identified, COLORS["positive"], "Directional views identified")):
            ax.bar(x, values.values, color=color, width=.65)
            style_axis(ax)
            ax.set_xticks(x, years)
            ax.yaxis.set_major_locator(MaxNLocator(integer=True, nbins=3))
            ax.set_title(heading, loc="left", fontsize=11.2, fontweight="bold", pad=14)
            upper = max(values.max(), 1) * 1.28
            ax.set_ylim(0, upper)
            for index, value in enumerate(values):
                ax.text(index, value + upper * .025, f"{int(value):,}", ha="center", fontsize=10)
        finished = int(exhausted.sum())
        searched = int(total_months.sum())
        search_note = (f"All {searched} monthly searches reached the end of available results."
                       if finished == searched else
                       f"{finished} of {searched} monthly searches reached the end of available results.")
        period = (f"{self.study['startDate']} to {self.study['endDate']}"
                  if self.study.get("startDate") and self.study.get("endDate") else "the requested publication window")
        self.save(fig, "01_archive_coverage", "Archive coverage and identified calls",
                  f"Retrieved public posts for {period}; boundary years may be partial.",
                  search_note + "\nUnavailable or deleted text, video-only calls and linked-article calls cannot be verified.",
                  top=.75, bottom=.22, evidence={"postsByYear": posts.to_dict(), "callsByYear": identified.to_dict(),
                                               "searchExhaustedMonthsByYear": exhausted.to_dict(), "monthsByYear": total_months.to_dict()})

    def horizons(self):
        horizons = sorted(int(x) for x in self.summary["byHorizon"])
        if not horizons or not self.rows["status"].isin(SCORED).any():
            self.skipped("02_horizon_hit_rates", "No standardized horizons available.")
            return
        fig, axes = plt.subplots(1, 2, figsize=(6.7, 4.65), sharey=True)
        evidence = []
        x = np.arange(len(horizons))
        for index, horizon in enumerate(horizons):
            group = self.rows.loc[self.rows["horizon"] == horizon]
            for ax, relative, color in ((axes[0], False, COLORS["positive"]), (axes[1], True, COLORS["blue"])):
                m = hit_metric(group, relative)
                evidence.append({"horizon": horizon, "relative": relative, **m})
                if m["n"]:
                    ax.errorbar(index, m["rate"], yerr=errors(m["rate"], m["ci"]),
                                fmt="o", color=color, capsize=3, markersize=6, linewidth=1.5)
                    ax.text(index, 5, f"n\n{m['n']}", ha="center", fontsize=10,
                            color=COLORS["secondary"], linespacing=1.15)
                else:
                    ax.text(index, 5, "n/a\nn=0", ha="center", fontsize=10, color=COLORS["secondary"])
            baseline = self.summary["byHorizon"][str(horizon)].get("sameDirectionSpyHitRatePct")
            if baseline is not None:
                axes[0].plot(index, baseline, marker="s", markersize=5.7, markerfacecolor="white",
                             markeredgecolor=COLORS["text"], linestyle="none")
        axes[0].plot([], [], "o", color=COLORS["positive"], label="Account calls")
        axes[0].plot([], [], "s", markerfacecolor="white", markeredgecolor=COLORS["text"], label="Same-direction SPY")
        axes[0].legend(loc="upper left", bbox_to_anchor=(-.05, 1.18), ncol=1, handletextpad=.4, borderaxespad=0)
        for ax, heading in zip(axes, ("Directional hit rate", "Market-relative hit rate")):
            style_axis(ax)
            ax.set_title(heading, loc="left", fontsize=11.2, fontweight="bold", pad=30)
            ax.set_xticks(x, [str(h) for h in horizons])
            ax.set_xlabel("SPY trading-session intervals", labelpad=10, fontsize=10)
            ax.set_ylim(0, 104)
            ax.set_yticks([0, 25, 50, 75, 100])
            ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))
        axes[1].axhline(50, color=COLORS["secondary"], linewidth=.9, linestyle=(0, (3, 3)))
        self.save(fig, "02_horizon_hit_rates", "Directional and market-relative hit rates by horizon",
                  "Standardized forward windows; market-relative results use matching dates and direction.",
                  "Whiskers: 95% Wilson intervals, assuming independent calls. n = hits + misses.\nExact-zero returns are excluded. The 50% line is a reference, not a test of skill.",
                  top=.68, bottom=.26, left=.1, evidence={"rates": evidence})

    def call_types(self):
        groups = []
        for key, rows in self.primary.groupby("callType", dropna=False):
            raw, relative = hit_metric(rows), hit_metric(rows, True)
            if raw["n"] or relative["n"]:
                groups.append((str(key), raw, relative))
        groups.sort(key=lambda x: x[1]["rate"] if x[1]["rate"] is not None else -1, reverse=True)
        if not groups:
            self.skipped("03_call_type_hit_rates", "No binary outcomes by call type.")
            return
        height = max(4.7, 2.05 + .45 * len(groups))
        fig, axes = plt.subplots(1, 2, figsize=(6.7, height), sharey=True)
        y = np.arange(len(groups))
        for index, (key, raw, relative) in enumerate(groups):
            for ax, metric, color in ((axes[0], raw, COLORS["positive"]), (axes[1], relative, COLORS["blue"])):
                if metric["n"]:
                    low, high = metric["ci"]
                    ax.errorbar(metric["rate"], index, xerr=errors(metric["rate"], metric["ci"]),
                                fmt="o", color=color, markersize=6, linewidth=1.6, capsize=3)
                    ax.text(104, index, f"{metric['rate']:.0f}%\nn={metric['n']:,}", va="center", fontsize=10)
                else:
                    ax.text(104, index, "n/a\nn=0", va="center", fontsize=10, color=COLORS["secondary"])
        axes[0].set_yticks(y, [label(key, 17) for key, _, _ in groups])
        axes[0].set_ylim(len(groups)-.5, -.5)
        for ax, title in zip(axes, ("Directional hit rate", "Market-relative hit rate")):
            style_axis(ax, "x")
            ax.set_title(title, loc="left", fontsize=11, fontweight="bold", pad=13)
            ax.set_xlim(0, 130)
            ax.set_xticks([0, 50, 100], ["0%", "50%", "100%"])
            ax.axvline(50, color=COLORS["secondary"], linewidth=.8, linestyle=(0, (3, 3)))
        axes[1].tick_params(labelleft=False)
        self.save(fig, "03_call_type_hit_rates", "Hit rates by call type",
                  f"{self.primary_horizon}-session outcomes by source-text call type; categories are descriptive.",
                  "Dots: hit rates. Whiskers: 95% Wilson intervals assuming independent calls.\nGroups are ordered by directional hit rate; the ranking does not establish significance.",
                  left=.14, right=.95, top=.76, bottom=.2,
                  evidence={"groups": [{"callType": key, "directional": raw, "relative": relative} for key, raw, relative in groups]})

    def thesis_heatmap(self, minimum: int = 20):
        if "thesis" not in self.rows:
            self.skipped("04_thesis_horizon_heatmap", "Source-derived thesis labels unavailable.")
            return
        horizons = sorted(self.rows["horizon"].unique())
        observed = self.rows.loc[self.rows["status"].isin(SCORED)]
        theses = sorted(observed["thesis"].fillna("unspecified").unique(),
                        key=lambda x: len(self.primary.loc[self.primary["thesis"] == x]), reverse=True)
        if not theses:
            self.skipped("04_thesis_horizon_heatmap", "No thesis labels found.")
            return
        rates = np.full((len(theses), len(horizons)), np.nan)
        denominators = np.zeros_like(rates, dtype=int)
        evidence = []
        for row_index, thesis in enumerate(theses):
            for col_index, horizon in enumerate(horizons):
                rows = self.rows.loc[(self.rows["thesis"].fillna("unspecified") == thesis) & (self.rows["horizon"] == horizon)]
                m = hit_metric(rows, True)
                denominators[row_index, col_index] = m["n"]
                if m["n"] >= minimum:
                    rates[row_index, col_index] = m["rate"]
                evidence.append({"thesis": str(thesis), "horizon": int(horizon), "masked": m["n"] < minimum, **m})
        fig, ax = plt.subplots(figsize=(6.7, max(4.75, 2.3 + .40 * len(theses))))
        cmap = LinearSegmentedColormap.from_list("call_rates", ["#B4441E", "#F4F4F2", "#37967A"])
        cmap.set_bad(COLORS["unavailable"])
        image = ax.imshow(np.ma.masked_invalid(rates), vmin=0, vmax=100, aspect="auto", cmap=cmap)
        for row_index in range(len(theses)):
            for col_index in range(len(horizons)):
                value, n = rates[row_index, col_index], denominators[row_index, col_index]
                if np.isfinite(value):
                    text, color = f"{value:.0f}%\nn={n:,}", "white" if value <= 25 or value >= 75 else COLORS["text"]
                else:
                    text, color = (f"n={n:,}" if n else "n/a"), COLORS["secondary"]
                ax.text(col_index, row_index, text, color=color, ha="center", va="center", fontsize=10)
        ax.set_yticks(np.arange(len(theses)), [label(t, 26) for t in theses])
        ax.set_xticks(np.arange(len(horizons)), [str(h) for h in horizons])
        ax.set_xlabel("SPY trading-session intervals", labelpad=9)
        ax.tick_params(length=0, pad=8)
        ax.set_xticks(np.arange(len(horizons) + 1) - .5, minor=True)
        ax.set_yticks(np.arange(len(theses) + 1) - .5, minor=True)
        ax.grid(which="minor", color="white", linewidth=2)
        ax.tick_params(which="minor", bottom=False, left=False)
        for spine in ax.spines.values():
            spine.set_visible(False)
        colorbar_axis = fig.add_axes((.2, .19, .75, .022))
        colorbar = fig.colorbar(image, cax=colorbar_axis, orientation="horizontal", ticks=[0, 50, 100])
        colorbar.ax.set_xticklabels(["0%", "50%", "100%"])
        colorbar.outline.set_visible(False)
        self.save(fig, "04_thesis_horizon_heatmap", "Market-relative hit rates by thesis and horizon",
                  "Source-text thesis categories; standardized forward windows.",
                  f"Gray cells have fewer than {minimum} binary outcomes; no rate is displayed.\nPercentages are rounded; n = hits + misses. Comparisons do not adjust for multiple testing.",
                  left=.2, right=.95, top=.81, bottom=.32, evidence={"minimumN": minimum, "cells": evidence})

    def year_bias(self):
        years = sorted(self.scored["publicationDateET"].astype(str).str[:4].unique())
        if not len(self.scored):
            self.skipped("05_year_bias_hit_rates", "No scored outcomes by publication year.")
            return
        fig, axes = plt.subplots(1, 2, figsize=(6.7, max(4.65, 2.5 + .52 * len(years))), sharey=True)
        evidence = []
        for ax, direction, color in ((axes[0], "bullish", COLORS["positive"]), (axes[1], "bearish", COLORS["negative"])):
            for index, year in enumerate(years):
                rows = self.primary.loc[(self.primary["publicationDateET"].astype(str).str[:4] == year) & (self.primary["direction"] == direction)]
                m = hit_metric(rows)
                evidence.append({"year": year, "direction": direction, **m})
                if m["n"]:
                    low, high = m["ci"]
                    ax.errorbar(m["rate"], index, xerr=errors(m["rate"], m["ci"]),
                                fmt="o", color=color, markerfacecolor=color if m["n"] >= 20 else "white",
                                markersize=6, linewidth=1.5, capsize=3)
                    ax.text(104, index, f"{m['rate']:.0f}%\nn={m['n']:,}", va="center", fontsize=10)
                else:
                    ax.text(52, index, "No binary outcomes", fontsize=10, color=COLORS["secondary"], ha="center")
            ax.set_title(label(direction), loc="left", fontweight="bold", fontsize=11.3, pad=15)
            style_axis(ax, "x")
            ax.set_xlim(0, 128)
            ax.set_xticks([0, 50, 100], ["0%", "50%", "100%"])
        axes[0].set_yticks(np.arange(len(years)), years)
        axes[0].invert_yaxis()
        axes[1].tick_params(labelleft=False)
        self.save(fig, "05_year_bias_hit_rates", "Directional hit rates by year and bias",
                  f"{self.primary_horizon}-session outcomes by publication year and bullish or bearish direction.",
                  "Whiskers: 95% Wilson intervals assuming independent calls. Open dots: n < 20.\nYear and bias groups describe the sample; the analysis does not isolate market regimes.",
                  left=.11, right=.92, top=.76, bottom=.2, evidence={"groups": evidence})

    def distribution(self):
        if len(self.scored) < 2:
            self.skipped("06_return_distributions", "At least two scored observations are needed to display a return distribution.")
            return
        fig, axes = plt.subplots(1, 2, figsize=(6.7, 4.75), sharey=True)
        fig.subplots_adjust(left=.125, right=.97, top=.75, bottom=.26, wspace=.42)
        evidence = []
        for ax, field, heading in ((axes[0], "directionalReturnPct", "Directional price return"),
                                   (axes[1], "relativeReturnPct", "Market-relative return")):
            all_values = []
            for direction, color, line in (("bullish", COLORS["positive"], "-"), ("bearish", COLORS["negative"], "--")):
                values = np.sort(self.scored.loc[self.scored["direction"] == direction, field].dropna().to_numpy(dtype=float))
                if not len(values):
                    continue
                cumulative = np.arange(1, len(values) + 1) / len(values) * 100
                ax.step(values, cumulative, where="post", color=color, linestyle=line,
                        label=f"{label(direction)} · n={len(values):,}")
                all_values.extend(values.tolist())
                evidence.append({"field": field, "direction": direction, "n": len(values),
                                 "minimum": float(values[0]), "maximum": float(values[-1]),
                                 "median": float(np.median(values)), "mean": float(np.mean(values))})
            style_axis(ax)
            ax.axvline(0, color=COLORS["text"], linewidth=.9)
            ax.set_title(heading, loc="left", fontsize=11.1, fontweight="bold", pad=16)
            ax.set_ylim(0, 103)
            ax.set_yticks([0, 25, 50, 75, 100])
            ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))
            ax.set_xscale("symlog", linthresh=10, linscale=1)
            if all_values:
                minimum, maximum = min(all_values), max(all_values)
                ax.set_xlim(min(-10, minimum * 1.12), max(10, maximum * 1.12))
                candidates = [-1000, -500, -100, -50, -10, 0, 10, 50, 100, 500, 1000, 5000]
                ticks = [t for t in candidates if ax.get_xlim()[0] <= t <= ax.get_xlim()[1]]
                # Keep legible ticks at the existing font size on a nonlinear axis.
                # Zero and the central +/-10% transition remain visible.
                fixed = {v for v in (-10, 0, 10) if v in ticks}
                fig.canvas.draw()
                renderer = fig.canvas.get_renderer()
                selected = list(fixed)
                for value in sorted((v for v in ticks if v not in fixed), key=abs, reverse=True):
                    pixel_x = ax.transData.transform((value, 0))[0]
                    width = renderer.get_text_width_height_descent(f"{value:g}%", ax.xaxis.get_ticklabels()[0].get_fontproperties(), False)[0]
                    collides = False
                    for kept in selected:
                        kept_x = ax.transData.transform((kept, 0))[0]
                        kept_width = renderer.get_text_width_height_descent(f"{kept:g}%", ax.xaxis.get_ticklabels()[0].get_fontproperties(), False)[0]
                        if abs(pixel_x-kept_x) < (width+kept_width)/2 + 2:
                            collides = True
                            break
                    if not collides:
                        selected.append(value)
                ticks = sorted(selected)
                ax.set_xticks(ticks)
            ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}%"))
            ax.set_xlabel("Forward return", labelpad=10)
        axes[0].set_ylabel("Share of observed calls at or below return", fontsize=10.2, labelpad=10)
        handles, labels = axes[0].get_legend_handles_labels()
        fig.legend(handles, labels, loc="upper left", bbox_to_anchor=(.125, .87),
                   ncol=2, fontsize=10, handlelength=2, handletextpad=.4, borderaxespad=0)
        self.save(fig, "06_return_distributions", "Distribution of directional and market-relative returns",
                  f"Every scored {self.primary_horizon}-session outcome, including neutral returns and full tails.",
                  "Negative directional returns oppose the call; negative relative returns lag same-direction SPY.\nThe symmetric-log x-axis includes extreme returns; the central ±10% is linear.",
                  left=.125, right=.97, top=.75, bottom=.26, evidence={"distributions": evidence})

    def concentration(self):
        if self.scored.empty:
            self.skipped("07_ticker_concentration", "No scored calls for ticker-concentration analysis.")
            return
        names = self.scored.copy()
        if "canonicalSymbol" in names:
            canonical = names["canonicalSymbol"].fillna("")
            names["groupSymbol"] = canonical.where(canonical != "", names["symbol"])
        else:
            names["groupSymbol"] = names["symbol"]
        counts = names.groupby("groupSymbol").size().sort_values(ascending=False)
        top = counts.head(10)
        remaining = int(counts.iloc[10:].sum())
        if remaining:
            top = pd.concat([top, pd.Series({"All other securities": remaining})])
        fig, ax = plt.subplots(figsize=(6.7, 5.65))
        y = np.arange(len(top))
        ax.barh(y, top.values, color=[COLORS["positive"]] * (len(top) - bool(remaining)) + ([COLORS["gray"]] if remaining else []), height=.63)
        ax.set_yticks(y, top.index)
        ax.invert_yaxis()
        total = int(counts.sum())
        ax.set_xlim(0, max(top.max(), 1) * 1.55)
        for index, number in enumerate(top):
            ax.text(number + top.max() * .025, index, f"{int(number):,} · {number / total * 100:.1f}%", va="center", fontsize=10.3)
        style_axis(ax, "x")
        ax.xaxis.set_major_locator(MaxNLocator(integer=True, nbins=4))
        ax.set_xlabel(f"Scored calls at {self.primary_horizon} sessions", labelpad=10)
        share_top_five = counts.head(5).sum() / total * 100
        self.save(fig, "07_ticker_concentration", "Concentration of scored calls by security",
                  f"{len(counts):,} securities account for {total:,} scored calls; the top five account for {share_top_five:.0f}%.",
                  "Verified ticker renames are grouped as one security. Top ten shown; others pooled.\nSensitivity checks use equal security weights and suppress repeated calls.",
                  left=.23, right=.97, top=.78, bottom=.23,
                  evidence={"scoredCalls": total, "uniqueTickers": len(counts), "topFiveSharePct": share_top_five,
                            "counts": {str(k): int(v) for k, v in counts.items()}})

    def sensitivity(self):
        main = self.summary.get("primary", {})
        ticker = self.summary.get("tickerBalanced", {})
        episode = self.summary.get("episodeSensitivity", {})
        candidates = [("All scored calls", main, f"n={main.get('binaryDenominator', 0):,}"),
                      ("One call per episode", episode.get("summary", {}), f"n={episode.get('summary', {}).get('binaryDenominator', 0):,}"),
                      ("Equal weight per security", ticker, f"{ticker.get('tickers', 0):,} securities")]
        for key, name in (("explicitOnlySensitivity", "Explicit calls only"),
                          ("highConfidenceSensitivity", "Text confidence ≥0.90")):
            if key in self.summary:
                metric = self.summary[key].get("summary", {})
                candidates.append((name, metric, f"n={metric.get('binaryDenominator', 0):,}"))
        items = [item for item in candidates if item[1].get("hitRatePct") is not None]
        if not items:
            self.skipped("08_sensitivity", "No eligible sensitivity estimates.")
            return
        fig, axes = plt.subplots(1, 2, figsize=(6.7, max(4.7, 2.45 + .65 * len(items))), sharey=True)
        for index, (title, metric, count) in enumerate(items):
            for ax, field, color in ((axes[0], "hitRatePct", COLORS["positive"]),
                                     (axes[1], "relativeHitRatePct", COLORS["blue"])):
                value = metric.get(field)
                if value is None:
                    ax.text(105, index, "n/a", fontsize=10.4, va="center", color=COLORS["secondary"])
                    continue
                ax.plot(value, index, marker="o", markersize=7, color=color)
                ax.text(105, index, f"{value:.1f}%", fontsize=10.4, va="center")
        axes[0].set_yticks(np.arange(len(items)), [label(title, 22) + "\n" + count for title, _, count in items])
        axes[0].invert_yaxis()
        for ax, title in zip(axes, ("Directional hit rate", "Market-relative hit rate")):
            style_axis(ax, "x")
            ax.set_title(title, loc="left", fontsize=11.1, fontweight="bold", pad=15)
            ax.set_xlim(0, 130)
            ax.set_xticks([0, 50, 100], ["0%", "50%", "100%"])
            ax.axvline(50, color=COLORS["secondary"], linestyle=(0, (3, 3)), linewidth=.8)
        axes[1].tick_params(labelleft=False)
        self.save(fig, "08_sensitivity", "Hit rates under alternative sampling and weighting rules",
                  f"{self.primary_horizon}-session selection, weighting and repeat-call sensitivities.",
                  f"The episode rule suppresses repeats for {self.episode_spacing} sessions. Each security is equally weighted in row 3.\nText confidence measures interpretation, not forecast success. Intervals are not shown.",
                  left=.29, right=.91, top=.74, bottom=.25,
                  evidence={"comparisons": [{"name": title, "countLabel": count, "metrics": metric} for title, metric, count in items]})

    def render(self):
        for operation in (self.coverage, self.horizons, self.call_types, self.thesis_heatmap,
                          self.year_bias, self.distribution, self.concentration, self.sensitivity):
            operation()
        payload = {"primaryHorizon": self.primary_horizon, "rowCount": len(self.rows),
                   "source": self.source, "rateDenominator": "hits + misses; exact-zero outcomes excluded",
                   "intervalCaution": "Wilson intervals assume independent calls; descriptive only.",
                   "figures": self.manifest}
        (self.destination / "chart-manifest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        return payload


def load_outcomes(path: Path) -> pd.DataFrame:
    required = {"callId", "horizon", "status", "relativeStatus", "symbol", "direction", "callType", "thesis",
                "publishedAt", "publicationDateET", "directionalReturnPct", "relativeReturnPct"}
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        values = payload["outcomes"] if isinstance(payload, dict) else payload
        rows = pd.DataFrame(values) if values else pd.DataFrame(columns=sorted(required))
    else:
        rows = pd.read_csv(path, encoding="utf-8-sig", keep_default_na=False)
    missing = required - set(rows.columns)
    if missing:
        raise ValueError(f"Outcomes are missing required columns: {', '.join(sorted(missing))}")
    rows["horizon"] = pd.to_numeric(rows["horizon"], errors="raise").astype(int)
    for field in ("directionalReturnPct", "relativeReturnPct", "rawReturnPct", "spyReturnPct"):
        if field in rows:
            rows[field] = pd.to_numeric(rows[field], errors="coerce")
    if rows.duplicated(["callId", "horizon"]).any():
        raise ValueError("Duplicate call/horizon rows would inflate chart denominators.")
    scored = rows.loc[rows["status"].isin(SCORED)]
    if not np.isfinite(scored[["directionalReturnPct", "relativeReturnPct"]].to_numpy(dtype=float)).all():
        raise ValueError("Every scored outcome must have finite directional and relative returns.")
    for field in ("callType", "thesis"):
        rows[field] = rows[field].replace("", "unspecified").fillna("unspecified")
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path.cwd(), help="Frozen study directory.")
    parser.add_argument("--outcomes", type=Path)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--archive-months", type=Path)
    parser.add_argument("--archive-audit", type=Path)
    parser.add_argument("--calls", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--source-text")
    args = parser.parse_args()
    args.outcomes = args.outcomes or args.data_dir / "outcomes.json"
    args.summary = args.summary or args.data_dir / "summary.json"
    args.archive_months = args.archive_months or args.data_dir / "archive-month-counts.csv"
    args.archive_audit = args.archive_audit or args.data_dir / "archive-audit.json"
    args.calls = args.calls or args.data_dir / "calls.json"
    args.output_dir = args.output_dir or args.data_dir / "figures"
    study_path = args.data_dir / "study.json"
    study = json.loads(study_path.read_text(encoding="utf-8")) if study_path.exists() else {}
    if study.get("benchmark", "SPY") != "SPY":
        raise ValueError("This report engine supports the SPY-session equity methodology. Use SPY or adapt and validate the complete method first.")
    rows = load_outcomes(args.outcomes)
    summary = json.loads(args.summary.read_text(encoding="utf-8"))
    archive = pd.read_csv(args.archive_months) if args.archive_months.exists() else None
    audit = json.loads(args.archive_audit.read_text(encoding="utf-8")) if args.archive_audit.exists() else None
    calls = json.loads(args.calls.read_text(encoding="utf-8")) if args.calls.exists() else None
    if isinstance(calls, dict):
        calls = calls["calls"]
    charting = StudyCharts(rows, summary, archive=archive, calls=calls, audit=audit,
                          destination=args.output_dir, source=args.source_text or study.get("sourceText") or DEFAULT_SOURCE,
                          study=study)
    payload = charting.render()
    print(json.dumps({"rendered": len([x for x in payload["figures"] if not x.get("skipped")]),
                      "skipped": [x for x in payload["figures"] if x.get("skipped")],
                      "outputDirectory": str(args.output_dir)}))


if __name__ == "__main__":
    main()
