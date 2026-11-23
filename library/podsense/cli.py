# SPDX-License-Identifier: Apache-2.0
"""podsense: look at, measure and record the motion data of the connected AirPods.

    podsense devices                      the AirPods connected to this computer
    podsense live                         live values and the sample rate, until Ctrl-C
    podsense rate --rates 50,100,200      what rate really arrives at each setting
    podsense record walk.csv --seconds 30 samples to a CSV file
"""
from __future__ import annotations

import argparse
import csv
import logging
import statistics
import sys
import time

from . import devices
from .errors import PodsenseError
from .protocol import Placement, Sample, StemPress
from .session import Session

CSV_COLUMNS = ["host_time", "timestamp", "gyro_x", "gyro_y", "gyro_z", "accel_x", "accel_y", "accel_z",
               "gravity_x", "gravity_y", "gravity_z", "quat_w", "quat_x", "quat_y", "quat_z"]


def summarize(samples):
    """Figures of a run of Samples: rates by both clocks, interval, gaps."""
    out = {"samples": len(samples)}
    if len(samples) < 3:
        return out
    host = samples[-1].host_time - samples[0].host_time
    device = samples[-1].timestamp - samples[0].timestamp
    steps = [b.timestamp - a.timestamp for a, b in zip(samples, samples[1:])]
    median = statistics.median(steps)
    out.update(hz_host=(len(samples) - 1) / host if host > 0 else 0.0,
               hz_device=(len(samples) - 1) / device if device > 0 else 0.0,
               median_ms=median * 1e3, longest_ms=max(steps) * 1e3,
               gaps=sum(1 for s in steps if median > 0 and s > 2.5 * median),   # samples lost or late
               out_of_order=sum(1 for s in steps if s <= 0))
    return out


def csv_row(sample):
    q = sample.quaternion or ("", "", "", "")
    return ["%.6f" % sample.host_time, "%.6f" % sample.timestamp,
            *("%.3f" % v for v in sample.gyro), *("%.4f" % v for v in sample.acceleration),
            *("%.4f" % v for v in sample.gravity), *(v if v == "" else "%.5f" % v for v in q)]


def _vec(values, width, digits):
    return " ".join("%+*.*f" % (width, digits, v) for v in values)


def cmd_devices(args):
    found = devices.connected()
    if not found:
        print("No AirPods connected to this computer.")
        return 1
    for d in found:
        print("%s  %s  %s" % (d.address, d.name, d.model))
    return 0


def cmd_live(args, session):
    recent, shown, first = [], 0.0, None
    for event in session.events():
        if isinstance(event, StemPress):
            print("\rstem press: %-70s" % event.kind)
        elif isinstance(event, Placement):
            print("\rearbuds: %-73s" % ", ".join(event.buds))
        if not isinstance(event, Sample):
            continue
        now = event.host_time
        first = first or now
        if args.seconds and now - first >= args.seconds:
            break
        recent.append(now)
        if now - shown < 0.1:
            continue
        shown = now
        recent = [t for t in recent if t > now - 1.0]
        q = "quat " + _vec(event.quaternion, 6, 3) if event.quaternion else "quat (not yet)"
        print("\r%3d Hz  gyro %s deg/s  gravity %s g  %s " % (
            len(recent), _vec(event.gyro, 7, 1), _vec(event.gravity, 5, 2), q), end="", flush=True)
    print()
    return 0


def collect(session, seconds, settle=1.0, wait=30.0):
    """Samples of `seconds` of streaming, leaving out the first `settle` seconds after a rate change."""
    out, start, first = [], time.monotonic(), None
    for event in session.events():
        now = time.monotonic()
        if isinstance(event, Sample) and now - start >= settle:
            first = first or now
            out.append(event)
        if first and now - first >= seconds:
            break
        if not first and now - start > wait:
            break
    return out


def cmd_rate(args, session):
    ok = False
    for rate in args.rates:
        session.set_rate(rate)
        s = summarize(collect(session, args.seconds))
        print("asked %g Hz" % rate)
        if "hz_host" not in s:
            print("  only %d samples: no rate" % s["samples"])
            continue
        ok = True
        print("  rate            %.1f Hz by this computer's clock, %.1f Hz by the AirPods' timestamps"
              % (s["hz_host"], s["hz_device"]))
        print("  samples         %d" % s["samples"])
        print("  interval        median %.2f ms, longest %.1f ms, gaps %d, out of order %d"
              % (s["median_ms"], s["longest_ms"], s["gaps"], s["out_of_order"]))
    return 0 if ok else 2


def cmd_record(args, session):
    kept = []
    with open(args.file, "w", newline="") as f:
        out = csv.writer(f)
        out.writerow(CSV_COLUMNS)
        print("Recording to %s, Ctrl-C to stop" % args.file)
        try:
            for sample in session.samples():
                out.writerow(csv_row(sample))
                kept.append(sample)
                if args.seconds and sample.host_time - kept[0].host_time >= args.seconds:
                    break
        except KeyboardInterrupt:
            pass
    s = summarize(kept)
    if "hz_device" in s:
        print("%d samples, %.1f Hz by the AirPods' timestamps, %d gaps" % (s["samples"], s["hz_device"], s["gaps"]))
    else:
        print("%d samples" % s["samples"])
    return 0 if kept else 2


def rates(text):
    return [float(x) for x in text.split(",") if x]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="podsense", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-q", "--quiet", action="store_true", help="no progress messages")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("devices", help="list the connected AirPods")

    def session_command(name, text):
        p = sub.add_parser(name, help=text)
        p.add_argument("--address", help="Bluetooth address, when several AirPods are connected")
        p.add_argument("--seconds", type=float, default=0, help="stop after this long")
        return p

    p = session_command("live", "live values and sample rate")
    p.add_argument("--rate", type=float, default=200, help="samples per second (default 200)")
    p.add_argument("--stem", action="store_true", help="also show squeezes of the stem")
    p = session_command("rate", "measure the rate that arrives")
    p.add_argument("--rates", type=rates, default=[50, 100, 200], help="rates to ask for (default 50,100,200)")
    p.set_defaults(seconds=10.0)
    p = session_command("record", "write samples to a CSV file")
    p.add_argument("file")
    p.add_argument("--rate", type=float, default=200, help="samples per second (default 200)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING if args.quiet else logging.INFO,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    try:
        if args.command == "devices":
            return cmd_devices(args)
        rate = args.rates[0] if args.command == "rate" else args.rate
        with Session(rate_hz=rate, address=args.address, stem_events=getattr(args, "stem", False)) as session:
            return {"live": cmd_live, "rate": cmd_rate, "record": cmd_record}[args.command](args, session)
    except KeyboardInterrupt:
        print()
        return 0
    except (PodsenseError, ValueError) as e:
        print("podsense: %s" % e, file=sys.stderr)
        return 1
