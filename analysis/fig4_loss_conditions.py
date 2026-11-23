"""Figure 4: loss and longest gap by audio state and by distance.

Two panels, one measure each, sharing the condition axis; one dot per run and a bar at the
median. Default experiments: E3 (audio; worn, 60 s runs) and E4 (distance and obstruction; not
worn, 30 s runs), at 200 Hz. The two groups are drawn apart because their run lengths differ.
"""
from __future__ import annotations

import statistics

import matplotlib.pyplot as plt

import common


AUDIO = {"none": "No audio", "stream": "Audio playing"}
HEAD_AUDIO, HEAD_PLACE = "Worn, 60 s runs", "Not worn, 30 s runs"


def label(meta):
    """(group heading, row label, sort key)."""
    c = common.condition(meta)
    if meta["exp"] == "E3":
        return HEAD_AUDIO, AUDIO.get(c["audio"], c["audio"]), (0, 0 if c["audio"] == "none" else 1)
    metres = c["distance"].replace("m", " m")
    blocked = c["obstruction"] not in ("", "none")
    what = "" if not blocked else ", " + ("wall" if "wall" in c["obstruction"] else c["obstruction"])
    return HEAD_PLACE, metres + what, (1, 1 if blocked else 0, float(c["distance"].rstrip("m") or 0))


def main():
    a = common.args(__doc__)
    found = [r for r in common.runs(a.runs, (a.exp or "E3,E4").split(",")) if r[0]["requested_hz"] == 200]
    if not found:
        return common.no_runs("fig4_loss_conditions")
    common.style()
    groups, order = {}, {}
    for meta, _, s in found:
        head, name, key = label(meta)
        groups.setdefault((head, name), []).append((meta, s))
        order[(head, name)] = key
    rows_y, y, last = [], 0, None                      # (y, heading or None, group key or None)
    for k in sorted(groups, key=order.get):
        if k[0] != last:
            y += 0.4 if last is not None else 0
            rows_y.append((y, k[0], None))
            y, last = y + 1, k[0]
        rows_y.append((y, None, k))
        y += 1
    fig, axes = plt.subplots(1, 2, figsize=(common.COLUMN_IN, 0.75 + 0.2 * len(rows_y)), sharey=True)
    measures = [("loss_pct", "Loss (%)"), ("longest_gap_sensor_ms", "Longest gap (ms)")]
    rows = []
    for ax, (key, title) in zip(axes, measures):
        for y, head, k in rows_y:
            if k is None:
                continue
            values = [s[key] for _, s in groups[k]]
            med = statistics.median(values)
            ax.barh(y, med, height=0.5, color=common.BLUE, alpha=0.28, zorder=2)
            ax.scatter(values, [y] * len(values), s=16, color=common.BLUE, edgecolor="white", linewidth=0.7, zorder=3)
            ax.annotate(("%.2f" if key == "loss_pct" else "%.0f") % med, xy=(max(values), y), xytext=(4, 0),
                        textcoords="offset points", va="center", fontsize=6.5, color=common.INK)
        ax.set_xlabel(title)
        ax.set_xlim(0, max(max(s[key] for _, s in g) for g in groups.values()) * 1.3 or 1)
        ax.grid(axis="y", visible=False)
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    fig.legend(handles=[Line2D([], [], linestyle="", marker="o", markersize=4, color=common.BLUE, markeredgecolor="white", label="Run"),
                        Patch(color=common.BLUE, alpha=0.28, label="Median of the runs (value shown)")],
               loc="lower center", ncol=2, bbox_to_anchor=(0.5, 0.98), handletextpad=0.4, columnspacing=1.2)
    axes[0].set_yticks([y for y, _, _ in rows_y])
    axes[0].set_yticklabels([head if k is None else k[1] for _, head, k in rows_y])
    for tick, (_, head, k) in zip(axes[0].get_yticklabels(), rows_y):
        if k is None:
            tick.set_fontweight("bold")
            tick.set_color(common.INK)
    axes[0].tick_params(axis="y", length=0)
    axes[0].set_ylim(rows_y[-1][0] + 0.6, -0.6)
    for _, head, k in rows_y:
        if k is None:
            continue
        for meta, s in groups[k]:
            rows.append([head, k[1], meta["run_id"], meta["exp"], meta["repeat"], "%.4f" % s["loss_pct"], s["missing"],
                         "%.1f" % s["longest_gap_sensor_ms"]])
    common.save(fig, a.out, "fig4_loss_conditions",
                ["group", "condition", "run_id", "exp", "repeat", "loss_pct", "missing", "longest_gap_sensor_ms"], rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
