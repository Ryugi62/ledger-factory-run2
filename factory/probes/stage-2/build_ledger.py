#!/usr/bin/env python3
"""Render factory/ledger/stage-2.md from ledger_rows.txt and the probes' @test(...) ids.

    python3 factory/probes/stage-2/build_ledger.py
"""
import collections
import glob
import importlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lib2  # noqa: E402,F401
import lib   # noqa: E402

OUT = os.path.abspath(os.path.join(HERE, "..", "..", "ledger", "stage-2.md"))
KINDS = ["behaviour", "error", "concurrency", "idempotency", "time", "data-migration", "UI-state", "limit"]

CHANGED = [
    ("S1-004", "Only the HTTP API is required.", "Superseded: stage 2 adds a browser UI (S2-004 … S2-085, S2-154 … S2-170)."),
    ("S1-005", "sum of wallet balances equals the seeded total", "Now the sum of `total` values; holds move no money (S2-090)."),
    ("S1-006", "no wallet balance may be negative, including transiently", "Extended: `available = total − held` is never negative either, at every read (S2-091, S2-112, S2-171)."),
    ("S1-012/S1-013/S1-021", "everything inside the single container, no outbound network", "Extends to UI assets (fonts, scripts, styles): the browser must load nothing from outside (S2-015, S2-023)."),
    ("S1-027, S1-184", "reset clears all state", "Also authorizations, holds and `authorization_ttl_seconds` (S2-173)."),
    ("S1-062", "negative fixture balance is a reset error", "Also: seeded unexpired open holds above a user's balance, and an invalid `authorization_ttl_seconds` (S2-104, S2-101)."),
    ("S1-066", "fixture keys may be omitted", "`authorizations` (default empty) and `authorization_ttl_seconds` (default 600) join them (S2-100, S2-106)."),
    ("S1-092, S1-093", "every other endpoint requires a bearer token", "`GET /requests` and `GET /authorizations` serve the HTML UI for `Accept: text/html` without a token; JSON (401 without token) otherwise (S2-012, S2-169)."),
    ("S1-096, S1-203", "five idempotent write paths", "Seven: + `POST /authorizations` and `POST /authorizations/{id}/capture` (S2-099)."),
    ("S1-108", "GET /me fields", "Adds `total`, `available`, `held`; `balance == total` (S2-093, S2-111, S2-112)."),
    ("S1-110, S1-199", "payment object shape", "Adds `authorization_id` (null unless created by a capture) (S2-127)."),
    ("S1-111, S1-130, S1-195, S1-196", "`409 insufficient_funds` on payments, request pay and settlements", "Evaluated against `available` (settlement net debits too) (S2-096)."),
    ("S1-171 … S1-184, S1-204", "export / import", "State must also carry authorizations, holds, captures, TTL and authorize/capture receipts; a stage-1 export must be accepted (S2-080, S2-172)."),
]


def parse_rows():
    rows, cur = [], None
    for line in open(os.path.join(HERE, "ledger_rows.txt"), encoding="utf-8"):
        line = line.rstrip("\n")
        if line.startswith("== "):
            f = [x.strip() for x in line[3:].split("|")]
            cur = {"id": f[0], "section": f[1], "kind": f[2], "star": f[3] == "★", "quote": "", "notes": [],
                   "manual": None, "script": None}
            rows.append(cur)
        elif cur is None or line.startswith("#") or not line:
            continue
        elif line.startswith("> "):
            cur["quote"] = line[2:]
        elif line.startswith("~ "):
            cur["notes"].append(line[2:])
        elif line.startswith("! "):
            cur["manual"] = line[2:]
        elif line.startswith("@ "):
            cur["script"] = line[2:]
    return rows


def esc(s):
    return s.replace("|", "\\|")


def main():
    for path in sorted(glob.glob(os.path.join(HERE, "q_*.py"))) + sorted(glob.glob(os.path.join(HERE, "u_*.py"))):
        importlib.import_module(os.path.basename(path)[:-3])
    rows = parse_rows()
    by_id = collections.defaultdict(list)
    for fn, ids in lib.TESTS:
        for i in ids:
            if i.startswith("S2-"):
                by_id[i].append("%s::%s" % (fn.__module__, fn.__name__))
    known = {r["id"] for r in rows}
    unknown = [i for i in by_id if i not in known]
    if unknown:
        raise SystemExit("probes cite unknown ledger ids: %s" % unknown)
    kinds = collections.Counter(r["kind"] for r in rows)
    stars = sum(1 for r in rows if r["star"])
    out = []
    w = out.append
    w("# Stage 2 requirement ledger — wallet screens and payment authorizations")
    w("")
    w("Source: stage-2 specification (parts 1-2) plus, by reference, every row of `factory/ledger/stage-1.md`.")
    w("Probes: `factory/probes/stage-2/` — API probes `q_*.py`, browser probes `u_*.py` (Playwright / headless Chromium), "
      "and the stage-1 suite `../stage-1/p_*.py`, all started by one command:")
    w("")
    w("    python3 factory/probes/stage-2/run_all.py http://127.0.0.1:8080")
    w("")
    w("Container level: `factory/probes/stage-2/run_docker.sh [--offline] [--stage1-ref <git-ref>]`. "
      "This file is generated: edit `ledger_rows.txt`, re-run `build_ledger.py`.")
    w("")
    w("## Everything earlier still holds")
    w("")
    w("Row **S2-001/S2-003**: every row of `factory/ledger/stage-1.md` (S1-001 … S1-208) continues to apply, probed by the unchanged "
      "stage-1 suite (run_all.py executes it as part of stage 2) — except where the table below says the text changed.")
    w("")
    w("## Earlier rows changed by stage 2")
    w("")
    w("| earlier row(s) | what it said | change in stage 2 |")
    w("|---|---|---|")
    for a, b, c in CHANGED:
        w("| %s | %s | %s |" % (esc(a), esc(b), esc(c)))
    w("")
    w("## Counts (stage-2 rows)")
    w("")
    w("| kind | rows |")
    w("|---|---|")
    for k in KINDS:
        w("| %s | %d |" % (k, kinds.get(k, 0)))
    w("| **total** | **%d** |" % len(rows))
    w("")
    w("★ rows (easy to miss: implied consequences, cross-section interactions, defaults, boundaries): **%d**" % stars)
    w("")
    manual = [r for r in rows if r["manual"]]
    w("Rows marked manual (nothing observable to assert): %d. Rows covered by the suite/UI probes as a group: %d."
      % (len(manual), len([r for r in rows if r["script"]])))
    w("")
    w("## Ambiguities and the reading chosen")
    w("")
    for rid, text in [
        ("S2-012", "A browser navigation (`Accept: text/html`, no way to send a bearer token) gets the HTML UI with 200; everything else, including `*/*`, gets JSON."),
        ("S2-101", "`authorization_ttl_seconds` of 0, negative, fractional, string or boolean is a reset error (422)."),
        ("S2-139", "Capture on an authorization that expired by the clock answers `authorization_expired`; a seeded status `expired` may answer either code."),
        ("S2-148", "Capture/void by a non-permitted party — including strangers — is exactly 403 (unlike stage-1 requests)."),
        ("S2-150", "`GET /authorizations` returns `{\"authorizations\": [...], \"has_more\": bool}` and strict reverse creation order."),
        ("S2-141", "A non-boolean `final` is a wrong JSON type: 400 `malformed_request`; a bad `amount` is 422."),
        ("S2-137", "Capture-body equality stays \"same JSON value\": `{}`, `{amount}`, `{amount, final:true}`, `{amount, final:false}` are all different bodies."),
        ("S2-157", "The authorise form may be on `/` or on `/authorizations`; the probes look on both."),
        ("S2-081", "Browser stays signed in across an export/import cycle taken between its actions (the stage-1 service has no UI, so the cross-version case is probed at API level)."),
        ("S2-080", "Needs a running stage-1 service (`PROBE_STAGE1_BASE_URL`, built by `run_docker.sh --stage1-ref`); SKIP otherwise."),
        ("S2-015…S2-023", "Product-quality requirements are probed through objective proxies (overflow at 375/768/1280, labels, focus, contrast, distinct state styles, loading/error/empty states, no raw ids, no external assets); screenshots are saved for a human or model reviewer."),
        ("S2-050", "Notes are compared by exact textContent (no template whitespace around them); amounts and handles are compared after trimming."),
    ]:
        w("- **%s** — %s" % (rid, text))
    w("")
    w("## Ledger")
    w("")
    w("| ID | section | requirement (verbatim) | kind | ★ | probe | reading / note |")
    w("|---|---|---|---|---|---|---|")
    for r in rows:
        cell = ["`%s`" % p for p in by_id.get(r["id"], [])]
        if r["script"]:
            cell.append("`%s`" % r["script"])
        if r["manual"]:
            cell.append("manual — %s" % r["manual"])
        if not cell:
            raise SystemExit("row %s has no probe and no manual reason" % r["id"])
        w("| %s | %s | %s | %s | %s | %s | %s |" % (
            r["id"], esc(r["section"]), esc(r["quote"]), r["kind"], "★" if r["star"] else "",
            "<br>".join(esc(c) for c in cell), esc(" ".join(r["notes"]))))
    w("")
    w("## Probe index")
    w("")
    w("| probe | ledger rows |")
    w("|---|---|")
    for fn, ids in lib.TESTS:
        if any(i.startswith("S2-") for i in ids):
            w("| `%s::%s` | %s |" % (fn.__module__, fn.__name__, ", ".join(i for i in ids if i.startswith("S2-"))))
    w("")
    w("## Probe design notes")
    w("")
    w("- Every probe resets the service to its own fixture; each browser probe uses a fresh context (no shared storage) and "
      "signs in through the real login form. Network interception (Playwright routes) simulates lost responses, "
      "delayed/out-of-order reads and stubbed failures; the real server is used for everything else.")
    w("- Time-based probes use `authorization_ttl_seconds` of 2-4 s and wait past `expires_at` + 1.3 s, with no request at the "
      "deadline, so both the read path and the write path must notice expiry on their own.")
    w("- Concurrency probes: authorize/pay bursts against one balance, concurrent captures (same key / different keys / "
      "final / non-final), capture vs void, payments vs capture, and a mixed storm with a sampler that checks every `/me` read "
      "(`balance == total`, `available == total − held`, nothing negative) plus a final ledger reconciliation from each user's own feed.")
    w("- Upgrade: stage-2 state export/import preserves authorizations, holds, TTL and receipts; a stage-1 export is imported "
      "when `PROBE_STAGE1_BASE_URL` is given. Browser upgrade probe: lost-response payment, export, reset to another fixture, import, "
      "retry from the same unchanged form without reloading.")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    open(OUT, "w", encoding="utf-8").write("\n".join(out) + "\n")
    print("wrote", OUT, "rows=%d stars=%d" % (len(rows), stars))
    print(dict(kinds))


if __name__ == "__main__":
    main()
