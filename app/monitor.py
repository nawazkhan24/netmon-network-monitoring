"""The monitoring engine.

Every POLL_INTERVAL seconds `poll_all()`:
  1. reads all devices from the database
  2. checks them in parallel (ping + optional SNMP)        -> poll_device()
  3. stores results, updates status, evaluates alerts       -> apply_result()
  4. sends Telegram / Email notifications after the DB commit
"""
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple

from . import alerts, config
from .database import db
from .ping_utils import ping_host
from .snmp_utils import snmp_poll

log = logging.getLogger("netmon.monitor")
_poll_lock = threading.Lock()  # prevents two polling rounds from overlapping

Notification = Tuple[str, str, str]  # (severity, title, message)


# --------------------------------------------------------------------------
# Step 1: check a single device (runs in a worker thread, does NOT touch the DB)
# --------------------------------------------------------------------------
def poll_device(dev: dict) -> dict:
    ts = time.time()
    ping = ping_host(dev["ip"], config.PING_COUNT)
    snmp = None
    if dev["snmp_enabled"] and ping.reachable:
        snmp = snmp_poll(dev["ip"], dev["snmp_port"], dev["community"], dev["if_index"], dev["if_name"])

    if not ping.reachable:
        ok, reason = False, "No ping response"
    elif snmp is not None and not snmp.ok:
        ok, reason = False, "SNMP agent not responding"
    else:
        ok, reason = True, None
    return {"ts": ts, "ok": ok, "reason": reason, "ping": ping, "snmp": snmp}


def _safe_poll(dev: dict) -> Optional[dict]:
    try:
        return poll_device(dev)
    except Exception:
        log.exception("Polling %s crashed", dev.get("name"))
        return None


# --------------------------------------------------------------------------
# Step 2: turn raw results into stored data + alerts
# --------------------------------------------------------------------------
def _rate(new: Optional[int], old: Optional[int], dt: float) -> Optional[float]:
    if new is None or old is None or dt <= 0 or new < old:  # counter reset/wrap -> skip sample
        return None
    return (new - old) * 8 / dt  # bytes -> bits per second


def _set_alert(conn, dev, kind, severity, message, notes: List[Notification]) -> None:
    """Raise an alert only when the condition turns active (no repeated spam)."""
    row = conn.execute("SELECT active FROM alert_state WHERE device_id=? AND kind=?", (dev["id"], kind)).fetchone()
    if row and row["active"]:
        return
    conn.execute("INSERT OR REPLACE INTO alert_state (device_id, kind, active) VALUES (?,?,1)", (dev["id"], kind))
    conn.execute(
        "INSERT INTO alerts (device_id, device_name, kind, severity, message, ts) VALUES (?,?,?,?,?,?)",
        (dev["id"], dev["name"], kind, severity, message, time.time()),
    )
    notes.append((severity, f"{severity}: {dev['name']} - {kind}", message))


def _clear_alert(conn, dev, kind, message, notes: List[Notification]) -> None:
    row = conn.execute("SELECT active FROM alert_state WHERE device_id=? AND kind=?", (dev["id"], kind)).fetchone()
    if not row or not row["active"]:
        return
    conn.execute("UPDATE alert_state SET active=0 WHERE device_id=? AND kind=?", (dev["id"], kind))
    conn.execute(
        "INSERT INTO alerts (device_id, device_name, kind, severity, message, ts) VALUES (?,?,?,?,?,?)",
        (dev["id"], dev["name"], kind, "RESOLVED", message, time.time()),
    )
    notes.append(("RESOLVED", f"RESOLVED: {dev['name']} - {kind}", message))


def _check_threshold(conn, dev, kind, value, limit, unit, label, notes) -> None:
    if value is None:
        return
    who = f"{dev['name']} ({dev['ip']})"
    if value >= limit:
        _set_alert(conn, dev, kind, "WARNING", f"{who}: {label} is {value:.1f}{unit} (limit {limit:g}{unit})", notes)
    else:
        _clear_alert(conn, dev, kind, f"{who}: {label} back to normal ({value:.1f}{unit})", notes)


def apply_result(conn, dev: dict, res: dict) -> List[Notification]:
    notes: List[Notification] = []
    ping, snmp, ts = res["ping"], res["snmp"], res["ts"]
    who = f"{dev['name']} ({dev['ip']})"

    # ---- status with "N failures in a row" rule (avoids false alarms) ----
    if res["ok"]:
        status, fail_count = "UP", 0
    else:
        fail_count = dev["fail_count"] + 1
        status = "DOWN" if fail_count >= config.FAIL_THRESHOLD else dev["status"]
    reason = res["reason"] if status == "DOWN" else None

    # ---- SNMP derived values ----
    cpu = ram = in_bps = out_bps = None
    if_status = dev["if_status"]
    upd = {}
    if snmp is not None and snmp.ok:
        cpu, ram, if_status = snmp.cpu_percent, snmp.ram_percent, snmp.if_status
        if cpu is None and snmp.cpu_total_ticks is not None and dev["last_cpu_total"] is not None:
            d_total = snmp.cpu_total_ticks - dev["last_cpu_total"]
            d_idle = snmp.cpu_idle_ticks - dev["last_cpu_idle"]
            if d_total > 0 and d_idle >= 0:
                cpu = round(max(0.0, min(100.0, (1 - d_idle / d_total) * 100)), 1)
        if snmp.cpu_total_ticks is not None:
            upd["last_cpu_idle"], upd["last_cpu_total"] = snmp.cpu_idle_ticks, snmp.cpu_total_ticks
        if snmp.if_index is not None:
            same_if = dev["if_index"] == snmp.if_index
            if same_if and dev["last_counter_ts"]:
                dt = ts - dev["last_counter_ts"]
                in_bps = _rate(snmp.in_octets, dev["last_in_octets"], dt)
                out_bps = _rate(snmp.out_octets, dev["last_out_octets"], dt)
            upd.update(if_index=snmp.if_index, if_name=snmp.if_name,
                       last_in_octets=snmp.in_octets, last_out_octets=snmp.out_octets, last_counter_ts=ts)

    latency = ping.avg_latency_ms
    if status == "DOWN" or not res["ok"]:
        cpu = ram = in_bps = out_bps = None
        if_status = None

    # ---- save current state + history ----
    fields = dict(status=status, down_reason=reason, fail_count=fail_count, latency_ms=latency,
                  packet_loss=ping.packet_loss, cpu_percent=cpu, ram_percent=ram, in_bps=in_bps,
                  out_bps=out_bps, if_status=if_status, last_checked=ts, **upd)
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE devices SET {sets} WHERE id=?", (*fields.values(), dev["id"]))
    conn.execute(
        "INSERT INTO metrics (device_id, ts, status, latency_ms, packet_loss, cpu_percent, ram_percent, in_bps, out_bps)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (dev["id"], ts, status, latency, ping.packet_loss, cpu, ram, in_bps, out_bps),
    )

    # ---- alerts ----
    if status == "DOWN":
        _set_alert(conn, dev, "DOWN", "CRITICAL", f"{who} is DOWN - {reason}", notes)
    elif status == "UP":
        _clear_alert(conn, dev, "DOWN", f"{who} is back UP", notes)
        _check_threshold(conn, dev, "CPU", cpu, config.CPU_THRESHOLD, "%", "CPU usage", notes)
        _check_threshold(conn, dev, "RAM", ram, config.RAM_THRESHOLD, "%", "RAM usage", notes)
        _check_threshold(conn, dev, "PACKET_LOSS", ping.packet_loss, config.LOSS_THRESHOLD, "%", "Packet loss", notes)
        _check_threshold(conn, dev, "LATENCY", latency, config.LATENCY_THRESHOLD_MS, " ms", "Latency", notes)
        if if_status is not None:
            if if_status != "up":
                _set_alert(conn, dev, "INTERFACE", "WARNING",
                           f"{who}: interface {snmp.if_name if snmp else ''} is {if_status}", notes)
            else:
                _clear_alert(conn, dev, "INTERFACE", f"{who}: interface is up again", notes)
    return notes


# --------------------------------------------------------------------------
# Scheduler jobs
# --------------------------------------------------------------------------
def poll_all() -> None:
    if not _poll_lock.acquire(blocking=False):
        log.warning("Previous polling round still running, skipping")
        return
    try:
        with db() as conn:
            devices = [dict(r) for r in conn.execute("SELECT * FROM devices ORDER BY id")]
        if not devices:
            return
        with ThreadPoolExecutor(max_workers=min(20, len(devices))) as pool:
            results = list(pool.map(_safe_poll, devices))

        notifications: List[Notification] = []
        with db() as conn:
            for dev, res in zip(devices, results):
                if res is None:
                    continue
                if conn.execute("SELECT 1 FROM devices WHERE id=?", (dev["id"],)).fetchone() is None:
                    continue  # deleted while we were polling
                notifications += apply_result(conn, dev, res)
        for severity, title, message in notifications:  # after commit: network calls can be slow
            alerts.notify(severity, title, message)
    except Exception:
        log.exception("poll_all failed")
    finally:
        _poll_lock.release()


def purge_old_data() -> None:
    now = time.time()
    with db() as conn:
        conn.execute("DELETE FROM metrics WHERE ts < ?", (now - config.RETENTION_DAYS * 86400,))
        conn.execute("DELETE FROM alerts WHERE ts < ?", (now - 30 * 86400,))
