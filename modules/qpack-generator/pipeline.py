"""
Modular pipeline engine for QPack generation.

Each step is a callable that receives a context dict and returns it (mutated).
Steps can be added, removed, reordered, or replaced at runtime.

Usage:
    p = Pipeline([ScanStep(), TemplateStep(), FilterStep(), ValidateStep(), DeployStep()])
    p.insert_before("validate", MyCustomStep())
    p.insert_after("filter", AnotherStep())
    result = p.run()
"""

from datetime import datetime
from pathlib import Path


class PipelineStep:
    """Base class for pipeline steps."""
    name: str = "unnamed"

    def __call__(self, ctx: dict) -> dict:
        raise NotImplementedError

    def __repr__(self):
        return f"<{self.__class__.__name__} '{self.name}'>"


class Pipeline:
    """Ordered sequence of steps that transform a shared context."""

    def __init__(self, steps: list[PipelineStep] = None):
        self.steps = steps or []
        self.log_lines: list[str] = []

    def add(self, step: PipelineStep):
        self.steps.append(step)
        return self

    def insert_before(self, target_name: str, step: PipelineStep):
        for i, s in enumerate(self.steps):
            if s.name == target_name:
                self.steps.insert(i, step)
                return self
        raise KeyError(f"Step '{target_name}' not found")

    def insert_after(self, target_name: str, step: PipelineStep):
        for i, s in enumerate(self.steps):
            if s.name == target_name:
                self.steps.insert(i + 1, step)
                return self
        raise KeyError(f"Step '{target_name}' not found")

    def remove(self, name: str):
        self.steps = [s for s in self.steps if s.name != name]
        return self

    def replace(self, name: str, step: PipelineStep):
        for i, s in enumerate(self.steps):
            if s.name == name:
                self.steps[i] = step
                return self
        raise KeyError(f"Step '{name}' not found")

    def log(self, msg: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {msg}"
        self.log_lines.append(line)
        print(line)

    def run(self, initial_ctx: dict = None) -> dict:
        ctx = initial_ctx or {}
        ctx["_pipeline"] = self
        ctx["_started_at"] = datetime.now().isoformat()
        ctx["_step_results"] = {}

        self.log(f"Pipeline starting — {len(self.steps)} steps: {[s.name for s in self.steps]}")

        for i, step in enumerate(self.steps):
            step_start = datetime.now()
            self.log(f"  [{i+1}/{len(self.steps)}] {step.name}...")
            try:
                ctx = step(ctx)
                elapsed = (datetime.now() - step_start).total_seconds()
                ctx["_step_results"][step.name] = {"status": "ok", "elapsed_s": round(elapsed, 2)}
                self.log(f"    done ({elapsed:.1f}s)")
            except Exception as e:
                elapsed = (datetime.now() - step_start).total_seconds()
                ctx["_step_results"][step.name] = {"status": "error", "error": str(e), "elapsed_s": round(elapsed, 2)}
                self.log(f"    FAILED: {e}")
                if ctx.get("_halt_on_error", True):
                    raise

        ctx["_completed_at"] = datetime.now().isoformat()
        self.log(f"Pipeline complete")
        return ctx
