#!/usr/bin/env python3
"""
Service Provisioner — Automated third-party service setup for pipeline builds.

When a build needs OAuth, databases, hosting, or payments, this provisioner
creates the resources automatically via CLI tools and APIs.

Supports:
  - Cloudflare (Pages, D1, Workers, KV) via wrangler CLI
  - Google OAuth via gcloud CLI (within existing project)
  - GitHub repos via gh CLI
  - JWT/secret generation
  - Stripe products/prices (when configured)

Usage:
  python3 service_provisioner.py provision <workspace_path>
  python3 service_provisioner.py status <workspace_path>
  python3 service_provisioner.py teardown <workspace_path>
  python3 service_provisioner.py list-accounts
"""

import sys
import os
import json
import secrets
import sqlite3
import subprocess
import argparse
from datetime import datetime

PLUGIN_ROOT = os.environ.get("CLAUDE_PLUGIN_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(PLUGIN_ROOT, "data", "soy.db")

# Cloudflare config
CF_API_TOKEN = os.environ.get("CLOUDFLARE_API_TOKEN", "<REDACTED-CF-TOKEN>")
CF_ACCOUNT_ID = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")

# Google Cloud config
GCP_PROJECT = os.environ.get("GCP_PROJECT", "signal-harvester")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def run_cmd(cmd, env=None, cwd=None, timeout=60):
    """Run a CLI command and return (success, stdout, stderr)."""
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    full_env["CLOUDFLARE_API_TOKEN"] = CF_API_TOKEN

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout, env=full_env, cwd=cwd
        )
        return result.returncode == 0, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "", "Command timed out"
    except FileNotFoundError:
        return False, "", f"Command not found: {cmd[0]}"


def store_credential(db, build_id, service, key, value, metadata=None):
    """Store a credential in the vault."""
    db.execute("""
        INSERT OR REPLACE INTO service_credentials
            (build_id, service, key, value, metadata, updated_at)
        VALUES (?, ?, ?, ?, ?, datetime('now'))
    """, (build_id, service, key, value, json.dumps(metadata) if metadata else None))
    db.commit()


def get_credential(db, build_id, service, key):
    """Retrieve a credential from the vault."""
    row = db.execute("""
        SELECT value, metadata FROM service_credentials
        WHERE build_id = ? AND service = ? AND key = ?
    """, (build_id, service, key)).fetchone()
    return row["value"] if row else None


def read_services_json(workspace):
    """Read the build's service requirements."""
    path = os.path.join(workspace, "services.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)

    # Auto-detect from REQUIREMENTS.md and source code
    services = {}
    req_path = os.path.join(workspace, "REQUIREMENTS.md")
    if os.path.exists(req_path):
        with open(req_path) as f:
            content = f.read().lower()
        if "cloudflare" in content or "pages" in content:
            services["hosting"] = {"provider": "cloudflare-pages"}
        if "d1" in content or "database" in content:
            services["database"] = {"provider": "cloudflare-d1"}
        if "google" in content and ("oauth" in content or "auth" in content):
            services["auth"] = {"provider": "google"}
        if "stripe" in content or "payment" in content:
            services["payments"] = {"provider": "stripe"}
        if "websocket" in content or "durable" in content:
            services["realtime"] = {"provider": "cloudflare-durable-objects"}

    # Also scan source for env var references
    for root, dirs, files in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in ("node_modules", ".git", ".gsd")]
        for fname in files:
            if fname.endswith((".ts", ".tsx", ".js", ".jsx", ".py")):
                try:
                    with open(os.path.join(root, fname)) as f:
                        src = f.read()
                    if "GOOGLE_CLIENT_ID" in src and "auth" not in services:
                        services["auth"] = {"provider": "google"}
                    if "STRIPE_" in src and "payments" not in services:
                        services["payments"] = {"provider": "stripe"}
                    if "JWT_SECRET" in src and "jwt" not in services:
                        services["jwt"] = {"provider": "auto"}
                except:
                    pass

    return services


def slugify(name):
    """Convert a name to a URL-safe slug."""
    import re
    slug = re.sub(r'[^a-z0-9]+', '-', name.lower().strip())
    return slug.strip('-')[:63]


# ===== Provisioners =====

def provision_jwt_secret(db, build_id, config):
    """Generate a secure JWT secret."""
    existing = get_credential(db, build_id, "jwt", "JWT_SECRET")
    if existing:
        print(f"  JWT_SECRET: already exists")
        return True

    secret = secrets.token_urlsafe(48)
    store_credential(db, build_id, "jwt", "JWT_SECRET", secret)
    print(f"  JWT_SECRET: generated (64 chars)")
    return True


def provision_cloudflare_pages(db, build_id, config):
    """Create a Cloudflare Pages project."""
    # Derive project name from build_id
    project_name = config.get("project") or slugify(build_id)

    existing = get_credential(db, build_id, "cloudflare_pages", "project_name")
    if existing:
        print(f"  Cloudflare Pages: project '{existing}' already exists")
        return True

    ok, stdout, stderr = run_cmd([
        "wrangler", "pages", "project", "create", project_name,
        "--production-branch", "main"
    ])

    if ok or "already exists" in stderr.lower():
        store_credential(db, build_id, "cloudflare_pages", "project_name", project_name,
                        {"url": f"https://{project_name}.pages.dev"})
        print(f"  Cloudflare Pages: created '{project_name}'")
        return True
    else:
        print(f"  Cloudflare Pages: FAILED — {stderr[:200]}")
        return False


def provision_cloudflare_d1(db, build_id, config):
    """Create a Cloudflare D1 database."""
    db_name = config.get("name") or f"{slugify(build_id)}-db"

    existing = get_credential(db, build_id, "cloudflare_d1", "database_id")
    if existing:
        print(f"  Cloudflare D1: database '{db_name}' already exists ({existing})")
        return True

    ok, stdout, stderr = run_cmd([
        "wrangler", "d1", "create", db_name
    ])

    if ok:
        # Parse database ID from output
        db_id = ""
        for line in stdout.split("\n"):
            if "database_id" in line:
                db_id = line.split("=")[-1].strip().strip('"').strip("'")
                break
            if "Created database" in line:
                # Sometimes format is different
                parts = line.split()
                for p in parts:
                    if len(p) > 30 and "-" in p:
                        db_id = p
                        break

        store_credential(db, build_id, "cloudflare_d1", "database_id", db_id,
                        {"name": db_name})
        store_credential(db, build_id, "cloudflare_d1", "database_name", db_name)
        print(f"  Cloudflare D1: created '{db_name}' (ID: {db_id[:20]}...)")
        return True
    else:
        if "already exists" in stderr.lower():
            print(f"  Cloudflare D1: '{db_name}' already exists")
            return True
        print(f"  Cloudflare D1: FAILED — {stderr[:200]}")
        return False


def provision_google_oauth(db, build_id, config):
    """Provision Google OAuth using the shared project client.

    All pipeline builds share one Google Cloud project and one OAuth client.
    Per-build: we store the shared credentials and note the redirect URI
    that needs to be added to the client's authorized redirects.
    """
    existing_id = get_credential(db, build_id, "google_oauth", "client_id")
    if existing_id and existing_id != "MANUAL_SETUP_REQUIRED":
        print(f"  Google OAuth: already provisioned ({existing_id[:30]}...)")
        return True

    # Get shared OAuth credentials from service_accounts
    account = db.execute(
        "SELECT config FROM service_accounts WHERE service = 'google_cloud' AND status = 'active'"
    ).fetchone()

    if not account:
        print(f"  Google OAuth: no Google Cloud account configured")
        print(f"    → Run: sqlite3 data/soy.db to add service_accounts entry")
        return False

    import json as _json
    gcp_config = _json.loads(account["config"])
    client_id = gcp_config.get("oauth_client_id", "")
    client_secret = gcp_config.get("oauth_client_secret", "")

    if not client_id or not client_secret:
        print(f"  Google OAuth: shared client credentials not configured")
        return False

    # Store the shared credentials for this build
    store_credential(db, build_id, "google_oauth", "client_id", client_id,
                    {"shared": True, "project": GCP_PROJECT})
    store_credential(db, build_id, "google_oauth", "client_secret", client_secret)

    # Note: redirect URIs need to be added to the Google Cloud Console
    # when the deploy URL is known (post-deploy step)
    print(f"  Google OAuth: using shared client ({client_id[:40]}...)")
    print(f"    → After deploy: add redirect URI to Google Cloud Console")
    return True


def provision_github_repo(db, build_id, config):
    """Create a GitHub repository."""
    repo_name = config.get("name") or slugify(build_id)

    existing = get_credential(db, build_id, "github", "repo_url")
    if existing:
        print(f"  GitHub: repo already exists ({existing})")
        return True

    ok, stdout, stderr = run_cmd([
        "gh", "repo", "create", repo_name,
        "--private", "--source=.", "--push"
    ], cwd=config.get("workspace"))

    if ok:
        repo_url = stdout.strip()
        store_credential(db, build_id, "github", "repo_url", repo_url,
                        {"name": repo_name})
        print(f"  GitHub: created {repo_url}")
        return True
    else:
        print(f"  GitHub: FAILED — {stderr[:200]}")
        return False


def provision_app_secret(db, build_id, name, length=48):
    """Generate a generic app secret."""
    existing = get_credential(db, build_id, "secrets", name)
    if existing:
        return True

    value = secrets.token_urlsafe(length)
    store_credential(db, build_id, "secrets", name, value)
    return True


# ===== Main Commands =====

def cmd_provision(args):
    """Provision all services needed by a build."""
    workspace = os.path.abspath(args.workspace)
    build_id = os.path.basename(workspace)
    db = get_db()

    print(f"Service Provisioner — {build_id}")
    print(f"Workspace: {workspace}")
    print(f"{'='*60}")

    services = read_services_json(workspace)

    if not services:
        print("No services detected. Create services.json or ensure REQUIREMENTS.md mentions needed services.")
        return

    print(f"Detected services: {', '.join(services.keys())}\n")

    results = {}

    # Always generate JWT secret if any auth is needed
    if "auth" in services or "jwt" in services:
        results["jwt"] = provision_jwt_secret(db, build_id, services.get("jwt", {}))

    # Cloudflare Pages
    if "hosting" in services:
        cfg = services["hosting"]
        if cfg.get("provider") == "cloudflare-pages":
            results["cloudflare_pages"] = provision_cloudflare_pages(db, build_id, cfg)

    # Cloudflare D1
    if "database" in services:
        cfg = services["database"]
        if cfg.get("provider") == "cloudflare-d1":
            results["cloudflare_d1"] = provision_cloudflare_d1(db, build_id, cfg)

    # Google OAuth
    if "auth" in services:
        cfg = services["auth"]
        if cfg.get("provider") == "google":
            results["google_oauth"] = provision_google_oauth(db, build_id, cfg)

    # GitHub
    if "repo" in services:
        results["github"] = provision_github_repo(db, build_id, services["repo"])

    # Generate .env file for the build
    generate_env_file(db, build_id, workspace)

    # Generate wrangler.toml if Cloudflare services are used
    if any(k.startswith("cloudflare") for k in results):
        generate_wrangler_config(db, build_id, workspace, services)

    print(f"\n{'='*60}")
    success = sum(1 for v in results.values() if v)
    total = len(results)
    manual = sum(1 for v in results.values() if not v)
    print(f"Provisioned: {success}/{total} services")
    if manual:
        print(f"Manual setup needed: {manual} services (check output above)")

    db.close()


def generate_env_file(db, build_id, workspace):
    """Generate .env file from provisioned credentials."""
    creds = db.execute("""
        SELECT service, key, value FROM service_credentials
        WHERE build_id = ? AND value != 'MANUAL_SETUP_REQUIRED'
    """, (build_id,)).fetchall()

    if not creds:
        return

    env_path = os.path.join(workspace, ".env")
    env_lines = ["# Auto-generated by Service Provisioner", f"# Build: {build_id}", ""]

    for cred in creds:
        env_key = cred["key"].upper()
        if cred["service"] == "cloudflare_pages":
            env_key = f"CF_{cred['key'].upper()}"
        elif cred["service"] == "google_oauth":
            env_key = f"GOOGLE_{cred['key'].upper()}"
        elif cred["service"] == "cloudflare_d1":
            env_key = f"D1_{cred['key'].upper()}"

        env_lines.append(f"{env_key}={cred['value']}")

    with open(env_path, "w") as f:
        f.write("\n".join(env_lines) + "\n")

    # Don't commit .env
    gitignore_path = os.path.join(workspace, ".gitignore")
    gitignore_content = ""
    if os.path.exists(gitignore_path):
        with open(gitignore_path) as f:
            gitignore_content = f.read()
    if ".env" not in gitignore_content:
        with open(gitignore_path, "a") as f:
            f.write("\n.env\n")

    print(f"\n  .env: generated with {len(creds)} credentials")


def generate_wrangler_config(db, build_id, workspace, services):
    """Generate or update wrangler.toml with provisioned resources."""
    toml_path = os.path.join(workspace, "wrangler.toml")

    project_name = get_credential(db, build_id, "cloudflare_pages", "project_name") or slugify(build_id)
    d1_id = get_credential(db, build_id, "cloudflare_d1", "database_id") or ""
    d1_name = get_credential(db, build_id, "cloudflare_d1", "database_name") or f"{project_name}-db"

    if os.path.exists(toml_path):
        # Don't overwrite existing wrangler.toml — the build may have customized it
        print(f"  wrangler.toml: exists, skipping generation (credentials in .env)")
        return

    config = f"""name = "{project_name}"
compatibility_date = "2026-03-27"
compatibility_flags = ["nodejs_compat"]

[vars]
ENVIRONMENT = "production"
"""

    if d1_id:
        config += f"""
[[d1_databases]]
binding = "DB"
database_name = "{d1_name}"
database_id = "{d1_id}"
"""

    if "realtime" in services:
        config += f"""
[durable_objects]
bindings = [
  {{ name = "SIGNAL_ROOM", class_name = "SignalRoom" }}
]

[[migrations]]
tag = "v1"
new_classes = ["SignalRoom"]
"""

    with open(toml_path, "w") as f:
        f.write(config)

    print(f"  wrangler.toml: generated")


def cmd_status(args):
    """Show provisioned services for a build."""
    workspace = os.path.abspath(args.workspace)
    build_id = os.path.basename(workspace)
    db = get_db()

    creds = db.execute("""
        SELECT service, key, value, metadata, created_at FROM service_credentials
        WHERE build_id = ?
        ORDER BY service, key
    """, (build_id,)).fetchall()

    if not creds:
        print(f"No services provisioned for {build_id}")
        return

    print(f"Services for {build_id}:")
    current_service = ""
    for cred in creds:
        if cred["service"] != current_service:
            current_service = cred["service"]
            print(f"\n  [{current_service}]")

        value = cred["value"]
        if len(value) > 20 and cred["key"] not in ("project_name", "database_name", "repo_url"):
            value = value[:8] + "..." + value[-4:]

        manual = " (MANUAL SETUP NEEDED)" if value == "MANUAL_SETUP_REQUIRED" else ""
        print(f"    {cred['key']}: {value}{manual}")

    db.close()


def cmd_teardown(args):
    """Remove provisioned services (careful!)."""
    workspace = os.path.abspath(args.workspace)
    build_id = os.path.basename(workspace)
    db = get_db()

    print(f"Teardown for {build_id}:")
    print(f"  This would delete Cloudflare projects, D1 databases, etc.")
    print(f"  Not implemented yet — manual teardown via wrangler/gcloud CLIs.")

    db.close()


def cmd_list_accounts(args):
    """List configured service accounts."""
    db = get_db()
    accounts = db.execute("SELECT * FROM service_accounts").fetchall()

    if not accounts:
        print("No service accounts configured.")
        print("\nTo add:")
        print("  Cloudflare: export CLOUDFLARE_API_TOKEN=...")
        print("  Google Cloud: gcloud auth login")
        print("  GitHub: gh auth login")
        print("  Stripe: stripe login")
        return

    for a in accounts:
        print(f"  [{a['service']}] {a['account_id'] or '?'} — {a['status']}")

    db.close()


def main():
    parser = argparse.ArgumentParser(description="Service Provisioner")
    subparsers = parser.add_subparsers(dest="command")

    p_prov = subparsers.add_parser("provision", help="Provision services for a build")
    p_prov.add_argument("workspace")

    p_status = subparsers.add_parser("status", help="Show provisioned services")
    p_status.add_argument("workspace")

    p_tear = subparsers.add_parser("teardown", help="Remove provisioned services")
    p_tear.add_argument("workspace")

    subparsers.add_parser("list-accounts", help="List service accounts")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    cmds = {
        "provision": cmd_provision,
        "status": cmd_status,
        "teardown": cmd_teardown,
        "list-accounts": cmd_list_accounts,
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()
