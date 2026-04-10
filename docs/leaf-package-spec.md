# SoY Leaf Package Specification

**Version:** 0.1.0
**Status:** Draft

---

## What Is a Leaf?

A **leaf** is a SoY module that was generated (fully or partially) by the Signal Harvester pipeline. It follows the same module format as any SoY module but carries metadata about its origin — which signal or forecast inspired it, what the pipeline produced, and how it evolved.

Leaves can be:
- **SoY modules** — personal data tools that plug into the platform (new tables, new commands, new cross-module enhancements)
- **Standalone products** — apps, services, or physical product orchestrations that live outside SoY but are tracked by it
- **Hybrid** — a SoY module that also has an external-facing component

## Leaf Manifest Format

Every leaf lives in `modules/<leaf-name>/` and contains a `manifest.json`:

```json
{
  "name": "leaf-name",
  "display_name": "Human-Readable Name",
  "version": "0.1.0",
  "description": "What this leaf does",
  "leaf": true,
  "origin": {
    "source": "signal|forecast|human",
    "signal_id": 12,
    "forecast_id": null,
    "harvest_date": "2026-03-27",
    "original_pain": "The pain point that inspired this",
    "industry": "Retail/E-commerce",
    "composite_score_at_approval": 5.3
  },
  "migration": "0XX_leaf_name.sql",
  "tables": [],
  "tools": [],
  "commands": [],
  "standalone_features": [],
  "enhancements": [],
  "deployment": {
    "type": "soy_module|standalone_web|chrome_extension|api_service|physical_fulfillment|bot",
    "url": null,
    "infrastructure": {}
  },
  "economics": {
    "revenue_model": "recurring_passive|recurring_active|one_time|usage_based",
    "pricing": {},
    "mrr_actual": 0,
    "total_revenue": 0,
    "costs_monthly": 0
  },
  "autonomy": {
    "score": 8,
    "setup": 7,
    "operation": 9,
    "support": 8,
    "maintenance": 7,
    "human_touchpoints": ["Monthly content review", "Customer escalations"]
  },
  "status": "idea|building|beta|shipped|profitable|killed",
  "shipped_at": null,
  "killed_at": null,
  "kill_reason": null
}
```

## Leaf Lifecycle

```
Signal/Forecast → Approved → Building → Beta → Shipped → Profitable
                                                  ↓
                                                Killed (with reason)
```

1. **Approved** — human signs off on the signal or forecast
2. **Building** — agent swarm (or manual Claude Code) constructs the leaf
3. **Beta** — deployed but not monetized, gathering feedback
4. **Shipped** — live and earning (or available to users)
5. **Profitable** — revenue exceeds costs
6. **Killed** — didn't work out (reason logged for evolution learning)

## How Leaves Connect to SoY

### As a Module
Standard SoY module: migration creates tables, manifest declares features, cross-module enhancements activate automatically. Users install via the module system.

### As a Standalone Product
The leaf's `deployment` block describes where it lives. SoY tracks it via `harvest_builds`:
- Revenue flows back to `harvest_builds.revenue`
- Status updates flow back to `harvest_builds.status`
- The evolution engine learns from outcomes

### As a Physical Product Chain
For service-chain and physical products:
- `deployment.type` = `"physical_fulfillment"`
- `deployment.infrastructure` describes the API chain (print provider, shipping API, storefront)
- `economics` tracks actual costs and margins
- `autonomy.human_touchpoints` lists where human involvement is needed

## Revenue Tracking

All leaf revenue is tracked in two places:
1. `harvest_builds.revenue` — aggregate per build
2. `manifest.json > economics.mrr_actual` — current monthly rate

The evolution engine uses revenue data to:
- Score which industries produce profitable leaves
- Weight future triage toward patterns that generated revenue
- Identify which build types (SaaS vs. physical vs. service chain) perform best

## Generating a Leaf from an Approved Signal

When a signal is approved for build:

1. Create `modules/<leaf-name>/manifest.json` with origin metadata
2. Create migration if the leaf needs SoY tables
3. Create build entry in `harvest_builds`
4. Build the thing (agent-assisted or manual)
5. Update manifest status through lifecycle
6. Track revenue and feed back to evolution engine

## Generating a Leaf from a Forecast

Same as above, but `origin.source = "forecast"` and `origin.forecast_id` is set instead of `signal_id`.
