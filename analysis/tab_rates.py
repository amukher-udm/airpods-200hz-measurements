"""Table I: the rate sweep, one row per requested rate, not worn (E1) and worn (E2).

Each cell is the mean over the runs of that rate that kept their session, taken from the
unrounded per-run metrics; the largest group is the maximum over those runs. The arrival
percentile is the 99th percentile of the host spacing between consecutive samples in each run.
Writes <out>/tab_rates.csv.
"""
from __future__ import annotations

import csv
import statistics
from pathlib import Path

import common


def main():
    a = common.args(__doc__)
    found = common.runs(a.runs, (a.exp or "E1,E2").split(","))
    if not found:
        return common.no_runs("tab_rates")
    rows = []
    for exp, worn in (("E1", "not worn"), ("E2", "worn")):
        for hz in sorted({m["requested_hz"] for m, _, _ in found if m["exp"] == exp}):
            g = [s for m, _, s in found if m["exp"] == exp and m["requested_hz"] == hz]
            rows.append([worn, "%g" % hz, len(g), "%.2f" % statistics.mean(s["rate_sensor_hz"] for s in g),
                         "%.2f" % statistics.mean(s["loss_pct"] for s in g),
                         "%.1f" % statistics.mean(s["interarrival_ms"]["p99"] for s in g),
                         max(s["burst_size"]["max"] for s in g)])
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "tab_rates.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["earbuds", "requested_hz", "runs", "delivered_hz_mean", "loss_pct_mean", "arrival_p99_ms_mean",
                    "largest_same_time_group"])
        w.writerows(rows)
    print("tab_rates: %d rows -> %s" % (len(rows), out / "tab_rates.csv"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
