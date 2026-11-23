"""Loss against time since the first sample, at 200 Hz.

Each run is cut into 5 s windows counted from its first sample (the first 60 s only, so the
30-minute run contributes its first minute). The loss in a window is the samples missing in it
over the samples expected in it, with a hole counted in the window where it starts. Thin lines
are single runs; thick lines are the mean over the runs that cover the window, drawn separately
for runs without audio and with audio. Default experiments: E1 to E5; runs that lost their
session are left out, as everywhere else.
"""
from __future__ import annotations

import numpy as np
import matplotlib.pyplot as plt

import common

WINDOW, SPAN = 5.0, 60.0
GROUPS = [("none", "No audio", common.BLUE), ("stream", "Audio playing", common.ORANGE)]


def windows(samples):
    """[(start s, missing, expected)] for the 5 s windows the run covers in full."""
    t = np.array(samples["sensor_ns"], dtype=np.int64)
    t = (t - t[0]) / 1e9
    step = np.diff(t)
    period = float(np.median(step))
    k = np.rint(step / period).astype(np.int64)
    holes = k > 1
    hole_t, hole_n = t[:-1][holes], k[holes] - 1
    out = []
    for a in np.arange(0.0, SPAN, WINDOW):
        if t[-1] < a + WINDOW - 0.5:
            break
        got = int(np.sum((t >= a) & (t < a + WINDOW)))
        missing = int(np.sum(hole_n[(hole_t >= a) & (hole_t < a + WINDOW)]))
        out.append((a, missing, got + missing))
    return out


def main():
    a = common.args(__doc__)
    found = [r for r in common.runs(a.runs, (a.exp or "E1,E2,E3,E4,E5").split(",")) if r[0]["requested_hz"] == 200]
    if not found:
        return common.no_runs("fig_loss_time")
    common.style()
    fig, ax = plt.subplots(figsize=(common.COLUMN_IN, 1.9))
    rows = []
    for audio, name, colour in GROUPS:
        per_run = []
        for meta, samples, _ in found:
            state = common.condition(meta)["audio"] or "none"
            if (state == "stream") != (audio == "stream"):
                continue
            w = windows(samples)
            per_run.append(w)
            ax.plot([s + WINDOW / 2 for s, _, _ in w], [100.0 * m / e for _, m, e in w], color=colour, alpha=0.22,
                    linewidth=0.6, zorder=2)
            rows += [[meta["run_id"], meta["exp"], state, "%g" % s, "%g" % (s + WINDOW), m, e, "%.3f" % (100.0 * m / e)]
                     for s, m, e in w]
        if not per_run:
            continue
        starts = sorted({s for w in per_run for s, _, _ in w})
        mean = [float(np.mean([100.0 * m / e for w in per_run for s, m, e in w if s == x])) for x in starts]
        ax.plot([s + WINDOW / 2 for s in starts], mean, color=colour, marker="o", markersize=3, zorder=3,
                label="%s (%d runs)" % (name, len(per_run)))
        rows += [["mean", "", audio, "%g" % s, "%g" % (s + WINDOW), "", "", "%.3f" % v] for s, v in zip(starts, mean)]
    ax.set_xlabel("Time since the first sample (s)")
    ax.set_ylabel("Loss (%)")
    ax.set_xlim(0, SPAN)
    ax.set_xticks(np.arange(0, SPAN + 1, 10))
    ax.set_ylim(0, None)
    ax.legend(loc="upper right")
    common.save(fig, a.out, "fig_loss_time",
                ["run_id", "exp", "audio", "window_start_s", "window_end_s", "missing", "expected", "loss_pct"], rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
