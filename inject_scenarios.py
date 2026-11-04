"""
inject_scenarios.py
Injects six simulated fault scenarios into the existing event history and records
their ground truth (asset, time window, expected rules) in scenario_runs.
The ground truth lives only in scenario_runs; the events carry no scenario label,
so the alert engine cannot use it.

Usage:
    python inject_scenarios.py            # inject (replaces a previous injection)
    python inject_scenarios.py --reset    # remove injected events and ground truth

Baseline readings of the affected asset inside each scenario window are replaced
by the scenario readings. To restore the baseline completely, run
generateData.py --reset again.
"""
import argparse
import os
import random
from datetime import timedelta

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import Json, execute_values

load_dotenv()

DB = dict(
    host=os.getenv("PGHOST", "localhost"),
    port=os.getenv("PGPORT", "5432"),
    dbname=os.getenv("PGDATABASE", "postgres"),
    user=os.getenv("PGUSER", "postgres"),
    password=os.getenv("PGPASSWORD", ""),
)
LOCAL_UTC_OFFSET = 2
INSERT = ("INSERT INTO events (asset_id, ts, event_type, severity, value, unit, details) "
          "VALUES %s RETURNING id")


def latest_local(ref, hour, minute, duration_min):
    """Latest UTC start whose local time of day is hour:minute and whose end is before ref."""
    ref_local = ref + timedelta(hours=LOCAL_UTC_OFFSET)
    cand = ref_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    while cand + timedelta(minutes=duration_min + 5) > ref_local:
        cand -= timedelta(days=1)
    return cand - timedelta(hours=LOCAL_UTC_OFFSET)


def ev(aid, ts, etype, sev="INFO", value=None, unit=None, details=None):
    return (aid, ts, etype, sev, value, unit, Json(details or {}))


def clear_range(cur, aid, types, start, end):
    cur.execute("DELETE FROM events WHERE asset_id = %s AND event_type = ANY(%s) AND ts >= %s AND ts <= %s",
                (aid, list(types), start, end))


def insert(cur, rows):
    return [r[0] for r in execute_values(cur, INSERT, rows, fetch=True)]


def reset(cur):
    cur.execute("SELECT event_ids FROM scenario_runs")
    ids = [i for (arr,) in cur.fetchall() for i in arr]
    if ids:
        cur.execute("DELETE FROM events WHERE id = ANY(%s)", (ids,))
    cur.execute("DELETE FROM scenario_runs")
    return len(ids)


def record(cur, key, name, desc, assets, start, end, rules, ids):
    cur.execute("""INSERT INTO scenario_runs
                   (scenario_key, name, description, asset_ids, start_ts, end_ts, expected_rules, event_ids)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (key, name, desc, assets, start, end, rules, ids))


def s1_cooling_failure(cur, ref, rng, aid):
    dur = 90
    start = latest_local(ref, 14, 0, dur)
    end = start + timedelta(minutes=dur)
    clear_range(cur, aid["HVAC-CRAC-01"], ("TEMPERATURE_READING", "HIGH_TEMPERATURE"), start, end)
    rows = []
    for i in range(dur // 5 + 1):
        ts = start + timedelta(minutes=5 * i)
        temp = 20 + 14 * (i / (dur / 5)) ** 1.2 + rng.gauss(0, 0.3)
        fan_on = i < 4
        details = {"humidity": round(45 + rng.gauss(0, 1.5), 1), "setpoint": 20.0, "fan_on": fan_on}
        if temp > 30:
            sev = "CRITICAL" if temp > 32 else "WARNING"
            rows.append(ev(aid["HVAC-CRAC-01"], ts, "HIGH_TEMPERATURE", sev, round(temp, 2), "°C", details))
        else:
            rows.append(ev(aid["HVAC-CRAC-01"], ts, "TEMPERATURE_READING", "INFO", round(temp, 2), "°C", details))
    ids = insert(cur, rows)
    record(cur, "S1", "Server-room cooling failure",
           "Cooling unit fan stops and server-room temperature climbs from 20 to about 34 °C over 90 minutes.",
           [aid["HVAC-CRAC-01"]], start, end, ["HVAC_TEMP_ANOMALY", "HVAC_HIGH_TEMP"], ids)


def s2_network_saturation(cur, ref, rng, aid):
    dur = 40
    start = latest_local(ref, 11, 0, dur)
    end = start + timedelta(minutes=dur)
    sw = aid["SW-CORE-01"]
    clear_range(cur, sw, ("BANDWIDTH_USAGE",), start, end)
    outages = [(start + timedelta(minutes=m), rng.randint(120, 180)) for m in (6, 14, 22, 30)]
    rows = []
    for t0, secs in outages:
        rows.append(ev(sw, t0, "LINK_DOWN", "CRITICAL", None, None, {"port": "Gi1/0/48"}))
        rows.append(ev(sw, t0 + timedelta(seconds=secs), "LINK_UP", "INFO", None, None,
                       {"port": "Gi1/0/48", "downtime_sec": secs}))
    for i in range(dur // 5 + 1):
        ts = start + timedelta(minutes=5 * i)
        if any(t0 <= ts <= t0 + timedelta(seconds=s) for t0, s in outages):
            continue
        mbps = rng.uniform(940, 1010)
        rows.append(ev(sw, ts, "BANDWIDTH_USAGE", "WARNING", round(mbps, 1), "Mbps",
                       {"packet_loss_pct": round(rng.uniform(1.5, 4.5), 2)}))
    ids = insert(cur, rows)
    record(cur, "S2", "Core switch saturation and link flapping",
           "Core switch traffic stays near 1 Gbps with 1.5-4.5% packet loss while the uplink drops four times in 40 minutes.",
           [sw], start, end, ["NET_LINK_FLAPPING", "NET_BANDWIDTH_HIGH", "NET_PACKET_LOSS"], ids)


def s3_badge_brute_force(cur, ref, rng, aid):
    dur = 15
    start = latest_local(ref, 1, 0, dur)
    end = start + timedelta(minutes=dur)
    door = aid["DOOR-SERVER-01"]
    cards = ["CARD-9034", "CARD-9112", "CARD-9475"]
    rows = []
    for i in range(14):
        rows.append(ev(door, start + timedelta(minutes=i, seconds=rng.randint(0, 40)), "ACCESS_DENIED", "WARNING",
                       None, None, {"card_id": rng.choice(cards), "direction": "IN",
                                    "reason": rng.choice(["UNKNOWN_CARD", "UNKNOWN_CARD", "NO_PERMISSION"])}))
    ids = insert(cur, rows)
    record(cur, "S3", "Night-time badge brute force at server-room door",
           "Fourteen denied attempts with three unknown cards at the server-room door in 14 minutes, outside working hours.",
           [door], start, end, ["ACC_DENIAL_SPIKE"], ids)


def s4_camera_blind_spot(cur, ref, rng, aid):
    dur = 35
    start = latest_local(ref, 3, 30, dur)
    end = start + timedelta(minutes=dur)
    cam, door = aid["CAM-SERVER-01"], aid["DOOR-SERVER-01"]
    clear_range(cur, cam, ("MOTION_DETECTED",), start, end)
    m = timedelta(minutes=1)
    rows = [ev(cam, start, "CAMERA_OFFLINE", "WARNING", None, None, {"reason": "NO_SIGNAL"}),
            ev(cam, end, "CAMERA_ONLINE", "INFO"),
            ev(door, start + 8 * m, "ACCESS_GRANTED", "INFO", None, None, {"card_id": "CARD-1007", "direction": "IN"}),
            ev(door, start + 9 * m, "ACCESS_GRANTED", "INFO", None, None, {"card_id": "CARD-1007", "direction": "IN"}),
            ev(door, start + 12 * m, "DOOR_HELD_OPEN", "WARNING", 150, "seconds"),
            ev(door, start + 30 * m, "ACCESS_GRANTED", "INFO", None, None, {"card_id": "CARD-1007", "direction": "OUT"})]
    ids = insert(cur, rows)
    record(cur, "S4", "Camera blind spot with after-hours door activity",
           "Server-room camera loses signal for 35 minutes at night while the door records entries and a held-open event.",
           [cam, door], start, end, ["CCTV_OFFLINE_LONG", "SEC_BLIND_SPOT_ACCESS", "ACC_DOOR_HELD_OPEN"], ids)


def s5_fire_panel(cur, ref, rng, aid):
    dur = 80
    start = latest_local(ref, 16, 0, dur)
    end = start + timedelta(minutes=dur)
    fa = aid["FA-PANEL-02"]
    clear_range(cur, fa, ("PANEL_HEARTBEAT",), start, end)
    m = timedelta(minutes=1)
    rows = [ev(fa, start + 10 * i * m, "PANEL_HEARTBEAT", "INFO", None, None,
               {"battery_pct": round(24 - 17 * i / 6, 1), "mains_ok": False}) for i in range(7)]
    rows += [ev(fa, start + 45 * m, "BATTERY_LOW", "WARNING", 12.0, "%", {"mains_ok": False}),
             ev(fa, start + 62 * m, "SMOKE_DETECTED", "CRITICAL", None, None, {"detector": "SD-07"}),
             ev(fa, start + 72 * m, "ALARM_RESET", "INFO")]
    ids = insert(cur, rows)
    record(cur, "S5", "Fire panel on battery with smoke alarm",
           "Mains power is lost at a fire panel, the battery drains from 24% to 7%, then a smoke detector trips.",
           [fa], start, end, ["FIRE_BATTERY_LOW", "FIRE_SMOKE"], ids)


def s6_filter_clog(cur, ref, rng, aid):
    dur = 120
    start = latest_local(ref, 11, 30, dur)
    end = start + timedelta(minutes=dur)
    ahu = aid["HVAC-AHU-02"]
    clear_range(cur, ahu, ("TEMPERATURE_READING", "HIGH_TEMPERATURE"), start, end)
    rows = []
    for i in range(dur // 5 + 1):
        temp = 23.8 + 6.5 * (i / (dur / 5)) ** 1.1 + rng.gauss(0, 0.3)
        rows.append(ev(ahu, start + timedelta(minutes=5 * i), "TEMPERATURE_READING", "INFO", round(temp, 2), "°C",
                       {"humidity": round(46 + rng.gauss(0, 1.5), 1), "setpoint": 23.0, "fan_on": True}))
    rows.append(ev(ahu, start + timedelta(minutes=10), "FILTER_FAULT", "WARNING", None, None,
                   {"note": "Differential pressure high"}))
    ids = insert(cur, rows)
    record(cur, "S6", "Clogged filter and slow temperature drift",
           "A filter fault is logged and the meeting-room temperature creeps from 23.8 to about 30 °C over two hours.",
           [ahu], start, end, ["HVAC_TEMP_ANOMALY", "HVAC_HIGH_TEMP", "HVAC_FILTER_FAULT"], ids)


SCENARIOS = [s1_cooling_failure, s2_network_saturation, s3_badge_brute_force,
             s4_camera_blind_spot, s5_fire_panel, s6_filter_clog]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="remove injected events and ground truth only")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    conn = psycopg2.connect(**DB)
    try:
        with conn, conn.cursor() as cur:
            removed = reset(cur)
            print(f"Removed {removed} previously injected events")
            if args.reset:
                return
            cur.execute("SELECT MAX(ts) FROM events")
            ref = cur.fetchone()[0]
            if ref is None:
                raise SystemExit("No events found. Run generateData.py first.")
            cur.execute("SELECT name, id FROM assets")
            aid = dict(cur.fetchall())
            rng = random.Random(args.seed)
            for fn in SCENARIOS:
                fn(cur, ref, rng, aid)
            cur.execute("SELECT scenario_key, name, start_ts, end_ts FROM scenario_runs ORDER BY scenario_key")
            for key, name, s, e in cur.fetchall():
                print(f"  {key}  {name}: {s:%Y-%m-%d %H:%M} -> {e:%H:%M} UTC")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
