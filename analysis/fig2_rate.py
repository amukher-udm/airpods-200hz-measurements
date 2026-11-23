"""Figure 2: the rate that arrives for each rate asked for (sensor clock), one dot per run.

Default experiments: E1. The Apple API baseline (E8, runs/apple-api/) is drawn as a reference
line when its recordings exist.
"""
from __future__ import annotations

import matplotlib.pyplot as plt

import apple_baseline
import common


def main():
    a = common.args(__doc__)
    found = common.runs(a.runs, (a.exp or "E1").split(","))
    if not found:
        return common.no_runs("fig2_rate")
    common.style()
    rows = [(meta["run_id"], meta["exp"], meta["repeat"], meta["requested_hz"], s["rate_sensor_hz"], s["rate_host_hz"],
             100 * s["rate_sensor_hz"] / meta["requested_hz"]) for meta, _, s in found]
    asked = sorted({r[3] for r in rows})
    fig, ax = plt.subplots(figsize=(common.COLUMN_IN, 2.3))
    top = max(asked) * 1.14
    ax.plot([0, top], [0, top], color=common.MUTED, linewidth=0.8, linestyle=(0, (4, 3)), zorder=1)
    ax.annotate("asked = delivered", xy=(top * 0.62, top * 0.62), xytext=(-4, 6), textcoords="offset points",
                ha="right", fontsize=7, color=common.INK2)
    apple = apple_baseline.rate(a.runs)
    if apple:
        ax.plot([0, top], [apple, apple], color=common.ORANGE, linewidth=1.0, zorder=2)
        ax.annotate("Apple's API: %.1f Hz" % apple, xy=(top, apple), xytext=(-2, 3), textcoords="offset points",
                    ha="right", fontsize=7, color=common.INK2)
    ax.scatter([r[3] for r in rows], [r[4] for r in rows], s=22, color=common.BLUE, edgecolor="white",
               linewidth=0.8, zorder=3)
    for rate in asked:
        got = [r[4] for r in rows if r[3] == rate]
        ax.annotate("%.1f" % (sum(got) / len(got)), xy=(rate, max(got)), xytext=(0, 5), textcoords="offset points",
                    ha="center", fontsize=7, color=common.INK)
    ax.set_xticks(asked)
    ax.set_xlim(0, top)
    ax.set_ylim(0, top)
    ax.set_xlabel("Requested rate (Hz)")
    ax.set_ylabel("Delivered rate (Hz)")
    common.save(fig, a.out, "fig2_rate",
                ["run_id", "exp", "repeat", "requested_hz", "rate_sensor_hz", "rate_host_hz", "delivered_pct"],
                [[r[0], r[1], r[2], "%g" % r[3], "%.3f" % r[4], "%.3f" % r[5], "%.2f" % r[6]] for r in rows])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
