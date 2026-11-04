"""
generateData.py
Generates simulated commercial-building events (HVAC, NETWORK, ACCESS, CCTV,
FIRE_ALARM) and loads them into PostgreSQL.

Usage:
    pip install psycopg2-binary
    python generateData.py                 # 48 hours of data ending now
    python generateData.py --hours 24
    python generateData.py --reset         # wipe events first, then regenerate

DB connection comes from env vars (defaults in DB below):
    PGHOST PGPORT PGDATABASE PGUSER PGPASSWORD
"""
import argparse
import math
import os
from dotenv import load_dotenv
load_dotenv()
import random
from collections import Counter
from datetime import datetime, timedelta, timezone

import psycopg2
from psycopg2.extras import Json, execute_values

DB = dict(
    host=os.getenv("PGHOST", "localhost"),
    port=os.getenv("PGPORT", "5432"),
    dbname=os.getenv("PGDATABASE", "postgres"),
    user=os.getenv("PGUSER", "postgres"),
    password=os.getenv("PGPASSWORD", ""),
)

LOCAL_UTC_OFFSET = 2

SYSTEMS = ["HVAC", "NETWORK", "ACCESS", "CCTV", "FIRE_ALARM"]

LOCATIONS = [
    ("Tower-A", 1, "Lobby"),
    ("Tower-A", 1, "Server Room"),
    ("Tower-A", 2, "Open Office"),
    ("Tower-A", 3, "Meeting Rooms"),
    ("Tower-A", 4, "Executive Floor"),
]

ASSETS = [
    ("HVAC-AHU-01", "HVAC", ("Tower-A", 2, "Open Office"), "Carrier 39M"),
    ("HVAC-AHU-02", "HVAC", ("Tower-A", 3, "Meeting Rooms"), "Carrier 39M"),
    ("HVAC-CRAC-01", "HVAC", ("Tower-A", 1, "Server Room"), "Vertiv Liebert"),
    ("SW-CORE-01", "NETWORK", ("Tower-A", 1, "Server Room"), "Cisco C9300"),
    ("SW-ACCESS-02", "NETWORK", ("Tower-A", 2, "Open Office"), "Cisco C9200"),
    ("DOOR-LOBBY-01", "ACCESS", ("Tower-A", 1, "Lobby"), "HID Signo"),
    ("DOOR-SERVER-01", "ACCESS", ("Tower-A", 1, "Server Room"), "HID Signo"),
    ("DOOR-EXEC-01", "ACCESS", ("Tower-A", 4, "Executive Floor"), "HID Signo"),
    ("CAM-LOBBY-01", "CCTV", ("Tower-A", 1, "Lobby"), "Axis P3245"),
    ("CAM-OFFICE-01", "CCTV", ("Tower-A", 2, "Open Office"), "Axis P3245"),
    ("CAM-SERVER-01", "CCTV", ("Tower-A", 1, "Server Room"), "Hikvision DS-2CD"),
    ("FA-PANEL-01", "FIRE_ALARM", ("Tower-A", 1, "Lobby"), "Honeywell NFS2"),
    ("FA-PANEL-02", "FIRE_ALARM", ("Tower-A", 3, "Meeting Rooms"), "Honeywell NFS2"),
]


def occupancy(ts):
    """~1.0 during working hours (08-18 local), low otherwise."""
    h = (ts + timedelta(hours=LOCAL_UTC_OFFSET)).hour
    return 1.0 if 8 <= h < 18 else 0.1


def steps(start, end, minutes):
    t = start
    while t < end:
        yield t
        t += timedelta(minutes=minutes)


def ev(asset_id, ts, etype, sev="INFO", value=None, unit=None, details=None):
    return (asset_id, ts, etype, sev, value, unit, Json(details or {}))


def gen_hvac(aid, name, start, end, rng):
    rows = []
    is_crac = "CRAC" in name
    for t in steps(start, end, 5):
        occ = occupancy(t)
        if is_crac:
            setpoint, temp = 20.0, 20.0 + rng.gauss(0, 0.4)
        else:
            setpoint = 23.0 if occ > 0.5 else 27.0
            temp = setpoint + rng.gauss(0, 0.5) + (0.8 if occ > 0.5 else 0)
        humidity = 45 + 8 * math.sin(t.hour / 24 * 2 * math.pi) + rng.gauss(0, 2)
        details = {"humidity": round(humidity, 1), "setpoint": setpoint,
                   "fan_on": occ > 0.5 or is_crac}

        if rng.random() < 0.006:
            temp += rng.uniform(5, 12)
            sev = "CRITICAL" if temp > 32 else "WARNING"
            rows.append(ev(aid, t, "HIGH_TEMPERATURE", sev, round(temp, 2), "°C", details))
        else:
            rows.append(ev(aid, t, "TEMPERATURE_READING", "INFO", round(temp, 2), "°C", details))

    if rng.random() < 0.5:
        t = start + (end - start) * rng.random()
        rows.append(ev(aid, t, "FILTER_FAULT", "WARNING", None, None,
                       {"note": "Differential pressure high"}))
    return rows


def gen_network(aid, name, start, end, rng):
    rows = []
    outages = []
    for _ in range(rng.randint(1, 3)):
        s = start + (end - start) * rng.random()
        outages.append((s, s + timedelta(minutes=rng.randint(3, 15))))

    last_restart = start - timedelta(days=rng.randint(10, 60))
    for s, e in outages:
        rows.append(ev(aid, s, "LINK_DOWN", "CRITICAL", None, None, {"port": "Gi1/0/48"}))
        rows.append(ev(aid, e, "LINK_UP", "INFO", None, None,
                       {"port": "Gi1/0/48", "downtime_sec": int((e - s).total_seconds())}))

    for t in steps(start, end, 5):
        if any(s <= t <= e for s, e in outages):
            continue
        base = 200 + 600 * occupancy(t)
        mbps = max(1.0, base + rng.gauss(0, 40))
        sev = "WARNING" if mbps > 900 else "INFO"
        rows.append(ev(aid, t, "BANDWIDTH_USAGE", sev, round(mbps, 1), "Mbps",
                       {"packet_loss_pct": round(abs(rng.gauss(0.1, 0.1)), 2)}))
        if t.minute == 0:
            up = int((t - last_restart).total_seconds())
            rows.append(ev(aid, t, "UPTIME_REPORT", "INFO", up, "seconds"))
    return rows


def gen_access(aid, name, start, end, rng):
    rows = []
    employees = [f"CARD-{1000 + i}" for i in range(60)]
    is_server = "SERVER" in name
    for t in steps(start, end, 1):
        occ = occupancy(t)
        p = (0.02 if is_server else 0.15) * occ + 0.002
        if rng.random() >= p:
            continue
        deny_p = 0.08 if occ > 0.5 else 0.35
        if is_server:
            deny_p += 0.15
        card = rng.choice(employees) if rng.random() > 0.05 else f"CARD-{rng.randint(9000, 9999)}"
        direction = rng.choice(["IN", "OUT"])
        if rng.random() < deny_p:
            rows.append(ev(aid, t, "ACCESS_DENIED", "WARNING", None, None,
                           {"card_id": card, "direction": direction,
                            "reason": rng.choice(["NO_PERMISSION", "EXPIRED_CARD", "UNKNOWN_CARD"])}))
        else:
            rows.append(ev(aid, t, "ACCESS_GRANTED", "INFO", None, None,
                           {"card_id": card, "direction": direction}))

    for _ in range(rng.randint(0, 2)):
        t = start + (end - start) * rng.random()
        if rng.random() < 0.5:
            rows.append(ev(aid, t, "DOOR_HELD_OPEN", "WARNING", rng.randint(30, 180), "seconds"))
        else:
            rows.append(ev(aid, t, "DOOR_FORCED", "CRITICAL", None, None, {"alarm": True}))
    return rows


def gen_cctv(aid, name, start, end, rng):
    rows = []
    for t in steps(start, end, 1):
        occ = occupancy(t)
        p = 0.10 * occ + 0.01
        if rng.random() < p:
            score = round(rng.uniform(20, 100), 1)
            rows.append(ev(aid, t, "MOTION_DETECTED", "INFO", score, "%",
                           {"zone": rng.choice(["A", "B", "C"]),
                            "after_hours": occ < 0.5}))
    if rng.random() < 0.6:
        s = start + (end - start) * rng.random()
        e = s + timedelta(minutes=rng.randint(2, 30))
        rows.append(ev(aid, s, "CAMERA_OFFLINE", "WARNING", None, None, {"reason": "NO_SIGNAL"}))
        rows.append(ev(aid, e, "CAMERA_ONLINE", "INFO"))
    return rows


def gen_fire(aid, name, start, end, rng):
    rows = []
    for t in steps(start, end, 60):
        rows.append(ev(aid, t, "PANEL_HEARTBEAT", "INFO", None, None,
                       {"battery_pct": round(rng.uniform(92, 100), 1)}))
    if rng.random() < 0.7:
        s = start + (end - start) * rng.random()
        rows.append(ev(aid, s, "SMOKE_DETECTED", "CRITICAL", None, None,
                       {"detector": f"SD-{rng.randint(1, 20):02d}", "false_alarm": True}))
        rows.append(ev(aid, s + timedelta(minutes=10), "ALARM_RESET", "INFO"))
    if rng.random() < 0.5:
        s = start + (end - start) * rng.random()
        rows.append(ev(aid, s, "BATTERY_LOW", "WARNING", round(rng.uniform(15, 25), 1), "%"))
    return rows


GENERATORS = {"HVAC": gen_hvac, "NETWORK": gen_network, "ACCESS": gen_access,
              "CCTV": gen_cctv, "FIRE_ALARM": gen_fire}


def seed_reference(cur):
    execute_values(cur, "INSERT INTO systems (type) VALUES %s ON CONFLICT (type) DO NOTHING",
                   [(s,) for s in SYSTEMS])
    execute_values(cur, "INSERT INTO locations (building, floor, zone) VALUES %s "
                        "ON CONFLICT (building, floor, zone) DO NOTHING", LOCATIONS)

    cur.execute("SELECT type, id FROM systems")
    sys_id = dict(cur.fetchall())
    cur.execute("SELECT building, floor, zone, id FROM locations")
    loc_id = {(b, f, z): i for b, f, z, i in cur.fetchall()}

    execute_values(
        cur,
        "INSERT INTO assets (name, system_id, location_id, model) VALUES %s "
        "ON CONFLICT (name) DO NOTHING",
        [(n, sys_id[s], loc_id[l], m) for n, s, l, m in ASSETS],
    )
    cur.execute("SELECT a.id, a.name, s.type FROM assets a JOIN systems s ON s.id = a.system_id")
    return cur.fetchall()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=48, help="hours of history (min 24)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--reset", action="store_true", help="delete existing events first")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    start = end - timedelta(hours=max(args.hours, 24))

    conn = psycopg2.connect(**DB)
    try:
        with conn, conn.cursor() as cur:
            assets = seed_reference(cur)
            if args.reset:
                cur.execute("TRUNCATE events RESTART IDENTITY")

            all_rows, per_system = [], Counter()
            for aid, name, stype in assets:
                rows = GENERATORS[stype](aid, name, start, end, rng)
                per_system[stype] += len(rows)
                all_rows.extend(rows)

            execute_values(
                cur,
                "INSERT INTO events (asset_id, ts, event_type, severity, value, unit, details) "
                "VALUES %s",
                all_rows, page_size=5000,
            )
    finally:
        conn.close()

    print(f"Window: {start.isoformat()}  ->  {end.isoformat()}")
    print(f"Assets: {len(assets)}   Events inserted: {sum(per_system.values())}")
    for s, n in sorted(per_system.items()):
        print(f"  {s:<11} {n}")


if __name__ == "__main__":
    main()