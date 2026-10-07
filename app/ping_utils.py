"""Ping a host with the operating system's ping command (works on Windows, Linux, macOS)."""
import logging
import platform
import re
import subprocess
from dataclasses import dataclass
from typing import List, Optional

log = logging.getLogger("netmon.ping")

# Reply lines look like:
#   Linux/Mac: "64 bytes from 8.8.8.8: icmp_seq=1 ttl=117 time=12.3 ms"
#   Windows  : "Reply from 8.8.8.8: bytes=32 time=12ms TTL=117"   (or "time<1ms")
_REPLY = re.compile(r"ttl[=:]", re.IGNORECASE)
_TIME = re.compile(r"time\s*[=<]\s*([\d.]+)", re.IGNORECASE)


@dataclass
class PingResult:
    reachable: bool
    avg_latency_ms: Optional[float]
    packet_loss: float  # percent, 0-100


def build_command(host: str, count: int) -> List[str]:
    system = platform.system().lower()
    if system == "windows":
        return ["ping", "-n", str(count), "-w", "1000", host]
    if system == "darwin":  # macOS: -W is in milliseconds
        return ["ping", "-c", str(count), "-i", "0.3", "-W", "1000", host]
    return ["ping", "-c", str(count), "-i", "0.3", "-W", "1", host]  # Linux


def parse_output(output: str, sent: int) -> PingResult:
    replies = len(_REPLY.findall(output))
    times = [float(t) for t in _TIME.findall(output)]
    replies = min(replies, sent)
    loss = round((sent - replies) / sent * 100, 1) if sent else 100.0
    avg = round(sum(times) / len(times), 2) if times else None
    return PingResult(reachable=replies > 0, avg_latency_ms=avg, packet_loss=loss)


def ping_host(host: str, count: int = 4) -> PingResult:
    """Send `count` pings. Never raises; returns 100% loss if ping fails to run."""
    cmd = build_command(host, count)
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=count * 2 + 5, errors="ignore"
        )
        return parse_output(proc.stdout, count)
    except FileNotFoundError:
        log.error("'ping' command not found on this system")
    except subprocess.TimeoutExpired:
        log.warning("ping to %s timed out", host)
    except Exception as exc:  # pragma: no cover
        log.error("ping error for %s: %s", host, exc)
    return PingResult(False, None, 100.0)
