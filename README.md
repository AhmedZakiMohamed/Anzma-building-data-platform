# Building Data Platform

Simulated commercial-building dataset (HVAC, Network, Access Control, CCTV, Fire Alarm) stored in a normalized PostgreSQL schema, exposed through a documented REST API, and visualized in an operational dashboard with KPIs.

- **Day 2 (Data Engineering & System Integration):** retrieve reliable data by **asset**, **location**, **timestamp** and **system type**.
- **Day 3 (Operational Dashboard & KPIs):** an interactive dashboard with measurable KPIs, historical trends, system filters, status indicators and equipment-health information, all calculated from the underlying data.

## Deliverables by Day

| Day | Deliverable | Location / Details |
|---|---|---|
| 2 | Data ingestion service | `POST /ingest` in `App.py` |
| 2 | Database schema & API docs | `schema.sql`, Swagger UI at `/docs` |
| 2 | Sample records generator | `generateData.py` |
| 2 | Data-flow diagram | `docs/Data-Flow-Diagram.png` |
| 3 | Operational dashboard (8 KPIs, trends, status) | `/dashboard`, `static/`, `dashboard_api.py` |
| 3 | KPI definitions & calculation logic | `KPI_DEFINITIONS.md` |

## Architecture / Data Flow Diagram

![Data Flow Diagram](docs/Data-Flow-Diagram.png)

- **Historical load:** `generateData.py` writes 24h+ of simulated events straight to the database.
- **Live ingestion:** any source can `POST /ingest`; events are validated before being stored (the whole batch is rejected if any asset is unknown).
- **Retrieval:** the REST API joins events with assets, systems and locations so every record comes back with full context.
- **Dashboard:** the page at `/dashboard` calls the `/dashboard/*` endpoints, which compute KPIs, trends and health status from the `events` table at request time.

## ER Diagram

![ER Diagram](docs/ER-Diagram.png)

Design notes:
- Systems and locations are stored once; assets reference them by foreign key (no duplication).
- `events.details` is JSONB for system-specific attributes (e.g. `card_id` for Access, `humidity` for HVAC) without a table per system.
- Index `(asset_id, ts)` serves the most common query: one asset over a time range. Extra indexes on `ts` and `event_type`.
- Timestamps are `TIMESTAMPTZ` (stored in UTC).

## Project Structure

```
.
├── docs/
│   ├── Data-Flow-Diagram.png
│   └── ER-Diagram.png
├── static/
│   ├── dashboard.html       # dashboard page (HTML + JavaScript)
│   ├── style.css            # dashboard CSS styles
│   └── chart.umd.js         # Chart.js, bundled so the dashboard works offline
├── .env                     # local DB credentials (git-ignored, create it yourself)
├── .gitignore
├── App.py                   # FastAPI: ingestion, retrieval, dashboard page
├── dashboard_api.py         # KPI, trend and equipment-health endpoints
├── generateData.py          # simulated data generator / bulk loader
├── KPI_DEFINITIONS.md       # KPI definitions and calculation methods
├── README.md
├── requirements.txt
└── schema.sql               # tables + indexes
```

## Setup

### 1. Database

Create a PostgreSQL database and run `schema.sql` (tables `locations`, `systems`, `assets`, `events` + indexes).

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

No extra packages are needed for the dashboard; Chart.js is included in `static/`.

### 3. Configure the connection

Create a file named `.env` in the project root with the following content:

```
PGHOST=localhost
PGPORT=5432
PGDATABASE=postgres
PGUSER=postgres
PGPASSWORD=your_password_here
```

`.env` is listed in `.gitignore`, so credentials are never committed. Real environment variables (e.g. `export PGPASSWORD=...`) also work and take precedence over `.env`.

### 4. Generate sample data

Default is 48 hours of data (minimum 24h):

```bash
python generateData.py --reset
# options: --hours 24  --seed 42
```

### 5. Run the application

```bash
uvicorn App:app --reload
```

| URL | What it is |
|---|---|
| http://127.0.0.1:8000/docs | Swagger UI: documented API endpoints |
| http://127.0.0.1:8000/dashboard | Operational dashboard |

## Operational Dashboard (Day 3)

Open http://127.0.0.1:8000/dashboard after generating data.

| Section | What it shows |
|---|---|
| System status | One indicator per system (Operational / Warning / Critical) with asset counts. Click a system to filter the whole page to it. |
| Key indicators | 8 KPIs with units and context. Hover a KPI to see its definition. |
| Trends | Events by system, HVAC average temperature, network bandwidth (average and peak), access attempts (granted vs denied). |
| Equipment health | Per-asset status, reason, event/fault/warning/critical counts and last-seen time. Most urgent assets are listed first. |
| Filters | System (all or one) and time window (6, 12, 24 or 48 hours). |

### KPIs

| KPI | Calculation |
|---|---|
| Total events | count of events in the window |
| Critical alerts | count of events with severity `CRITICAL` |
| Network availability (%) | `100 x (1 - total link downtime / (window length x network assets))` |
| Avg bandwidth (Mbps) | mean of `BANDWIDTH_USAGE` values, with the peak shown below |
| Access denial rate (%) | `ACCESS_DENIED / (ACCESS_GRANTED + ACCESS_DENIED)` |
| Avg HVAC temperature (°C) | mean of temperature readings, with the number of high-temperature events below |
| Camera offline incidents | count of `CAMERA_OFFLINE` events |
| Active fire alarms | panels whose latest smoke/reset event is `SMOKE_DETECTED` |

Full definitions, source fields, status rules and notes are in [KPI_DEFINITIONS.md](KPI_DEFINITIONS.md).

### How the numbers are produced

- Nothing is hardcoded: every value is calculated from the `events` table when the page loads or a filter changes.
- The latest event timestamp in the database is used as the reference time ("now"), so the dashboard shows data whenever it was generated.
- Asset status: an open incident (link down, smoke alarm active, camera offline) or a fault event in the last 60 minutes. System status is the worst status among its assets.

### Try a live status change

Ingest a link-down event for the core switch, then click **Refresh**. The Network indicator turns Critical and `SW-CORE-01` appears first in the equipment table:

```bash
TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
curl -X POST http://127.0.0.1:8000/ingest -H "Content-Type: application/json" -d '{
  "events": [{"asset_name": "SW-CORE-01", "ts": "'"$TS"'", "event_type": "LINK_DOWN", "severity": "CRITICAL"}]
}'
```

Resolve it by ingesting a `LINK_UP` event for the same asset.

## Simulated Data

| System | Events generated |
|---|---|
| HVAC | temperature + humidity every 5 min (day/night setpoints), `HIGH_TEMPERATURE` spikes, `FILTER_FAULT` |
| NETWORK | bandwidth every 5 min, `LINK_DOWN`/`LINK_UP` outages with downtime, hourly `UPTIME_REPORT` |
| ACCESS | `ACCESS_GRANTED` / `ACCESS_DENIED` (higher denial at night and on the server-room door), `DOOR_HELD_OPEN`, `DOOR_FORCED` |
| CCTV | `MOTION_DETECTED` (more during working hours), `CAMERA_OFFLINE`/`ONLINE` |
| FIRE_ALARM | hourly `PANEL_HEARTBEAT`, false `SMOKE_DETECTED` + `ALARM_RESET`, `BATTERY_LOW` |

A small share of anomalies is injected on purpose so the data behaves realistically.

Verify volume and time span:

```sql
SELECT s.type, COUNT(*) AS events, MIN(e.ts) AS first_ts, MAX(e.ts) AS last_ts
FROM events e
JOIN assets a  ON a.id = e.asset_id
JOIN systems s ON s.id = a.system_id
GROUP BY s.type ORDER BY s.type;
```

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| POST | `/ingest` | Ingest 1 to 5000 events (validated, atomic) |
| GET | `/events` | Query events; filters below |
| GET | `/assets/{id}/events` | Events for one asset |
| GET | `/assets` | List assets (`system_type`, `location_id`) |
| GET | `/assets/{id}` | Single asset |
| GET | `/locations` | List locations (`building`, `floor`) |
| GET | `/systems` | List systems |
| GET | `/health` | Service/DB check |
| GET | `/dashboard/summary` | KPIs (with definitions) and system status indicators |
| GET | `/dashboard/trends` | Time series for the dashboard charts |
| GET | `/dashboard/equipment-health` | Per-asset health status |

The three `/dashboard/*` endpoints accept `hours` (1 to 168, default 24) and an optional `system_type`.

### `GET /events` filters

| Param | Type | Notes |
|---|---|---|
| `asset_id` | int | |
| `location_id` | int | |
| `system_type` | enum | `HVAC`, `NETWORK`, `ACCESS`, `CCTV`, `FIRE_ALARM` |
| `from`, `to` | ISO-8601 | inclusive range |
| `event_type` | string | e.g. `ACCESS_DENIED` |
| `severity` | enum | `INFO`, `WARNING`, `CRITICAL` |
| `limit`, `offset` | int | default 100, max 1000 |
| `order` | `asc`/`desc` | by timestamp, default `desc` |

Response: `{ "total", "limit", "offset", "items": [ { id, ts, event_type, severity, value, unit, details, asset_id, asset_name, system_type, location_id, building, floor, zone } ] }`

### Examples

```bash
# by system type
curl "http://127.0.0.1:8000/events?system_type=HVAC&limit=5"

# by location
curl "http://127.0.0.1:8000/events?location_id=1"

# by asset + time range (use a range inside your generated data, see first_ts/last_ts above)
curl "http://127.0.0.1:8000/assets/1/events?from=2026-11-01T00:00:00Z&to=2026-11-02T00:00:00Z"

# combined
curl "http://127.0.0.1:8000/events?system_type=ACCESS&severity=WARNING&location_id=1"

# dashboard KPIs for the last 12 hours, network only
curl "http://127.0.0.1:8000/dashboard/summary?hours=12&system_type=NETWORK"

# ingest (timestamp = now, since future timestamps are rejected)
TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
curl -X POST http://127.0.0.1:8000/ingest -H "Content-Type: application/json" -d '{
  "events": [{
    "asset_name": "HVAC-AHU-01",
    "ts": "'"$TS"'",
    "event_type": "temperature_reading",
    "value": 23.4, "unit": "°C",
    "details": {"humidity": 47}
  }]
}'
```

### Ingestion validation rules

- `asset_id` or `asset_name` required and must exist
- `ts` ISO-8601; naive values are treated as UTC; more than 5 minutes in the future is rejected
- `severity` must be `INFO`, `WARNING` or `CRITICAL`
- `event_type` is normalized to `UPPER_SNAKE_CASE`; other characters are rejected
- Invalid batch returns HTTP 422 and nothing is stored

## Known Limitations / Next Steps

- No authentication on the API or the dashboard.
- `generateData.py` loads directly into the DB rather than through `/ingest`.
- The dashboard refreshes on demand (Refresh button or filter change), not automatically.
- KPI and status thresholds (such as the 60-minute status window) are fixed in `dashboard_api.py`.
- Partitioning of `events` by time would be the next step for larger volumes.
