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
| implementer | Claude Code | claude-opus-5-5 | `stage-N/` source, Dockerfile, RUN.md; commits cite ledger IDs | accept its own work, edit an accepted stage |
| reviewer | Claude Code | claude-opus-5-5 | blind re-derivation, clean build, isolated checks, probes, diff reading, `factory/reviews/stage-N-rK.md` | fix code |

Standing instructions: `mandates/<seat>.md`. They name no endpoint, field or error code;
`harness check` finds no track vocabulary in them. Everything track-specific arrived in the
single task message (first message in `room.json`).

## Design choices and what they cost

1. **Ledger before code.** The analyst quotes every normative sentence of the stage spec
   as a row with an ID, a kind and the probe that exercises it; ★ marks the rows a minimal
   build would skip. This run: **553 rows** (208 / 173 / 120 / 52), 532 covered by a
   passing probe, 20 manual, 1 known gap. Cost: the analyst is the slowest and most
   expensive seat — 72 min and 53 % of the run's model spend — and it starts every stage.
   To hide that, the coordinator pipelines: it sends the *next* stage's ledger handoff while
   the current stage is in review: the stage-4 ledger was ready before stage 3 closed, and stage 3 waited 8 min.
2. **Probes are independent of the shipped checks.** The analyst never opens them, so its
   probes are a second opinion the implementer cannot fit code to by accident. They
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
| 1 | 05:25–06:48 | 1 | — | 208 (93) | s1 147/147 · claimed 1 | 247843d |
| 2 | 06:48–08:14 | 2 | F1 → 2dcc6ae | 173 | s1 147/147 · s2 35/35 · claimed 2 | 2dcc6ae |
| 3 | 08:14–08:53 | 1 | — | 120 | + s3 6/6 · claimed 3 | cc562c1 |
| 4 | 08:53–09:24 | 1 | — | 52 | + s4 5/5 · claimed 4 | 777b76d |

`harness run --all --mode isolated` on a fresh clone: **every folder claims its own stage**
(chain 1→4). The shipped checks are a portion of the graded suites; the probes ran in
addition (stage 4: 202 + 173 passing probe assertions, offline and with upgrades from stages 1–3; the 2 phase-A failures were probe defects, fixed in 1837adf).

**Model spend** (Band's catalog-estimated USD at list price; the seats ran on existing
subscriptions, so the cash cost was $0): analyst $30.62 · implementer $16.84 · reviewer
$8.82 · coordinator $1.32 · **total $57.61** for the submitted run. Rehearsals cost another
$12.80 (toy run $8.27, aborted first run $4.53).

Work split (git): implementer 14 commits / 9,030 lines, analyst 9 / 15,013 (ledgers and
probes), reviewer 5 verdicts, coordinator 4 reports. The human made one commit before the
run (mandates, license) and one after it (this file, README, `room.json`).

## How the factory caught bad work in this run

| What | Found by | Evidence | Fixed by |
|---|---|---|---|
| `/requests` rendered a literal `null` whenever the user had requests (ledger S2-015, ★S2-020). All shipped checks and all probes passed. | reviewer, own Playwright session | `factory/reviews/stage-2-r1.md` (REJECT on 4beb1c4) | implementer 2dcc6ae; analyst added a leaked-text probe for every route (1940007) |
| Two stage-1 edge cases: a signup that is mid-hash during a reset lands in the new state; an imported password hash with an extreme cost could make that login fail | reviewer, diff reading (stage 1) | `stage-1-r1.md` | implementer, in stage 2 (confirmed in `stage-2-r1.md`) |
| A probe script ignored the stage folder and failed on a correct build | reviewer | `stage-1-r1.md` | analyst 169cac5 |
| Upgrade probes compared payments without ignoring fields later stages add | reviewer | `stage-4-r1.md` | analyst 1837adf |
| Stage-3 exports cannot carry statement snapshots that stage 4 wants (S4-045) | analyst + reviewer | `stage-4-r1.md` | recorded as a known gap; stage 3 stays frozen |

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
   Claude Code signed in.
2. Create the seats — `factory/setup/create-seats.sh` (one `band agent create` per seat,
   instructions linked live to `mandates/<seat>.md`, `--claude-permission-mode
   bypassPermissions` so no seat waits for an approval).
3. Create a room with the four seats and yourself — `factory/setup/new-room.sh`.
4. Send @coordinator one message: the spec file paths, the result repository, folder rules
   and the check command (ours is the first message in `room.json`). Send nothing else.
5. When the coordinator posts its run summary: Band console → Sessions → the room →
   ⋮ → Download → Download full session → save as `room.json`.

Point the same mandates at another problem by changing only that one message.

## Limitations

- Green shipped checks are not proof: the hidden suites decide. The ledger and probes are
  our best attempt at them, and one known gap (S4-045) is open.
- The ledger is two readings of the spec (analyst, then the reviewer's blind sample). A
  sentence both misread would pass.
- Implementer and reviewer run the same model after the provider fallback.
- The analyst dominates time and cost; a cheaper ledger (★ rows only) would halve stage 1
  but give the reviewer less to hold the build to.
- UI quality is judged by the reviewer and headless-browser probes, not by a human designer.
