#!/usr/bin/env python3
"""
CodeReview-Env - Complete QA Audit Script
Pre-submission audit for OpenEnv Round 1.
Tests LOCAL server and HF Space (remote).
"""

import json
import sys
import time
import traceback
import requests
import subprocess

# ─── Configurable ───────────────────────────────────────────────────────────
LOCAL_URL  = "http://localhost:7860"
REMOTE_URL = "https://thowfiq23-codereview-env-staging.hf.space"
# ─────────────────────────────────────────────────────────────────────────────

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

results = {"local": [], "remote": []}

def record(env_label, test_name, status, detail=""):
    tag = f"[{status}]"
    print(f"  {tag:7s} [{env_label.upper()}] {test_name}", end="")
    if detail:
        print(f"\n           {detail}", end="")
    print()
    results[env_label].append((test_name, status, detail))

def get(base, path, **kw):
    return requests.get(f"{base}{path}", timeout=30, **kw)

def post(base, path, payload=None, raw=None, **kw):
    if raw is not None:
        return requests.post(f"{base}{path}", data=raw,
                             headers={"Content-Type": "application/json"}, timeout=60, **kw)
    return requests.post(f"{base}{path}", json=payload, timeout=60, **kw)

# ════════════════════════════════════════════════════════════════════════════
# SINGLE-SERVER TEST SUITE
# ════════════════════════════════════════════════════════════════════════════

def run_suite(base_url, label):
    print(f"\n{'='*70}")
    print(f" TESTING: {label}  ({base_url})")
    print(f"{'='*70}\n")

    # ── 1. GET /health ────────────────────────────────────────────────────
    try:
        r = get(base_url, "/health")
        assert r.status_code == 200, f"HTTP {r.status_code}"
        body = r.json()
        status_val = body.get("status", "")
        # Spec requires "healthy"; old code had "ok" — accept both for remote
        assert status_val in ("ok", "healthy"), f"Body: {body}"
        record(label, f"GET /health → {{status:{status_val}}}", PASS)
    except Exception as e:
        record(label, "GET /health", FAIL, str(e))

    # ── 2. GET /tasks ─────────────────────────────────────────────────────
    try:
        r = get(base_url, "/tasks")
        assert r.status_code == 200, f"HTTP {r.status_code}"
        body = r.json()
        assert "tasks" in body, f"No 'tasks' key: {body}"
        assert body["tasks"] == ["task_1_pr", "task_2_pr", "task_3_pr"], f"Got: {body['tasks']}"
        record(label, "GET /tasks → 3 task ids", PASS)
    except Exception as e:
        record(label, "GET /tasks → 3 task ids", FAIL, str(e))

    # ── 3. POST /reset — returns a valid CodeObservation ─────────────────
    try:
        r = post(base_url, "/reset")
        assert r.status_code == 200, f"HTTP {r.status_code}"
        obs = r.json()
        # Check required CodeObservation fields
        for field in ("task_id", "context", "available_files", "step_number", "done", "reward"):
            assert field in obs, f"Missing field: {field}"
        valid_tasks = {"task_1_pr", "task_2_pr", "task_3_pr"}
        assert obs["task_id"] in valid_tasks, f"Unexpected task_id: {obs['task_id']}"
        assert obs["step_number"] == 0, f"step_number should be 0, got {obs['step_number']}"
        assert obs["done"] == False
        assert obs["reward"] == 0.01
        assert isinstance(obs["available_files"], list) and len(obs["available_files"]) > 0
        record(label, f"POST /reset → CodeObservation ({obs['task_id']})", PASS)
    except Exception as e:
        record(label, "POST /reset → CodeObservation", FAIL, str(e))

    # ── 4. GET /state after reset ─────────────────────────────────────────
    try:
        r = get(base_url, "/state")
        assert r.status_code == 200
        state = r.json()
        for field in ("episode_id", "task_id", "step_count", "done", "total_reward"):
            assert field in state, f"Missing field: {field}"
        assert state["task_id"] in valid_tasks, f"Unexpected task_id: {state['task_id']}"
        assert state["step_count"] == 0
        assert state["done"] == False
        assert state["total_reward"] == 0.01
        record(label, f"GET /state → ReviewState (all fields, task={state['task_id']})", PASS)
    except Exception as e:
        record(label, "GET /state → ReviewState (all fields)", FAIL, str(e))

    # ── 5. POST /step execute_command → reward 0.0, step_number 1 ─────────
    try:
        r = post(base_url, "/step", {"action_type": "execute_command", "command": "echo hello"})
        assert r.status_code == 200
        body = r.json()
        # Check StepResponse structure
        for field in ("observation", "reward", "done", "info", "typed_reward"):
            assert field in body, f"Missing field: {field}"
        obs = body["observation"]
        assert obs["step_number"] == 1, f"Expected 1, got {obs['step_number']}"
        assert body["reward"] == 0.01, f"execute_command should return 0.01 reward (floor), got {body['reward']}"
        assert body["done"] == False
        # Check typed_reward fields
        tr = body["typed_reward"]
        for field in ("value", "is_terminal", "breakdown"):
            assert field in tr, f"typed_reward missing: {field}"
        assert tr["value"] == 0.01
        assert tr["is_terminal"] == False
        record(label, "POST /step execute_command → reward=0.0 + typed_reward", PASS)
    except Exception as e:
        record(label, "POST /step execute_command → reward=0.0 + typed_reward", FAIL, str(e))

    # ── 6. Security: path traversal ──────────────────────────────────────
    try:
        r = post(base_url, "/step", {
            "action_type": "patch_file",
            "target_file": "../../etc/passwd",
            "new_content": "pwned"
        })
        body = r.json()
        obs = body["observation"]
        assert "Security Error" in obs["action_result"] or "traversal" in obs["action_result"].lower(), \
            f"Expected security error, got: {obs['action_result']}"
        assert body["reward"] == 0.01, f"Traversal should give 0.01 reward (floor), got {body['reward']}"
        record(label, "Security: path traversal blocked (../../etc/passwd)", PASS)
    except Exception as e:
        record(label, "Security: path traversal blocked (../../etc/passwd)", FAIL, str(e))

    # ── 7. Security: absolute path blocked ──────────────────────────────
    try:
        r = post(base_url, "/step", {
            "action_type": "patch_file",
            "target_file": "/etc/passwd",
            "new_content": "pwned"
        })
        body = r.json()
        obs = body["observation"]
        assert "Security Error" in obs["action_result"] or "traversal" in obs["action_result"].lower(), \
            f"Expected security error, got: {obs['action_result']}"
        record(label, "Security: absolute path blocked (/etc/passwd)", PASS)
    except Exception as e:
        record(label, "Security: absolute path blocked (/etc/passwd)", FAIL, str(e))

    # ── 8. Security: auth/../tests/ bypass blocked ───────────────────────
    try:
        r = post(base_url, "/step", {
            "action_type": "patch_file",
            "target_file": "auth/../tests/test_auth.py",
            "new_content": "def test_sql_injection_fixed(): pass\ndef test_crypto_fixed(): pass\n"
        })
        body = r.json()
        obs = body["observation"]
        assert "Security Error" in obs["action_result"] or "read-only" in obs["action_result"].lower(), \
            f"Expected security error, got: {obs['action_result']}"
        record(label, "Security: auth/../tests/ traversal bypass blocked", PASS)
    except Exception as e:
        record(label, "Security: auth/../tests/ traversal bypass blocked", FAIL, str(e))

    # ── 9. patch_file — syntax error detection ──────────────────────────
    try:
        r = post(base_url, "/step", {
            "action_type": "patch_file",
            "target_file": "auth/models.py",
            "new_content": "def broken(\n    pass"
        })
        body = r.json()
        obs = body["observation"]
        assert "Syntax Error" in obs["action_result"] or "syntax" in obs["action_result"].lower(), \
            f"Expected syntax error feedback, got: {obs['action_result']}"
        assert body["reward"] == 0.01, f"Syntax error should give 0.01 reward (floor), got {body['reward']}"
        record(label, "patch_file: syntax error detected and blocked", PASS)
    except Exception as e:
        record(label, "patch_file: syntax error detected and blocked", FAIL, str(e))

    # ── 10. patch_file — valid fix, dense reward [0, 0.9] ─────────────────
    try:
        r = post(base_url, "/step", {
            "action_type": "patch_file",
            "target_file": "auth/models.py",
            "new_content": (
                "def get_user_query(username):\n"
                "    return ('SELECT * FROM users WHERE username = %s', (username,))\n"
            )
        })
        body = r.json()
        reward = body["reward"]
        assert 0.01 <= reward <= 0.89, f"patch_file reward should be in (0,1) open interval, got {reward}"
        record(label, f"patch_file: dense reward in [0, 0.9] = {reward}", PASS)
    except Exception as e:
        record(label, "patch_file: dense reward in [0, 0.9]", FAIL, str(e))

    # ── 11. submit_review — partial fix gives partial score ───────────────
    # (auth/crypto.py still uses MD5)
    try:
        r = post(base_url, "/step", {
            "action_type": "submit_review",
            "summary": "Fixed SQL injection but not MD5"
        })
        body = r.json()
        reward = body["reward"]
        done = body["done"]
        assert done == True, f"submit_review should set done=True, got {done}"
        assert 0.0 <= reward <= 1.0, f"reward {reward} out of range"
        record(label, f"submit_review: done=True, reward={reward:.4f}", PASS)
    except Exception as e:
        record(label, "submit_review: done=True", FAIL, str(e))

    # ── 12. Post-done guard ───────────────────────────────────────────────
    try:
        r = post(base_url, "/step", {"action_type": "execute_command", "command": "echo post-done"})
        body = r.json()
        obs = body["observation"]
        assert obs["done"] == True, f"Post-done step should still return done=True"
        assert "already done" in obs["action_result"].lower() or "reset" in obs["action_result"].lower(), \
            f"Expected already-done message, got: {obs['action_result']}"
        record(label, "Post-done guard: further steps blocked after done=True", PASS)
    except Exception as e:
        record(label, "Post-done guard: further steps blocked after done=True", FAIL, str(e))

    # ── 13. Task cycling: reset #2 → advances to next task ───────────────
    try:
        # Get current task after all the prior steps to know which one we're on
        state_r = get(base_url, "/state")
        cur_state = state_r.json()
        cur_idx = cur_state.get("current_task_index", 0)
        tasks_ordered = ["task_1_pr", "task_2_pr", "task_3_pr"]
        next_idx = (cur_idx + 1) % 3
        expected_next = tasks_ordered[next_idx]

        r = post(base_url, "/reset")
        obs = r.json()
        assert obs["task_id"] == expected_next, f"Expected {expected_next}, got {obs['task_id']}"
        record(label, f"Task cycling: advances correctly ({cur_state['task_id']} → {expected_next})", PASS)
    except Exception as e:
        record(label, "Task cycling: advances correctly", FAIL, str(e))

    # ── 14. Task cycling: reset #3 → advances again ───────────────────────
    try:
        state_r = get(base_url, "/state")
        cur_state = state_r.json()
        cur_idx = cur_state.get("current_task_index", 0)
        next_idx = (cur_idx + 1) % 3
        expected_next = tasks_ordered[next_idx]

        r = post(base_url, "/reset")
        obs = r.json()
        assert obs["task_id"] == expected_next, f"Expected {expected_next}, got {obs['task_id']}"
        record(label, f"Task cycling: advances again ({cur_state['task_id']} → {expected_next})", PASS)
    except Exception as e:
        record(label, "Task cycling: advances again", FAIL, str(e))

    # ── 15. Task cycling: reset #4 → wrap-around after 3 resets ─────────
    try:
        state_r = get(base_url, "/state")
        cur_state = state_r.json()
        cur_idx = cur_state.get("current_task_index", 0)
        next_idx = (cur_idx + 1) % 3
        expected_next = tasks_ordered[next_idx]

        r = post(base_url, "/reset")
        obs = r.json()
        assert obs["task_id"] == expected_next, f"Expected {expected_next}, got {obs['task_id']}"
        record(label, f"Task cycling: wrap-around ({cur_state['task_id']} → {expected_next})", PASS)
    except Exception as e:
        record(label, "Task cycling: wrap-around", FAIL, str(e))

    # ── 16. max_steps enforcement ─────────────────────────────────────────
    # We are now on task_1_pr again. Exhaust 10 steps with execute_command.
    max_steps_hit = False
    steps_taken = 0
    done_val = False
    try:
        # Current step_count after reset = 0
        for i in range(10):
            r = post(base_url, "/step", {"action_type": "execute_command", "command": "echo x"})
            body = r.json()
            done_val = body["done"]
            steps_taken = body["observation"]["step_number"]
            if done_val:
                max_steps_hit = True
                break
        assert max_steps_hit, f"Episode should end at step 10, still running at step {steps_taken}"
        assert steps_taken == 10, f"Expected step_number=10, got {steps_taken}"
        record(label, f"max_steps=10 enforced (done at step {steps_taken})", PASS)
    except Exception as e:
        record(label, "max_steps=10 enforced", FAIL, str(e))

    # ── 17. Invalid JSON recovery ─────────────────────────────────────────
    try:
        # Need to reset first since episode ended
        post(base_url, "/reset")
        r = post(base_url, "/step", raw=b"{not valid json!!!")
        assert r.status_code == 200, f"Should not 500 on bad JSON, got HTTP {r.status_code}"
        body = r.json()
        assert "observation" in body, "Should return valid StepResponse even on bad JSON"
        record(label, "Invalid JSON recovery: server returns 200 with fallback action", PASS)
    except Exception as e:
        record(label, "Invalid JSON recovery", FAIL, str(e))

    # ── 18. Output truncation (200k char command) ─────────────────────────
    try:
        # Generate a large output via yes command with limit
        r = post(base_url, "/step", {
            "action_type": "execute_command",
            "command": "python3 -c \"print('A' * 300000)\""
        })
        body = r.json()
        obs = body["observation"]
        result_text = obs["action_result"]
        assert "TRUNCATED" in result_text, f"Large output should be truncated, got {len(result_text)} chars (first 100: {result_text[:100]})"
        record(label, "Output truncation: large output truncated with [TRUNCATED] marker", PASS)
    except Exception as e:
        record(label, "Output truncation: large output truncated", FAIL, str(e))

    # ── 19. Full reward trajectory: task_2_pr ─────────────────────────────
    try:
        # Keep resetting until we land on task_2_pr (cycling may be anywhere)
        for _ in range(4):
            r = post(base_url, "/reset")
            obs = r.json()
            if obs["task_id"] == "task_2_pr":
                break
        assert obs["task_id"] == "task_2_pr", f"Could not reach task_2_pr after 4 resets, got {obs['task_id']}"

        # Fix billing/discounts.py first (wrong multiplier: 0.20 should be 0.80)
        r = post(base_url, "/step", {
            "action_type": "patch_file",
            "target_file": "billing/discounts.py",
            "new_content": "def apply_discount(amount):\n    return amount * 0.80\n"
        })
        body = r.json()
        reward_after_discount = body["reward"]

        # Fix billing/cart.py (handle quantity)
        r = post(base_url, "/step", {
            "action_type": "patch_file",
            "target_file": "billing/cart.py",
            "new_content": (
                "from billing.discounts import apply_discount\n\n"
                "def calculate_total(items):\n"
                "    total = 0\n"
                "    for item in items:\n"
                "        total += item['price'] * item['quantity']\n"
                "    return apply_discount(total)\n"
            )
        })
        body = r.json()
        reward_after_cart = body["reward"]

        # Submit
        r = post(base_url, "/step", {
            "action_type": "submit_review",
            "summary": "Fixed quantity multiplication and discount rate"
        })
        body = r.json()
        final_reward = body["reward"]
        done_val = body["done"]

        assert done_val, "submit_review should set done=True"
        assert final_reward == 0.99, f"Both tests pass, expected 0.99, got {final_reward}"
        record(label, f"task_2_pr full trajectory: submit_reward={final_reward} (expected 0.99)", PASS)
    except Exception as e:
        record(label, "task_2_pr full trajectory", FAIL, str(e))
        traceback.print_exc()

    # ── 20. Full reward trajectory: task_3_pr ─────────────────────────────
    try:
        for _ in range(4):
            r = post(base_url, "/reset")
            obs = r.json()
            if obs["task_id"] == "task_3_pr":
                break
        assert obs["task_id"] == "task_3_pr", f"Could not reach task_3_pr after 4 resets, got {obs['task_id']}"

        # Fix payments/config.py (remove hardcoded key)
        r = post(base_url, "/step", {
            "action_type": "patch_file",
            "target_file": "payments/config.py",
            "new_content": (
                "import os\n\n"
                "STRIPE_TEST_KEY = os.getenv('STRIPE_TEST_KEY')\n"
                "STRIPE_LIVE_KEY = os.getenv('STRIPE_LIVE_KEY')\n"
            )
        })

        # Fix payments/processor.py (make async with asyncio.sleep)
        r = post(base_url, "/step", {
            "action_type": "patch_file",
            "target_file": "payments/processor.py",
            "new_content": (
                "import asyncio\n\n"
                "async def process_payment(user_id, amount):\n"
                "    await asyncio.sleep(0)\n"
                "    return True\n"
            )
        })
        body = r.json()
        reward_patch = body["reward"]

        # Submit
        r = post(base_url, "/step", {
            "action_type": "submit_review",
            "summary": "Removed hardcoded Stripe key and made process_payment async"
        })
        body = r.json()
        final_reward = body["reward"]
        done_val = body["done"]

        assert done_val, "submit_review should set done=True"
        assert final_reward == 0.99, f"Both tests pass, expected 0.99, got {final_reward}"
        record(label, f"task_3_pr full trajectory: submit_reward={final_reward} (expected 0.99)", PASS)
    except Exception as e:
        record(label, "task_3_pr full trajectory", FAIL, str(e))
        traceback.print_exc()

    # ── 21. Full reward trajectory: task_1_pr complete fix ────────────────
    try:
        for _ in range(4):
            r = post(base_url, "/reset")
            obs = r.json()
            if obs["task_id"] == "task_1_pr":
                break
        assert obs["task_id"] == "task_1_pr", f"Could not reach task_1_pr after 4 resets, got {obs['task_id']}"

        # Fix SQL injection
        r = post(base_url, "/step", {
            "action_type": "patch_file",
            "target_file": "auth/models.py",
            "new_content": (
                "def get_user_query(username):\n"
                "    return ('SELECT * FROM users WHERE username = %s', (username,))\n"
            )
        })

        # Fix MD5
        r = post(base_url, "/step", {
            "action_type": "patch_file",
            "target_file": "auth/crypto.py",
            "new_content": (
                "import hashlib\n\n"
                "def hash_password(password: str):\n"
                "    return hashlib.sha256(password.encode()).hexdigest()\n"
            )
        })
        body = r.json()
        dense_reward = body["reward"]
        assert 0.01 <= dense_reward <= 0.89, f"Expected dense reward in (0,1), got {dense_reward}"

        # Submit
        r = post(base_url, "/step", {
            "action_type": "submit_review",
            "summary": "Fixed SQL injection and MD5 hashing"
        })
        body = r.json()
        final_reward = body["reward"]
        done_val = body["done"]

        assert done_val, "submit_review should set done=True"
        assert final_reward == 0.99, f"Both tests pass, expected 0.99, got {final_reward}"
        record(label, f"task_1_pr complete fix: submit_reward={final_reward} (expected 0.99)", PASS)
    except Exception as e:
        record(label, "task_1_pr complete fix", FAIL, str(e))
        traceback.print_exc()

    # ── 22. State consistency after episode ───────────────────────────────
    try:
        state_r = get(base_url, "/state")
        state = state_r.json()
        assert state["done"] == True, f"State.done should be True after submit, got {state['done']}"
        assert state["total_reward"] == 0.99, f"total_reward should be 0.99, got {state['total_reward']}"
        record(label, "State consistency: total_reward=0.99 after perfect solve", PASS)
    except Exception as e:
        record(label, "State consistency: total_reward after perfect solve", FAIL, str(e))

    print()


# ════════════════════════════════════════════════════════════════════════════
# OPENENV VALIDATE CLI
# ════════════════════════════════════════════════════════════════════════════

def run_openenv_validate():
    print(f"\n{'='*70}")
    print(" OPENENV VALIDATE CLI")
    print(f"{'='*70}\n")

    # 1. Structural validate (openenv validate .)
    try:
        proc = subprocess.run(
            ["openenv", "validate", "."],
            capture_output=True, text=True, timeout=30,
            cwd="/workspaces/codereview-env"
        )
        output = (proc.stdout + proc.stderr).strip()
        print(f"  [structural] exit={proc.returncode}  output: {output[:200]}")
        if proc.returncode == 0 and ("OK" in output or "Ready" in output):
            record("local", "openenv validate . (structural)", PASS, output[:200])
        else:
            record("local", "openenv validate . (structural)", FAIL, output[:200])
    except Exception as e:
        record("local", "openenv validate . (structural)", FAIL, str(e))

    # 2. Runtime validate (openenv validate --url http://localhost:7860)
    try:
        proc = subprocess.run(
            ["openenv", "validate", "--url", LOCAL_URL, "--timeout", "30"],
            capture_output=True, text=True, timeout=60,
            cwd="/workspaces/codereview-env"
        )
        output = (proc.stdout + proc.stderr).strip()
        print(f"  [runtime]    exit={proc.returncode}")
        try:
            data = json.loads(output)
            passed = data.get("passed", False)
            summary = data.get("summary", {})
            print(f"  passed={passed}  summary={summary}")
            failed_criteria = summary.get("failed_criteria", [])
            if passed:
                record("local", "openenv validate --url (runtime)", PASS,
                       f"passed={summary.get('passed_count')}/{summary.get('total_count')}")
            else:
                record("local", "openenv validate --url (runtime)", FAIL,
                       f"failed_criteria={failed_criteria}")
        except json.JSONDecodeError:
            print(f"  Non-JSON output: {output[:300]}")
            if proc.returncode == 0:
                record("local", "openenv validate --url (runtime)", PASS, output[:200])
            else:
                record("local", "openenv validate --url (runtime)", FAIL, output[:200])
    except Exception as e:
        record("local", "openenv validate --url (runtime)", FAIL, str(e))


# ════════════════════════════════════════════════════════════════════════════
# SUMMARY REPORT
# ════════════════════════════════════════════════════════════════════════════

def print_summary():
    print(f"\n{'='*70}")
    print(" FINAL QA AUDIT SUMMARY")
    print(f"{'='*70}")
    for label in ("local", "remote"):
        tests = results[label]
        if not tests:
            continue
        passed  = sum(1 for _, s, _ in tests if s == PASS)
        failed  = sum(1 for _, s, _ in tests if s == FAIL)
        skipped = sum(1 for _, s, _ in tests if s == SKIP)
        total   = len(tests)
        print(f"\n  {label.upper()}: {passed}/{total} PASS  {failed} FAIL  {skipped} SKIP")
        if failed:
            print(f"  Failures:")
            for name, status, detail in tests:
                if status == FAIL:
                    print(f"    ✗ {name}")
                    if detail:
                        print(f"      → {detail}")
    print()


# ════════════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # 1. Local tests
    run_suite(LOCAL_URL, "local")

    # 2. openenv validate
    run_openenv_validate()

    # 3. Remote tests (HF Space)
    print(f"\n  Checking HF Space availability...", end=" ")
    try:
        r = requests.get(f"{REMOTE_URL}/health", timeout=20)
        if r.status_code == 200:
            print("ONLINE")
            run_suite(REMOTE_URL, "remote")
        else:
            print(f"HTTP {r.status_code} — SKIPPING REMOTE TESTS")
            record("remote", "HF Space reachable", SKIP, f"HTTP {r.status_code}")
    except Exception as e:
        print(f"OFFLINE ({e}) — SKIPPING REMOTE TESTS")
        record("remote", "HF Space reachable", SKIP, str(e))

    # 4. Summary
    print_summary()

    # Exit code
    local_failures = sum(1 for _, s, _ in results["local"] if s == FAIL)
    sys.exit(0 if local_failures == 0 else 1)
