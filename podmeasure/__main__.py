"""python -m podmeasure record|summary ..."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import metrics


def main(argv=None):
    ap = argparse.ArgumentParser(prog="podmeasure", description="Record and summarise AirPods motion-stream runs.")
    sub = ap.add_subparsers(dest="command", required=True)
    r = sub.add_parser("record", help="record one run to runs/<run-id>/")
    r.add_argument("--exp", required=True, help="experiment id, e.g. E1")
    r.add_argument("--rate", type=float, default=200, help="samples per second to ask for (default 200)")
    r.add_argument("--seconds", type=float, default=60, help="run length from the first sample (default 60)")
    r.add_argument("--in-ear", required=True, choices=["no", "one", "both"], help="how the earbuds are worn")
    r.add_argument("--audio", required=True, help="none, music or call")
    r.add_argument("--distance", required=True, help="e.g. 1m")
    r.add_argument("--obstruction", default="none", help="none, wall, body ...")
    r.add_argument("--repeat", type=int, default=1)
    r.add_argument("--notes", default="")
    r.add_argument("--root", default="runs")
    r.add_argument("--address", help="Bluetooth address when several AirPods are connected")
    s = sub.add_parser("summary", help="recompute the summary of a recorded run from its samples, save and print it")
    s.add_argument("run_dir")
    args = ap.parse_args(argv)

    if args.command == "record":
        from . import recorder
        run_dir = recorder.record(args.exp, args.rate, args.seconds,
                                  {"in_ear": args.in_ear, "audio": args.audio, "distance": args.distance,
                                   "obstruction": args.obstruction},
                                  repeat=args.repeat, notes=args.notes, root=args.root, address=args.address)
        status = json.loads((run_dir / "meta.json").read_text())["status"]
        print("\nFiles: %s" % run_dir)
        return 0 if status == "ok" else 2
    from . import recorder
    samples, events, meta = metrics.load_run(args.run_dir)
    summary = metrics.compute(samples, events, meta["requested_hz"])
    old = json.loads((Path(args.run_dir) / "summary.json").read_text())
    summary.update({k: old.get(k) for k in ("run_id", "status", "error", "time_to_session_open_s", "time_to_first_sample_s")})
    (Path(args.run_dir) / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(recorder.report(summary, meta))
    return 0


if __name__ == "__main__":
    sys.exit(main())
