"""Delivery metrics of one recorded run. Pure functions over arrays: no Bluetooth, no files
except in load_run().

Two clocks are kept apart throughout:
  sensor   the AirPods' own timestamp of each sample (integer nanoseconds)
  host     time.monotonic() on the computer when the Bluetooth callback delivered the sample
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np

SAMPLE_COLUMNS = ["idx", "host_time", "recv_time", "msg_idx", "n_in_msg", "sensor_ns",
                  "gyro_x", "gyro_y", "gyro_z", "accel_x", "accel_y", "accel_z",
                  "gravity_x", "gravity_y", "gravity_z", "quat_w", "quat_x", "quat_y", "quat_z", "raw_hex"]
EVENT_COLUMNS = ["host_time", "type", "detail"]


def _pct(x, p):
    return float(np.percentile(x, p)) if len(x) else float("nan")


def _dist(x_ms):
    """Summary of a set of durations in milliseconds."""
    if not len(x_ms):
        return {"median": None, "p90": None, "p99": None, "max": None}
    return {"median": _pct(x_ms, 50), "p90": _pct(x_ms, 90), "p99": _pct(x_ms, 99), "max": float(np.max(x_ms))}


def sensor_metrics(sensor_ns, requested_hz=None):
    """Rate, loss, duplicates and order from the sensor timestamps alone.

    The nominal period is the median step. A step of k periods (k >= 2) means k-1 samples that
    the sensor produced never arrived: every such sample is counted, a single drop included.
    """
    t = np.asarray(sensor_ns, dtype=np.int64)
    n = len(t)
    out = {"samples": int(n), "requested_hz": requested_hz}
    if n < 3:
        return out
    steps = np.diff(t)
    forward = steps[steps > 0]
    period = float(np.median(forward)) if len(forward) else 0.0
    span = float(t[-1] - t[0])
    out.update(duration_sensor_s=span / 1e9,
               rate_sensor_hz=(n - 1) / (span / 1e9) if span > 0 else None,
               period_ms=period / 1e6,
               duplicates=int(np.sum(steps == 0)),
               out_of_order=int(np.sum(steps < 0)))
    if period <= 0:
        return out
    k = np.rint(forward / period).astype(np.int64)
    holes = k[k > 1]
    missing = int(np.sum(holes - 1))
    out.update(holes=int(len(holes)), missing=missing,
               expected=int(n + missing),
               loss_pct=100.0 * missing / (n + missing),
               longest_gap_sensor_ms=float(np.max(forward)) / 1e6,
               off_grid=int(np.sum(np.abs(forward - k * period) > 0.02 * period)),
               hole_sizes={str(int(v)): int(c) for v, c in zip(*np.unique(holes - 1, return_counts=True))})
    return out


def arrival_metrics(host_time, sensor_ns, recv_time=None):
    """How samples reach the host: spacing, bursts, and delay variation.

    Samples delivered in one Bluetooth callback share a host time; such a group is a burst.
    Delay variation is host time minus sensor time with the clock drift removed (a line through
    the lowest lag of each of 20 slices of the run) and its smallest value set to zero, so it
    shows how much later than the fastest sample each one arrived, not the absolute delay.
    """
    h = np.asarray(host_time, dtype=np.float64)
    t = np.asarray(sensor_ns, dtype=np.int64)
    n = len(h)
    out = {}
    if n < 3:
        return out
    span = h[-1] - h[0]
    out.update(duration_host_s=float(span), rate_host_hz=(n - 1) / span if span > 0 else None)
    out["interarrival_ms"] = _dist(np.diff(h) * 1e3)
    starts = np.flatnonzero(np.r_[True, np.diff(h) != 0])
    sizes = np.diff(np.r_[starts, n])
    out["callbacks"] = int(len(starts))
    out["burst_size"] = {"mean": float(np.mean(sizes)), "p99": _pct(sizes, 99), "max": int(np.max(sizes)),
                         "share_single_pct": 100.0 * float(np.mean(sizes == 1)),
                         "hist": {str(int(v)): int(c) for v, c in zip(*np.unique(sizes, return_counts=True))}}
    out["callback_interarrival_ms"] = _dist(np.diff(h[starts]) * 1e3)
    out["longest_gap_host_ms"] = float(np.max(np.diff(h))) * 1e3
    ts = (t - t[0]) / 1e9
    lag = h - h[0] - ts
    edges = np.linspace(0, n, 21).astype(int)
    xs, ys = [], []
    for a, b in zip(edges, edges[1:]):
        if b > a:
            i = a + int(np.argmin(lag[a:b]))
            xs.append(ts[i])
            ys.append(lag[i])
    slope, intercept = np.polyfit(xs, ys, 1) if len(xs) >= 2 and ts[-1] > 0 else (0.0, float(np.min(lag)))
    resid = lag - (slope * ts + intercept)
    delay = (resid - np.min(resid)) * 1e3
    out["drift_ppm"] = float(slope) * 1e6
    out["delay_variation_ms"] = _dist(delay)
    if recv_time is not None:
        out["pipe_delay_ms"] = _dist((np.asarray(recv_time, dtype=np.float64) - h) * 1e3)
    return out


def freshness_metrics(raw_hex):
    """Consecutive samples whose payload after the timestamp is byte-identical.

    A stream repeated from a lower rate would show these on one parity of the index only.
    """
    body = [r[18:] for r in raw_hex]          # hex chars after report id (1 byte) and timestamp (8 bytes)
    same = np.array([a == b for a, b in zip(body, body[1:])], dtype=bool)
    idx = np.flatnonzero(same)
    return {"identical_consecutive": int(len(idx)),
            "identical_even_index": int(np.sum(idx % 2 == 0)),
            "identical_odd_index": int(np.sum(idx % 2 == 1))}


COUNTER_OFFSET = 54     # u16 little-endian at the end of the 56-byte report: +1 per sample produced


def counter_metrics(raw_hex):
    """Loss from the report's own sample counter, independent of the timestamps.

    Seen on AirPods Pro 2, firmware 9B47: the last two bytes of the report count the samples the
    earbud produced at its internal rate. A step of k usual steps means k-1 samples never arrived;
    0 is a repeated sample.
    """
    if not raw_hex or any(len(r) < 2 * (COUNTER_OFFSET + 2) for r in raw_hex):
        return {}
    i = 2 * COUNTER_OFFSET
    c = np.array([int(r[i + 2:i + 4] + r[i:i + 2], 16) for r in raw_hex], dtype=np.int64)
    d = np.diff(c) % 65536
    ahead = d[(d >= 1) & (d < 32768)]
    if not len(ahead):
        return {}
    # The counter runs at the sensor's own 200 Hz: at a lower requested rate it advances by a
    # fixed step per delivered sample (2 at 100 Hz, 8 at 25 Hz). The usual step is that unit.
    values, counts = np.unique(ahead, return_counts=True)
    unit = int(values[np.argmax(counts)])
    k = np.rint(ahead / unit).astype(np.int64)
    missing = int(np.sum(k[k > 1] - 1))
    return {"counter_step": unit, "counter_missing": missing, "counter_holes": int(np.sum(k > 1)),
            "counter_off_step": int(np.sum(ahead % unit != 0)),
            "counter_repeats": int(np.sum(d == 0)), "counter_backward": int(np.sum(d >= 32768)),
            "counter_loss_pct": 100.0 * missing / (len(c) + missing)}


def event_metrics(events):
    """Counts of what happened to the session during the run."""
    count = lambda kind: sum(1 for e in events if e["type"] == kind)
    return {"reconnect_attempts": count("reconnect_attempt"), "sessions_opened": count("session_open"),
            "session_losses": count("session_lost"), "stream_restarts": count("stream_restart")}


def compute(samples, events=(), requested_hz=None):
    """Every metric of a run. `samples` maps column name -> list, as load_run() returns."""
    out = sensor_metrics(samples["sensor_ns"], requested_hz)
    out.update(arrival_metrics(samples["host_time"], samples["sensor_ns"], samples.get("recv_time")))
    if samples.get("raw_hex"):
        out.update(freshness_metrics(samples["raw_hex"]))
        out.update(counter_metrics(samples["raw_hex"]))
        if "counter_missing" in out and "missing" in out:
            out["counter_agrees_with_timestamps"] = out["counter_missing"] == out["missing"]
    if samples.get("n_in_msg"):
        firsts = np.flatnonzero(np.r_[True, np.diff(np.asarray(samples["msg_idx"])) != 0])
        sizes = np.asarray(samples["n_in_msg"])[firsts] if len(firsts) else np.array([], dtype=int)
        out["samples_per_message"] = {str(int(v)): int(c) for v, c in zip(*np.unique(sizes, return_counts=True))}
    out.update(event_metrics(events))
    return out


def load_run(run_dir):
    """(samples, events, meta) of a recorded run directory."""
    run_dir = Path(run_dir)
    samples = {c: [] for c in SAMPLE_COLUMNS}
    with open(run_dir / "samples.csv", newline="") as f:
        for row in csv.DictReader(f):
            samples["idx"].append(int(row["idx"]))
            samples["host_time"].append(float(row["host_time"]))
            samples["recv_time"].append(float(row["recv_time"]))
            samples["msg_idx"].append(int(row["msg_idx"]))
            samples["n_in_msg"].append(int(row["n_in_msg"]))
            samples["sensor_ns"].append(int(row["sensor_ns"]))
            samples["raw_hex"].append(row["raw_hex"])
            for c in SAMPLE_COLUMNS[6:19]:
                samples[c].append(float(row[c]) if row[c] != "" else float("nan"))
    events = []
    if (run_dir / "events.csv").exists():
        with open(run_dir / "events.csv", newline="") as f:
            events = list(csv.DictReader(f))
    meta = json.loads((run_dir / "meta.json").read_text())
    return samples, events, meta


def list_runs(root="runs", status="ok"):
    """Run directories under `root` recorded by podmeasure, oldest first; only those with this status."""
    found = []
    for d in sorted(Path(root).iterdir()) if Path(root).exists() else []:
        if (d / "meta.json").exists() and (d / "samples.csv").exists() and (d / "summary.json").exists():
            meta = json.loads((d / "meta.json").read_text())
            if "exp" in meta and (status is None or meta.get("status") == status):
                found.append(d)
    return found
