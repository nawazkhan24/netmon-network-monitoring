"""FastAPI application: REST API + dashboard + background scheduler."""
import logging
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from . import alerts, config
from .database import db, init_db
from .monitor import poll_all, purge_old_data

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("netmon")

scheduler = BackgroundScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    scheduler.add_job(poll_all, "interval", seconds=config.POLL_INTERVAL, max_instances=1,
                      coalesce=True, next_run_time=datetime.now(), id="poll")
    scheduler.add_job(purge_old_data, "interval", hours=1, id="purge")
    scheduler.start()
    log.info("NetMon started: polling every %ss | telegram=%s email=%s", config.POLL_INTERVAL,
             alerts.telegram_configured(), alerts.email_configured())
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="NetMon - Network Monitoring & Alert System", version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")

HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]*$")  # must start with a letter/digit (blocks "-flag" tricks)


class DeviceIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    ip: str = Field(min_length=1, max_length=253, description="IPv4 address or hostname")
    device_type: Literal["router", "switch", "server", "other"] = "server"
    snmp_enabled: bool = False
    snmp_port: int = Field(161, ge=1, le=65535)
    community: str = Field("public", min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.\-@#]+$")

    @field_validator("name")
    @classmethod
    def clean_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name cannot be empty")
        return v

    @field_validator("ip")
    @classmethod
    def check_host(cls, v: str) -> str:
        v = v.strip()
        if not HOST_RE.match(v):
            raise ValueError("enter a valid IPv4 address or hostname")
        return v


PUBLIC_COLUMNS = ("id, name, ip, device_type, snmp_enabled, snmp_port, if_name, status, down_reason, latency_ms, "
                  "packet_loss, cpu_percent, ram_percent, in_bps, out_bps, if_status, last_checked")


@app.get("/", include_in_schema=False)
def dashboard():
    return FileResponse(config.STATIC_DIR / "index.html")


@app.get("/api/health")
def health():
    return {"status": "ok", "poll_interval_seconds": config.POLL_INTERVAL}


@app.get("/api/summary")
def summary():
    with db() as conn:
        counts = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) n FROM devices GROUP BY status")}
        active = conn.execute("SELECT COUNT(*) FROM alert_state WHERE active=1").fetchone()[0]
    total = sum(counts.values())
    return {"total": total, "up": counts.get("UP", 0), "down": counts.get("DOWN", 0),
            "unknown": counts.get("UNKNOWN", 0), "active_alerts": active,
            "thresholds": {"cpu": config.CPU_THRESHOLD, "ram": config.RAM_THRESHOLD,
                           "loss": config.LOSS_THRESHOLD, "latency_ms": config.LATENCY_THRESHOLD_MS}}


@app.get("/api/devices")
def list_devices():
    with db() as conn:
        return [dict(r) for r in conn.execute(f"SELECT {PUBLIC_COLUMNS} FROM devices ORDER BY id")]


@app.post("/api/devices", status_code=201)
def add_device(device: DeviceIn):
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO devices (name, ip, device_type, snmp_enabled, snmp_port, community, created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (device.name, device.ip, device.device_type, int(device.snmp_enabled), device.snmp_port,
             device.community, time.time()),
        )
        new_id = cur.lastrowid
    return {"id": new_id, "message": "Device added. First results appear within a few seconds."}


@app.delete("/api/devices/{device_id}")
def delete_device(device_id: int):
    with db() as conn:
        cur = conn.execute("DELETE FROM devices WHERE id=?", (device_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Device not found")
    return {"message": "Device deleted"}


@app.get("/api/devices/{device_id}/metrics")
def device_metrics(device_id: int, minutes: int = Query(60, ge=1, le=1440)):
    with db() as conn:
        if conn.execute("SELECT 1 FROM devices WHERE id=?", (device_id,)).fetchone() is None:
            raise HTTPException(404, "Device not found")
        rows = conn.execute(
            "SELECT ts, status, latency_ms, packet_loss, cpu_percent, ram_percent, in_bps, out_bps "
            "FROM metrics WHERE device_id=? AND ts >= ? ORDER BY ts",
            (device_id, time.time() - minutes * 60),
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/alerts")
def list_alerts(limit: int = Query(50, ge=1, le=500)):
    with db() as conn:
        rows = conn.execute(
            "SELECT id, device_id, device_name, kind, severity, message, ts FROM alerts ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/test-notification")
def test_notification():
    """Sends a test message so you can verify Telegram / Email settings."""
    sent = alerts.notify("INFO", "NetMon test", "This is a test notification from NetMon.")
    return {"telegram_configured": alerts.telegram_configured(),
            "email_configured": alerts.email_configured(), "sent": sent}
