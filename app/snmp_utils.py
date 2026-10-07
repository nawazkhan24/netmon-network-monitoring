"""SNMP v2c polling with pysnmp.

OIDs used (standard / net-snmp MIBs):
  CPU    : UCD-SNMP-MIB  ssCpuIdle, or raw CPU tick counters (ssCpuRaw*)
  RAM    : UCD-SNMP-MIB  memTotalReal, memAvailReal, memBuffer, memCached
  IF     : IF-MIB        ifDescr, ifOperStatus, ifHCInOctets, ifHCOutOctets
Routers/switches from vendors (Cisco, Huawei...) may need vendor specific CPU/RAM OIDs.
"""
import asyncio
import logging
from dataclasses import dataclass
from typing import Dict, Optional

from pysnmp.hlapi.v3arch.asyncio import (
    CommunityData,
    ContextData,
    ObjectIdentity,
    ObjectType,
    SnmpEngine,
    UdpTransportTarget,
    get_cmd,
    walk_cmd,
)

log = logging.getLogger("netmon.snmp")

UCD = "1.3.6.1.4.1.2021"
OID_CPU_IDLE = f"{UCD}.11.11.0"
# raw CPU tick counters: user, nice, system, idle, wait, interrupt, softirq, steal
OID_CPU_RAW = {
    "user": f"{UCD}.11.50.0",
    "nice": f"{UCD}.11.51.0",
    "system": f"{UCD}.11.52.0",
    "idle": f"{UCD}.11.53.0",
    "wait": f"{UCD}.11.54.0",
    "interrupt": f"{UCD}.11.56.0",
    "softirq": f"{UCD}.11.61.0",
    "steal": f"{UCD}.11.64.0",
}
OID_MEM = {
    "total": f"{UCD}.4.5.0",
    "avail": f"{UCD}.4.6.0",
    "buffer": f"{UCD}.4.14.0",
    "cached": f"{UCD}.4.15.0",
}
OID_HR_PROCESSOR_LOAD = "1.3.6.1.2.1.25.3.3.1.2"  # HOST-RESOURCES-MIB (table)
OID_IF_DESCR = "1.3.6.1.2.1.2.2.1.2"
OID_IF_OPER = "1.3.6.1.2.1.2.2.1.8"
OID_IF_HC_IN = "1.3.6.1.2.1.31.1.1.1.6"
OID_IF_HC_OUT = "1.3.6.1.2.1.31.1.1.1.10"
OID_IF_IN = "1.3.6.1.2.1.2.2.1.10"
OID_IF_OUT = "1.3.6.1.2.1.2.2.1.16"

IF_OPER_NAMES = {1: "up", 2: "down", 3: "testing", 4: "unknown", 5: "dormant", 6: "notPresent", 7: "lowerLayerDown"}
_MISSING = ("NoSuchObject", "NoSuchInstance", "EndOfMibView")


@dataclass
class SnmpResult:
    ok: bool = False
    error: Optional[str] = None
    cpu_percent: Optional[float] = None      # set when ssCpuIdle / hrProcessorLoad available
    cpu_idle_ticks: Optional[int] = None     # raw counters (monitor computes % from deltas)
    cpu_total_ticks: Optional[int] = None
    ram_percent: Optional[float] = None
    if_index: Optional[int] = None
    if_name: Optional[str] = None
    if_status: Optional[str] = None
    in_octets: Optional[int] = None
    out_octets: Optional[int] = None


def _value(var_bind) -> Optional[int]:
    """Return an int for numeric values, None for missing OIDs."""
    val = var_bind[1]
    if type(val).__name__ in _MISSING:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


async def _get(engine, auth, target, oids: Dict[str, str]) -> Dict[str, Optional[int]]:
    err_ind, err_status, _idx, var_binds = await get_cmd(
        engine, auth, target, ContextData(), *[ObjectType(ObjectIdentity(o)) for o in oids.values()]
    )
    if err_ind:
        raise RuntimeError(str(err_ind))
    if err_status:
        raise RuntimeError(err_status.prettyPrint())
    return {key: _value(vb) for key, vb in zip(oids.keys(), var_binds)}


async def _walk(engine, auth, target, base_oid: str) -> Dict[int, str]:
    """Walk a table column; returns {last_oid_number: value_as_text}."""
    rows: Dict[int, str] = {}
    async for err_ind, err_status, _idx, var_binds in walk_cmd(
        engine, auth, target, ContextData(), ObjectType(ObjectIdentity(base_oid)), lexicographicMode=False
    ):
        if err_ind or err_status:
            break
        for vb in var_binds:
            rows[int(str(vb[0]).rsplit(".", 1)[1])] = vb[1].prettyPrint()
    return rows


async def _poll(host, port, community, if_index, if_name) -> SnmpResult:
    res = SnmpResult()
    engine = SnmpEngine()
    try:
        auth = CommunityData(community, mpModel=1)  # mpModel=1 -> SNMP v2c
        target = await UdpTransportTarget.create((host, port), timeout=2, retries=1)

        # 1) CPU + RAM (single request). A failure here means the agent is not answering.
        wanted = {"cpu_idle": OID_CPU_IDLE, **{f"raw_{k}": v for k, v in OID_CPU_RAW.items()},
                  **{f"mem_{k}": v for k, v in OID_MEM.items()}}
        data = await _get(engine, auth, target, wanted)
        res.ok = True

        if data["cpu_idle"] is not None:
            res.cpu_percent = round(max(0.0, min(100.0, 100.0 - data["cpu_idle"])), 1)
        elif data["raw_idle"] is not None:
            parts = {k: (data[f"raw_{k}"] or 0) for k in OID_CPU_RAW}
            res.cpu_idle_ticks = parts["idle"] + parts["wait"]
            res.cpu_total_ticks = sum(parts.values())
        else:  # routers: average of hrProcessorLoad
            loads = [int(v) for v in (await _walk(engine, auth, target, OID_HR_PROCESSOR_LOAD)).values() if v.isdigit()]
            if loads:
                res.cpu_percent = round(sum(loads) / len(loads), 1)

        if data["mem_total"] and data["mem_avail"] is not None:
            used = data["mem_total"] - data["mem_avail"] - (data["mem_buffer"] or 0) - (data["mem_cached"] or 0)
            res.ram_percent = round(max(0.0, min(100.0, used / data["mem_total"] * 100)), 1)

        # 2) Interface: auto-select the first non-loopback interface if none chosen yet
        if if_index is None:
            names = await _walk(engine, auth, target, OID_IF_DESCR)
            oper = await _walk(engine, auth, target, OID_IF_OPER)
            real = [(i, n) for i, n in sorted(names.items()) if not n.lower().startswith(("lo", "null", "loopback"))]
            # prefer an interface that is "up" (oper status 1); otherwise the first real one
            pick = next(((i, n) for i, n in real if oper.get(i, "").startswith("up") or oper.get(i) == "1"),
                        real[0] if real else None)
            if pick:
                if_index, if_name = pick
        if if_index is not None:
            res.if_index, res.if_name = if_index, if_name
            iface = await _get(engine, auth, target, {
                "oper": f"{OID_IF_OPER}.{if_index}",
                "hc_in": f"{OID_IF_HC_IN}.{if_index}",
                "hc_out": f"{OID_IF_HC_OUT}.{if_index}",
            })
            if iface["oper"] is not None:
                res.if_status = IF_OPER_NAMES.get(iface["oper"], "unknown")
            res.in_octets, res.out_octets = iface["hc_in"], iface["hc_out"]
            if res.in_octets is None:  # very old devices without 64-bit counters
                old = await _get(engine, auth, target, {"in": f"{OID_IF_IN}.{if_index}", "out": f"{OID_IF_OUT}.{if_index}"})
                res.in_octets, res.out_octets = old["in"], old["out"]
    except Exception as exc:
        res.ok = False
        res.error = str(exc) or exc.__class__.__name__
        log.warning("SNMP poll of %s:%s failed: %s", host, port, res.error)
    finally:
        closer = getattr(engine, "close_dispatcher", None)
        if closer:
            try:
                closer()
            except Exception:
                pass
    return res


def snmp_poll(host: str, port: int, community: str, if_index: Optional[int] = None,
              if_name: Optional[str] = None) -> SnmpResult:
    """Blocking wrapper (called from worker threads)."""
    return asyncio.run(_poll(host, port, community, if_index, if_name))
