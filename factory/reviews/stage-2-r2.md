# Stage 2 review, round 2 — ACCEPT

Commit checked: `2dcc6aeec66a682ce64fd8eefc606612bf8e13b1`. It is one commit on top of 4beb1c4, with nothing rewritten.
Ledger: `factory/ledger/stage-2.md` (418d858 rows, plus the analyst's 1940007 probe mapping for S2-015/017/020/023)
and `stage-1.md`. Verification ran on a clean clone of 2dcc6ae, re-checked from the build step onward.

## 1. Blind re-derivation

Done in round 1 (`stage-2-r1.md`), and the specification is unchanged. The only ledger change since is
1940007, which adds the leaked-text probe to S2-015, S2-017, S2-020 and S2-023 and no new requirement. No gaps.

## 2. Clean build and start

`docker build stage-2` → OK. Run with `--cpus 2 --memory 2g --network none -e PORT=8080` → `/health` 200 in under 2 s.

## 3. Checks

| Command | Result |
|---|---|
| `harness run --track pocketful --repo <clean clone> --stage 2 --mode isolated --out checks/s2-reviewer-r2a` | stage 1 **147/147**, stage 2 **35/35**; stage 3 fail (expected); claimed stage: 2 |
| `factory/probes/stage-2/run_docker.sh --offline --stage1-ref 247843d`, phase A (UI, custom PORT, 2 vCPU/2 GiB, cross-instance, stage-1 upgrade) | **144 passed, 0 failed, 0 skipped**, including the new `u_quality::test_no_leaked_null_undefined_nan_text_on_routes_with_data` |
| same script, offline phase (API only) | 115 passed, 0 failed, 2 skipped (by design) |
| Own leaked-text scan (`innerText` of `/`, `/requests`, `/split`, `/authorizations`, `/login`, `/signup`; data-rich and empty accounts) | no "null", "undefined", "NaN" or "[object" on any route |
| Own Playwright pass (JPY fixture with seeded holds) | Same as round 1: available is the headline (10000 JPY) over total (12000) and held (2000); `15.5` → `pay-error` with nothing sent; resubmitting an unchanged form moves money once; a response dropped after commit → `pay-uncertain`, and the retry clears it and moves money once (11650); no horizontal scroll at 375/1280 |

## 4. Reading

The diff from 4beb1c4 to 2dcc6ae in `stage-2/` touches only `src/public/app.js`. A `put()` helper
filters `null`, `undefined` and `false` children before calling `replaceChildren`, and all former
`replaceChildren` call sites use it. It is now the only direct call. The remaining `.append` calls
pass only `h()` results or nodes. Screenshot `/requests` at 375 px: the stray "null" line is gone.
**F1 is resolved.**

Round-1 note (a) was kept deliberately: a capture with an empty body returns 400 `malformed_request`
under stage-1 §5 (an unparseable body), and the analyst's probe asserts the same. `{}` captures the
remainder. That is a defensible reading, so it does not count as a finding.

## 5. Earlier stages

`git diff 247843d 2dcc6ae -- stage-1` is empty. The stage-1 shipped checks and probes pass against stage 2.

## Decision

**ACCEPT**
