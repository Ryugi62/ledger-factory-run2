# Stage 3 review, round 1 — ACCEPT

Commit checked: `cc562c1680116428e41851948619571f547e9094` (6246927 copy of stage-2, d9259f3 server, cc562c1 RUN.md).
Ledger: `factory/ledger/stage-3.md` at 898c447 (+ stage-1.md, stage-2.md). Verification used a clean clone
of cc562c1 with `factory/probes` taken from f765fae (includes the analyst's 762740c relaxation, which
only stops assuming feed order between payments created within one second, an order S1-158 leaves
unspecified).

## 1. Blind re-derivation

Before opening the ledger, I listed the normative statements of the two longest sections myself:
"Effective time, recorded time, and corrections" (24 statements) and "GET /statement" (9). Each maps
to a ledger row (S3-020..S3-031, S3-032..S3-079). **No ledger gaps.**

## 2. Clean build and start

`docker build stage-3` → OK. Run with `--cpus 2 --memory 2g --network none -e PORT=8080` → `/health` 200 in under 2 s.

## 3. Checks

| Command | Result |
|---|---|
| `harness run --track pocketful --repo <clean clone> --stage 3 --mode isolated --out checks/s3-reviewer-r1a` | stage 1 **147/147**, stage 2 **35/35**, stage 3 **6/6**; stage 4 fail (expected); claimed stage: 3 |
| `factory/probes/stage-3/run_docker.sh --offline --stage1-ref 247843d --stage2-ref 2dcc6ae`, phase A (UI, custom PORT, 2 vCPU/2 GiB, cross-instance, stage-1 and stage-2 → stage-3 upgrades) | **184 passed, 0 failed, 0 skipped** |
| same script, offline phase (API only) | 154 passed, 0 failed, 3 skipped (upgrades and cross-instance, skipped by design) |
| Own black-box script (`review-tmp/s3t.py`, `s3t2.py`, outside the repository) | 74 checks: 72 passed. The 2 failures were mistakes in my own script (a wrong expected value, and a stale revision caused by an earlier step). After fixing the script, the scenarios behave correctly |

My own scenarios covered:
- A future seeded `created_at` → 422 with state unchanged.
- `as_of` before the earliest payment returns the opening balance (fixture balance minus the seeded net). `as_of` exactly at a payment is inclusive, and payments at the same instant combine. A naive time, a bare date, an empty value, a space separator and an impossible date all give 422. An odd offset is echoed exactly.
- Statement: ties ordered by id, running `balance_after`, the half-open `[from, to)` window, and opening, closing and `balance_after` unchanged by paging. An offset beyond the end returns no entries and `has_more: false`. A third party sees no public payments of others. `limit`, `offset` and `from` validation works.
- Snapshot: frozen after a later payment. `known_at` together with `snapshot` → 422. Another user's token or an unknown token → 404.
- Corrections:
  - A non-sender gets 403, an unknown payment 404, and a missing key 400. Each invalid field gives 422.
  - A valid correction returns 201 with the `recorded_at` the server assigns. Replay gives 200 with the same body; a different body with the same key gives 409 `idempotency_key_reuse`; a stale revision gives 409 `stale_revision`.
  - A decrease moves the difference back from the receiver to the sender.
- Revisions endpoint: parties only. A third party gets 404, and no token gives 401. `/activity` keeps the original amount.
- `known_at` before a correction selects revision 1, and before anything was recorded it selects nothing. Statement entries carry `revision`, `effective_at` and the selected `payment.amount`. A zero-amount reversal stays as a zero-delta entry.
- Overdraft checks:
  - A current unaffordable increase gives `insufficient_funds`, and so does a receiver-debit decrease.
  - Moving a debit before earlier credits gives `historical_overdraft`. The failure leaves the balance and revisions intact, and the key is not claimed.
  - The same move to an instant with a simultaneous credit is accepted, because movements at one boundary are combined.
- The sum of balances is conserved now and at 5 historical instants.
- Settlement members and captures are rejected with 422 `linked_payment_immutable`. A member's revision 1 has `effective_at = recorded_at = committed_at`.
- Historical holds: `held` matches the current value just after now, is 0 before the hold existed, and is 0 after the TTL deadline. `closed_at` is null while the hold is open. A capture appears once in the statement.
- Of 8 concurrent corrections on the same `expected_revision`, exactly one returns 201 and the other 7 get 409.

## 4. Reading

- Instants are integer microseconds. A strictly increasing event clock (`tick`) makes recorded times distinct and monotonic.
- Each payment has a `revs` list. `selectedRev` picks the latest revision recorded at or before `known_at`, and `balanceAt` and `buildStatement` apply it by effective time.
- `heldAt` limits known events to `min(as_of, known_at)`, and the expiry deadline counts as known once the creation is.
- `historyIsSound` sweeps total and available over every effective and hold-event instant under the latest revisions, for both parties.
- `opCorrect` validates in this order: 404 → 403 → 422 → `linked_payment_immutable` → `stale_revision` → `insufficient_funds` (against available) → `historical_overdraft`. A failed check pops the revision it tentatively appended and changes no balances. It runs synchronously, so concurrent corrections are serialised.
- The UI files (`app.js`, `app.css`, `ui.js`) are byte-identical to the accepted stage 2. No fixture-specific or test-name logic.

Non-blocking observations:
- `effective_at` is accepted up to 2 s past the server clock (`CLOCK_SKEW_US`). For those 2 s, plain `GET /me` already shows the corrected amount while `GET /me?as_of=now` does not. A strict "not later than now" test with a lead under 2 s would also pass where it should be refused. This is a narrow window, not a confirmed failure.
- Every non-snapshot `GET /statement` stores a frozen result until reset, so memory grows with statement calls. That is fine under the stated limits, but it has no upper bound.

## 5. Earlier stages

`git diff 247843d cc562c1 -- stage-1` and `git diff 2dcc6ae cc562c1 -- stage-2` are both empty. The stage-1 and
stage-2 shipped checks and probes pass against the stage-3 service.

## Decision

**ACCEPT**
