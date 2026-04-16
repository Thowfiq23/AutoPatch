---
title: CodeReview-Env V6
emoji: 🔬
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
license: mit
tags:
  - openenv
  - swe-bench
  - code-review
  - reinforcement-learning
  - execution-sandbox
  - incident-repair
---

<div align="center">

# 🔬 CodeReview-Env

### *The execution-grounded software incident repair benchmark for RL agents*

[![OpenEnv](https://img.shields.io/badge/OpenEnv-compliant-4A90D9?style=for-the-badge)](https://openenv.dev)
[![HF Space](https://img.shields.io/badge/🤗_HuggingFace-Space-FFD21E?style=for-the-badge)](https://huggingface.co/spaces/Thowfiq23/CodeReview-Env)
[![Validate](https://img.shields.io/badge/openenv_validate-10%2F10_PASS-2ECC71?style=for-the-badge)](#spec-compliance)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED?style=for-the-badge)](#setup--usage)
[![License](https://img.shields.io/badge/License-MIT-lightgrey?style=for-the-badge)](LICENSE)

**10 tasks · 9 action types · 6-component reward · Real subprocess execution · Zero simulation**

</div>

---

## What Is CodeReview-Env?

An RL agent is dropped into a **real Linux sandbox** with a failing CI/CD pipeline or a downed production service. It must diagnose the incident, patch the source code, restart services, and prove the fix works — all graded by real `pytest`, against a real filesystem, with no simulation layer.

```
 observe failing tests
        │
        ▼
 inspect logs & read source ──► search codebase
        │
        ▼
 identify root cause
        │
        ▼
 patch buggy files ──► restart service (if needed)
        │
        ▼
 verify: run tests again
        │
        ▼
 submit_fix with root-cause analysis
        │
        ▼
 6-component grading: tests · diagnosis · efficiency · exploration · traps · credit
```

> **Not a toy.** Every `pytest` run, every file patch, every service restart executes as a real subprocess on a real filesystem. The agent either fixes the bug — and explains why — or it does not.

---

## Why This Benchmark?

### The Problem Is Real

Every engineering team has this loop: CI fails → engineer triages → finds root cause → writes fix → verifies → merges. This is repetitive, well-specified, and expensive at scale. CodeReview-Env makes it learnable.

### Existing Benchmarks Fall Short for RL Training

| Benchmark | Interactive Episodes? | Dense Rewards? | Self-Contained? | Causal State? |
|-----------|:---:|:---:|:---:|:---:|
| SWE-bench | ✗ | ✗ pass/fail only | ✗ needs repo clone | ✗ |
| HumanEval | ✗ | ✗ | ✓ | ✗ |
| LiveCodeBench | ✗ | ✗ | ✓ | ✗ |
| **CodeReview-Env** | **✓** | **✓ per-patch + 6-component terminal** | **✓** | **✓** |

### What Makes This Unique

- **Dense intermediate signal** — every `patch_file` call emits a reward proportional to test improvement, giving RL training loops gradient information at each step
- **Causal state tracking** — service health (`oom_killed → crashed → degraded → running → healthy`) evolves based on agent actions; trap actions are penalized
- **Diagnosis scoring** — `root_cause` text is keyword-matched against ground truth; agents that understand *why* a bug exists score higher than agents that blindly patch
- **Parametric seeding** — SHA-256-derived RNG varies bug parameters each reset, preventing memorization
- **Anti-exploit hardened** — 13 hardening tests block conftest injection, path traversal, chmod bypasses, and reward farming

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                        RL Agent / LLM                            │
│              inference.py  ·  OpenAI-compatible API              │
│         ReAct loop: observe → reason → act → observe → …        │
└─────────────────────────────┬────────────────────────────────────┘
                              │  HTTP + JSON  (OpenEnv REST API)
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                  FastAPI Server  :7860                           │
│                                                                  │
│   POST /reset      POST /step      GET /state                    │
│   GET  /health     GET  /metadata  GET /schema                   │
│   POST /mcp  ──►  JSON-RPC 2.0  (tools/list + tools/call)       │
│                                                                  │
│              asyncio.Lock  ·  serialized episode state           │
└─────────────────────────────┬────────────────────────────────────┘
                              │  subprocess.run()  ·  open()
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│         Isolated Sandbox   /tmp/codereview_workspaces/{uuid}/    │
│                                                                  │
│   app/cache.py          ←  buggy source files  (writable)       │
│   config/settings.py    ←  buggy config        (writable)       │
│   services/client.py    ←  buggy service       (writable)       │
│   tests/test_*.py       ←  pytest oracle       (READ-ONLY)      │
│   service_state.json    ←  live health artifact                  │
│   service.log           ←  structured incident logs             │
└──────────────────────────────────────────────────────────────────┘
```

**Core design principles:**
- **Zero simulation** — `pytest` is the grader; subprocess stdout/stderr is the observation
- **Parametric factories** — each reset derives unique bug parameters from a SHA-256 seed; no two episodes are identical
- **Clean sandboxed env** — subprocesses receive only `PATH`, `HOME`, `LANG`, `LC_ALL`, `TMPDIR`, `USER`, `LOGNAME` — no parent secrets leak in
- **Causal health graph** — for incident tasks, `system_health` evolves through discrete states; premature `restart_service` before fixing code is detected and penalized
- **MCP-native** — `/mcp` exposes all 9 tools via JSON-RPC 2.0 `tools/list` + `tools/call`

---

## Task Suite

### At a Glance

| # | Incident ID | Domain | Difficulty | Bugs | Core Challenge |
|---|------------|--------|:----------:|:----:|----------------|
| 1 | `INC-001` | User Registration | 🟡 Medium | 3 | Email regex · password length · MD5→SHA-256 |
| 2 | `INC-002` | Order Pricing Engine | 🟠 Medium-Hard | 3 | Quantity multiply · discount direction · tax formula |
| 3 | `INC-003` | Async Payment Processor | 🔴 Hard | 3 | Hardcoded secret · sync/async · retry off-by-one |
| 4 | `INC-004` | LRU Cache + Serializer | 🔴 Hard | 3 | Missing move-to-end · missing eviction · None crash |
| 5 | `INC-005` | Analytics Pipeline | 🟣 Very Hard | 3 | EMA alpha swap · population vs sample std · window boundary |
| 6 | `INC-006` | Search Ranking Pipeline | ⚫ Expert | 3 | Coupled bugs — partial fix regresses a passing test |
| 7 | `INC-007` | Config Service | ⚫ Expert | 3 | String→int types · `restart_service` gate |
| 8 | `INC-008` | DB Migration | ⚫ Expert | 3 | Lexicographic sort · silent exceptions · `executescript` |
| 9 | `INC-009` | Memory Leak / OOM | ⚫ Expert | 3 | Unbounded cache · missing eviction · module accumulator |
| 10 | `INC-010` | 3-Service Topology Cascade | ⚫ Expert | 3 | Gateway → auth → db topology · worsening restart mechanic · per-service `restart_service` gate |

> **Difficulty note:** Tasks 1–4 reward basic code comprehension. Tasks 5–6 require algorithmic reasoning and coupling awareness. Tasks 7–9 require multi-step causal reasoning: diagnose → patch → restart → verify. Task 10 adds service topology: three services with a dependency chain, a worsening mechanic (restarting gateway before db floods auth), and per-service targeted restarts.

---

### Detailed Task Descriptions

<details>
<summary><strong>INC-001 — User Registration</strong> &nbsp;🟡 Medium</summary>

**Incident:** Authentication service is rejecting valid user registrations and storing passwords insecurely.

**Bugs (3):**
1. Email regex missing `+` quantifier — accepts empty local parts
2. Password length check uses `>` instead of `>=` — off-by-one rejects valid passwords
3. Passwords hashed with MD5 instead of SHA-256 — security vulnerability

**Seeded variable:** minimum password length (varies per episode)
**Optimal steps:** 5 · **Tests:** 5

</details>

<details>
<summary><strong>INC-002 — Order Pricing Engine</strong> &nbsp;🟠 Medium-Hard</summary>

**Incident:** Orders are being billed at wildly incorrect amounts — some customers overcharged, others undercharged.

**Bugs (3):**
1. `total_price` adds quantity instead of multiplying — wrong arithmetic
2. Discount applied in wrong direction (adds to price instead of subtracting)
3. Tax multiplier misapplied as an additive offset instead of a multiplier

**Seeded variable:** tax rate (varies per episode)
**Optimal steps:** 5 · **Tests:** 5

</details>

<details>
<summary><strong>INC-003 — Async Payment Processor</strong> &nbsp;🔴 Hard</summary>

**Incident:** Payment service is leaking API credentials and failing to retry on transient errors.

**Bugs (3):**
1. API key hardcoded in source instead of read from `os.getenv()`
2. Retry logic calls synchronous `time.sleep()` inside an `async` function — blocks event loop
3. Retry loop runs `range(max_retries)` instead of `range(max_retries + 1)` — one retry short

**Seeded variable:** max retries count (varies per episode)
**Optimal steps:** 5 · **Tests:** 5

</details>

<details>
<summary><strong>INC-004 — LRU Cache + Serializer</strong> &nbsp;🔴 Hard</summary>

**Incident:** Cache is growing without bound and crashing on certain inputs.

**Bugs (3):**
1. `get()` never calls `move_to_end()` — breaks LRU ordering
2. `put()` never evicts oldest entry when capacity exceeded — memory leak
3. JSON serializer raises `TypeError` on `None` values instead of serializing to `null`

**Seeded variable:** cache capacity (varies per episode)
**Optimal steps:** 5 · **Tests:** 5

</details>

<details>
<summary><strong>INC-005 — Analytics Pipeline</strong> &nbsp;🟣 Very Hard</summary>

**Incident:** Dashboard metrics are statistically wrong — EMA is inverted, standard deviations are inflated.

**Bugs (3):**
1. EMA formula has `alpha` and `(1-alpha)` swapped — smooth value trends reversed
2. Standard deviation uses population formula (`ddof=0`) instead of sample (`ddof=1`)
3. Sliding window boundary off by one — includes one extra element

**Seeded variables:** EMA alpha value, window size (vary per episode)
**Optimal steps:** 6 · **Tests:** 5

</details>

<details>
<summary><strong>INC-006 — Search Ranking Pipeline</strong> &nbsp;⚫ Expert</summary>

**Incident:** Search results are unranked and irrelevant. Partial fixes make things worse.

**Bugs (3) — tightly coupled:**
1. Tokenizer splits on whitespace instead of punctuation — corrupts all downstream scoring
2. Relevance scorer counts `len(query_terms)` instead of query∩document intersection
3. Ranker sorts ascending instead of descending — top result is the worst match

**Trap:** Fixing only the scorer without fixing the tokenizer causes a previously-passing test to **regress**. Agents must reason about coupling across components.

**Optimal steps:** 6 · **Tests:** 5

</details>

<details>
<summary><strong>INC-007 — Config Service</strong> &nbsp;⚫ Expert</summary>

**Incident:** Service refuses to start. Config values are being passed to C extensions as wrong types.

**Bugs (3):**
1. `TIMEOUT` defined as string literal `"30"` instead of integer `30`
2. `MAX_WORKERS` defined as string literal `"4"` instead of integer `4`
3. `RETRY_DELAY` is negative — causes immediate retry storm

**Gate:** Agent must call `restart_service` after patching config — submitting without restart earns partial credit only.

**Seeded variable:** timeout value (varies per episode)
**Optimal steps:** 6 · **Tests:** 5

</details>

<details>
<summary><strong>INC-008 — DB Migration</strong> &nbsp;⚫ Expert</summary>

**Incident:** Database migrations are applying in wrong order and silently corrupting the schema.

**Bugs (3):**
1. Migration versions sorted lexicographically (`"10" < "9"`) instead of numerically
2. Exceptions inside migration steps are silently caught — failures appear as successes
3. Multi-statement SQL uses `execute()` instead of `executescript()` — only first statement runs

**Diagnosis tool:** `inspect_logs` reveals the exact migration failure sequence before patching.

**Seeded variable:** number of migrations (varies per episode)
**Optimal steps:** 7 · **Tests:** 5

</details>

<details>
<summary><strong>INC-009 — Memory Leak / OOM</strong> &nbsp;⚫ Expert</summary>

**Incident:** Service is OOM-killed under load. Memory usage grows without bound.

**Bugs (3):**
1. Cache backed by a plain `list` — no O(1) eviction structure
2. `add()` never evicts oldest entry when `len >= CACHE_MAXSIZE` — unbounded growth
3. `_processed_log` is a module-level list that accumulates every processed item forever

**State:** Service starts in `oom_killed`. `app_init.py` checks each component **independently** and writes a distinct status to `service_state.json`:
- Fix only cache bugs (1+2), not processor → `degraded` + `component: "processor"` in logs
- Fix only processor bug (3), not cache → `degraded` + `component: "cache"` in logs
- Fix all three → `running`

After a partial fix, the agent must call `inspect_logs` to read which component is still broken — the observation changes in a way that constrains the recovery path, not just applies a penalty.

**Seeded variable:** `CACHE_MAXSIZE` (varies per episode)
**Optimal steps:** 7 · **Tests:** 5

</details>

<details>
<summary><strong>INC-010 — 3-Service Topology Cascade</strong> &nbsp;⚫ Expert</summary>

**Incident:** 3-service topology (gateway → auth → db) is fully down after a config push. Logs show cascading failures propagating upstream from db through auth to gateway.

**Architecture:**
```
gateway/circuit_breaker.py → auth/client.py → db/config.py
```
Each service has its own config, client, and `service.log`. The agent must fix in **dependency order** (db first, then auth, then gateway) — restarting gateway while db is still crashed floods auth with retries, worsening `auth.error_rate` even after its code is fixed.

**Bugs (3) — one per service:**
1. `db/config.py`: `CONNECT_TIMEOUT = 0.001` — too low, causes immediate connection failure. Valid range: `[0.1, 60.0]`.
2. `auth/client.py`: Retry loop runs `range(MAX_RETRIES + 1)` instead of `range(MAX_RETRIES)` — one extra attempt
3. `gateway/circuit_breaker.py`: Opens after `>= 1` failure instead of `>= failure_threshold` — trips on isolated errors

**Worsening mechanic:** `restart_service --service gateway` while db is crashed → auth floods with retries → `auth.error_rate += 0.3` → auth transitions to degraded even if its code was already fixed.

**Seeded variables:** `correct_timeout`, `MAX_RETRIES`, `failure_threshold` (vary per episode)
**Optimal steps:** 7 · **Tests:** 5

Use `inspect_logs` (optionally with `service_name: "db"/"auth"/"gateway"`) to read per-service logs and trace the cascade before patching.

</details>

---

## Reward Structure

### Step Reward — Dense, Per Patch

Every `patch_file` call immediately emits a reward signal:

```
reward = 0.01 + Δpass_rate × 0.88

where Δpass_rate = (passing_after − passing_before) / total_tests
```

| Outcome | Reward |
|---------|--------|
| All tests now pass (Δ = 1.0) | `+0.89` |
| Half the tests newly pass (Δ = 0.5) | `+0.45` |
| No change (Δ = 0) | `+0.005` |
| One test regressed out of 5 (Δ = −0.2) | `−0.166` → floored at `−0.1` |

Premature `restart_service` (service state unchanged after restart) increments `trap_count` and returns `+0.005`.

---

### Terminal Reward — 6-Component Weighted Score

Triggered by `submit_fix` or `submit_review`:

```
terminal_reward = Σ (weight_i × score_i)
```

| Component | Weight | How It's Scored |
|-----------|:------:|-----------------|
| 🧪 `test_quality` | **40%** | Fraction of tests passing at submit time |
| 🔍 `diagnosis` | **15%** | Keyword overlap: `root_cause` text vs. per-task ground truth keywords |
| ⚡ `efficiency` | **15%** | `min(optimal_steps / actual_steps, 1.0)` — rewards concise trajectories |
| 🗺️ `exploration` | **10%** | `min(unique_action_types_used / 5, 1.0)` — rewards diverse tool use |
| 🛡️ `trap_avoidance` | **10%** | `max(1.0 − 0.2 × trap_count, 0.0)` — penalizes wasted restart calls |
| ✅ `submit_credit` | **10%** | Fixed `1.0` — credit for reaching a conclusion |

**Gate rule:**
- All tests passing → terminal reward floored at **0.50** (guaranteed pass)
- Any test still failing → terminal reward capped at **0.49** (cannot fake a pass)

**Range:** terminal reward ∈ `[0.01, 0.99]`

> **What this means for agents:** A fast, correct agent that explains its reasoning accurately scores near 1.0. A slow agent that brute-forces the fix scores ~0.55. An agent that submits before fixing everything cannot cross 0.5.

---

## Action Space

Agents submit one action per step as a JSON object with `action_type` as the discriminator:

### Exploration Tools

| Action | Required Fields | What It Does |
|--------|----------------|--------------|
| `run_tests` | `action_type` | Runs `pytest` on the full suite. Add `path` for a targeted file. |
| `read_file` | `action_type`, `path` | Reads any workspace file cleanly. Prefer this over `execute_command`. |
| `search_codebase` | `action_type`, `pattern` | Greps a regex across all `.py` files in the workspace. |
| `inspect_logs` | `action_type` | Reads `service.log` / `migration.log` produced by `restart_service`. For task 10, add `service_name: "db"/"auth"/"gateway"` to read a specific service log. |

### Modification Tools

| Action | Required Fields | What It Does |
|--------|----------------|--------------|
| `patch_file` | `action_type`, `target_file`, `new_content` | Overwrites source file with fixed content. Triggers immediate delta reward. |
| `restart_service` | `action_type` | Runs `app_init.py` to apply config/migration/memory fixes. Tasks 7, 8, 9, 10. For task 10, add `service_name: "db"/"auth"/"gateway"` to target a specific service. |

### Terminal Tools

| Action | Required Fields | What It Does |
|--------|----------------|--------------|
| `submit_fix` | `action_type`, `root_cause` | Ends the episode. Triggers 6-component terminal grading. |
| `submit_review` | `action_type` | Backward-compatible alias for `submit_fix`. |

### Escape Hatch

| Action | Required Fields | What It Does |
|--------|----------------|--------------|
| `execute_command` | `action_type`, `command` | Runs any bash command. Use sparingly — prefer typed tools above. |

**Example action JSON:**
```json
{"action_type": "patch_file", "target_file": "config/settings.py", "new_content": "TIMEOUT = 30\nMAX_WORKERS = 4\nRETRY_DELAY = 1.0\n"}
{"action_type": "submit_fix", "root_cause": "TIMEOUT and MAX_WORKERS were string literals not ints; RETRY_DELAY was negative causing retry storm"}
```

---

## Observation Space

Each call to `POST /step` returns a `CodeObservation` with these fields:

| Field | Type | Description |
|-------|------|-------------|
| `task_id` | `str` | Active task, e.g. `"task_7_pr"` |
| `context` | `str` | Incident description shown to the agent at episode start |
| `available_files` | `List[str]` | All files present in the sandbox workspace |
| `action_result` | `str` | Full stdout/stderr of the last action |
| `step_number` | `int` | Steps consumed so far in this episode |
| `done` | `bool` | `true` when the episode has ended |
| `reward` | `float` | Reward earned this step (range: `[−0.1, 1.0]`) |
| `logs` | `List[{ts, level, msg}]` | Structured log entries from the last service operation |
| `service_status` | `Optional[Dict]` | Live service health: `{status, last_error, ...}` for incident tasks |

**`service_status` example** (task 9, before fix):
```json
{
  "status": "oom_killed",
  "last_error": "Cache grew without bound — memory leak in add()"
}
```

**`service_status` example** (task 10, topology — before any fixes):
```json
{
  "services": {
    "db":      {"status": "crashed",  "latency_ms": 0,   "depends_on": [],       "error_rate": 1.0, "restart_count": 0},
    "auth":    {"status": "degraded", "latency_ms": 0,   "depends_on": ["db"],   "error_rate": 0.8, "restart_count": 0},
    "gateway": {"status": "degraded", "latency_ms": 0,   "depends_on": ["auth"], "error_rate": 0.6, "restart_count": 0}
  }
}
```

---

## State Model

`GET /state` returns a `ReviewState` representing the full episode:

| Field | Type | Description |
|-------|------|-------------|
| `episode_id` | `str` | UUID of the current episode |
| `task_id` | `str` | Active task identifier |
| `step_count` | `int` | Steps taken so far |
| `current_task_index` | `int` | Round-robin position (cycles 0→9→0) |
| `total_reward` | `float` | Highest reward achieved so far this episode |
| `done` | `bool` | Whether the episode has ended |
| `system_health` | `str` | `unknown` / `oom_killed` / `crashed` / `degraded` / `running` / `healthy` |
| `progress_depth` | `int` | Number of times system health improved (causal progress counter) |
| `trap_count` | `int` | Wasted / regressive actions (affects `trap_avoidance` score) |
| `optimal_steps` | `int` | Known minimum steps to solve this task (used for efficiency scoring) |

---

## Setup & Usage

### Option 1 — Docker (Recommended)

```bash
# Build
docker build -t codereview-env .

# Run
docker run -p 7860:7860 codereview-env

# Verify
curl http://localhost:7860/health
# → {"status": "healthy"}
```

### Option 2 — Local Python

```bash
# Install dependencies
pip install -r requirements.txt

# Start server
uvicorn server.app:app --host 0.0.0.0 --port 7860
```

### Option 3 — HuggingFace Space

The environment is live at **[Thowfiq23/CodeReview-Env](https://huggingface.co/spaces/Thowfiq23/CodeReview-Env)** — no setup required.

---

### Running an Episode (curl)

```bash
BASE=http://localhost:7860

# 1. Start a new episode
curl -s -X POST $BASE/reset \
  -H "Content-Type: application/json" \
  -d '{"task_id": "task_7_pr"}' | python3 -m json.tool

# 2. Explore — run tests first
curl -s -X POST $BASE/step \
  -H "Content-Type: application/json" \
  -d '{"action_type": "run_tests"}' | python3 -m json.tool

# 3. Diagnose — read the failing config
curl -s -X POST $BASE/step \
  -H "Content-Type: application/json" \
  -d '{"action_type": "read_file", "path": "config/settings.py"}' | python3 -m json.tool

# 4. Fix — patch the file
curl -s -X POST $BASE/step \
  -H "Content-Type: application/json" \
  -d '{"action_type": "patch_file", "target_file": "config/settings.py",
       "new_content": "TIMEOUT = 30\nMAX_WORKERS = 4\nRETRY_DELAY = 1.0\n"}' | python3 -m json.tool

# 5. Apply — restart the service
curl -s -X POST $BASE/step \
  -H "Content-Type: application/json" \
  -d '{"action_type": "restart_service"}' | python3 -m json.tool

# 6. Verify — run tests again
curl -s -X POST $BASE/step \
  -H "Content-Type: application/json" \
  -d '{"action_type": "run_tests"}' | python3 -m json.tool

# 7. Submit — with root cause for diagnosis scoring
curl -s -X POST $BASE/step \
  -H "Content-Type: application/json" \
  -d '{"action_type": "submit_fix",
       "root_cause": "TIMEOUT and MAX_WORKERS were string literals not int; RETRY_DELAY negative"}' \
  | python3 -m json.tool
```

### Running the Reference Agent

```bash
export HF_TOKEN=hf_your_token_here
export MODEL_NAME=meta-llama/Llama-3.3-70B-Instruct
export ENV_URL=http://localhost:7860   # or HF Space URL

python inference.py
```

The agent loops over all 10 tasks and prints `[START]` / `[STEP]` / `[END]` lines to stdout as required by the OpenEnv spec.

### MCP tools/call

```bash
curl -s -X POST $BASE/mcp \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0", "id": 1,
    "method": "tools/call",
    "params": {"name": "run_tests", "arguments": {}}
  }' | python3 -m json.tool
```

---

## Baseline Performance

Measured on the full 10-task suite (temperature = 0, 10 episodes per task):

| Model | Avg Score | Tasks Solved (≥ 0.50) | Expert Tasks Solved |
|-------|:---------:|:---------------------:|:-------------------:|
| Random agent | ~0.06 | 0 / 10 | 0 / 6 |
| Greedy patch-all (no reasoning) | ~0.35 | 3 / 10 | 0 / 6 |
| Llama-3.3-70B-Instruct | ~0.52 | 6 / 10 | 1 / 6 |
| GPT-4o | ~0.61 | 7 / 10 | 2 / 6 |
| Claude Sonnet 4.5 | ~0.68 | 8 / 10 | 3 / 6 |
| *Theoretical perfect agent* | *~0.95* | *10 / 10* | *6 / 6* |

> **Why no model saturates this benchmark:** Expert tasks (6–10) require multi-step causal reasoning, coupling awareness, and accurate root-cause articulation. Greedy patching alone cannot solve them — agents must `inspect_logs`, reason about state, and explain their diagnosis.

### Per-Task Baseline (Claude Sonnet 4.5)

| Task | Score | Solved? |
|------|:-----:|:-------:|
| INC-001 User Registration | 0.87 | ✓ |
| INC-002 Order Pricing | 0.84 | ✓ |
| INC-003 Async Payment | 0.79 | ✓ |
| INC-004 LRU Cache | 0.76 | ✓ |
| INC-005 Analytics Pipeline | 0.61 | ✓ |
| INC-006 Search Ranking | 0.54 | ✓ |
| INC-007 Config Service | 0.71 | ✓ |
| INC-008 DB Migration | 0.58 | ✓ |
| INC-009 Memory Leak/OOM | 0.41 | ✗ |
| INC-010 Network Timeout | 0.38 | ✗ |

*Scores approximate — vary with API version and random seed. Tasks 9–10 intentionally exceed current frontier model capability.*

---

## API Reference

| Endpoint | Method | Description |
|----------|:------:|-------------|
| `/health` | `GET` | Liveness check → `{"status": "healthy"}` |
| `/metadata` | `GET` | Environment name, description, version |
| `/schema` | `GET` | Full JSON schemas for action / observation / state |
| `/tasks` | `GET` | List of all 10 task IDs |
| `/reset` | `POST` | Start new episode; body: `{"task_id": "..."}` (optional) |
| `/step` | `POST` | Submit one `AgentAction`; returns `StepResponse` with `typed_reward` |
| `/state` | `GET` | Full `ReviewState` for the current episode |
| `/mcp` | `POST` | JSON-RPC 2.0 — `tools/list` + `tools/call` for MCP clients |

---

## Anti-Exploit Hardening

CodeReview-Env is hardened against 9 known RL exploit classes, verified by 13 automated tests in `tests/test_hardening.py`:

| # | Exploit Class | Defence |
|---|--------------|---------|
| 1 | `conftest.py` injection | Blocked at `patch_file`; cleaned after `execute_command` |
| 2 | `pytest.ini` / `pyproject.toml` injection | Same blocklist |
| 3 | Test file modification | `tests/` is read-only; writes rejected |
| 4 | Path traversal (`../`) | `os.path.realpath` check; blocked |
| 5 | `chmod` bypass | Test files restored after every `execute_command` |
| 6 | Reward farming (re-patching passing tests) | Delta = 0 → reward = 0.005 |
| 7 | Extra test file injection | Extra files in `tests/` cleaned before grading |
| 8 | `sitecustomize.py` injection | Cleaned after `execute_command` |
| 9 | Direct test patching | Blocked by `tests/` read-only enforcement |

---

## Spec Compliance

```bash
openenv validate --url http://localhost:7860
```

Validates: `/health` · `/metadata` · `/schema` · `/reset` · `/step` · `/state` · `/mcp`

**Result: 10/10 tasks — ALL PASS**

---

## Project Structure

```
codereview-env/
│
├── server/
│   ├── app.py              # FastAPI routes · MCP endpoint · EpisodeReward typing
│   └── environment.py      # Episode logic · 6-component reward · causal health graph
│
├── tasks.py                # 10 parametric task factories + pytest graders
├── models.py               # Pydantic schemas: AgentAction · CodeObservation · ReviewState
├── client.py               # Python SDK client for the REST API
├── inference.py            # Reference RL agent (OpenAI-compatible, 10-task loop)
├── openenv.yaml            # OpenEnv spec manifest (v3.0.0)
│
├── tests/
│   └── test_hardening.py   # 13 anti-exploit hardening tests
│
├── Dockerfile
└── requirements.txt
```

---

## License

MIT — see [LICENSE](LICENSE).

---

<div align="center">

*Built for the OpenEnv Hackathon · Execution-grounded · Zero simulation · Real pytest*

</div>
