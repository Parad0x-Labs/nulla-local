"""
Embedding service for NullaMemory semantic retrieval.

Priority:
  1. Ollama /api/embed with nomic-embed-text  (best, 768-dim)
  2. Ollama /api/embed with any embed-capable model
  3. Hash-bag-of-words fallback              (pure Python, 384-dim)

The fallback produces good exact-match and keyword-overlap similarity
without requiring any model download.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import math
import re
import urllib.error
import urllib.request
from collections.abc import Sequence
from functools import lru_cache

_OLLAMA_BASE = "http://127.0.0.1:11434"
_EMBED_MODELS = ["nomic-embed-text", "mxbai-embed-large", "all-minilm"]
_FALLBACK_DIMS = 384


# ── Ollama embed ───────────────────────────────────────────────────────────────


def _ollama_embed(text: str, model: str, timeout: int = 15) -> list[float] | None:
    payload = {"model": model, "input": text}
    req = urllib.request.Request(
        f"{_OLLAMA_BASE}/api/embed",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode())
        embs = data.get("embeddings") or []
        if embs and embs[0]:
            return [float(x) for x in embs[0]]
    except Exception:
        pass
    return None


@lru_cache(maxsize=1)
def _best_embed_model() -> str | None:
    """Return the first embed-capable model Ollama has, or None."""
    try:
        with urllib.request.urlopen(f"{_OLLAMA_BASE}/api/tags", timeout=3) as r:
            data = json.loads(r.read().decode())
        available = {m["name"].split(":")[0] for m in data.get("models", [])}
        for m in _EMBED_MODELS:
            if m in available:
                return m
        # Last resort: try any model tagged as embed
        for m in data.get("models", []):
            if "embed" in m["name"].lower():
                return m["name"]
    except Exception:
        pass
    return None


# ── Hash bag-of-words fallback ─────────────────────────────────────────────────


def _hash_bow_embed(text: str, dims: int = _FALLBACK_DIMS) -> list[float]:
    """
    Lightweight deterministic embedding via character n-gram hashing.
    Good for keyword overlap; no semantic generalisation.
    """
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    if not tokens:
        return [0.0] * dims

    vec = [0.0] * dims
    # unigrams
    for tok in tokens:
        idx = int(hashlib.md5(tok.encode()).hexdigest(), 16) % dims
        vec[idx] += 1.0
    # bigrams (improve phrase matching)
    for a, b in itertools.pairwise(tokens):
        idx = int(hashlib.md5(f"{a}_{b}".encode()).hexdigest(), 16) % dims
        vec[idx] += 0.5

    mag = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / mag for x in vec]


# ── Public API ─────────────────────────────────────────────────────────────────


def embed(text: str) -> list[float]:
    """
    Generate an embedding vector for *text*.

    Tries Ollama first (semantic quality), falls back to hash-BoW (exact match).
    """
    text = str(text or "").strip()
    if not text:
        return [0.0] * _FALLBACK_DIMS

    model = _best_embed_model()
    if model:
        vec = _ollama_embed(text, model)
        if vec:
            return vec

    return _hash_bow_embed(text)


def embed_batch(texts: Sequence[str]) -> list[list[float]]:
    """Embed a list of texts. Uses the same backend for all."""
    return [embed(t) for t in texts]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(y * y for y in b))
    return dot / (mag_a * mag_b) if mag_a and mag_b else 0.0


def embedding_backend() -> str:
    """Return a label describing which backend is active."""
    model = _best_embed_model()
    return f"ollama:{model}" if model else f"hash-bow:{_FALLBACK_DIMS}d"


__all__ = [
    "cosine_similarity",
    "embed",
    "embed_batch",
    "embedding_backend",
]
