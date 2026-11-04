# Day 4: Alerting, Anomaly Detection and AI Explanations

Day 4 adds an alert engine on top of the Day 2 data platform: threshold rules, a statistical anomaly detector, severity classification, six simulated fault scenarios, and AI-generated incident explanations with recommended corrective actions.

**Success criterion:** every alert and every recommended action must be traceable to actual simulated measurements, not to unsupported AI assumptions.

## How traceability is achieved

1. The **alert engine** decides that something is wrong. It stores, with each alert, the evidence that triggered it: the event IDs, their timestamps and values, the threshold that was crossed and the measured facts (for example peak value, duration, whether the fan was reported off).
2. The **local LLM never queries the database.** It receives only that evidence package, plus a list of allowed causes and actions that were already filtered by the measured facts.
3. The LLM chooses causes and actions **by ID from the playbook**; the wording of causes and actions comes from the playbook (`alert_config.py`), not from the model.
4. The model's summary is **validated**: every number, time and date must appear in the evidence, and every citation (`[E1]`, `[E2]`...) must refer to a real evidence event.
5. If the model is unavailable or fails validation, a **deterministic explanation** built from the same evidence and playbook is stored instead. It is validated the same way.

```
events table
    |
    v
Alert engine  ->  alerts table (severity, evidence, facts, thresholds)
(rules + anomaly + compound)
    |
    v
Evidence package  ->  Ollama (qwen2.5:7b)  ->  validation  ->  explanation + actions
                                                   |
                                                   +-- failed / unavailable -> deterministic fallback
```

## Files

| File | Purpose |
|---|---|
| `schema_day4.sql` | `alerts` table (with evidence and explanation as JSONB) and `scenario_runs` (ground truth for the evaluation) |
| `alert_config.py` | All thresholds, the rule catalogue and the cause/action playbooks |
| `alert_engine.py` | Rule evaluation, compound rule, robust z-score anomaly detection, evidence building |
| `ai_explainer.py` | Evidence package, Ollama call, grounding validation, normalization of citation formatting, deterministic fallback |
| `alerts_api.py` | REST endpoints for running the engine, listing alerts and generating explanations |
| `inject_scenarios.py` | Injects the six fault scenarios and records their ground truth |
| `evaluate_alerts.py` | Measures detection, latency and evidence integrity |
| `measure_explain.py` | Explains one alert per rule and reports grounding results |
| `review_explain.txt` | Generated explanations saved for manual spot-checking |
| `test_citations.py` | Automated unit tests verifying strict citation parsing, fallback behavior, and text normalization. |

## Setup and run

### 1. Create the Day 4 tables

Run `schema_day4.sql` in TablePlus (or `psql`). The tables are created only if they do not exist.

### 2. Enable the alert endpoints

Add to the end of `App.py`:

```python
from alerts_api import create_alerts_router

app.include_router(create_alerts_router(db))
```

### 3. Start the local model (for AI explanations)

```bash
ollama pull qwen2.5:7b
export OLLAMA_MODEL=qwen2.5:7b
```

Defaults: `OLLAMA_HOST=http://localhost:11434`, `OLLAMA_MODEL=llama3.2:3b`. Without Ollama running, explanations are still produced by the deterministic fallback.

### 4. Generate data, inject the scenarios, run the engine

```bash
python generateData.py --reset
python inject_scenarios.py
uvicorn App:app --reload
```

In another terminal:

```bash
curl -X POST "http://127.0.0.1:8000/alerts/run?hours=48"
```

### 5. Evaluate

```bash
python evaluate_alerts.py
```

### 6. Explain an alert

Find an alert ID with `GET /alerts`, then:

```bash
curl -X POST "http://127.0.0.1:8000/alerts/1/explain"
curl -X POST "http://127.0.0.1:8000/alerts/1/explain?use_llm=false"
```

The second command skips the model and returns the deterministic explanation.

## Alert rules (threshold-based)

All values are defined in `alert_config.py`.

| Rule | Condition | Severity |
|---|---|---|
| `HVAC_HIGH_TEMP` | temperature above 29 °C (25 °C in the server room) | WARNING; CRITICAL above 32 °C (30 °C server room) |
| `HVAC_FILTER_FAULT` | the unit reports a filter fault | WARNING |
| `NET_LINK_DOWN` | `LINK_DOWN` event (with the paired `LINK_UP` as evidence) | CRITICAL |
| `NET_LINK_FLAPPING` | 3 or more `LINK_DOWN` events within 30 min on one asset | CRITICAL |
| `NET_BANDWIDTH_HIGH` | 3 or more consecutive readings above 900 Mbps | WARNING; CRITICAL if the mean is 980 Mbps or more |
| `NET_PACKET_LOSS` | 3 or more consecutive readings with loss above 1% | WARNING; CRITICAL if the mean is 3% or more |
| `ACC_DOOR_FORCED` | `DOOR_FORCED` event | CRITICAL |
| `ACC_DOOR_HELD_OPEN` | door held open longer than 120 s | WARNING |
| `ACC_DENIAL_SPIKE` | 5 or more denied attempts within 15 min on one door | WARNING; CRITICAL at 10 or more |
| `CCTV_OFFLINE_LONG` | camera offline for 15 min or more | WARNING |
| `CCTV_AFTER_HOURS_MOTION` | 5 or more after-hours motion events within 30 min in the server room | WARNING |
| `SEC_BLIND_SPOT_ACCESS` | compound: door activity outside working hours at the same location while the camera is offline | CRITICAL |
| `FIRE_SMOKE` | `SMOKE_DETECTED` event (the reset is attached as evidence when present) | CRITICAL |
| `FIRE_BATTERY_LOW` | battery low event or heartbeat battery below 20% | WARNING; CRITICAL below 10% |

Sustained-condition rules (several consecutive readings) are used where single readings are noisy, so that one-off spikes do not raise an alert.

### Severity classification

- **CRITICAL:** safety, security or outage conditions that need immediate action (fire, forced door, link down, severe overheating, security blind spot).
- **WARNING:** degradation or an early sign of a problem (high load, a filter fault, a long camera outage, an anomaly).
- `INFO` is part of the scale but is not currently emitted by the rules.

### When an alert is "detected"

Each alert stores `detected_ts`: the moment its condition was first satisfied (for example the third consecutive reading above the threshold, or the 15th minute of a camera outage). The evaluation measures latency from the start of a scenario to this timestamp.

## Anomaly detection (statistical)

**Method:** robust z-score using the median and the median absolute deviation (MAD), which are not distorted by the anomalies themselves.

```
z = 0.6745 x (value - median) / MAD
```

- Applied to HVAC temperature and network bandwidth, per asset.
- A separate baseline is kept for working hours (08:00 to 18:00, UTC+2) and off hours, because both temperature setpoints and traffic differ between them.
- A reading is flagged when |z| is 4 or more **and** the deviation from the median is at least 1.0 °C (temperature) or 80 Mbps (bandwidth). The minimum deviation prevents tiny absolute changes from being flagged when the MAD is very small.
- An anomaly alert needs 3 or more consecutive flagged readings in the same direction (high or low).
- The anomaly alert records the baseline median, the MAD and the peak z-score as evidence.

The anomaly detector gives an earlier warning than the fixed thresholds for gradual faults (see the evaluation).

## Fault scenarios

Six scenarios are injected by `inject_scenarios.py`. Their ground truth (asset, time window, expected rules) is stored only in `scenario_runs`; the injected events carry no scenario label, so the engine cannot use it.

| # | Scenario | What is injected | Expected rules |
|---|---|---|---|
| S1 | Server-room cooling failure | The cooling unit's fan is reported off and the temperature climbs from 20 °C to about 34 °C over 90 min | `HVAC_TEMP_ANOMALY`, `HVAC_HIGH_TEMP` |
| S2 | Core switch saturation and link flapping | Traffic near 1 Gbps with 1.5% to 4.5% packet loss while the uplink drops four times in 40 min | `NET_LINK_FLAPPING`, `NET_BANDWIDTH_HIGH`, `NET_PACKET_LOSS` |
| S3 | Badge brute force at night | 14 denied attempts with 3 unknown cards at the server-room door in 14 min | `ACC_DENIAL_SPIKE` |
| S4 | Camera blind spot | The server-room camera loses signal for 35 min at night while the door records entries and a held-open event | `CCTV_OFFLINE_LONG`, `SEC_BLIND_SPOT_ACCESS`, `ACC_DOOR_HELD_OPEN` |
| S5 | Fire panel on battery with smoke alarm | Mains power lost, battery drains from 24% to 7%, then a smoke detector trips | `FIRE_BATTERY_LOW`, `FIRE_SMOKE` |
| S6 | Clogged filter and temperature drift | A filter fault is logged and the meeting-room temperature creeps from 23.8 °C to about 30 °C over 2 hours | `HVAC_TEMP_ANOMALY`, `HVAC_HIGH_TEMP`, `HVAC_FILTER_FAULT` |

Injection replaces the baseline readings of the affected asset inside each scenario window. To restore the original baseline, run `generateData.py --reset` again.

## Evidence

Each alert stores an evidence object, available through `GET /alerts/{id}`:

```
{
  "thresholds": {"warn": 25.0, "crit": 30.0, "unit": "°C"},
  "facts": {"max_value": 33.87, "sustained": true, "fan_off": true, "zone": "Server Room", ...},
  "event_count": 19,
  "events": [
    {"ref": "E1", "event_id": 4312, "ts": "...", "event_type": "HIGH_TEMPERATURE",
     "value": 30.4, "unit": "°C", "details": {"fan_on": false, "setpoint": 20.0}},
    ...
  ]
}
```

- `event_id` points to a row in the `events` table; `value` and `ts` are copied from that row.
- At most 10 events are stored per alert (first, last, the peak and an even sample), and `event_count` states how many events triggered it.
- Only whitelisted event attributes are kept in the evidence, so the model sees nothing else.

## AI explanations

`POST /alerts/{id}/explain` returns the alert with its explanation:

| Field | Content |
|---|---|
| `summary` | Short description with event citations such as `[E1]` |
| `probable_causes` | Causes from the playbook, each with `supported_by`, the measured facts that justify it |
| `recommended_actions` | Actions from the playbook, ordered by priority, each with `because`, the measured fact that justifies it |
| `evidence_refs` | The evidence events available for the alert |
| `confidence` | `low`, `medium` or `high` |
| `source` | `OLLAMA` or `FALLBACK` |
| `grounding` | Validation result, violations, number of attempts, normalization log, and any LLM error |

### What the model may and may not do

- It sees only the evidence package and the filtered lists of allowed causes and actions.
- A cause or action is offered only when its condition holds for the measured facts. For example, "fan failure" is offered only when the unit reported its fan as off, and "denial from unknown cards" only when most denied cards were unknown.
- It cannot add causes or actions of its own: IDs that are not in the offered lists are ignored.

### Grounding validation

A summary is accepted only if:

- it contains at least one citation and every citation refers to an existing evidence event;
- every date and time in it appears in the evidence;
- every number in it appears in the evidence (within rounding);
- square brackets are used only for event references; cause and action IDs are not allowed in brackets.

If validation fails, the model is asked once more with the list of violations. If it fails again, or Ollama cannot be reached, the deterministic fallback is stored and the reason is recorded under `grounding`.

### Normalization of citation formatting

Smaller local models sometimes vary the citation format. A deterministic helper (`normalize_summary`) fixes formatting only: it converts `(E1)` to `[E1]` and removes malformed tags such as `[C1]` or `[FAN_FAILURE]` that do not correspond to an evidence event. It never changes numbers, times or measurements, and every correction is recorded under `grounding.normalized`.

## Evaluation results

Reproduce with `python evaluate_alerts.py` and `measure_explain.py`. Figures come from a run of the full pipeline on the generated 48-hour dataset with the six scenarios injected.

### Detection

| Metric | Result |
|---|---|
| Scenarios detected | 6 of 6 |
| Expected rules fired | 14 of 14 |
| Mean time to first expected alert | 12.9 min after the scenario started |

Detection latency per rule (minutes after the scenario started):

| Scenario | Rule: latency |
|---|---|
| S1 | `HVAC_TEMP_ANOMALY`: 25, `HVAC_HIGH_TEMP`: 45 |
| S2 | `NET_BANDWIDTH_HIGH`: 10, `NET_PACKET_LOSS`: 10, `NET_LINK_FLAPPING`: 22 |
| S3 | `ACC_DENIAL_SPIKE`: 5 |
| S4 | `SEC_BLIND_SPOT_ACCESS`: 8, `ACC_DOOR_HELD_OPEN`: 12, `CCTV_OFFLINE_LONG`: 15 |
| S5 | `FIRE_BATTERY_LOW`: 20, `FIRE_SMOKE`: 62 |
| S6 | `HVAC_FILTER_FAULT`: 10, `HVAC_TEMP_ANOMALY`: 45, `HVAC_HIGH_TEMP`: 100 |

**Early warning from the anomaly detector:** it flagged S1 20 minutes and S6 55 minutes before the fixed temperature threshold was crossed.

### Evidence traceability

| Metric | Result |
|---|---|
| Distinct evidence events referenced by alerts | 93 |
| Found in the `events` table | 93 of 93 |
| Matching event type, value and timestamp | 93 of 93 |
| Alerts with at least one evidence event | 32 of 32 |

### AI explanations (qwen2.5:7b via Ollama)

| Metric | Result |
|---|---|
| Alerts explained | 14 (one per rule that produced alerts; the other 2 of the 16 rules raised no alert on this dataset) |
| Passed grounding validation | 14 of 14, on the first attempt, with no fallback needed |
| Summaries that needed citation-format normalization | about 5 of 14, all logged under `grounding.normalized` |

Figures can vary slightly between runs because of the model's output.

### Alerts outside the injected scenarios

The generator itself produces background faults (link outages, temperature spikes, forced doors, false smoke alarms, long camera outages). The engine alerts on them, and each of these alerts is backed by an explicit event in the data. They are reported separately by `evaluate_alerts.py` and are not counted as scenario detections.

## API reference

| Method | Endpoint | Description |
|---|---|---|
| POST | `/alerts/run?hours=` | Run all rules and the anomaly detector over the last `hours` (1 to 168); replaces the stored alerts for that window |
| GET | `/alerts` | List alerts. Filters: `severity`, `asset_id`, `system_type`, `rule_id`, `method`, `from`, `to`, `limit`, `offset` |
| GET | `/alerts/{id}` | One alert with evidence, explanation and recommendations |
| POST | `/alerts/{id}/explain?use_llm=` | Generate and store the explanation (`use_llm=false` skips the model) |

The window ends at the latest event timestamp in the database, as for the dashboard.

## Known limitations

- **Validation is syntactic, not semantic.** It guarantees that numbers, times, dates and citations come from the evidence. It cannot detect a sentence that is wrongly phrased (for example saying no threshold was crossed on a critical alert, or treating a filter flag as covering the whole period). Spot-check the generated explanations (`review_explain.txt`) before relying on them. Numbers written as words are not checked.
- **Run-to-run variation:** the model's wording, and therefore the normalization count, can differ slightly between runs.
- **Batch evaluation, not streaming:** the engine runs on demand over a time window (`POST /alerts/run`); it does not process events as they arrive.
- **Recall is measured, precision is not.** Detection is measured on the injected scenarios. The alerts raised on the generator's background faults are listed but not scored as true or false positives.
- **Synthetic data:** thresholds are tuned for this simulated building and would need calibration against real sensors.
- **Fixed configuration:** thresholds, the working-hours calendar (08:00 to 18:00, UTC+2) and the 7-day baseline history are set in `alert_config.py`.
- **No incident grouping:** several rules can fire for one underlying fault (for example the anomaly and threshold alerts in S1); they are listed separately.
- **Baseline contamination:** injected scenarios replace baseline readings and can influence the baseline statistics; the median/MAD method limits this effect.
