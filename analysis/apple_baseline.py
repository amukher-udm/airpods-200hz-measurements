"""E8 baseline: what Apple's CMHeadphoneMotionManager delivered on the same Mac and AirPods.

Reads runs/apple-api/official-control-*.jsonl (one JSON object per line, written by a
test app: kind official_sample with sensor_timestamp_s and host_uptime_s). Writes one row per
recording and an "all" row to <out>/tab_apple_baseline.csv.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np

import common


def load(path):
    t, h = [], []
    for line in open(path):
        x = json.loads(line)
        if x.get("kind") == "official_sample":
            t.append(x["sensor_timestamp_s"])
            h.append(x["host_uptime_s"])
    return np.array(t), np.array(h)


def recordings(root):
    """[(name, sensor_s, host_s)] of each Apple-API recording with samples."""
    out = []
    for p in sorted((Path(root) / "apple-api").glob("official-control-*.jsonl")):
        t, h = load(p)
        if len(t) >= 3:
            out.append((p.stem.replace("official-control-", "")[:8], t, h))
    return out


def row(name, t, h):
    dt, dh = np.diff(t) * 1e3, np.diff(h) * 1e3
    period = float(np.median(dt))
    k = np.rint(dt / period)
    return [name, len(t), "%.2f" % (t[-1] - t[0]), "%.3f" % ((len(t) - 1) / (t[-1] - t[0])),
            "%.3f" % ((len(h) - 1) / (h[-1] - h[0])), "%.3f" % period, "%.3f" % dt.max(),
            int(np.sum(k[k > 1] - 1)), int(np.sum(dt <= 0)),
            "%.2f" % np.percentile(dh, 50), "%.2f" % np.percentile(dh, 99), "%.2f" % dh.max()]


def rate(root):
    """Samples per second over all recordings (sensor clock), or None when there are none."""
    recs = recordings(root)
    if not recs:
        return None
    return sum(len(t) - 1 for _, t, _ in recs) / sum(t[-1] - t[0] for _, t, _ in recs)


def main():
    a = common.args(__doc__)
    recs = recordings(a.runs)
    if not recs:
        return common.no_runs("tab_apple_baseline")
    rows = [row(*r) for r in recs]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "tab_apple_baseline.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["recording", "samples", "duration_s", "rate_sensor_hz", "rate_host_hz", "period_ms",
                    "longest_step_sensor_ms", "missing", "non_increasing", "host_interarrival_median_ms",
                    "host_interarrival_p99_ms", "host_interarrival_max_ms"])
        w.writerows(rows)
        w.writerow(["all (pooled rate)", sum(r[1] for r in rows), "", "%.3f" % rate(a.runs)] + [""] * 8)
    print("tab_apple_baseline: %d recordings -> %s" % (len(rows), out / "tab_apple_baseline.csv"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
