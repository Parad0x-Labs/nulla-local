# HANDOVER: NullaBook Deployment State -- Session 2

Date: 2026-03-16 (evening)
Status: **definitive handover -- supersedes ALL previous handovers for deployment, infra, and NullaBook state**

---

## 1. What Happened This Session

1. **Discovered SSH keys and deployed** -- keys are at `~/Desktop/ssh/nulla-ssh/nulla_do_ed25519_v2`, not where previous handover implied
2. **Full codebase sync to all 4 nodes** -- rsync with `--delete` (excluding .venv, .git, config/meet_clusters, tls/)
3. **Fixed NullaBook feed cross-origin bug** -- dashboard JS was fetching `/v1/nullabook/feed` from raw upstream meet IP (self-signed cert, blocked by browser). Changed to same-origin fetch via watch proxy.
4. **Added NullaBook proxy routes to watch server** -- `/v1/nullabook/*` GET requests and `/nullabook` standalone page now served by the watch node
5. **Restarted local NULLA agent** to latest code

---

## 2. Live Infrastructure

### Node Map

| Role | IP | SSH User | Config File | NULLA_HOME | PID (current) |
|------|----|----------|-------------|------------|----------------|
| **EU Meet** | 203.0.113.11 | root | seed-eu-1.json | /var/lib/nulla/meet-eu-1 | 337823 |
| **US Meet** | 203.0.113.12 | root | seed-us-1.json | /var/lib/nulla/meet-us-1 | 305369 |
| **APAC Meet** | 203.0.113.13 | root | seed-apac-1.json | /var/lib/nulla/meet-apac-1 | 325216 |
| **Watch** | 203.0.113.14 | root | watch-edge-1.json | /var/lib/nulla/watch-edge-1 | 227099 |

### SSH Key

```
Path: ~/Desktop/ssh/nulla-ssh/nulla_do_ed25519_v2
User: root
```

The key is also discovered by `core/public_hive_bridge.py` via `find_public_hive_ssh_key()` which checks multiple locations in order:
1. `NULLA_PUBLIC_HIVE_SSH_KEY_PATH` env var
2. `{project_root}/ssh/nulla-ssh/nulla_do_ed25519_v2`
3. `{project_root}/../ssh/nulla-ssh/nulla_do_ed25519_v2`
4. `~/.ssh/nulla_do_ed25519_v2`
5. `~/.ssh/nulla_do_ed25519`
6. `~/Desktop/ssh/nulla-ssh/nulla_do_ed25519_v2`

### DNS

`nullabook.com` -> `203.0.113.14` (Cloudflare DNS-only, gray cloud, NOT proxied)

**WARNING**: DO NOT enable Cloudflare proxy (orange cloud). It breaks the site.

### Architecture

```
Browser (HTTPS/H2)
    |
    v
Caddy (port 443, LE cert, NO gzip, flush_interval -1)
    |
    v
Watch Server (port 8788, self-signed TLS)
    |-- /                    -> Brain Hive dashboard HTML (NullaBook mode on nullabook.com domain)
    |-- /nullabook           -> Standalone NullaBook page HTML
    |-- /api/dashboard       -> Proxied from best upstream meet node
    |-- /v1/nullabook/*      -> Proxied to upstream meet nodes (feed, profiles, posts)
    |-- /health              -> Watch health check
    |
    v (proxies to)
3x Meet Nodes (port 8766, mTLS)
    |-- EU:   203.0.113.11
    |-- US:   203.0.113.12
    |-- APAC: 203.0.113.13
```

### Caddy Config

File: `/etc/caddy/Caddyfile` on watch node (203.0.113.14)

```
nullabook.com, www.nullabook.com {
    reverse_proxy https://127.0.0.1:8788 {
        transport http {
            tls_insecure_skip_verify
        }
        flush_interval -1
    }
}
```

**CRITICAL**: No `encode gzip`. Adding it breaks HTTP/2 stream termination with the Python HTTP/1.0 backend.

---

## 3. How to Deploy Changes

### Quick: Single File Update

```bash
SSH_KEY="$HOME/Desktop/ssh/nulla-ssh/nulla_do_ed25519_v2"
SRC="$HOME/Desktop/Decentralized_NULLA"

# Sync a file to all nodes
for IP in 203.0.113.11 203.0.113.12 203.0.113.13 203.0.113.14; do
  rsync -az -e "ssh -i $SSH_KEY -o StrictHostKeyChecking=no" \
    "$SRC/core/brain_hive_dashboard.py" \
    "root@${IP}:/opt/Decentralized_NULLA/core/brain_hive_dashboard.py"
done
```

### Full Codebase Sync

```bash
SSH_KEY="$HOME/Desktop/ssh/nulla-ssh/nulla_do_ed25519_v2"
SRC="$HOME/Desktop/Decentralized_NULLA/"

for IP in 203.0.113.11 203.0.113.12 203.0.113.13 203.0.113.14; do
  rsync -az --delete \
    --exclude '.venv' \
    --exclude '.nulla_local' \
    --exclude '.git' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude 'config/meet_clusters' \
    --exclude 'tls/' \
    --exclude '.DS_Store' \
    -e "ssh -i $SSH_KEY -o StrictHostKeyChecking=no" \
    "$SRC" "root@${IP}:/opt/Decentralized_NULLA/"
done
```

**IMPORTANT**: Exclude `config/meet_clusters` and `tls/` -- these contain per-node configs and TLS certs that differ per server.

### Restart Meet Nodes

Each meet node MUST be started with `NULLA_HOME` pointing to its dedicated directory. Without this, `runtime_guard.py` will refuse to start.

```bash
SSH_KEY="$HOME/Desktop/ssh/nulla-ssh/nulla_do_ed25519_v2"

# EU
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no root@203.0.113.11 \
  "pkill -f 'seed-eu-1' || true; sleep 2; cd /opt/Decentralized_NULLA; \
   nohup env NULLA_HOME=/var/lib/nulla/meet-eu-1 .venv/bin/python \
   ops/run_meet_node_from_config.py --config config/meet_clusters/do_ip_first_4node/seed-eu-1.json \
   > /var/log/nulla/meet-eu-1.log 2>&1 </dev/null &"

# US
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no root@203.0.113.12 \
  "pkill -f 'seed-us-1' || true; sleep 2; cd /opt/Decentralized_NULLA; \
   nohup env NULLA_HOME=/var/lib/nulla/meet-us-1 .venv/bin/python \
   ops/run_meet_node_from_config.py --config config/meet_clusters/do_ip_first_4node/seed-us-1.json \
   > /var/log/nulla/meet-us-1.log 2>&1 </dev/null &"

# APAC
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no root@203.0.113.13 \
  "pkill -f 'seed-apac-1' || true; sleep 2; cd /opt/Decentralized_NULLA; \
   nohup env NULLA_HOME=/var/lib/nulla/meet-apac-1 .venv/bin/python \
   ops/run_meet_node_from_config.py --config config/meet_clusters/do_ip_first_4node/seed-apac-1.json \
   > /var/log/nulla/meet-apac-1.log 2>&1 </dev/null &"
```

### Restart Watch Node

```bash
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no root@203.0.113.14 \
  "pkill -f 'run_brain_hive_watch' || true; sleep 2; cd /opt/Decentralized_NULLA; \
   nohup env NULLA_HOME=/var/lib/nulla/watch-edge-1 .venv/bin/python \
   ops/run_brain_hive_watch_from_config.py \
   --config config/meet_clusters/do_ip_first_4node/watch-edge-1.json \
   > /var/log/nulla/watch-edge-1.log 2>&1 </dev/null &"
```

### Verify After Deploy

```bash
# Quick health check
curl -sk https://nullabook.com/ -o /dev/null -w "HOMEPAGE: %{http_code} %{size_download}b\n"
curl -sk https://nullabook.com/nullabook -o /dev/null -w "NULLABOOK: %{http_code} %{size_download}b\n"
curl -sk https://nullabook.com/v1/nullabook/feed -w "\nFEED: %{http_code}\n"
curl -sk https://nullabook.com/api/dashboard -o /dev/null -w "DASHBOARD: %{http_code} %{size_download}b\n"

# Expected:
# HOMEPAGE:  200 ~220000b
# NULLABOOK: 200 ~9600b
# FEED:      200 (JSON with posts array)
# DASHBOARD: 200 ~170000b
```

### Gotcha: pkill Kills SSH

When you run `pkill -f run_meet_node` over SSH, it can match the SSH command itself and kill your session. Use the config filename pattern instead: `pkill -f 'seed-eu-1'`.

### Gotcha: Address Already In Use

If a node refuses to start with `OSError: [Errno 98] Address already in use`, find and kill the orphan:
```bash
ssh -i "$SSH_KEY" root@<IP> "ss -tlnp | grep 8766; kill -9 \$(ss -tlnp | grep 8766 | grep -oP 'pid=\K[0-9]+')"
```

### Gotcha: runtime_guard Blocks Startup

All meet nodes MUST have `NULLA_HOME` set to a dedicated directory (e.g., `/var/lib/nulla/meet-eu-1`). If `NULLA_HOME` resolves to the default `.nulla_local` inside the project, `runtime_guard.py` will refuse to start with "Non-loopback meet node should use a dedicated NULLA_HOME".

---

## 4. What's Working on nullabook.com Right Now

| Feature | Status | Proof |
|---------|--------|-------|
| Homepage loads | YES | 200, 220KB |
| NullaBook mode activates (domain detection) | YES | JS detects hostname, hides workstation chrome |
| NullaBook standalone page (/nullabook) | YES | 200, 9.6KB |
| Dashboard API (/api/dashboard) | YES | Returns real data: 803 posts, 9 topics, 3 agents |
| NullaBook feed API (/v1/nullabook/feed) | YES | Returns {ok: true, posts: []} (empty -- no social posts yet) |
| NullaBook handle check (/v1/nullabook/check-handle/X) | YES | Returns availability status |
| Feed fetch from browser (same-origin) | FIXED this session | Was broken by cross-origin meet URL |
| Hive task posts in Commons tab | YES | Shows research topics, claims, agents |
| Social posts in feed | NO | Feed is empty -- nobody has posted via NullaBook system yet |
| Agent NullaBook registration (local) | YES | Works via OpenClaw chat ("create NullaBook profile") |
| Agent NullaBook posting (local) | YES | Works via OpenClaw chat ("post to NullaBook: ...") |

---

## 5. What's NOT Working / Missing -- Honest Assessment

### Why It Doesn't Look Like Reddit/Moltbook Yet

The NullaBook social feed has **zero posts**. The current feed only shows hive task activity (research topics, claims, agent posts from the Brain Hive system). These look like technical dashboard entries, not social network posts.

To make it look like a social network, these things need to happen:

1. **Agents need to actually post** -- The local agent can post via OpenClaw, but those posts only go to the local SQLite DB. The meet node DBs on the servers are empty.
2. **The standalone NullaBook page (/nullabook)** exists but is a minimal shell -- it needs to be the primary surface for nullabook.com, not the dashboard.
3. **The main dashboard** is designed as a hive workstation, not a social feed. When accessed from nullabook.com it hides the workstation chrome, but the content is still task-oriented (topics, claims, vital signs).
4. **No user-facing post creation UI** -- posting only works through the agent chat interface (OpenClaw). There's no web form to write a post.
5. **No public agent profiles** viewable from the web -- the API exists (`/v1/nullabook/profile/<handle>`) but there's no HTML page for it.
6. **No reply/upvote UI** -- the API supports replies and upvotes, but there's no browser UI for these actions.

### What Would Make It Feel Like Reddit/Moltbook

In priority order:

1. **Make `/nullabook` the homepage** when accessed from nullabook.com (redirect or serve it at `/`)
2. **Populate the feed** -- have agents auto-post their research findings as social posts
3. **Add a post creation form** on the web UI (even if it requires an API key)
4. **Add profile pages** at `/nullabook/profile/<handle>` with post history
5. **Add reply/upvote buttons** on posts
6. **Add trending topics/tags** sidebar
7. **Design the feed cards** to look more like social posts (avatar, handle, relative time, engagement counts)

### Technical Gaps

| Gap | Details |
|-----|---------|
| NullaBook posts only stored locally | Agent posts go to local SQLite, not the meet node DBs. Need agent-to-meet post sync. |
| No POST proxy on watch node | Watch node only proxies GET requests for NullaBook. POST requests (create post, register) aren't proxied. |
| Bootstrap JSON files are modified | `knowledge_presence.json`, `local_first.json`, `safe_orchestration.json` show as modified in git but were committed. These affect model provider defaults. |
| Python version mismatch | pyproject.toml targets Python 3.10 but local dev uses 3.9. `zip(strict=False)` and `str | None` cause issues. |

---

## 6. Git State

```
Branch: main
HEAD:   a699414 Add NullaBook proxy to watch server, fix feed cross-origin bug
Remote: Parad0x-Labs/nulla-hive-mind (pushed, up to date)
Version: 0.4.0
Clean working tree: YES (after this commit)
```

### Recent Commits (relevant)

```
a699414 Add NullaBook proxy to watch server, fix feed cross-origin bug
e673e83 NullaBook real backend: post storage, API routes, agent flow, feed UI
8b4b3b1 Tighten execution markers: remove overly broad phrases that hijack instructions
373851d Fix CODEOWNERS to use GitHub username for PR review routing
af692cf Fix three critical agent behavior bugs
8252902 Fix sample provider config to use Ollama default port
45cdfa5 Bump version to 0.4.0 -- NullaBook identity and 5-mode dashboard
```

---

## 7. Local Agent State

- **PID**: 14640
- **Port**: 127.0.0.1:11435
- **Backend**: Ollama qwen2.5:14b on Apple Silicon (MPS)
- **Version**: 0.4.0-closed-test
- **NullaBook**: Agent knows about NullaBook via fast path in `apps/nulla_agent.py`
- **Registered handles**: SLS_0x (registered in local DB during earlier testing)

### How to Restart Local Agent

```bash
kill $(pgrep -f nulla_api_server) 2>/dev/null
cd ~/Desktop/Decentralized_NULLA
nohup python3 -m apps.nulla_api_server > /tmp/nulla_agent.log 2>&1 &
```

---

## 8. Key Files Changed Across Both Sessions Today

| File | What Changed |
|------|-------------|
| `storage/migrations.py` | Added `nullabook_posts` table |
| `storage/nullabook_store.py` | NEW -- CRUD for NullaBook posts |
| `apps/meet_and_greet_server.py` | Added 7 NullaBook API routes |
| `apps/brain_hive_watch_server.py` | Added NullaBook proxy routes + /nullabook page |
| `apps/nulla_agent.py` | Rewrote NullaBook fast path (registration flow, posting) |
| `apps/nulla_api_server.py` | OpenAI-compatible response format for /v1/chat/completions |
| `core/brain_hive_dashboard.py` | NullaBook overhaul + feed fetch fix |
| `core/nullabook_feed_page.py` | NEW -- standalone NullaBook HTML page |
| `tools/web/web_research.py` | CoinGecko crypto price fast path, Py3.9 zip fix |
| `tests/test_nullabook_store.py` | NEW -- 17 tests for NullaBook CRUD |
| `tests/test_nullabook_api.py` | NEW -- 12 tests for NullaBook API routes |

---

## 9. Known Pitfalls (Inherited + New)

1. **Cloudflare proxy = instant death** -- DNS-only (gray cloud). Do NOT enable orange cloud.
2. **Never add `encode gzip` to Caddy** -- breaks HTTP/2 stream with Python HTTP/1.0 backend.
3. **NULLA_HOME is required for meet nodes** -- without it, runtime_guard blocks startup.
4. **pkill over SSH kills the SSH session** -- use config filename pattern (e.g., `pkill -f 'seed-eu-1'`).
5. **Python 3.9 vs 3.10** -- avoid `str | None` syntax and `zip(strict=False)` in any file that runs on local dev.
6. **MutationObserver + subtree = infinite loop** -- never observe an element with subtree:true if callback mutates descendants.
7. **Full rsync --delete** requires excluding `config/meet_clusters` and `tls/` or you'll wipe per-node configs.
8. **Unicode in dashboard HTML** -- use HTML entities (`&#x1F98B;`), not Python surrogate escapes (`\ud83e`).

---

## 10. What the Next Agent Should Do

If the goal is to make nullabook.com look like a real social network (Reddit/Moltbook-killer):

1. **Redesign the nullabook.com landing** -- either make `/nullabook` the default for the domain, or rebuild the NullaBook mode of the dashboard to lead with social content (feed, profiles, trending) rather than vital signs and task lineage.
2. **Bridge hive activity to NullaBook posts** -- when an agent solves a topic or publishes research, auto-create a NullaBook social post so the feed has content.
3. **Add POST proxy to watch node** -- the watch server currently only proxies GET requests for NullaBook. Registration and posting from the web need POST support.
4. **Build profile pages** -- render at `/nullabook/profile/<handle>` with post history, bio, stats.
5. **Add web-based post/reply UI** -- at minimum a form on the NullaBook page.
6. **Seed the feed** -- create some initial posts so visitors see activity, not an empty page.
