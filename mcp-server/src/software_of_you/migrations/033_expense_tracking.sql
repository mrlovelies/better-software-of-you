-- Expense Tracking — CRA T2125 aligned categories for self-employment tax filing.
-- Extends the income_tracking module to cover the expense side.

CREATE TABLE IF NOT EXISTS expense_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    amount REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'CAD',
    category TEXT NOT NULL
        CHECK (category IN (
            'union_dues', 'agent_commission', 'travel', 'home_office',
            'equipment', 'professional_development', 'marketing',
            'meals_entertainment', 'office_supplies', 'software_subscriptions',
            'phone_internet', 'professional_fees', 'insurance', 'vehicle', 'other'
        )),
    description TEXT,
    vendor TEXT,                     -- who was paid (e.g., "ACTRA", "Best Buy")
    reference_number TEXT,           -- receipt/invoice number
    tax_year INTEGER NOT NULL,
    expense_date TEXT,               -- when expense occurred (ISO date)
    hst_gst_amount REAL DEFAULT 0,  -- tax paid on the expense (claimable as ITC)
    deductible_pct REAL DEFAULT 100, -- e.g., 50 for meals_entertainment
    deductible_amount REAL,          -- amount * deductible_pct / 100
    income_record_id INTEGER,        -- optional link to income record
    contact_id INTEGER,
    project_id INTEGER,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (income_record_id) REFERENCES income_records(id),
    FOREIGN KEY (contact_id) REFERENCES contacts(id),
    FOREIGN KEY (project_id) REFERENCES projects(id)
);

CREATE INDEX IF NOT EXISTS idx_expense_tax_year ON expense_records(tax_year);
CREATE INDEX IF NOT EXISTS idx_expense_category ON expense_records(category);
CREATE INDEX IF NOT EXISTS idx_expense_income ON expense_records(income_record_id);

-- Update module version
UPDATE modules SET version = '1.1' WHERE name = 'income_tracking';
