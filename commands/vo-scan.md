---
description: Parse Backstage/CCC digests for eligible VO roles
allowed-tools: ["Bash", "Read"]
---

# VO Digest Scanner

Parse Backstage and Casting Call Club digest emails to find eligible voiceover roles. As an ACTRA member based in Toronto, Alex can take **non-union work outside Canada** — this tool filters digests to surface only eligible roles.

Database at `${CLAUDE_PLUGIN_ROOT:-$(pwd)}/data/soy.db`.

## Step 1: Find Recent Digests

Query for recent unprocessed Backstage and Casting Call Club digest emails:
```sql
SELECT id, gmail_id, subject, snippet, received_at FROM emails
WHERE from_address IN ('noreply@backstage.com', 'moderators@castingcall.club')
ORDER BY received_at DESC LIMIT 10;
```

Check which have already been processed:
```sql
SELECT value FROM soy_meta WHERE key = 'vo_digests_processed';
```

Filter out already-processed email IDs. If no unprocessed digests exist, tell the user:
"No new VO digests to scan. Your Backstage and CCC emails are up to date."

## Step 2: Fetch Full Email Bodies

For each unprocessed digest email, fetch the full body via Gmail API:

```python
# Get OAuth token
ACCESS_TOKEN=$(python3 "${CLAUDE_PLUGIN_ROOT:-$(pwd)}/shared/google_auth.py" token 2>/dev/null)
```

Then for each email:
```bash
curl -s -H "Authorization: Bearer $ACCESS_TOKEN" \
  "https://gmail.googleapis.com/gmail/v1/users/me/messages/<gmail_id>?format=full"
```

Decode the base64url-encoded body (HTML part). Convert to readable text.

## Step 3: Parse Role Listings

Each digest email contains multiple role listings. Parse the HTML body to extract individual roles. Look for repeated patterns/structures — each role typically includes:

- **Role/Project name**
- **Type** (commercial, animation, audiobook, podcast, video game, etc.)
- **Pay** (paid/unpaid, rate if listed)
- **Union status** (union, nonunion, SAG-AFTRA, etc.)
- **Location/Country** (USA, UK, remote, etc.)
- **Deadline** (if listed)
- **Description snippet**

For **Backstage** digests: roles are typically in card/list format with clear field labels.

For **Casting Call Club** digests: roles listed with "mod-approved" tag, pay amount, and category.

## Step 4: Filter for Eligibility

Apply ACTRA eligibility rules:

**INCLUDE a role if:**
- Union status is **non-union** (or unspecified) AND
- Location is **NOT Canada** (not Toronto, Vancouver, Montreal, etc.) OR location is unspecified/remote

**EXCLUDE a role if:**
- Union status is **union** (SAG-AFTRA is OK for non-Canadian productions, but ACTRA jobs should go through the agent)
- Location is explicitly **Canada** and it's a non-union production (ACTRA rules prohibit this)

**Flag as "check eligibility" if:**
- Location is unspecified or "remote" — Alex should verify the production country
- Union status is unclear

## Step 5: Present Filtered Results

Present the filtered roles in a clean format. For each matching role show:

```
### [Role/Project Name]
- **Type:** Animation | **Pay:** $200 | **Union:** Non-union
- **Location:** USA (Remote)
- **Deadline:** March 5, 2026
- [Brief description if available]
→ Add to pipeline? (yes/skip)
```

Group by source (Backstage vs CCC). Show the filter stats:
"Scanned **23 roles** from Backstage digest → **5 eligible matches** (filtered out 12 union, 6 Canadian)"

## Step 6: Add Selected Roles to Pipeline

For any role the user wants to add, create an audition record:
```sql
INSERT INTO auditions (project_name, role_name, role_type, production_type, source, status, received_at, notes)
VALUES (?, ?, 'voiceover', ?, 'backstage', 'reviewing', datetime('now'), ?);
```
(Use 'castingcallclub' for CCC roles)

Log activity:
```sql
INSERT INTO activity_log (entity_type, entity_id, action, details, created_at)
VALUES ('audition', <id>, 'created', 'Added from VO digest scan', datetime('now'));
```

## Step 7: Mark Digests Processed

After processing, update the processed list in soy_meta:
```sql
INSERT OR REPLACE INTO soy_meta (key, value, updated_at)
VALUES ('vo_digests_processed',
        COALESCE((SELECT value FROM soy_meta WHERE key = 'vo_digests_processed'), '') || ',<email_ids>',
        datetime('now'));
```

Confirm: "Scanned N digests. Added X roles to your pipeline. Use `/auditions` to see your full pipeline."
