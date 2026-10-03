# Stage 1 review, round 1 — ACCEPT

Commit checked: `247843dbcb916edd5aca24c2426b8d48a67aa41f` (service 93ae813 + Dockerfile/RUN.md 247843d).
Ledger: `factory/ledger/stage-1.md` at 9c0a17b. Verification ran on a clean clone of 247843d
(the shared working tree had uncommitted analyst files under `factory/probes/`; none in `stage-1/`).

## 1. Blind re-derivation

I listed the normative statements of §8 (API) and §4 (Model) myself before opening the ledger:
22 from §4 and 34 from §8. Each one maps to a ledger row (S1-035..S1-066, S1-108..S1-159, plus
S1-091 and S1-096 for the cross-references). **No ledger gaps.** Every ★ row names at least one probe.

## 2. Clean build and start

- `docker build -t pocketful-stage-1-review stage-1` → OK (node:22-alpine, no dependencies).
- `docker run --cpus 2 --memory 2g --network none -e PORT=8080 …` → `GET /health` 200 `{"status":"ok"}` in under 2 s.

## 3. Checks

| Command | Result |
|---|---|
| `.venv/bin/python -m harness run --track pocketful --repo <clean clone> --stage 1 --mode isolated --out checks/s1-reviewer-r1a` | stage 1 **147 / 147 passed**; stage 2 fail (expected); claimed stage: 1 |
| `PROBE_REPO=$PWD/stage-1 IMAGE=pocketful-stage-1-review factory/probes/stage-1/run_docker.sh --skip-build --offline`, phase A (PORT=9137, 2 vCPU, 2 GiB, cross-instance against phase B on default 8080) | **83 passed, 0 failed, 0 skipped**; healthy at 0.0 s; phase B default-port healthy |
| same script, offline phase (internal network) | 81 passed, 1 failed, 1 skipped |

The only offline failure is `p_runtime.test_delivery_files` [S1-010]: "Dockerfile missing at the
repository root (/repo/Dockerfile)". That is a probe defect: `run_docker.sh` mounts the repository root
as `/repo` and ignores `PROBE_REPO`. `stage-1/Dockerfile` and `stage-1/RUN.md` exist and are
non-empty. RUN.md contains the docker command and PORT. The same probe passes in phase A with
`PROBE_REPO` set. This does not count against the implementation. The analyst should fix the probe.

## 4. Reading of the diff (9c0a17b..247843d: stage-1/ only, 4 files)

- No logic special-cases fixture data or test names. Grepping for fixture handles, ids and test names finds nothing.
- Writes are atomic. Every state-changing operation, including the idempotency claim-and-store, runs
  synchronously on the event loop with no `await` between the check and the write. The only async
  steps are scrypt hashing in signup and login, and conflicts are re-checked after the await.
- Idempotency: the key is scoped by user, path and key, the body is compared in canonical (sorted-key)
  form, 4xx responses never claim a key, and a claimed key is resolved before field validation.
- Settlement affordability is computed on net per-wallet deltas. All payments are committed in one
  synchronous block with a shared `committed_at`.
- Manual spot checks (JPY fixture): a net-affordable settlement commits; replaying it with a
  different body gives 409 `idempotency_key_reuse`; `limit=+4` gives 422; a fixture with a negative
  balance gives 422 and leaves the previous state intact.
- There is no UI in this stage, so no screen states to check.

Non-blocking observations (no ledger row is violated, and no probe or check fails):
- A signup that is mid-hash when `POST /_test/reset` lands re-checks conflicts against the new
  state and inserts the user into it. A test is unlikely to hit this. Rechecking `state` identity
  after the await, as login already does, would close it.
- An imported state with a hand-crafted scrypt hash (N up to 2^20, r up to 32) passes validation,
  but it would exceed `maxmem` and make that user's login return 500. Unchanged exports are unaffected.

## 5. Earlier stages

Stage 1 is the first stage, so no earlier stage folders exist. The commits touch only `stage-1/`.

## Decision

**ACCEPT**
