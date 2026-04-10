#!/usr/bin/env python3
"""
Q-Learning Router — Ported from Ruflo v3 (@claude-flow/cli/src/ruvector/q-learning-router.ts)

Reinforcement learning router that learns which LLM model works best
for which signal type, based on triage outcome quality.

Optimizations (preserved from Ruflo):
- LRU cache for repeated signal patterns
- Feature hashing for O(1) state representation
- Epsilon-greedy with exponential decay
- Experience replay for stable learning
- Model persistence to SQLite

Usage:
  from q_router import QLearningRouter

  router = QLearningRouter()
  decision = router.route("freelance invoicing pain point from r/SaaS")
  # decision = {route: 'qwen2.5:14b', confidence: 0.82, explored: False}

  # After human reviews the triage result:
  router.learn("freelance invoicing...", "qwen2.5:14b", reward=0.9)
"""

import json
import math
import os
import random
import hashlib
import time
import sqlite3
from typing import Dict, List, Optional, Tuple, Any
from collections import OrderedDict

PLUGIN_ROOT = os.environ.get("CLAUDE_PLUGIN_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(PLUGIN_ROOT, "data", "soy.db")

# Our LLM routes (adapted from Ruflo's agent roles)
ROUTE_NAMES = [
    "mistral:7b",       # fast noise filter
    "qwen2.5:7b",       # fast scoring
    "qwen2.5:14b",      # deep scoring
    "llama3.1:8b",      # general tasks
    "claude-haiku",      # API - fast, cheap
    "claude-sonnet",     # API - balanced
    "skip",              # signal should be skipped entirely
]

# Feature keywords adapted for our signal domain
FEATURE_KEYWORDS = {
    # Dev tools signals → probably need deeper analysis
    "developer": 0, "code": 0, "api": 0, "tool": 0, "build": 0, "software": 0, "app": 0,
    # Business/SaaS signals → moderate analysis
    "business": 1, "saas": 1, "startup": 1, "client": 1, "revenue": 1, "pricing": 1,
    # Consumer/retail signals → simpler analysis usually
    "buy": 2, "shop": 2, "product": 2, "brand": 2, "clothing": 2, "food": 2,
    # Frustration signals → need careful triage
    "frustrated": 3, "terrible": 3, "worst": 3, "hate": 3, "broken": 3, "useless": 3,
    # Wish/need signals → high value, deeper analysis
    "wish": 4, "need": 4, "looking for": 4, "alternative": 4, "better": 4,
    # Personal/noise → likely should skip
    "relationship": 5, "dating": 5, "family": 5, "personal": 5, "story": 5,
    # Technical signals
    "automate": 6, "workflow": 6, "integration": 6, "pipeline": 6, "data": 6,
}

DEFAULT_CONFIG = {
    "learning_rate": 0.1,
    "gamma": 0.99,
    "exploration_initial": 1.0,
    "exploration_final": 0.01,
    "exploration_decay": 500,  # lower than Ruflo's 10k since we have fewer signals
    "exploration_decay_type": "exponential",
    "max_states": 5000,
    "num_actions": len(ROUTE_NAMES),
    "replay_buffer_size": 500,
    "replay_batch_size": 16,
    "enable_replay": True,
    "cache_size": 128,
    "cache_ttl": 300,  # seconds
    "state_space_dim": 32,
    "auto_save_interval": 25,
}


class QLearningRouter:
    """Adaptive LLM router using Q-learning with experience replay."""

    def __init__(self, config=None):
        self.config = {**DEFAULT_CONFIG, **(config or {})}
        self.q_table = {}  # state_key -> [q_values]
        self.visits = {}   # state_key -> visit count
        self.epsilon = self.config["exploration_initial"]
        self.step_count = 0
        self.update_count = 0
        self.avg_td_error = 0.0

        # Experience replay (circular buffer)
        self.replay_buffer = []
        self.replay_idx = 0
        self.total_experiences = 0

        # LRU cache
        self.cache = OrderedDict()
        self.cache_hits = 0
        self.cache_misses = 0

        # Load persisted model
        self._load_model()

    def route(self, signal_text: str, explore: bool = True) -> Dict[str, Any]:
        """Route a signal to the best LLM based on learned patterns.

        Returns:
            {route, confidence, q_values, explored, alternatives}
        """
        state_key = self._hash_state(signal_text)

        # Check cache (exploitation only)
        if not explore:
            cached = self._get_cached(state_key)
            if cached:
                self.cache_hits += 1
                return cached
            self.cache_misses += 1

        # Epsilon-greedy action selection
        should_explore = explore and random.random() < self.epsilon
        q_values = self._get_q_values(state_key)

        if should_explore:
            action_idx = random.randint(0, self.config["num_actions"] - 1)
        else:
            action_idx = self._argmax(q_values)

        confidence = self._softmax_confidence(q_values, action_idx)

        # Top alternatives
        scored = sorted(enumerate(q_values), key=lambda x: -x[1])
        alternatives = [
            {"route": ROUTE_NAMES[i], "score": round(v, 4)}
            for i, v in scored[1:4]
        ]

        decision = {
            "route": ROUTE_NAMES[action_idx],
            "confidence": round(confidence, 4),
            "q_values": [round(v, 4) for v in q_values],
            "explored": should_explore,
            "alternatives": alternatives,
        }

        if not should_explore:
            self._cache_route(state_key, decision)

        return decision

    def learn(self, signal_text: str, action: str, reward: float, next_signal: str = None):
        """Update Q-values from triage outcome.

        reward: 0.0 (human completely overrode) to 1.0 (human agreed perfectly)
        """
        state_key = self._hash_state(signal_text)
        action_idx = ROUTE_NAMES.index(action) if action in ROUTE_NAMES else -1
        if action_idx == -1:
            return 0.0

        next_key = self._hash_state(next_signal) if next_signal else None

        # Store in replay buffer
        if self.config["enable_replay"]:
            experience = {
                "state_key": state_key,
                "action_idx": action_idx,
                "reward": reward,
                "next_key": next_key,
                "priority": abs(reward) + 0.1,
            }
            if len(self.replay_buffer) < self.config["replay_buffer_size"]:
                self.replay_buffer.append(experience)
            else:
                self.replay_buffer[self.replay_idx % self.config["replay_buffer_size"]] = experience
            self.replay_idx += 1
            self.total_experiences += 1

        # Direct Q-value update
        td_error = self._update_q(state_key, action_idx, reward, next_key)

        # Experience replay
        if (self.config["enable_replay"] and
                len(self.replay_buffer) >= self.config["replay_batch_size"]):
            self._experience_replay()

        # Decay epsilon
        self.step_count += 1
        self.epsilon = self._calculate_epsilon()

        # Prune Q-table
        if len(self.q_table) > self.config["max_states"]:
            self._prune_q_table()

        self.update_count += 1
        self.avg_td_error = (self.avg_td_error * (self.update_count - 1) + abs(td_error)) / self.update_count

        # Auto-save
        if (self.config["auto_save_interval"] > 0 and
                self.update_count % self.config["auto_save_interval"] == 0):
            self._save_model()

        # Invalidate cache periodically
        if self.update_count % 25 == 0:
            self.cache.clear()

        return td_error

    def get_stats(self) -> Dict[str, Any]:
        return {
            "states": len(self.q_table),
            "step_count": self.step_count,
            "update_count": self.update_count,
            "epsilon": round(self.epsilon, 4),
            "avg_td_error": round(self.avg_td_error, 4),
            "experiences": self.total_experiences,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "cache_hit_rate": round(self.cache_hits / max(1, self.cache_hits + self.cache_misses), 4),
        }

    # ===== Private Methods =====

    def _hash_state(self, text: str) -> str:
        """Feature hash text into a compact state key."""
        text_lower = text.lower()
        features = [0.0] * self.config["state_space_dim"]

        for keyword, group in FEATURE_KEYWORDS.items():
            if keyword in text_lower:
                # Hash keyword to feature index
                idx = int(hashlib.md5(keyword.encode()).hexdigest(), 16) % self.config["state_space_dim"]
                features[idx] += 1.0

        # Also hash the overall text for uniqueness
        text_hash = int(hashlib.md5(text_lower[:200].encode()).hexdigest(), 16)
        features[text_hash % self.config["state_space_dim"]] += 0.5

        # Normalize
        norm = sum(f * f for f in features) ** 0.5
        if norm > 0:
            features = [f / norm for f in features]

        return hashlib.md5(json.dumps(features).encode()).hexdigest()[:16]

    def _get_q_values(self, state_key: str) -> List[float]:
        if state_key not in self.q_table:
            self.q_table[state_key] = [0.0] * self.config["num_actions"]
            self.visits[state_key] = 0
        self.visits[state_key] = self.visits.get(state_key, 0) + 1
        return self.q_table[state_key]

    def _update_q(self, state_key, action_idx, reward, next_key):
        q_values = self._get_q_values(state_key)
        current_q = q_values[action_idx]

        if next_key:
            next_q = self._get_q_values(next_key)
            target = reward + self.config["gamma"] * max(next_q)
        else:
            target = reward

        td_error = target - current_q
        q_values[action_idx] += self.config["learning_rate"] * td_error
        return td_error

    def _experience_replay(self):
        batch = random.sample(self.replay_buffer, min(self.config["replay_batch_size"], len(self.replay_buffer)))
        for exp in batch:
            self._update_q(exp["state_key"], exp["action_idx"], exp["reward"], exp["next_key"])

    def _calculate_epsilon(self):
        decay_type = self.config["exploration_decay_type"]
        decay_steps = self.config["exploration_decay"]
        initial = self.config["exploration_initial"]
        final = self.config["exploration_final"]

        if decay_type == "linear":
            return max(final, initial - (initial - final) * self.step_count / decay_steps)
        elif decay_type == "cosine":
            progress = min(1.0, self.step_count / decay_steps)
            return final + (initial - final) * 0.5 * (1 + math.cos(math.pi * progress))
        else:  # exponential
            decay_rate = -math.log(max(final / initial, 1e-10)) / decay_steps
            return max(final, initial * math.exp(-decay_rate * self.step_count))

    def _argmax(self, values):
        max_val = max(values)
        candidates = [i for i, v in enumerate(values) if v == max_val]
        return random.choice(candidates)

    def _softmax_confidence(self, q_values, action_idx):
        max_q = max(q_values)
        exp_values = [math.exp(q - max_q) for q in q_values]
        total = sum(exp_values)
        return exp_values[action_idx] / total if total > 0 else 1.0 / len(q_values)

    def _prune_q_table(self):
        """Remove least-visited states."""
        sorted_states = sorted(self.visits.items(), key=lambda x: x[1])
        to_remove = len(self.q_table) - self.config["max_states"] + 100
        for state_key, _ in sorted_states[:to_remove]:
            self.q_table.pop(state_key, None)
            self.visits.pop(state_key, None)

    def _get_cached(self, state_key):
        if state_key in self.cache:
            entry = self.cache[state_key]
            if time.time() - entry["time"] < self.config["cache_ttl"]:
                self.cache.move_to_end(state_key)
                return entry["decision"]
            else:
                del self.cache[state_key]
        return None

    def _cache_route(self, state_key, decision):
        while len(self.cache) >= self.config["cache_size"]:
            self.cache.popitem(last=False)
        self.cache[state_key] = {"decision": decision, "time": time.time()}

    def _save_model(self):
        """Persist model to SQLite soy_meta."""
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute("PRAGMA busy_timeout=5000")
            model = json.dumps({
                "version": "1.0.0",
                "q_table": {k: v for k, v in self.q_table.items()},
                "visits": self.visits,
                "stats": {
                    "step_count": self.step_count,
                    "update_count": self.update_count,
                    "avg_td_error": self.avg_td_error,
                    "epsilon": self.epsilon,
                },
                "total_experiences": self.total_experiences,
            })
            conn.execute("""
                INSERT OR REPLACE INTO soy_meta (key, value, updated_at)
                VALUES ('q_router_model', ?, datetime('now'))
            """, (model,))
            conn.commit()
            conn.close()
        except Exception:
            pass

    def _load_model(self):
        """Load persisted model from SQLite."""
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute("PRAGMA busy_timeout=5000")
            row = conn.execute("SELECT value FROM soy_meta WHERE key = 'q_router_model'").fetchone()
            conn.close()
            if not row:
                return

            model = json.loads(row[0])
            if not model.get("version", "").startswith("1."):
                return

            self.q_table = model.get("q_table", {})
            self.visits = model.get("visits", {})
            stats = model.get("stats", {})
            self.step_count = stats.get("step_count", 0)
            self.update_count = stats.get("update_count", 0)
            self.avg_td_error = stats.get("avg_td_error", 0.0)
            self.epsilon = stats.get("epsilon", self.config["exploration_initial"])
            self.total_experiences = model.get("total_experiences", 0)
        except Exception:
            pass
