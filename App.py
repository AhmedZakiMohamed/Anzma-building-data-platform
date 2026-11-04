"""
Building Data API  (FastAPI + PostgreSQL)

Run:
    pip install -r requirements.txt
    uvicorn App:app --reload
Docs (Swagger): http://127.0.0.1:8000/docs
"""
import os
from dotenv import load_dotenv
load_dotenv()
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

from fastapi import FastAPI, HTTPException, Query
from psycopg2.extras import Json, RealDictCursor, execute_values
from psycopg2.pool import SimpleConnectionPool
from pydantic import BaseModel, Field, field_validator, model_validator

DB = dict(
    host=os.getenv("PGHOST", "localhost"),
    port=os.getenv("PGPORT", "5432"),
    dbname=os.getenv("PGDATABASE", "postgres"),
    user=os.getenv("PGUSER", "postgres"),
    password=os.getenv("PGPASSWORD", ""),
)

SystemType = Literal["HVAC", "NETWORK", "ACCESS", "CCTV", "FIRE_ALARM"]
Severity = Literal["INFO", "WARNING", "CRITICAL"]

app = FastAPI(
    title="Building Data API",
    description="Ingestion and retrieval of HVAC, Network, Access Control, "
                "CCTV and Fire Alarm events. Filter by asset, location, "
                "timestamp and system type.",
    version="1.0.0",
)

pool: Optional[SimpleConnectionPool] = None


@app.on_event("startup")
def startup():
    global pool
    pool = SimpleConnectionPool(1, 10, **DB)


@app.on_event("shutdown")
def shutdown():
    if pool:
        pool.closeall()


@contextmanager
def db():
    """Yield a dict-cursor; commit on success, rollback on error."""
    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


class EventIn(BaseModel):
    asset_id: Optional[int] = Field(None, description="Asset id (or use asset_name)")
    asset_name: Optional[str] = Field(None, description="Asset name, e.g. HVAC-AHU-01")
    ts: datetime = Field(..., description="ISO-8601 timestamp; naive values are treated as UTC")
    event_type: str = Field(..., min_length=2, max_length=50, examples=["TEMPERATURE_READING"])
    severity: Severity = "INFO"
    value: Optional[float] = None
    unit: Optional[str] = Field(None, max_length=20)
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("event_type")
    @classmethod
    def normalise_type(cls, v: str) -> str:
        v = v.strip().upper().replace(" ", "_")
        if not v.replace("_", "").isalnum():
            raise ValueError("event_type must contain only letters, digits and underscores")
        return v

    @field_validator("ts")
    @classmethod
    def utc_and_not_future(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        if v > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise ValueError("ts is in the future")
        return v

    @model_validator(mode="after")
    def need_asset_ref(self):
        if self.asset_id is None and not self.asset_name:
            raise ValueError("provide asset_id or asset_name")
        return self


class IngestBatch(BaseModel):
    events: list[EventIn] = Field(..., min_length=1, max_length=5000)


@app.post("/ingest", tags=["Ingestion"], status_code=201,
          summary="Ingest one or more events")
def ingest(batch: IngestBatch):
    """Validates the batch, resolves assets, and inserts atomically.
    If any event references an unknown asset, nothing is stored (HTTP 422)."""
    with db() as cur:
        cur.execute("SELECT id, name FROM assets")
        rows = cur.fetchall()
        by_id = {r["id"] for r in rows}
        by_name = {r["name"]: r["id"] for r in rows}

        to_insert, errors = [], []
        for i, e in enumerate(batch.events):
            aid = e.asset_id if e.asset_id is not None else by_name.get(e.asset_name)
            if aid is None or aid not in by_id:
                errors.append({"index": i, "error": "unknown asset",
                               "asset_id": e.asset_id, "asset_name": e.asset_name})
                continue
            to_insert.append((aid, e.ts, e.event_type, e.severity,
                              e.value, e.unit, Json(e.details)))

        if errors:
            raise HTTPException(422, detail={"message": "batch rejected", "errors": errors})

        execute_values(
            cur,
            "INSERT INTO events (asset_id, ts, event_type, severity, value, unit, details) VALUES %s",
            to_insert,
        )
    return {"inserted": len(to_insert)}


@app.get("/systems", tags=["Reference"])
def list_systems():
    with db() as cur:
        cur.execute("SELECT id, type FROM systems ORDER BY id")
        return cur.fetchall()


@app.get("/locations", tags=["Reference"])
def list_locations(building: Optional[str] = None, floor: Optional[int] = None):
    where, params = [], []
    if building:
        where.append("building = %s"); params.append(building)
    if floor is not None:
        where.append("floor = %s"); params.append(floor)
    sql = "SELECT id, building, floor, zone FROM locations"
    if where:
        sql += " WHERE " + " AND ".join(where)
    with db() as cur:
        cur.execute(sql + " ORDER BY id", params)
        return cur.fetchall()


ASSET_SELECT = """
    SELECT a.id, a.name, a.model, a.status,
           s.type AS system_type,
           l.id AS location_id, l.building, l.floor, l.zone
    FROM assets a
    JOIN systems s   ON s.id = a.system_id
    JOIN locations l ON l.id = a.location_id
"""


@app.get("/assets", tags=["Reference"])
def list_assets(system_type: Optional[SystemType] = None,
                location_id: Optional[int] = None):
    where, params = [], []
    if system_type:
        where.append("s.type = %s"); params.append(system_type)
    if location_id is not None:
        where.append("l.id = %s"); params.append(location_id)
    sql = ASSET_SELECT + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY a.id"
    with db() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


@app.get("/assets/{asset_id}", tags=["Reference"])
def get_asset(asset_id: int):
    with db() as cur:
        cur.execute(ASSET_SELECT + " WHERE a.id = %s", (asset_id,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, "asset not found")
    return row


EVENT_SELECT = """
    SELECT e.id, e.ts, e.event_type, e.severity, e.value, e.unit, e.details,
           a.id AS asset_id, a.name AS asset_name,
           s.type AS system_type,
           l.id AS location_id, l.building, l.floor, l.zone
    FROM events e
    JOIN assets a    ON a.id = e.asset_id
    JOIN systems s   ON s.id = a.system_id
    JOIN locations l ON l.id = a.location_id
"""
EVENT_COUNT = EVENT_SELECT.split("FROM", 1)[1]


def query_events(*, asset_id, location_id, system_type, event_type, severity,
                 ts_from, ts_to, limit, offset, order):
    if ts_from and ts_to and ts_from > ts_to:
        raise HTTPException(400, "'from' must be earlier than 'to'")

    where, params = [], []
    for cond, val in [
        ("a.id = %s", asset_id), ("l.id = %s", location_id),
        ("s.type = %s", system_type), ("e.event_type = %s", event_type.upper() if event_type else None),
        ("e.severity = %s", severity), ("e.ts >= %s", ts_from), ("e.ts <= %s", ts_to),
    ]:
        if val is not None:
            where.append(cond); params.append(val)
    w = (" WHERE " + " AND ".join(where)) if where else ""

    with db() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM" + EVENT_COUNT + w, params)
        total = cur.fetchone()["n"]
        cur.execute(EVENT_SELECT + w + f" ORDER BY e.ts {order.upper()}, e.id LIMIT %s OFFSET %s",
                    params + [limit, offset])
        items = cur.fetchall()
    return {"total": total, "limit": limit, "offset": offset, "items": items}


@app.get("/events", tags=["Events"], summary="Query events with filters")
def get_events(
    asset_id: Optional[int] = None,
    location_id: Optional[int] = None,
    system_type: Optional[SystemType] = None,
    event_type: Optional[str] = None,
    severity: Optional[Severity] = None,
    ts_from: Optional[datetime] = Query(None, alias="from", description="Start (inclusive), ISO-8601"),
    ts_to: Optional[datetime] = Query(None, alias="to", description="End (inclusive), ISO-8601"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    order: Literal["asc", "desc"] = "desc",
):
    return query_events(asset_id=asset_id, location_id=location_id, system_type=system_type,
                        event_type=event_type, severity=severity, ts_from=ts_from,
                        ts_to=ts_to, limit=limit, offset=offset, order=order)


@app.get("/assets/{asset_id}/events", tags=["Events"], summary="Events of one asset")
def get_asset_events(
    asset_id: int,
    event_type: Optional[str] = None,
    severity: Optional[Severity] = None,
    ts_from: Optional[datetime] = Query(None, alias="from"),
    ts_to: Optional[datetime] = Query(None, alias="to"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    order: Literal["asc", "desc"] = "desc",
):
    with db() as cur:
        cur.execute("SELECT 1 FROM assets WHERE id = %s", (asset_id,))
        if not cur.fetchone():
            raise HTTPException(404, "asset not found")
    return query_events(asset_id=asset_id, location_id=None, system_type=None,
                        event_type=event_type, severity=severity, ts_from=ts_from,
                        ts_to=ts_to, limit=limit, offset=offset, order=order)


@app.get("/health", tags=["Meta"])
def health():
    with db() as cur:
        cur.execute("SELECT COUNT(*) AS events FROM events")
        return {"status": "ok", "events": cur.fetchone()["events"]}

from dashboard_api import create_router
app.include_router(create_router(db))

import os
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


@app.get("/dashboard", include_in_schema=False)
def dashboard_page():
    return FileResponse(os.path.join(BASE_DIR, "static", "dashboard.html"))    
from alerts_api import create_alerts_router
app.include_router(create_alerts_router(db))