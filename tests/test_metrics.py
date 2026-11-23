"""Metrics on synthetic logs where the answer is known."""
import numpy as np
import pytest

from podmeasure import metrics

PERIOD_NS = 5_000_000


def stream(n=2000, drop=(), period_ns=PERIOD_NS, start_ns=7_000_000_000):
    """Sensor timestamps of n produced samples with the indices in `drop` never delivered."""
    keep = np.array([i for i in range(n) if i not in set(drop)])
    return start_ns + keep * period_ns


def test_clean_stream():
    m = metrics.sensor_metrics(stream(2000), 200)
    assert m["samples"] == 2000 and m["missing"] == 0 and m["holes"] == 0
    assert m["loss_pct"] == 0 and m["duplicates"] == 0 and m["out_of_order"] == 0 and m["off_grid"] == 0
    assert m["rate_sensor_hz"] == pytest.approx(200.0)
    assert m["period_ms"] == pytest.approx(5.0)
    assert m["longest_gap_sensor_ms"] == pytest.approx(5.0)


def test_single_drops_are_counted():
    """The stock podsense rule (step > 2.5 x median) misses these."""
    m = metrics.sensor_metrics(stream(2000, drop=[100, 500, 900]), 200)
    assert m["missing"] == 3 and m["holes"] == 3
    assert m["expected"] == 2000 and m["samples"] == 1997
    assert m["loss_pct"] == pytest.approx(100 * 3 / 2000)
    assert m["hole_sizes"] == {"1": 3}


def test_long_hole_and_mixed_loss():
    drop = list(range(300, 325)) + [700, 701] + [1500]
    m = metrics.sensor_metrics(stream(2000, drop=drop), 200)
    assert m["missing"] == 28 and m["holes"] == 3
    assert m["hole_sizes"] == {"1": 1, "2": 1, "25": 1}
    assert m["longest_gap_sensor_ms"] == pytest.approx(26 * 5.0)
    assert m["loss_pct"] == pytest.approx(1.4)


def test_duplicates_and_out_of_order():
    t = list(stream(1000))
    t.insert(400, t[399])                    # the same sample twice
    t[700], t[701] = t[701], t[700]          # two samples swapped
    m = metrics.sensor_metrics(t, 200)
    assert m["duplicates"] == 1
    assert m["out_of_order"] == 1
    assert m["samples"] == 1001


def test_off_grid_steps_are_reported():
    t = stream(1000).copy()
    t[500:] += 1_300_000                     # a step of 6.3 ms: not a whole number of periods
    m = metrics.sensor_metrics(t, 200)
    assert m["off_grid"] == 1 and m["missing"] == 0


def test_too_few_samples():
    assert metrics.sensor_metrics(stream(2), 200) == {"samples": 2, "requested_hz": 200}
    assert metrics.arrival_metrics([0.0, 0.1], stream(2)) == {}


def bursty_host(sensor_ns, burst=4, delay_s=0.003):
    """Each group of `burst` samples is delivered together, `delay_s` after its last sample."""
    ts = (np.asarray(sensor_ns) - sensor_ns[0]) / 1e9
    host = np.empty(len(ts))
    for a in range(0, len(ts), burst):
        host[a:a + burst] = ts[min(a + burst, len(ts)) - 1] + delay_s
    return 1000.0 + host


def test_bursts():
    t = stream(2000)
    m = metrics.arrival_metrics(bursty_host(t, burst=4), t)
    assert m["callbacks"] == 500
    assert m["burst_size"]["max"] == 4 and m["burst_size"]["mean"] == pytest.approx(4.0)
    assert m["burst_size"]["share_single_pct"] == 0
    assert m["burst_size"]["hist"] == {"4": 500}
    assert m["callback_interarrival_ms"]["median"] == pytest.approx(20.0)
    assert m["interarrival_ms"]["median"] == pytest.approx(0.0)
    assert m["interarrival_ms"]["max"] == pytest.approx(20.0)
    assert m["rate_host_hz"] == pytest.approx(1999 / (ts_span(t) - 0.015), rel=1e-6)
    # within a burst of 4 the first sample waited 15 ms longer than the last
    assert m["delay_variation_ms"]["max"] == pytest.approx(15.0, abs=0.01)
    assert m["delay_variation_ms"]["median"] == pytest.approx(7.5, abs=2.6)


def ts_span(t):
    return (t[-1] - t[0]) / 1e9


def test_one_sample_per_callback_has_no_delay_variation():
    t = stream(2000)
    m = metrics.arrival_metrics(bursty_host(t, burst=1), t)
    assert m["callbacks"] == 2000 and m["burst_size"]["share_single_pct"] == 100
    assert m["delay_variation_ms"]["max"] == pytest.approx(0.0, abs=1e-6)
    assert m["rate_host_hz"] == pytest.approx(200.0)


def test_known_jitter_and_one_late_delivery():
    rng = np.random.default_rng(1)
    t = stream(4000)
    ts = (t - t[0]) / 1e9
    jitter = rng.uniform(0.0, 0.010, len(ts))            # every sample 0 to 10 ms late
    host = np.maximum.accumulate(50.0 + ts + jitter)     # delivery never goes back in time
    host[2000:2020] = host[2019] + 0.080                 # twenty samples held 80 ms and released together
    host = np.maximum.accumulate(host)
    m = metrics.arrival_metrics(host, t)
    assert 4.0 < m["delay_variation_ms"]["median"] < 6.5
    assert m["delay_variation_ms"]["max"] == pytest.approx(80 + 95 + 5, abs=12)   # first of the 20 waited longest
    assert m["burst_size"]["max"] >= 20


def test_clock_drift_is_removed_and_reported():
    t = stream(12000)                                    # 60 s
    ts = (t - t[0]) / 1e9
    host = 10.0 + ts * (1 + 200e-6)                      # the host clock runs 200 ppm fast
    m = metrics.arrival_metrics(host, t)
    assert m["drift_ppm"] == pytest.approx(200.0, abs=1.0)
    assert m["delay_variation_ms"]["max"] < 0.01
    assert m["rate_host_hz"] == pytest.approx(200.0 / (1 + 200e-6), rel=1e-9)


def test_pipe_delay():
    t = stream(100)
    host = bursty_host(t, burst=1)
    m = metrics.arrival_metrics(host, t, recv_time=host + 0.0004)
    assert m["pipe_delay_ms"]["median"] == pytest.approx(0.4)


def raw(ts_ns, payload):
    return (bytes([1]) + int(ts_ns).to_bytes(8, "little") + payload).hex()


def raw_counted(ts_ns, counter):
    return (bytes([1]) + int(ts_ns).to_bytes(8, "little") + bytes(45) + (counter % 65536).to_bytes(2, "little")).hex()


def test_counter_loss_including_wraparound():
    produced = [i for i in range(3000) if i not in (100, 2000, 2001, 2002)]
    m = metrics.counter_metrics([raw_counted(i * PERIOD_NS, 65000 + i) for i in produced])   # wraps at 65536
    assert m["counter_missing"] == 4 and m["counter_holes"] == 2
    assert m["counter_repeats"] == 0 and m["counter_backward"] == 0
    assert m["counter_loss_pct"] == pytest.approx(100 * 4 / 3000)


def test_counter_at_a_lower_rate_steps_by_more_than_one():
    produced = [i for i in range(1500) if i not in (700, 701)]
    m = metrics.counter_metrics([raw_counted(i * 8 * PERIOD_NS, 65000 + 8 * i) for i in produced])   # 25 Hz
    assert m["counter_step"] == 8 and m["counter_missing"] == 2 and m["counter_holes"] == 1
    assert m["counter_off_step"] == 0


def test_counter_repeat_and_short_reports():
    rows = [raw_counted(i * PERIOD_NS, c) for i, c in enumerate([5, 6, 6, 7])]
    assert metrics.counter_metrics(rows)["counter_repeats"] == 1
    assert metrics.counter_metrics(["01" * 20]) == {}


def test_freshness_fresh_and_repeated():
    t = stream(1000)
    fresh = [raw(ts, i.to_bytes(4, "little") * 11 + b"\0\0\0") for i, ts in enumerate(t)]
    assert metrics.freshness_metrics(fresh)["identical_consecutive"] == 0
    doubled = [raw(ts, (i // 2).to_bytes(4, "little") * 11 + b"\0\0\0") for i, ts in enumerate(t)]
    m = metrics.freshness_metrics(doubled)                # a 100 Hz stream repeated to 200 Hz
    assert m["identical_consecutive"] == 500
    assert m["identical_even_index"] == 500 and m["identical_odd_index"] == 0


def test_event_counts():
    events = [{"type": k} for k in ["run_start", "open_start", "reconnect_attempt", "reconnect_attempt",
                                    "session_open", "session_lost", "open_start", "session_open",
                                    "stream_restart", "run_end"]]
    assert metrics.event_metrics(events) == {"reconnect_attempts": 2, "sessions_opened": 2,
                                             "session_losses": 1, "stream_restarts": 1}


def test_compute_puts_it_together():
    t = stream(2000, drop=[10, 11, 12])
    host = bursty_host(t, burst=2)
    samples = {"sensor_ns": list(t), "host_time": list(host), "recv_time": list(host + 0.001),
               "msg_idx": list(range(len(t))), "n_in_msg": [1] * len(t),
               "raw_hex": [raw(ts, i.to_bytes(4, "little") * 11 + b"\0\0\0") for i, ts in enumerate(t)]}
    m = metrics.compute(samples, [{"type": "session_open"}], 200)
    assert m["missing"] == 3 and m["samples"] == 1997 and m["sessions_opened"] == 1
    assert m["samples_per_message"] == {"1": 1997}
    assert m["identical_consecutive"] == 0


def test_compute_checks_counter_against_timestamps():
    produced = [i for i in range(2000) if i not in (10, 11, 12)]
    t = stream(2000, drop=[10, 11, 12])
    host = bursty_host(t, burst=1)
    samples = {"sensor_ns": list(t), "host_time": list(host),
               "raw_hex": [raw_counted(ts, i) for ts, i in zip(t, produced)]}
    m = metrics.compute(samples, [], 200)
    assert m["counter_missing"] == 3 and m["counter_agrees_with_timestamps"] is True
