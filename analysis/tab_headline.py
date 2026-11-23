"""Table of headline numbers: one row per run. Writes figures/tab_headline.csv and .tex.

Default experiments: E1 to E5.
"""
from __future__ import annotations

import csv
from pathlib import Path

import common

COLUMNS = ["run_id", "exp", "repeat", "requested_hz", "in_ear", "audio", "distance", "obstruction",
           "samples", "expected", "missing", "loss_pct", "counter_missing", "holes", "rate_sensor_hz", "rate_host_hz",
           "longest_gap_sensor_ms", "interarrival_p99_ms", "interarrival_max_ms", "delay_variation_p99_ms",
           "burst_max", "burst_share_single_pct", "duplicates", "out_of_order",
           "reconnect_attempts", "session_losses", "stream_restarts"]


def row(meta, s):
    c = common.condition(meta)
    return [meta["run_id"], meta["exp"], meta["repeat"], "%g" % meta["requested_hz"], c["in_ear"], c["audio"],
            c["distance"], c["obstruction"], s["samples"], s["expected"], s["missing"], "%.3f" % s["loss_pct"],
            s.get("counter_missing", ""), s["holes"], "%.2f" % s["rate_sensor_hz"], "%.2f" % s["rate_host_hz"],
            "%.1f" % s["longest_gap_sensor_ms"], "%.1f" % s["interarrival_ms"]["p99"],
            "%.1f" % s["interarrival_ms"]["max"], "%.1f" % s["delay_variation_ms"]["p99"], s["burst_size"]["max"],
            "%.1f" % s["burst_size"]["share_single_pct"], s["duplicates"], s["out_of_order"],
            s["reconnect_attempts"], s["session_losses"], s["stream_restarts"]]


def main():
    a = common.args(__doc__)
    found = common.runs(a.runs, (a.exp or "E1,E2,E3,E4,E5").split(","), interrupted=True)
    if not found:
        return common.no_runs("tab_headline")
    rows = [row(meta, s) for meta, _, s in found]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "tab_headline.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        w.writerows(rows)
    pick = ["exp", "requested_hz", "in_ear", "audio", "distance", "rate_sensor_hz", "loss_pct",
            "longest_gap_sensor_ms", "interarrival_p99_ms", "burst_max"]
    head = ["Exp.", "Asked (Hz)", "In ear", "Audio", "Dist.", "Rate (Hz)", "Loss (\\%)", "Longest gap (ms)",
            "Arrival p99 (ms)", "Largest burst"]
    idx = [COLUMNS.index(c) for c in pick]
    lines = ["\\begin{tabular}{" + "l" * 5 + "r" * 5 + "}", "\\hline", " & ".join(head) + " \\\\", "\\hline"]
    lines += [" & ".join(str(r[i]) for i in idx) + " \\\\" for r in rows]
    lines += ["\\hline", "\\end{tabular}"]
    (out / "tab_headline.tex").write_text("\n".join(lines) + "\n")
    print("tab_headline: %d runs -> %s, %s" % (len(rows), out / "tab_headline.csv", out / "tab_headline.tex"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
