-- Financial Accounts & Transaction Sync
-- Pulls transaction data from PayPal (REST API), Wealthsimple (internal API),
-- and RBC (CSV import) into a unified local schema.
-- Sensitive fields obfuscated at write time (account numbers → last4).
-- Follows auto-sync pattern: check soy_meta freshness → sync silently → update timestamp.

-- ═══════════════════════════════════════════════════════════════
-- financial_accounts: Registered financial accounts
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS financial_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL CHECK (source IN ('paypal', 'wealthsimple', 'rbc', 'koho', 'other')),
    account_type TEXT NOT NULL CHECK (account_type IN (
        'chequing', 'savings', 'credit', 'prepaid', 'investment',
        'rrsp', 'tfsa', 'cash', 'other'
    )),
    label TEXT NOT NULL,                    -- human-friendly name ("RBC Savings", "PayPal CAD")
    account_last4 TEXT,                     -- last 4 digits only, never store full number
    currency TEXT NOT NULL DEFAULT 'CAD',
    institution TEXT,                       -- "Royal Bank of Canada", "PayPal", etc.
    is_business INTEGER NOT NULL DEFAULT 0, -- 1 if primarily used for business
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disconnected', 'closed')),
    last_synced_at TEXT,
    sync_cursor TEXT,                       -- opaque cursor for incremental sync (e.g., PayPal page token)
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_fin_acct_source_last4
    ON financial_accounts(source, account_type, account_last4);

-- ═══════════════════════════════════════════════════════════════
-- financial_transactions: Unified transaction ledger
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS financial_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES financial_accounts(id) ON DELETE CASCADE,
    external_id TEXT,                       -- source-specific transaction ID (for dedup)
    transaction_date TEXT NOT NULL,         -- ISO date (YYYY-MM-DD)
    posted_date TEXT,                       -- date posted/settled (may differ from txn date)
    description TEXT NOT NULL,              -- raw description from source
    description_clean TEXT,                 -- normalized/cleaned description
    amount REAL NOT NULL,                   -- positive = credit/income, negative = debit/expense
    currency TEXT NOT NULL DEFAULT 'CAD',
    balance_after REAL,                     -- running balance if available
    txn_type TEXT CHECK (txn_type IN (
        'purchase', 'payment', 'transfer', 'deposit', 'withdrawal',
        'refund', 'fee', 'interest', 'dividend', 'contribution',
        'sale', 'other'
    )),
    counterparty TEXT,                      -- who you paid / who paid you
    counterparty_email TEXT,                -- PayPal shows this
    category TEXT,                          -- auto-assigned or manual category
    tax_category TEXT,                      -- maps to expense_records.category if deductible
    t2125_number INTEGER,                   -- 1=VO, 2=consulting, NULL=personal/unassigned
    is_business INTEGER DEFAULT 0,          -- flagged as business expense/income
    is_reviewed INTEGER DEFAULT 0,          -- human has confirmed categorization
    expense_record_id INTEGER REFERENCES expense_records(id) ON DELETE SET NULL,
    income_record_id INTEGER REFERENCES income_records_new(id) ON DELETE SET NULL,
    raw_data TEXT,                          -- JSON blob of original record (for debugging)
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Dedup index: prevent re-importing the same transaction
CREATE UNIQUE INDEX IF NOT EXISTS idx_fin_txn_dedup
    ON financial_transactions(account_id, external_id);

CREATE INDEX IF NOT EXISTS idx_fin_txn_date ON financial_transactions(transaction_date);
CREATE INDEX IF NOT EXISTS idx_fin_txn_category ON financial_transactions(category);
CREATE INDEX IF NOT EXISTS idx_fin_txn_tax_cat ON financial_transactions(tax_category);
CREATE INDEX IF NOT EXISTS idx_fin_txn_business ON financial_transactions(is_business);
CREATE INDEX IF NOT EXISTS idx_fin_txn_counterparty ON financial_transactions(counterparty);
CREATE INDEX IF NOT EXISTS idx_fin_txn_reviewed ON financial_transactions(is_reviewed);

-- ═══════════════════════════════════════════════════════════════
-- financial_balances: Point-in-time balance snapshots
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS financial_balances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES financial_accounts(id) ON DELETE CASCADE,
    balance REAL NOT NULL,
    available REAL,                         -- available balance (may differ from total)
    currency TEXT NOT NULL DEFAULT 'CAD',
    as_of TEXT NOT NULL,                    -- ISO datetime
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_fin_bal_account ON financial_balances(account_id, as_of);

-- ═══════════════════════════════════════════════════════════════
-- transaction_rules: Auto-categorization patterns
-- Matches against description or counterparty to assign category
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS transaction_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern TEXT NOT NULL,                  -- substring or regex to match (case-insensitive)
    match_field TEXT NOT NULL DEFAULT 'description'
        CHECK (match_field IN ('description', 'counterparty', 'counterparty_email')),
    category TEXT,                          -- general category label
    tax_category TEXT,                      -- maps to expense_records category enum
    t2125_number INTEGER,                   -- 1=VO, 2=consulting, NULL=personal
    is_business INTEGER DEFAULT 0,
    txn_type TEXT,                          -- override txn_type if matched
    priority INTEGER DEFAULT 50,            -- higher = checked first (for overlapping patterns)
    notes TEXT,
    active INTEGER DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_txn_rules_active ON transaction_rules(active, priority DESC);

-- Seed rules from known bills and vendors
INSERT OR IGNORE INTO transaction_rules (pattern, match_field, category, tax_category, t2125_number, is_business, notes) VALUES
-- Home office utilities (business-use-of-home, 28.6%)
('ROGERS', 'description', 'Internet', 'phone_internet', 2, 1, 'Rogers internet bill'),
('ENBRIDGE', 'description', 'Heat', 'home_office', 2, 1, 'Enbridge gas - heat'),
('TORONTO HYDRO', 'description', 'Electricity', 'home_office', 2, 1, 'Toronto Hydro - electricity'),
('TORONTO WATER', 'description', 'Water', 'home_office', 2, 1, 'City water/sewer'),
('RFA MORTGAGE', 'description', 'Mortgage', 'home_office', 2, 1, 'RFA mortgage payments (interest portion deductible)'),

-- Software & subscriptions
('ANTHROPIC', 'description', 'Software', 'software_subscriptions', 2, 1, 'Claude subscription'),
('ANTHROPIC', 'counterparty', 'Software', 'software_subscriptions', 2, 1, 'Claude subscription'),
('1PASSWORD', 'description', 'Software', 'software_subscriptions', 2, 1, '1Password annual'),
('1PASSWORD', 'counterparty', 'Software', 'software_subscriptions', 2, 1, '1Password annual'),

-- VO/Acting expenses
('ACTRA', 'description', 'Union Dues', 'union_dues', 1, 1, 'ACTRA dues'),
('CASTING WORKBOOK', 'description', 'VO Platform', 'professional_development', 1, 1, 'Casting Workbook membership'),
('CASTING WORKBOOK', 'counterparty', 'VO Platform', 'professional_development', 1, 1, 'Casting Workbook membership'),
('CASTING CALL CLUB', 'description', 'VO Platform', 'professional_development', 1, 1, 'Casting Call Club'),
('CASTINGCALL', 'description', 'VO Platform', 'professional_development', 1, 1, 'Casting Call Club (PayPal descriptor)'),
('TALENT PAYMENT', 'description', 'VO Income', NULL, 1, 1, 'Talent Payment Services - VO income'),

-- Consulting income
('DICO', 'description', 'Consulting Income', NULL, 2, 1, 'Dico Ltd consulting payments'),

-- Employment
('BATL', 'description', 'Employment', NULL, NULL, 0, 'BATL axe throwing payroll'),

-- Property
('PROPERTY TAX', 'description', 'Property Tax', 'home_office', 2, 1, 'City of Toronto property tax'),
('HOME INSURANCE', 'description', 'Insurance', 'home_office', 2, 1, 'Home insurance premium'),

-- Known personal (flag as not business to skip during tax review)
('UBER', 'description', 'Transport', NULL, NULL, 0, 'Uber rides - personal unless flagged'),
('AMAZON', 'description', 'Shopping', NULL, NULL, 0, 'Amazon - personal unless office equipment'),
('D&D BEYOND', 'description', 'Entertainment', NULL, NULL, 0, 'D&D subscription - personal'),
('NINTENDO', 'description', 'Entertainment', NULL, NULL, 0, 'Nintendo - personal'),
('BEACON', 'description', 'Entertainment', NULL, NULL, 0, 'Beacon/Critical Role - personal');

-- ═══════════════════════════════════════════════════════════════
-- Link tracked_bills to financial accounts (optional upgrade)
-- ═══════════════════════════════════════════════════════════════

-- Add column to tracked_bills if it doesn't exist
-- SQLite doesn't support ADD COLUMN IF NOT EXISTS, so we check first
CREATE TABLE IF NOT EXISTS _migration_check_052 (done INTEGER);
INSERT OR IGNORE INTO _migration_check_052 VALUES (1);

-- ═══════════════════════════════════════════════════════════════
-- Computed Views
-- ═══════════════════════════════════════════════════════════════

-- v_financial_summary: Monthly spend/income by category
DROP VIEW IF EXISTS v_financial_summary;
CREATE VIEW IF NOT EXISTS v_financial_summary AS
SELECT
    strftime('%Y-%m', transaction_date) AS month,
    fa.source,
    fa.label AS account_label,
    ft.category,
    ft.is_business,
    ft.t2125_number,
    COUNT(*) AS txn_count,
    SUM(CASE WHEN ft.amount < 0 THEN ft.amount ELSE 0 END) AS total_spent,
    SUM(CASE WHEN ft.amount > 0 THEN ft.amount ELSE 0 END) AS total_received,
    SUM(ft.amount) AS net
FROM financial_transactions ft
JOIN financial_accounts fa ON fa.id = ft.account_id
GROUP BY month, fa.source, fa.label, ft.category, ft.is_business, ft.t2125_number
ORDER BY month DESC, total_spent ASC;

-- v_tax_deductible_txns: Transactions flagged as business with tax categories
DROP VIEW IF EXISTS v_tax_deductible_txns;
CREATE VIEW IF NOT EXISTS v_tax_deductible_txns AS
SELECT
    ft.id,
    ft.transaction_date,
    fa.source,
    fa.label AS account_label,
    ft.description,
    ft.description_clean,
    ft.counterparty,
    ft.amount,
    ft.currency,
    ft.category,
    ft.tax_category,
    ft.t2125_number,
    CASE ft.t2125_number
        WHEN 1 THEN 'VO/Acting (711500)'
        WHEN 2 THEN 'Consulting (541619)'
        ELSE 'Unassigned'
    END AS business_label,
    ft.is_reviewed,
    ft.expense_record_id,
    ft.income_record_id,
    ft.notes,
    strftime('%Y', ft.transaction_date) AS tax_year
FROM financial_transactions ft
JOIN financial_accounts fa ON fa.id = ft.account_id
WHERE ft.is_business = 1
ORDER BY ft.transaction_date DESC;

-- v_uncategorized_txns: Transactions needing human review
DROP VIEW IF EXISTS v_uncategorized_txns;
CREATE VIEW IF NOT EXISTS v_uncategorized_txns AS
SELECT
    ft.id,
    ft.transaction_date,
    fa.source,
    fa.label AS account_label,
    ft.description,
    ft.counterparty,
    ft.amount,
    ft.currency,
    ft.txn_type
FROM financial_transactions ft
JOIN financial_accounts fa ON fa.id = ft.account_id
WHERE ft.category IS NULL
  AND ft.is_reviewed = 0
ORDER BY ABS(ft.amount) DESC;

-- v_monthly_cashflow: High-level monthly in/out per account
DROP VIEW IF EXISTS v_monthly_cashflow;
CREATE VIEW IF NOT EXISTS v_monthly_cashflow AS
SELECT
    strftime('%Y-%m', ft.transaction_date) AS month,
    fa.label AS account_label,
    fa.source,
    SUM(CASE WHEN ft.amount > 0 THEN ft.amount ELSE 0 END) AS money_in,
    SUM(CASE WHEN ft.amount < 0 THEN ABS(ft.amount) ELSE 0 END) AS money_out,
    SUM(ft.amount) AS net_flow,
    COUNT(*) AS txn_count
FROM financial_transactions ft
JOIN financial_accounts fa ON fa.id = ft.account_id
GROUP BY month, fa.label, fa.source
ORDER BY month DESC;

-- Clean up migration check table
DROP TABLE IF EXISTS _migration_check_052;
