# Stage 2 review, round 1 — REJECT

Commit checked: `4beb1c45b263a6e6be966e9ce70361d7bc9ac3e3`. Ledger: `factory/ledger/stage-2.md` at 418d858,
plus `stage-1.md`. Verification ran on a clean clone of 4beb1c4.

## 1. Blind re-derivation

Before opening the ledger, I listed the normative statements of the two longest sections myself:
"API" (§ GET /me, POST /authorizations, capture, void, GET /authorizations) and
"Authorizations and captures". That gave 29 statements. Each maps to S2-086..S2-153 (for example,
`authorization_id: null` on ordinary payments maps to S2-127, and `remaining_amount` maps to S2-135).
**No ledger gaps.**

## 2. Clean build and start

- `docker build stage-2` → OK. Run with `--cpus 2 --memory 2g --network none -e PORT=8080` → `/health` 200 in under 2 s.

## 3. Checks

| Command | Result |
|---|---|
| `harness run --track pocketful --repo <clean clone> --stage 2 --mode isolated --out checks/s2-reviewer-r1a` | stage 1 **147/147**, stage 2 **35/35** (both against the stage-2 service); stage 3 fail (expected); claimed stage: 2 |
| `factory/probes/stage-2/run_docker.sh --offline --stage1-ref 247843d`, phase A (UI, PORT=9237, 2 vCPU/2 GiB, cross-instance, stage-1 → stage-2 upgrade) | **143 passed, 0 failed, 0 skipped** |
| same script, offline phase (API only) | 115 passed, 0 failed, 2 skipped (upgrade and cross-instance, skipped by design) |
| Own Playwright session (JPY fixture with seeded holds) | headline `wallet-available` 10000 JPY, total 12000 JPY, held 2000 JPY; `15.5` in JPY → `pay-error` with no request sent; resubmitting an unchanged form moved money once; a response dropped after commit → `pay-uncertain` shown with no `pay-error`, and the retry cleared both and moved money once (12000 − 100 − 250 = 11650); `scrollWidth` equals the viewport at 375 and 1280 on `/`, `/requests`, `/split` and `/authorizations` |

## 4. Reading

The API diff from stage 1 is sound. Every hold, capture and void runs synchronously. Expiry is
applied lazily on every read and write path (`refreshAuth` in `heldOf`, `authView`, the list,
capture and void). `insufficient_funds` is evaluated against `available` on payments, requests and
settlements. Stage-1 exports import with no authorizations and the default TTL. Both stage-1 review
notes are fixed: signup and login now bind to the state they started in, and imported scrypt cost is
bounded and cannot throw.

### Finding F1 — the literal text "null" is rendered on `/requests` (S2-015, ★ S2-020)

- Requirements: S2-015 "The browser experience must feel like a coherent, presentation-ready
  consumer finance product, not a test harness with controls attached." S2-020 ★ "Format people,
  amounts and timestamps for people first; expose technical identifiers only where they help the user."
- Observed: whenever the caller has at least one request, `/requests` shows a bare line reading
  `null` between the page intro and the "Asked of you" card. Reproduced at 375 px and 1280 px.
  `document.body.innerText` contains "null" on `/requests` and on no other route.
- Cause: `stage-2/src/public/app.js:463`
  `body.replaceChildren(!incoming.length && !outgoing.length ? h(...) : null, h('div', …))`.
  `Element.replaceChildren` turns `null` into a text node "null". The `h()` helper filters nulls,
  but `replaceChildren` does not.
- Reproduce: reset with any request involving the user, sign in, open `/requests`, and read the text
  under "Money people have asked you for, and money you have asked for."
- This is the most common state of a required route, so it is visibly unmet. No probe catches it
  (probe gap for the analyst: assert no "null"/"undefined"/"NaN" in `innerText` on every route with data).

Non-blocking observations:
- `POST /authorizations/{id}/capture` with an empty body returns 400, but `POST /requests/{id}/pay`
  treats an empty body as `{}`. The spec says `amount` is optional. Accepting an empty body as `{}`
  would be the safer reading, and it would be consistent with pay.
- The authorise form exists only on `/authorizations`. The spec does not pin its route, so this is
  acceptable. It is reachable through the "Holds" navigation.
- A UI capture uses a fresh key per click, so a lost capture response cannot be retried
  idempotently. The default final capture closes the hold, so no money can move twice. Not required by the spec.

## 5. Earlier stages

`git diff 247843d 4beb1c4 -- stage-1` is empty, so `stage-1/` is unchanged since it was accepted.
The stage-1 harness checks pass against the stage-2 service.

## Decision

**REJECT**. Fix F1, then hand off a new commit for a full re-check.
