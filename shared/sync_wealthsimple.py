#!/usr/bin/env python3
"""Wealthsimple Transaction Sync for Software of You.

Authenticates via Wealthsimple's internal API (reverse-engineered from web app),
pulls account and transaction data, and stores it in the local SoY database.

Sensitive fields are obfuscated before storage (account numbers → last4).

Usage:
    python3 shared/sync_wealthsimple.py setup         # Test credentials
    python3 shared/sync_wealthsimple.py accounts       # List accounts
    python3 shared/sync_wealthsimple.py sync [--days N] # Sync recent activity (default 90)
    python3 shared/sync_wealthsimple.py sync --from 2025-01-01 --to 2025-12-31
    python3 shared/sync_wealthsimple.py balances       # Fetch current balances
    python3 shared/sync_wealthsimple.py status         # Show sync status

Credentials:
    Set WEALTHSIMPLE_EMAIL and WEALTHSIMPLE_PASSWORD in .env
"""

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

# Wealthsimple API
WS_AUTH_BASE = "https://api.production.wealthsimple.com"
WS_AUTH_URL = f"{WS_AUTH_BASE}/v1/oauth/v2/token"
WS_TRADE_BASE = "https://trade-service.wealthsimple.com"
WS_LOGIN_PAGE = "https://my.wealthsimple.com/app/login"

# Fallback client_id — extracted from WS web app JS bundle.
# If this stops working, the bootstrap flow will scrape a fresh one.
WS_CLIENT_ID_FALLBACK = "4da53ac2b03225bed1550eba8e4611e086c7b905a3855e6ed12ea08c246758fa"

# Map WS account types to our schema
WS_ACCOUNT_TYPE_MAP = {
    "ca_rrsp": "rrsp",
    "ca_tfsa": "tfsa",
    "ca_non_registered": "investment",
    "ca_non_registered_crypto": "investment",
    "ca_lira": "investment",
    "ca_rrif": "investment",
    "ca_resp": "investment",
    "ca_cash": "chequing",
    "ca_savings": "savings",
    "ca_joint": "investment",
}


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
    """Get Wealthsimple credentials from .env or environment."""
    env = _load_env()
    email = os.environ.get("WEALTHSIMPLE_EMAIL") or env.get("WEALTHSIMPLE_EMAIL")
    password = os.environ.get("WEALTHSIMPLE_PASSWORD") or env.get(
        "WEALTHSIMPLE_PASSWORD"
    )
    if not email or not password:
        return None, None
    return email, password


def _get_db():
    """Get database connection."""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _api_request(url, method="GET", headers=None, data=None, timeout=30):
    """Make an HTTP request. Returns (response_dict, error_string, response_headers)."""
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
            resp_headers = dict(resp.headers)
            parsed = json.loads(body) if body else {}
            return parsed, None, resp_headers
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        resp_headers = dict(e.headers) if e.headers else {}
        try:
            err = json.loads(body)
            msg = err.get("error_description") or err.get("error") or body[:300]
        except json.JSONDecodeError:
            msg = body[:300]
        return None, f"HTTP {e.code}: {msg}", resp_headers
    except Exception as e:
        return None, str(e), {}


def _obfuscate_account(account_str):
    """Obfuscate account number to last 4 characters."""
    if not account_str:
        return None
    clean = re.sub(r"[^0-9A-Za-z]", "", str(account_str))
    if len(clean) >= 4:
        return clean[-4:]
    return clean


# ═══════════════════════════════════════════════════════════════
# Authentication
# ═══════════════════════════════════════════════════════════════


def _get_browser_profile_dir():
    """Get persistent browser profile directory."""
    profile_dir = os.path.join(
        os.path.expanduser("~"), ".local", "share", "software-of-you", "ws-browser"
    )
    os.makedirs(profile_dir, exist_ok=True)
    return profile_dir


def _browser_session(email, password, pages_to_visit=None):
    """Run a browser session with persistent profile.

    Uses stealth mode and a persistent browser context so that:
    - Cloudflare Turnstile challenges are solved once and remembered
    - Session cookies persist between runs (may skip login entirely)
    - All API responses are captured during navigation

    Args:
        email: Wealthsimple email
        password: Wealthsimple password
        pages_to_visit: list of URLs to navigate to after login.
                        API responses from all pages are captured.
                        If None, just logs in and returns to home.

    Returns (results_dict, error) where results_dict contains:
        - "access_token", "refresh_token" (if captured during login)
        - "api_responses": list of {url, status, body} for all captured API calls
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None, "Playwright not installed. Run: pip install playwright && playwright install chromium"

    try:
        from playwright_stealth import stealth_sync
    except ImportError:
        stealth_sync = None

    results = {"api_responses": []}
    profile_dir = _get_browser_profile_dir()

    try:
        with sync_playwright() as p:
            # Use persistent context for cookie/session persistence
            context = p.chromium.launch_persistent_context(
                profile_dir,
                headless=True,
                viewport={"width": 1280, "height": 720},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-features=IsolateOrigins,site-per-process",
                ],
            )

            page = context.pages[0] if context.pages else context.new_page()

            # Apply stealth if available
            if stealth_sync:
                stealth_sync(page)

            # Capture all interesting API responses
            def capture_response(response):
                url = response.url
                status = response.status

                # Capture auth tokens
                if "/oauth" in url and status == 200:
                    try:
                        body = response.json()
                        if "access_token" in body:
                            results["access_token"] = body["access_token"]
                            results["refresh_token"] = body.get("refresh_token")
                            results["expires_in"] = body.get("expires_in", 3600)
                    except Exception:
                        pass

                # Capture API data responses
                if status == 200 and any(x in url for x in [
                    "graphql", "/account", "/activities", "/positions",
                    "/balances", "/financials", "trade-service",
                ]):
                    try:
                        body = response.json()
                        results["api_responses"].append({
                            "url": url,
                            "status": status,
                            "body": body,
                        })
                    except Exception:
                        pass

            page.on("response", capture_response)

            # Check if we're already logged in by going to home
            page.goto("https://my.wealthsimple.com/app/home", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)

            current_url = page.url
            needs_login = "/login" in current_url or "/sign-in" in current_url

            if needs_login:
                # Navigate to login page
                if "/login" not in current_url:
                    page.goto("https://my.wealthsimple.com/app/login", wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(3000)

                # Fill email
                try:
                    page.wait_for_selector('input[type="email"], input[name="email"], input[autocomplete="username"]', timeout=15000)
                    email_input = page.query_selector('input[type="email"], input[name="email"], input[autocomplete="username"]')
                    if email_input:
                        email_input.fill(email)
                except Exception:
                    pass

                # Handle multi-step login (email → next → password)
                password_input = page.query_selector('input[type="password"]')
                if not password_input:
                    try:
                        submit = page.query_selector('button[type="submit"]')
                        if submit:
                            submit.click()
                        page.wait_for_timeout(3000)
                        password_input = page.wait_for_selector('input[type="password"]', timeout=10000)
                    except Exception:
                        pass

                if password_input:
                    password_input.fill(password)

                    submit = page.query_selector('button[type="submit"]')
                    if submit:
                        submit.click()
                    else:
                        password_input.press("Enter")

                    # Wait for login to complete or handle verification
                    for i in range(60):  # 60 seconds
                        page.wait_for_timeout(1000)
                        current_url = page.url

                        if any(x in current_url for x in ["/app/home", "/app/trade", "/app/invest"]):
                            break

                        # Check for OTP/verification code input
                        otp_input = page.query_selector(
                            'input[type="tel"], input[type="number"], '
                            'input[autocomplete="one-time-code"], '
                            'input[name="otp"], input[name="code"], '
                            'input[aria-label*="code"], input[aria-label*="verification"], '
                            'input[placeholder*="code"], input[placeholder*="digit"]'
                        )

                        if otp_input and i < 50:
                            # Prompt for the OTP code
                            print("\nWealthsimple sent a verification code to your phone.")
                            code = input("Enter the code: ").strip()

                            if code:
                                # Try multiple approaches for OTP entry
                                # Approach 1: Check for multiple single-digit inputs
                                digit_inputs = page.query_selector_all(
                                    'input[type="tel"], input[type="number"], '
                                    'input[maxlength="1"], input[autocomplete="one-time-code"]'
                                )

                                if len(digit_inputs) >= 4:
                                    # Individual digit boxes
                                    for idx, digit in enumerate(code):
                                        if idx < len(digit_inputs):
                                            digit_inputs[idx].fill(digit)
                                            page.wait_for_timeout(100)
                                else:
                                    # Single input — type character by character
                                    otp_input.click()
                                    otp_input.fill("")
                                    page.wait_for_timeout(200)
                                    otp_input.type(code, delay=100)

                                page.wait_for_timeout(2000)

                                # Try to submit — wait for button to become enabled
                                for _ in range(10):
                                    verify_btn = page.query_selector(
                                        'button[type="submit"]:not([disabled]), '
                                        'button:has-text("Verify"):not([disabled]), '
                                        'button:has-text("Confirm"):not([disabled]), '
                                        'button:has-text("Continue"):not([disabled]), '
                                        'button:has-text("Submit"):not([disabled])'
                                    )
                                    if verify_btn:
                                        verify_btn.click()
                                        break
                                    page.wait_for_timeout(500)
                                else:
                                    # No enabled button found — try pressing Enter
                                    page.keyboard.press("Enter")

                                page.wait_for_timeout(5000)
                                continue

                        # After a long wait with no OTP field found, check page text
                        if i == 45:
                            page_text = page.inner_text("body")[:500].lower()
                            if any(x in page_text for x in ["verify", "confirm", "security", "device"]):
                                # There's a verification step but we can't find the input
                                print(f"\nVerification page detected but no input field found.")
                                print(f"Current URL: {current_url}")
                                print(f"Page text: {page_text[:200]}")
                                context.close()
                                return None, "Could not find verification code input field"

            # Check if we made it to the app
            current_url = page.url
            logged_in = any(x in current_url for x in ["/app/home", "/app/trade", "/app/invest", "/app/account"])

            if not logged_in:
                # Last resort — check if we got an access token from intercepted responses
                if not results.get("access_token"):
                    context.close()
                    return None, f"Login did not complete. Final URL: {current_url}"

            # Wait a bit for home page API calls to fire
            page.wait_for_timeout(5000)

            # Navigate to additional pages to capture more data
            if pages_to_visit:
                for url in pages_to_visit:
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=20000)
                        page.wait_for_timeout(5000)
                    except Exception:
                        pass

            context.close()
            return results, None

    except Exception as e:
        return None, f"Browser session failed: {str(e)}"


def authenticate(pages_to_visit=None):
    """Open a browser session with Wealthsimple, capturing API data.

    Uses persistent browser profile so sessions survive between runs.
    All API responses during navigation are captured and returned.

    Args:
        pages_to_visit: optional list of URLs to navigate after login

    Returns (results_dict, error_string).
    results_dict contains "access_token", "api_responses", etc.
    """
    email, password = _get_credentials()
    if not email:
        return None, "Wealthsimple credentials not configured. Set WEALTHSIMPLE_EMAIL and WEALTHSIMPLE_PASSWORD in .env"

    results, err = _browser_session(email, password, pages_to_visit)
    if err:
        return None, err

    # Cache tokens if captured
    if results.get("access_token"):
        db = _get_db()
        _cache_tokens(db, results["access_token"], results.get("refresh_token"), results.get("expires_in", 3600))
        db.close()

    return results, None


def _cache_tokens(db, access_token, refresh_token, expires_in):
    """Cache auth tokens in soy_meta."""
    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=expires_in - 60)
    ).isoformat()

    db.execute(
        "INSERT OR REPLACE INTO soy_meta (key, value, updated_at) "
        "VALUES ('ws_access_token', ?, datetime('now'))",
        (access_token,),
    )
    if refresh_token:
        db.execute(
            "INSERT OR REPLACE INTO soy_meta (key, value, updated_at) "
            "VALUES ('ws_refresh_token', ?, datetime('now'))",
            (refresh_token,),
        )
    db.execute(
        "INSERT OR REPLACE INTO soy_meta (key, value, updated_at) "
        "VALUES ('ws_token_expires', ?, datetime('now'))",
        (expires_at,),
    )
    db.commit()


def _ws_get(path, token, base=None):
    """Make an authenticated GET request to Wealthsimple API."""
    if base is None:
        base = WS_AUTH_BASE
    db = _get_db()
    device_id = db.execute(
        "SELECT value FROM soy_meta WHERE key = 'ws_device_id'"
    ).fetchone()
    db.close()

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "x-wealthsimple-client": "@wealthsimple/wealthsimple",
        "x-ws-profile": "trade",
        "x-ws-api-version": "12",
    }
    if device_id:
        headers["x-ws-device-id"] = device_id["value"]

    # Try production API first, fall back to trade-service
    resp, err, _ = _api_request(f"{base}{path}", headers=headers)
    if err and "403" in str(err) and base != WS_TRADE_BASE:
        resp, err, _ = _api_request(f"{WS_TRADE_BASE}{path}", headers=headers)
    return resp, err


def _ws_graphql(query, variables, token):
    """Make a GraphQL request to Wealthsimple."""
    db = _get_db()
    device_id = db.execute(
        "SELECT value FROM soy_meta WHERE key = 'ws_device_id'"
    ).fetchone()
    db.close()

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "x-wealthsimple-client": "@wealthsimple/wealthsimple",
        "x-ws-profile": "trade",
        "x-ws-api-version": "12",
        "x-ws-locale": "en-CA",
        "x-platform-os": "web",
    }
    if device_id:
        headers["x-ws-device-id"] = device_id["value"]

    data = {"query": query, "variables": variables}
    resp, err, _ = _api_request(
        "https://my.wealthsimple.com/graphql",
        method="POST",
        headers=headers,
        data=data,
    )
    return resp, err


# ═══════════════════════════════════════════════════════════════
# Account Sync
# ═══════════════════════════════════════════════════════════════


def _get_cached_responses():
    """Get cached API responses from last browser session."""
    db = _get_db()
    row = db.execute(
        "SELECT value FROM soy_meta WHERE key = 'ws_cached_api_responses'"
    ).fetchone()
    db.close()
    if row:
        try:
            return json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            pass
    return None


def _extract_accounts_from_responses(api_responses):
    """Extract account data from captured Wealthsimple API responses."""
    for api_resp in api_responses:
        body = api_resp.get("body", {})
        url = api_resp.get("url", "")

        # REST account list
        if "/account" in url and isinstance(body, dict) and "results" in body:
            return body["results"]
        if "/account" in url and isinstance(body, list):
            return body

        # GraphQL responses containing accounts
        if "graphql" in url and isinstance(body, dict):
            data = body.get("data", {})
            for key in data:
                val = data[key]
                if isinstance(val, dict):
                    # Look for accounts in nested structures
                    if "accounts" in val:
                        accts = val["accounts"]
                        if isinstance(accts, dict) and "edges" in accts:
                            return [e.get("node", e) for e in accts["edges"]]
                        if isinstance(accts, list):
                            return accts
                    # Look for allAccounts or similar
                    for subkey in val:
                        subval = val[subkey]
                        if isinstance(subval, list) and subval and isinstance(subval[0], dict):
                            if any(k in subval[0] for k in ["id", "accountId", "unifiedAccountType"]):
                                return subval
    return None


def _extract_activities_from_responses(api_responses):
    """Extract activity/transaction data from captured Wealthsimple API responses."""
    activities = []
    seen_ids = set()

    for api_resp in api_responses:
        body = api_resp.get("body", {})
        if not isinstance(body, dict):
            continue

        data = body.get("data", {})

        # GraphQL activityFeedItems (the main source)
        if "activityFeedItems" in data:
            feed = data["activityFeedItems"]
            edges = feed.get("edges", []) if isinstance(feed, dict) else []
            for edge in edges:
                node = edge.get("node", edge)
                if isinstance(node, dict) and "canonicalId" in node:
                    cid = node["canonicalId"]
                    if cid not in seen_ids:
                        seen_ids.add(cid)
                        activities.append(node)

        # Also check for transfers
        if "transfers" in data:
            transfers = data["transfers"]
            results = transfers.get("results", []) if isinstance(transfers, dict) else []
            for t in results:
                if isinstance(t, dict) and "id" in t:
                    tid = t["id"]
                    if tid not in seen_ids:
                        seen_ids.add(tid)
                        activities.append(t)

    return activities


def sync_accounts(token=None):
    """Store Wealthsimple accounts from cached API responses.

    Uses data captured during 'setup' — no browser session needed.
    Run 'setup' first to capture fresh data.
    """
    api_responses = _get_cached_responses()
    if not api_responses:
        return {"error": "No cached data. Run 'setup' first (requires interactive browser login)."}

    accounts_data = _extract_accounts_from_responses(api_responses)

    if not accounts_data:
        # Debug: show what GraphQL operation names we captured
        ops = []
        for r in api_responses:
            body = r.get("body", {})
            if isinstance(body, dict) and "data" in body:
                ops.append(list(body["data"].keys()))
        return {"error": f"Could not find account data in {len(api_responses)} cached responses. GraphQL ops: {ops[:15]}"}

    db = _get_db()
    accounts = accounts_data if isinstance(accounts_data, list) else [accounts_data]

    synced = 0
    for acct in accounts:
        acct_id = acct.get("id", "")
        acct_type_raw = acct.get("account_type", acct.get("type", "other"))
        acct_type = WS_ACCOUNT_TYPE_MAP.get(acct_type_raw, "other")
        currency = acct.get("base_currency", "CAD")
        label = acct.get("nickname") or acct.get("account_type", "Wealthsimple")

        # Friendly label
        type_labels = {
            "rrsp": "RRSP",
            "tfsa": "TFSA",
            "investment": "Non-Registered",
            "chequing": "Cash",
            "savings": "Savings",
        }
        if label == acct_type_raw:
            label = f"Wealthsimple {type_labels.get(acct_type, acct_type.title())}"

        last4 = _obfuscate_account(acct_id)

        # Upsert account
        existing = db.execute(
            "SELECT id FROM financial_accounts WHERE source = 'wealthsimple' AND account_last4 = ?",
            (last4,),
        ).fetchone()

        if existing:
            db.execute(
                "UPDATE financial_accounts SET label = ?, account_type = ?, currency = ?, "
                "sync_cursor = ?, updated_at = datetime('now') WHERE id = ?",
                (label, acct_type, currency, acct_id, existing["id"]),
            )
        else:
            db.execute(
                """INSERT INTO financial_accounts
                   (source, account_type, label, account_last4, currency,
                    institution, is_business, status, sync_cursor)
                   VALUES ('wealthsimple', ?, ?, ?, ?, 'Wealthsimple', 0, 'active', ?)""",
                (acct_type, label, last4, currency, acct_id),
            )
        synced += 1

        # Store balance if available
        balance = acct.get("current_balance", {})
        if isinstance(balance, dict):
            bal_amount = float(balance.get("amount", 0))
        elif isinstance(balance, (int, float)):
            bal_amount = float(balance)
        else:
            bal_amount = None

        if bal_amount is not None:
            fin_acct = db.execute(
                "SELECT id FROM financial_accounts WHERE source = 'wealthsimple' AND account_last4 = ?",
                (last4,),
            ).fetchone()
            if fin_acct:
                db.execute(
                    "INSERT INTO financial_balances (account_id, balance, currency, as_of) "
                    "VALUES (?, ?, ?, datetime('now'))",
                    (fin_acct["id"], bal_amount, currency),
                )

    db.commit()
    db.close()

    return {"accounts_synced": synced}


# ═══════════════════════════════════════════════════════════════
# Transaction / Activity Sync
# ═══════════════════════════════════════════════════════════════


def _apply_categorization_rules(db, description, counterparty):
    """Apply transaction_rules to categorize a transaction."""
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

        if pattern in target:
            return {
                "category": rule["category"],
                "tax_category": rule["tax_category"],
                "t2125_number": rule["t2125_number"],
                "is_business": rule["is_business"],
            }

    return {}


def _parse_activity(activity, account_label="Wealthsimple"):
    """Parse a Wealthsimple activity into our schema fields.

    Handles both GraphQL activityFeedItems format and legacy REST format.
    """
    # Get type — GraphQL uses uppercase (DIVIDEND, DIY_BUY, etc.)
    act_type = (
        activity.get("type")
        or activity.get("object")
        or activity.get("transferType")
        or "unknown"
    )
    sub_type = activity.get("subType", "")
    status = activity.get("status") or activity.get("state", "")

    # Skip pending/cancelled/failed
    if status and status.lower() in ("cancelled", "failed", "rejected"):
        return None

    # Amount — GraphQL uses flat 'amount' field, REST uses nested dict
    amount_raw = activity.get("amount")
    currency = activity.get("currency", "CAD")

    if isinstance(amount_raw, dict):
        amount = float(amount_raw.get("amount", 0) or 0)
        currency = amount_raw.get("currency", currency)
    elif amount_raw is not None:
        try:
            amount = float(amount_raw)
        except (ValueError, TypeError):
            amount = 0
    else:
        amount = 0

    # Determine sign from amountSign field or type
    amount_sign = (activity.get("amountSign") or "").lower()
    txn_type_map = {
        "dividend": ("dividend", 1),
        "cash_dividend": ("dividend", 1),
        "diy_buy": ("purchase", -1),
        "buy": ("purchase", -1),
        "diy_sell": ("sale", 1),
        "sell": ("sale", 1),
        "deposit": ("deposit", 1),
        "withdrawal": ("withdrawal", -1),
        "internal_transfer": ("transfer", 1),
        "institutional_transfer": ("transfer", 1),
        "fee": ("fee", -1),
        "interest": ("interest", 1),
        "refund": ("refund", 1),
        "referral_bonus": ("deposit", 1),
        "giveaway_bonus": ("deposit", 1),
        "contribution": ("contribution", -1),
        "payment": ("purchase", -1),
        "aft": ("transfer", 1),
        "p2p_payment": ("transfer", -1),
        "spend": ("purchase", -1),
    }

    type_key = act_type.lower().replace(" ", "_")
    if sub_type:
        sub_key = sub_type.lower().replace(" ", "_")
        txn_type, default_sign = txn_type_map.get(sub_key, txn_type_map.get(type_key, ("other", 1)))
    else:
        txn_type, default_sign = txn_type_map.get(type_key, ("other", 1))

    # Apply sign
    if amount_sign == "negative" or amount_sign == "minus":
        amount = -abs(amount)
    elif amount_sign == "positive" or amount_sign == "plus":
        amount = abs(amount)
    elif amount > 0 and default_sign < 0:
        amount = -amount

    # Description
    symbol = activity.get("assetSymbol", "")
    description_parts = []
    readable_type = act_type.replace("_", " ").title()
    if sub_type:
        readable_type = sub_type.replace("_", " ").title()
    description_parts.append(readable_type)
    if symbol:
        description_parts.append(symbol)
    qty = activity.get("assetQuantity")
    if qty:
        description_parts.append(f"x{qty}")

    # Add counterparty info if available
    merchant = activity.get("spendMerchant")
    originator = activity.get("aftOriginatorName")
    etransfer_name = activity.get("eTransferName")
    p2p = activity.get("p2pHandle")
    counterparty = merchant or originator or etransfer_name or p2p or account_label

    description = " ".join(description_parts) or f"Wealthsimple {act_type}"

    # Date
    occurred_at = (
        activity.get("occurredAt")
        or activity.get("completedAt")
        or activity.get("completed_at")
        or activity.get("created_at")
        or activity.get("expectedCompletionDate")
        or ""
    )
    txn_date = occurred_at[:10] if occurred_at else datetime.now().strftime("%Y-%m-%d")

    # External ID
    external_id = (
        activity.get("canonicalId")
        or activity.get("canonical_id")
        or activity.get("id")
    )

    if not external_id:
        return None

    return {
        "external_id": external_id,
        "transaction_date": txn_date,
        "posted_date": txn_date,
        "description": description,
        "amount": amount,
        "currency": currency,
        "txn_type": txn_type,
        "counterparty": counterparty,
        "raw_data": json.dumps(activity, default=str),
    }


def sync_activities(start_date=None, end_date=None, token=None):
    """Sync Wealthsimple activities from cached API responses.

    Uses data captured during 'setup' — no browser session needed.
    """
    # Ensure accounts are synced first
    acct_result = sync_accounts()
    if "error" in acct_result:
        return acct_result

    api_responses = _get_cached_responses()
    if not api_responses:
        return {"error": "No cached data. Run 'setup' first."}

    activities = _extract_activities_from_responses(api_responses)

    db = _get_db()

    # Get the default WS account (first one)
    default_acct = db.execute(
        "SELECT id, label FROM financial_accounts WHERE source = 'wealthsimple' LIMIT 1"
    ).fetchone()

    if not default_acct:
        db.close()
        return {"error": "No Wealthsimple accounts found."}

    total_stored = 0
    total_skipped = 0

    for activity in activities:
        parsed = _parse_activity(activity, default_acct["label"])
        if not parsed or not parsed["external_id"]:
            total_skipped += 1
            continue

        # Date filter
        if start_date and parsed["transaction_date"] < start_date:
            total_skipped += 1
            continue
        if end_date and parsed["transaction_date"] > end_date:
            total_skipped += 1
            continue

        # Find matching account
        acct_id_ws = activity.get("accountId") or activity.get("account_id")
        acct_row = None
        if acct_id_ws:
            last4 = _obfuscate_account(acct_id_ws)
            acct_row = db.execute(
                "SELECT id FROM financial_accounts WHERE source = 'wealthsimple' AND account_last4 = ?",
                (last4,),
            ).fetchone()
        if not acct_row:
            acct_row = default_acct

        # Dedup
        existing = db.execute(
            "SELECT id FROM financial_transactions WHERE account_id = ? AND external_id = ?",
            (acct_row["id"], parsed["external_id"]),
        ).fetchone()

        if existing:
            total_skipped += 1
            continue

        # Apply rules
        rules = _apply_categorization_rules(
            db, parsed["description"], parsed["counterparty"]
        )

        db.execute(
            """INSERT INTO financial_transactions
               (account_id, external_id, transaction_date, posted_date,
                description, description_clean, amount, currency, txn_type,
                counterparty,
                category, tax_category, t2125_number, is_business,
                raw_data)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                acct_row["id"],
                parsed["external_id"],
                parsed["transaction_date"],
                parsed["posted_date"],
                parsed["description"],
                parsed["description"],
                parsed["amount"],
                parsed["currency"],
                parsed["txn_type"],
                parsed["counterparty"],
                rules.get("category"),
                rules.get("tax_category"),
                rules.get("t2125_number"),
                rules.get("is_business", 0),
                parsed["raw_data"],
            ),
        )
        total_stored += 1

    # Update timestamps
    ws_accounts = db.execute(
        "SELECT id FROM financial_accounts WHERE source = 'wealthsimple'"
    ).fetchall()
    for ws_acct in ws_accounts:
        db.execute(
            "UPDATE financial_accounts SET last_synced_at = datetime('now'), "
            "updated_at = datetime('now') WHERE id = ?",
            (ws_acct["id"],),
        )
    db.execute(
        "INSERT OR REPLACE INTO soy_meta (key, value, updated_at) "
        "VALUES ('wealthsimple_last_synced', datetime('now'), datetime('now'))"
    )
    db.commit()

    result = {
        "stored": total_stored,
        "skipped": total_skipped,
        "activities_found": len(activities),
        "accounts": len(ws_accounts),
    }

    db.execute(
        "INSERT INTO activity_log (entity_type, entity_id, action, details, created_at) "
        "VALUES ('financial_sync', 0, 'wealthsimple_sync', ?, datetime('now'))",
        (json.dumps(result),),
    )
    db.commit()
    db.close()

    return result


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════


def cmd_setup(args):
    """Test Wealthsimple credentials."""
    email, password = _get_credentials()
    if not email:
        print("Wealthsimple not configured.")
        print("Add these to your .env file:")
        print("  WEALTHSIMPLE_EMAIL=your_email")
        print("  WEALTHSIMPLE_PASSWORD=your_password")
        sys.exit(1)

    print(f"Authenticating as {email}...")
    results, err = authenticate(pages_to_visit=[
        "https://my.wealthsimple.com/app/activity",
        "https://my.wealthsimple.com/app/accounts",
    ])
    if err:
        print(f"Auth failed: {err}")
        sys.exit(1)

    print("Wealthsimple connected.")
    token = results.get("access_token")
    print(f"  Token captured: {'Yes' if token else 'No (using session cookies)'}")
    api_responses = results.get("api_responses", [])
    print(f"  API responses captured: {len(api_responses)}")
    for r in api_responses[:10]:
        print(f"    {r.get('status', '?')} {r.get('url', '?')[:80]}")

    # Save captured API responses for offline use by other commands
    if api_responses:
        db = _get_db()
        db.execute(
            "INSERT OR REPLACE INTO soy_meta (key, value, updated_at) "
            "VALUES ('ws_cached_api_responses', ?, datetime('now'))",
            (json.dumps(api_responses),),
        )
        db.commit()
        db.close()
        print(f"\n  Cached {len(api_responses)} API responses for offline use.")
        print("  Run 'accounts' or 'sync' to process the data (no browser needed).")


def cmd_accounts(args):
    """List and sync accounts."""
    result = sync_accounts()
    if "error" in result:
        print(f"Error: {result['error']}")
        sys.exit(1)
    print(f"Synced {result['accounts_synced']} accounts.")

    db = _get_db()
    accounts = db.execute(
        "SELECT label, account_type, currency, account_last4 FROM financial_accounts "
        "WHERE source = 'wealthsimple'"
    ).fetchall()
    for acct in accounts:
        print(f"  {acct['label']} ({acct['account_type']}) [{acct['currency']}] ...{acct['account_last4']}")
    db.close()


def cmd_sync(args):
    """Sync activities/transactions."""
    days = 90
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

    print(f"Syncing Wealthsimple activities: {start_date} to {end_date}...")
    result = sync_activities(start_date, end_date)

    if "error" in result:
        print(f"Error: {result['error']}")
        sys.exit(1)

    print(f"  Stored: {result['stored']} new transactions")
    print(f"  Skipped: {result['skipped']} (duplicates/filtered)")
    print(f"  Accounts: {result['accounts']}")


def cmd_balances(args):
    """Fetch and display balances."""
    token, err = authenticate()
    if err:
        print(f"Error: {err}")
        sys.exit(1)

    sync_accounts(token)

    db = _get_db()
    balances = db.execute(
        """SELECT fa.label, fb.balance, fb.currency, fb.as_of
           FROM financial_balances fb
           JOIN financial_accounts fa ON fa.id = fb.account_id
           WHERE fa.source = 'wealthsimple'
           AND fb.id IN (
               SELECT MAX(id) FROM financial_balances
               GROUP BY account_id
           )"""
    ).fetchall()

    for b in balances:
        print(f"  {b['label']}: ${b['balance']:.2f} {b['currency']} (as of {b['as_of']})")
    db.close()


def cmd_status(args):
    """Show sync status."""
    db = _get_db()

    last_sync = db.execute(
        "SELECT value FROM soy_meta WHERE key = 'wealthsimple_last_synced'"
    ).fetchone()

    accounts = db.execute(
        "SELECT COUNT(*) as c FROM financial_accounts WHERE source = 'wealthsimple'"
    ).fetchone()

    txn_count = db.execute(
        "SELECT COUNT(*) as c FROM financial_transactions ft "
        "JOIN financial_accounts fa ON fa.id = ft.account_id "
        "WHERE fa.source = 'wealthsimple'"
    ).fetchone()

    email, _ = _get_credentials()

    print("Wealthsimple Sync Status")
    print("=" * 40)
    print(f"  Configured:    {'Yes' if email else 'No'}")
    print(f"  Last synced:   {last_sync['value'] if last_sync else 'Never'}")
    print(f"  Accounts:      {accounts['c'] if accounts else 0}")
    print(f"  Transactions:  {txn_count['c'] if txn_count else 0}")

    db.close()


def main():
    if len(sys.argv) < 2:
        print("Usage: sync_wealthsimple.py <setup|accounts|sync|balances|status>")
        print("")
        print("Commands:")
        print("  setup              Test Wealthsimple credentials")
        print("  accounts           List and sync accounts")
        print("  sync [--days N]    Sync recent activity (default 90 days)")
        print("  sync --from DATE --to DATE   Sync specific date range")
        print("  balances           Fetch current balances")
        print("  status             Show sync status")
        sys.exit(1)

    command = sys.argv[1]
    rest = sys.argv[2:]

    if command == "setup":
        cmd_setup(rest)
    elif command == "accounts":
        cmd_accounts(rest)
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
