#!/usr/bin/env python3
"""PayPal Transaction Sync for Software of You.

Authenticates via OAuth2 client credentials, pulls transactions from the
PayPal Transaction Search API, and stores them in the local SoY database.

Sensitive fields are obfuscated before storage (account numbers → last4).

Usage:
    python3 shared/sync_paypal.py setup          # Store credentials
    python3 shared/sync_paypal.py sync [--days N] # Sync recent transactions (default 30)
    python3 shared/sync_paypal.py sync --from 2025-01-01 --to 2025-12-31
    python3 shared/sync_paypal.py balances        # Fetch current balances
    python3 shared/sync_paypal.py status          # Show sync status

Credentials:
    Set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET in .env
    Create a personal app at https://developer.paypal.com/dashboard/applications
    Required scopes: Transaction Search (https://uri.paypal.com/services/reporting/search/read)
"""

import base64
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

PLUGIN_ROOT = os.environ.get(
    "CLAUDE_PLUGIN_ROOT",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)
DB_PATH = os.path.join(
    os.path.expanduser("~"), ".local", "share", "software-of-you", "soy.db"
)
ENV_PATH = os.path.join(PLUGIN_ROOT, ".env")

# PayPal API base URLs
PAYPAL_LIVE_BASE = "https://api-m.paypal.com"
PAYPAL_SANDBOX_BASE = "https://api-m.sandbox.paypal.com"

# Use live by default
PAYPAL_BASE = PAYPAL_LIVE_BASE


def _load_env():
    """Load .env file into a dict."""
    env = {}
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip("'\"")
    return env


def _get_credentials():
    """Get PayPal client credentials from .env or environment."""
    env = _load_env()
    client_id = os.environ.get("PAYPAL_CLIENT_ID") or env.get("PAYPAL_CLIENT_ID")
    client_secret = os.environ.get("PAYPAL_CLIENT_SECRET") or env.get(
        "PAYPAL_CLIENT_SECRET"
    )
    if not client_id or not client_secret:
        return None, None
    return client_id, client_secret


def _get_db():
    """Get database connection."""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _api_request(url, method="GET", headers=None, data=None, timeout=30):
    """Make an HTTP request. Returns (response_dict, error_string)."""
    if headers is None:
        headers = {}
    req = urllib.request.Request(url, headers=headers, method=method)
    if data:
        if isinstance(data, str):
            req.data = data.encode("utf-8")
        elif isinstance(data, bytes):
            req.data = data
        else:
            req.data = json.dumps(data).encode("utf-8")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}, None
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            err = json.loads(body)
            msg = err.get("error_description") or err.get("message") or body[:200]
        except json.JSONDecodeError:
            msg = body[:200]
        return None, f"HTTP {e.code}: {msg}"
    except Exception as e:
        return None, str(e)


# ═══════════════════════════════════════════════════════════════
# OAuth2 Authentication
# ═══════════════════════════════════════════════════════════════


def get_access_token():
    """Get an OAuth2 access token using client credentials."""
    client_id, client_secret = _get_credentials()
    if not client_id:
        return None, "PayPal credentials not configured. Set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET in .env"

    # Check for cached token
    db = _get_db()
    cached = db.execute(
        "SELECT value, updated_at FROM soy_meta WHERE key = 'paypal_access_token'"
    ).fetchone()
    expires = db.execute(
        "SELECT value FROM soy_meta WHERE key = 'paypal_token_expires'"
    ).fetchone()

    if cached and expires:
        try:
            expires_at = datetime.fromisoformat(expires["value"])
            if datetime.now(timezone.utc) < expires_at:
                db.close()
                return cached["value"], None
        except (ValueError, TypeError):
            pass

    # Request new token
    auth_string = base64.b64encode(
        f"{client_id}:{client_secret}".encode()
    ).decode()

    resp, err = _api_request(
        f"{PAYPAL_BASE}/v1/oauth2/token",
        method="POST",
        headers={
            "Authorization": f"Basic {auth_string}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data="grant_type=client_credentials",
    )

    if err:
        db.close()
        return None, f"Auth failed: {err}"

    access_token = resp.get("access_token")
    expires_in = resp.get("expires_in", 32400)  # Default 9 hours

    if not access_token:
        db.close()
        return None, f"No access_token in response: {resp}"

    # Cache token
    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=expires_in - 60)
    ).isoformat()
    db.execute(
        "INSERT OR REPLACE INTO soy_meta (key, value, updated_at) "
        "VALUES ('paypal_access_token', ?, datetime('now'))",
        (access_token,),
    )
    db.execute(
        "INSERT OR REPLACE INTO soy_meta (key, value, updated_at) "
        "VALUES ('paypal_token_expires', ?, datetime('now'))",
        (expires_at,),
    )
    db.commit()
    db.close()

    return access_token, None


# ═══════════════════════════════════════════════════════════════
# Transaction Sync
# ═══════════════════════════════════════════════════════════════


def _ensure_paypal_account(db):
    """Ensure a PayPal financial account exists. Returns account_id."""
    row = db.execute(
        "SELECT id FROM financial_accounts WHERE source = 'paypal' LIMIT 1"
    ).fetchone()
    if row:
        return row["id"]

    db.execute(
        """INSERT INTO financial_accounts
           (source, account_type, label, currency, institution, is_business, status)
           VALUES ('paypal', 'cash', 'PayPal', 'CAD', 'PayPal', 0, 'active')"""
    )
    db.commit()
    return db.execute(
        "SELECT id FROM financial_accounts WHERE source = 'paypal' LIMIT 1"
    ).fetchone()["id"]


def _obfuscate_account(account_str):
    """Obfuscate account number to last 4 digits."""
    if not account_str:
        return None
    clean = re.sub(r"[^0-9]", "", str(account_str))
    if len(clean) >= 4:
        return clean[-4:]
    return clean


def _apply_categorization_rules(db, description, counterparty, counterparty_email):
    """Apply transaction_rules to categorize a transaction. Returns dict of matched fields."""
    rules = db.execute(
        "SELECT * FROM transaction_rules WHERE active = 1 ORDER BY priority DESC"
    ).fetchall()

    for rule in rules:
        pattern = rule["pattern"].upper()
        field = rule["match_field"]

        target = ""
        if field == "description" and description:
            target = description.upper()
        elif field == "counterparty" and counterparty:
            target = counterparty.upper()
        elif field == "counterparty_email" and counterparty_email:
            target = counterparty_email.upper()

        if pattern in target:
            return {
                "category": rule["category"],
                "tax_category": rule["tax_category"],
                "t2125_number": rule["t2125_number"],
                "is_business": rule["is_business"],
                "txn_type": rule["txn_type"],
            }

    return {}


def _parse_paypal_transaction(txn):
    """Parse a PayPal API transaction into our schema fields."""
    info = txn.get("transaction_info", {})
    payer = txn.get("payer_info", {})
    cart = txn.get("cart_info", {})

    # Amount — PayPal uses transaction_amount for the primary amount
    amount_obj = info.get("transaction_amount", {})
    amount = float(amount_obj.get("value", 0))
    currency = amount_obj.get("currency_code", "CAD")

    # Transaction type mapping
    txn_event_code = info.get("transaction_event_code", "")
    txn_type_map = {
        "T0000": "purchase",      # General payment
        "T0001": "purchase",      # Mass payment
        "T0002": "purchase",      # Subscription payment
        "T0003": "purchase",      # Pre-approved payment
        "T0004": "purchase",      # eBay payment
        "T0005": "sale",          # Direct payment
        "T0006": "sale",          # Express Checkout
        "T0007": "sale",          # Website payment
        "T0008": "deposit",       # Postage payment
        "T0009": "purchase",      # Gift certificate
        "T0010": "purchase",      # Third party
        "T0011": "refund",        # Reversal
        "T0012": "deposit",       # Donation received
        "T0100": "deposit",       # General received
        "T0200": "refund",        # General currency conversion
        "T0300": "deposit",       # Funding
        "T0400": "withdrawal",    # Withdrawal
        "T0500": "fee",           # Fee
        "T0700": "transfer",      # General transfer
        "T0800": "purchase",      # Billing agreement
        "T0900": "deposit",       # Funds consolidation
        "T1000": "transfer",      # General hold
        "T1100": "refund",        # Reversal
        "T1200": "other",         # Adjustment
    }
    # Match on first 5 chars (e.g., T0006 → sale)
    txn_type = txn_type_map.get(txn_event_code[:5], "other")

    # Determine sign: PayPal sends negative for debits
    # The transaction_amount already has the correct sign

    # Counterparty info
    payer_name = payer.get("payer_name", {})
    counterparty_parts = [
        payer_name.get("given_name", ""),
        payer_name.get("surname", ""),
    ]
    counterparty = " ".join(p for p in counterparty_parts if p).strip()
    if not counterparty:
        counterparty = payer_name.get("alternate_full_name")

    counterparty_email = payer.get("email_address")

    # Description
    description = info.get("transaction_subject") or info.get("transaction_note", "")
    if not description and cart.get("item_details"):
        items = cart["item_details"]
        if items:
            description = items[0].get("item_name", "")

    if not description:
        description = f"PayPal {txn_event_code}"

    return {
        "external_id": info.get("transaction_id"),
        "transaction_date": (info.get("transaction_initiation_date", "")[:10]
                            or info.get("transaction_updated_date", "")[:10]),
        "posted_date": info.get("transaction_updated_date", "")[:10] or None,
        "description": description,
        "amount": amount,
        "currency": currency,
        "txn_type": txn_type,
        "counterparty": counterparty,
        "counterparty_email": counterparty_email,
        "raw_data": json.dumps(txn),
    }


def _fetch_window(token, account_id, db, start_iso, end_iso, page_size=500):
    """Fetch transactions for a single <=31-day window. Returns (stored, skipped, error)."""
    stored = 0
    skipped = 0
    page = 1
    total_pages = 1

    while page <= total_pages:
        params = urllib.parse.urlencode({
            "start_date": start_iso,
            "end_date": end_iso,
            "fields": "transaction_info,payer_info,cart_info",
            "page_size": page_size,
            "page": page,
        })

        resp, err = _api_request(
            f"{PAYPAL_BASE}/v1/reporting/transactions?{params}",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )

        if err:
            return stored, skipped, f"Transaction fetch failed (page {page}): {err}"

        transactions = resp.get("transaction_details", [])
        total_pages = resp.get("total_pages", 1)

        for txn in transactions:
            parsed = _parse_paypal_transaction(txn)

            if not parsed["external_id"]:
                skipped += 1
                continue

            # Check if already exists (dedup)
            existing = db.execute(
                "SELECT id FROM financial_transactions WHERE account_id = ? AND external_id = ?",
                (account_id, parsed["external_id"]),
            ).fetchone()

            if existing:
                skipped += 1
                continue

            # Apply categorization rules
            rules = _apply_categorization_rules(
                db,
                parsed["description"],
                parsed["counterparty"],
                parsed["counterparty_email"],
            )

            db.execute(
                """INSERT INTO financial_transactions
                   (account_id, external_id, transaction_date, posted_date,
                    description, description_clean, amount, currency, txn_type,
                    counterparty, counterparty_email,
                    category, tax_category, t2125_number, is_business,
                    raw_data)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    account_id,
                    parsed["external_id"],
                    parsed["transaction_date"],
                    parsed["posted_date"],
                    parsed["description"],
                    parsed["description"],  # clean = raw for now
                    parsed["amount"],
                    parsed["currency"],
                    parsed["txn_type"],
                    parsed["counterparty"],
                    parsed["counterparty_email"],
                    rules.get("category"),
                    rules.get("tax_category"),
                    rules.get("t2125_number"),
                    rules.get("is_business", 0),
                    parsed["raw_data"],
                ),
            )
            stored += 1

        page += 1

        # Rate limit: 1 second between paginated requests
        if page <= total_pages:
            import time
            time.sleep(1)

    return stored, skipped, None


def sync_transactions(start_date, end_date):
    """Sync PayPal transactions for a date range.

    PayPal limits each request to a 31-day window, so longer ranges
    are automatically chunked into consecutive windows.
    """
    token, err = get_access_token()
    if err:
        return {"error": err}

    db = _get_db()
    account_id = _ensure_paypal_account(db)

    total_stored = 0
    total_skipped = 0
    windows = 0

    # Chunk into 31-day windows
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    window_start = start

    while window_start <= end:
        window_end = min(window_start + timedelta(days=30), end)

        start_iso = window_start.strftime("%Y-%m-%dT00:00:00-0000")
        end_iso = window_end.strftime("%Y-%m-%dT23:59:59-0000")

        stored, skipped, err = _fetch_window(
            token, account_id, db, start_iso, end_iso
        )

        if err:
            # Commit what we have so far, then report the error
            db.commit()
            db.close()
            return {
                "error": err,
                "stored_before_error": total_stored,
                "window": f"{window_start.date()} to {window_end.date()}",
            }

        total_stored += stored
        total_skipped += skipped
        windows += 1

        # Commit after each window
        db.commit()

        window_start = window_end + timedelta(days=1)

        # Brief pause between windows to be polite to the API
        if window_start <= end:
            import time
            time.sleep(1)

    # Update sync timestamps
    db.execute(
        "UPDATE financial_accounts SET last_synced_at = datetime('now'), updated_at = datetime('now') "
        "WHERE id = ?",
        (account_id,),
    )
    db.execute(
        "INSERT OR REPLACE INTO soy_meta (key, value, updated_at) "
        "VALUES ('paypal_last_synced', datetime('now'), datetime('now'))"
    )
    db.commit()

    result = {
        "stored": total_stored,
        "skipped": total_skipped,
        "windows": windows,
        "date_range": f"{start_date} to {end_date}",
    }

    # Log activity
    db.execute(
        "INSERT INTO activity_log (entity_type, entity_id, action, details, created_at) "
        "VALUES ('financial_sync', ?, 'paypal_sync', ?, datetime('now'))",
        (account_id, json.dumps(result)),
    )
    db.commit()
    db.close()

    return result


# ═══════════════════════════════════════════════════════════════
# Balances
# ═══════════════════════════════════════════════════════════════


def fetch_balances():
    """Fetch current PayPal balances."""
    token, err = get_access_token()
    if err:
        return {"error": err}

    resp, err = _api_request(
        f"{PAYPAL_BASE}/v1/reporting/balances?as_of_time=NOW&currency_code=CAD",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )

    if err:
        return {"error": f"Balance fetch failed: {err}"}

    db = _get_db()
    account_id = _ensure_paypal_account(db)
    balances = []

    for bal in resp.get("balances", []):
        currency = bal.get("currency", "CAD")
        total = float(bal.get("total_balance", {}).get("value", 0))
        available = float(bal.get("available_balance", {}).get("value", 0))

        db.execute(
            "INSERT INTO financial_balances (account_id, balance, available, currency, as_of) "
            "VALUES (?, ?, ?, ?, datetime('now'))",
            (account_id, total, available, currency),
        )
        balances.append({
            "currency": currency,
            "total": total,
            "available": available,
        })

    db.commit()
    db.close()

    return {"balances": balances}


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════


def cmd_setup(args):
    """Check/store PayPal credentials."""
    client_id, client_secret = _get_credentials()
    if client_id and client_secret:
        # Test the credentials
        token, err = get_access_token()
        if err:
            print(f"Credentials found but auth failed: {err}")
            sys.exit(1)
        print(f"PayPal connected. Access token obtained.")
        print(f"Client ID: {client_id[:8]}...{client_id[-4:]}")
    else:
        print("PayPal not configured.")
        print("Add these to your .env file:")
        print("  PAYPAL_CLIENT_ID=your_client_id")
        print("  PAYPAL_CLIENT_SECRET=your_client_secret")
        print("")
        print("Get credentials at: https://developer.paypal.com/dashboard/applications")
        print("Create a 'Live' app (not sandbox) with Transaction Search scope.")
        sys.exit(1)


def cmd_sync(args):
    """Sync transactions."""
    # Parse args
    days = 30
    start_date = None
    end_date = None

    i = 0
    while i < len(args):
        if args[i] == "--days" and i + 1 < len(args):
            days = int(args[i + 1])
            i += 2
        elif args[i] == "--from" and i + 1 < len(args):
            start_date = args[i + 1]
            i += 2
        elif args[i] == "--to" and i + 1 < len(args):
            end_date = args[i + 1]
            i += 2
        else:
            i += 1

    if not start_date:
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    if not end_date:
        end_date = datetime.now().strftime("%Y-%m-%d")

    print(f"Syncing PayPal transactions: {start_date} to {end_date}...")
    result = sync_transactions(start_date, end_date)

    if "error" in result:
        print(f"Error: {result['error']}")
        sys.exit(1)

    print(f"  Stored: {result['stored']} new transactions")
    print(f"  Skipped: {result['skipped']} (duplicates)")
    print(f"  Windows: {result.get('windows', '?')}")


def cmd_balances(args):
    """Fetch and display balances."""
    result = fetch_balances()
    if "error" in result:
        print(f"Error: {result['error']}")
        sys.exit(1)

    for bal in result["balances"]:
        print(f"  {bal['currency']}: ${bal['total']:.2f} (available: ${bal['available']:.2f})")


def cmd_status(args):
    """Show sync status."""
    db = _get_db()

    last_sync = db.execute(
        "SELECT value FROM soy_meta WHERE key = 'paypal_last_synced'"
    ).fetchone()

    account = db.execute(
        "SELECT * FROM financial_accounts WHERE source = 'paypal' LIMIT 1"
    ).fetchone()

    txn_count = db.execute(
        "SELECT COUNT(*) as c FROM financial_transactions ft "
        "JOIN financial_accounts fa ON fa.id = ft.account_id "
        "WHERE fa.source = 'paypal'"
    ).fetchone()

    categorized = db.execute(
        "SELECT COUNT(*) as c FROM financial_transactions ft "
        "JOIN financial_accounts fa ON fa.id = ft.account_id "
        "WHERE fa.source = 'paypal' AND ft.category IS NOT NULL"
    ).fetchone()

    business = db.execute(
        "SELECT COUNT(*) as c FROM financial_transactions ft "
        "JOIN financial_accounts fa ON fa.id = ft.account_id "
        "WHERE fa.source = 'paypal' AND ft.is_business = 1"
    ).fetchone()

    client_id, _ = _get_credentials()

    print("PayPal Sync Status")
    print("=" * 40)
    print(f"  Configured:    {'Yes' if client_id else 'No'}")
    print(f"  Last synced:   {last_sync['value'] if last_sync else 'Never'}")
    print(f"  Transactions:  {txn_count['c'] if txn_count else 0}")
    print(f"  Categorized:   {categorized['c'] if categorized else 0}")
    print(f"  Business:      {business['c'] if business else 0}")

    db.close()


def main():
    if len(sys.argv) < 2:
        print("Usage: sync_paypal.py <setup|sync|balances|status>")
        print("")
        print("Commands:")
        print("  setup              Check/test PayPal credentials")
        print("  sync [--days N]    Sync recent transactions (default 30 days)")
        print("  sync --from DATE --to DATE   Sync specific date range")
        print("  balances           Fetch current balances")
        print("  status             Show sync status")
        sys.exit(1)

    command = sys.argv[1]
    rest = sys.argv[2:]

    if command == "setup":
        cmd_setup(rest)
    elif command == "sync":
        cmd_sync(rest)
    elif command == "balances":
        cmd_balances(rest)
    elif command == "status":
        cmd_status(rest)
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
