"""
Task definitions for CodeReview-Env.

Each task uses a _repo_factory(seed) callable so every episode reset generates
a fresh problem instance with different concrete values.  The LLM cannot
memorise answers — it must read the code, understand the bug, and fix the
algorithm correctly.

Difficulty progression:
  task_1_pr  Medium        — 3 bugs, user registration (INC-001)
  task_2_pr  Medium-Hard   — 3 bugs, order pricing (INC-019)
  task_3_pr  Hard          — 3 bugs, async payment processor (INC-034)
  task_4_pr  Hard          — 3 bugs, LRU cache (INC-056)
  task_5_pr  Very Hard     — 3 algorithmic bugs, analytics pipeline (INC-121)
  task_6_pr  Expert        — 3 coupled bugs, search ranking (INC-312)
  task_7_pr  Expert        — 3 config type bugs + restart gate (INC-031)
  task_8_pr  Expert        — 3 migration bugs + inspect/restart gate (INC-047)
  task_9_pr  Expert        — 3 memory-leak bugs + OOM restart gate (INC-089)
  task_10_pr Expert        — 3 network/circuit bugs + cascade trap (INC-201)
"""
import re
import subprocess
import os
import platform
import random
import math
from typing import List, Dict, Any, Callable


# =============================================================================
# Repository factories
# Each factory accepts an integer seed and returns {filepath: source_code}.
# Seeds are derived from the episode UUID by environment.py so each
# reset() gets a reproducible but unique problem instance.
# =============================================================================

def _make_task_1_repo(seed: int) -> dict:
    """
    User Registration — 3 bugs across 2 files.
      1. users/validator.py  email regex uses '*' (0-or-more) → accepts @example.com
      2. users/validator.py  password length check uses '> 8' → rejects exactly-8-char PW
      3. users/storage.py    MD5 hashing (32 hex chars) — need ≥64 (SHA-256+)

    Randomised per episode:
      • Which 3 of 6 valid email addresses appear in the test
      • Which of 4 password variant sets (all structurally equivalent) is used
    """
    rng = random.Random(seed)

    # ── random test parameters ──────────────────────────────────────────────
    email_pool = [
        "user@example.com",
        "first.last@company.io",
        "alice+filter@mail.example.net",
        "bob.smith@subdomain.org",
        "carol@example-corp.co.uk",
        "x123@university.edu",
    ]
    valid_emails = rng.sample(email_pool, 3)

    # Four structurally identical variants: (valid-8, invalid-7, no-upper, no-digit)
    pw_variants = [
        ("Passw0rd", "Passw0r", "password1", "PasswordX"),
        ("MyPas1sX", "MyPas1s", "mypas1sx", "MyPasssXX"),
        ("Hello1Wo", "Hello1W", "hello1wo", "HelloWorld"),
        ("Abc1DefG", "Abc1Def", "abc1defg", "AbcDefGhi"),
    ]
    pw_valid8, pw_7, pw_no_upper, pw_no_digit = pw_variants[rng.randint(0, 3)]

    # ── static buggy sources ─────────────────────────────────────────────────
    validator_src = (
        "import re\n"
        "\n"
        "def validate_email(email: str) -> bool:\n"
        "    # BUG 1: '*' allows empty local part -- '@example.com' is accepted\n"
        "    return bool(re.match(r'^[\\w.+-]*@([\\w-]+\\.)+\\w{2,}$', email))\n"
        "\n"
        "def validate_password(password: str) -> bool:\n"
        "    \"\"\"Password must be >=8 chars, contain uppercase and digit.\"\"\"\n"
        "    # BUG 2: '> 8' rejects exactly-8-char passwords; must be '>= 8'\n"
        "    has_length = len(password) > 8\n"
        "    has_upper  = any(c.isupper() for c in password)\n"
        "    has_digit  = any(c.isdigit() for c in password)\n"
        "    return has_length and has_upper and has_digit\n"
    )
    storage_src = (
        "import hashlib\n"
        "\n"
        "def hash_password(password: str) -> str:\n"
        "    \"\"\"Return a secure hex-digest of the password.\"\"\"\n"
        "    # BUG 3: MD5 produces only 32 hex chars and is cryptographically broken\n"
        "    return hashlib.md5(password.encode()).hexdigest()\n"
        "\n"
        "def store_user(email: str, password: str) -> dict:\n"
        "    return {'email': email, 'password_hash': hash_password(password)}\n"
    )

    # ── parametric test file ─────────────────────────────────────────────────
    # Uses .format(); {{ }} produces literal { } in the generated test source.
    test_template = """\
from users.validator import validate_email, validate_password
from users.storage import hash_password

# ── email ──────────────────────────────────────────────────────────────────

def test_email_rejects_empty_local_part():
    assert not validate_email("@example.com"), (
        "Validation Error: address with no local part before '@' must be rejected."
    )

def test_email_accepts_valid_addresses():
    for addr in {valid_emails}:
        assert validate_email(addr), (
            f"Validation Error: {{addr!r}} is a valid address but was rejected."
        )

def test_email_rejects_malformed():
    for addr in ["", "notanemail", "user@", "@@domain.com"]:
        assert not validate_email(addr), (
            f"Validation Error: malformed address {{addr!r}} was incorrectly accepted."
        )

# ── password ───────────────────────────────────────────────────────────────

def test_password_accepts_8_char_minimum():
    # {pw_valid8!r} is exactly 8 characters with uppercase + digit — must be valid
    assert validate_password({pw_valid8}), (
        "Validation Error: 8-character password satisfying all rules was rejected. "
        "Minimum length must be 8 (>= 8), not 9 (> 8)."
    )

def test_password_rejects_7_chars():
    assert not validate_password({pw_7}), (
        "Validation Error: 7-character password must be rejected."
    )

def test_password_rejects_no_uppercase():
    assert not validate_password({pw_no_upper}), (
        "Validation Error: password with no uppercase letter must be rejected."
    )

def test_password_rejects_no_digit():
    assert not validate_password({pw_no_digit}), (
        "Validation Error: password with no digit must be rejected."
    )

# ── hashing ────────────────────────────────────────────────────────────────

def test_hash_uses_strong_algorithm():
    h = hash_password("hunter2")
    assert len(h) >= 64, (
        f"Security Error: hash length {{len(h)}} chars is too short. "
        "MD5=32 chars is cryptographically broken; use SHA-256 (64 chars) or stronger."
    )

def test_hash_determinism_and_uniqueness():
    assert hash_password("alpha") != hash_password("beta"), (
        "Security Error: different passwords produced the same hash."
    )
    assert hash_password("same_pw") == hash_password("same_pw"), (
        "Security Error: same password produced different hashes on two calls."
    )
"""
    test_src = test_template.format(
        valid_emails=repr(valid_emails),
        pw_valid8=repr(pw_valid8),
        pw_7=repr(pw_7),
        pw_no_upper=repr(pw_no_upper),
        pw_no_digit=repr(pw_no_digit),
    )

    return {
        "users/validator.py": validator_src,
        "users/storage.py": storage_src,
        "tests/test_registration.py": test_src,
    }


def _make_task_2_repo(seed: int) -> dict:
    """
    Order Pricing Engine — 3 bugs across 2 files.
      1. orders/cart.py     ignores item['quantity'] — sums prices, not price×qty
      2. orders/pricing.py  apply_discount keeps the X% instead of removing it
      3. orders/pricing.py  apply_tax adds tax_rate as a raw offset, not a multiplier

    Randomised per episode:
      • 2–3 items with different prices and quantities
      • discount_pct: one of [10, 15, 20, 25]
      • tax_rate: one of [0.05, 0.08, 0.10, 0.15]
      • All expected values computed from the correct formula — the LLM must
        fix the algorithm to produce the right numbers, not hardcode them.
    """
    rng = random.Random(seed)

    # ── random pricing scenario ──────────────────────────────────────────────
    n_items = rng.randint(2, 3)
    prices    = [rng.choice([5, 8, 10, 12, 15, 20, 25]) for _ in range(n_items)]
    quantities = [rng.randint(1, 5) for _ in range(n_items)]
    items = [{"price": float(p), "quantity": q} for p, q in zip(prices, quantities)]

    discount_pct = rng.choice([10, 15, 20, 25])
    tax_rate     = rng.choice([0.05, 0.08, 0.10, 0.15])

    # Correct expected values (LLM must produce these after fixing the code)
    subtotal   = sum(item["price"] * item["quantity"] for item in items)
    discounted = subtotal * (1 - discount_pct / 100)
    total      = round(discounted * (1 + tax_rate), 4)

    # Per-primitive expected values for unit tests
    disc_unit_input  = rng.choice([50.0, 80.0, 100.0, 120.0, 200.0])
    disc_unit_expect = round(disc_unit_input * (1 - discount_pct / 100), 4)

    tax_unit_input   = round(disc_unit_input * (1 - discount_pct / 100), 4)
    tax_unit_expect  = round(tax_unit_input * (1 + tax_rate), 4)

    # ── static buggy sources ─────────────────────────────────────────────────
    cart_src = (
        "from orders.pricing import apply_discount, apply_tax\n"
        "\n"
        "def calculate_total(items: list, discount_pct: float, tax_rate: float) -> float:\n"
        "    \"\"\"Return the final order total after discount and tax.\"\"\"\n"
        "    subtotal = 0.0\n"
        "    for item in items:\n"
        "        # BUG 1: ignores quantity — must multiply price by quantity\n"
        "        subtotal += item['price']\n"
        "    discounted = apply_discount(subtotal, discount_pct)\n"
        "    return apply_tax(discounted, tax_rate)\n"
    )
    pricing_src = (
        "def apply_discount(amount: float, discount_pct: float) -> float:\n"
        "    \"\"\"Return amount after removing discount_pct percent.\"\"\"\n"
        "    # BUG 2: returns the discount portion, not the remaining amount\n"
        "    return amount * (discount_pct / 100)\n"
        "\n"
        "def apply_tax(amount: float, tax_rate: float) -> float:\n"
        "    \"\"\"Return amount with tax_rate (decimal, e.g. 0.10 = 10%%) applied.\"\"\"\n"
        "    # BUG 3: adds tax_rate as raw offset instead of multiplicative factor\n"
        "    # e.g. apply_tax(80, 0.10) returns 80.10 instead of 88.00\n"
        "    return amount + tax_rate\n"
    )

    # ── parametric test file ─────────────────────────────────────────────────
    test_template = """\
from orders.cart import calculate_total
from orders.pricing import apply_discount, apply_tax

# ── primitive unit tests ───────────────────────────────────────────────────

def test_discount_removes_percentage():
    # A {discount_pct}% discount on {disc_unit_input} should leave {disc_unit_expect}
    result = apply_discount({disc_unit_input}, {discount_pct})
    assert abs(result - {disc_unit_expect}) < 0.01, (
        f"Pricing Error: apply_discount({disc_unit_input}, {discount_pct}) "
        f"returned {{result:.4f}}, expected {disc_unit_expect:.4f}. "
        "A {discount_pct}% discount means {keep_pct}% of the amount remains."
    )

def test_tax_is_multiplicative():
    # {tax_pct}% tax on {tax_unit_input} should give {tax_unit_expect}
    result = apply_tax({tax_unit_input}, {tax_rate})
    assert abs(result - {tax_unit_expect}) < 0.01, (
        f"Pricing Error: apply_tax({tax_unit_input}, {tax_rate}) "
        f"returned {{result:.4f}}, expected {tax_unit_expect:.4f}. "
        "tax_rate is a decimal multiplier (e.g. 0.10 means ×1.10), not an additive offset."
    )

# ── integration test ───────────────────────────────────────────────────────

def test_cart_total_with_quantities():
    # Items: {items_repr}
    # Correct subtotal (price × qty each): {subtotal:.2f}
    # After {discount_pct}% discount:      {discounted:.2f}
    # After {tax_pct}% tax:                {total:.4f}
    items = {items}
    result = calculate_total(items, discount_pct={discount_pct}, tax_rate={tax_rate})
    assert abs(result - {total}) < 0.01, (
        f"Cart Error: calculate_total returned {{result:.4f}}, expected {total:.4f}. "
        "Check: (1) quantity is multiplied, (2) discount removes the percentage, "
        "(3) tax is a multiplicative factor."
    )

def test_cart_no_discount_no_tax():
    # Simple sanity check: single item, no discount, no tax
    items = [{{'price': 10.0, 'quantity': 3}}]
    result = calculate_total(items, discount_pct=0.0, tax_rate=0.0)
    assert abs(result - 30.0) < 0.01, (
        f"Cart Error: 3 × $10 with no discount and no tax should be $30.00, got {{result:.4f}}."
    )
"""
    test_src = test_template.format(
        discount_pct=discount_pct,
        keep_pct=100 - discount_pct,
        tax_rate=tax_rate,
        tax_pct=int(tax_rate * 100),
        disc_unit_input=disc_unit_input,
        disc_unit_expect=disc_unit_expect,
        tax_unit_input=tax_unit_input,
        tax_unit_expect=tax_unit_expect,
        items=repr(items),
        items_repr=items,
        subtotal=subtotal,
        discounted=discounted,
        total=total,
    )

    return {
        "orders/cart.py": cart_src,
        "orders/pricing.py": pricing_src,
        "tests/test_orders.py": test_src,
    }


def _make_task_3_repo(seed: int) -> dict:
    """
    Async Payment Processor — 3 bugs across 3 files.
      1. payments/config.py    hardcoded sk_live_ key
      2. payments/processor.py synchronous time.sleep (must be async def + await)
      3. payments/retry.py     range(max_retries + 1) → one extra attempt

    Randomised per episode:
      • max_retries value used in the retry test (2, 3, 4, or 5)
        The LLM cannot guess the expected call count without reading the code.
    """
    rng = random.Random(seed)
    max_retries = rng.choice([2, 3, 4, 5])

    config_src = (
        "import os\n"
        "\n"
        "STRIPE_TEST_KEY = os.getenv('STRIPE_TEST_KEY')\n"
        "# BUG 1: live key must never be hardcoded — load from environment\n"
        "STRIPE_LIVE_KEY = 'sk_live_9876543210qwerty'\n"
    )
    processor_src = (
        "import time\n"
        "\n"
        "# BUG 2: synchronous sleep blocks the entire event loop\n"
        "# Must be declared 'async def' and use 'await asyncio.sleep(...)'\n"
        "def process_payment(user_id: str, amount: float) -> bool:\n"
        "    time.sleep(0)\n"
        "    return True\n"
    )
    retry_src = (
        "import time\n"
        "\n"
        "def retry(fn, max_retries: int = 3, delay: float = 0.0):\n"
        "    \"\"\"\n"
        "    Call fn() up to max_retries times on failure.\n"
        "    Raises the last exception when all attempts are exhausted.\n"
        "    \"\"\"\n"
        "    last_exc = None\n"
        "    # BUG 3: range(max_retries + 1) gives max_retries+1 attempts\n"
        "    for attempt in range(max_retries + 1):\n"
        "        try:\n"
        "            return fn()\n"
        "        except Exception as e:\n"
        "            last_exc = e\n"
        "            if attempt < max_retries:\n"
        "                time.sleep(delay)\n"
        "    raise last_exc\n"
    )

    test_template = """\
import ast
import inspect
import payments.config as cfg_module

# ── security: no hardcoded live key ──────────────────────────────────────

def test_no_hardcoded_live_key():
    src = inspect.getsource(cfg_module)
    assert "sk_live_" not in src, (
        "Security Error: a hardcoded Stripe live key (sk_live_...) is present in "
        "config.py. Remove it and load the value from the environment."
    )

def test_live_key_loaded_from_env():
    src = inspect.getsource(cfg_module)
    assert "os.getenv" in src or "os.environ" in src, (
        "Security Error: STRIPE_LIVE_KEY must be loaded from the environment "
        "(os.getenv or os.environ), not hardcoded or simply deleted."
    )

# ── async: must not block event loop ─────────────────────────────────────

def test_process_payment_is_async():
    from payments.processor import process_payment
    tree = ast.parse(inspect.getsource(process_payment))
    async_defs = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "process_payment"
    ]
    assert async_defs, (
        "Performance Error: process_payment must be 'async def' so it does not "
        "block the event loop."
    )

def test_process_payment_awaits():
    from payments.processor import process_payment
    tree = ast.parse(inspect.getsource(process_payment))
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "process_payment"
    )
    assert any(isinstance(n, ast.Await) for n in ast.walk(fn)), (
        "Performance Error: process_payment must use 'await' (e.g. await asyncio.sleep(...))."
    )

# ── retry: exact attempt count ───────────────────────────────────────────

def test_retry_exact_attempt_count():
    from payments.retry import retry
    calls = []

    def always_fails():
        calls.append(1)
        raise RuntimeError("fail")

    try:
        retry(always_fails, max_retries={max_retries}, delay=0.0)
    except RuntimeError:
        pass

    assert len(calls) == {max_retries}, (
        f"Retry Error: retry(fn, max_retries={max_retries}) must call fn exactly "
        f"{max_retries} times, but called it {{len(calls)}} times."
    )

def test_retry_returns_on_success():
    from payments.retry import retry
    assert retry(lambda: 42, max_retries={max_retries}) == 42, (
        "Retry Error: retry must return the function result when it succeeds."
    )
"""
    test_src = test_template.format(max_retries=max_retries)

    return {
        "payments/config.py": config_src,
        "payments/processor.py": processor_src,
        "payments/retry.py": retry_src,
        "tests/test_payments.py": test_src,
    }


def _make_task_4_repo(seed: int) -> dict:
    """
    LRU Cache + Serialiser — 3 bugs across 2 files.
      1. cache/store.py      get() doesn't call move_to_end() → wrong eviction order
      2. cache/store.py      put() doesn't popitem(last=False) → never evicts
      3. cache/serializer.py deserialize(None) raises TypeError instead of returning None

    Randomised per episode:
      • Cache capacity (2, 3, or 4)
      • Key names (drawn from a pool so access patterns differ)
      The LLM must implement both LRU invariants correctly regardless of capacity.
    """
    rng = random.Random(seed)

    capacity = rng.choice([2, 3, 4])
    key_pool  = ["alpha", "beta", "gamma", "delta", "epsilon"]
    keys = rng.sample(key_pool, capacity + 1)   # capacity + 1 keys → triggers eviction
    k = keys  # shorthand

    store_src = (
        "from collections import OrderedDict\n"
        "\n"
        "class LRUCache:\n"
        "    def __init__(self, capacity: int):\n"
        "        self.capacity = capacity\n"
        "        self.cache: OrderedDict = OrderedDict()\n"
        "\n"
        "    def get(self, key: str):\n"
        "        \"\"\"Return value for key, or None if absent.\"\"\"\n"
        "        if key not in self.cache:\n"
        "            return None\n"
        "        # BUG 1: must call self.cache.move_to_end(key) to mark as recently used\n"
        "        return self.cache[key]\n"
        "\n"
        "    def put(self, key: str, value) -> None:\n"
        "        \"\"\"Insert/update key; evict the LRU entry when over capacity.\"\"\"\n"
        "        if key in self.cache:\n"
        "            del self.cache[key]\n"
        "        elif len(self.cache) >= self.capacity:\n"
        "            # BUG 2: must call self.cache.popitem(last=False) to evict oldest\n"
        "            pass\n"
        "        self.cache[key] = value\n"
    )
    serializer_src = (
        "import json\n"
        "\n"
        "def serialize(data) -> str:\n"
        "    return json.dumps(data)\n"
        "\n"
        "def deserialize(raw: str):\n"
        "    \"\"\"Decode a JSON string. Returns None when raw is None.\"\"\"\n"
        "    # BUG 3: json.loads(None) raises TypeError — must guard for None input\n"
        "    return json.loads(raw)\n"
    )

    # Access pattern for the MRU promotion test:
    # Insert k[0]..k[cap-1], access k[0] (makes it MRU), insert k[cap] → should evict k[1]
    mru_evicted = k[1]
    mru_kept    = k[0]
    overflow_key = k[capacity]

    test_template = """\
from cache.store import LRUCache
from cache.serializer import serialize, deserialize

# ── eviction when at capacity ─────────────────────────────────────────────

def test_evicts_lru_on_overflow():
    cache = LRUCache(capacity={capacity})
    # Fill to capacity
    for i, key in enumerate({fill_keys}):
        cache.put(key, i)
    # One more insert must evict the oldest key
    cache.put({overflow_key!r}, 99)
    assert cache.get({first_key!r}) is None, (
        "LRU Error: {first_key!r} was inserted first and must be evicted "
        "when capacity is exceeded, but it is still present."
    )
    assert cache.get({overflow_key!r}) == 99

# ── get() must promote key to MRU ────────────────────────────────────────

def test_get_promotes_to_most_recent():
    cache = LRUCache(capacity={capacity})
    for i, key in enumerate({fill_keys}):
        cache.put(key, i)
    cache.get({mru_kept!r})          # access makes this MRU
    cache.put({overflow_key!r}, 99)   # should now evict {mru_evicted!r}, not {mru_kept!r}
    assert cache.get({mru_evicted!r}) is None, (
        "LRU Error: {mru_evicted!r} should be evicted (LRU after {mru_kept!r} was accessed), "
        "but {mru_kept!r} was evicted instead. get() must call move_to_end()."
    )
    assert cache.get({mru_kept!r}) is not None, (
        "LRU Error: {mru_kept!r} was accessed most recently and must NOT be evicted."
    )

def test_update_promotes_key():
    cache = LRUCache(capacity={capacity})
    for i, key in enumerate({fill_keys}):
        cache.put(key, i)
    cache.put({first_key!r}, 999)     # update → becomes MRU
    cache.put({overflow_key!r}, 88)   # should evict {second_key!r}
    assert cache.get({second_key!r}) is None, (
        "LRU Error: after updating {first_key!r} (making it MRU), "
        "{second_key!r} should be the LRU and get evicted."
    )
    assert cache.get({first_key!r}) == 999

# ── serialiser None guard ─────────────────────────────────────────────────

def test_deserialize_none_returns_none():
    assert deserialize(None) is None, (
        "Serialiser Error: deserialize(None) must return None, not raise TypeError."
    )

def test_serialize_roundtrip():
    for val in [42, "hello", [1, 2], {{"k": "v"}}, True, None]:
        assert deserialize(serialize(val)) == val, (
            f"Serialiser Error: round-trip failed for {{val!r}}."
        )
"""
    fill_keys = k[:capacity]
    test_src = test_template.format(
        capacity=capacity,
        fill_keys=repr(fill_keys),
        first_key=fill_keys[0],
        second_key=fill_keys[1],
        mru_kept=mru_kept,
        mru_evicted=mru_evicted,
        overflow_key=overflow_key,
    )

    return {
        "cache/store.py": store_src,
        "cache/serializer.py": serializer_src,
        "tests/test_cache.py": test_src,
    }


def _make_task_5_repo(seed: int) -> dict:
    """
    Analytics Pipeline — 3 algorithmic bugs across 3 files.

      1. analytics/aggregator.py  EMA weights are SWAPPED:
           buggy:   ema = alpha * prev + (1-alpha) * new_value
           correct: ema = alpha * new_value + (1-alpha) * prev

      2. analytics/detector.py  Uses POPULATION std (divides by n) instead of
           SAMPLE std (divides by n-1), causing false positives on small datasets.

      3. analytics/window.py  Off-by-one: range(n - size) drops the last window;
           must be range(n - size + 1).

    Randomised per episode:
      • EMA alpha (0.2, 0.3, or 0.4) and a 4-value sequence.
        Expected EMA computed from the CORRECT formula — the LLM must fix the
        formula to produce the right answer, cannot hardcode it.

      • Base value for the false-positive anomaly test — proves sample std is needed.
        Mathematically: [base]*4 + [base*2] always gives z ≈ 1.789 with sample std
        (< threshold 2.0, NOT anomaly) but z = 2.0 with population std (false positive).

      • Window size and data length — the LLM must fix the range boundary to get
        the right window count, which changes every episode.
    """
    rng = random.Random(seed)

    # ── EMA parameters ───────────────────────────────────────────────────────
    alpha = rng.choice([0.2, 0.3, 0.4])
    seq   = [float(rng.randint(5, 50)) for _ in range(4)]

    # Compute correct EMA for the full sequence
    ema_val = seq[0]
    for v in seq[1:]:
        ema_val = alpha * v + (1.0 - alpha) * ema_val
    ema_expected = round(ema_val, 9)

    # Compute BUGGY EMA to confirm the test would fail with the bug
    ema_buggy = seq[0]
    for v in seq[1:]:
        ema_buggy = alpha * ema_buggy + (1.0 - alpha) * v
    # (used only for documentation; not embedded in tests)

    # Two-value check: prev=seq[-2] result, new=seq[-1]
    # After all but last update, correct ema_prev was:
    ema_prev = seq[0]
    for v in seq[1:-1]:
        ema_prev = alpha * v + (1.0 - alpha) * ema_prev

    # ── Anomaly detection parameters ─────────────────────────────────────────
    # [base]*4 + [base*2]: z_outlier ≈ 1.789 with sample std (NOT anomaly at 2.0)
    #                      z_outlier = 2.0   with pop std    (false positive)
    fp_base    = rng.randint(3, 15)
    fp_outlier = fp_base * 2
    fp_values  = [fp_base] * 4 + [fp_outlier]

    # Clear outlier: [mean_val]*8 + [outlier]; z_outlier = 8/3 ≈ 2.667 with sample std
    clear_base    = rng.randint(5, 20)
    clear_outlier = clear_base + rng.randint(20, 60)
    clear_values  = [clear_base] * 8 + [clear_outlier]

    # ── Window parameters ────────────────────────────────────────────────────
    window_size    = rng.choice([2, 3, 4])
    extra          = rng.randint(2, 4)
    data_len       = window_size + extra
    window_data    = list(range(1, data_len + 1))
    expected_count = data_len - window_size + 1
    expected_last  = window_data[data_len - window_size:]

    # ── Static buggy sources ─────────────────────────────────────────────────
    aggregator_src = (
        "class ExponentialMovingAverage:\n"
        "    \"\"\"\n"
        "    Exponential Moving Average.\n"
        "    Correct formula: ema_t = alpha * x_t + (1 - alpha) * ema_{t-1}\n"
        "    alpha in (0, 1] — higher alpha gives more weight to recent values.\n"
        "    \"\"\"\n"
        "    def __init__(self, alpha: float):\n"
        "        if not (0 < alpha <= 1):\n"
        "            raise ValueError('alpha must be in (0, 1]')\n"
        "        self.alpha = alpha\n"
        "        self._value = None\n"
        "\n"
        "    def update(self, new_value: float) -> None:\n"
        "        if self._value is None:\n"
        "            self._value = float(new_value)\n"
        "        else:\n"
        "            # BUG 1: alpha and (1-alpha) are applied to the WRONG terms.\n"
        "            # Should be: alpha * new_value + (1 - alpha) * self._value\n"
        "            self._value = self.alpha * self._value + (1 - self.alpha) * new_value\n"
        "\n"
        "    @property\n"
        "    def value(self) -> float:\n"
        "        if self._value is None:\n"
        "            raise RuntimeError('No data: call update() first')\n"
        "        return self._value\n"
    )

    detector_src = (
        "import math\n"
        "\n"
        "def _mean(values: list) -> float:\n"
        "    return sum(values) / len(values)\n"
        "\n"
        "def _std(values: list) -> float:\n"
        "    \"\"\"Standard deviation for z-score normalisation.\"\"\"\n"
        "    if len(values) < 2:\n"
        "        return 0.0\n"
        "    m = _mean(values)\n"
        "    # BUG 2: divides by len(values) — population std (biased, ddof=0).\n"
        "    # Must divide by len(values) - 1 — sample std (unbiased, ddof=1).\n"
        "    variance = sum((x - m) ** 2 for x in values) / len(values)\n"
        "    return math.sqrt(variance)\n"
        "\n"
        "def detect_anomalies(values: list, threshold: float = 2.0) -> list:\n"
        "    \"\"\"Return bool list; True where |z-score| >= threshold.\"\"\"\n"
        "    if len(values) < 2:\n"
        "        return [False] * len(values)\n"
        "    m = _mean(values)\n"
        "    s = _std(values)\n"
        "    if s == 0:\n"
        "        return [False] * len(values)\n"
        "    return [abs((x - m) / s) >= threshold for x in values]\n"
    )

    window_src = (
        "from typing import List\n"
        "\n"
        "def sliding_window(data: list, size: int) -> List[list]:\n"
        "    \"\"\"\n"
        "    Return all contiguous sub-lists of length 'size'.\n"
        "    E.g. sliding_window([1,2,3,4,5], 3) -> [[1,2,3],[2,3,4],[3,4,5]]\n"
        "    \"\"\"\n"
        "    if size <= 0 or size > len(data):\n"
        "        return []\n"
        "    # BUG 3: range(n - size) is one step short — drops the last window.\n"
        "    # Must be range(len(data) - size + 1).\n"
        "    return [data[i:i + size] for i in range(len(data) - size)]\n"
    )

    # ── Pre-computed values for test template ────────────────────────────────
    one_minus_alpha_r9   = round(1 - alpha, 9)
    seq_last             = seq[-1]
    seq_second_last      = seq[-2]
    seq_prefix_repr      = repr(seq[:-1])
    ema_prev_r6          = round(ema_prev, 6)
    two_step_expected_r6 = round(alpha * seq_last + (1 - alpha) * ema_prev, 6)
    steps_comment        = "\n".join(
        f"#   ema_{i+1} = {alpha}*{v} + {one_minus_alpha_r9}*prev"
        for i, v in enumerate(seq[1:])
    )

    # ── Parametric test file ─────────────────────────────────────────────────
    test_template = """\
from analytics.aggregator import ExponentialMovingAverage
from analytics.detector import detect_anomalies
from analytics.window import sliding_window

# ── EMA: correct alpha weighting ─────────────────────────────────────────
# alpha={alpha}, sequence={seq}
# Correct step-by-step:
#   ema_0 = {seq0}
{steps_comment}
# Final expected value: {ema_expected}

def test_ema_first_update_is_identity():
    ema = ExponentialMovingAverage(alpha={alpha})
    ema.update({seq0})
    assert abs(ema.value - {seq0}) < 1e-9, (
        f"EMA Error: first update({seq0}) should set value to {seq0}, got {{ema.value}}."
    )

def test_ema_full_sequence():
    ema = ExponentialMovingAverage(alpha={alpha})
    for v in {seq}:
        ema.update(v)
    assert abs(ema.value - {ema_expected}) < 1e-6, (
        f"EMA Error: with alpha={alpha} and sequence {seq}, "
        f"expected {ema_expected:.6f} but got {{ema.value:.6f}}. "
        "Check the formula: ema = alpha * new_value + (1 - alpha) * previous_ema."
    )

def test_ema_two_step():
    # Isolated two-step check: prev={seq_second_last}, new={seq_last}, alpha={alpha}
    # Correct: {alpha}*{seq_last} + {one_minus_alpha_r9}*{ema_prev_r6} = {two_step_expected_r6}
    ema = ExponentialMovingAverage(alpha={alpha})
    for v in {seq_prefix_repr}:
        ema.update(v)
    prev = ema.value
    ema.update({seq_last})
    expected = {alpha} * {seq_last} + {one_minus_alpha_r9} * prev
    assert abs(ema.value - expected) < 1e-9, (
        f"EMA Error: update({seq_last}) with prev={{prev:.4f}} and alpha={alpha} "
        f"should give {{expected:.6f}}, got {{ema.value:.6f}}."
    )

# ── Anomaly detection: sample std (ddof=1) ───────────────────────────────
# Dataset: {fp_values}
# With population std (buggy): z({fp_outlier}) = 2.0  -> flagged (FALSE POSITIVE)
# With sample    std (correct): z({fp_outlier}) ~= 1.789 -> not flagged (correct)

def test_no_false_positive_with_sample_std():
    values = {fp_values}
    result = detect_anomalies(values, threshold=2.0)
    assert not result[-1], (
        "Detector Error: {fp_outlier} in {fp_values} is NOT an anomaly at threshold=2.0 "
        "when using sample std (ddof=1). Population std gives a false positive here. "
        "Fix _std() to divide by (n-1) instead of n."
    )

def test_detects_clear_outlier():
    # [{clear_base}]*8 + [{clear_outlier}]: z_outlier ~= 2.667 with sample std (> 2.0)
    values = {clear_values}
    result = detect_anomalies(values, threshold=2.0)
    assert result[-1], (
        "Detector Error: {clear_outlier} in a baseline of {clear_base}s is a "
        "clear outlier and must be detected."
    )
    assert not any(result[:-1]), (
        "Detector Error: only the last value should be flagged -- the baseline values "
        "must not be false-positives."
    )

# ── Sliding window: correct boundary ─────────────────────────────────────
# data={window_data}, size={window_size} -> expect {expected_count} windows

def test_sliding_window_count():
    windows = sliding_window({window_data}, size={window_size})
    assert len(windows) == {expected_count}, (
        f"Window Error: sliding_window over {data_len} elements with size={window_size} "
        f"should produce {expected_count} windows, got {{len(windows)}}. "
        "Fix the range: range(n - size + 1), not range(n - size)."
    )

def test_sliding_window_last_is_correct():
    windows = sliding_window({window_data}, size={window_size})
    assert windows[-1] == {expected_last}, (
        f"Window Error: last window should be {expected_last}, got {{windows[-1] if windows else 'none'}}."
    )

def test_sliding_window_full_span():
    data = list(range(1, {window_size} + 1))
    windows = sliding_window(data, size={window_size})
    assert windows == [data], (
        "Window Error: size==len(data) must produce exactly one window equal to the full list."
    )
"""
    test_src = test_template.format(
        alpha=alpha,
        seq=repr(seq),
        seq0=seq[0],
        seq_prefix_repr=seq_prefix_repr,
        seq_last=seq_last,
        seq_second_last=seq_second_last,
        one_minus_alpha_r9=one_minus_alpha_r9,
        ema_expected=ema_expected,
        ema_prev_r6=ema_prev_r6,
        two_step_expected_r6=two_step_expected_r6,
        steps_comment=steps_comment,
        fp_values=repr(fp_values),
        fp_base=fp_base,
        fp_outlier=fp_outlier,
        clear_base=clear_base,
        clear_outlier=clear_outlier,
        clear_values=repr(clear_values),
        window_data=repr(window_data),
        window_size=window_size,
        expected_count=expected_count,
        expected_last=repr(expected_last),
        data_len=data_len,
    )

    return {
        "analytics/aggregator.py": aggregator_src,
        "analytics/detector.py": detector_src,
        "analytics/window.py": window_src,
        "tests/test_analytics.py": test_src,
    }


def _make_task_6_repo(seed: int) -> dict:
    """
    Document Search + Ranking — 3 bugs with a coupled scoring/ranking trap.

    Files:
      search/indexer.py  — Bug 1: splits on whitespace only (punctuation sticks to tokens)
      search/scorer.py   — Bug 2: divides by len(doc_terms) instead of len(query_terms)
      search/ranker.py   — Bug 3: sorts ascending instead of descending

    THE TRAP — why this is hard:
      test_rank_best_match_first PASSES with BOTH Bug 2 and Bug 3 present because
      the verbose document (all query terms + many extras) gets a LOW buggy score
      (few matches / many total terms) while the short imprecise document gets a
      HIGH buggy score (partial matches / few total terms).  Ascending sort then
      accidentally puts the verbose doc first — the correct answer.

      Fix Bug 2 alone → scores are now correct (verbose=1.0 > short<1.0) but the
      ascending sort (Bug 3) promotes the short doc → ranking test FAILS.

      Fix Bug 3 alone → descending sort with still-wrong scores promotes the short
      doc (higher buggy score) → ranking test FAILS.

      Both must be fixed simultaneously.

    Parametric per episode:
      • Words drawn from a 16-word pool, shuffled by seed
      • Query size: 2 or 3 terms
      • Extra term count in verbose doc: 3–6
    """
    rng = random.Random(seed)

    WORD_POOL = [
        "alpha", "beta", "gamma", "delta", "epsilon", "zeta",
        "eta",   "theta","iota",  "kappa", "lambda",  "mu",
        "nu",    "xi",   "omicron","pi",
    ]
    pool = WORD_POOL[:]
    rng.shuffle(pool)

    # ── Parameters ────────────────────────────────────────────────────────────
    q_size      = rng.choice([2, 3])
    extra_count = rng.randint(3, 6)

    query_terms    = pool[:q_size]
    extra_terms    = pool[q_size : q_size + extra_count]
    # verbose doc: all query terms + many extras  (correct winner; low buggy score)
    verbose_terms  = query_terms + extra_terms         # len = q_size + extra_count
    # short doc: only (q_size-1) query matches + 1 non-query term (wrong loser; high buggy score)
    short_terms    = query_terms[:-1] + [pool[q_size + extra_count]]  # len = q_size

    # Invariant check: buggy_verbose < buggy_short  (ascending accidentally correct)
    buggy_verbose  = q_size / len(verbose_terms)
    buggy_short    = (q_size - 1) / len(short_terms)
    assert buggy_verbose < buggy_short, "Coupling invariant failed for this seed"

    correct_short_num = q_size - 1
    correct_short_str = f"{correct_short_num}/{q_size}"

    # Tokenizer test words (independent of coupling)
    tok_word1 = pool[q_size + extra_count + 1]
    tok_word2 = pool[q_size + extra_count + 2]
    tok_punc  = rng.choice([",", "!", "?"])
    tok_input = f"{tok_word1}{tok_punc} {tok_word2}!"
    tok_expected = [tok_word1, tok_word2]

    # Scorer standalone test: doc with all query terms + 1 extra → must score 1.0
    scorer_extra_word = pool[q_size + extra_count + 3]
    scorer_doc_terms  = query_terms + [scorer_extra_word]

    # ── Pre-computed repr strings (no Python expressions in format fields) ────
    query_repr        = repr(query_terms)
    verbose_repr      = repr(verbose_terms)
    short_repr        = repr(short_terms)
    tok_input_repr    = repr(tok_input)
    tok_expected_repr = repr(tok_expected)
    scorer_doc_repr   = repr(scorer_doc_terms)
    q_size_minus_1    = q_size - 1
    buggy_verbose_r4  = round(buggy_verbose, 4)
    buggy_short_r4    = round(buggy_short, 4)

    # ── Static source files ───────────────────────────────────────────────────
    indexer_src = (
        "import re\n"
        "\n"
        "def tokenize(text: str) -> list:\n"
        "    \"\"\"\n"
        "    Split text into lowercase tokens, stripping all punctuation.\n"
        "    E.g. tokenize('Hello, world!') -> ['hello', 'world']\n"
        "    \"\"\"\n"
        "    # BUG 1: splits on whitespace only -- punctuation sticks to tokens.\n"
        "    # Fix: use re.split(r'\\W+', text.lower()) and filter empty strings.\n"
        "    return [w.lower() for w in text.split() if w]\n"
    )

    scorer_src = (
        "def relevance_score(query_terms: list, doc_terms: list) -> float:\n"
        "    \"\"\"\n"
        "    Fraction of query terms found in the document.  Range [0.0, 1.0].\n"
        "    1.0 means every query term appears at least once in the document.\n"
        "    \"\"\"\n"
        "    if not query_terms:\n"
        "        return 0.0\n"
        "    matches = len(set(query_terms) & set(doc_terms))\n"
        "    # BUG 2: divides by len(doc_terms) -- score unfairly penalises long docs.\n"
        "    # Fix: divide by len(query_terms) so a full match always returns 1.0.\n"
        "    return matches / len(doc_terms)\n"
    )

    ranker_src = (
        "from search.scorer import relevance_score\n"
        "\n"
        "def rank_documents(query_terms: list, documents: list) -> list:\n"
        "    \"\"\"\n"
        "    Return documents sorted by relevance to query_terms, highest first.\n"
        "    Each document is a dict with at least a 'terms' key (list[str]).\n"
        "    \"\"\"\n"
        "    scored = [\n"
        "        (doc, relevance_score(query_terms, doc['terms']))\n"
        "        for doc in documents\n"
        "    ]\n"
        "    # BUG 3: sorted ascending (lowest score first) -- must be descending.\n"
        "    # Fix: add reverse=True to sorted().\n"
        "    return [doc for doc, _ in sorted(scored, key=lambda x: x[1])]\n"
    )

    # ── Parametric test file ──────────────────────────────────────────────────
    test_template = """\
from search.indexer import tokenize
from search.scorer import relevance_score
from search.ranker import rank_documents

# ── Tokenizer ────────────────────────────────────────────────────────────────

def test_tokenize_strips_punctuation():
    result = tokenize({tok_input_repr})
    assert result == {tok_expected_repr}, (
        "Indexer Error: tokenize() must strip punctuation. "
        "Use re.split(r'\\\\W+', ...) not str.split(). Got: " + repr(result)
    )

def test_tokenize_lowercases():
    assert tokenize("Alpha BETA") == ["alpha", "beta"], (
        "Indexer Error: tokenize() must lowercase all tokens."
    )

# ── Scorer ───────────────────────────────────────────────────────────────────

def test_full_query_match_scores_one():
    # doc has all {q_size} query terms plus 1 extra; every query term is present
    score = relevance_score({query_repr}, {scorer_doc_repr})
    assert abs(score - 1.0) < 1e-9, (
        f"Scorer Error: all query terms present -> score must be 1.0, got {{score:.4f}}. "
        "Fix: divide by len(query_terms), not len(doc_terms)."
    )

def test_empty_query_scores_zero():
    assert relevance_score([], ["any", "word"]) == 0.0, (
        "Scorer Error: empty query must score 0.0."
    )

def test_no_match_scores_zero():
    assert relevance_score(["missing"], ["other", "words"]) == 0.0, (
        "Scorer Error: no matching terms must score 0.0."
    )

# ── Ranker (coupled trap) ────────────────────────────────────────────────────
# verbose_doc = {verbose_repr}
#   all {q_size} query terms + {extra_count} extras -> correct score 1.0
#   buggy score (divide by doc length): {buggy_verbose_r4}
#
# short_doc = {short_repr}
#   only {q_size_minus_1} of {q_size} query terms -> correct score {correct_short_str}
#   buggy score (divide by doc length): {buggy_short_r4}
#
# TRAP: buggy_verbose ({buggy_verbose_r4}) < buggy_short ({buggy_short_r4})
#   => ascending sort accidentally puts verbose_doc first (correct answer).
#   Fix scorer alone: verbose=1.0 > short, but ascending puts short first -> FAIL.
#   Fix ranker alone: descending, but buggy scores put short first -> FAIL.
#   Must fix BOTH scorer and ranker together.

def test_rank_best_match_first():
    query       = {query_repr}
    verbose_doc = {{"id": "verbose", "terms": {verbose_repr}}}
    short_doc   = {{"id": "short",   "terms": {short_repr}}}
    results = rank_documents(query, [verbose_doc, short_doc])
    assert results[0]["id"] == "verbose", (
        f"Ranker Error: verbose_doc matches all {{len(query)}} query terms (score 1.0) "
        f"and must rank first. Got order: {{[d['id'] for d in results]}}. "
        "Hint: fix BOTH scorer (divide by len(query_terms)) "
        "AND ranker (reverse=True) -- one fix without the other regresses this test."
    )

def test_rank_preserves_count():
    query       = {query_repr}
    verbose_doc = {{"id": "verbose", "terms": {verbose_repr}}}
    short_doc   = {{"id": "short",   "terms": {short_repr}}}
    results = rank_documents(query, [verbose_doc, short_doc])
    assert len(results) == 2, (
        f"Ranker Error: rank_documents must return all documents. Got {{len(results)}}."
    )
"""
    test_src = test_template.format(
        tok_input_repr=tok_input_repr,
        tok_expected_repr=tok_expected_repr,
        q_size=q_size,
        q_size_minus_1=q_size_minus_1,
        extra_count=extra_count,
        query_repr=query_repr,
        verbose_repr=verbose_repr,
        short_repr=short_repr,
        scorer_doc_repr=scorer_doc_repr,
        buggy_verbose_r4=buggy_verbose_r4,
        buggy_short_r4=buggy_short_r4,
        correct_short_str=correct_short_str,
    )

    return {
        "search/__init__.py": "",
        "search/indexer.py":  indexer_src,
        "search/scorer.py":   scorer_src,
        "search/ranker.py":   ranker_src,
        "tests/test_search.py": test_src,
    }


def _make_task_9_repo(seed: int) -> dict:
    """
    Memory Leak + OOM Kill — 3 bugs in app/cache.py and app/processor.py.

      1. Cache.__init__: plain list instead of bounded container
         → cache grows without bound, no eviction on add()
      2. Cache.add: no maxsize check — oldest entries never evicted
         Fix: enforce len <= CACHE_MAXSIZE, evict oldest before appending
      3. processor.py: module-level _processed_log list retains every
         processed event forever — memory leak on long-running service
         Fix: remove or bound the module-level accumulator

    After fixing the leaks, the agent must call restart_service.
    app_init.py simulates a load burst and checks that cache is bounded
    and the processor has no unbounded accumulator.
    test_service_state_running ONLY passes after restart_service with
    fixed code — it reads service_state.json for status="running".

    Randomised per episode:
      • CACHE_MAXSIZE (20, 25, 30, 35, or 40)
    """
    rng = random.Random(seed)
    maxsize = rng.choice([20, 25, 30, 35, 40])

    cache_src = (
        '"""In-memory event cache with LRU eviction.\n\n'
        'CACHE_MAXSIZE controls the maximum number of entries.\n'
        'When full, the oldest entry must be evicted before adding the new one.\n'
        '"""\n'
        "from collections import deque\n"
        "\n"
        f"CACHE_MAXSIZE = {maxsize}  # Maximum entries; enforce in add()\n"
        "\n"
        "class Cache:\n"
        '    """Bounded event cache. Must never exceed CACHE_MAXSIZE entries."""\n'
        "\n"
        "    def __init__(self):\n"
        "        # BUG 1: plain list instead of bounded container.\n"
        "        # Fix: self._store = deque(maxlen=CACHE_MAXSIZE)\n"
        "        self._store = []\n"
        "\n"
        "    def add(self, key: str, value) -> None:\n"
        '        """Add or update an entry. Must evict oldest when at capacity."""\n'
        "        # BUG 2: no eviction — appends forever, memory grows without bound.\n"
        "        # Fix: while len(self._store) >= CACHE_MAXSIZE: self._store.pop(0)\n"
        "        self._store.append((key, value))\n"
        "\n"
        "    def get(self, key: str):\n"
        '        """Return most recent value for key, or None if not present."""\n'
        "        for k, v in reversed(self._store):\n"
        "            if k == key:\n"
        "                return v\n"
        "        return None\n"
        "\n"
        "    def __len__(self) -> int:\n"
        "        return len(self._store)\n"
        "\n"
        "    def clear(self) -> None:\n"
        "        self._store.clear()\n"
    )

    processor_src = (
        '"""Event processor — transforms raw events into structured records."""\n'
        "\n"
        "# BUG 3: module-level list accumulates every processed event forever.\n"
        "# On a long-running service this causes an unbounded memory leak.\n"
        "# Fix: remove _processed_log entirely (return count from a local var),\n"
        "#      or use a ring-buffer: deque(maxlen=N).\n"
        "_processed_log = []\n"
        "\n"
        "def process_event(event: dict) -> dict:\n"
        '    """Process a raw event dict and return a structured record."""\n'
        "    record = {\n"
        '        "id":   event.get("id", "unknown"),\n'
        '        "type": event.get("type", "generic"),\n'
        '        "size": len(str(event)),\n'
        "    }\n"
        "    _processed_log.append(record)  # BUG 3: never cleared\n"
        "    return record\n"
        "\n"
        "def processed_count() -> int:\n"
        '    """Return the number of events in the accumulator."""\n'
        "    return len(_processed_log)\n"
    )

    app_init_src = (
        '#!/usr/bin/env python3\n'
        '"""Service initializer — checks cache and processor independently.\n\n'
        "Produces one of four distinct outcomes written to service_state.json:\n"
        "  running          — both components healthy\n"
        "  degraded         — one component fixed, the other still leaking\n"
        "                     (service_state['component'] names the broken one)\n"
        "  oom_killed       — both components still leaking\n"
        "  crashed          — import or unexpected error\n\n"
        "Use inspect_logs after each restart to read which component is still broken.\n"
        'Run after fixing app/cache.py and/or app/processor.py.\n'
        '"""\n'
        "import importlib, json, os, sys, datetime\n"
        "\n"
        'STATE_PATH = "service_state.json"\n'
        'LOG_PATH   = "service.log"\n'
        "\n"
        "def _log(level: str, msg: str):\n"
        "    ts = datetime.datetime.utcnow().isoformat() + 'Z'\n"
        "    with open(LOG_PATH, 'a') as f:\n"
        "        f.write(f'{ts} {level} {msg}\\n')\n"
        "\n"
        "def main():\n"
        "    try:\n"
        "        from app.cache import Cache, CACHE_MAXSIZE\n"
        "        import app.processor as proc_mod\n"
        "        importlib.reload(proc_mod)\n"
        "\n"
        "        burst = CACHE_MAXSIZE * 3\n"
        "\n"
        "        # --- check cache independently ---\n"
        "        cache = Cache()\n"
        "        for i in range(burst):\n"
        '            cache.add(f"key-{i}", {"data": "x" * 64})\n'
        "        cache_size = len(cache)\n"
        "        cache_ok = cache_size <= CACHE_MAXSIZE\n"
        "\n"
        "        # --- check processor independently (fresh module state) ---\n"
        "        for i in range(burst):\n"
        '            proc_mod.process_event({"id": i, "type": "load-test"})\n'
        "        pc = proc_mod.processed_count()\n"
        "        processor_ok = pc <= CACHE_MAXSIZE * 2\n"
        "\n"
        "        if cache_ok and processor_ok:\n"
        "            _log('INFO', f'All checks passed: cache={cache_size}/{CACHE_MAXSIZE} processed={pc}')\n"
        "            state = {'status': 'running', 'pid': os.getpid(),\n"
        "                     'cache_size': cache_size, 'processed': pc}\n"
        "            with open(STATE_PATH, 'w') as f:\n"
        "                json.dump(state, f)\n"
        "            print(f'Service started: cache={cache_size}/{CACHE_MAXSIZE} processed={pc}')\n"
        "            return 0\n"
        "\n"
        "        elif cache_ok and not processor_ok:\n"
        "            msg = (f'Partial fix detected: cache is bounded ({cache_size}/{CACHE_MAXSIZE}) '\n"
        "                   f'but processor still leaking ({pc} records after {burst} events). '\n"
        "                   'Fix: remove or bound _processed_log in app/processor.py.')\n"
        "            _log('WARNING', msg)\n"
        "            state = {'status': 'degraded',\n"
        "                     'component': 'processor',\n"
        "                     'cache_ok': True,\n"
        "                     'last_error': f'processor _processed_log still accumulating: {pc} records'}\n"
        "            with open(STATE_PATH, 'w') as f:\n"
        "                json.dump(state, f)\n"
        "            print(msg, file=sys.stderr)\n"
        "            return 1\n"
        "\n"
        "        elif not cache_ok and processor_ok:\n"
        "            msg = (f'Partial fix detected: processor is bounded ({pc} records) '\n"
        "                   f'but cache still leaking ({cache_size} entries, limit {CACHE_MAXSIZE}). '\n"
        "                   'Fix: add eviction logic in cache.add() in app/cache.py.')\n"
        "            _log('WARNING', msg)\n"
        "            state = {'status': 'degraded',\n"
        "                     'component': 'cache',\n"
        "                     'processor_ok': True,\n"
        "                     'last_error': f'cache.add() not evicting: {cache_size} entries'}\n"
        "            with open(STATE_PATH, 'w') as f:\n"
        "                json.dump(state, f)\n"
        "            print(msg, file=sys.stderr)\n"
        "            return 1\n"
        "\n"
        "        else:\n"
        "            msg = (f'Both components still leaking: '\n"
        "                   f'cache={cache_size}/{CACHE_MAXSIZE} processor={pc}/{CACHE_MAXSIZE*2}')\n"
        "            _log('FATAL', f'OOM killed: {msg}')\n"
        "            state = {'status': 'oom_killed',\n"
        "                     'last_error': msg}\n"
        "            with open(STATE_PATH, 'w') as f:\n"
        "                json.dump(state, f)\n"
        "            print(f'OOM killed: {msg}', file=sys.stderr)\n"
        "            return 1\n"
        "\n"
        "    except Exception as e:\n"
        "        _log('ERROR', f'Crash: {e}')\n"
        "        state = {'status': 'crashed', 'last_error': str(e)}\n"
        "        with open(STATE_PATH, 'w') as f:\n"
        "            json.dump(state, f)\n"
        "        print(f'Crash: {e}', file=sys.stderr)\n"
        "        return 1\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    sys.exit(main())\n"
    )

    service_log_src = (
        "2026-01-01T00:00:01Z FATAL OOM killed: Cache grew to 63 items "
        f"(CACHE_MAXSIZE={maxsize}). Memory leak: cache.add() never evicts.\n"
        f"2026-01-01T00:00:01Z FATAL OOM killed: Processor accumulated 63 records "
        f"after 60 events. Memory leak: _processed_log grows without bound.\n"
    )
    service_state_src = (
        '{"status": "oom_killed", '
        '"last_error": "Cache grew without bound — memory leak in add()"}\n'
    )

    test_template = """\
import json
import os
import importlib
import pytest
from app.cache import Cache, CACHE_MAXSIZE


def test_cache_bounded_after_burst():
    \"\"\"Cache must not exceed CACHE_MAXSIZE entries after adding 3x the limit.\"\"\"
    cache = Cache()
    for i in range(CACHE_MAXSIZE * 3):
        cache.add(f"k{{i}}", i)
    assert len(cache) <= CACHE_MAXSIZE, (
        f"Memory Leak: cache grew to {{len(cache)}} entries (limit {{CACHE_MAXSIZE}}). "
        "Fix: add eviction in cache.add() — remove oldest entry when at capacity."
    )


def test_cache_evicts_oldest():
    \"\"\"When cache is full, the oldest entry must be evicted.\"\"\"
    cache = Cache()
    for i in range(CACHE_MAXSIZE + 5):
        cache.add(f"k{{i}}", i)
    assert cache.get("k0") is None, (
        "Memory Leak: oldest cache entry 'k0' still present after exceeding maxsize. "
        "Fix: evict oldest before appending in cache.add()."
    )


def test_cache_latest_entry_retrievable():
    \"\"\"Most recently added entry must still be accessible after the cache fills.\"\"\"
    cache = Cache()
    for i in range(CACHE_MAXSIZE + 5):
        cache.add(f"k{{i}}", i * 10)
    last_key = f"k{{CACHE_MAXSIZE + 4}}"
    assert cache.get(last_key) == (CACHE_MAXSIZE + 4) * 10, (
        "Cache Error: most recent entry not retrievable after cache filled."
    )


def test_processor_does_not_accumulate_unbounded():
    \"\"\"Processor must not retain more than 2*CACHE_MAXSIZE records across a burst.\"\"\"
    import app.processor as proc_mod
    importlib.reload(proc_mod)
    burst = CACHE_MAXSIZE * 3
    for i in range(burst):
        proc_mod.process_event({"id": i, "type": "test"})
    count = proc_mod.processed_count()
    assert count <= CACHE_MAXSIZE * 2, (
        f"Memory Leak: processor retained {{count}} records after {{burst}} events. "
        "Fix: remove or bound the module-level _processed_log list "
        "(e.g. use deque(maxlen=N) or delete it entirely)."
    )


def test_service_state_running():
    \"\"\"service_state.json must show status='running' after restart_service.

    Workflow:
      1. Fix app/cache.py (add eviction logic — Bug 1 + Bug 2).
      2. Fix app/processor.py (remove unbounded _processed_log — Bug 3).
      3. Call restart_service — app_init.py validates memory safety and
         writes service_state.json with status='running'.
      4. This test then passes.
    \"\"\"
    assert os.path.isfile("service_state.json"), (
        "service_state.json not found. Fix memory leaks, then call restart_service "
        "to run app_init.py which validates cache bounds and writes service_state.json."
    )
    with open("service_state.json") as f:
        state = json.load(f)
    status = state.get("status")
    assert status == "running", (
        f"Service not running: status={{status!r}}. "
        "last_error=" + repr(state.get("last_error", "none")) + ". "
        "Use inspect_logs to read service.log. Fix the memory leaks, then call restart_service."
    )
"""

    return {
        "app/__init__.py": "",
        "app/cache.py": cache_src,
        "app/processor.py": processor_src,
        "app_init.py": app_init_src,
        "service.log": service_log_src,
        "service_state.json": service_state_src,
        "tests/__init__.py": "",
        "tests/test_memory.py": test_template,
    }


def _make_task_10_repo(seed: int) -> dict:
    """
    Network Timeout Cascade — 3-service topology incident (gateway -> auth -> db).

    One bug per service:
      1. services/db/config.py: CONNECT_TIMEOUT = 0.001 (two-sided trap: must be in [0.1, 60.0])
      2. services/auth/client.py: range(MAX_RETRIES + 1) — one extra retry attempt
      3. services/gateway/config.py: CIRCUIT_BREAKER_THRESHOLD = 1 (opens on first failure)

    Causal ordering matters:
      • Restarting gateway while db is crashed floods auth — auth degrades further
      • Correct fix order: fix db -> restart db -> fix auth -> restart auth -> fix gateway -> restart gateway
      • Or fix all 3 then restart_service (no service_name) to check in dependency order

    Worsening mechanic:
      restart_service --service gateway while db/auth unhealthy
        => auth.error_rate increases, auth may transition running -> degraded
        => gateway stays degraded, new log entry explains cascade

    Randomised per episode:
      • correct_timeout  (2.0, 3.0, 5.0, or 10.0)
      • failure_threshold (3, 4, or 5)
      • max_retries       (2, 3, or 4)
    """
    import json as _json
    rng = random.Random(seed)
    correct_timeout   = rng.choice([2.0, 3.0, 5.0, 10.0])
    failure_threshold = rng.choice([3, 4, 5])
    max_retries       = rng.choice([2, 3, 4])

    # ── services/db ──────────────────────────────────────────────────────────
    db_init = ""
    db_config_src = (
        '"""Database service configuration."""\n'
        "\n"
        "# BUG 1: CONNECT_TIMEOUT is dangerously low — causes immediate connection\n"
        "# failures that cascade through auth into gateway.\n"
        f"# Fix: set to a value in [0.1, 60.0]  (e.g. {correct_timeout})\n"
        "# TRAP: values > 60.0 also fail the range test — stay within [0.1, 60.0].\n"
        "CONNECT_TIMEOUT = 0.001\n"
        "\n"
        'DB_HOST = "localhost"\n'
        "DB_PORT = 5432\n"
    )
    db_client_src = (
        '"""Database service health check."""\n'
        "from services.db.config import CONNECT_TIMEOUT\n"
        "\n"
        "\n"
        "def check_health() -> dict:\n"
        '    """Verify DB config is valid. Raises on bad CONNECT_TIMEOUT."""\n'
        "    if not isinstance(CONNECT_TIMEOUT, (int, float)):\n"
        "        raise TypeError(\n"
        "            f\"CONNECT_TIMEOUT must be numeric, got {type(CONNECT_TIMEOUT).__name__}\"\n"
        "        )\n"
        "    if not (0.1 <= CONNECT_TIMEOUT <= 60.0):\n"
        "        raise TimeoutError(\n"
        "            f\"CONNECT_TIMEOUT={CONNECT_TIMEOUT} outside valid range [0.1, 60.0]. \"\n"
        "            \"All DB connections will time out immediately.\"\n"
        "        )\n"
        "    return {\n"
        '        "status": "running",\n'
        '        "latency_ms": int(CONNECT_TIMEOUT * 50),\n'
        '        "error_rate": 0.0,\n'
        "    }\n"
    )

    # ── services/auth ────────────────────────────────────────────────────────
    auth_init = ""
    auth_config_src = (
        '"""Auth service configuration."""\n'
        f"MAX_RETRIES = {max_retries}   # number of connection attempts\n"
        "AUTH_TIMEOUT = 5.0\n"
    )
    auth_client_src = (
        '"""Auth service client with retry logic."""\n'
        "from services.auth.config import MAX_RETRIES\n"
        "\n"
        "\n"
        "def connect_with_retry() -> dict:\n"
        '    """Attempt connection; return attempt count."""\n'
        "    attempts = 0\n"
        "    # BUG 2: range(MAX_RETRIES + 1) makes one extra attempt.\n"
        "    # Fix: change to range(MAX_RETRIES)\n"
        "    for _ in range(MAX_RETRIES + 1):\n"
        "        attempts += 1\n"
        '    return {"attempts": attempts, "expected": MAX_RETRIES}\n'
        "\n"
        "\n"
        "def check_health() -> dict:\n"
        '    """Check auth health. Status depends on retry count correctness."""\n'
        "    result = connect_with_retry()\n"
        "    ok = result[\"attempts\"] == result[\"expected\"]\n"
        "    return {\n"
        '        "status": "running" if ok else "degraded",\n'
        '        "latency_ms": 120 if ok else None,\n'
        '        "error_rate": 0.0 if ok else 0.7,\n'
        "    }\n"
    )

    # ── services/gateway ─────────────────────────────────────────────────────
    gw_init = ""
    gw_config_src = (
        '"""Gateway service configuration."""\n'
        "\n"
        "# BUG 3: CIRCUIT_BREAKER_THRESHOLD = 1 causes the circuit to open on\n"
        "# the very first failure, preventing any recovery after a transient error.\n"
        f"# Fix: set to {failure_threshold}  (the correct threshold for this episode)\n"
        "CIRCUIT_BREAKER_THRESHOLD = 1\n"
        "\n"
        "GATEWAY_TIMEOUT = 10.0\n"
    )
    gw_cb_src = (
        '"""Circuit breaker pattern for cascading-failure protection."""\n'
        "from services.gateway.config import CIRCUIT_BREAKER_THRESHOLD\n"
        "\n"
        "\n"
        "class CircuitBreaker:\n"
        '    """Opens after CIRCUIT_BREAKER_THRESHOLD consecutive failures."""\n'
        "\n"
        "    def __init__(self):\n"
        "        self.failure_threshold = CIRCUIT_BREAKER_THRESHOLD\n"
        "        self._failure_count    = 0\n"
        "        self._state            = 'closed'  # closed | open\n"
        "\n"
        "    def call(self, func, *args, **kwargs):\n"
        '        """Execute func through the circuit breaker."""\n'
        "        if self._state == 'open':\n"
        "            raise RuntimeError('Circuit breaker is OPEN — failing fast')\n"
        "        try:\n"
        "            result = func(*args, **kwargs)\n"
        "            self._failure_count = 0\n"
        "            return result\n"
        "        except Exception:\n"
        "            self._failure_count += 1\n"
        "            if self._failure_count >= self.failure_threshold:\n"
        "                self._state = 'open'\n"
        "            raise\n"
        "\n"
        "    def check_health(self) -> dict:\n"
        "        return {\n"
        '            "status": "running" if self._state == "closed" else "degraded",\n'
        '            "circuit_state": self._state,\n'
        '            "failure_count": self._failure_count,\n'
        '            "threshold": self.failure_threshold,\n'
        '            "error_rate": 0.0 if self._state == "closed" else 0.94,\n'
        "        }\n"
    )

    # ── app_init.py ──────────────────────────────────────────────────────────
    app_init_src = (
        '#!/usr/bin/env python3\n'
        '"""Topology-aware service initializer for 3-service incident.\n\n'
        "Service dependency chain:  gateway --> auth --> db\n\n"
        "Usage:\n"
        "  python app_init.py                     # restart all in dependency order\n"
        "  python app_init.py --service db        # restart db only\n"
        "  python app_init.py --service auth      # restart auth only\n"
        "  python app_init.py --service gateway   # restart gateway only\n\n"
        "Worsening mechanic:\n"
        "  Restarting gateway while db is not healthy floods auth with failed\n"
        "  requests — auth.error_rate rises and may transition to degraded.\n"
        "  Always fix root-cause services first: db -> auth -> gateway.\n\n"
        "Writes service_state.json with per-service topology.\n"
        'Per-service logs: services/{name}/service.log\n'
        '"""\n'
        "import argparse\n"
        "import datetime\n"
        "import importlib\n"
        "import json\n"
        "import os\n"
        "import sys\n"
        "\n"
        'STATE_PATH  = "service_state.json"\n'
        'MASTER_LOG  = "service.log"\n'
        'SERVICES    = ["db", "auth", "gateway"]\n'
        "\n"
        "\n"
        "def _ts():\n"
        "    return datetime.datetime.utcnow().isoformat() + 'Z'\n"
        "\n"
        "\n"
        "def _log(service, level, msg):\n"
        "    entry = f'{_ts()} {level} [{service}] {msg}\\n'\n"
        "    with open(MASTER_LOG, 'a') as f:\n"
        "        f.write(entry)\n"
        "    svc_log = os.path.join('services', service, 'service.log')\n"
        "    os.makedirs(os.path.dirname(svc_log), exist_ok=True)\n"
        "    with open(svc_log, 'a') as f:\n"
        "        f.write(entry)\n"
        "\n"
        "\n"
        "def _load_topology():\n"
        "    if os.path.isfile(STATE_PATH):\n"
        "        try:\n"
        "            with open(STATE_PATH) as f:\n"
        "                return json.load(f)\n"
        "        except Exception:\n"
        "            pass\n"
        "    return {\n"
        "        'services': {\n"
        "            'db':      {'status': 'crashed',  'latency_ms': None, 'depends_on': [],       'error_rate': 1.0,  'restart_count': 0},\n"
        "            'auth':    {'status': 'degraded', 'latency_ms': None, 'depends_on': ['db'],   'error_rate': 0.7,  'restart_count': 0},\n"
        "            'gateway': {'status': 'degraded', 'latency_ms': 9999, 'depends_on': ['auth'], 'error_rate': 0.94, 'restart_count': 0},\n"
        "        }\n"
        "    }\n"
        "\n"
        "\n"
        "def _write_topology(topo):\n"
        "    with open(STATE_PATH, 'w') as f:\n"
        "        json.dump(topo, f, indent=2)\n"
        "\n"
        "\n"
        "def _restart_db(topo):\n"
        "    svc = dict(topo['services']['db'])\n"
        "    svc['restart_count'] = svc.get('restart_count', 0) + 1\n"
        "    svc['depends_on'] = []\n"
        "    try:\n"
        "        import services.db.client as mod\n"
        "        importlib.reload(mod)\n"
        "        result = mod.check_health()\n"
        "        svc.update(result)\n"
        "        _log('db', 'INFO', f'Health OK: {result}')\n"
        "    except Exception as e:\n"
        "        svc['status'] = 'crashed'\n"
        "        svc['latency_ms'] = None\n"
        "        svc['error_rate'] = 1.0\n"
        "        _log('db', 'FATAL', f'Health check failed: {e}')\n"
        "    topo['services']['db'] = svc\n"
        "\n"
        "\n"
        "def _restart_auth(topo):\n"
        "    svc = dict(topo['services']['auth'])\n"
        "    svc['restart_count'] = svc.get('restart_count', 0) + 1\n"
        "    svc['depends_on'] = ['db']\n"
        "    db_status = topo['services']['db']['status']\n"
        "    try:\n"
        "        import services.auth.client as mod\n"
        "        importlib.reload(mod)\n"
        "        result = mod.check_health()\n"
        "        if db_status not in ('running', 'healthy'):\n"
        "            svc['status'] = 'degraded'\n"
        "            svc['latency_ms'] = None\n"
        "            svc['error_rate'] = max(result.get('error_rate', 0.5), 0.5)\n"
        "            _log('auth', 'WARNING',\n"
        "                 f'Auth code OK but db is {db_status!r} — auth cannot connect. '\n"
        "                 'Fix db first, then restart auth.')\n"
        "        else:\n"
        "            svc.update(result)\n"
        "            _log('auth', 'INFO', f'Health OK: {result}')\n"
        "    except Exception as e:\n"
        "        svc['status'] = 'degraded'\n"
        "        svc['latency_ms'] = None\n"
        "        svc['error_rate'] = 0.9\n"
        "        _log('auth', 'ERROR', f'Health check failed: {e}')\n"
        "    topo['services']['auth'] = svc\n"
        "\n"
        "\n"
        "def _restart_gateway(topo):\n"
        "    svc = dict(topo['services']['gateway'])\n"
        "    svc['restart_count'] = svc.get('restart_count', 0) + 1\n"
        "    svc['depends_on'] = ['auth']\n"
        "    auth_status = topo['services']['auth']['status']\n"
        "    db_status   = topo['services']['db']['status']\n"
        "    # Worsening mechanic: restarting gateway while db/auth unhealthy\n"
        "    # floods auth with failed requests\n"
        "    if auth_status != 'running' or db_status not in ('running', 'healthy'):\n"
        "        new_err = min(topo['services']['auth'].get('error_rate', 0.5) + 0.3, 0.99)\n"
        "        topo['services']['auth']['error_rate'] = new_err\n"
        "        if topo['services']['auth']['status'] == 'running':\n"
        "            topo['services']['auth']['status'] = 'degraded'\n"
        "        _log('gateway', 'FATAL',\n"
        "             f'CASCADING FAILURE: gateway restarted while db={db_status!r} '\n"
        "             f'auth={auth_status!r}. Auth flooded — error_rate now {new_err:.2f}. '\n"
        "             'Fix order: db first, then auth, then gateway.')\n"
        "        svc['status'] = 'degraded'\n"
        "        svc['latency_ms'] = 9999\n"
        "        svc['error_rate'] = min(svc.get('error_rate', 0.94) + 0.05, 0.99)\n"
        "        topo['services']['gateway'] = svc\n"
        "        return\n"
        "    try:\n"
        "        import services.gateway.circuit_breaker as cb_mod\n"
        "        importlib.reload(cb_mod)\n"
        "        cb = cb_mod.CircuitBreaker()\n"
        "        # Probe: circuit should stay closed after (threshold-1) failures\n"
        "        n_probe = max(cb.failure_threshold - 1, 1)\n"
        "        def _probe_fail():\n"
        "            raise RuntimeError('health-check probe')\n"
        "        for _ in range(n_probe):\n"
        "            try:\n"
        "                cb.call(_probe_fail)\n"
        "            except RuntimeError:\n"
        "                pass\n"
        "        if cb._state == 'closed':\n"
        "            svc['status'] = 'running'\n"
        "            svc['latency_ms'] = 45\n"
        "            svc['error_rate'] = 0.0\n"
        "            svc['circuit_state'] = 'closed'\n"
        "            svc['threshold'] = cb.failure_threshold\n"
        "            _log('gateway', 'INFO',\n"
        "                 f'Circuit breaker healthy: threshold={cb.failure_threshold}')\n"
        "        else:\n"
        "            svc['status'] = 'degraded'\n"
        "            svc['error_rate'] = 0.94\n"
        "            svc['circuit_state'] = 'open'\n"
        "            _log('gateway', 'WARNING',\n"
        "                 f'Circuit opened after {cb._failure_count} failure(s) '\n"
        "                 f'(threshold={cb.failure_threshold}). '\n"
        "                 'Fix: set CIRCUIT_BREAKER_THRESHOLD correctly in services/gateway/config.py')\n"
        "    except Exception as e:\n"
        "        svc['status'] = 'degraded'\n"
        "        svc['error_rate'] = 0.94\n"
        "        _log('gateway', 'ERROR', f'Gateway check failed: {e}')\n"
        "    topo['services']['gateway'] = svc\n"
        "\n"
        "\n"
        "def main():\n"
        "    parser = argparse.ArgumentParser()\n"
        "    parser.add_argument('--service', choices=['db', 'auth', 'gateway'],\n"
        "                        help='Restart a specific service; omit for all')\n"
        "    args = parser.parse_args()\n"
        "    topo = _load_topology()\n"
        "    if args.service:\n"
        "        {'db': _restart_db, 'auth': _restart_auth,\n"
        "         'gateway': _restart_gateway}[args.service](topo)\n"
        "        print(f\"[{args.service}] status={topo['services'][args.service]['status']}\")\n"
        "    else:\n"
        "        for name, fn in [('db', _restart_db), ('auth', _restart_auth),\n"
        "                         ('gateway', _restart_gateway)]:\n"
        "            fn(topo)\n"
        "            print(f'[{name}] status={topo[\"services\"][name][\"status\"]}')\n"
        "    _write_topology(topo)\n"
        "    statuses = [s['status'] for s in topo['services'].values()]\n"
        "    return 0 if all(s in ('running', 'healthy') for s in statuses) else 1\n"
        "\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    sys.exit(main())\n"
    )

    # ── Initial service_state.json (all degraded/crashed) ────────────────────
    initial_topology = _json.dumps({
        "services": {
            "db": {
                "status": "crashed", "latency_ms": None, "depends_on": [],
                "error_rate": 1.0, "restart_count": 0,
                "last_error": f"CONNECT_TIMEOUT=0.001 below minimum 0.1 — all connections timing out",
            },
            "auth": {
                "status": "degraded", "latency_ms": None, "depends_on": ["db"],
                "error_rate": 0.70, "restart_count": 0,
                "last_error": f"db unreachable; retry loop executing {max_retries+1} attempts (should be {max_retries})",
            },
            "gateway": {
                "status": "degraded", "latency_ms": 9999, "depends_on": ["auth"],
                "error_rate": 0.94, "restart_count": 0,
                "last_error": "circuit_breaker CIRCUIT_BREAKER_THRESHOLD=1 opens on first failure",
            },
        }
    }, indent=2)

    # ── Pre-written cascade failure logs ─────────────────────────────────────
    service_log_src = (
        f"2026-01-01T00:00:01Z FATAL [db] CONNECT_TIMEOUT=0.001 below minimum 0.1s — "
        "all DB connections timing out immediately\n"
        f"2026-01-01T00:00:01Z ERROR [auth] Cannot reach db (timeout=0.001s); "
        f"retry loop executed {max_retries+1} attempts instead of {max_retries} — range off-by-one\n"
        "2026-01-01T00:00:01Z ERROR [gateway] circuit_breaker opened after 1 failure "
        "(CIRCUIT_BREAKER_THRESHOLD=1) — threshold too aggressive\n"
        "2026-01-01T00:00:02Z FATAL [gateway] Cascading failure: "
        "db_timeout -> auth_degraded -> gateway_error_rate=94%\n"
    )

    # ── Test suite ────────────────────────────────────────────────────────────
    test_template = (
        '"""Tests for INC-201: 3-service network timeout cascade.\n\n'
        "Dependency chain: gateway --> auth --> db\n\n"
        "Bug 1 (db):      CONNECT_TIMEOUT=0.001 — too low, cascades to all dependents.\n"
        "                 TRAP: > 60.0 also fails. Valid range: [0.1, 60.0].\n"
        f"Bug 2 (auth):    range(MAX_RETRIES + 1) — one extra retry attempt.\n"
        f"Bug 3 (gateway): CIRCUIT_BREAKER_THRESHOLD=1 — opens on first failure.\n"
        '"""\n'
        "import json\n"
        "import os\n"
        "import pytest\n"
        "from services.db.config import CONNECT_TIMEOUT\n"
        "from services.auth.config import MAX_RETRIES\n"
        "from services.auth.client import connect_with_retry\n"
        "from services.gateway.circuit_breaker import CircuitBreaker\n"
        "from services.gateway.config import CIRCUIT_BREAKER_THRESHOLD\n"
        "\n"
        "\n"
        "def test_db_timeout_in_valid_range():\n"
        '    """CONNECT_TIMEOUT must be in [0.1, 60.0]."""\n'
        "    assert isinstance(CONNECT_TIMEOUT, (int, float)), (\n"
        '        "CONNECT_TIMEOUT must be a number."\n'
        "    )\n"
        "    assert 0.1 <= CONNECT_TIMEOUT <= 60.0, (\n"
        "        f\"CONNECT_TIMEOUT={CONNECT_TIMEOUT} is outside [0.1, 60.0]. \"\n"
        f"        \"Too small cascades timeouts; too large masks failures. \"\n"
        f"        \"Fix: set to a value like {correct_timeout} in services/db/config.py\"\n"
        "    )\n"
        "\n"
        "\n"
        "def test_auth_retries_exact_count():\n"
        '    """Auth client must make exactly MAX_RETRIES attempts."""\n'
        "    result = connect_with_retry()\n"
        "    assert result['attempts'] == result['expected'], (\n"
        "        f\"Expected {result['expected']} attempts, got {result['attempts']}. \"\n"
        "        \"Fix: change range(MAX_RETRIES + 1) to range(MAX_RETRIES) \"\n"
        "        \"in services/auth/client.py\"\n"
        "    )\n"
        "\n"
        "\n"
        "def test_circuit_stays_closed_on_isolated_failure():\n"
        '    """A single failure must NOT open the circuit breaker."""\n'
        "    cb = CircuitBreaker()\n"
        "    def _fail():\n"
        "        raise RuntimeError('transient')\n"
        "    try:\n"
        "        cb.call(_fail)\n"
        "    except RuntimeError:\n"
        "        pass\n"
        "    assert cb._state == 'closed', (\n"
        "        f'Circuit opened after 1 failure '\n"
        "        f'(CIRCUIT_BREAKER_THRESHOLD={CIRCUIT_BREAKER_THRESHOLD}). '\n"
        f"        'Fix: set CIRCUIT_BREAKER_THRESHOLD = {failure_threshold} '\n"
        "        'in services/gateway/config.py'\n"
        "    )\n"
        "\n"
        "\n"
        "def test_circuit_opens_after_consecutive_failures():\n"
        f'    """Circuit must open after {failure_threshold} consecutive failures."""\n'
        "    cb = CircuitBreaker()\n"
        "    def _fail():\n"
        "        raise RuntimeError('persistent')\n"
        "    for _ in range(CIRCUIT_BREAKER_THRESHOLD):\n"
        "        try:\n"
        "            cb.call(_fail)\n"
        "        except RuntimeError:\n"
        "            pass\n"
        "    assert cb._state == 'open', (\n"
        "        f'Circuit still {cb._state!r} after {CIRCUIT_BREAKER_THRESHOLD} failures.'\n"
        "    )\n"
        "\n"
        "\n"
        "def test_service_topology_all_running():\n"
        '    """All 3 services must show status=running after targeted restarts.\n\n'
        "    Workflow:\n"
        "      1. Fix services/db/config.py   (CONNECT_TIMEOUT in [0.1, 60.0])\n"
        "      2. Fix services/auth/client.py (range(MAX_RETRIES + 1) -> range(MAX_RETRIES))\n"
        "      3. Fix services/gateway/config.py (CIRCUIT_BREAKER_THRESHOLD = correct value)\n"
        "      4. Call restart_service (optionally with --service flag per dependency order)\n"
        "      5. This test then passes.\n"
        '    """\n'
        "    assert os.path.isfile('service_state.json'), (\n"
        "        \"service_state.json not found. Fix all 3 bugs then call restart_service.\"\n"
        "    )\n"
        "    with open('service_state.json') as f:\n"
        "        state = json.load(f)\n"
        "    assert 'services' in state, \"service_state.json must contain a 'services' topology dict\"\n"
        "    for name in ('db', 'auth', 'gateway'):\n"
        "        svc = state['services'].get(name, {})\n"
        "        status = svc.get('status', 'unknown')\n"
        "        assert status in ('running', 'healthy'), (\n"
        "            f\"Service '{name}' not healthy: status={status!r}. \"\n"
        "            f\"last_error={svc.get('last_error', 'none')!r}. \"\n"
        "            \"Use inspect_logs (with service_name='db'/'auth'/'gateway') to diagnose. \"\n"
        "            \"Fix order: db -> auth -> gateway.\"\n"
        "        )\n"
    )

    return {
        "services/__init__.py":               "",
        "services/db/__init__.py":            db_init,
        "services/db/config.py":              db_config_src,
        "services/db/client.py":              db_client_src,
        "services/auth/__init__.py":          auth_init,
        "services/auth/config.py":            auth_config_src,
        "services/auth/client.py":            auth_client_src,
        "services/gateway/__init__.py":       gw_init,
        "services/gateway/config.py":         gw_config_src,
        "services/gateway/circuit_breaker.py": gw_cb_src,
        "app_init.py":                        app_init_src,
        "service_state.json":                 initial_topology,
        "service.log":                        service_log_src,
        "tests/__init__.py":                  "",
        "tests/test_network.py":              test_template,
    }


def _make_task_7_repo(seed: int) -> dict:
    """
    Service Config Type Errors — 3 bugs in config/settings.py.

      1. TIMEOUT is a string (e.g. "45") instead of int 45
         → causes TypeError: can only concatenate str (not "float") to str
      2. MAX_WORKERS is a string (e.g. "6") instead of int 6
         → causes TypeError in range(MAX_WORKERS) and arithmetic
      3. RETRY_DELAY is a negative number (e.g. -1)
         → semantically invalid: retry storms on failure

    After fixing the config, the agent must call restart_service.
    app_init.py validates config types/values and writes service_state.json.
    test_service_state_running() ONLY passes after restart_service is called
    with a valid config — it reads service_state.json for status="running".

    Randomised per episode:
      • TIMEOUT value (one of 30, 45, 60, 90, 120)
      • MAX_WORKERS value (one of 2, 4, 6, 8)
      • RETRY_DELAY buggy value (one of -1, -2, -3)
    """
    rng = random.Random(seed)

    timeout_val  = rng.choice([30, 45, 60, 90, 120])
    workers_val  = rng.choice([2, 4, 6, 8])
    retry_buggy  = rng.choice([-1, -2, -3])

    # ── Buggy config (all three values have type/value errors) ───────────────
    settings_src = (
        '"""Service configuration. All values must be correctly typed."""\n'
        "\n"
        "# BUG 1: TIMEOUT is a string — causes TypeError in time arithmetic.\n"
        "# Fix: remove the quotes so it is an int literal.\n"
        f'TIMEOUT = "{timeout_val}"\n'
        "\n"
        "# BUG 2: MAX_WORKERS is a string — causes TypeError in range() and pool arithmetic.\n"
        "# Fix: remove the quotes so it is an int literal.\n"
        f'MAX_WORKERS = "{workers_val}"\n'
        "\n"
        "# BUG 3: RETRY_DELAY is negative — causes immediate retry storms on failure.\n"
        "# Fix: set to a non-negative value (e.g. 0.5).\n"
        f"RETRY_DELAY = {retry_buggy}\n"
    )

    # ── app/handler.py — correct code that uses settings (not buggy itself) ──
    handler_src = (
        '"""Request handler — uses config for timeouts and worker limits."""\n'
        "import time\n"
        "from config import settings\n"
        "\n"
        "def handle_request(payload: dict) -> dict:\n"
        '    """Process a request within the configured timeout window."""\n'
        "    deadline = time.time() + settings.TIMEOUT          # TypeError if TIMEOUT is str\n"
        "    slots    = list(range(settings.MAX_WORKERS))       # TypeError if MAX_WORKERS is str\n"
        "    backoff  = settings.RETRY_DELAY                    # must be >= 0\n"
        "    return {\n"
        '        "status": "ok",\n'
        '        "deadline": deadline,\n'
        '        "slots": slots,\n'
        '        "backoff": backoff,\n'
        "    }\n"
    )

    # ── app_init.py — validates config and writes service_state.json ─────────
    app_init_src = (
        '#!/usr/bin/env python3\n'
        '"""Service initializer. Run after fixing config to apply changes.\n'
        "Called by the restart_service action. Validates config types and values,\n"
        'then writes service_state.json with status="running" or "crashed".\n'
        '"""\n'
        "import json, os, sys, time\n"
        "\n"
        "STATE_PATH = \"service_state.json\"\n"
        "\n"
        "def main():\n"
        "    try:\n"
        "        from config import settings\n"
        "        if not isinstance(settings.TIMEOUT, int):\n"
        "            raise TypeError(\n"
        "                f\"TIMEOUT must be int, got {type(settings.TIMEOUT).__name__}: \"\n"
        "                f\"{settings.TIMEOUT!r}\"\n"
        "            )\n"
        "        if not isinstance(settings.MAX_WORKERS, int):\n"
        "            raise TypeError(\n"
        "                f\"MAX_WORKERS must be int, got {type(settings.MAX_WORKERS).__name__}: \"\n"
        "                f\"{settings.MAX_WORKERS!r}\"\n"
        "            )\n"
        "        if not isinstance(settings.RETRY_DELAY, (int, float)):\n"
        "            raise TypeError(\n"
        "                f\"RETRY_DELAY must be numeric, got \"\n"
        "                f\"{type(settings.RETRY_DELAY).__name__}: {settings.RETRY_DELAY!r}\"\n"
        "            )\n"
        "        if settings.TIMEOUT <= 0:\n"
        "            raise ValueError(f\"TIMEOUT must be positive, got {settings.TIMEOUT}\")\n"
        "        if settings.MAX_WORKERS < 1:\n"
        "            raise ValueError(f\"MAX_WORKERS must be >= 1, got {settings.MAX_WORKERS}\")\n"
        "        if settings.RETRY_DELAY < 0:\n"
        "            raise ValueError(\n"
        "                f\"RETRY_DELAY must be non-negative, got {settings.RETRY_DELAY}\"\n"
        "            )\n"
        "        state = {\"status\": \"running\", \"pid\": os.getpid(),\n"
        "                 \"started_at\": time.time()}\n"
        "        with open(STATE_PATH, \"w\") as f:\n"
        "            json.dump(state, f)\n"
        "        print(\n"
        "            f\"Service started: TIMEOUT={settings.TIMEOUT}s, \"\n"
        "            f\"MAX_WORKERS={settings.MAX_WORKERS}, \"\n"
        "            f\"RETRY_DELAY={settings.RETRY_DELAY}s\"\n"
        "        )\n"
        "        return 0\n"
        "    except Exception as e:\n"
        "        state = {\"status\": \"crashed\", \"last_error\": str(e)}\n"
        "        with open(STATE_PATH, \"w\") as f:\n"
        "            json.dump(state, f)\n"
        "        print(f\"Service failed to start: {e}\", file=sys.stderr)\n"
        "        return 1\n"
        "\n"
        "if __name__ == \"__main__\":\n"
        "    sys.exit(main())\n"
    )

    # ── Initial service_state.json (crashed — reflects buggy config) ─────────
    service_state_src = (
        '{"status": "crashed", '
        '"last_error": "TypeError: config values are strings, not ints"}\n'
    )

    # ── Parametric test file ─────────────────────────────────────────────────
    test_src = """\
import json
import os
import pytest
from config import settings
from app.handler import handle_request


def test_timeout_is_int():
    \"\"\"TIMEOUT must be int so time arithmetic doesn't raise TypeError.\"\"\"
    assert isinstance(settings.TIMEOUT, int), (
        "Config Error: TIMEOUT must be int, got "
        + type(settings.TIMEOUT).__name__
        + ": " + repr(settings.TIMEOUT)
        + ". Remove the quotes in config/settings.py."
    )
    assert settings.TIMEOUT > 0, (
        "Config Error: TIMEOUT must be positive, got " + str(settings.TIMEOUT)
    )


def test_max_workers_is_int():
    \"\"\"MAX_WORKERS must be int so range() and pool arithmetic don't raise TypeError.\"\"\"
    assert isinstance(settings.MAX_WORKERS, int), (
        "Config Error: MAX_WORKERS must be int, got "
        + type(settings.MAX_WORKERS).__name__
        + ": " + repr(settings.MAX_WORKERS)
        + ". Remove the quotes in config/settings.py."
    )
    assert 1 <= settings.MAX_WORKERS <= 16, (
        "Config Error: MAX_WORKERS=" + str(settings.MAX_WORKERS)
        + " is outside valid range [1, 16]."
    )


def test_retry_delay_is_nonnegative():
    \"\"\"RETRY_DELAY must be >= 0 to avoid immediate retry storms.\"\"\"
    assert isinstance(settings.RETRY_DELAY, (int, float)), (
        "Config Error: RETRY_DELAY must be numeric, got "
        + type(settings.RETRY_DELAY).__name__
        + ": " + repr(settings.RETRY_DELAY) + "."
    )
    assert settings.RETRY_DELAY >= 0, (
        "Config Error: RETRY_DELAY=" + str(settings.RETRY_DELAY)
        + " is negative. A negative retry delay causes retry storms. Set to >= 0."
    )


def test_handler_processes_request():
    \"\"\"handle_request must not raise TypeError when config is correctly typed.\"\"\"
    try:
        result = handle_request({"user": "alice", "op": "read"})
        assert result["status"] == "ok", "Handler returned unexpected status."
        assert isinstance(result["slots"], list), "Handler slots must be a list."
        assert result["backoff"] >= 0, "Handler backoff must be non-negative."
    except TypeError as e:
        pytest.fail(
            "Handler raised TypeError — likely a string config value used in "
            "arithmetic or range(). Fix config/settings.py. Error: " + str(e)
        )


def test_service_state_running():
    \"\"\"service_state.json must show status='running' after restart_service.

    Workflow:
      1. Fix all 3 bugs in config/settings.py (types + RETRY_DELAY >= 0).
      2. Call restart_service — this runs app_init.py which validates config
         and writes service_state.json with status='running'.
      3. This test then passes.
    \"\"\"
    assert os.path.isfile("service_state.json"), (
        "service_state.json not found. "
        "After fixing config bugs, call restart_service to run app_init.py "
        "which validates config and creates this file."
    )
    with open("service_state.json") as f:
        state = json.load(f)
    status = state.get("status")
    assert status == "running", (
        "Service did not start. Expected status='running', got "
        + repr(status) + ". "
        + "Last error: " + repr(state.get("last_error", "none"))
        + ". Fix all config bugs, then call restart_service again."
    )
"""

    return {
        "config/__init__.py": "",
        "config/settings.py": settings_src,
        "app/__init__.py": "",
        "app/handler.py": handler_src,
        "app_init.py": app_init_src,
        "service_state.json": service_state_src,
        "tests/__init__.py": "",
        "tests/test_service.py": test_src,
    }


def _make_task_8_repo(seed: int) -> dict:
    """
    Database Migration Ordering — 3 bugs in db/migrator.py.

      1. sorted(files) sorts lexicographically: "10_..." < "2_..." alphabetically
         so the index migration runs before the posts table exists → FK/index fails.
         Fix: sort by the leading integer: key=lambda f: int(f.split('_')[0])

      2. Exception handler swallows failures silently with a bare `pass`.
         The agent cannot tell which migration failed without inspect_logs.
         Fix: re-raise the exception (or log + re-raise).

      3. conn.execute(sql) only handles one statement per call.
         10_add_indexes.sql contains two CREATE INDEX statements; only the first
         runs, silently dropping the second.
         Fix: use conn.executescript(sql) for multi-statement SQL files.

    Workflow the agent must follow:
      1. inspect_logs (migration.log shows errors from previous restart attempts)
      2. Fix db/migrator.py (all 3 bugs)
      3. Call restart_service (runs app_init.py → runs migrations → creates app.db)
      4. Tests read app.db and verify tables + indexes exist

    Randomised per episode:
      • Primary user column name (username / email / handle / nickname)
      • Clear text content column name (title / body / message / content)
    """
    rng = random.Random(seed)

    user_col    = rng.choice(["username", "email", "handle", "nickname"])
    content_col = rng.choice(["title", "body", "message", "content"])
    idx2_name   = f"idx_users_{user_col}"

    # ── Migration SQL files ───────────────────────────────────────────────────
    sql_1 = (
        f"CREATE TABLE IF NOT EXISTS users (\n"
        f"    id      INTEGER PRIMARY KEY AUTOINCREMENT,\n"
        f"    {user_col} TEXT NOT NULL UNIQUE\n"
        f");\n"
    )
    sql_2 = (
        f"CREATE TABLE IF NOT EXISTS posts (\n"
        f"    id      INTEGER PRIMARY KEY AUTOINCREMENT,\n"
        f"    user_id INTEGER NOT NULL,\n"
        f"    {content_col} TEXT NOT NULL,\n"
        f"    FOREIGN KEY (user_id) REFERENCES users(id)\n"
        f");\n"
    )
    # Two statements — requires executescript()
    sql_10 = (
        "CREATE INDEX IF NOT EXISTS idx_posts_user_id ON posts(user_id);\n"
        f"CREATE INDEX IF NOT EXISTS {idx2_name} ON users({user_col});\n"
    )

    # ── Buggy migrator ────────────────────────────────────────────────────────
    migrator_src = (
        '"""Database migration runner."""\n'
        "import sqlite3\n"
        "import os\n"
        "import logging\n"
        "\n"
        "LOG_PATH = \"migration.log\"\n"
        "\n"
        "def run_migrations(db_path: str, migration_dir: str) -> None:\n"
        '    """Apply all pending SQL migration files in numeric order."""\n'
        "    logging.basicConfig(\n"
        '        filename=LOG_PATH, level=logging.INFO,\n'
        '        format="%(asctime)s %(levelname)s %(message)s"\n'
        "    )\n"
        "    conn = sqlite3.connect(db_path)\n"
        "    try:\n"
        "        # BUG 1: sorted() uses lexicographic order.\n"
        '        # "10_add_indexes.sql" sorts BEFORE "2_create_posts.sql"\n'
        "        # because '1' < '2' as a character.\n"
        "        # Fix: sort by leading integer:\n"
        "        #   key=lambda f: int(f.split('_')[0])\n"
        "        all_files = sorted(os.listdir(migration_dir))\n"
        "        sql_files = [f for f in all_files if f.endswith('.sql')]\n"
        "        for fname in sql_files:\n"
        "            fpath = os.path.join(migration_dir, fname)\n"
        "            with open(fpath) as fh:\n"
        "                sql = fh.read()\n"
        "            try:\n"
        "                # BUG 2: conn.execute() only runs ONE statement per call.\n"
        "                # 10_add_indexes.sql has TWO CREATE INDEX statements;\n"
        "                # the second is silently dropped.\n"
        "                # Fix: use conn.executescript(sql)\n"
        "                conn.execute(sql)\n"
        "                conn.commit()\n"
        "                logging.info(f\"Applied: {fname}\")\n"
        "            except Exception:\n"
        "                # BUG 3: exception swallowed — migration failure is invisible.\n"
        "                # Fix: re-raise so the caller knows a migration failed.\n"
        "                pass\n"
        "    finally:\n"
        "        conn.close()\n"
    )

    # ── app_init.py — runs migrations, writes migration.log ──────────────────
    app_init_src = (
        '#!/usr/bin/env python3\n'
        '"""Database initializer. Run via restart_service to apply migrations.\n'
        "Reads SQL files from migrations/, applies them to app.db in order,\n"
        'and writes progress to migration.log.\n'
        '"""\n'
        "import os, sys, logging\n"
        "\n"
        "DB_PATH        = \"app.db\"\n"
        "MIGRATION_DIR  = \"migrations\"\n"
        "\n"
        "def main():\n"
        "    try:\n"
        "        from db.migrator import run_migrations\n"
        "        run_migrations(DB_PATH, MIGRATION_DIR)\n"
        "        print(\"Migrations complete.\")\n"
        "        return 0\n"
        "    except Exception as e:\n"
        "        print(f\"Migration failed: {e}\", file=sys.stderr)\n"
        "        return 1\n"
        "\n"
        "if __name__ == \"__main__\":\n"
        "    sys.exit(main())\n"
    )

    # ── Parametric test file ─────────────────────────────────────────────────
    test_template = """\
import os
import sqlite3
import pytest

DB_PATH = "app.db"


def _get_tables(conn):
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )
    return {{row[0] for row in cur.fetchall()}}


def _get_indexes(conn):
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
    )
    return {{row[0] for row in cur.fetchall()}}


def test_db_exists():
    \"\"\"app.db must exist. Call restart_service to run migrations if missing.\"\"\"
    assert os.path.isfile(DB_PATH), (
        "app.db not found. "
        "Fix db/migrator.py, then call restart_service to run app_init.py. "
        "app_init.py runs all SQL migrations and creates app.db. "
        "Use inspect_logs to read migration.log if something goes wrong."
    )


def test_users_table_exists():
    \"\"\"The users table must be created by 1_create_users.sql.\"\"\"
    assert os.path.isfile(DB_PATH), pytest.skip("app.db missing — run test_db_exists first")
    conn = sqlite3.connect(DB_PATH)
    tables = _get_tables(conn)
    conn.close()
    assert "users" in tables, (
        "DB Error: 'users' table not found. "
        "Migration 1_create_users.sql may not have run. "
        "Check that db/migrator.py sorts files by leading integer, not alphabetically."
    )


def test_posts_table_exists():
    \"\"\"The posts table must be created by 2_create_posts.sql (depends on users).\"\"\"
    assert os.path.isfile(DB_PATH), pytest.skip("app.db missing — run test_db_exists first")
    conn = sqlite3.connect(DB_PATH)
    tables = _get_tables(conn)
    conn.close()
    assert "posts" in tables, (
        "DB Error: 'posts' table not found. "
        "Migration 2_create_posts.sql may have failed because users table was not yet created. "
        "Fix: sort migrations by leading integer so 2_create_posts runs AFTER 1_create_users. "
        "Also ensure exceptions are not silently swallowed (Bug 3)."
    )


def test_posts_index_exists():
    \"\"\"idx_posts_user_id must be created by 10_add_indexes.sql.\"\"\"
    assert os.path.isfile(DB_PATH), pytest.skip("app.db missing — run test_db_exists first")
    conn = sqlite3.connect(DB_PATH)
    indexes = _get_indexes(conn)
    conn.close()
    assert "idx_posts_user_id" in indexes, (
        "DB Error: index idx_posts_user_id not found. "
        "10_add_indexes.sql contains two CREATE INDEX statements. "
        "Fix: use conn.executescript(sql) instead of conn.execute(sql) for multi-statement files. "
        "Use inspect_logs to check migration.log for details."
    )


def test_user_column_index_exists():
    \"\"\"idx_users_{user_col} must also be created by 10_add_indexes.sql.\"\"\"
    assert os.path.isfile(DB_PATH), pytest.skip("app.db missing — run test_db_exists first")
    conn = sqlite3.connect(DB_PATH)
    indexes = _get_indexes(conn)
    conn.close()
    assert "{idx2_name}" in indexes, (
        "DB Error: index {idx2_name} not found. "
        "This is the second CREATE INDEX in 10_add_indexes.sql. "
        "Fix: use conn.executescript(sql) to execute both statements."
    )
"""
    test_src = test_template.format(user_col=user_col, idx2_name=idx2_name)

    return {
        "db/__init__.py": "",
        "db/migrator.py": migrator_src,
        "migrations/1_create_users.sql": sql_1,
        "migrations/2_create_posts.sql": sql_2,
        "migrations/10_add_indexes.sql": sql_10,
        "app_init.py": app_init_src,
        "tests/__init__.py": "",
        "tests/test_migrations.py": test_src,
    }


# =============================================================================
# Task manifest
# =============================================================================

TASKS: List[Dict[str, Any]] = [
    {
        "task_id": "task_1_pr",
        "description": (
            "INC-001: User registration service is rejecting valid sign-ups and "
            "storing passwords insecurely. Email validation accepts empty local parts, "
            "the minimum-length check is off-by-one, and passwords are hashed with MD5. "
            "Fix all three bugs in users/ so the tests pass."
        ),
        "_repo_factory": _make_task_1_repo,
        "_diagnosis_keywords": frozenset({
            "regex", "plus", "one-or-more", "md5", "sha256", "sha-256",
            "hashlib", "length", ">=", "minimum", "local", "empty",
        }),
        "_optimal_steps": 5,
    },
    {
        "task_id": "task_2_pr",
        "description": (
            "INC-019: Order totals are wrong after the pricing engine was refactored. "
            "Quantity is ignored in subtotal calculation, discounts inflate the price "
            "instead of reducing it, and tax is applied as a flat offset instead of "
            "a multiplier. Fix all three bugs so the tests pass."
        ),
        "_repo_factory": _make_task_2_repo,
        "_diagnosis_keywords": frozenset({
            "quantity", "multiply", "discount", "subtract", "remove",
            "tax", "multiplier", "1+", "rate", "offset",
        }),
        "_optimal_steps": 5,
    },
    {
        "task_id": "task_3_pr",
        "description": (
            "INC-034: Payment service is failing in production. Secrets scanner "
            "flagged a hardcoded Stripe live key, async handlers are blocking the "
            "event loop with a sync sleep, and the retry loop runs one iteration "
            "too many. Fix all three so the tests pass."
        ),
        "_repo_factory": _make_task_3_repo,
        "_diagnosis_keywords": frozenset({
            "async", "await", "asyncio", "sleep", "getenv", "environment",
            "range", "retry", "off-by-one", "hardcoded", "secret",
        }),
        "_optimal_steps": 5,
    },
    {
        "task_id": "task_4_pr",
        "description": (
            "INC-056: LRU cache is evicting the wrong entries and the serialiser "
            "crashes on null input. Cache get() never calls move_to_end(), eviction "
            "logic is missing from put(), and deserialize(None) raises TypeError. "
            "Fix all three bugs so the tests pass."
        ),
        "_repo_factory": _make_task_4_repo,
        "_diagnosis_keywords": frozenset({
            "move_to_end", "popitem", "evict", "lru", "capacity",
            "none", "guard", "serializer", "null", "ordereddict",
        }),
        "_optimal_steps": 5,
    },
    {
        "task_id": "task_5_pr",
        "description": (
            "INC-121: Real-time analytics pipeline is producing incorrect metrics. "
            "Three algorithmic bugs: EMA formula has alpha and (1-alpha) swapped, "
            "anomaly detector uses biased population std (false positives), and "
            "sliding-window has an off-by-one that drops the last window. "
            "Fix all three so the tests pass."
        ),
        "_repo_factory": _make_task_5_repo,
        "_diagnosis_keywords": frozenset({
            "alpha", "swap", "ema", "population", "sample", "ddof",
            "range", "window", "off-by-one", "+1", "biased", "std",
        }),
        "_optimal_steps": 6,
    },
    {
        "task_id": "task_6_pr",
        "description": (
            "INC-312: Search quality regression after scorer refactor. "
            "CI is red on three files: indexer strips only whitespace (not punctuation), "
            "scorer divides by doc length (penalises long documents), and ranker sorts "
            "ascending. WARNING: fixing the scorer alone causes a currently-passing "
            "ranking test to regress — both scorer and ranker must be fixed together."
        ),
        "_repo_factory": _make_task_6_repo,
        "_diagnosis_keywords": frozenset({
            "re.split", "punctuation", "split", "query_terms", "len",
            "reverse", "descending", "coupled", "regression", "together",
        }),
        "_optimal_steps": 6,
    },
    {
        "task_id": "task_7_pr",
        "description": (
            "INC-031: Request-handling service fails to start after config was last "
            "edited. config/settings.py has three bugs: TIMEOUT and MAX_WORKERS are "
            "string literals instead of ints (TypeError in arithmetic and range()), "
            "and RETRY_DELAY is negative (retry storms). "
            "Fix the config, then call restart_service to apply changes."
        ),
        "_repo_factory": _make_task_7_repo,
        "_diagnosis_keywords": frozenset({
            "string", "str", "int", "type", "timeout", "max_workers",
            "retry_delay", "negative", "quote", "literal", "typeerror",
        }),
        "_optimal_steps": 6,
    },
    {
        "task_id": "task_8_pr",
        "description": (
            "INC-047: Database migrations are failing silently after migration script "
            "refactor. db/migrator.py has three bugs: lexicographic sort runs "
            "'10_add_indexes' before '2_create_posts', exceptions are swallowed with "
            "bare pass (invisible failures), and conn.execute() drops multi-statement "
            "SQL. Use inspect_logs, fix all three, then call restart_service."
        ),
        "_repo_factory": _make_task_8_repo,
        "_diagnosis_keywords": frozenset({
            "sort", "lexicographic", "numeric", "executescript", "execute",
            "silent", "exception", "migration", "pass", "swallowed",
        }),
        "_optimal_steps": 7,
    },
    {
        "task_id": "task_9_pr",
        "description": (
            "INC-089: Service is being OOM-killed under load. inspect_logs shows "
            "service.log has two memory leak errors. app/cache.py never evicts "
            "entries (unbounded list), and app/processor.py accumulates every "
            "processed event in a module-level list. "
            "Fix both leaks, then call restart_service to verify memory safety."
        ),
        "_repo_factory": _make_task_9_repo,
        "_diagnosis_keywords": frozenset({
            "leak", "unbounded", "evict", "maxsize", "bounded", "clear",
            "accumulate", "memory", "oom", "deque", "list", "module-level",
        }),
        "_optimal_steps": 7,
    },
    {
        "task_id": "task_10_pr",
        "description": (
            "INC-201: 3-service topology (gateway → auth → db) is fully down after "
            "a config push. Three bugs across services: db/config.py CONNECT_TIMEOUT "
            "is 0.001 s (valid range: 0.1–60.0), auth/client.py retries one too many "
            "times (range off-by-one), gateway/circuit_breaker.py opens after just "
            "1 failure instead of the configured threshold. Use inspect_logs to trace "
            "the cascade. Fix bugs in dependency order (db → auth → gateway), "
            "call restart_service for each service, then submit."
        ),
        "_repo_factory": _make_task_10_repo,
        "_diagnosis_keywords": {
            "affected_service": frozenset({
                "db", "database", "connect_timeout", "timeout", "auth", "gateway",
            }),
            "root_cause": frozenset({
                "threshold", "circuit_breaker", "range", "retries", "off-by-one",
                "connect_timeout", "0.001", "consecutive",
            }),
            "propagation": frozenset({
                "cascade", "upstream", "downstream", "auth", "gateway",
                "flood", "degraded", "worsened",
            }),
            "remediation": frozenset({
                "db_first", "dependency", "order", "sequential", "restart",
                "topology", "bottom-up",
            }),
        },
        "_optimal_steps": 7,
    },
]


def get_task(task_id: str) -> Dict[str, Any]:
    for t in TASKS:
        if t["task_id"] == task_id:
            return t
    raise ValueError(f"Task {task_id} not found")


def list_task_ids() -> List[str]:
    return [t["task_id"] for t in TASKS]


# =============================================================================
# Per-task grader functions
# =============================================================================

_CLEAN_ENV_KEYS = {"PATH", "HOME", "LANG", "LC_ALL"}
_WINDOWS_ENV_KEYS = {"SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP", "COMSPEC"}


def _run_pytest(workspace_dir: str) -> float:
    keys = _CLEAN_ENV_KEYS.copy()
    if platform.system() == "Windows":
        keys |= _WINDOWS_ENV_KEYS
    env = {k: v for k, v in os.environ.items() if k in keys}
    env["PYTHONPATH"] = workspace_dir
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if "PATH" not in env:
        env["PATH"] = "/usr/local/bin:/usr/bin:/bin"

    try:
        result = subprocess.run(
            "pytest tests/ --disable-warnings -v",
            cwd=workspace_dir, shell=True,
            capture_output=True, text=True, timeout=15, env=env
        )
        if result.returncode == 0:
            return 1.0

        output = result.stdout + result.stderr
        n_passed = sum(1 for line in output.splitlines() if " PASSED" in line)
        n_failed = sum(1 for line in output.splitlines() if " FAILED" in line or " ERROR" in line)
        total = n_passed + n_failed
        if total > 0:
            return n_passed / total

        m_pass = re.search(r"(\d+) passed", output)
        m_fail = re.search(r"(\d+) failed", output)
        p = int(m_pass.group(1)) if m_pass else 0
        f = int(m_fail.group(1)) if m_fail else 0
        return p / (p + f) if (p + f) > 0 else 0.0

    except Exception:
        return 0.0


def grade_task_1_pr(workspace_dir: str) -> float:
    """Grader: user registration validation + hashing."""
    return _run_pytest(workspace_dir)


def grade_task_2_pr(workspace_dir: str) -> float:
    """Grader: order pricing (quantity, discount, tax)."""
    return _run_pytest(workspace_dir)


def grade_task_3_pr(workspace_dir: str) -> float:
    """Grader: async payment processor + retry count."""
    return _run_pytest(workspace_dir)


def grade_task_4_pr(workspace_dir: str) -> float:
    """Grader: LRU cache eviction order + serialiser None guard."""
    return _run_pytest(workspace_dir)


def grade_task_5_pr(workspace_dir: str) -> float:
    """Grader: EMA formula, sample std, sliding-window boundary."""
    return _run_pytest(workspace_dir)


def grade_task_6_pr(workspace_dir: str) -> float:
    """Grader: tokenizer punctuation, relevance scorer, coupled ranking trap."""
    return _run_pytest(workspace_dir)


def grade_task_7_pr(workspace_dir: str) -> float:
    """Grader: config type errors + service restart gate."""
    return _run_pytest(workspace_dir)


def grade_task_8_pr(workspace_dir: str) -> float:
    """Grader: DB migration order, silent exceptions, multi-statement execute."""
    return _run_pytest(workspace_dir)


def grade_task_9_pr(workspace_dir: str) -> float:
    """Grader: memory-leak cache + processor, OOM-restart gate."""
    return _run_pytest(workspace_dir)


def grade_task_10_pr(workspace_dir: str) -> float:
    """Grader: network timeout, retry off-by-one, circuit-breaker threshold."""
    return _run_pytest(workspace_dir)


GRADERS: Dict[str, Callable[[str], float]] = {
    "task_1_pr":  grade_task_1_pr,
    "task_2_pr":  grade_task_2_pr,
    "task_3_pr":  grade_task_3_pr,
    "task_4_pr":  grade_task_4_pr,
    "task_5_pr":  grade_task_5_pr,
    "task_6_pr":  grade_task_6_pr,
    "task_7_pr":  grade_task_7_pr,
    "task_8_pr":  grade_task_8_pr,
    "task_9_pr":  grade_task_9_pr,
    "task_10_pr": grade_task_10_pr,
}
