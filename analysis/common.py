"""Shared by the figure scripts: the runs, their metrics, and one plotting style.

Every script reads only runs/<run-id>/{samples.csv,events.csv,meta.json} and recomputes the
metrics from the samples, so a figure never depends on a stored summary.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from podmeasure import metrics  # noqa: E402

INK, INK2, MUTED, GRID = "#0b0b0b", "#52514e", "#898781", "#e4e3df"
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"          # categorical slots 1-3
RATE_RAMP = ["#86b6ef", "#3987e5", "#1c5cab", "#0d366b"]       # one hue, light to dark: ordered rates
COLUMN_IN = 3.45                                               # one IEEE column


def style():
    plt.rcParams.update({
        "font.family": "serif", "font.size": 8, "axes.labelsize": 8, "xtick.labelsize": 7, "ytick.labelsize": 7,
        "legend.fontsize": 7, "axes.edgecolor": MUTED, "axes.labelcolor": INK, "text.color": INK,
        "xtick.color": INK2, "ytick.color": INK2, "axes.linewidth": 0.6, "xtick.major.width": 0.6,
        "ytick.major.width": 0.6, "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.5, "axes.axisbelow": True,
        "lines.linewidth": 1.4, "legend.frameon": False, "pdf.fonttype": 42, "savefig.bbox": "tight",
        "savefig.pad_inches": 0.02,
    })


def args(description):
    ap = argparse.ArgumentParser(description=description)
    ap.add_argument("--runs", default=str(ROOT / "runs"), help="directory of recorded runs")
    ap.add_argument("--out", default=str(ROOT / "figures"), help="where the PDF and CSV go")
    ap.add_argument("--exp", default=None, help="comma-separated experiment ids to use (default: the script's own)")
    return ap.parse_args()


def runs(root, exps=None, interrupted=False):
    """[(meta, samples, summary)] of every valid run, oldest first, limited to experiment ids `exps`.

    A run whose session was lost and reopened has a hole of tens of seconds, so its rate and loss
    describe the interruption, not the stream. Such runs are left out unless `interrupted` is true;
    tab_headline.py lists them all, with their session losses.
    """
    out = []
    for d in metrics.list_runs(root):
        samples, events, meta = metrics.load_run(d)
        if exps and meta["exp"] not in exps:
            continue
        summary = metrics.compute(samples, events, meta["requested_hz"])
        if summary.get("session_losses") and not interrupted:
            continue
        out.append((meta, samples, summary))
    return out


def condition(meta):
    c = meta["conditions"]
    return {"in_ear": c.get("in_ear", ""), "audio": c.get("audio", ""), "distance": c.get("distance", ""),
            "obstruction": c.get("obstruction", "")}


def save(fig, out_dir, name, header, rows):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_dir / (name + ".pdf"))
    fig.savefig(out_dir / (name + ".png"), dpi=200)        # for looking at; the paper uses the PDF
    plt.close(fig)
    with open(out_dir / (name + ".csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    print("%s: %d rows -> %s.pdf, %s.csv" % (name, len(rows), out_dir / name, out_dir / name))


def no_runs(name):
    print("%s: no valid runs for it yet, nothing written" % name)
    return 0
