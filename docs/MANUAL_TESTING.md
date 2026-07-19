# Manual Test Plan — Aetheris

A hands-on test script organized by the four personas defined in
[`docs/PRD.md`](PRD.md) §3. Each section is runnable independently. Every
command below has been executed and verified against the live deployment.

**Live endpoints:**

| | URL |
|---|---|
| Frontend (Vercel) | https://frontend-ivory-nine-13.vercel.app |
| Backend API (Railway) | https://aetheris-production-ad80.up.railway.app |

**Local prerequisites** (only needed for Personas 2 and 4, which exercise
code paths with no UI yet):

```bash
cd /Volumes/LaCie/AETHERIS
source .venv/bin/activate
```

---

## Persona 1 — End Customer

*Wants their issue resolved through the chat widget. Never sees a config
file, an API contract, or a log line.*

### 1.1 Basic conversation, no tool needed

1. Open https://frontend-ivory-nine-13.vercel.app
2. Type: `What are your support hours?`
3. **Expect:** a direct text answer, no 🔧 badge underneath it, no errors.

### 1.2 A question that triggers a tool

1. Type: `Where is my order A-2002?`
2. **Expect:** a `🔧 get_order_status` badge appears under the reply. The
   reply itself says the lookup temporarily failed — that's correct, not a
   bug (see note below).
3. **Expect:** thinking indicator (`Aetheris is thinking…`) shown while
   waiting, then a clean bubble — no raw error text, no stack trace.

> **Why the tool "fails":** `config/example_tools.json` points at
> `api.tenant-partner.example.com`, a placeholder domain with no real
> backend. This is the intended demo behavior — it proves the graceful
> tool-error path (`tool_execution.py` catches the failure and Claude
> explains it to the user) rather than the happy path. To see the happy
> path, point `AETHERIS_TOOLS_CONFIG` at a tool with a real backend (see
> Persona 2).

### 1.3 Multi-turn memory

1. Continuing the same page (don't click "New conversation"), type:
   `Thanks, also can I get a refund?`
2. **Expect:** the reply references order A-2002 from the *previous* turn
   without you repeating it, and a `🔧 issue_refund` badge appears — proof
   `session_id` correctly threads conversation history.

### 1.4 Session reset

1. Click **New conversation**.
2. Type: `What did we just talk about?`
3. **Expect:** Claude has no memory of A-2002 — confirms `session_id` was
   actually cleared, not just visually reset.

---

## Persona 2 — Tenant Administrator (non-engineer registering a tool)

*Wants to give the agent a new capability by writing JSON, with no code
deploy.* There's no admin UI yet (flagged in the README's **Known
simplifications**), so this persona's actual interface today is
`config/example_tools.json` plus the validation layer it goes through.

### 2.1 Confirm the validator rejects an insecure endpoint

```bash
.venv/bin/python -c "
from pydantic import ValidationError
from aetheris.tools.schema import AdminToolConfig

base = {'name': 'lookup_order', 'description': 'Look up order status by ID',
        'method': 'GET', 'parameters': [], 'tenant_id': 'tenant-demo'}

try:
    AdminToolConfig.model_validate({**base, 'endpoint': 'http://api.example.com/order'})
    print('BUG: http endpoint was accepted')
except ValidationError as e:
    print('Correctly rejected:', e.errors()[0]['msg'])
"
```

**Expect:** `Correctly rejected: Value error, endpoint must use https://`

### 2.2 Confirm you can't paste a literal secret into the config

```bash
.venv/bin/python -c "
from pydantic import ValidationError
from aetheris.tools.schema import AdminToolConfig

base = {'name': 'lookup_order', 'description': 'Look up order status by ID',
        'method': 'GET', 'parameters': [], 'tenant_id': 'tenant-demo',
        'endpoint': 'https://api.example.com/order'}

try:
    AdminToolConfig.model_validate({**base, 'auth': {'scheme': 'bearer_env'}})
    print('BUG: auth without env_var was accepted')
except ValidationError as e:
    print('Correctly rejected:', e.errors()[0]['msg'])
"
```

**Expect:** `Correctly rejected: Value error, auth scheme 'bearer_env' requires env_var`
— the admin can only *name* an environment variable, never embed the secret
value itself.

### 2.3 Register a new tool and see it reach the model

```bash
.venv/bin/python -c "
from aetheris.tools.registry import ToolRegistry

registry = ToolRegistry()
registry.register_from_configs(
    [{
        'name': 'check_warranty',
        'description': 'Check whether a product serial number is under warranty.',
        'endpoint': 'https://api.example.com/warranty',
        'method': 'GET',
        'parameters': [{'name': 'serial', 'type': 'string', 'description': 'Product serial number', 'required': True}],
    }],
    tenant_id='tenant-demo',
    domain_allowlist=frozenset({'api.example.com'}),
)
specs = registry.tool_specs()
print('Registered tools:', [s['name'] for s in specs])
print('Input schema for check_warranty:', specs[0]['input_schema'])
"
```

**Expect:** `check_warranty` appears in the list with a JSON Schema for
`serial` — this is exactly what gets handed to Claude's tool-use API. No
code was written for this specific integration; only JSON.

---

## Persona 3 — Support/Platform Engineer

*Owns the deployment. Cares about health, logs, and the API contract
directly — not the chat widget.*

### 3.1 Health check both environments

```bash
curl -s -o /dev/null -w "local n/a\n"  # skip if not running locally
curl -s https://aetheris-production-ad80.up.railway.app/healthz
```

**Expect:** `{"status":"ok"}`

### 3.2 API contract — new session

```bash
curl -s -X POST https://aetheris-production-ad80.up.railway.app/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "hello"}' | python3 -m json.tool
```

**Expect:** JSON with `session_id`, `reply`, `tool_trajectory` (array), and
`circuit_breaker_tripped` (bool).

### 3.3 API contract — unknown session_id

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST https://aetheris-production-ad80.up.railway.app/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id": "not-a-real-session", "message": "hi"}'
```

**Expect:** `404`

### 3.4 CORS is actually configured

```bash
curl -s -i -X OPTIONS https://aetheris-production-ad80.up.railway.app/chat \
  -H "Origin: https://frontend-ivory-nine-13.vercel.app" \
  -H "Access-Control-Request-Method: POST" | grep -i access-control
```

**Expect:** `access-control-allow-origin`, `access-control-allow-methods`
headers present.

### 3.5 Full regression suite

```bash
.venv/bin/python -m pytest -v
```

**Expect:** `17 passed`, no network access required.

### 3.6 Railway logs and variables

```bash
railway logs
railway variables
```

**Expect:** `ANTHROPIC_API_KEY` present (value not printed to logs —
verify by name, don't `echo` the value), recent request logs with `200 OK`
on `/healthz` and `/chat`.

---

## Persona 4 — Security/Platform Reviewer

*Doesn't trust the code — verifies the SSRF and loop-termination guarantees
directly, not by reading the docstrings.*

### 4.1 SSRF sandbox — attack attempts

```bash
.venv/bin/python -c "
from aetheris.tools.sandbox import validate_url, SSRFBlockedError

tests = [
    ('cloud metadata (AWS/GCP/Azure)', 'https://169.254.169.254/latest/meta-data/'),
    ('loopback', 'https://127.0.0.1/admin'),
    ('RFC1918 private range', 'https://10.0.0.5/internal'),
    ('non-HTTPS', 'http://example.com/api'),
]
for label, url in tests:
    try:
        validate_url(url, domain_allowlist=frozenset())
        print(f'{label}: NOT BLOCKED <-- would be a critical bug')
    except SSRFBlockedError as e:
        print(f'{label}: blocked ({e})')
"
```

**Expect:** all four lines say `blocked`. If any says `NOT BLOCKED`, that's
a real vulnerability — stop and investigate before deploying.

### 4.2 Off-allowlist domain

```bash
.venv/bin/python -c "
from aetheris.tools.sandbox import validate_url, SSRFBlockedError
try:
    validate_url('https://evil.example.com/api', domain_allowlist=frozenset({'api.tenant-partner.example.com'}))
    print('NOT BLOCKED <-- bug')
except SSRFBlockedError as e:
    print('blocked:', e)
"
```

**Expect:** blocked, since `evil.example.com` isn't the tenant's allowlisted
domain.

### 4.3 Circuit breaker — automated, not manual

Triggering the breaker through the live chat UI isn't reliable (it depends
on the model actually choosing to loop, which a well-behaved model mostly
won't do on demand). Use the deterministic tests instead — they force both
trip conditions with a scripted fake model:

```bash
.venv/bin/python -m pytest tests/test_aetheris_core.py -v -k circuit_breaker
```

**Expect:** both `test_circuit_breaker_trips_on_repeated_tool_signature` and
`test_circuit_breaker_trips_on_max_loops_with_varied_args` pass — proving
independently that (a) an identical repeated tool call trips immediately,
and (b) varied-argument calls still trip at the hard numeric cap.

### 4.4 Audit trail

Re-run 1.2 or 1.3 above (anything that triggers a tool call) via the API
directly, and inspect `tool_trajectory` in the response — that's the same
data structure (`ToolCallRecord`) that would back a real audit log:

```bash
curl -s -X POST https://aetheris-production-ad80.up.railway.app/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Where is my order A-2002?"}' | python3 -m json.tool
```

**Expect:** `tool_trajectory` lists exactly the tools that were called, in
order — nothing silently invoked, nothing missing.

### 4.5 Full security unit suite

```bash
.venv/bin/python -m pytest tests/test_tool_sandbox.py -v
```

**Expect:** 8 passed — every SSRF/scheme/allowlist edge case, all hermetic
(no real network access).

---

## Quick smoke test (all four personas, ~5 minutes)

Run in order; stop and investigate on any unexpected result.

```bash
# Persona 3: is it even up?
curl -s https://aetheris-production-ad80.up.railway.app/healthz

# Persona 1: does a real conversation work?
curl -s -X POST https://aetheris-production-ad80.up.railway.app/chat \
  -H "Content-Type: application/json" -d '{"message": "hello"}' | python3 -m json.tool

# Persona 4: does the security suite pass?
.venv/bin/python -m pytest tests/test_tool_sandbox.py -q

# Persona 4: does the circuit breaker actually bound loops?
.venv/bin/python -m pytest tests/test_aetheris_core.py -q -k circuit_breaker

# Persona 2: does config validation actually reject bad input?
.venv/bin/python -c "
from pydantic import ValidationError
from aetheris.tools.schema import AdminToolConfig
try:
    AdminToolConfig.model_validate({'name': 'x', 'description': 'x'*10, 'method': 'GET',
        'parameters': [], 'tenant_id': 't', 'endpoint': 'http://insecure.example.com'})
    print('FAIL: insecure endpoint accepted')
except ValidationError:
    print('PASS: insecure endpoint rejected')
"
```

If all five commands return healthy/passing output, the system is in a
demoable state.
