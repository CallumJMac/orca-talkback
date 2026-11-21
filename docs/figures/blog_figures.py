# /// script
# requires-python = ">=3.11"
# dependencies = ["matplotlib>=3.10,<3.11", "numpy"]
# ///
"""Charts for the orca-talkback blog post, as theme-aware SVGs.

echo-cancellation.json holds the microphone level before and after AEC3 for
one four-sentence reply through MacBook speakers. The time-to-first-word
stages are medians of five runs on an M4 Max.

    uv run docs/figures/blog_figures.py --output-dir ../CallumJMac.github.io/public/assets/orca-talkback
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import matplotlib

matplotlib.use("svg")
import matplotlib.pyplot as plt
import numpy as np

FIGURE_DIR = Path(__file__).resolve().parent
LIGHT = {
    "surface": "#fdfdfd",
    "ink": "#1d1d1f",
    "ink-secondary": "#55565b",
    "ink-muted": "#8c8d92",
    "rule": "#b9babe",
    "series-1": "#2a78d6",
    "series-2": "#eb6834",
}
DARK = {
    "surface": "#212737",
    "ink": "#eaedf3",
    "ink-secondary": "#b8bdc9",
    "ink-muted": "#8a90a0",
    "rule": "#5a6278",
    "series-1": "#3987e5",
    "series-2": "#d95926",
}
INK, SECONDARY, MUTED = LIGHT["ink"], LIGHT["ink-secondary"], LIGHT["ink-muted"]
FULL_WIDTH_INCHES = 6.0
DISPLAY_PIXELS_PER_POINT = 680 / (FULL_WIDTH_INCHES * 72)
# Median milliseconds from the agent finishing to the first word.
STAGES = [
    ("read the reply from Orca", 370),
    ("synthesise the first sentence", 320),
    ("speaker buffer", 120),
]


def apply_style() -> None:
    plt.rcParams.update(
        {
            "svg.fonttype": "path",
            "svg.hashsalt": "orca-talkback-blog",
            "font.family": "sans-serif",
            "font.sans-serif": ["Helvetica", "Arial"],
            "font.size": 8,
            "axes.labelsize": 8.5,
            "axes.labelcolor": INK,
            "axes.edgecolor": SECONDARY,
            "axes.linewidth": 0.6,
            "axes.facecolor": LIGHT["surface"],
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": False,
            "xtick.color": SECONDARY,
            "ytick.color": SECONDARY,
            "xtick.labelcolor": INK,
            "ytick.labelcolor": INK,
            "xtick.labelsize": 7.5,
            "ytick.labelsize": 7.5,
            "xtick.direction": "out",
            "ytick.direction": "out",
            "xtick.major.size": 2.5,
            "ytick.major.size": 2.5,
            "xtick.major.width": 0.6,
            "ytick.major.width": 0.6,
            "figure.facecolor": LIGHT["surface"],
            "savefig.facecolor": LIGHT["surface"],
            "text.color": INK,
        }
    )


def theme_and_save(figure, path: Path, description: str) -> dict:
    figure.savefig(
        path,
        format="svg",
        bbox_inches="tight",
        pad_inches=0.04,
        metadata={"Date": None},
    )
    plt.close(figure)
    svg = path.read_text()
    for token, light_hex in LIGHT.items():
        svg = re.sub(re.escape(light_hex), f"var(--{token})", svg, flags=re.IGNORECASE)

    def block(palette: dict[str, str]) -> str:
        return " ".join(f"--{key}: {value};" for key, value in palette.items())

    style = (
        f"<style>:root {{ {block(LIGHT)} }} @media (prefers-color-scheme: dark) "
        f"{{ :root {{ {block(DARK)} }} }}</style>"
    )
    escaped = description.replace("&", "&amp;").replace("<", "&lt;")
    svg = re.sub(r"<metadata>.*?</metadata>", "", svg, flags=re.DOTALL)
    svg = re.sub(
        r"(<svg[^>]*>)",
        lambda match: f"{match.group(1)}<desc>{escaped}</desc>{style}",
        svg,
        count=1,
    )
    path.write_text(svg)
    width, height = map(
        float, re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg).groups()
    )
    return {
        "width": round(width * DISPLAY_PIXELS_PER_POINT),
        "height": round(height * DISPLAY_PIXELS_PER_POINT),
    }


def figure_time_to_first_word(output_dir: Path) -> dict:
    figure, axis = plt.subplots(figsize=(FULL_WIDTH_INCHES, 1.25))
    colours = [LIGHT["series-1"], LIGHT["series-2"], LIGHT["rule"]]
    start = 0
    for (name, duration), colour in zip(STAGES, colours, strict=True):
        axis.barh(
            0,
            duration,
            left=start,
            height=0.42,
            color=colour,
            edgecolor=LIGHT["surface"],
            linewidth=1.2,
        )
        axis.annotate(
            f"{name}\n{duration} ms",
            xy=(start + duration / 2, -0.21),
            xytext=(0, -4),
            textcoords="offset points",
            ha="center",
            va="top",
            fontsize=7.5,
            color=INK,
        )
        start += duration
    axis.axvline(start, color=INK, linewidth=0.7, ymin=0.5, ymax=0.86)
    axis.annotate(
        f"first word\n{start} ms",
        xy=(start, 0.25),
        xytext=(4, 0),
        textcoords="offset points",
        ha="left",
        va="center",
        fontsize=7.5,
        color=INK,
    )
    axis.set_xlim(0, 960)
    axis.set_ylim(-1.05, 0.55)
    axis.spines["left"].set_visible(False)
    axis.spines["bottom"].set_bounds(0, 900)
    axis.set_yticks([])
    axis.set_xticks(range(0, 901, 100))
    axis.xaxis.set_ticks_position("top")
    axis.spines["bottom"].set_visible(False)
    axis.spines["top"].set_visible(True)
    axis.spines["top"].set_bounds(0, 900)
    axis.xaxis.set_label_position("top")
    axis.set_xlabel("Milliseconds after the agent finishes")
    return theme_and_save(
        figure,
        output_dir / "time-to-first-word.svg",
        "Timeline from the agent finishing to the first spoken word: reading the reply from "
        "Orca takes 370 ms, synthesising the first sentence 320 ms and the speaker buffer "
        "120 ms, so the first word starts at 810 ms.",
    )


def figure_echo_cancellation(output_dir: Path) -> dict:
    data = json.loads((FIGURE_DIR / "echo-cancellation.json").read_text())
    time = np.array(data["time_s"])
    microphone = np.array(data["microphone_dbfs"])
    cancelled = np.array(data["after_aec_dbfs"])
    figure, axis = plt.subplots(figsize=(FULL_WIDTH_INCHES, 2.45))
    for values, colour, name in (
        (microphone, LIGHT["series-2"], "microphone"),
        (cancelled, LIGHT["series-1"], "after echo\ncancellation"),
    ):
        axis.plot(time, values, color=colour, linewidth=1.0)
        median = float(np.median(values))
        axis.hlines(
            median,
            time[0],
            time[-1],
            color=colour,
            linewidth=0.7,
            linestyle=(0, (3, 2)),
        )
        axis.annotate(
            f"{name}\nmedian {median:.0f} dBFS".replace("-", "\u2212"),
            xy=(time[-1], median),
            xytext=(6, 0),
            textcoords="offset points",
            va="center",
            fontsize=7.5,
            color=colour,
        )
    reduction = float(np.median(microphone - cancelled))
    axis.set_xlim(0, time[-1] + 2.6)
    axis.spines["bottom"].set_bounds(0, 12)
    axis.set_xticks(range(0, 13, 2))
    axis.set_ylim(-80, -20)
    axis.set_xlabel("Time into the reply (s)")
    axis.set_ylabel("Level (dBFS)")
    return theme_and_save(
        figure,
        output_dir / "echo-cancellation.svg",
        "Microphone level while orca-talkback speaks a four-sentence reply through MacBook "
        f"speakers. The microphone hears its voice at a median of {np.median(microphone):.0f} "
        f"dBFS; after echo cancellation the median is {np.median(cancelled):.0f} dBFS, "
        f"a median of {reduction:.0f} dB quieter per sample, with brief jumps at the start of each sentence.",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    apply_style()
    sizes = {
        "time-to-first-word": figure_time_to_first_word(args.output_dir),
        "echo-cancellation": figure_echo_cancellation(args.output_dir),
    }
    print(json.dumps(sizes))


if __name__ == "__main__":
    main()
