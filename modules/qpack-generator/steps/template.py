"""
Step 2: Template — Load question templates for installed modules.
"""

import json
from pathlib import Path
import sys
_mod_dir = Path(__file__).resolve().parents[1]
if str(_mod_dir) not in sys.path:
    sys.path.insert(0, str(_mod_dir))
from pipeline import PipelineStep

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates"


class TemplateStep(PipelineStep):
    name = "template"

    def __call__(self, ctx: dict) -> dict:
        log = ctx["_pipeline"].log
        enabled = ctx["enabled_modules"]

        templates = {}
        loaded = 0

        # Load all template files from the templates directory
        if TEMPLATE_DIR.exists():
            for f in sorted(TEMPLATE_DIR.glob("*.json")):
                try:
                    data = json.loads(f.read_text())
                    module_name = data.get("module", f.stem)
                    templates[module_name] = data
                    loaded += 1
                except (json.JSONDecodeError, KeyError) as e:
                    log(f"    WARN: failed to load {f.name}: {e}")

        # Also scan for extension QPacks in leaves/
        leaves_dir = Path(__file__).resolve().parents[3] / "leaves"
        if leaves_dir.exists():
            for qpack_file in leaves_dir.glob("*/qpacks/*.json"):
                try:
                    data = json.loads(qpack_file.read_text())
                    module_name = data.get("module", qpack_file.parent.parent.name)
                    templates[module_name] = data
                    loaded += 1
                except (json.JSONDecodeError, KeyError) as e:
                    log(f"    WARN: failed to load {qpack_file}: {e}")

        log(f"    loaded {loaded} template files")

        # Track which templates have matching enabled modules
        matched = [m for m in templates if m in enabled or m == "core"]
        unmatched = [m for m in templates if m not in enabled and m != "core"]
        if unmatched:
            log(f"    {len(unmatched)} templates without matching module: {', '.join(unmatched)}")
        log(f"    {len(matched)} templates matched to enabled modules")

        ctx["templates"] = templates
        return ctx
