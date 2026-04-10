#!/usr/bin/env python3
"""
HNSW Vector Index — Ported from Ruflo v3 (@claude-flow/memory/src/hnsw-index.ts)

High-performance Hierarchical Navigable Small World index for
approximate nearest-neighbor search. Used for signal deduplication
and semantic similarity clustering.

Optimizations (preserved from Ruflo):
- Binary heap for O(log n) priority queue operations
- Pre-normalized vectors for O(1) cosine similarity
- Bounded max-heap for efficient top-k tracking

Usage:
  from hnsw_index import HNSWIndex

  index = HNSWIndex(dimensions=384)  # match embedding model
  index.add("signal_1", embedding_vector)
  results = index.search(query_vector, k=5)  # returns [(id, distance), ...]
  similar = index.find_duplicates(embedding, threshold=0.85)
"""

import math
import random
import json
import os
import numpy as np
from typing import List, Tuple, Optional, Dict, Any


class BinaryMaxHeap:
    """Bounded max-heap for efficient top-k tracking.
    Keeps k smallest elements by evicting largest when full."""

    def __init__(self, max_size=float('inf')):
        self.heap = []  # list of (priority, item)
        self.max_size = max_size

    @property
    def size(self):
        return len(self.heap)

    def insert(self, item, priority):
        if len(self.heap) >= self.max_size and priority >= self.heap[0][0]:
            return False
        if len(self.heap) >= self.max_size:
            self.heap[0] = (priority, item)
            self._bubble_down(0)
        else:
            self.heap.append((priority, item))
            self._bubble_up(len(self.heap) - 1)
        return True

    def peek_max_priority(self):
        return self.heap[0][0] if self.heap else float('inf')

    def extract_max(self):
        if not self.heap:
            return None
        top = self.heap[0][1]
        last = self.heap.pop()
        if self.heap:
            self.heap[0] = last
            self._bubble_down(0)
        return top

    def to_sorted(self):
        return sorted(self.heap, key=lambda x: x[0])

    def _bubble_up(self, i):
        while i > 0:
            parent = (i - 1) // 2
            if self.heap[parent][0] >= self.heap[i][0]:
                break
            self.heap[parent], self.heap[i] = self.heap[i], self.heap[parent]
            i = parent

    def _bubble_down(self, i):
        n = len(self.heap)
        while True:
            largest = i
            left, right = 2 * i + 1, 2 * i + 2
            if left < n and self.heap[left][0] > self.heap[largest][0]:
                largest = left
            if right < n and self.heap[right][0] > self.heap[largest][0]:
                largest = right
            if largest == i:
                break
            self.heap[largest], self.heap[i] = self.heap[i], self.heap[largest]
            i = largest


class HNSWIndex:
    """HNSW index for fast approximate nearest-neighbor search.

    Performance: O(log n) search, O(log n) insert.
    """

    def __init__(self, dimensions=384, M=16, ef_construction=200, max_elements=100000, metric='cosine'):
        self.dimensions = dimensions
        self.M = M
        self.ef_construction = ef_construction
        self.max_elements = max_elements
        self.metric = metric
        self.level_mult = 1.0 / math.log(M) if M > 1 else 1.0

        self.nodes = {}  # id -> {vector, normalized, connections, level}
        self.entry_point = None
        self.max_level = 0

        # Stats
        self.search_count = 0
        self.insert_count = 0

    def add(self, id: str, vector: np.ndarray):
        """Add a vector to the index."""
        if isinstance(vector, list):
            vector = np.array(vector, dtype=np.float32)

        if vector.shape[0] != self.dimensions:
            raise ValueError(f"Dimension mismatch: expected {self.dimensions}, got {vector.shape[0]}")

        if len(self.nodes) >= self.max_elements:
            raise RuntimeError("Index is full")

        # Pre-normalize for O(1) cosine similarity
        norm = np.linalg.norm(vector)
        normalized = vector / norm if norm > 0 and self.metric == 'cosine' else None

        level = self._random_level()

        node = {
            'vector': vector,
            'normalized': normalized,
            'connections': {l: set() for l in range(level + 1)},
            'level': level,
        }

        if self.entry_point is None:
            self.entry_point = id
            self.max_level = level
            self.nodes[id] = node
        else:
            self.nodes[id] = node
            self._insert_node(id, node)

        self.insert_count += 1

    def search(self, query: np.ndarray, k: int = 5, ef: int = None) -> List[Tuple[str, float]]:
        """Search for k nearest neighbors. Returns list of (id, distance)."""
        if isinstance(query, list):
            query = np.array(query, dtype=np.float32)

        if self.entry_point is None:
            return []

        search_ef = ef or max(k, self.ef_construction)

        # Pre-normalize query
        norm = np.linalg.norm(query)
        normalized_query = query / norm if norm > 0 and self.metric == 'cosine' else None

        # Traverse from top layer down to layer 1
        current = self.entry_point
        current_dist = self._distance(query, normalized_query, self.nodes[current])

        for level in range(self.max_level, 0, -1):
            changed = True
            while changed:
                changed = False
                node = self.nodes[current]
                connections = node['connections'].get(level, set())
                for neighbor_id in connections:
                    if neighbor_id not in self.nodes:
                        continue
                    d = self._distance(query, normalized_query, self.nodes[neighbor_id])
                    if d < current_dist:
                        current = neighbor_id
                        current_dist = d
                        changed = True

        # Search layer 0 with ef candidates
        candidates = self._search_layer(query, normalized_query, current, search_ef, 0)
        self.search_count += 1

        return candidates[:k]

    def find_duplicates(self, vector: np.ndarray, threshold: float = 0.85) -> List[Tuple[str, float]]:
        """Find signals similar enough to be considered duplicates.
        Returns list of (id, similarity) where similarity > threshold."""
        if self.entry_point is None:
            return []

        results = self.search(vector, k=10)

        # Convert distance to similarity (for cosine: similarity = 1 - distance)
        duplicates = []
        for id, distance in results:
            similarity = 1.0 - distance if self.metric == 'cosine' else 1.0 / (1.0 + distance)
            if similarity >= threshold:
                duplicates.append((id, similarity))

        return duplicates

    def has(self, id: str) -> bool:
        return id in self.nodes

    @property
    def size(self):
        return len(self.nodes)

    def remove(self, id: str) -> bool:
        """Remove a point from the index."""
        if id not in self.nodes:
            return False

        node = self.nodes[id]
        for level in range(node['level'] + 1):
            for neighbor_id in node['connections'].get(level, set()):
                if neighbor_id in self.nodes:
                    self.nodes[neighbor_id]['connections'].get(level, set()).discard(id)

        del self.nodes[id]

        if self.entry_point == id:
            if not self.nodes:
                self.entry_point = None
                self.max_level = 0
            else:
                best_id, best_level = None, 0
                for nid, n in self.nodes.items():
                    if n['level'] > best_level:
                        best_level = n['level']
                        best_id = nid
                self.entry_point = best_id
                self.max_level = best_level

        return True

    def save(self, path: str):
        """Save index to disk."""
        data = {
            'dimensions': self.dimensions,
            'M': self.M,
            'metric': self.metric,
            'entry_point': self.entry_point,
            'max_level': self.max_level,
            'nodes': {}
        }
        for id, node in self.nodes.items():
            data['nodes'][id] = {
                'vector': node['vector'].tolist(),
                'level': node['level'],
                'connections': {str(l): list(conns) for l, conns in node['connections'].items()},
            }
        with open(path, 'w') as f:
            json.dump(data, f)

    @classmethod
    def load(cls, path: str) -> 'HNSWIndex':
        """Load index from disk."""
        with open(path) as f:
            data = json.load(f)

        index = cls(dimensions=data['dimensions'], M=data['M'], metric=data.get('metric', 'cosine'))
        index.entry_point = data['entry_point']
        index.max_level = data['max_level']

        for id, node_data in data['nodes'].items():
            vector = np.array(node_data['vector'], dtype=np.float32)
            norm = np.linalg.norm(vector)
            index.nodes[id] = {
                'vector': vector,
                'normalized': vector / norm if norm > 0 and index.metric == 'cosine' else None,
                'level': node_data['level'],
                'connections': {int(l): set(conns) for l, conns in node_data['connections'].items()},
            }

        return index

    def get_stats(self) -> Dict[str, Any]:
        return {
            'vector_count': len(self.nodes),
            'max_level': self.max_level,
            'search_count': self.search_count,
            'insert_count': self.insert_count,
            'dimensions': self.dimensions,
        }

    # ===== Private Methods =====

    def _random_level(self):
        level = 0
        while random.random() < 0.5 and level < 16:
            level += 1
        return level

    def _distance(self, query, normalized_query, node):
        """Compute distance between query and node."""
        if self.metric == 'cosine' and normalized_query is not None and node['normalized'] is not None:
            # O(1) cosine distance from pre-normalized vectors
            return 1.0 - float(np.dot(normalized_query, node['normalized']))
        elif self.metric == 'euclidean':
            diff = query - node['vector']
            return float(np.dot(diff, diff))
        else:
            # Fallback cosine
            dot = float(np.dot(query, node['vector']))
            norm_q = float(np.linalg.norm(query))
            norm_n = float(np.linalg.norm(node['vector']))
            if norm_q == 0 or norm_n == 0:
                return 1.0
            return 1.0 - dot / (norm_q * norm_n)

    def _search_layer(self, query, normalized_query, entry_id, ef, level):
        """Search a single layer using heap-based greedy search."""
        visited = {entry_id}
        entry_dist = self._distance(query, normalized_query, self.nodes[entry_id])

        # Candidates (min-heap by distance)
        candidates = [(entry_dist, entry_id)]
        # Results (max-heap, bounded to ef)
        results = BinaryMaxHeap(ef)
        results.insert(entry_id, entry_dist)

        while candidates:
            candidates.sort(key=lambda x: x[0])
            current_dist, current_id = candidates.pop(0)

            # If the closest candidate is farther than the farthest result, stop
            if current_dist > results.peek_max_priority():
                break

            node = self.nodes.get(current_id)
            if not node:
                continue

            for neighbor_id in node['connections'].get(level, set()):
                if neighbor_id in visited or neighbor_id not in self.nodes:
                    continue
                visited.add(neighbor_id)

                d = self._distance(query, normalized_query, self.nodes[neighbor_id])

                if d < results.peek_max_priority() or results.size < ef:
                    candidates.append((d, neighbor_id))
                    results.insert(neighbor_id, d)

        return [(item, priority) for priority, item in results.to_sorted()]

    def _insert_node(self, id, node):
        """Insert a new node into the graph."""
        query = node['vector']
        normalized_query = node['normalized']

        current = self.entry_point
        current_dist = self._distance(query, normalized_query, self.nodes[current])

        # Find entry point for node's level
        for level in range(self.max_level, node['level'], -1):
            changed = True
            while changed:
                changed = False
                for neighbor_id in self.nodes[current]['connections'].get(level, set()):
                    if neighbor_id not in self.nodes:
                        continue
                    d = self._distance(query, normalized_query, self.nodes[neighbor_id])
                    if d < current_dist:
                        current = neighbor_id
                        current_dist = d
                        changed = True

        # Insert at each level
        for level in range(min(node['level'], self.max_level), -1, -1):
            neighbors = self._search_layer(query, normalized_query, current, self.ef_construction, level)

            # Select M best neighbors
            selected = neighbors[:self.M]

            for neighbor_id, _ in selected:
                node['connections'].setdefault(level, set()).add(neighbor_id)
                self.nodes[neighbor_id]['connections'].setdefault(level, set()).add(id)

                # Prune if over limit
                neighbor_conns = self.nodes[neighbor_id]['connections'].get(level, set())
                if len(neighbor_conns) > self.M * 2:
                    self._prune(neighbor_id, level)

            if selected:
                current = selected[0][0]

        # Update entry point if new node has higher level
        if node['level'] > self.max_level:
            self.entry_point = id
            self.max_level = node['level']

    def _prune(self, node_id, level):
        """Prune connections to keep within M limit."""
        node = self.nodes[node_id]
        connections = node['connections'].get(level, set())
        if len(connections) <= self.M:
            return

        # Keep M closest connections
        scored = []
        for conn_id in connections:
            if conn_id in self.nodes:
                d = self._distance(node['vector'], node['normalized'], self.nodes[conn_id])
                scored.append((d, conn_id))

        scored.sort()
        keep = {conn_id for _, conn_id in scored[:self.M]}
        node['connections'][level] = keep
