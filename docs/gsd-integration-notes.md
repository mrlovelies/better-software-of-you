# GSD Integration — Hurdles & Fixes

Running log of issues encountered during GSD headless integration with the Signal Harvester pipeline.

---

## Hurdle 1: `.gsd/` directory required for headless mode
**Error:** `No .gsd/ directory found in current directory. Run 'gsd' interactively first to initialize a project.`

**Root cause:** GSD's headless mode checks for `.gsd/` before even calling `bootstrapGsdProject()`. The bootstrap function only creates `milestones/` and `runtime/` subdirs.

**Fix:** Create `.gsd/milestones/` and `.gsd/runtime/` in the `gsd_bridge.py prepare` step. Also copy PREFERENCES.md into `.gsd/`.

**Prevention:** Always scaffold `.gsd/` in the prepare step.

---

## Hurdle 2: Answer injection doesn't match GSD's prompts
**Error:** Build enters infinite loop asking "What's the vision?" because `answers.json` keys didn't match GSD's actual prompt text.

**Root cause:** GSD's discussion phase asks "What's the vision?" but our answers.json had keys like "what would you like to build". The headless answer matching uses substring search, but "vision" doesn't substring-match against "what would you like to build".

**What we tried:**
- Broader answer keys ("vision", "correct anything", "confirm", "ready", etc.)
- Still looped — the answer injection may not work reliably in headless auto mode

**Fix:** Use `--yolo seed.md` flag instead of `--answers`. The yolo flag provides the project vision via a seed file, bypassing the interactive discussion entirely.

**Prevention:** For pipeline builds, always use `--yolo` with a seed file generated from REQUIREMENTS.md. Don't rely on `--answers` for the initial project setup.

---

## Hurdle 3: `--yolo` expects a file path, not inline text
**Error:** `Yolo seed file not found: /path/to/Build drinkingaloneina.bar...`

**Root cause:** GSD's `auto --yolo` parser treats the next argument as a file path. We passed the vision text inline which was interpreted as a file name starting with "Build".

**Fix:** Create a `seed.md` file in the workspace with the vision/brief, then pass `"auto --yolo seed.md"` to headless.

**Prevention:** The `gsd_bridge.py prepare` step should generate `seed.md` alongside `REQUIREMENTS.md`.

---

## Hurdle 4: GSD headless command parsing
**Error:** Arguments after `headless` need careful quoting.

**Root cause:** `gsd headless [cmd] [args]` parses the command as a single string. `gsd headless auto --yolo seed.md` works, but `gsd headless --timeout 7200000 --json auto --yolo seed.md` may need the auto command quoted.

**Fix:** Use `gsd headless --timeout 7200000 --json "auto --yolo seed.md"` with the auto subcommand as a quoted string.

**Prevention:** Standardize the invocation format in gsd_bridge.py.

---

## Hurdle 5: SoY bootstrap.sh runs in build workspace
**Observation:** GSD (via Claude Code's CLAUDE.md) tries to run SoY's bootstrap.sh in the build workspace. It correctly detected "this is a forecast build, not a SoY plugin instance" and proceeded with the GSD workflow.

**Not a bug** — Claude Code's instructions include running bootstrap.sh at session start. The build workspace doesn't have it, so it moves on. But for cleanliness, we could add a `.claude.md` (note: lowercase) in the build workspace that says "Skip SoY bootstrap — this is a standalone product build."

---

## Recommendations for gsd_bridge.py Updates

1. Generate `seed.md` in the prepare step (not just answers.json)
2. Use `--yolo seed.md` invocation instead of `--answers`
3. Add a build-specific `.claude.md` or `CLAUDE.md` to the workspace
4. Document the exact invocation format
5. Add a pre-flight check that validates the workspace before building

---

*Last updated: 2026-03-27*
