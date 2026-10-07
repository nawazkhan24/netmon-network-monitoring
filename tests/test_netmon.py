import time

from fastapi.testclient import TestClient

from app.database import db
from app.main import app
from app.monitor import apply_result
from app.ping_utils import PingResult, parse_output
from app.snmp_utils import SnmpResult

client = TestClient(app)  # (scheduler is not started without the `with` block)


# ---------- ping parsing ----------
def test_parse_linux_ping():
    out = "64 bytes from 8.8.8.8: icmp_seq=1 ttl=117 time=12.5 ms\n64 bytes from 8.8.8.8: icmp_seq=2 ttl=117 time=13.5 ms\n"
    r = parse_output(out, 4)
    assert r.reachable and r.packet_loss == 50.0 and r.avg_latency_ms == 13.0


def test_parse_windows_ping():
    out = "Reply from 8.8.8.8: bytes=32 time=12ms TTL=117\nReply from 8.8.8.8: bytes=32 time<1ms TTL=117\n"
    r = parse_output(out, 2)
    assert r.reachable and r.packet_loss == 0.0


def test_parse_all_lost():
    r = parse_output("Request timed out.\nRequest timed out.\n", 2)
    assert not r.reachable and r.packet_loss == 100.0 and r.avg_latency_ms is None


# ---------- API ----------
def test_add_list_delete_device():
    r = client.post("/api/devices", json={"name": "R1", "ip": "192.168.1.1", "snmp_enabled": True})
    assert r.status_code == 201
    devices = client.get("/api/devices").json()
    assert len(devices) == 1 and "community" not in devices[0]  # secret never leaves the server
    assert client.delete(f"/api/devices/{devices[0]['id']}").status_code == 200
    assert client.delete("/api/devices/999").status_code == 404


def test_rejects_dangerous_host():
    for bad in ["-c 5", "1.1.1.1; rm -rf /", "a b", ""]:
        assert client.post("/api/devices", json={"name": "x", "ip": bad}).status_code == 422


# ---------- alert logic ----------
def _device():
    client.post("/api/devices", json={"name": "R1", "ip": "10.0.0.1", "snmp_enabled": True})
    return 1


def _apply(ok, snmp=None, reason="No ping response"):
    with db() as conn:
        dev = dict(conn.execute("SELECT * FROM devices WHERE id=1").fetchone())
        ping = PingResult(ok, 5.0 if ok else None, 0.0 if ok else 100.0)
        return apply_result(conn, dev, {"ts": time.time(), "ok": ok, "reason": None if ok else reason,
                                        "ping": ping, "snmp": snmp})


def test_down_needs_two_failures_and_alerts_once_then_recovers():
    _device()
    assert _apply(False) == []                      # 1st failure: no alarm yet
    notes = _apply(False)                           # 2nd failure: DOWN
    assert len(notes) == 1 and notes[0][0] == "CRITICAL"
    assert _apply(False) == []                      # still down: no duplicate alert
    notes = _apply(True)                            # recovered
    assert len(notes) == 1 and notes[0][0] == "RESOLVED"


def test_cpu_threshold_alert():
    _device()
    snmp = SnmpResult(ok=True, cpu_percent=97.0, ram_percent=40.0, if_index=2, if_name="eth0", if_status="up")
    notes = _apply(True, snmp)
    assert any(n[0] == "WARNING" and "CPU" in n[2] for n in notes)
    assert _apply(True, snmp) == []                 # no repeat while it stays high
