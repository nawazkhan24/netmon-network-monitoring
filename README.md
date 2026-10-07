# NetMon: Network Monitoring and Alert System

A prototype network operations (NOC style) tool that monitors routers, switches and servers using **ICMP ping** and **SNMP v2c**, shows live status and graphs on a web dashboard, and sends **Telegram / Email alerts** when a device goes down or crosses a threshold.

> This is a learning prototype built during an internship. It is not a production system and is not affiliated with any company.

## Features

| Area | What it does |
|---|---|
| Device status | UP / DOWN detection using ping and SNMP agent reachability |
| Ping monitoring | Latency and packet loss (several pings per check) |
| SNMP monitoring | CPU, RAM, interface status, in/out bandwidth (64-bit counters) |
| Alerts | Device down, CPU, RAM, packet loss, latency, interface down, plus "resolved" messages |
| Alert hygiene | N failures in a row before DOWN, one alert per incident (no spam) |
| Dashboard | Live table, status strip, per-device graphs (15 min to 24 h), alert history |
| Notifications | Telegram bot and/or Email (SMTP) |
| API | REST API with automatic docs at `/docs` |
| Test lab | Docker containers acting as fake SNMP routers / switches / servers |

## Architecture

```
 Browser (static/index.html + Chart.js)
        |  REST (JSON, refresh every 5 s)
        v
 FastAPI  (app/main.py)  <------------------------------+
        |                                               |
        v                                               |
 SQLite  (netmon.db)  <--- writes ---  Monitor engine (app/monitor.py)
  devices | metrics | alerts | alert_state        runs every N seconds (APScheduler)
                                         |
                       +-----------------+------------------+
                       v                 v                  v
                 ping_utils.py     snmp_utils.py       alerts.py
                 (OS ping cmd)     (pysnmp, v2c)    (Telegram / Email)
                       |                 |
                       v                 v
                 Network devices (routers, switches, servers)
```

**One polling round:** read devices, check all of them in parallel threads, save results and history, compare with thresholds, create alerts, then send notifications.

## Project structure

```
netmon/
├── app/
│   ├── __init__.py
│   ├── main.py          FastAPI app, REST API, scheduler start/stop
│   ├── monitor.py       polling engine, status logic, alert rules
│   ├── ping_utils.py    ping command + output parsing (Windows/Linux/Mac)
│   ├── snmp_utils.py    SNMP polling (CPU, RAM, interface, traffic)
│   ├── alerts.py        Telegram and Email sending
│   ├── database.py      SQLite schema and helpers
│   └── config.py        settings from .env
├── static/
│   └── index.html       dashboard (HTML + CSS + JS + Chart.js)
├── docker/snmpd/        Dockerfile + config for the fake SNMP devices
├── scripts/seed_demo.py adds demo devices to a running NetMon
├── tests/               automated tests (pytest)
├── docs/DEMO_SCRIPT.md  step by step demo for mentor / HR
├── docker-compose.yml   3 fake SNMP devices (ports 1161-1163)
├── run.py               starts the application
├── requirements.txt     libraries
├── requirements-dev.txt libraries + pytest
├── .env.example         settings template (copy to .env)
└── .gitignore
```

## Setup

You need **Python 3.10+** (check with `python --version`). Docker is only needed for the test devices.

```bash
# 1. go to the project folder
cd netmon

# 2. create and activate a virtual environment
python -m venv venv
venv\Scripts\activate            # Windows
source venv/bin/activate         # Mac / Linux

# 3. install libraries
pip install -r requirements.txt

# 4. create your settings file
copy .env.example .env           # Windows
cp .env.example .env             # Mac / Linux

# 5. run
python run.py
```

Open **http://127.0.0.1:8000**. API docs: **http://127.0.0.1:8000/docs**.

On Windows, if `python` is not found, use `py`. On Mac/Linux use `python3`.

## Try it without any hardware

1. In the dashboard click **Add a device**, add name `Localhost`, address `127.0.0.1`. It turns **UP** within a few seconds.
2. Add `Fake-Router` with address `10.255.255.1`. After two failed checks it turns **DOWN**.

## Test with fake SNMP devices (Docker)

```bash
docker compose up -d --build        # starts 3 SNMP agents on ports 1161, 1162, 1163
python scripts/seed_demo.py         # adds them to NetMon (needs NetMon running)
```

Or add them manually: address `127.0.0.1`, tick **Monitor with SNMP**, port `1161`, community `public`.

Useful demo commands:

```bash
docker stop netmon-router-sim       # device goes DOWN (alert sent)
docker start netmon-router-sim      # device recovers (RESOLVED alert)

# generate network traffic so the bandwidth graph moves
docker exec -d netmon-router-sim ping -s 1400 -i 0.01 -c 30000 switch-sim

# raise CPU (the SNMP agent reports host-wide CPU); undo with: docker restart netmon-server-sim
docker exec -d netmon-server-sim sh -c "while :; do :; done"
```

For an easy CPU alert demo, set `CPU_THRESHOLD=15` in `.env` and restart NetMon.

Note: a Docker device on `127.0.0.1` is considered DOWN when its **SNMP agent** stops answering, even though the loopback ping still works.

## Telegram alerts (10 minutes)

1. In Telegram search **@BotFather**, send `/newbot`, follow the steps, copy the **token**.
2. Open your new bot and send it any message (for example "hi").
3. Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` and copy the number after `"chat":{"id":`.
4. Put both in `.env`:
   ```
   TELEGRAM_BOT_TOKEN=123456:ABC...
   TELEGRAM_CHAT_ID=987654321
   ```
5. Restart NetMon and press **Send test alert** in the dashboard.

Email works the same way using the `SMTP_*` and `EMAIL_TO` values (for Gmail create an *App Password*).

## How the status and alerts work

- A device is **UP** when ping succeeds and (if SNMP is enabled) the SNMP agent answers.
- It becomes **DOWN** after `FAIL_THRESHOLD` failed checks in a row (default 2), with a reason: "No ping response" or "SNMP agent not responding".
- Threshold alerts (CPU, RAM, packet loss, latency, interface) fire **once** when the condition starts and send a **RESOLVED** message when it clears.
- Bandwidth is calculated from the change in interface byte counters between two polls.
- Graph history is kept for `RETENTION_DAYS` (default 7).

## REST API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/summary` | counts, active alerts, thresholds |
| GET | `/api/devices` | all devices with latest values |
| POST | `/api/devices` | add a device |
| DELETE | `/api/devices/{id}` | delete a device and its history |
| GET | `/api/devices/{id}/metrics?minutes=60` | graph data |
| GET | `/api/alerts?limit=50` | alert history |
| POST | `/api/test-notification` | send a test Telegram / email |
| GET | `/api/health` | health check |

## Run the tests

```bash
pip install -r requirements-dev.txt
pytest
```

## Upload to GitHub

```bash
git init
git add .
git commit -m "NetMon: SNMP network monitoring and alert system"
git branch -M main
git remote add origin https://github.com/<your-username>/netmon-network-monitoring.git
git push -u origin main
```

Before pushing, check that `.env` and `*.db` do **not** appear in `git status` (they are in `.gitignore`). Never commit tokens or passwords.

## Limitations (be honest about these in your presentation)

- Prototype for learning: no login on the dashboard, so run it on a trusted network only.
- SNMP v2c only (community string, no encryption). v3 is the secure option for real networks.
- CPU/RAM use standard Linux (net-snmp) OIDs. Vendor devices such as Cisco or Huawei need vendor specific OIDs.
- IPv6 addresses are not supported in the add-device form.
- SQLite and a single server process; large networks would need a time-series database and distributed pollers.
- Devices that block ICMP will show as DOWN.

## Future scope

SNMP v3, user login with roles, SNMP traps, auto discovery of devices, vendor OID profiles, topology map, Grafana/Prometheus export, escalation rules and maintenance windows.

## Tech stack

Python, FastAPI, APScheduler, pysnmp, SQLite, Chart.js, Docker (test lab), pytest.
