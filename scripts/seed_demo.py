"""Adds demo devices to a running NetMon:   python scripts/seed_demo.py"""
import sys

import requests

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
DEMO = [
    {"name": "Router-Sim", "ip": "127.0.0.1", "device_type": "router", "snmp_enabled": True, "snmp_port": 1161},
    {"name": "Switch-Sim", "ip": "127.0.0.1", "device_type": "switch", "snmp_enabled": True, "snmp_port": 1162},
    {"name": "Server-Sim", "ip": "127.0.0.1", "device_type": "server", "snmp_enabled": True, "snmp_port": 1163},
    {"name": "Google-DNS", "ip": "8.8.8.8", "device_type": "other"},
    {"name": "Unreachable-Test", "ip": "10.255.255.1", "device_type": "router"},
]

existing = {d["name"] for d in requests.get(f"{BASE}/api/devices", timeout=10).json()}
for dev in DEMO:
    if dev["name"] in existing:
        print("skip (already exists):", dev["name"])
        continue
    r = requests.post(f"{BASE}/api/devices", json=dev, timeout=10)
    print("added" if r.ok else f"failed ({r.status_code})", dev["name"])
