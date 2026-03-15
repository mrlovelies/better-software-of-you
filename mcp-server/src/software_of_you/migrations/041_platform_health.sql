-- Platform Health Module
-- Overnight sweeps monitoring DB integrity, process health, migration drift, stale servers

-- Individual check results
CREATE TABLE IF NOT EXISTS health_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sweep_id INTEGER REFERENCES health_sweeps(id),
    check_type TEXT NOT NULL,  -- 'db_integrity', 'processes', 'stale_server', 'error_logs', 'migration_count', 'syncthing'
    machine TEXT NOT NULL,  -- 'razer', 'lucy', 'macbook'
    status TEXT NOT NULL CHECK (status IN ('ok', 'warning', 'error')),
    details TEXT,  -- JSON details
    auto_fixed INTEGER DEFAULT 0,
    fix_details TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Aggregated sweep runs
CREATE TABLE IF NOT EXISTS health_sweeps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sweep_type TEXT NOT NULL,  -- 'check', 'sweep'
    machine TEXT NOT NULL,
    total_checks INTEGER DEFAULT 0,
    passed INTEGER DEFAULT 0,
    warnings INTEGER DEFAULT 0,
    errors INTEGER DEFAULT 0,
    auto_fixed INTEGER DEFAULT 0,
    summary TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Latest status per check_type per machine + 24h error count
CREATE VIEW IF NOT EXISTS v_health_summary AS
SELECT
    hc.check_type,
    hc.machine,
    hc.status,
    hc.details,
    hc.auto_fixed,
    hc.created_at as last_check_at,
    (SELECT COUNT(*) FROM health_checks hc2
     WHERE hc2.check_type = hc.check_type
       AND hc2.machine = hc.machine
       AND hc2.status = 'error'
       AND hc2.created_at > datetime('now', '-24 hours')
    ) as errors_24h,
    (SELECT COUNT(*) FROM health_checks hc3
     WHERE hc3.check_type = hc.check_type
       AND hc3.machine = hc.machine
       AND hc3.status = 'warning'
       AND hc3.created_at > datetime('now', '-24 hours')
    ) as warnings_24h
FROM health_checks hc
INNER JOIN (
    SELECT check_type, machine, MAX(id) as max_id
    FROM health_checks
    GROUP BY check_type, machine
) latest ON hc.id = latest.max_id;

-- Register the module
INSERT OR IGNORE INTO modules (name, version, enabled)
VALUES ('platform-health', '0.1.0', 1);
