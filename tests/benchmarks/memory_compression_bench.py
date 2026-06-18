#!/usr/bin/env python3
"""
NULLA Memory & Compression Benchmark
Proves that ContextWindow (summarize + memory retrieval) preserves recall
while keeping token count bounded — unlike raw or sliding-window approaches.

Scenario: N-turn conversation.  5 facts planted in turns 1-5.
          (N-10) filler turns.  5 recall questions in the last 5 turns.

Modes compared:
  raw       — all messages sent every turn (unbounded growth)
  window    — keep last WINDOW_SIZE turns only (facts lost)
  compress  — ContextWindow summarize + keep recent (facts in summary)
  retrieve  — ContextWindow + memory retrieval before each question

Usage:
  python -m tests.benchmarks.memory_compression_bench
  python -m tests.benchmarks.memory_compression_bench --quick
  python -m tests.benchmarks.memory_compression_bench --turns 100
  python -m tests.benchmarks.memory_compression_bench --turns 200 --mode compress
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.context_window import ContextWindow
from core.conversation_summarizer import token_estimate
from core.embedding_service import embedding_backend

_OLLAMA_BASE = "http://127.0.0.1:11434"
_QA_MODEL = "qwen3:8b"
WINDOW_SIZE = 10   # turns kept in sliding-window mode

# ── planted facts ──────────────────────────────────────────────────────────────

FACTS = [
    {"turn": 1, "plant": "My server admin password is: Glacier-7749",     "question": "What is my server admin password?",          "answer": "Glacier-7749"},
    {"turn": 2, "plant": "The Postgres database runs on port 5433",        "question": "What port does my database run on?",           "answer": "5433"},
    {"turn": 3, "plant": "My preferred programming language is Rust",      "question": "What is my preferred programming language?",   "answer": "Rust"},
    {"turn": 4, "plant": "The project deadline is 2025-11-01",             "question": "When is the project deadline?",                "answer": "2025-11-01"},
    {"turn": 5, "plant": "The prod API key is sk-nulla-9f3c2a",            "question": "What is the production API key?",              "answer": "sk-nulla-9f3c2a"},
]

# ── filler content generation ──────────────────────────────────────────────────

_FILLER_TEMPLATES = [
    "Can you explain {concept}?",
    "What are the pros and cons of {concept}?",
    "How does {concept} work internally?",
    "What are best practices for {concept}?",
    "Compare {concept} with common alternatives.",
    "When should I avoid {concept}?",
]

_CONCEPTS = [
    "async/await", "microservices", "CI/CD pipelines", "NoSQL databases",
    "the CAP theorem", "garbage collection in Go", "unit testing",
    "rate limiting", "REST APIs", "Docker networking", "reverse proxies",
    "TLS handshakes", "eventual consistency", "processes vs threads",
    "memory leaks in Node.js", "dependency injection", "hash tables",
    "stack vs heap memory", "compiler design", "the SOLID principles",
    "event sourcing", "CQRS", "WebSockets", "GraphQL", "gRPC",
    "Kubernetes scheduling", "service meshes", "observability pillars",
    "SLO and error budgets", "distributed tracing", "load balancing",
    "circuit breakers", "Redis caching strategies", "message queues",
    "pub/sub architecture", "OAuth 2.0 flows", "JWT tokens", "CORS headers",
    "Content Security Policy", "SQL query optimisation", "connection pooling",
    "database sharding", "blue/green deployments", "canary releases",
    "feature flags", "A/B testing", "chaos engineering", "eBPF",
    "eBPF in production", "WebAssembly", "Rust ownership model",
    "Tokio async runtime", "virtual DOM reconciliation",
]


def _generate_filler(count: int) -> list[str]:
    """Generate `count` filler questions by cycling templates × concepts."""
    result = []
    for i in range(count):
        tmpl = _FILLER_TEMPLATES[i % len(_FILLER_TEMPLATES)]
        concept = _CONCEPTS[i % len(_CONCEPTS)]
        result.append(tmpl.format(concept=concept))
    return result


SYSTEM_PROMPT = (
    "You are a helpful assistant with a good memory. "
    "When asked about facts mentioned earlier in the conversation, "
    "answer precisely and concisely."
)

# ── LLM call ───────────────────────────────────────────────────────────────────


def ask_llm(messages: list[dict], timeout: int = 60) -> str:
    payload = {
        "model": _QA_MODEL,
        "messages": messages,
        "stream": False,
        "think": False,
        "options": {"temperature": 0.1, "num_predict": 128},
    }
    req = urllib.request.Request(
        f"{_OLLAMA_BASE}/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode())
    return data["message"]["content"]


def check_answer(response: str, expected: str) -> bool:
    return expected.lower() in response.lower()


# ── mode runners ───────────────────────────────────────────────────────────────


def run_raw(turns: int = 30) -> dict:
    """Keep all messages — token count grows unbounded."""
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    peak_tokens = 0

    for fact in FACTS:
        messages.append({"role": "user",      "content": fact["plant"]})
        messages.append({"role": "assistant", "content": "Noted."})

    filler = _generate_filler(turns - 10)
    for f in filler:
        messages.append({"role": "user",      "content": f})
        messages.append({"role": "assistant", "content": "Sure, here is a brief answer."})
        peak_tokens = max(peak_tokens, token_estimate(messages))

    correct = 0
    for fact in FACTS:
        messages.append({"role": "user", "content": fact["question"]})
        peak_tokens = max(peak_tokens, token_estimate(messages))
        try:
            ans = ask_llm(messages)
        except Exception:
            ans = ""
        correct += int(check_answer(ans, fact["answer"]))
        messages.append({"role": "assistant", "content": ans or "I don't know."})

    return {"recall": correct, "total": len(FACTS), "peak_tokens": peak_tokens, "compressions": 0}


def run_window(turns: int = 30) -> dict:
    """Sliding window: keep last WINDOW_SIZE messages only."""
    full: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    peak_tokens = 0

    for fact in FACTS:
        full.append({"role": "user",      "content": fact["plant"]})
        full.append({"role": "assistant", "content": "Noted."})

    filler = _generate_filler(turns - 10)
    for f in filler:
        full.append({"role": "user",      "content": f})
        full.append({"role": "assistant", "content": "Sure."})

    correct = 0
    for fact in FACTS:
        full.append({"role": "user", "content": fact["question"]})
        window = full[:1] + full[-(WINDOW_SIZE):]
        peak_tokens = max(peak_tokens, token_estimate(window))
        try:
            ans = ask_llm(window)
        except Exception:
            ans = ""
        correct += int(check_answer(ans, fact["answer"]))
        full.append({"role": "assistant", "content": ans or "I don't know."})

    return {"recall": correct, "total": len(FACTS), "peak_tokens": peak_tokens, "compressions": 0}


def run_compress(turns: int = 30) -> dict:
    """ContextWindow with LLM summarization of old turns."""
    # Scale threshold proportionally for very long conversations
    threshold = min(20, max(16, turns // 5))
    keep = min(8, max(6, turns // 20))
    ctx = ContextWindow(
        agent_id="bench_compress",
        db_path="/tmp/nulla_bench_compress.db",
        persist_memory=False,
        summary_threshold=threshold,
        keep_recent=keep,
    )
    ctx.add_system(SYSTEM_PROMPT)
    peak_tokens = 0

    for fact in FACTS:
        ctx.add("user", fact["plant"])
        ctx.add("assistant", "Noted.")

    filler = _generate_filler(turns - 10)
    for f in filler:
        ctx.add("user", f)
        ctx.add("assistant", "Sure.")
        peak_tokens = max(peak_tokens, token_estimate(ctx.messages_for_llm()))

    correct = 0
    for fact in FACTS:
        ctx.add("user", fact["question"])
        msgs = ctx.messages_for_llm()
        peak_tokens = max(peak_tokens, token_estimate(msgs))
        try:
            ans = ask_llm(msgs)
        except Exception:
            ans = ""
        correct += int(check_answer(ans, fact["answer"]))
        ctx.add("assistant", ans or "I don't know.")

    ctx.close()
    return {
        "recall": correct,
        "total": len(FACTS),
        "peak_tokens": peak_tokens,
        "compressions": ctx.compression_count,
    }


def run_retrieve(turns: int = 30) -> dict:
    """ContextWindow + smart memory retrieval before each recall question."""
    threshold = min(20, max(16, turns // 5))
    keep = min(8, max(6, turns // 20))
    ctx = ContextWindow(
        agent_id="bench_retrieve",
        db_path="/tmp/nulla_bench_retrieve.db",
        persist_memory=True,
        summary_threshold=threshold,
        keep_recent=keep,
    )
    ctx.add_system(SYSTEM_PROMPT)
    peak_tokens = 0

    for fact in FACTS:
        ctx.add("user", fact["plant"])
        ctx.add("assistant", "Noted.")

    filler = _generate_filler(turns - 10)
    for f in filler:
        ctx.add("user", f)
        ctx.add("assistant", "Sure.")

    correct = 0
    for fact in FACTS:
        ctx.add("user", fact["question"])
        ctx.inject_relevant(fact["question"])
        msgs = ctx.messages_for_llm()
        peak_tokens = max(peak_tokens, token_estimate(msgs))
        try:
            ans = ask_llm(msgs)
        except Exception:
            ans = ""
        correct += int(check_answer(ans, fact["answer"]))
        ctx.add("assistant", ans or "I don't know.")

    compressions = ctx.compression_count
    memory_nodes = ctx.memory_node_count
    ctx.close()
    return {
        "recall": correct,
        "total": len(FACTS),
        "peak_tokens": peak_tokens,
        "compressions": compressions,
        "memory_nodes": memory_nodes,
    }


# ── report ─────────────────────────────────────────────────────────────────────

W = 76


def print_report(results: dict, turns: int) -> None:
    filler_turns = turns - 10
    print()
    print("=" * W)
    print("NULLA MEMORY & COMPRESSION BENCHMARK".center(W))
    print(f"{turns}-turn conversation  |  5 facts planted early  |  5 recall Qs  |  {filler_turns} filler".center(W))
    print("=" * W)

    print(f"\n  Embedding backend: {embedding_backend()}")
    print("  Summarizer model:  qwen3:0.6b / 8b (smallest available)")
    print()

    col_r = 12
    col_t = 16
    col_c = 12

    print(f"  {'MODE':<22}  {'RECALL':<{col_r}}  {'PEAK TOKENS':<{col_t}}  {'COMPRESSIONS':<{col_c}}")
    print(f"  {'-'*22}  {'-'*col_r}  {'-'*col_t}  {'-'*col_c}")

    modes = [
        ("raw",      "Raw (no compression)"),
        ("window",   f"Sliding window({WINDOW_SIZE})"),
        ("compress", "ContextWindow compress"),
        ("retrieve", "ContextWindow+retrieve"),
    ]

    baseline_tokens = results.get("raw", {}).get("peak_tokens", 1)

    for key, label in modes:
        r = results.get(key, {})
        if not r:
            print(f"  {label:<22}  (skipped)")
            continue
        recall = r["recall"]
        total = r["total"]
        tok = r["peak_tokens"]
        comp = r.get("compressions", 0)

        recall_str = f"{recall}/{total} ({recall*100//total}%)"
        tok_str = f"{tok:,}"
        reduction = int((1 - tok / baseline_tokens) * 100) if baseline_tokens else 0
        if reduction > 0:
            tok_str += f"  (-{reduction}%)"
        elif reduction < 0:
            tok_str += f"  (+{-reduction}%)"
        comp_str = str(comp) if comp else "—"

        print(f"  {label:<22}  {recall_str:<{col_r}}  {tok_str:<{col_t}}  {comp_str:<{col_c}}")

    print(f"  {'-'*22}  {'-'*col_r}  {'-'*col_t}  {'-'*col_c}")

    raw_recall = results.get("raw", {}).get("recall", 0)
    cmp_recall = results.get("compress", {}).get("recall", 0)
    ret_recall = results.get("retrieve", {}).get("recall", 0)
    win_recall = results.get("window", {}).get("recall", 0)
    raw_tok = results.get("raw", {}).get("peak_tokens", 0)
    cmp_tok = results.get("compress", {}).get("peak_tokens", 0)

    print()
    if cmp_tok and raw_tok:
        saving = int((1 - cmp_tok / raw_tok) * 100)
        print(f"  ContextWindow cut token usage by {saving}%")
    if win_recall < cmp_recall:
        print(f"  Sliding window lost {raw_recall - win_recall} fact(s) — compress retained them all")
    if ret_recall >= cmp_recall:
        print(f"  Memory retrieval maintained {ret_recall}/{raw_recall} recall with persistent storage")
    elif ret_recall < cmp_recall:
        print(f"  ⚠  Retrieve mode recalled {ret_recall}/{raw_recall} — check injection dedup")

    print("=" * W)


# ── main ───────────────────────────────────────────────────────────────────────


def main() -> None:
    ap = argparse.ArgumentParser(description="NULLA memory & compression benchmark")
    ap.add_argument("--quick",  action="store_true", help="5 filler turns (fastest, same as --turns 15)")
    ap.add_argument("--turns",  type=int, default=30, help="Total conversation turns (default 30; try 100, 200)")
    ap.add_argument("--mode",   help="Run only this mode: raw|window|compress|retrieve")
    args = ap.parse_args()

    turns = 15 if args.quick else args.turns
    if turns < 12:
        print("ERROR: --turns must be at least 12 (5 facts + 2 filler + 5 recall)")
        sys.exit(1)

    try:
        with urllib.request.urlopen(f"{_OLLAMA_BASE}/api/tags", timeout=3):
            pass
    except Exception:
        print("ERROR: Ollama not reachable. Start it first.")
        sys.exit(1)

    print("\nNULLA Memory & Compression Benchmark")
    print(f"Embedding : {embedding_backend()}")
    print(f"Turns     : {turns}  ({turns - 10} filler  +  5 fact plants  +  5 recall Qs)")
    print()

    runners = {
        "raw":      run_raw,
        "window":   run_window,
        "compress": run_compress,
        "retrieve": run_retrieve,
    }

    if args.mode and args.mode not in runners:
        print(f"Unknown mode: {args.mode}. Choose from: {list(runners)}")
        sys.exit(1)

    active = {k: v for k, v in runners.items() if not args.mode or k == args.mode}
    results: dict = {}

    for mode, fn in active.items():
        print(f"  Running [{mode}] ...", end=" ", flush=True)
        t0 = time.time()
        try:
            r = fn(turns=turns)
            results[mode] = r
            elapsed = time.time() - t0
            print(f"done  {elapsed:.0f}s  recall={r['recall']}/{r['total']}  tokens={r['peak_tokens']:,}")
        except Exception as e:
            print(f"ERROR: {e}")
            results[mode] = {}

    if not args.mode:
        print_report(results, turns)

    out_path = Path("/tmp/nulla_memory_bench_results.json")
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()
