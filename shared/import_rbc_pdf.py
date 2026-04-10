#!/usr/bin/env python3
"""RBC PDF Statement Importer for Software of You.

Parses RBC bank statement PDFs and imports transactions into the
local SoY database. Handles the Ratelink Essential statement format.

Usage:
    python3 shared/import_rbc_pdf.py /path/to/statement.pdf
    python3 shared/import_rbc_pdf.py /path/to/statements.zip
    python3 shared/import_rbc_pdf.py /path/to/folder/  # all PDFs in folder
    python3 shared/import_rbc_pdf.py status
"""

import json
import os
import re
import sqlite3
import sys
import zipfile
from datetime import datetime

PLUGIN_ROOT = os.environ.get(
    "CLAUDE_PLUGIN_ROOT",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)
DB_PATH = os.path.join(
    os.path.expanduser("~"), ".local", "share", "software-of-you", "soy.db"
)

# Month abbreviation → number
MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_rbc_account(db, last4="7180"):
    """Ensure an RBC financial account exists."""
    row = db.execute(
        "SELECT id FROM financial_accounts WHERE source = 'rbc' AND account_last4 = ?",
        (last4,),
    ).fetchone()
    if row:
        return row["id"]

    db.execute(
        """INSERT INTO financial_accounts
           (source, account_type, label, account_last4, currency,
            institution, is_business, status)
           VALUES ('rbc', 'savings', 'RBC Savings', ?, 'CAD',
                   'Royal Bank of Canada', 0, 'active')""",
        (last4,),
    )
    db.commit()
    return db.execute(
        "SELECT id FROM financial_accounts WHERE source = 'rbc' AND account_last4 = ?",
        (last4,),
    ).fetchone()["id"]


def _apply_categorization_rules(db, description):
    """Apply transaction_rules to categorize."""
    rules = db.execute(
        "SELECT * FROM transaction_rules WHERE active = 1 ORDER BY priority DESC"
    ).fetchall()

    desc_upper = (description or "").upper()
    for rule in rules:
        pattern = rule["pattern"].upper()
        field = rule["match_field"]
        if field in ("description", "counterparty") and pattern in desc_upper:
            return {
                "category": rule["category"],
                "tax_category": rule["tax_category"],
                "t2125_number": rule["t2125_number"],
                "is_business": rule["is_business"],
            }
    return {}


def _parse_amount(text):
    """Parse a dollar amount string, handling commas."""
    if not text:
        return None
    clean = text.replace(",", "").replace("$", "").strip()
    try:
        return float(clean)
    except ValueError:
        return None


def _infer_txn_type(description, is_withdrawal):
    """Infer transaction type from description."""
    desc = (description or "").lower()
    if "e-transfer" in desc and ("autodeposit" in desc or "received" in desc):
        return "deposit"
    if "e-transfer sent" in desc:
        return "transfer"
    if "payroll" in desc or "misc payment" in desc:
        return "deposit"
    if "mortgage" in desc:
        return "payment"
    if "bill pmt" in desc or "bill payment" in desc:
        return "payment"
    if "interac purchase" in desc or "contactless" in desc:
        return "purchase"
    if "online banking payment" in desc or "online banking transfer" in desc:
        return "payment"
    if "loan" in desc:
        return "payment"
    if "cheque deposit" in desc or "mobile cheque" in desc:
        return "deposit"
    if "find & save" in desc:
        return "transfer"
    if "interest" in desc:
        return "interest"
    if is_withdrawal:
        return "withdrawal"
    return "deposit"


def parse_rbc_statement(pdf_path):
    """Parse an RBC statement PDF into a list of transactions.

    Returns list of dicts with: date, description, withdrawal, deposit, balance
    """
    try:
        import fitz
    except ImportError:
        print("Error: PyMuPDF (fitz) not installed. Run: pip install pymupdf")
        return []

    doc = fitz.open(pdf_path)

    # Extract statement period from first page
    first_page_text = doc[0].get_text()
    period_match = re.search(
        r"From\s+(\w+\s+\d+,\s+\d{4})\s+to\s+(\w+\s+\d+,\s+\d{4})",
        first_page_text,
    )
    statement_year = None
    statement_start_month = None
    if period_match:
        try:
            end_date = datetime.strptime(period_match.group(2), "%B %d, %Y")
            start_date = datetime.strptime(period_match.group(1), "%B %d, %Y")
            statement_year = end_date.year
            statement_start_month = start_date.month
        except ValueError:
            pass

    # Extract account number
    acct_match = re.search(r"(\d{5}-\d{7})", first_page_text)
    account_number = acct_match.group(1) if acct_match else None

    # Parse all pages
    all_text = ""
    for page in doc:
        all_text += page.get_text() + "\n"
    doc.close()

    # Split into lines and parse transactions
    lines = all_text.split("\n")
    transactions = []
    current_date = None
    current_year = statement_year or 2025
    current_month = None

    date_pattern = re.compile(r"^(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)$", re.IGNORECASE)
    amount_pattern = re.compile(r"^[\d,]+\.\d{2}$")

    # Skip/noise patterns
    skip_patterns = [
        "Your Ratelink", "Royal Bank", "account statement", "Details of your",
        "Summary of", "Ratelink Essential", "RBPDA", "*3L", "LEO ACCOUNT",
        "P.O. Box", "Toronto ON", "How to reach", "1-800", "www.rbc",
        "Withdrawals ($)", "Deposits ($)", "Balance ($)", "of 7",
        "continued", "Your account number", "Your opening balance",
        "Total deposits", "Total withdrawals", "Your closing balance",
        "HANNA AVE",
    ]

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1  # Always advance — prevents infinite loops

        if not line:
            continue

        # Skip noise lines
        if any(skip in line for skip in skip_patterns):
            continue

        # Check for date line
        date_match = date_pattern.match(line)
        if date_match:
            day = int(date_match.group(1))
            month_str = date_match.group(2).lower()
            month = MONTH_MAP.get(month_str, 1)
            if current_month and month < current_month and month <= 2:
                current_year = (statement_year or 2025)
            current_month = month
            current_date = f"{current_year}-{month:02d}-{day:02d}"
            continue

        # Skip pure amount lines (orphaned — no description context)
        if amount_pattern.match(line):
            continue

        # Skip Opening Balance
        if line == "Opening Balance":
            continue

        # This should be a description line — collect it and any continuation
        if current_date is None:
            continue

        description_lines = [line]

        # Peek ahead for continuation lines (merchant names, reference codes)
        while i < len(lines):
            peek = lines[i].strip()
            if not peek or amount_pattern.match(peek) or date_pattern.match(peek):
                break
            if any(skip in peek for skip in skip_patterns):
                break
            # Likely a continuation (merchant name, reference)
            description_lines.append(peek)
            i += 1

        description = " ".join(description_lines)

        # Collect amounts that follow
        amounts = []
        while i < len(lines):
            peek = lines[i].strip()
            if amount_pattern.match(peek):
                amounts.append(_parse_amount(peek))
                i += 1
            else:
                break

        if not amounts:
            continue

        # Determine withdrawal vs deposit
        desc_lower = description.lower()
        is_deposit = any(x in desc_lower for x in [
            "autodeposit", "payroll", "misc payment", "cheque deposit",
            "mobile cheque", "online banking transfer", "interest",
        ])

        withdrawal = None
        deposit = None
        balance = None

        if len(amounts) == 1:
            if is_deposit:
                deposit = amounts[0]
            else:
                withdrawal = amounts[0]
        elif len(amounts) == 2:
            if is_deposit:
                deposit = amounts[0]
            else:
                withdrawal = amounts[0]
            balance = amounts[1]
        elif len(amounts) >= 3:
            withdrawal = amounts[0]
            deposit = amounts[1]
            balance = amounts[2]

        if withdrawal or deposit:
            transactions.append({
                "date": current_date,
                "description": description,
                "withdrawal": withdrawal,
                "deposit": deposit,
                "balance": balance,
            })

    return transactions, account_number


def import_transactions(pdf_path):
    """Parse a single RBC PDF and import transactions."""
    transactions, account_number = parse_rbc_statement(pdf_path)

    if not transactions:
        return {"error": f"No transactions found in {pdf_path}", "parsed": 0}

    last4 = account_number[-4:] if account_number else "7180"

    db = _get_db()
    account_id = _ensure_rbc_account(db, last4)

    stored = 0
    skipped = 0

    for txn in transactions:
        date = txn["date"]
        description = txn["description"]
        withdrawal = txn.get("withdrawal")
        deposit = txn.get("deposit")

        # Determine amount (positive for deposits, negative for withdrawals)
        if deposit:
            amount = deposit
        elif withdrawal:
            amount = -withdrawal
        else:
            skipped += 1
            continue

        # Generate external_id for dedup (date + description + amount)
        external_id = f"rbc_{date}_{description[:50]}_{amount:.2f}"

        # Check dedup
        existing = db.execute(
            "SELECT id FROM financial_transactions WHERE account_id = ? AND external_id = ?",
            (account_id, external_id),
        ).fetchone()

        if existing:
            skipped += 1
            continue

        # Determine transaction type
        is_withdrawal = amount < 0
        txn_type = _infer_txn_type(description, is_withdrawal)

        # Extract counterparty from description
        counterparty = description
        # Common patterns: "Contactless Interac purchase - XXXX MERCHANT NAME"
        merchant_match = re.search(r"purchase\s*-\s*\d+\s+(.+)", description, re.IGNORECASE)
        if merchant_match:
            counterparty = merchant_match.group(1).strip()
        elif "e-Transfer" in description:
            parts = description.split()
            # Find the name after "sent" or "Autodeposit"
            for idx, p in enumerate(parts):
                if p.lower() in ("sent", "autodeposit") and idx + 1 < len(parts):
                    counterparty = " ".join(parts[idx + 1:]).split(" CA")[0].split(" DF")[0].split(" VK")[0].split(" NF")[0].split(" ZN")[0].split(" BU")[0]
                    break
        elif "Bill Pmt" in description or "Bill Payment" in description:
            parts = description.split("Pmt")[-1].strip() if "Pmt" in description else description
            counterparty = parts.strip()

        # Apply categorization rules
        rules = _apply_categorization_rules(db, description)

        db.execute(
            """INSERT INTO financial_transactions
               (account_id, external_id, transaction_date, posted_date,
                description, description_clean, amount, currency, txn_type,
                counterparty, balance_after,
                category, tax_category, t2125_number, is_business,
                raw_data)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'CAD', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                account_id,
                external_id,
                date,
                date,
                description,
                counterparty,
                amount,
                txn_type,
                counterparty,
                txn.get("balance"),
                rules.get("category"),
                rules.get("tax_category"),
                rules.get("t2125_number"),
                rules.get("is_business", 0),
                json.dumps(txn),
            ),
        )
        stored += 1

    # Update timestamps
    db.execute(
        "UPDATE financial_accounts SET last_synced_at = datetime('now'), updated_at = datetime('now') "
        "WHERE id = ?",
        (account_id,),
    )
    db.execute(
        "INSERT OR REPLACE INTO soy_meta (key, value, updated_at) "
        "VALUES ('rbc_last_imported', datetime('now'), datetime('now'))"
    )
    db.commit()

    result = {"stored": stored, "skipped": skipped, "parsed": len(transactions), "file": os.path.basename(pdf_path)}

    db.execute(
        "INSERT INTO activity_log (entity_type, entity_id, action, details, created_at) "
        "VALUES ('financial_sync', ?, 'rbc_import', ?, datetime('now'))",
        (account_id, json.dumps(result)),
    )
    db.commit()
    db.close()

    return result


def import_from_path(path):
    """Import from a PDF, ZIP, or directory."""
    total_stored = 0
    total_skipped = 0
    total_parsed = 0
    files_processed = 0

    if path.endswith(".zip"):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            z = zipfile.ZipFile(path)
            z.extractall(tmpdir)
            for f in sorted(z.namelist()):
                if f.endswith(".pdf"):
                    filepath = os.path.join(tmpdir, f)
                    result = import_transactions(filepath)
                    if "error" not in result:
                        total_stored += result["stored"]
                        total_skipped += result["skipped"]
                        total_parsed += result["parsed"]
                        files_processed += 1
                        print(f"  {os.path.basename(f)}: {result['stored']} stored, {result['skipped']} skipped ({result['parsed']} parsed)")
                    else:
                        print(f"  {os.path.basename(f)}: {result['error']}")

    elif path.endswith(".pdf"):
        result = import_transactions(path)
        if "error" not in result:
            total_stored = result["stored"]
            total_skipped = result["skipped"]
            total_parsed = result["parsed"]
            files_processed = 1
            print(f"  {result['stored']} stored, {result['skipped']} skipped ({result['parsed']} parsed)")
        else:
            print(f"  Error: {result['error']}")

    elif os.path.isdir(path):
        for f in sorted(os.listdir(path)):
            if f.endswith(".pdf"):
                filepath = os.path.join(path, f)
                result = import_transactions(filepath)
                if "error" not in result:
                    total_stored += result["stored"]
                    total_skipped += result["skipped"]
                    total_parsed += result["parsed"]
                    files_processed += 1
                    print(f"  {f}: {result['stored']} stored, {result['skipped']} skipped ({result['parsed']} parsed)")
                else:
                    print(f"  {f}: {result['error']}")

    print(f"\nTotal: {total_stored} stored, {total_skipped} skipped, {total_parsed} parsed from {files_processed} files")
    return {"stored": total_stored, "skipped": total_skipped, "parsed": total_parsed, "files": files_processed}


def cmd_status():
    """Show import status."""
    db = _get_db()
    last = db.execute("SELECT value FROM soy_meta WHERE key = 'rbc_last_imported'").fetchone()
    accts = db.execute("SELECT COUNT(*) as c FROM financial_accounts WHERE source = 'rbc'").fetchone()
    txns = db.execute(
        "SELECT COUNT(*) as c FROM financial_transactions ft "
        "JOIN financial_accounts fa ON fa.id = ft.account_id WHERE fa.source = 'rbc'"
    ).fetchone()
    biz = db.execute(
        "SELECT COUNT(*) as c FROM financial_transactions ft "
        "JOIN financial_accounts fa ON fa.id = ft.account_id WHERE fa.source = 'rbc' AND ft.is_business = 1"
    ).fetchone()

    print("RBC Import Status")
    print("=" * 40)
    print(f"  Last imported:   {last['value'] if last else 'Never'}")
    print(f"  Accounts:        {accts['c']}")
    print(f"  Transactions:    {txns['c']}")
    print(f"  Business:        {biz['c']}")
    db.close()


def main():
    if len(sys.argv) < 2:
        print("Usage: import_rbc_pdf.py <path.pdf|path.zip|folder/|status>")
        sys.exit(1)

    arg = sys.argv[1]
    if arg == "status":
        cmd_status()
    else:
        print(f"Importing RBC statements from: {arg}")
        import_from_path(arg)


if __name__ == "__main__":
    main()
