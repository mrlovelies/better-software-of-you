#!/usr/bin/env python3
"""
Signal Deduplication — Semantic duplicate detection using HNSW index.

Uses Ollama embeddings + HNSW vector index (extracted from Ruflo) to find
semantically similar signals that regex-based URL dedup misses.

"I wish there was a tool to track freelance invoices" and
"Freelancers need better billing software" are the same pain point.

Usage:
  from signal_dedup import SignalDeduplicator

  dedup = SignalDeduplicator()
  is_dup, similar = dedup.check("I wish there was an app for invoicing")
  # is_dup=True, similar=[("signal_42", 0.91)]

  dedup.add("signal_55", "New unique signal text")
"""

import os
import json
import numpy as np
from urllib.request import Request, urlopen
from typing import List, Tuple, Optional

from hnsw_index import HNSWIndex

PLUGIN_ROOT = os.environ.get("CLAUDE_PLUGIN_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://100.91.234.67:11434")
OLLAMA_HOST_14B = os.environ.get("OLLAMA_HOST_14B", "http://100.74.238.16:11434")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "mistral:7b")  # fast, good enough for similarity
INDEX_PATH = os.path.join(PLUGIN_ROOT, "data", "signal_hnsw.json")

# Embedding dimension varies by model — we'll detect on first call
_embedding_dim = None


def get_embedding(text: str, model: str = None) -> Optional[np.ndarray]:
    """Get embedding vector from Ollama."""
    global _embedding_dim
    model = model or EMBEDDING_MODEL

    url = f"{OLLAMA_HOST}/api/embeddings"
    payload = json.dumps({
        "model": model,
        "prompt": text[:2000],  # cap text length for embedding
    }).encode()

    req = Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
            embedding = data.get("embedding")
            if embedding:
                vec = np.array(embedding, dtype=np.float32)
                _embedding_dim = len(vec)
                return vec
    except Exception as e:
        print(f"  [warn] Embedding failed: {e}")
    return None


def get_embedding_dim() -> int:
    """Get the embedding dimension for the current model."""
    global _embedding_dim
    if _embedding_dim is None:
        # Probe with a test string
        vec = get_embedding("test")
        if vec is not None:
            _embedding_dim = len(vec)
        else:
            _embedding_dim = 4096  # fallback for mistral:7b
    return _embedding_dim


class SignalDeduplicator:
    """Semantic deduplication using HNSW vector index + Ollama embeddings."""

    def __init__(self, threshold: float = 0.85, index_path: str = None):
        self.threshold = threshold
        self.index_path = index_path or INDEX_PATH
        self.index = None
        self._load_index()

    def _load_index(self):
        """Load existing index or create new."""
        try:
            if os.path.exists(self.index_path):
                self.index = HNSWIndex.load(self.index_path)
                return
        except Exception:
            pass

        dim = get_embedding_dim()
        self.index = HNSWIndex(dimensions=dim, M=16, ef_construction=100)

    def _save_index(self):
        """Persist index to disk."""
        if self.index:
            try:
                self.index.save(self.index_path)
            except Exception as e:
                print(f"  [warn] Failed to save index: {e}")

    def check(self, text: str) -> Tuple[bool, List[Tuple[str, float]]]:
        """Check if a signal is a semantic duplicate of existing signals.

        Returns:
            (is_duplicate, [(similar_id, similarity_score), ...])
        """
        if self.index is None or self.index.size == 0:
            return False, []

        embedding = get_embedding(text)
        if embedding is None:
            return False, []  # can't check without embedding

        # Ensure dimensions match
        if embedding.shape[0] != self.index.dimensions:
            return False, []

        duplicates = self.index.find_duplicates(embedding, threshold=self.threshold)
        is_dup = len(duplicates) > 0

        return is_dup, duplicates

    def add(self, signal_id: str, text: str) -> bool:
        """Add a signal to the index for future dedup checks.

        Returns True if successfully added.
        """
        embedding = get_embedding(text)
        if embedding is None:
            return False

        # Ensure index matches embedding dimension
        if self.index is None or (self.index.size > 0 and embedding.shape[0] != self.index.dimensions):
            self.index = HNSWIndex(dimensions=embedding.shape[0], M=16, ef_construction=100)

        if self.index.dimensions != embedding.shape[0]:
            self.index = HNSWIndex(dimensions=embedding.shape[0], M=16, ef_construction=100)

        try:
            self.index.add(signal_id, embedding)
            # Auto-save every 10 additions
            if self.index.size % 10 == 0:
                self._save_index()
            return True
        except Exception as e:
            print(f"  [warn] Failed to add to index: {e}")
            return False

    def save(self):
        """Explicitly save the index."""
        self._save_index()

    def get_similar(self, text: str, k: int = 5) -> List[Tuple[str, float]]:
        """Find k most similar signals to the given text."""
        if self.index is None or self.index.size == 0:
            return []

        embedding = get_embedding(text)
        if embedding is None:
            return []

        results = self.index.search(embedding, k=k)
        # Convert distance to similarity
        return [(id, round(1.0 - dist, 4)) for id, dist in results]

    @property
    def size(self):
        return self.index.size if self.index else 0

    def get_stats(self):
        if self.index:
            return self.index.get_stats()
        return {"vector_count": 0}
