# 5-minute demo script (for mentor / HR)

Before the demo: `docker compose up -d --build`, start NetMon, run `python scripts/seed_demo.py`.
For a faster demo set `POLL_INTERVAL_SECONDS=10` in `.env`.

1. **Show the dashboard.** Point out the status strip (up / down / pending), the device table, CPU/RAM meters and live traffic.
2. **Open a device.** Click Router-Sim: show the bandwidth, CPU/RAM, latency and packet-loss graphs.
3. **Explain the checks.** Ping gives UP/DOWN, latency and packet loss. SNMP gives CPU, RAM, interface status and traffic counters.
4. **Break something live.** Run `docker stop netmon-router-sim`. Within about 30 seconds the device turns DOWN (reason: SNMP agent not responding) and a Telegram message arrives.
5. **Fix it.** Run `docker start netmon-router-sim`. The device returns to UP and a RESOLVED alert is sent.
6. **Threshold alert.** Set `CPU_THRESHOLD=15` in `.env`, restart, and run the CPU load command from the README. A WARNING alert appears once.
7. **Show the code structure** (`app/monitor.py`, `app/snmp_utils.py`) and `http://127.0.0.1:8000/docs` (auto-generated API docs).
8. **Close with limitations and future scope** (see README).
