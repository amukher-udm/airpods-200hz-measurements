"""Figure 3: how far apart samples reach the host, by rate asked for.

Cumulative share of samples against the time since the previous sample arrived (host clock,
log axis). All runs at one rate are pooled. Default experiments: E1.
"""
from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np

import common


def main():
    a = common.args(__doc__)
    found = common.runs(a.runs, (a.exp or "E1").split(","))
    if not found:
        return common.no_runs("fig3_interarrival")
    common.style()
    pooled = {}
    for meta, samples, _ in found:
        gaps = np.diff(np.asarray(samples["host_time"])) * 1e3
        pooled.setdefault(meta["requested_hz"], []).append(gaps)
    rates = sorted(pooled)
    colors = common.RATE_RAMP[-len(rates):] if len(rates) <= 4 else common.RATE_RAMP
    fig, ax = plt.subplots(figsize=(common.COLUMN_IN, 2.3))
    rows = []
    floor = 0.05                                    # ms: samples of one callback share a time; drawn at the floor
    for rate, color in zip(rates, colors):
        gaps = np.sort(np.maximum(np.concatenate(pooled[rate]), floor))
        share = np.arange(1, len(gaps) + 1) / len(gaps) * 100
        ax.step(gaps, share, where="post", color=color, label="%g Hz" % rate)
        ax.axvline(1000.0 / rate, color=color, linewidth=0.6, linestyle=(0, (1, 2)), zorder=1)
        for q in (1, 5, 10, 25, 50, 75, 90, 95, 99, 99.9, 100):
            rows.append(["%g" % rate, q, "%.3f" % float(np.percentile(gaps, q)), len(gaps)])
    ax.set_xscale("log")
    ax.set_xlim(floor, max(300, ax.get_xlim()[1]))
    ax.set_ylim(0, 100)
    ax.set_xlabel("Time since previous sample arrived (ms)")
    ax.set_ylabel("Samples (%)")
    ax.legend(title="Requested", loc="upper left", title_fontsize=7, handlelength=1.4)
    ax.annotate("dotted: sample period", xy=(0.98, 0.04), xycoords="axes fraction", ha="right", fontsize=6.5,
                color=common.INK2)
    common.save(fig, a.out, "fig3_interarrival", ["requested_hz", "percentile", "interarrival_ms", "samples"], rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
