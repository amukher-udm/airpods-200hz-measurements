"""The recorder against a scripted stand-in for the Bluetooth transport."""
import json
import struct

import podsense
from podsense import protocol
from podsense.devices import Device

from podmeasure import metrics, recorder


def block(timestamp_ns, gyro=(0, 0, 0)):
    b = bytearray(56)
    b[0] = 1
    struct.pack_into("<Q", b, 1, timestamp_ns)
    struct.pack_into("<hhh", b, 20, 100, 200, 300)
    struct.pack_into("<hhh", b, 26, *gyro)
    struct.pack_into("<hhh", b, 44, 0, 0, -1000)
    return bytes(b)


def message(*reports):
    body = b""
    for report in reports:
        inner = bytes([8, protocol.SERVICE_MOTION, 26, len(report)]) + report
        body += bytes([58, len(inner)]) + inner
    return protocol.SENSOR_DATA + struct.pack("<H", len(body)) + body


class FakeTransport:
    settle = 0.0

    def __init__(self, script):
        self.script = list(script)

    def open(self, device):
        pass

    def read(self, timeout):
        if not self.script:
            return None
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def write(self, data):
        pass

    def close(self):
        pass


DEVICE = Device("Test AirPods", "AA:BB:CC:DD:EE:FF", "2014")
CONDITIONS = {"in_ear": "no", "audio": "none", "distance": "1m", "obstruction": "none"}


def run(script, tmp_path, seconds=1.0, **kw):
    session = recorder.RecSession(rate_hz=200, _transport=FakeTransport(script), _device=DEVICE)
    run_dir = recorder.record("T", 200, seconds, CONDITIONS, root=tmp_path, session=session, quiet=True, **kw)
    return run_dir, json.loads((run_dir / "meta.json").read_text()), json.loads((run_dir / "summary.json").read_text())


def test_records_samples_with_both_clocks_and_raw_report(tmp_path):
    # 5 ms apart on both clocks; sample 50 is lost; samples 100-102 arrive in one message
    script = []
    for i in range(300):
        if i == 50 or i in (101, 102):
            continue
        reports = [block(i * 5_000_000, gyro=(i, 0, 0))]
        if i == 100:
            reports += [block(101 * 5_000_000, gyro=(101, 0, 0)), block(102 * 5_000_000, gyro=(102, 0, 0))]
        script.append((100.0 + i * 0.005, message(*reports)))
    run_dir, meta, summary = run(script, tmp_path, seconds=1.0)
    samples, events, _ = metrics.load_run(run_dir)

    assert meta["status"] == "ok" and meta["exp"] == "T" and meta["conditions"] == CONDITIONS
    assert meta["device_model"] == "AirPods Pro (2nd generation)"
    assert samples["sensor_ns"][0] == 0 and samples["sensor_ns"][1] == 5_000_000
    assert samples["idx"] == list(range(len(samples["idx"])))
    # 1.0 s from the first sample: samples 0..199 minus the lost one
    assert summary["samples"] == 199 and summary["missing"] == 1 and summary["holes"] == 1
    assert summary["samples_per_message"] == {"1": 196, "3": 1}
    assert samples["n_in_msg"][99:102] == [3, 3, 3]
    assert len(set(samples["msg_idx"][99:102])) == 1
    assert samples["host_time"][99] == samples["host_time"][101]
    assert bytes.fromhex(samples["raw_hex"][3]) == block(3 * 5_000_000, gyro=(3, 0, 0))
    assert samples["gyro_x"][3] == round(3 * protocol.GYRO_DPS_PER_COUNT, 3)
    kinds = [e["type"] for e in events]
    assert kinds[0] == "run_start" and kinds[-1] == "run_end" and "session_open" in kinds


def test_lost_session_is_reopened_and_logged(tmp_path):
    script = [(100.0 + i * 0.005, message(block(i * 5_000_000))) for i in range(40)]
    script.append(podsense.PodsenseError("the AirPods disconnected"))
    script += [(102.3 + i * 0.005, message(block((460 + i) * 5_000_000))) for i in range(200)]
    run_dir, meta, summary = run(script, tmp_path, seconds=3.0)
    assert meta["status"] == "ok"
    assert summary["session_losses"] == 1 and summary["sessions_opened"] == 2
    assert summary["samples"] == 180                       # 40 before, and 140 until 3.0 s from the first
    assert summary["missing"] == 420 and summary["holes"] == 1


def test_no_samples_is_a_failed_run_that_is_still_written(tmp_path):
    run_dir, meta, summary = run([], tmp_path, seconds=1.0, wait=0.3)
    assert meta["status"] == "failed" and meta["error"] == "no sample arrived"
    assert summary["samples"] == 0
    assert (run_dir / "samples.csv").exists() and (run_dir / "events.csv").exists()


def test_placement_is_logged_and_checked_against_the_label(tmp_path):
    placement = (99.9, protocol.HEADER + bytes([protocol.OP_PLACEMENT, 0, 0, 0]))     # both in the ear
    script = [placement] + [(100.0 + i * 0.005, message(block(i * 5_000_000))) for i in range(250)]
    run_dir, meta, _ = run(script, tmp_path, seconds=1.0)
    _, events, _ = metrics.load_run(run_dir)
    assert meta["placement_reported"] == ["in_ear", "in_ear"]
    assert "in_ear was given as 'no'" in meta["placement_warning"]
    assert any(e["type"] == "placement" and e["detail"] == "in_ear,in_ear" for e in events)
