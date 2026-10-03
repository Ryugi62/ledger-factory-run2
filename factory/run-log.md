# Pocketful run log

## Stage 1 report
- Start / end (UTC): 2026-10-03 05:25 / 06:48
- Review rounds: 1 (round 1 ACCEPT). Rejections: none.
- Ledger items: 208 total; 199 covered by a passing probe (7 of them only by run_docker.sh), 9 manual (nothing observable); known gaps: 0.
- Reviewer final check: clean build, --cpus 2 --memory 2g --network none, /health 200 in <2 s; harness isolated stage 1 147/147 passed (stage 2 line failed, expected; claimed stage: 1); analyst probes 83/83 in docker phase A, offline phase 81 pass / 1 fail / 1 skip — the failure is probe defect S1-010 (script ignores PROBE_REPO), not a product defect.
- Non-blocking reviewer notes: signup mid-hash during a reset lands in post-reset state; crafted imported hash with huge N could make that login 500.
- Accepted commit: 247843dbcb916edd5aca24c2426b8d48a67aa41f (verdict 1f09d3d, factory/reviews/stage-1-r1.md).

## Stage 2 report
- Start / end (UTC): 2026-10-03 06:48 / 08:14
- Review rounds: 2. Rejections: round 1 on 4beb1c4 (F1: literal "null" text rendered on /requests, S2-015/S2-020; cause replaceChildren with null child) -> answered by commit 2dcc6ae (all replaceChildren calls go through a null-filtering helper). Analyst added a leaked-text probe (1940007).
- Ledger items: 173 total (plus stage-1 ledger carried forward); 166 covered by a passing probe, 7 manual; known gaps: 0.
- Reviewer final check: clean build, --cpus 2 --memory 2g --network none, /health 200 in <2 s; harness isolated stage 2: stage 1 147/147, stage 2 35/35 (stage 3 line failed, expected; claimed stage: 2); run_docker.sh --offline --stage1-ref 247843d: phase A 144 pass / 0 fail, offline phase 115 pass / 0 fail / 2 skipped by design; own Playwright pass (available headline, JPY decimals refused, retry moves money once, no horizontal scroll at 375/1280 px, no leaked null/undefined/NaN) all correct; stage-1/ untouched.
- Accepted commit: 2dcc6aeec66a682ce64fd8eefc606612bf8e13b1 (verdict 1c8ec08, factory/reviews/stage-2-r2.md).

## Stage 3 report
- Start / end (UTC): 2026-10-03 08:14 / 08:53
- Review rounds: 1 (round 1 ACCEPT). Rejections: none.
- Ledger items: 120 total (plus stages 1-2 carried forward); 116 covered by a passing probe, 4 manual (permissive/informational); known gaps: 0.
- Reviewer final check: clean build, --cpus 2 --memory 2g --network none, /health 200 in <2 s; harness isolated stage 3: stage 1 147/147, stage 2 35/35, stage 3 6/6 (stage 4 line failed, expected; claimed stage: 3); run_docker.sh --offline --stage1-ref 247843d --stage2-ref 2dcc6ae: phase A 184 pass / 0 fail / 0 skip, offline phase 154 pass / 0 fail / 3 skipped by design; own 74-check black-box script (as_of, statement paging/snapshots, corrections error ladder, known_at, conservation, concurrent corrections: exactly one 201 of 8) all correct; stage-1/ and stage-2/ untouched.
- Non-blocking reviewer notes: effective_at accepted up to 2 s past server clock (/me vs /me?as_of=now can disagree for 2 s); statement snapshots kept unbounded until reset.
- Accepted commit: cc562c1680116428e41851948619571f547e9094 (verdict fe817bb, factory/reviews/stage-3-r1.md).

