#!/usr/bin/env python3
"""
GSD Bridge — Connects the Signal Harvester pipeline to GSD's headless build system.

When Paperclip dispatches a build:
1. Generates REQUIREMENTS.md from signal/forecast data
2. Generates answers.json for GSD's auto-mode prompts
3. Creates a build workspace with PREFERENCES.md
4. Invokes GSD headless
5. Parses results and feeds back to the pipeline

Usage:
  python3 gsd_bridge.py prepare <signal_id|forecast_id> [--type=signal|forecast]
  python3 gsd_bridge.py build <workspace_path> [--budget=75] [--timeout=3600]
  python3 gsd_bridge.py status <workspace_path>
  python3 gsd_bridge.py feedback <workspace_path>   # feed outcome to Q-router
"""

import sys
import os
import json
import sqlite3
import shutil
import subprocess
import argparse
from datetime import datetime

PLUGIN_ROOT = os.environ.get("CLAUDE_PLUGIN_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(PLUGIN_ROOT, "data", "soy.db")
TEMPLATES_DIR = os.path.join(PLUGIN_ROOT, "shared", "gsd-templates")
BUILDS_DIR = os.path.join(PLUGIN_ROOT, "builds")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def get_signal_data(db, signal_id):
    """Fetch all data for a signal build."""
    signal = db.execute("""
        SELECT s.*, t.composite_score, t.market_size_score, t.monetization_score,
               t.build_complexity_score, t.existing_solutions_score, t.soy_leaf_fit_score,
               t.existing_solutions, t.monetization_model, t.build_estimate,
               t.target_audience, t.human_notes
        FROM harvest_signals s
        JOIN harvest_triage t ON t.signal_id = s.id
        WHERE s.id = ? AND t.verdict = 'approved'
    """, (signal_id,)).fetchone()

    if not signal:
        return None

    # Get competitive intel for the same industry
    competitive = db.execute("""
        SELECT target_product, complaint_summary, complaint_type, missing_features,
               composite_score, switchability_score, build_advantage_score
        FROM competitive_signals
        WHERE target_category = ? AND complaint_summary IS NOT NULL
        ORDER BY composite_score DESC LIMIT 5
    """, (signal["industry"],)).fetchall()

    return {"signal": dict(signal), "competitive": [dict(c) for c in competitive]}


def get_forecast_data(db, forecast_id):
    """Fetch all data for a forecast build."""
    forecast = db.execute("""
        SELECT * FROM harvest_forecasts WHERE id = ? AND status = 'approved'
    """, (forecast_id,)).fetchone()

    if not forecast:
        return None

    # Get related signals for context
    related = db.execute("""
        SELECT s.extracted_pain, s.industry, t.composite_score
        FROM harvest_signals s
        JOIN harvest_triage t ON t.signal_id = s.id
        WHERE s.industry = ? AND t.verdict = 'approved'
        ORDER BY t.composite_score DESC LIMIT 5
    """, (forecast["industry"],)).fetchall()

    # Get competitive intel
    competitive = db.execute("""
        SELECT target_product, complaint_summary, complaint_type, missing_features,
               composite_score
        FROM competitive_signals
        WHERE target_category = ? AND complaint_summary IS NOT NULL
        ORDER BY composite_score DESC LIMIT 5
    """, (forecast["industry"],)).fetchall()

    return {
        "forecast": dict(forecast),
        "related_signals": [dict(r) for r in related],
        "competitive": [dict(c) for c in competitive],
    }


def generate_requirements(data, source_type):
    """Generate REQUIREMENTS.md from pipeline data."""
    if source_type == "signal":
        sig = data["signal"]
        pain = sig.get("extracted_pain") or sig.get("raw_text", "")[:500]
        original_text = sig.get("raw_text", "")[:1000]
        industry = sig.get("industry", "Unknown")
        composite = sig.get("composite_score", "?")
        audience = sig.get("target_audience", "Unknown")
        existing = sig.get("existing_solutions", "None known")
        monetization = sig.get("monetization_model", "TBD")
        source_url = sig.get("source_url", "")
        source_id = sig.get("id", "?")
        build_type = "standalone_saas"
        autonomy = 8
        build_days = sig.get("build_estimate", "7-14 days")
        soy_fit = sig.get("soy_leaf_fit_score", 5)
    else:
        fc = data["forecast"]
        pain = fc.get("description", "")
        original_text = fc.get("origin_reasoning", "")
        industry = fc.get("industry", "Unknown")
        composite = fc.get("composite_score", "?")
        audience = fc.get("target_audience", "Unknown")
        existing = "See competitive analysis below"
        build_type = fc.get("build_type", "standalone_saas")
        autonomy = fc.get("autonomy_score", 7)
        build_days = fc.get("estimated_build_days", 14)
        soy_fit = fc.get("soy_leaf_fit_score", 5)
        source_url = ""
        source_id = fc.get("id", "?")

        # Parse monetization strategy
        monetization = "TBD"
        try:
            strat = json.loads(fc.get("monetization_strategy", "{}"))
            monetization = strat
        except (json.JSONDecodeError, TypeError):
            pass

    # Build competitive analysis section
    comp_lines = []
    for c in data.get("competitive", []):
        features = ""
        if c.get("missing_features"):
            try:
                feats = json.loads(c["missing_features"])
                features = f" Missing: {', '.join(feats[:3])}"
            except (json.JSONDecodeError, TypeError):
                pass
        comp_lines.append(
            f"- **{c.get('target_product', '?')}** ({c.get('complaint_type', '?')}): "
            f"{c.get('complaint_summary', 'N/A')}{features}"
        )
    competitive_text = "\n".join(comp_lines) if comp_lines else "No competitive signals harvested for this industry."

    # Build monetization section
    if isinstance(monetization, dict):
        channels = monetization.get("channels", [])
        channel_lines = []
        for ch in channels:
            channel_lines.append(
                f"- **{ch.get('name', '?')}**: {ch.get('description', '')} "
                f"({ch.get('pricing', '?')} — {ch.get('estimated_monthly', '?')})"
            )
        revenue_channels = "\n".join(channel_lines) if channel_lines else "TBD"
        path_to_mrr = monetization.get("path_to_mrr", "TBD")
        key_assumption = monetization.get("key_assumption", "TBD")
        biggest_risk = monetization.get("biggest_risk", "TBD")
    else:
        revenue_channels = str(monetization)
        path_to_mrr = "TBD"
        key_assumption = "TBD"
        biggest_risk = "TBD"

    requires_physical = "No"
    physical_notes = ""
    if source_type == "forecast":
        fc = data["forecast"]
        if fc.get("requires_physical"):
            requires_physical = "Yes"
            physical_notes = fc.get("physical_complexity_notes", "")

    # Read template and fill
    template_path = os.path.join(TEMPLATES_DIR, "REQUIREMENTS.template.md")
    with open(template_path) as f:
        template = f.read()

    # Simple template substitution
    replacements = {
        "{{generated_at}}": datetime.now().isoformat(),
        "{{source_type}}": source_type,
        "{{source_id}}": str(source_id),
        "{{pain_point}}": pain,
        "{{original_signal_text}}": original_text,
        "{{source_url}}": source_url or "N/A",
        "{{industry}}": industry,
        "{{composite_score}}": str(composite),
        "{{target_audience}}": audience,
        "{{competitive_analysis}}": competitive_text,
        "{{existing_solutions}}": existing if isinstance(existing, str) else "See above",
        "{{build_advantage}}": "Our pipeline can build and iterate faster than incumbents. The competitive signals show specific missing features we can address.",
        "{{monetization_strategy}}": "See revenue channels below",
        "{{revenue_channels}}": revenue_channels,
        "{{path_to_mrr}}": path_to_mrr,
        "{{key_assumption}}": key_assumption,
        "{{biggest_risk}}": biggest_risk,
        "{{build_type}}": build_type,
        "{{autonomy_score}}": str(autonomy),
        "{{estimated_build_days}}": str(build_days),
        "{{budget_ceiling}}": "75.00",
        "{{requires_physical}}": requires_physical,
    }

    content = template
    for key, value in replacements.items():
        content = content.replace(key, value)

    # Handle conditional sections
    if physical_notes:
        content = content.replace("{{#physical_notes}}", "").replace("{{/physical_notes}}", "")
        content = content.replace("{{physical_complexity_notes}}", physical_notes)
    else:
        # Remove the physical notes block
        import re
        content = re.sub(r'\{\{#physical_notes\}\}.*?\{\{/physical_notes\}\}', '', content, flags=re.DOTALL)

    return content


def generate_answers(data, source_type):
    """Generate answers.json for GSD headless auto-mode."""
    if source_type == "signal":
        sig = data["signal"]
        title = sig.get("extracted_pain", "")[:80] or "Solution for harvested signal"
        description = sig.get("raw_text", "")[:500]
    else:
        fc = data["forecast"]
        title = fc.get("title", "Forecasted product")
        description = fc.get("description", "")

    return {
        "prompts": {
            "what would you like to build": f"{title}. See REQUIREMENTS.md for the full brief including pain point, competitive analysis, and monetization strategy.",
            "describe your project": f"{title} — {description[:200]}",
            "what is the goal": f"Build a shippable product that solves: {title}",
        },
    }


def cmd_prepare(args):
    """Prepare a build workspace from a signal or forecast."""
    db = get_db()
    source_type = args.type
    source_id = args.id

    # Fetch data
    if source_type == "signal":
        data = get_signal_data(db, source_id)
    else:
        data = get_forecast_data(db, source_id)

    if not data:
        print(f"No approved {source_type} #{source_id} found.")
        return

    # Create workspace
    slug = f"{source_type}-{source_id}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    workspace = os.path.join(BUILDS_DIR, slug)
    os.makedirs(workspace, exist_ok=True)

    # Generate REQUIREMENTS.md
    requirements = generate_requirements(data, source_type)
    with open(os.path.join(workspace, "REQUIREMENTS.md"), "w") as f:
        f.write(requirements)

    # Copy PREFERENCES.md
    shutil.copy(
        os.path.join(TEMPLATES_DIR, "PREFERENCES.md"),
        os.path.join(workspace, "PREFERENCES.md")
    )

    # Copy CONTEXT.md (pre-build research instructions)
    context_src = os.path.join(TEMPLATES_DIR, "CONTEXT.md")
    if os.path.exists(context_src):
        shutil.copy(context_src, os.path.join(workspace, "CONTEXT.md"))

    # Copy verification scripts
    scripts_src = os.path.join(TEMPLATES_DIR, "scripts")
    if os.path.exists(scripts_src):
        scripts_dst = os.path.join(workspace, "scripts")
        shutil.copytree(scripts_src, scripts_dst, dirs_exist_ok=True)
        # Make executable
        for script in os.listdir(scripts_dst):
            if script.endswith(".sh"):
                os.chmod(os.path.join(scripts_dst, script), 0o755)

    # Generate answers.json
    answers = generate_answers(data, source_type)
    with open(os.path.join(workspace, "answers.json"), "w") as f:
        json.dump(answers, f, indent=2)

    # Initialize .gsd directory structure (required for headless mode)
    os.makedirs(os.path.join(workspace, ".gsd", "milestones"), exist_ok=True)
    os.makedirs(os.path.join(workspace, ".gsd", "runtime"), exist_ok=True)

    # Copy PREFERENCES.md into .gsd/ as well (GSD reads from both locations)
    shutil.copy(
        os.path.join(workspace, "PREFERENCES.md"),
        os.path.join(workspace, ".gsd", "PREFERENCES.md")
    )

    # Initialize git repo
    subprocess.run(["git", "init"], cwd=workspace, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=workspace, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", f"Initial build workspace from {source_type} #{source_id}"],
        cwd=workspace, capture_output=True
    )

    # Store build metadata
    meta = {
        "source_type": source_type,
        "source_id": source_id,
        "workspace": workspace,
        "created_at": datetime.now().isoformat(),
        "status": "prepared",
    }
    with open(os.path.join(workspace, ".build-meta.json"), "w") as f:
        json.dump(meta, f, indent=2)

    # Update pipeline DB
    if source_type == "forecast":
        db.execute("""
            UPDATE harvest_forecasts SET status = 'building', updated_at = datetime('now')
            WHERE id = ?
        """, (source_id,))
    db.execute("""
        INSERT INTO activity_log (entity_type, entity_id, action, details, created_at)
        VALUES ('build', ?, 'workspace_prepared', ?, datetime('now'))
    """, (source_id, json.dumps(meta)))
    db.commit()

    print(f"Build workspace prepared: {workspace}")
    print(f"  REQUIREMENTS.md — signal data, competitive intel, monetization")
    print(f"  PREFERENCES.md — quality gates, verification, model routing")
    print(f"  answers.json — auto-mode prompts")
    print(f"\nTo build: python3 shared/gsd_bridge.py build {workspace}")

    db.close()
    return workspace


def cmd_build(args):
    """Invoke GSD headless on a prepared workspace."""
    workspace = os.path.abspath(args.workspace)

    if not os.path.exists(os.path.join(workspace, "REQUIREMENTS.md")):
        print(f"Not a valid build workspace: {workspace}")
        return

    budget = args.budget or 75
    timeout = (args.timeout or 3600) * 1000  # convert to ms

    # Update PREFERENCES.md budget
    prefs_path = os.path.join(workspace, "PREFERENCES.md")
    with open(prefs_path) as f:
        prefs = f.read()
    prefs = prefs.replace("budget_ceiling: 75.00", f"budget_ceiling: {budget:.2f}")
    with open(prefs_path, "w") as f:
        f.write(prefs)

    answers_path = os.path.join(workspace, "answers.json")

    cmd = [
        "gsd", "headless",
        "--timeout", str(timeout),
        "--json",
        "--answers", answers_path,
        "auto"
    ]

    print(f"Starting GSD build in {workspace}")
    print(f"  Budget: ${budget}")
    print(f"  Timeout: {timeout // 1000}s")
    print(f"  Command: {' '.join(cmd)}")

    # Update meta
    meta_path = os.path.join(workspace, ".build-meta.json")
    with open(meta_path) as f:
        meta = json.load(f)
    meta["status"] = "building"
    meta["build_started_at"] = datetime.now().isoformat()
    meta["budget"] = budget
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)

    # Run GSD headless
    log_path = os.path.join(workspace, "build.log")
    with open(log_path, "w") as log_file:
        result = subprocess.run(
            cmd,
            cwd=workspace,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            timeout=timeout // 1000 + 300,  # extra 5min grace
        )

    # Parse result
    meta["build_completed_at"] = datetime.now().isoformat()
    meta["exit_code"] = result.returncode

    exit_map = {0: "success", 1: "error", 10: "blocked", 11: "cancelled"}
    meta["status"] = exit_map.get(result.returncode, "unknown")

    # Try to parse cost from log
    try:
        with open(log_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("{"):
                    try:
                        event = json.loads(line)
                        if "cost" in event:
                            meta["cost"] = event["cost"]
                        if event.get("status"):
                            meta["gsd_status"] = event["status"]
                    except json.JSONDecodeError:
                        pass
    except Exception:
        pass

    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)

    print(f"\nBuild {'completed' if result.returncode == 0 else 'failed'}")
    print(f"  Status: {meta['status']}")
    print(f"  Exit code: {result.returncode}")
    print(f"  Log: {log_path}")

    if result.returncode == 0:
        print(f"\nTo feed results back: python3 shared/gsd_bridge.py feedback {workspace}")


def cmd_status(args):
    """Check build status."""
    workspace = os.path.abspath(args.workspace)
    meta_path = os.path.join(workspace, ".build-meta.json")

    if not os.path.exists(meta_path):
        print(f"No build meta found in {workspace}")
        return

    with open(meta_path) as f:
        meta = json.load(f)

    print(f"Build: {workspace}")
    print(f"  Source: {meta.get('source_type')} #{meta.get('source_id')}")
    print(f"  Status: {meta.get('status')}")
    print(f"  Created: {meta.get('created_at')}")
    if meta.get("build_started_at"):
        print(f"  Started: {meta.get('build_started_at')}")
    if meta.get("build_completed_at"):
        print(f"  Completed: {meta.get('build_completed_at')}")
    if meta.get("cost"):
        print(f"  Cost: ${meta['cost'].get('total', '?')}")
    if meta.get("exit_code") is not None:
        print(f"  Exit code: {meta.get('exit_code')}")


def cmd_feedback(args):
    """Feed build outcome back to Q-learning router and evolution engine."""
    workspace = os.path.abspath(args.workspace)
    meta_path = os.path.join(workspace, ".build-meta.json")

    if not os.path.exists(meta_path):
        print(f"No build meta found in {workspace}")
        return

    with open(meta_path) as f:
        meta = json.load(f)

    db = get_db()

    # Calculate reward based on outcome
    success = meta.get("status") == "success"
    cost = meta.get("cost", {}).get("total", 0)
    budget = meta.get("budget", 75)
    cost_efficiency = 1.0 - min(1.0, cost / budget) if budget > 0 else 0.5

    # Composite reward: success + cost efficiency
    reward = (0.7 if success else -0.3) + (0.3 * cost_efficiency)

    # Feed to Q-router
    try:
        sys.path.insert(0, os.path.join(PLUGIN_ROOT, "shared"))
        from q_router import QLearningRouter
        router = QLearningRouter()

        # The "signal text" for the router is the build brief
        req_path = os.path.join(workspace, "REQUIREMENTS.md")
        if os.path.exists(req_path):
            with open(req_path) as f:
                brief = f.read()[:500]
            router.learn(brief, "claude-opus-4-6", reward)
            print(f"Q-router feedback: reward={reward:.3f}")
    except ImportError:
        print("Q-router not available")

    # Update evolution engine
    source_type = meta.get("source_type")
    source_id = meta.get("source_id")

    db.execute("""
        INSERT INTO harvest_evolution_log (stage, change_type, description, reason)
        VALUES ('build', 'build_completed', ?, ?)
    """, (
        f"Build {meta.get('status')}: {source_type} #{source_id} in {workspace}",
        json.dumps({
            "success": success,
            "cost": cost,
            "budget": budget,
            "reward": reward,
            "cost_efficiency": cost_efficiency,
        }),
    ))

    # Update forecast/build status
    if source_type == "forecast":
        new_status = "shipped" if success else "idea"
        db.execute("""
            UPDATE harvest_forecasts SET status = ?, updated_at = datetime('now')
            WHERE id = ?
        """, (new_status, source_id))

    db.execute("""
        INSERT INTO activity_log (entity_type, entity_id, action, details, created_at)
        VALUES ('build', ?, 'build_feedback', ?, datetime('now'))
    """, (source_id, json.dumps({
        "status": meta.get("status"),
        "reward": reward,
        "cost": cost,
    })))

    db.commit()
    print(f"Pipeline feedback recorded. Reward: {reward:.3f}")
    db.close()


def cmd_deploy(args):
    """Provision services and deploy a completed build."""
    workspace = os.path.abspath(args.workspace)
    meta_path = os.path.join(workspace, ".build-meta.json")

    if not os.path.exists(meta_path):
        print(f"No build meta found in {workspace}")
        return

    with open(meta_path) as f:
        meta = json.load(f)

    if meta.get("status") != "success":
        print(f"Build status is '{meta.get('status')}' — only successful builds can be deployed.")
        if not args.force:
            return

    print(f"Deploy: {workspace}")
    print(f"{'='*60}")

    # Step 1: Provision services
    print("\n--- Step 1: Service Provisioning ---")
    provision_cmd = [sys.executable, os.path.join(PLUGIN_ROOT, "shared", "service_provisioner.py"),
                     "provision", workspace]
    subprocess.run(provision_cmd, cwd=PLUGIN_ROOT)

    # Step 2: Build the frontend (if applicable)
    print("\n--- Step 2: Build ---")
    pkg_json = os.path.join(workspace, "package.json")
    if os.path.exists(pkg_json):
        subprocess.run(["npm", "run", "build", "--if-present"], cwd=workspace, capture_output=True)
        print("  npm build: done")

    # Step 3: Deploy to Cloudflare Pages (if configured)
    print("\n--- Step 3: Deploy ---")
    db = get_db()
    build_id = os.path.basename(workspace)
    project_name = None

    cred_row = db.execute("""
        SELECT value FROM service_credentials
        WHERE build_id = ? AND service = 'cloudflare_pages' AND key = 'project_name'
    """, (build_id,)).fetchone()

    if cred_row:
        project_name = cred_row["value"]

    if project_name and project_name != "MANUAL_SETUP_REQUIRED":
        # Find the dist/build output directory
        dist_dir = None
        for candidate in ["dist", "build", "out", "packages/web/dist"]:
            if os.path.isdir(os.path.join(workspace, candidate)):
                dist_dir = candidate
                break

        if dist_dir:
            env = os.environ.copy()
            env["CLOUDFLARE_API_TOKEN"] = CF_API_TOKEN if 'CF_API_TOKEN' in dir() else os.environ.get("CLOUDFLARE_API_TOKEN", "")

            result = subprocess.run(
                ["wrangler", "pages", "deploy", dist_dir, f"--project-name={project_name}"],
                cwd=workspace, capture_output=True, text=True, env=env,
                timeout=120
            )

            if result.returncode == 0:
                # Parse deploy URL
                deploy_url = ""
                for line in result.stdout.split("\n"):
                    if "https://" in line and ".pages.dev" in line:
                        deploy_url = line.strip()
                        break

                meta["deploy_url"] = deploy_url
                meta["deployed_at"] = datetime.now().isoformat()
                with open(meta_path, "w") as f:
                    json.dump(meta, f, indent=2)

                print(f"  Deployed to: {deploy_url}")
            else:
                print(f"  Deploy failed: {result.stderr[:200]}")
        else:
            print(f"  No dist/build directory found — build may not have a frontend")
    else:
        print(f"  No Cloudflare Pages project configured — skipping deploy")

    # Step 4: Update Google OAuth redirect URIs (if we have a deploy URL)
    if meta.get("deploy_url"):
        print(f"\n--- Step 4: Post-deploy Config ---")
        print(f"  Deploy URL: {meta['deploy_url']}")
        print(f"  TODO: Update Google OAuth redirect URIs to include {meta['deploy_url']}/api/auth/callback")
        print(f"  TODO: Update CORS origins in the API")

    print(f"\n{'='*60}")
    print(f"Deploy complete.")
    db.close()


def main():
    parser = argparse.ArgumentParser(description="GSD Bridge — pipeline build dispatch")
    subparsers = parser.add_subparsers(dest="command")

    p_prep = subparsers.add_parser("prepare", help="Prepare build workspace")
    p_prep.add_argument("id", type=int, help="Signal or forecast ID")
    p_prep.add_argument("--type", choices=["signal", "forecast"], default="forecast")

    p_build = subparsers.add_parser("build", help="Run GSD headless build")
    p_build.add_argument("workspace", help="Path to prepared workspace")
    p_build.add_argument("--budget", type=float, help="Budget ceiling in dollars")
    p_build.add_argument("--timeout", type=int, help="Timeout in seconds")

    p_status = subparsers.add_parser("status", help="Check build status")
    p_status.add_argument("workspace")

    p_feedback = subparsers.add_parser("feedback", help="Feed outcome to pipeline")
    p_feedback.add_argument("workspace")

    p_deploy = subparsers.add_parser("deploy", help="Provision services and deploy")
    p_deploy.add_argument("workspace")
    p_deploy.add_argument("--force", action="store_true", help="Deploy even if build status isn't success")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    cmds = {
        "prepare": cmd_prepare,
        "build": cmd_build,
        "status": cmd_status,
        "feedback": cmd_feedback,
        "deploy": cmd_deploy,
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()
