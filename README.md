# Ledger Factory — Dark Factory hackathon, `pocketful` track

**Team:** Ledger Factory (solo — Taegeol Kim, GitHub [@Ryugi62](https://github.com/Ryugi62))
**Track:** `pocketful` — a wallet and payments app
**Factory:** four Band Desktop seats — coordinator, analyst, implementer, reviewer — that
turn each specification into a numbered requirement ledger *before* any code is written,
and reject any build that cannot show evidence for it.

## Result at a glance

| Folder | Isolated-mode harness (shipped checks) | Review rounds | Accepted commit |
|---|---|---|---|
| `stage-1/` | suite 1 147/147 · claims stage 1 | 1 | `247843d` |
| `stage-2/` | suites 1–2 147/147 · 35/35 · claims stage 2 | 2 (one REJECT) | `2dcc6ae` |
| `stage-3/` | suites 1–3 147/147 · 35/35 · 6/6 · claims stage 3 | 1 | `cc562c1` |
| `stage-4/` | suites 1–4 147/147 · 35/35 · 6/6 · 5/5 · claims stage 4 | 1 | `777b76d` |

`harness run --all --mode isolated` on a fresh clone: every folder claims its own stage.
The submitted run took **3 h 59 min** from one dispatch to the coordinator's final report,
with a model spend of **$57.61** (Band's list-price estimate; $0 cash — existing
subscriptions). The shipped checks are only part of the graded suites; see `FACTORY.md` for
the 553-row requirement ledger and probes the band built to cover the rest. Known gaps we
expect the hidden suites may find: S4-045 (statement snapshots issued by the stage-3 service
are not carried through its export) and capture with an empty body (400 instead of `{}`).
Stages 3 and 4 of pocketful specify API behaviour only, so the UI is the stage-2 UI.

One human message started the submitted run (`room.json`, first message). Nothing was sent
to the room after it.

## How to read this repository

| Path | What it is |
|---|---|
| `FACTORY.md` | The factory: seats, design choices and their cost, measured time and spend, how it catches bad work, how to stand it up |
| `mandates/` | One standing instruction per seat (`coordinator`, `analyst`, `implementer`, `reviewer`), each naming its harness and model. Generic — no track vocabulary |
| `room.json` | The Band room of the submitted run, downloaded unchanged (Download full session) |
| `stage-N/` | The service as it stood when stage N was accepted. Each folder builds on its own: see its `RUN.md` |
| `factory/ledger/stage-N.md` | The analyst's requirement ledger for stage N (★ = what a minimal build skips) |
| `factory/probes/stage-N/` | Black-box probes written from the spec only, never from the shipped checks |
| `factory/reviews/stage-N-rK.md` | Every review verdict, committed by the reviewer — including the rejections |
| `factory/run-log.md` | The coordinator's per-stage reports |
| `factory/setup/` | The two scripts that create the seats and the room |

## Run a stage

```sh
cd stage-4 && docker build -t pocketful-stage-4 . && docker run --rm -e PORT=8080 -p 8080:8080 pocketful-stage-4
# open http://localhost:8080/signup to create an account (new accounts start at 0),
# or seed users with POST /_test/reset (see stage-1 spec fixture) and open /login
```

Every commit under `stage-N/` was made by a seat in the room (git authors `analyst`,
`implementer`, `reviewer`, `coordinator`). The human — Taegeol Kim, git author
"Ryugi62 (human)" — committed the mandates and license before the run, and this README,
`FACTORY.md`, `room.json` and `factory/setup/` after it.

License: MIT.
