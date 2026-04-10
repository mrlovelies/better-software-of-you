#!/usr/bin/env python3
"""
Persona Review Gate — Multi-perspective review of build plans before execution.

After GSD produces a ROADMAP, this dispatches 5 parallel "reviewer" agents,
each with a different cognitive bias, then runs a Red Teamer who attacks
the other reviews. Their feedback is synthesized and injected into the
build as REVIEW-FEEDBACK.md.

The personas:
  👤 User Advocate    — "Would I actually use this?"
  💰 Revenue Skeptic  — "Who's paying and why?"
  🔒 Security Hardliner — "What gets someone hurt?"
  ✂️ Simplicity Advocate — "This is too complex"
  ⚔️ Competitive Realist — "Why not use the existing thing?"
  🎯 Red Teamer       — "Where are the other reviewers wrong?"

Usage:
  python3 persona_review.py review <workspace_path>
  python3 persona_review.py synthesize <workspace_path>
  python3 persona_review.py full <workspace_path>  # review + red team + synthesize
"""

import sys
import os
import re
import json
import time
import argparse
import concurrent.futures
from urllib.request import Request, urlopen
from datetime import datetime

PLUGIN_ROOT = os.environ.get("CLAUDE_PLUGIN_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TEMPLATES_DIR = os.path.join(PLUGIN_ROOT, "templates")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://100.91.234.67:11434")
OLLAMA_HOST_14B = os.environ.get("OLLAMA_HOST_14B", "http://100.74.238.16:11434")

# Load personas
def load_personas():
    path = os.path.join(TEMPLATES_DIR, "review-personas.json")
    with open(path) as f:
        return json.load(f)


def call_anthropic(system_prompt, user_prompt, model="claude-sonnet-4-6", max_tokens=1500):
    """Call Claude API for persona reviews."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        # Try reading from claude config
        config_paths = [
            os.path.expanduser("~/.claude/credentials"),
            os.path.expanduser("~/.config/claude/credentials"),
        ]
        for cp in config_paths:
            if os.path.exists(cp):
                try:
                    with open(cp) as f:
                        for line in f:
                            if "api_key" in line.lower() or "anthropic" in line.lower():
                                # Try JSON
                                try:
                                    data = json.loads(f.read())
                                    api_key = data.get("api_key", "")
                                except:
                                    pass
                except:
                    pass

    if not api_key:
        return None, "No ANTHROPIC_API_KEY found"

    url = "https://api.anthropic.com/v1/messages"
    payload = json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
    }).encode()

    req = Request(url, data=payload, headers={
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    })

    try:
        with urlopen(req, timeout=600) as resp:
            data = json.loads(resp.read().decode())
            text = data.get("content", [{}])[0].get("text", "")
            return text, None
    except Exception as e:
        return None, str(e)


def call_ollama(system_prompt, user_prompt, model="qwen2.5:14b"):
    host = OLLAMA_HOST_14B if "14b" in model else OLLAMA_HOST
    """Fallback to local LLM for reviews."""
    url = f"{host}/api/chat"
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "options": {"temperature": 0.7, "num_predict": 2048},
    }).encode()

    req = Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read().decode())
            return data.get("message", {}).get("content", ""), None
    except Exception as e:
        return None, str(e)


def gather_build_context(workspace, spec_mode=False):
    """Read the build plan files for review.
    
    In spec_mode, reads SPEC.md as primary context (pre-build review).
    Otherwise, reads GSD milestone files (legacy build review).
    """
    context_parts = []

    if spec_mode:
        filenames = ["SPEC.md", "pipeline-data.json", "REQUIREMENTS.md"]
    else:
        filenames = ["REQUIREMENTS.md", ".gsd/milestones/M001/M001-ROADMAP.md",
                     ".gsd/milestones/M001/M001-CONTEXT.md", ".gsd/DECISIONS.md",
                     ".gsd/PROJECT.md", "SPEC.md"]

    for filename in filenames:
        path = os.path.join(workspace, filename)
        if os.path.exists(path):
            with open(path) as f:
                content = f.read()
            context_parts.append(f"## {filename}\n\n{content}")

    return "\n\n---\n\n".join(context_parts)



def call_claude_cli(system_prompt, user_prompt, model="claude-sonnet-4-6", max_tokens=1500):
    """Use Claude CLI (subscription) for persona reviews."""
    import subprocess
    prompt = f"""<system>{system_prompt}</system>

{user_prompt}"""
    try:
        result = subprocess.run(
            ["claude", "-p", "--dangerously-skip-permissions", "--model", model, "--output-format", "text"],
            input=prompt, capture_output=True, text=True, timeout=300
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip(), None
        return None, result.stderr[:200] or "Empty response"
    except subprocess.TimeoutExpired:
        return None, "CLI timed out after 300s"
    except FileNotFoundError:
        return None, "claude CLI not found"

def run_persona_review(persona, context, use_api=True):
    """Run a single persona's review."""
    user_prompt = f"""Review this product build plan. Apply your specific perspective ruthlessly.

{context}

Now give your review. Be specific, blunt, and actionable. No filler.

REQUIRED: End your review with a score line in this exact format:
SCORE: [1-10] where 1=kill this immediately, 5=needs investigation, 10=build now
CONFIDENCE: [HIGH/MEDIUM/LOW] based on whether your claims are verifiable"""

    start = time.monotonic()

    if use_api:
        text, error = call_claude_cli(persona["system_prompt"], user_prompt)
        if error:
            # Fallback to Ollama
            text, error = call_ollama(persona["system_prompt"], user_prompt)
    else:
        text, error = call_ollama(persona["system_prompt"], user_prompt)

    duration = time.monotonic() - start

    return {
        "persona_id": persona["id"],
        "persona_name": persona["name"],
        "icon": persona["icon"],
        "review": text or f"Review failed: {error}",
        "error": error,
        "duration_seconds": round(duration, 1),
    }


def run_red_team(workspace, spec_mode=False):
    """Run the Red Teamer as a 6th pass after all other reviewers, feeding them all reviews."""
    reviews_dir = os.path.join(workspace, ".gsd", "reviews")
    if not os.path.exists(reviews_dir):
        print("  No reviews found for red team pass.")
        return None

    # Read all existing reviews
    all_reviews = []
    for filename in sorted(os.listdir(reviews_dir)):
        if filename.endswith(".md") and filename != "red_teamer.md":
            path = os.path.join(reviews_dir, filename)
            with open(path) as f:
                all_reviews.append(f.read())

    if not all_reviews:
        print("  No reviews to red-team.")
        return None

    combined_reviews = "\n\n---\n\n".join(all_reviews)

    # Load the red teamer persona
    config = load_personas()
    red_teamer = None
    for p in config["personas"]:
        if p["id"] == "red_teamer":
            red_teamer = p
            break

    if not red_teamer:
        print("  Red Teamer persona not found in review-personas.json")
        return None

    # Also read the spec/context for full picture
    context = gather_build_context(workspace, spec_mode=spec_mode)

    user_prompt = f"""You have received reviews from 5 other reviewers. Your job is to attack THEIR reasoning.

## The Product Context
{context[:3000]}

## The Reviews You Are Attacking
{combined_reviews}

Now tear apart these reviews. Find the false consensus, the missed kill shots, and the claims that need verification."""

    print("  Running Red Teamer (sequential, needs depth)...")
    start = time.monotonic()
    text, error = call_claude_cli(
        red_teamer["system_prompt"],
        user_prompt,
        model="claude-opus-4-6",
        max_tokens=2000,
    )
    duration = time.monotonic() - start

    review = text or f"Red team review failed: {error}"

    # Save red team review
    path = os.path.join(reviews_dir, "red_teamer.md")
    with open(path, "w") as f:
        f.write(f"# {red_teamer['icon']} {red_teamer['name']} Review\n\n")
        f.write(f"*Generated: {datetime.now().isoformat()}*\n\n")
        f.write(review)

    status = "OK" if not error else f"FALLBACK: {error[:50]}"
    print(f"  {red_teamer['icon']} {red_teamer['name']}: {status} ({round(duration, 1)}s)")

    return review


def cmd_review(args):
    """Run all persona reviews in parallel."""
    workspace = os.path.abspath(args.workspace)
    config = load_personas()
    # Filter out the red_teamer - it runs separately after all others
    personas = [p for p in config["personas"] if p["id"] != "red_teamer"]
    use_api = not args.local

    print(f"Persona Review Gate — {len(personas)} reviewers")
    print(f"Workspace: {workspace}")
    print(f"Model: {'Claude API' if use_api else 'Ollama (local)'}")
    print(f"{'='*60}")

    # Gather context
    spec_mode = getattr(args, "spec_mode", False)
    context = gather_build_context(workspace, spec_mode=spec_mode)
    if not context:
        print("No build plan files found. Run GSD planning first.")
        return

    print(f"Context: {len(context)} chars from build plan files\n")

    # Run reviews in parallel
    reviews = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            executor.submit(run_persona_review, p, context, use_api): p
            for p in personas
        }
        for future in concurrent.futures.as_completed(futures):
            persona = futures[future]
            result = future.result()
            reviews.append(result)
            status = "OK" if not result["error"] else f"FALLBACK: {result['error'][:50]}"
            print(f"  {result['icon']} {result['persona_name']}: {status} ({result['duration_seconds']}s)")

    # Save individual reviews
    reviews_dir = os.path.join(workspace, ".gsd", "reviews")
    os.makedirs(reviews_dir, exist_ok=True)

    for review in reviews:
        path = os.path.join(reviews_dir, f"{review['persona_id']}.md")
        with open(path, "w") as f:
            f.write(f"# {review['icon']} {review['persona_name']} Review\n\n")
            f.write(f"*Generated: {datetime.now().isoformat()}*\n\n")
            f.write(review["review"])

    # Save summary
    summary_path = os.path.join(workspace, ".gsd", "reviews", "summary.json")
    with open(summary_path, "w") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "workspace": workspace,
            "reviews": reviews,
        }, f, indent=2)

    print(f"\n{'='*60}")
    print(f"Reviews saved to {reviews_dir}/")
    print(f"Run 'synthesize' to combine into REVIEW-FEEDBACK.md")


def cmd_synthesize(args):
    """Synthesize individual reviews into a single REVIEW-FEEDBACK.md."""
    workspace = os.path.abspath(args.workspace)
    reviews_dir = os.path.join(workspace, ".gsd", "reviews")

    if not os.path.exists(reviews_dir):
        print("No reviews found. Run 'review' first.")
        return

    # Read all review files
    reviews = []
    for filename in sorted(os.listdir(reviews_dir)):
        if filename.endswith(".md"):
            path = os.path.join(reviews_dir, filename)
            with open(path) as f:
                reviews.append(f.read())

    combined = "\n\n---\n\n".join(reviews)

    # Extract scores from reviews for variance reporting
    score_pattern = re.compile(r'SCORE:\s*(\d+)', re.IGNORECASE)
    scores_by_persona = {}
    for review_text in reviews:
        lines = review_text.strip().split("\n")
        persona_name = lines[0].strip("# ").strip() if lines else "Unknown"
        match = score_pattern.search(review_text)
        if match:
            scores_by_persona[persona_name] = int(match.group(1))

    score_values = list(scores_by_persona.values())
    if score_values:
        score_mean = sum(score_values) / len(score_values)
        score_min = min(score_values)
        score_max = max(score_values)
        score_spread = score_max - score_min
        score_summary = f"Score Variance: mean={score_mean:.1f}, min={score_min}, max={score_max}, spread={score_spread}"
        if len(score_values) >= 2:
            sorted_scores = sorted(scores_by_persona.items(), key=lambda x: x[1])
            lowest = sorted_scores[0]
            highest = sorted_scores[-1]
            dissent_note = f"Strongest Dissent: {lowest[0]} scored {lowest[1]} while {highest[0]} scored {highest[1]}"
        else:
            dissent_note = "Insufficient scores for dissent analysis"
    else:
        score_summary = "No numeric scores extracted from reviews"
        dissent_note = "No scores available"

    # Synthesize with LLM
    synthesis_prompt = f"""You are synthesizing feedback from product reviewers (including a Red Teamer who attacked the other reviews). Produce a single, actionable document the build team should read BEFORE starting implementation.

## Score Data
{score_summary}
{dissent_note}

Structure your output as:

## YOUR ONE NEXT ACTION
[The single most important thing the founder should do before reading the rest of this document]

## Overall Score: [1-10]
Where 1=kill this immediately, 5=needs major investigation, 10=build now.
Based on reviewer scores: {score_summary}

## Critical Issues (MUST address before building)
[Issues that would cause the product to fail if not addressed]

## Strong Recommendations (SHOULD address)
[Issues that significantly improve the product]

## Considerations (COULD address)
[Nice-to-haves and future considerations]

## Scope Adjustments
[Specific slices or features to add, remove, or modify based on the reviews]

## Consensus Points
[Where multiple reviewers agreed — these are the strongest signals]

## Strongest Dissent
{dissent_note}
[Quote the dissenting reviewer's key argument. Explain why this dissent matters.]

## Verify Before Acting
[Numbered list of specific factual claims from the reviews that need human verification. For each: the claim, who made it, and how to verify it.]

Reviews:

{combined}

Synthesize into a clear, actionable feedback document."""

    print("Synthesizing reviews...")

    text, error = call_claude_cli(
        "You synthesize multi-perspective product reviews into actionable build feedback.",
        synthesis_prompt,
        model="claude-sonnet-4-6",
        max_tokens=3000,
    )

    if error:
        text, error = call_ollama(
            "You synthesize multi-perspective product reviews into actionable build feedback.",
            synthesis_prompt,
        )

    if not text:
        print(f"Synthesis failed: {error}")
        return

    # Extract verdict from synthesis
    verdict = "proceed"
    text_upper = text.upper()
    if "BLOCK" in text_upper and ("CRITICAL" in text_upper or "MUST" in text_upper):
        verdict = "block"
    elif "REVISE" in text_upper or "MUST ADDRESS" in text_upper:
        verdict = "revise"

    # Update metadata with verdict
    meta_path = os.path.join(workspace, ".build-meta.json")
    if os.path.exists(meta_path):
        with open(meta_path) as mf:
            meta = json.load(mf)
        meta["spec_review_verdict"] = verdict
        meta["review_completed_at"] = datetime.now().isoformat()
        meta["status"] = "spec_reviewed"
        with open(meta_path, "w") as mf:
            json.dump(meta, mf, indent=2)
    print(f"Spec review verdict: {verdict.upper()}")

    # Write REVIEW-FEEDBACK.md
    feedback_path = os.path.join(workspace, "REVIEW-FEEDBACK.md")
    with open(feedback_path, "w") as f:
        f.write(f"# Multi-Perspective Build Review\n\n")
        f.write(f"> Generated by Persona Review Gate on {datetime.now().isoformat()}\n")
        f.write(f"> 6 reviewers: User Advocate, Revenue Skeptic, Security Hardliner, Simplicity Advocate, Competitive Realist, Red Teamer\n\n")
        f.write(f"---\n\n")
        f.write(text)
        f.write(f"\n\n---\n\n## Individual Reviews\n\n")
        f.write(combined)

    # Also copy into .gsd so GSD picks it up
    gsd_feedback = os.path.join(workspace, ".gsd", "REVIEW-FEEDBACK.md")
    with open(gsd_feedback, "w") as f:
        f.write(text)

    print(f"REVIEW-FEEDBACK.md written to {feedback_path}")
    print(f"Also injected into .gsd/ for GSD consumption")


def cmd_full(args):
    """Run review + red team + synthesize in one step."""
    cmd_review(args)
    print()
    # Red Team pass (sequential — needs all other reviews as input)
    workspace = os.path.abspath(args.workspace)
    spec_mode = getattr(args, "spec_mode", False)
    run_red_team(workspace, spec_mode=spec_mode)
    print()
    cmd_synthesize(args)


def main():
    parser = argparse.ArgumentParser(description="Persona Review Gate")
    subparsers = parser.add_subparsers(dest="command")

    p_review = subparsers.add_parser("review", help="Run all persona reviews")
    p_review.add_argument("workspace")
    p_review.add_argument("--local", action="store_true", help="Use Ollama instead of Claude API")
    p_review.add_argument("--spec-mode", action="store_true", help="Review SPEC.md (pre-build) instead of GSD files")

    p_synth = subparsers.add_parser("synthesize", help="Synthesize reviews into feedback")
    p_synth.add_argument("workspace")

    p_full = subparsers.add_parser("full", help="Review + synthesize")
    p_full.add_argument("workspace")
    p_full.add_argument("--local", action="store_true")
    p_full.add_argument("--spec-mode", action="store_true", help="Review SPEC.md (pre-build)")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    {"review": cmd_review, "synthesize": cmd_synthesize, "full": cmd_full}[args.command](args)


if __name__ == "__main__":
    main()
