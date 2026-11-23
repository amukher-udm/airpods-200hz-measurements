"""Record one run of the AirPods motion stream to a run directory.

    runs/<run-id>/samples.csv    one row per sample, both clocks, the raw report
    runs/<run-id>/events.csv     session opens, reconnects, losses, restarts, placement changes
    runs/<run-id>/meta.json      what was measured, on what, under which conditions
    runs/<run-id>/summary.json   the metrics of the run (podmeasure.metrics.compute)

podsense is used as it is. RecSession overrides one internal method of podsense.Session, _read,
to keep the raw report and the message each sample came in; session events are taken from
podsense's own log messages.
"""
from __future__ import annotations

import csv
import datetime
import json
import logging
import platform
import struct
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import podsense
from podsense import protocol
from podsense.session import Session

from . import metrics

LOG_EVENTS = (("Opening a session", "open_start"), ("Reconnecting the AirPods", "reconnect_attempt"),
              ("Session open", "session_open"), ("Session lost", "session_lost"),
              ("No samples for", "stream_restart"))


@dataclass(frozen=True, slots=True)
class RecSample(protocol.Sample):
    """A podsense Sample with what the recorder adds."""
    sensor_ns: int = 0           # the report's timestamp, untouched
    recv_time: float = 0.0       # time.monotonic() when the recorder read the message
    msg_idx: int = 0             # running index of the message that carried it
    n_in_msg: int = 1            # samples in that message
    raw: bytes = b""             # the whole report


class RunOver(Exception):
    """The run length has passed, or nothing arrived in time."""


class RecSession(Session):
    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.msg_idx = 0
        self.first_sample_host = None    # host time of the first sample of all, settle period included
        self.stop_at = None              # monotonic time after which _read ends the run
        self.give_up_at = None           # ... when no sample has come at all

    def _read(self, timeout):
        now = time.monotonic()
        if self.stop_at is not None and now > self.stop_at:
            raise RunOver("run length reached")
        if self.stop_at is None and self.give_up_at is not None and now > self.give_up_at:
            raise RunOver("no sample arrived")
        item = self._link.read(max(0.0, timeout))
        if item is None:
            return []
        recv = time.monotonic()
        host_time, message = item
        found = []
        if message[:10] == protocol.SENSOR_DATA:
            blocks = protocol.motion_blocks(message)
            if blocks:
                self.msg_idx += 1
            for block in blocks:
                s = protocol.decode(block, host_time)
                found.append(RecSample(s.timestamp, s.host_time, s.gyro, s.acceleration, s.gravity, s.quaternion,
                                       sensor_ns=struct.unpack_from("<Q", block, 1)[0], recv_time=recv,
                                       msg_idx=self.msg_idx, n_in_msg=len(blocks), raw=bytes(block)))
        else:
            found = protocol.events(message, host_time)
        for event in found:
            if isinstance(event, protocol.Sample):
                self._last_sample = time.monotonic()
                if self.first_sample_host is None:
                    self.first_sample_host = event.host_time
            elif isinstance(event, protocol.Placement):
                self.placement = event
        return found


class _LogToEvents(logging.Handler):
    """podsense's progress messages as timed events."""

    def __init__(self, sink):
        super().__init__(level=logging.INFO)
        self.sink = sink

    def emit(self, record):
        text = record.getMessage()
        kind = next((k for prefix, k in LOG_EVENTS if text.startswith(prefix)), "log")
        self.sink(time.monotonic(), kind, text)


# --------------------------------------------------------------------------- what the run was made on
def _run(cmd, timeout=30):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def bluetooth_snapshot(product_id=None):
    """Controller and AirPods facts from macOS, without addresses or serial numbers."""
    if sys.platform != "darwin":
        return {}
    try:
        data = json.loads(_run(["system_profiler", "SPBluetoothDataType", "-json"]))["SPBluetoothDataType"][0]
    except (ValueError, KeyError, IndexError):
        return {}
    keep = lambda d: {k: v for k, v in d.items() if "ddress" not in k and "erial" not in k}
    out = {"controller": keep(data.get("controller_properties", {})), "airpods": None}
    for entry in data.get("device_connected", []):
        for _name, info in entry.items():
            if "device_productID" in info and (product_id is None or
                                               info["device_productID"].lower() == "0x" + product_id.lower()):
                out["airpods"] = keep(info)
    return out


def environment():
    root = Path(podsense.__file__).resolve().parents[2]
    env = {"python": platform.python_version(), "platform": platform.platform(),
           "podsense_commit": _run(["git", "-C", str(root), "rev-parse", "HEAD"]),
           "podsense_dirty": bool(_run(["git", "-C", str(root), "status", "--porcelain"]))}
    if sys.platform == "darwin":
        env.update(host_model=_run(["sysctl", "-n", "hw.model"]), host_chip=_run(["sysctl", "-n", "machdep.cpu.brand_string"]),
                   os="macOS %s (%s)" % (platform.mac_ver()[0], _run(["sw_vers", "-buildVersion"])))
        try:
            import objc
            env["pyobjc"] = objc.__version__
        except ImportError:
            pass
    return env


# --------------------------------------------------------------------------- one run
def record(exp, rate_hz, seconds, conditions, repeat=1, notes="", root="runs", wait=90.0,
           address=None, session=None, quiet=False):
    """Record `seconds` of streaming, counted from the first sample kept. Returns the run directory.

    conditions  {"in_ear": ..., "audio": ..., "distance": ..., "obstruction": ...} as the operator set them
    wait        seconds to wait for the first sample before the run is written off as failed
    session     a RecSession to use instead of opening the connected AirPods (tests)
    """
    started = datetime.datetime.now(datetime.timezone.utc)
    run_id = "%s_%s_%ghz_r%d" % (started.astimezone().strftime("%Y%m%d-%H%M%S"), exp, rate_hz, repeat)
    run_dir = Path(root) / run_id
    run_dir.mkdir(parents=True)
    say = (lambda *a: None) if quiet else (lambda *a: print(*a, flush=True))

    events = []
    add_event = lambda at, kind, detail: events.append({"host_time": "%.6f" % at, "type": kind, "detail": detail})
    handler = _LogToEvents(add_event)
    logger = logging.getLogger("podsense")
    logger.addHandler(handler)
    old_level = logger.level
    logger.setLevel(logging.INFO)

    session = session or RecSession(rate_hz=rate_hz, address=address)
    t_start = time.monotonic()
    meta = {"run_id": run_id, "exp": exp, "repeat": repeat, "requested_hz": rate_hz, "requested_seconds": seconds,
            "started_utc": started.isoformat(timespec="seconds"), "monotonic_at_start": t_start,
            "conditions": conditions, "notes": notes, "status": "failed", "error": None,
            "environment": environment(), "recorder": "podmeasure 1"}
    add_event(t_start, "run_start", run_id)
    samples = {c: [] for c in metrics.SAMPLE_COLUMNS}
    first = t_open = None
    n = 0
    say("Run %s: %g Hz for %g s. Opening the session (sound stops for 10 to 25 s on macOS)..." % (run_id, rate_hz, seconds))
    try:
        with open(run_dir / "samples.csv", "w", newline="") as f:
            out = csv.writer(f)
            out.writerow(metrics.SAMPLE_COLUMNS)
            session.give_up_at = t_start + wait
            try:
                session.open()
                t_open = time.monotonic()
                meta["bluetooth_at_start"] = bluetooth_snapshot(getattr(session.device, "product_id", None))
                meta["device_model"] = getattr(session.device, "model", None)
                session.give_up_at = time.monotonic() + wait
                for event in session.events():
                    if isinstance(event, protocol.Placement):
                        add_event(event.host_time, "placement", ",".join(event.buds))
                        continue
                    if not isinstance(event, RecSample):
                        continue
                    if first is None:
                        first = event.host_time
                        session.stop_at = time.monotonic() + seconds + 0.5
                        say("First sample. Recording...")
                    if event.host_time - first >= seconds:
                        break
                    q = event.quaternion or ("", "", "", "")
                    row = [n, "%.6f" % event.host_time, "%.6f" % event.recv_time, event.msg_idx, event.n_in_msg,
                           event.sensor_ns, *("%.3f" % v for v in event.gyro),
                           *("%.4f" % v for v in event.acceleration), *("%.4f" % v for v in event.gravity),
                           *(v if v == "" else "%.5f" % v for v in q), event.raw.hex()]
                    out.writerow(row)
                    for c, v in zip(metrics.SAMPLE_COLUMNS, row):
                        samples[c].append(v)
                    n += 1
            except RunOver as e:
                if first is None:
                    meta["error"] = str(e)
            except podsense.PodsenseError as e:
                meta["error"] = str(e)
            except KeyboardInterrupt:
                meta["error"] = "stopped by the operator"
            finally:
                add_event(time.monotonic(), "run_end", "%d samples" % n)
                meta["bluetooth_at_end"] = bluetooth_snapshot(getattr(session.device, "product_id", None))
                session.close()
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)

    if n >= 3 and meta["error"] is None:
        meta["status"] = "ok"
    placement = session.placement
    meta["placement_reported"] = list(placement.buds) if placement else None
    told = str(conditions.get("in_ear", "")).lower()
    if placement and told in ("yes", "no", "both", "one", "none"):
        worn = sum(1 for b in placement.buds if b == "in_ear")
        expect = {"yes": (1, 2), "both": (2,), "one": (1,), "no": (0,), "none": (0,)}[told]
        if worn not in expect:
            meta["placement_warning"] = "in_ear was given as %r but the AirPods reported %s" % (
                conditions.get("in_ear"), ",".join(placement.buds))

    with open(run_dir / "events.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=metrics.EVENT_COLUMNS)
        w.writeheader()
        w.writerows(sorted(events, key=lambda e: float(e["host_time"])))

    numeric = {"host_time": [float(v) for v in samples["host_time"]],
               "recv_time": [float(v) for v in samples["recv_time"]],
               "sensor_ns": samples["sensor_ns"], "msg_idx": samples["msg_idx"],
               "n_in_msg": samples["n_in_msg"], "raw_hex": samples["raw_hex"]}
    summary = metrics.compute(numeric, events, rate_hz) if n else {"samples": 0, "requested_hz": rate_hz}
    summary.update(run_id=run_id, status=meta["status"], error=meta["error"],
                   time_to_session_open_s=(t_open - t_start) if t_open else None,
                   time_to_first_sample_s=(session.first_sample_host - t_start) if session.first_sample_host else None)
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    say(report(summary, meta))
    return run_dir


def report(s, meta=None):
    """The end-of-run summary as text."""
    lines = ["", "Run %s: %s" % (s.get("run_id", ""), (s.get("status") or "").upper())]
    if s.get("error"):
        lines.append("  error            %s" % s["error"])
    if s.get("samples", 0) < 3:
        lines.append("  samples          %d" % s.get("samples", 0))
        return "\n".join(lines)
    ia, cb, dv, bs = s["interarrival_ms"], s["callback_interarrival_ms"], s["delay_variation_ms"], s["burst_size"]
    lines += [
        "  samples          %d of %d expected, %d missing in %d holes (%.3f %% loss)" % (
            s["samples"], s["expected"], s["missing"], s["holes"], s["loss_pct"]),
        "  rate             %.2f Hz by the AirPods' timestamps, %.2f Hz by this computer's clock (asked %g)" % (
            s["rate_sensor_hz"], s["rate_host_hz"], s["requested_hz"]),
        "  counter          %s" % ("%d missing by the report's own counter (%s the timestamps)" % (
            s["counter_missing"], "agrees with" if s.get("counter_agrees_with_timestamps") else "DIFFERS from")
            if "counter_missing" in s else "not available"),
        "  sensor interval  nominal %.3f ms, longest %.1f ms, duplicates %d, out of order %d" % (
            s["period_ms"], s["longest_gap_sensor_ms"], s["duplicates"], s["out_of_order"]),
        "  arrival          per sample: median %.2f ms, p99 %.1f ms, longest %.1f ms" % (ia["median"], ia["p99"], ia["max"]),
        "                   per callback: median %.2f ms, p99 %.1f ms" % (cb["median"], cb["p99"]),
        "  bursts           %d callbacks, %.1f %% carry one sample, largest %d" % (
            s["callbacks"], bs["share_single_pct"], bs["max"]),
        "  delay variation  median %.1f ms, p99 %.1f ms, longest %.1f ms" % (dv["median"], dv["p99"], dv["max"]),
        "  session          first sample after %.1f s, reconnect attempts %d, losses %d, stream restarts %d" % (
            s["time_to_first_sample_s"] or float("nan"), s["reconnect_attempts"], s["session_losses"], s["stream_restarts"]),
    ]
    if meta and meta.get("placement_warning"):
        lines.append("  WARNING          %s" % meta["placement_warning"])
    return "\n".join(lines)
