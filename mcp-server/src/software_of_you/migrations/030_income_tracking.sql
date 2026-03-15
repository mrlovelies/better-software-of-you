-- Income Tracking module
-- Track income from multiple sources (freelance, VO/commercial, employment, residuals) for tax filing.

CREATE TABLE IF NOT EXISTS income_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    amount REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'CAD',
    source TEXT NOT NULL,           -- payer name (e.g., "A&W" or "BATL")
    category TEXT NOT NULL          -- 'vo_commercial', 'freelance', 'employment', 'residual', 'other'
        CHECK (category IN ('vo_commercial', 'freelance', 'employment', 'residual', 'other')),
    description TEXT,               -- what the work was
    reference_number TEXT,          -- invoice/PO number (e.g., "PW26-0128")
    tax_year INTEGER NOT NULL,      -- filing year
    received_date TEXT,             -- when payment was received (ISO date)
    invoice_date TEXT,              -- when invoice was sent/issued
    contact_id INTEGER,             -- optional link to agent/client contact
    project_id INTEGER,             -- optional link to SoY project
    agent_fee_pct REAL,             -- agent commission percentage (e.g., 15.0)
    agent_fee_amount REAL,          -- calculated agent fee
    net_amount REAL,                -- amount after agent fee
    tax_withheld REAL DEFAULT 0,    -- any tax withheld at source
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (contact_id) REFERENCES contacts(id),
    FOREIGN KEY (project_id) REFERENCES projects(id)
);

CREATE INDEX IF NOT EXISTS idx_income_tax_year ON income_records(tax_year);
CREATE INDEX IF NOT EXISTS idx_income_category ON income_records(category);
CREATE INDEX IF NOT EXISTS idx_income_source ON income_records(source);
CREATE INDEX IF NOT EXISTS idx_income_contact ON income_records(contact_id);

INSERT OR REPLACE INTO modules (name, version, enabled)
VALUES ('income_tracking', '1.0', 1);
