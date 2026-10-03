# Pocketful run log

## Stage 1 report
- Start / end (UTC): 2026-10-03 05:25 / 06:48
- Review rounds: 1 (round 1 ACCEPT). Rejections: none.
- Ledger items: 208 total; 199 covered by a passing probe (7 of them only by run_docker.sh), 9 manual (nothing observable); known gaps: 0.
- Reviewer final check: clean build, --cpus 2 --memory 2g --network none, /health 200 in <2 s; harness isolated stage 1 147/147 passed (stage 2 line failed, expected; claimed stage: 1); analyst probes 83/83 in docker phase A, offline phase 81 pass / 1 fail / 1 skip — the failure is probe defect S1-010 (script ignores PROBE_REPO), not a product defect.
- Non-blocking reviewer notes: signup mid-hash during a reset lands in post-reset state; crafted imported hash with huge N could make that login 500.
- Accepted commit: 247843dbcb916edd5aca24c2426b8d48a67aa41f (verdict 1f09d3d, factory/reviews/stage-1-r1.md).

