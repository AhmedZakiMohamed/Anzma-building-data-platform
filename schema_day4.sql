CREATE TABLE IF NOT EXISTS alerts (
    id                 BIGSERIAL PRIMARY KEY,
    rule_id            VARCHAR(50)  NOT NULL,
    detection_method   VARCHAR(10)  NOT NULL
                       CHECK (detection_method IN ('THRESHOLD','ANOMALY','COMPOUND')),
    severity           VARCHAR(10)  NOT NULL
                       CHECK (severity IN ('INFO','WARNING','CRITICAL')),
    asset_id           INT          NOT NULL REFERENCES assets(id),
    title              VARCHAR(200) NOT NULL,
    summary            TEXT         NOT NULL,
    first_ts           TIMESTAMPTZ  NOT NULL,
    last_ts            TIMESTAMPTZ  NOT NULL,
    detected_ts        TIMESTAMPTZ  NOT NULL,
    evidence           JSONB        NOT NULL,
    status             VARCHAR(15)  NOT NULL DEFAULT 'OPEN'
                       CHECK (status IN ('OPEN','ACKNOWLEDGED','RESOLVED')),
    explanation        JSONB,
    explanation_source VARCHAR(10)  CHECK (explanation_source IN ('OLLAMA','FALLBACK')),
    explained_at       TIMESTAMPTZ,
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_alerts_first_ts ON alerts (first_ts);
CREATE INDEX IF NOT EXISTS idx_alerts_asset_ts ON alerts (asset_id, first_ts);
CREATE INDEX IF NOT EXISTS idx_alerts_severity ON alerts (severity);

CREATE TABLE IF NOT EXISTS scenario_runs (
    id             SERIAL PRIMARY KEY,
    scenario_key   VARCHAR(10)  NOT NULL,
    name           VARCHAR(100) NOT NULL,
    description    TEXT         NOT NULL,
    asset_ids      INT[]        NOT NULL,
    start_ts       TIMESTAMPTZ  NOT NULL,
    end_ts         TIMESTAMPTZ  NOT NULL,
    expected_rules TEXT[]       NOT NULL,
    event_ids      BIGINT[]     NOT NULL DEFAULT '{}'
);
