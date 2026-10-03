# Stage 4 review, round 1 — ACCEPT (with one recorded known gap)

Commit checked: `777b76d6609432183d3e8928b76220ebe64a9817` (531f619 copy of stage-3, 3a3b3e3 server, 777b76d RUN.md).
Ledger: `factory/ledger/stage-4.md` at f765fae (S4-045 note updated in 81cfc9e), plus the stage-1..3 ledgers.
Verification ran on a clean clone of 777b76d.

## 1. Blind re-derivation

Before opening the ledger, I listed the normative statements of the two longest sections myself:
"Batch corrections" and "Refunds and corrected history" (34 statements). Each maps to S4-006..S4-045.
**No ledger gaps.**

## 2. Clean build and start

`docker build stage-4` → OK. Run with `--cpus 2 --memory 2g --network none -e PORT=8080` → `/health` 200 in under 2 s.

## 3. Checks

| Command | Result |
|---|---|
| `harness run --track pocketful --repo <clean clone> --stage 4 --mode isolated --out checks/s4-reviewer-r1a` | stage 1 **147/147**, stage 2 **35/35**, stage 3 **6/6**, stage 4 **5/5**; highest contiguous stage 4; claimed stage: 4 |
| `HP_A=28437 … factory/probes/stage-4/run_docker.sh --offline --stage1-ref 247843d --stage2-ref 2dcc6ae --stage3-ref cc562c1`, phase A | 202 passed, **2 failed**, 0 skipped. I used non-default host ports because the defaults 18437/18441 were held by another run on this machine |
| same, offline phase | 173 passed, 0 failed, 4 skipped (upgrades and cross-instance, skipped by design) |
| The two failing upgrade probes plus all other upgrade probes, rerun against the same images. My only change was to drop **null** `refund_of` and `correction_batch_id` in the probes' comparison helpers (`review-tmp/s4norm`, outside the repository) | **8 passed, 0 failed**. The stage-3 part prints `KNOWN GAP (S4-045)` for the three old stage-3 snapshot tokens |
| Own black-box script (`review-tmp/s4t.py`, outside the repository) | **50/50** passed. My first two runs had script mistakes: a hold larger than the available balance, and a batch that correctly drew 409 `historical_overdraft` because it would have overdrawn available at an earlier hold. After correcting the script, all 50 pass |

### The two phase-A probe failures are probe defects, not service defects

- `q_upgrade::test_stage1_export_is_accepted_by_stage2` (stage-2 probe) and the stage-3 branch of
  `s_upgrade4::test_exports_of_stages_1_to_3_are_accepted` compare payment views byte for byte. Their
  helpers tolerate only a null `authorization_id`.
- Stage 4 is *required* to add `refund_of: null` to every non-refund payment ("Other payments have
  `refund_of: null`", S4-016). It also exposes `correction_batch_id` on revisions (S4-036).
- With null values of exactly those two fields ignored, both probes pass. No other difference exists.
  Replayed idempotent receipts are still byte-identical to the originals (checked inside the same probes).
- **For the analyst:** extend both helpers to ignore a null `refund_of` and a null `correction_batch_id`.

### Own scenarios covered

- Refunds:
  - Errors: the sender gets 403, an unknown payment 404, and a missing key 400. An amount of 0, −1, 1.5, "10", null or true, or a missing amount, gives 422.
  - A successful refund returns 201 with the reversed parties, `refund_of`, null `request_id` and `authorization_id`, and the original note and visibility. Replay gives 200 with the same body.
  - The cumulative cap gives `refund_exceeds_payment`, and so does a correction to below the refunded total. The cap follows the corrected amount.
  - A refund of a refund gives `invalid_refund_target`, and correcting a refund gives `linked_payment_immutable`.
  - Against available funds (money on hold), a refund gets 409 `insufficient_funds`.
  - Refunding a request payment leaves the request `paid`. Refunding a capture leaves the authorization `captured` with held 0.
  - Every other payment carries `refund_of: null`.
- Batches:
  - Errors: no token gives 401, a non-operator 403, and a missing key 400. A list of 0 or 33 items, duplicate ids, or a missing list gives 422. A missing settlement member gives `incomplete_settlement`, and members at differing instants give 422.
  - An item error (404) beats completeness. A capture in a batch gives `linked_payment_immutable`, plus `stale_revision` and `refund_exceeds_payment`.
  - A successful batch returns 201 with revisions in input order, one shared `recorded_at`, and `correction_batch_id` on each revision. Offset spellings `+00:00` and `+02:00` for the same instant are accepted, and unknown fields are ignored.
  - Money moves by the combined net. Replay gives 200 with the same body, and a changed body gives `idempotency_key_reuse`. A settlement retry returns its original body.
  - A single correction of a settlement member gives `linked_payment_immutable`.
  - A settlement member can be refunded, and the refund does not join the settlement (the 2-member batch still succeeds).
  - Combined current shortfall gives `insufficient_funds`, and a past overdraft gives `historical_overdraft`. A future `effective_at` gives 422.
  - When concurrent batches and a single correction share one expected revision, exactly one returns 201.
  - The seeded total is conserved.

## 4. Reading

- `opRefund` checks in this order: 404 → 403 → amount 422 → `invalid_refund_target` → `refund_exceeds_payment` against the latest corrected amount → available funds.
- `opCorrectionBatch` validates the shape, then each item in input order (`correctionFields`, 404, `checkCorrectable`: linked → stale → refunded floor), then settlement completeness, then the same effective instant.
- `applyCorrections` checks the combined net against available funds, appends every revision with one `tick()` instant (strictly later than any earlier recorded instant), runs `historyIsSound` for each affected wallet, and pops every revision on failure. Everything is synchronous, so batches are atomic and serialised.
- The stage-3 review note about the 2-second `effective_at` allowance was addressed: the check is now strict against the service clock.
- Snapshots are exported and imported (S4-049 roundtrip probe passes). The UI files are byte-identical to stage 3.
- There is no fixture-specific or test-name logic.

## 5. S4-045 known gap (recorded, not a defect of this revision)

S4-045 ★ requires accepting stage 1–3 exports "retaining settlement membership, corrections and snapshots".
Membership and corrections are retained: the incomplete-batch and correction-history checks pass for stage-1, stage-2
and stage-3 exports. The *snapshot tokens issued by the old stage-3 service* are unknown after import (404).

I verified the cause independently. The accepted stage-3 `exportState` (cc562c1) does not serialise
`state.snapshots`, so those tokens are not present in any stage-3 export, and no stage-4 implementation can rebuild
them. The stage-3 specification only required tokens to "last until reset", so the stage-3 acceptance did not
miss a stage-3 requirement. The coordinator ruled that accepted folders are never edited.

Stage 4 does everything possible here: it imports snapshots when an export contains them, and it exports and
re-imports its own (S4-049, probed). The gap is documented in `stage-4/RUN.md` and in the ledger note.

## 6. Earlier stages

`git diff` of 247843d → 777b76d over `stage-1`, 2dcc6ae over `stage-2` and cc562c1 over `stage-3` are all empty.
The stage-1..3 shipped checks and probes pass against stage 4, apart from the two probe defects above.

## Decision

**ACCEPT**. The only open item is the S4-045 stage-3 snapshot-token gap, recorded here and documented in RUN.md.
The two phase-A probe defects are for the analyst.
