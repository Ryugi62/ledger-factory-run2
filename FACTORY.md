# FACTORY.md — Ledger Factory

A four-seat software factory for Band Desktop that builds a service from a written
specification with no human in the loop. Its one idea: **the verifier, not the generator,
is the bottleneck.** The shipped checks cover only part of each stage (9–35 % for the later
stages), and "every one of [the hidden tests] is written in the specification". So before
any code exists, one seat turns the specification into a numbered **requirement ledger**,
and the judge refuses a revision that cannot show evidence against it.

```
human ──(one task message for the whole run)──▶ @coordinator
                                                   │  full spec, pasted in parts
                       ┌───────────────────────────┤
                       ▼                           ▼
                  @analyst                    @implementer ◀──── REJECT + ledger IDs ───┐
      ledger (every normative sentence,        stage-N/ code, one commit                │
      ★ = what a minimal build skips) +        per work item, naming ledger IDs         │
      black-box probes written from the               │ full revision + requirements    │
      spec only (never from the shipped checks)       ▼                                 │
                       └──────────────────────▶  @reviewer ── ACCEPT ──▶ @coordinator ──┘
                         ledger + probe command   blind re-derivation · clean build ·
                                                  isolated checks · probes · diff reading
```

## Seats

| Seat | Harness | Model | Owns | Never does |
|---|---|---|---|---|
| coordinator | Claude Code | claude-sonnet-5-5 | dispatch, self-contained handoffs (spec pasted in numbered parts), stop rule, silence rule, stage reports in `factory/run-log.md` | write product code, accept work |
| analyst | Claude Code | claude-sonnet-5-5 | `factory/ledger/stage-N.md`, `factory/probes/stage-N/` | open the shipped checks, write product code |
| implementer | Claude Code | claude-opus-5-5 | `stage-N/` source, Dockerfile, RUN.md; commits cite ledger IDs (in practice 2–4 commits per stage, the first one large) | accept its own work, edit an accepted stage |
| reviewer | Claude Code | claude-opus-5-5 | blind re-derivation, clean build, isolated checks, probes, diff reading, `factory/reviews/stage-N-rK.md` | fix code |

Standing instructions: `mandates/<seat>.md`. They name no endpoint, field or error code;
`harness check` finds no track vocabulary in them. Everything track-specific arrived in the
single task message (first message in `room.json`).

## Design choices and what they cost

1. **Ledger before code.** The analyst quotes every normative sentence of the stage spec
   as a row with an ID, a kind and the probe that exercises it; ★ marks the rows a minimal
   build would skip. This run: **553 rows** (208 / 173 / 120 / 52; 315 ★), 532 covered by
   a passing probe, 20 manual, 1 known gap (counts from the coordinator's stage reports;
   the stage-2 ledger file itself lists 8 manual rows where the report says 7 — the file
   is authoritative, so read 165 + 8 for stage 2). Cost: the
   analyst is the most expensive seat — 53 % of the run's model spend — and the slowest at
   the start: 72 min for the stage-1 ledger.
   To hide that, the coordinator pipelines: it sends the *next* stage's ledger handoff while
   the current stage is in review. The stage-4 ledger was committed 15 min before stage 3
   closed; the stage-3 ledger landed 8 min after stage 2 closed, so the implementer waited
   8 min there. During the stage-1 ledger it waited 73 min — the price of ledger-first.
2. **Probes are independent of the shipped checks.** The analyst never opens them, so its
   probes are a second, spec-derived opinion. The implementer does read them before coding
   (06:39 in stage 1: it read the probes' assertions, then wrote the service) — that is
   test-first work against tests derived from the spec by a seat that never saw the shipped
   checks, which is the point of the design. The reviewer then re-checks with its own
   scripts, so passing the probes is never enough. They
   exercise retries, concurrent bursts, upgrades from the previous stage's export, an
   offline container with 2 vCPU / 2 GiB, and the UI through a headless browser.
3. **A reviewer that does not trust the ledger.** Before opening it, the reviewer lists
   the normative statements of the two longest sections itself and diffs them against the
   ledger. It verifies on its own clean clone, in isolated mode, and writes extra checks
   of its own. Cost: 5–20 min per round.
4. **Reviews are commits.** Every verdict, including the rejection, is in
   `factory/reviews/`, next to the code it judged.
5. **Stop rule and silence rule.** At most four review rounds per stage, then known gaps
   are recorded and the run moves on. After each handoff the coordinator waits on the git
   log (blocking waits of ≤ 10 min) and resends after 45 min of silence — see "what failed".
6. **Frozen stages.** An accepted stage folder is never edited. When stage 4 exposed
   something the accepted stage 3 cannot export (S4-045), the coordinator recorded it as a
   known gap instead of rewriting history.

## Measured run (the submitted one)

One dispatch at 2026-10-03 05:25:02 UTC; final report 09:24:27 UTC — **3 h 59 min, no
human message in between.**

| Stage | Window (UTC) | Rounds | Rejections → fix | Ledger rows (★) | Isolated harness, shipped checks | Accepted |
|---|---|---|---|---|---|---|
| 1 | 05:25–06:48 | 1 | — | 208 (91) | s1 147/147 · claimed 1 | 247843d |
| 2 | 06:48–08:14 | 2 | F1 → 2dcc6ae | 173 (92) | s1 147/147 · s2 35/35 · claimed 2 | 2dcc6ae |
| 3 | 08:14–08:53 | 1 | — | 120 (92) | + s3 6/6 · claimed 3 | cc562c1 |
| 4 | 08:53–09:24 | 1 | — | 52 (40) | + s4 5/5 · claimed 4 | 777b76d |

`harness run --all --mode isolated` on a fresh clone: **every folder claims its own stage**
(chain 1→4). The shipped checks are a portion of the graded suites; the probes ran in
addition (stage 4: 202 + 173 passing probe assertions, offline and with upgrades from stages 1–3; the 2 phase-A failures were probe defects, fixed in 1837adf).

**Model spend** (Band's catalog-estimated USD at list price, per seat for this room; the
seats ran on existing subscriptions, so the cash cost was $0): analyst $30.62 · implementer
$16.84 · reviewer $8.82 · coordinator $1.32 · **total $57.61** (Band's own total; the seat
figures are rounded). Band reports spend per seat and room, not per stage; per stage, the
time split below is the best proxy. About $14.4 per accepted stage. Rehearsals cost another
$12.80 (toy run $8.27, aborted first run $4.53).

Where the time went (UTC, from commits and room timestamps):

| Stage | Ledger (analyst) | Build (implementer) | Review (reviewer) |
|---|---|---|---|
| 1 | 05:25–06:37 · 72 min | 06:38–06:44 · 6 min | 06:44–06:48 · 4 min |
| 2 | 06:38–07:16 · 38 min (pipelined) | 07:17–07:46 · 29 min + fix 08:01–08:07 | r1 07:53–08:01 · r2 08:07–08:14 |
| 3 | → 08:22 (pipelined) | 08:23–08:34 · 11 min | 08:34–08:52 · 18 min |
| 4 | → 08:37 (ready before stage 3 closed) | 08:53–09:04 · 11 min | 09:04–09:24 · 20 min |

Work split (git): implementer 14 commits / 9,030 lines, analyst 9 / 15,013 (ledgers and
probes), reviewer 5 verdicts, coordinator 4 reports. The human (Taegeol Kim, git author
"Ryugi62 (human)", "Taegeol Kim" in the room) made one commit before the run (mandates,
license) and commits after it (this file, README, `room.json`, `factory/setup/`). Nothing
under `stage-N/` was written by the human.

Genericity, measured: the same four mandates (an earlier revision — the diff since is the
duplicate-handoff, review-commit, pipelining and silence rules) first ran the organizers'
unrelated `toy` counter track; stages 1 and 2 were accepted there before its Codex seats
hit their usage limit. Only the task message differed.

## How the factory caught bad work in this run

| What | Found by | Evidence | Fixed by |
|---|---|---|---|
| `/requests` rendered a literal `null` whenever the user had requests (ledger S2-015, ★S2-020). All shipped checks and all probes passed. | reviewer, own Playwright session | `factory/reviews/stage-2-r1.md` (REJECT on 4beb1c4) | implementer 2dcc6ae; analyst added a leaked-text probe for every route (1940007) |
| Two stage-1 edge cases: a signup that is mid-hash during a reset lands in the new state; an imported password hash with an extreme cost could make that login fail | reviewer, diff reading (stage 1) | `stage-1-r1.md` | implementer `d4c51d3`, in stage 2 (confirmed in `stage-2-r1.md`) |
| A probe script ignored the stage folder and failed on a correct build | reviewer | `stage-1-r1.md` | analyst 169cac5 |
| Upgrade probes compared payments without ignoring fields later stages add | reviewer | `stage-4-r1.md` | analyst 1837adf |
| Stage 3 accepted a correction `effective_at` up to 2 s in the future | reviewer, non-blocking note | `stage-3-r1.md` | implementer `3a3b3e3`, in stage 4 (strict against the service clock; confirmed in `stage-4-r1.md`) |
| Capture with an empty body returns 400 while pay treats it as `{}` | reviewer, non-blocking note | `stage-2-r1.md` | **dropped — a teamwork miss.** Nobody owned non-blocking notes, so this one was never decided. Rule we would add: every non-blocking note becomes a ledger row of the next stage, so it is either fixed or explicitly waived |
| Stage-3 exports cannot carry statement snapshots that stage 4 wants (S4-045) | analyst + reviewer | `stage-4-r1.md` | recorded as a known gap; stage 3 stays frozen — see below |

Two seats addressing each other, both directions (from `room.json`):
`07:53:04 implementer → @reviewer "REVIEW HANDOFF stage 2 (Pocketful), part 1/4 …"` ·
`08:01:36 reviewer → @implementer @coordinator "Stage 2 round 1: REJECT …"` ·
`08:07:01 implementer → @reviewer "REVIEW HANDOFF stage 2 round 2 …"` ·
`08:14:17 reviewer → @implementer @coordinator "Stage 2 round 2: ACCEPT."`

**S4-045 is a factory miss, not just a gap.** No stage-3 ledger row said "an export carries
every piece of state the service holds", so nobody checked that statement snapshots were
exported. The coordinator decided — inside the band, in its own message, with no human
input — not to edit the accepted stage-3 folder. The hidden stage-4 suite may well test
this. The rule we would add: the analyst writes one ledger row per stateful entity for
export/import, and the reviewer checks exports field by field against the live state.

### Audit: the one look at a shipped test
Timeline from `room.json`: 07:42:36 a shipped stage-2 check (`/login`, `/signup` reachable
when signed in) failed in the implementer's harness run (the failing log names the test); 07:42:39 the implementer ran
`grep -A25 "def test_routes_are_directly_navigable" …/test_sample.py`; 07:46 commit
`4beb1c4` "/login and /signup stay directly navigable when signed in (S2-008, S2-009,
S2-029)". The diff (+22/−12 in `app.js`, `app.css`) makes the two routes show their form
inside the signed-in layout instead of redirecting; it contains no fixture value, handle,
test name or test-only branch. The rows it cites: S2-008/S2-009 — the route table lists
`/signup` and `/login` as routes of the product — and ★S2-029 — "`current-user` | Visible
on every screen when signed in". A search of all 1,555 room messages finds no other tool
call that opened a shipped test file; the analyst never did. **We concede the point:** this
one behaviour (a signed-in user opening `/login` and `/signup` sees the form) was learned
from a shipped test. The ledger listed both routes but no row said what a signed-in visitor
sees there, and the implementer went to the test instead of sending the gap to the analyst.
The fix is general product behaviour, not a branch for the test — but the right path was
"failing log → probe gap → analyst", and the implementer mandate should say "failure logs
only, never test source".

The reviewer, too, bent its mandate once. In stage 4 two upgrade probes failed. It patched
the two comparison helpers in a scratch copy to confirm the failures were probe defects
(both compared payments without ignoring the `refund_of: null` field stage 4 adds), asked
the analyst to fix them, and accepted at 09:24. The analyst's fix (`1837adf`) landed at
09:13, but the verdict cites the scratch run, not a rerun on the repository version — the
mandate says not to accept with a failing check. Its blind lists and own check scripts,
left outside the repository during the run, are archived unchanged in
`factory/reviews/extra/` (copied by the human after the run).

## What we tried that failed

- **Two model providers.** The first submitted-run attempt used Codex for the analyst and
  reviewer, so the judge would not share the builder's blind spots. 21 minutes in, the
  analyst hit its ChatGPT plan's usage limit; its turn ended without a word, and the
  coordinator waited forever. We discarded that room and repository, moved every seat to
  Claude Code, and added the silence rule. Independence now comes from the mandates (blind
  re-derivation, own clean clone, own checks) rather than from a second provider.
- **Duplicate review handoffs** (toy rehearsal): the implementer and the coordinator both
  briefed the reviewer. The coordinator now only confirms the commit when the
  implementer's handoff is complete.
- **Review evidence outside the repository** (toy rehearsal): the reviewer wrote its
  verdicts to scratch files. Verdicts are now commits.
- **Forbidding the ask-the-human tool** on headless Claude Code seats: Band refuses to
  start the runtime (the tool is part of Band's control channel). Autonomy is enforced by
  the mandates; no seat asked.
- **Bare Claude Code context** needs an API key; the seats use the local login with only
  Band's own MCP server allowed (`--claude-strict-mcp-config`), so no personal tools leak in.
- The implementer once read a shipped sample test to understand a failing check; the
  resulting commit (4beb1c4) cites spec rows S2-008/009/029. Reading the failing log is
  what the participant guide expects; we would tighten this to "logs only" next time.

## Stand it up yourself

1. Band Desktop 0.4.12+, signed in; a Docker daemon; Python 3.12 for the event harness;
   Claude Code signed in. Identity used here: Band handle `xorjf1027`.
2. Create the seats — `factory/setup/create-seats.sh` (one `band agent create` per seat,
   instructions linked live to `mandates/<seat>.md`, `--claude-permission-mode
   bypassPermissions` so no seat waits for an approval).
3. Create a room with the four seats and yourself — `factory/setup/new-room.sh`.
4. Send @coordinator one message: the spec file paths, the result repository, folder rules
   and the check command (ours is the first message in `room.json`). Send nothing else.
5. When the coordinator posts its run summary: Band console → Sessions → the room →
   ⋮ → Download → Download full session → save as `room.json`.

Point the same mandates at another problem by changing only that one message.

## Hidden-test misses we expect

The factory's own prediction, per stage, of where the hidden suites may fail it:
- Stage 1–2: little expected — 79 % and 35 % of checks shipped, all green, plus 83 and 144 docker-phase probe
  assertions. Possible: capture with an empty body (400, not treated as `{}`).
- Stage 3 (9 % shipped): edge cases of statement snapshot paging and `known_at` views
  beyond what our 154–184 probe assertions cover.
- Stage 4 (16 % shipped): **S4-045** — snapshot tokens issued by the stage-3 service do not
  survive its export → import into stage 4. Most likely a real loss.

## Genericity evidence

`factory/genericity/`: the toy-track rehearsal's git log and the diff of the mandates since
that run (78 lines: harness/model lines, the duplicate-handoff rule, review commits,
pipelining, the silence rule). The same mandates built the organizers' unrelated counter
service through two accepted stages. The word "ledger" is the factory's own term, chosen
before the track was picked; `harness check` finds no track vocabulary.

## Code map (stage-4, for a maintainer)

`stage-4/src/server.js` (1,748 lines, Node.js standard library only) is one module in
sections: errors (26) · helpers: microsecond clock, RFC 3339, canonical JSON, field rules
(41) · passwords, scrypt (181) · state and payment revision history (222–416) · bitemporal
history: balances as of / known at, holds, overdraft checks, statements (417–524) · reset
fixture validation (525) · export / import across stages 1–4 (687) · HTTP plumbing and the
idempotent-write wrapper (907–1039) · operations: payments, requests, splits,
settlements, authorizations (1040) · history endpoints (1321) · corrections, refunds,
batches (1411–1550) · auth (1551) · routing and HTML/JSON negotiation (1608). Every write
runs synchronously on the event loop, which is the whole concurrency story. The UI is
`src/public/app.js` (814 lines) + `app.css` (229, design tokens), unchanged since stage 2:
pocketful stages 3 and 4 specify API behaviour only. The regression suite is
`factory/probes/stage-1..4/`; there are no unit tests inside the stage folders — a cost of
letting the band choose one-file services. To re-run it against a stage:
`python3 factory/probes/stage-N/run_all.py http://127.0.0.1:8080` (service running), or
`factory/probes/stage-N/run_docker.sh --offline` to build the folder and test it in an
offline 2 vCPU / 2 GiB container. To add an endpoint: add the ledger rows, a probe, a
handler in the operations section and a route in the routing section — commit `3a3b3e3`
(refunds and correction batches) is a worked example.

## Limitations

- Green shipped checks are not proof: stages 3 and 4 ship 6 and 5 checks. The ledger and
  probes are our best attempt at the rest. Known gaps: S4-045 (stage-3 snapshot export) and
  empty-body capture returning 400.
- One file per service: fast for the band, harder for a human maintainer (see code map).
- The ledger is two readings of the spec (analyst, then the reviewer's blind sample). A
  sentence both misread would pass.
- Implementer and reviewer run the same model after the provider fallback.
- The analyst dominates time and cost; a cheaper ledger (★ rows only) would halve stage 1
  but give the reviewer less to hold the build to.
- UI quality is judged by the reviewer and headless-browser probes, not by a human designer.
