# Multi-Google-Account Support for Software of You

## Applying the Patch

From the repo root:

```bash
git apply multi-google-account-support.patch
```

If there are conflicts (e.g., other changes to the same files since this was generated):

```bash
git apply --3way multi-google-account-support.patch
```

After applying, run bootstrap to execute the new migrations:

```bash
bash shared/bootstrap.sh
```

This creates the `google_accounts` table, adds `account_id` columns to `emails` and `calendar_events`, creates the `tokens/` directory, and updates the computed views.

---

## What Changed

### New files (4)

| File | Purpose |
|------|---------|
| `data/migrations/015_multi_google_accounts.sql` | `google_accounts` table + `account_id` on `emails`/`calendar_events` |
| `data/migrations/016_multi_account_views.sql` | Updated `v_discovery_candidates` and `v_nudge_items` to exclude connected account emails |
| `mcp-server/src/software_of_you/migrations/015_multi_google_accounts.sql` | Copy for MCP server |
| `mcp-server/src/software_of_you/migrations/016_multi_account_views.sql` | Copy for MCP server |

### Modified files (7)

| File | Changes |
|------|---------|
| `shared/google_auth.py` | Per-account tokens in `tokens/` dir, `list_accounts()`, `migrate_legacy_token()`, DB registration, new `accounts` CLI command |
| `mcp-server/src/software_of_you/google_auth.py` | Same changes ported for MCP server (pathlib, uses `execute`/`execute_write`) |
| `mcp-server/src/software_of_you/google_sync.py` | `account_email` param on all sync functions, `sync_all_accounts()`, direction detection per-account |
| `shared/scheduled_sync.sh` | Replaced inline sync with `sync_all_accounts()` |
| `shared/sync_transcripts.py` | Multi-account iteration in `cmd_scan()` |
| `shared/bootstrap.sh` | Creates `tokens/` directory |
| `commands/google-setup.md` | Multi-account UX in `/google-setup` |

---

## How It Works

### Token storage

Before: single `~/.local/share/software-of-you/google_token.json`

After: `~/.local/share/software-of-you/tokens/<email>.json` (one file per account)

Example:
```
tokens/
  j.alex.somerville_gmail.com.json
  alex_alexsomerville.com.json
```

### Legacy migration

On first use after applying the patch, `migrate_legacy_token()` runs automatically:
1. Loads the old `google_token.json`
2. Calls the userinfo API to discover the email
3. Moves the file to `tokens/<email>.json`
4. Registers the account in `google_accounts` table
5. Backfills all existing `emails` and `calendar_events` rows with `account_id`
6. Deletes the old file

No user action required — it just happens.

### Direction detection

Previously: called userinfo API on every sync to get the user's email, compared against `from_address`.

Now: each sync function receives `account_email` as a parameter. Direction is `outbound` when `from_email == account_email`, `inbound` otherwise. This correctly handles cross-account emails (alex@ sending to j.alex@ shows as outbound in alex@'s sync, inbound in j.alex@'s sync).

### Sync flow

`sync_all_accounts()` iterates all active accounts in `google_accounts`, gets each one's token, and runs `sync_gmail()`, `sync_calendar()`, and `sync_transcripts()` for each. Falls back to legacy single-token behavior if no accounts are registered.

---

## How to Add Multiple Accounts

### For users (via Claude)

Just run `/google-setup`. It shows connected accounts and offers to add more. The auth flow auto-detects the email and registers it.

### Programmatically

```bash
# Connect a new account (opens browser)
python3 shared/google_auth.py auth

# List connected accounts
python3 shared/google_auth.py accounts

# Get token for a specific account
python3 shared/google_auth.py token alex@alexsomerville.com

# Check status of all accounts
python3 shared/google_auth.py status

# Revoke a specific account
python3 shared/google_auth.py revoke alex@alexsomerville.com
```

### Database

```sql
-- See all connected accounts
SELECT * FROM google_accounts;

-- See emails by account
SELECT ga.email, COUNT(*) FROM emails e
JOIN google_accounts ga ON ga.id = e.account_id
GROUP BY ga.email;

-- See which account an email came from
SELECT e.subject, e.direction, ga.email AS account
FROM emails e
JOIN google_accounts ga ON ga.id = e.account_id
LIMIT 10;
```

---

## Backward Compatibility

- All functions accept `email=None` and fall back to legacy behavior
- `soy_meta` timestamps are still updated at the global key (`gmail_last_synced`) for compatibility, plus per-account keys (`gmail_last_synced:alex@example.com`)
- If `google_accounts` table is empty, everything works as before (single token)
- Computed views handle the case where `google_accounts` is empty (the `NOT IN` subquery returns nothing, so no extra filtering occurs)
