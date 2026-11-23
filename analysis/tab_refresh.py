"""How often each field of the report carries a new value at 200 Hz.

For every valid 200 Hz run: among consecutive delivered samples whose counter differs by one, the
share whose acceleration bytes (offset 38-43), rotation-rate bytes (26-31) and quaternion bytes
(20-25) equal the previous sample's, split by the parity of the report counter.
The paper uses the recordings longer than 25 s (over 5000 such pairs) that kept their session;
the column in_paper marks them, and the ranges over them are printed.
Writes <out>/tab_refresh.csv.
"""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

import common
from podmeasure import metrics

FIELDS = {"accel": (38, 44), "gyro": (26, 32), "quat": (20, 26)}
MIN_PAIRS = 5000        # 25 s at 200 Hz


def main():
    a = common.args(__doc__)
    rows = []
    for d in metrics.list_runs(a.runs):
        import json
        meta = json.loads((d / "meta.json").read_text())
        if meta["requested_hz"] != 200 or (a.exp and meta["exp"] not in a.exp.split(",")):
            continue
        raw = []
        with open(d / "samples.csv", newline="") as f:
            for r in csv.DictReader(f):
                raw.append(bytes.fromhex(r["raw_hex"]))
        if len(raw) < 100:
            continue
        cnt = np.array([int.from_bytes(b[54:56], "little") for b in raw])
        step1 = (np.diff(cnt) % 65536) == 1
        odd = (cnt[1:] % 2 == 1) & step1
        even = (cnt[1:] % 2 == 0) & step1
        with open(d / "events.csv", newline="") as f:
            lost = any(e["type"] == "session_lost" for e in csv.DictReader(f))
        row = [meta["run_id"], meta["exp"], meta["conditions"].get("in_ear", ""), int(step1.sum())]
        for name, (lo, hi) in FIELDS.items():
            same = np.array([raw[i][lo:hi] == raw[i - 1][lo:hi] for i in range(1, len(raw))])
            row += ["%.1f" % (100 * same[odd].mean()), "%.1f" % (100 * same[even].mean())]
        rows.append(row + ["yes" if lost else "no", "yes" if int(step1.sum()) > MIN_PAIRS and not lost else "no"])
    if not rows:
        return common.no_runs("tab_refresh")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "tab_refresh.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["run_id", "exp", "in_ear", "pairs", "accel_same_odd_pct", "accel_same_even_pct", "gyro_same_odd_pct",
                    "gyro_same_even_pct", "quat_same_odd_pct", "quat_same_even_pct", "session_lost", "in_paper"])
        w.writerows(rows)
    print("tab_refresh: %d runs -> %s" % (len(rows), out / "tab_refresh.csv"))
    for worn, name in (("no", "not worn"), ("both", "worn")):
        g = [(max(float(r[4]), float(r[5])), min(float(r[4]), float(r[5]))) for r in rows if r[2] == worn and r[-1] == "yes"]
        if g:
            print("  %s, %d recordings: acceleration repeats on one parity %.1f-%.1f %%, on the other %.1f-%.1f %%" % (
                name, len(g), min(x for x, _ in g), max(x for x, _ in g), min(y for _, y in g), max(y for _, y in g)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
