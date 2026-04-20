# AutoPatch

## What It Does

AutoPatch is a self-improving multi-agent system that automatically detects, patches, and validates Python security and logic bugs. It connects to a sandboxed code-review environment, runs pytest to observe failures, plans targeted fixes using an LLM, applies patches file-by-file, and scores the result — then uses an Evolver agent to rewrite its own system prompt based on reward trajectories, getting measurably better over episodes without human intervention.

---

## Architecture

```
                        ┌─────────────────────────────────┐
                        │         LangGraph Pipeline        │
                        │                                   │
  codereview-env        │  reset → plan → read_file         │
  (localhost:7860) ◄────┤       → code → critic → patch     │
                        │       → submit                    │
                        │                                   │
                        │  Agents:                          │
                        │  ┌──────────┐  ┌──────────────┐  │
                        │  │ Planner  │  │    Coder     │  │
                        │  │ (tasks)  │  │ (LLM patch)  │  │
                        │  └──────────┘  └──────────────┘  │
                        │  ┌──────────┐  ┌──────────────┐  │
                        │  │  Critic  │  │   Evolver    │  │
                        │  │(validate)│  │(prompt rewrite│ │
                        │  └──────────┘  └──────────────┘  │
                        │  ┌──────────┐                     │
                        │  │  Memory  │  (trajectory store) │
                        │  └──────────┘                     │
                        └─────────────────────────────────┘
                                        │
                        ┌───────────────▼──────────────────┐
                        │   FastAPI + React Dashboard        │
                        │   localhost:8000 / localhost:5173  │
                        │   Live SSE logs + reward curve     │
                        └──────────────────────────────────┘
```

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
cd autopatch/dashboard && npm install
```

### 2. Configure environment

```bash
cp .env.example .env
# Fill in:
GROQ_API_KEY=your_groq_api_key
GITHUB_TOKEN=your_github_pat   # optional, for ingester
MODEL_NAME=llama-3.3-70b-versatile
ENV_URL=http://localhost:7860
```

### 3. Start the codereview sandbox

```bash
cd codereview-env
pip install -e .
python -m pytest --co -q   # verify tasks load
uvicorn codereview.server:app --port 7860
```

### 4. Run AutoPatch

```bash
# CLI — 10 episodes recommended (Groq free tier: 100k tokens/day)
python -m autopatch.run --episodes 10 --log-level INFO

# With dashboard
uvicorn autopatch.api:app --port 8000 &
cd autopatch/dashboard && npm run dev
# Open http://localhost:5173
```

### 5. GitHub ingester (optional)

```bash
python -m autopatch.github.ingester --repo owner/repo --issue 42
```

---

## Benchmark Scores (10-episode run)

| Task | Bug Type | Score |
|------|----------|-------|
| task_1 | sql_injection | 1.000 |
| task_2 | weak_crypto | 1.000 |
| task_3 | hardcoded_secret | 1.000 |
| task_4 | async_error | 1.000 |
| task_5 | logic_error (sort) | 1.000 |
| task_6 | logic_error (migration) | 1.000 |
| task_7 | logic_error (exception) | 1.000 |
| task_8 | logic_error (retry) | 1.000 |
| task_9 | logic_error (memory leak, 2 files) | 0.362 |
| task_10 | logic_error (cascade, 3 services) | 0.880 |
| **Average** | | **0.924** |

---

## How the Evolver Works

Every 5 episodes, the Evolver agent:

1. Retrieves all stored reward trajectories from Memory
2. Summarises each episode: steps taken, step of first reward, final score
3. Sends the current Coder system prompt + performance data to the LLM
4. Receives a rewritten prompt that **preserves all existing rules** and adds new guidance targeting low-scoring patterns
5. Calls `coder.set_system(new_prompt)` — all subsequent episodes use the improved prompt

The evolved prompt is printed to stdout so the improvement is visible:
```
[EVOLVER] episode=5 prompt updated (2912->4036 chars)
[EVOLVER] episode=10 prompt updated (4036->5142 chars)
```

Minimum 3 trajectories required before evolution fires. A failed evolution never crashes the run.
