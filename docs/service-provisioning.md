# Service Provisioning — Automated Setup for Pipeline Builds

**Problem:** Every product build needs third-party services (OAuth, databases, hosting, payments). Manual setup kills pipeline autonomy.

**Goal:** When the build pipeline says "I need Google OAuth for this product," the provisioner creates the project, credentials, and redirect URIs automatically.

---

## Service Categories

### Tier 1: Fully Automatable (API-driven, no human interaction)

| Service | API | What We Automate |
|---------|-----|-----------------|
| **Cloudflare Pages** | Wrangler CLI / Cloudflare API | Create project, deploy, configure custom domain, create D1 database, create KV namespace |
| **Cloudflare Workers** | Wrangler CLI | Deploy worker, set secrets, configure routes |
| **Cloudflare D1** | Wrangler CLI | Create database, run migrations, bind to worker |
| **GitHub Repos** | `gh` CLI | Create repo, set secrets, configure actions |
| **DNS (Cloudflare)** | Cloudflare API | Add A/CNAME records for custom domains |
| **Stripe** | Stripe CLI / API | Create product, price, webhook endpoint (requires existing Stripe account) |
| **Tailscale Serve/Funnel** | `tailscale` CLI | Expose port for staging previews |

### Tier 2: Semi-Automatable (API exists but requires initial human setup)

| Service | What's Automated | What's Manual |
|---------|-----------------|---------------|
| **Google OAuth** | Create credentials, set redirect URIs via Cloud API | Initial project creation + consent screen (one-time) |
| **Google Cloud** | Create projects, enable APIs via `gcloud` CLI | Initial org/billing setup |
| **Apple Developer** | App Store Connect API for app metadata | Certificate creation, provisioning profiles |

### Tier 3: Manual-Only (for now)

| Service | Why |
|---------|-----|
| **App Store / Play Store** | Submission requires human review |
| **Domain Registration** | Requires payment decision |
| **Business accounts** (bank, legal) | Requires identity verification |

---

## Architecture

### Service Provisioner (`shared/service_provisioner.py`)

```
Build starts → REQUIREMENTS.md specifies needed services
    ↓
Provisioner reads requirements
    ↓
For each service needed:
    1. Check if credentials already exist (in vault)
    2. If not, provision via API
    3. Store credentials in vault
    4. Inject into build workspace as env vars / wrangler.toml
    ↓
Build proceeds with all services configured
```

### Credential Vault

All provisioned credentials stored in:
- **SoY database** (`service_credentials` table) — encrypted at rest
- **Per-build `.env`** — generated from vault at build time, never committed to git
- **Cloudflare secrets** — for production deployments via `wrangler secret put`

### Google OAuth Automation

**One-time setup (already done):**
- Google Cloud project "Signal Harvester" exists
- OAuth consent screen configured
- `gcloud` CLI authenticated

**Per-build automation:**
```bash
# Create OAuth client for a new product
gcloud auth application-default login  # one-time
gcloud alpha iap oauth-clients create \
  --display-name="drinkingaloneina.bar" \
  --project=signal-harvester-xxxxx

# Or via REST API:
POST https://oauth2.googleapis.com/v2/projects/{project}/oauthClients
{
  "displayName": "drinkingaloneina.bar",
  "redirectUris": ["https://drinkingalone.bar/api/auth/callback"],
  "javascriptOrigins": ["https://drinkingalone.bar"]
}
```

**Reality check:** Google's OAuth client creation API is limited. The practical approach:
1. Use ONE Google Cloud project for all pipeline builds
2. Each build gets its own OAuth client ID within that project
3. Redirect URIs and origins are updated via API when deploy URL is known
4. Test users added programmatically

### Cloudflare Automation

**Already have:** Cloudflare API token (in memory: `reference_cloudflare_token.md`)

**Per-build:**
```bash
# Create Pages project
wrangler pages project create drinkingaloneina-bar

# Create D1 database
wrangler d1 create drinkingalone-db

# Deploy
wrangler pages deploy ./dist --project-name=drinkingaloneina-bar

# Set secrets
wrangler secret put JWT_SECRET --project=drinkingaloneina-bar
wrangler secret put GOOGLE_CLIENT_ID --project=drinkingaloneina-bar
```

### Stripe Automation

**Requires:** Existing Stripe account with API key

**Per-build:**
```bash
# Create product
stripe products create --name="drinkingaloneina.bar Premium"

# Create price
stripe prices create \
  --product=prod_xxxxx \
  --unit-amount=500 \
  --currency=usd \
  --recurring[interval]=month

# Create webhook endpoint
stripe webhook_endpoints create \
  --url="https://drinkingalone.bar/api/webhooks/stripe" \
  --enabled-events=checkout.session.completed,customer.subscription.updated
```

---

## Implementation Plan

### Phase 1: Cloudflare provisioning (immediate)
- Wrangler commands in the build pipeline
- Auto-create Pages project + D1 + Workers
- Auto-deploy on build completion

### Phase 2: Google OAuth provisioning
- Use gcloud CLI to create OAuth clients within our existing project
- Auto-configure redirect URIs based on deploy URL
- Store client ID/secret in vault

### Phase 3: Stripe provisioning
- Create products/prices from monetization strategy
- Configure webhook endpoints
- Store keys in vault

### Phase 4: Credential vault
- Encrypted storage in SoY database
- Per-build .env generation
- Secret rotation support

---

## Integration with GSD Build

In `gsd_bridge.py`, after build completion:
```python
def post_build_provision(workspace, deploy_url):
    """Provision services needed by the built product."""
    # Read REQUIREMENTS.md for service needs
    # Check vault for existing credentials
    # Provision missing services
    # Inject credentials into deployment config
    # Deploy with full configuration
```

The build itself should declare what services it needs in a `services.json`:
```json
{
  "auth": {"provider": "google", "scopes": ["openid", "email", "profile"]},
  "database": {"provider": "cloudflare-d1", "name": "drinkingalone-db"},
  "hosting": {"provider": "cloudflare-pages", "project": "drinkingaloneina-bar"},
  "payments": {"provider": "stripe", "products": [{"name": "Premium", "price_monthly": 500}]},
  "realtime": {"provider": "cloudflare-durable-objects"}
}
```

The provisioner reads this and handles everything.

---

*Last updated: 2026-03-27*
