"""
evaluate_alerts.py
Measures the alert engine against the injected scenarios (scenario_runs) and checks
that every piece of alert evidence exists unchanged in the events table.

Usage:
    python inject_scenarios.py
    (call POST /alerts/run?hours=48, or python -c "..." as documented)
    python evaluate_alerts.py
"""
import os
import sys
from collections import Counter, defaultdict
from datetime import timedelta
from statistics import mean

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor

load_dotenv()

DB = dict(
    host=os.getenv("PGHOST", "localhost"),
    port=os.getenv("PGPORT", "5432"),
    dbname=os.getenv("PGDATABASE", "postgres"),
    user=os.getenv("PGUSER", "postgres"),
    password=os.getenv("PGPASSWORD", ""),
)
TOLERANCE = timedelta(minutes=10)


def main():
    conn = psycopg2.connect(**DB)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM scenario_runs ORDER BY scenario_key")
    scenarios = cur.fetchall()
    cur.execute("SELECT id, rule_id, severity, detection_method, asset_id, first_ts, last_ts, detected_ts, "
                "evidence, explanation_source, explanation FROM alerts ORDER BY first_ts")
    alerts = cur.fetchall()
    if not scenarios or not alerts:
        sys.exit("Nothing to evaluate: run inject_scenarios.py and POST /alerts/run first.")

    def related(a, s):
        return (a["asset_id"] in s["asset_ids"] and a["first_ts"] <= s["end_ts"] + TOLERANCE
                and a["last_ts"] >= s["start_ts"] - TOLERANCE)

    print("## Scenario detection\n")
    print("| Scenario | Expected rules | Rules fired | Detected | Latency per rule (min after start) |")
    print("|---|---|---|---|---|")
    detected, expected_total, expected_hit, first_latencies = 0, 0, 0, []
    per_rule_latency = defaultdict(dict)
    for s in scenarios:
        rel = [a for a in alerts if related(a, s)]
        lat = {}
        for a in rel:
            m = (a["detected_ts"] - s["start_ts"]).total_seconds() / 60
            lat[a["rule_id"]] = min(m, lat.get(a["rule_id"], m))
        hit = [r for r in s["expected_rules"] if r in lat]
        expected_total += len(s["expected_rules"])
        expected_hit += len(hit)
        if hit:
            detected += 1
            first_latencies.append(min(lat[r] for r in hit))
        per_rule_latency[s["scenario_key"]] = lat
        lat_txt = ", ".join(f"{r}: {lat[r]:.0f}" for r in s["expected_rules"] if r in lat) or "-"
        extra = sorted(set(lat) - set(s["expected_rules"]))
        fired = ", ".join(sorted(lat)) + (f" (unexpected: {', '.join(extra)})" if extra else "")
        print(f"| {s['scenario_key']} {s['name']} | {', '.join(s['expected_rules'])} | {fired or '-'} | "
              f"{'yes' if hit else 'NO'} | {lat_txt} |")

    print(f"\nScenarios detected: {detected}/{len(scenarios)}")
    print(f"Expected rules fired: {expected_hit}/{expected_total} ({100 * expected_hit / expected_total:.0f}%)")
    print(f"Mean time to first expected alert: {mean(first_latencies):.1f} min after scenario start")

    print("\n## Early warning from anomaly detection\n")
    for s in scenarios:
        lat = per_rule_latency[s["scenario_key"]]
        for anomaly, threshold in (("HVAC_TEMP_ANOMALY", "HVAC_HIGH_TEMP"),):
            if anomaly in lat and threshold in lat:
                print(f"- {s['scenario_key']}: {anomaly} at {lat[anomaly]:.0f} min, {threshold} at {lat[threshold]:.0f} min "
                      f"({lat[threshold] - lat[anomaly]:.0f} min earlier warning)")

    unrelated = [a for a in alerts if not any(related(a, s) for s in scenarios)]
    print(f"\n## Alerts outside the injected scenarios\n\nTotal alerts: {len(alerts)}; "
          f"outside scenarios: {len(unrelated)}\n")
    print("| Rule | Count | Severity |")
    print("|---|---|---|")
    by_rule = Counter(a["rule_id"] for a in unrelated)
    for rule, n in by_rule.most_common():
        sevs = Counter(a["severity"] for a in unrelated if a["rule_id"] == rule)
        print(f"| {rule} | {n} | {', '.join(f'{k} x{v}' for k, v in sevs.items())} |")

    print("\n## Evidence traceability\n")
    ids = {e["event_id"]: e for a in alerts for e in a["evidence"]["events"]}
    cur.execute("SELECT id, ts, event_type, value FROM events WHERE id = ANY(%s)", (list(ids),))
    db = {r["id"]: r for r in cur.fetchall()}
    missing = [i for i in ids if i not in db]
    mismatch = [i for i, e in ids.items() if i in db and (
        db[i]["event_type"] != e["event_type"] or
        (e["value"] is None) != (db[i]["value"] is None) or
        (e["value"] is not None and abs(db[i]["value"] - e["value"]) > 1e-9) or
        abs((db[i]["ts"].timestamp()) - __import__("datetime").datetime.fromisoformat(e["ts"]).timestamp()) > 1e-3)]
    print(f"Distinct evidence events referenced: {len(ids)}")
    print(f"Found in the events table: {len(ids) - len(missing)}/{len(ids)}")
    print(f"Matching type, value and timestamp: {len(ids) - len(missing) - len(mismatch)}/{len(ids)}")
    print(f"Alerts with at least one evidence event: {sum(1 for a in alerts if a['evidence']['events'])}/{len(alerts)}")

    explained = [a for a in alerts if a["explanation"]]
    if explained:
        src = Counter(a["explanation_source"] for a in explained)
        passed = sum(1 for a in explained if a["explanation"]["grounding"]["passed"])
        print(f"\n## AI explanations\n\nExplained alerts: {len(explained)}/{len(alerts)} "
              f"({', '.join(f'{k} {v}' for k, v in src.items())})")
        print(f"Explanations that passed grounding validation: {passed}/{len(explained)}")
    conn.close()


if __name__ == "__main__":
    main()
