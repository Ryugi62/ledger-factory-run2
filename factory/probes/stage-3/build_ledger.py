#!/usr/bin/env python3
"""Render factory/ledger/stage-3.md from ledger_rows.txt and the probes' @test(...) ids.

    python3 factory/probes/stage-3/build_ledger.py
"""
import collections
import glob
import importlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lib3  # noqa: E402,F401
import lib   # noqa: E402

OUT = os.path.abspath(os.path.join(HERE, "..", "..", "ledger", "stage-3.md"))
KINDS = ["behaviour", "error", "concurrency", "idempotency", "time", "data-migration", "UI-state", "limit"]

CHANGED = [
    ("S1-108, S2-111, S2-112", "GET /me fields; balance equals total; available = total - held", "Adds optional `as_of` and `known_at` (echoed exactly); the four money fields describe one historical view; plain GET /me reports current corrected values (S3-012 … S3-019, S3-066 … S3-073, S3-097)."),
    ("S1-110, S1-199, S2-127", "payment object shape", "Same keys. `created_at` is now specified as the instant the money moved and may be supplied by seeded payments (S3-006 … S3-009); in a statement entry `payment.amount` is the selected revision's amount (S3-076)."),
    ("S1-157, S1-158", "GET /activity newest first by created_at", "Retained, and corrections never change what the feed shows: original amount, original order, no new items (S3-008, S3-061, S3-062)."),
    ("S1-061, S1-065, S1-066", "fixture `balance` is the balance after all seeded payments; fixture payment format; omittable keys", "Seeded payments may carry `created_at`; the balance is still the ending balance; opening balance = ending balance - net effect of seeded payments (S3-009, S3-011, S3-036)."),
    ("S1-062", "negative fixture balance is a reset error", "Also: a seeded payment `created_at` in the future (S3-010)."),
    ("S1-005, S2-090", "sum of balances / totals equals the seeded total", "Now also in every historical view, for every (as_of, known_at) (S3-060, S3-120)."),
    ("S1-006, S2-091", "no balance negative; available never negative", "Extended to every effective-time boundary and past hold interval under the latest known revisions: corrections that would violate it are `409 historical_overdraft` (S3-057, S3-058, S3-108)."),
    ("S1-111, S1-130, S2-096", "`409 insufficient_funds` against `available`", "Also for the debit a correction causes (increase: sender, decrease: receiver) and it takes precedence over historical_overdraft (S3-056, S3-109)."),
    ("S1-096, S1-203, S2-099", "seven idempotent write paths", "Eight: + `POST /payments/{id}/corrections` (S3-040, S3-115 … S3-117)."),
    ("S1-110, S1-199, S1-200", "settlement members: ordinary payments with settlement_id, created_at = committed_at", "Members additionally have revision 1 with effective_at = recorded_at = committed_at and cannot be corrected (S3-092, S3-093)."),
    ("S2-126, S2-134", "captures are payments created by `POST /authorizations/{id}/capture`", "Captures are immutable linked payments (correction is 422 linked_payment_immutable) and appear exactly once in statements (S3-096, S3-113)."),
    ("S2-115, S2-135", "authorization object fields", "Adds `closed_at` (null while open, event time when closed; expired: expires_at) (S3-106)."),
    ("S2-102, S2-107, S2-110", "seeded authorizations", "Seeded open holds are created at reset unless `created_at` is supplied; this matters only for historical views (S3-110)."),
    ("S1-027, S1-184", "reset clears all state", "Also snapshots: a snapshot token from before a reset is 404 (S3-084)."),
    ("S1-171 … S1-184, S1-204, S2-080, S2-172", "export / import", "State must also carry revision histories, opening balances, correction receipts and authorization closing times; exports of the stage-1 and stage-2 services must be accepted and their authorizations and captures accounted for (S3-094, S3-095, S3-118)."),
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
    for path in sorted(glob.glob(os.path.join(HERE, "r_*.py"))):
        importlib.import_module(os.path.basename(path)[:-3])
    rows = parse_rows()
    by_id = collections.defaultdict(list)
    for fn, ids in lib.TESTS:
        for i in ids:
            if i.startswith("S3-"):
                by_id[i].append("%s::%s" % (fn.__module__, fn.__name__))
    known = {r["id"] for r in rows}
    unknown = [i for i in by_id if i not in known]
    if unknown:
        raise SystemExit("probes cite unknown ledger ids: %s" % unknown)
    kinds = collections.Counter(r["kind"] for r in rows)
    stars = sum(1 for r in rows if r["star"])
    out = []
    w = out.append
    w("# Stage 3 requirement ledger — statements, historical balances and payment corrections")
    w("")
    w("Source: stage-3 specification (part 1/1) plus, by reference, every row of `factory/ledger/stage-1.md` and `factory/ledger/stage-2.md`.")
    w("Probes: `factory/probes/stage-3/` — API probes `r_*.py` (standard library only), the stage-2 suite (`../stage-2/q_*.py` API probes and "
      "`u_*.py` browser probes) and the stage-1 suite (`../stage-1/p_*.py`), all started by one command:")
    w("")
    w("    python3 factory/probes/stage-3/run_all.py http://127.0.0.1:8080")
    w("")
    w("Container level: `factory/probes/stage-3/run_docker.sh [--offline] [--stage1-ref <git-ref>] [--stage2-ref <git-ref>]`. "
      "This file is generated: edit `ledger_rows.txt`, re-run `build_ledger.py`.")
    w("")
    w("## Everything earlier still holds")
    w("")
    w("Row **S3-001**: every row of `factory/ledger/stage-1.md` (S1-001 … S1-208) and `factory/ledger/stage-2.md` (S2-001 … S2-173) continues "
      "to apply, probed by the unchanged stage-1 and stage-2 suites (run_all.py executes both as part of stage 3; `--no-stage1`, `--no-stage2`, "
      "`--no-ui` narrow it) — except where the table below says the text changed.")
    w("")
    w("## Earlier rows changed by stage 3")
    w("")
    w("| earlier row(s) | what it said | change in stage 3 |")
    w("|---|---|---|")
    for a_, b_, c_ in CHANGED:
        w("| %s | %s | %s |" % (esc(a_), esc(b_), esc(c_)))
    w("")
    w("## Counts (stage-3 rows)")
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
    w("Rows marked manual (nothing observable to assert): %d. Rows covered by the suites as a group: %d."
      % (len(manual), len([r for r in rows if r["script"]])))
    w("")
    w("## Ambiguities and the reading chosen")
    w("")
    for rid, text in [
        ("S3-012", "`Z` is part of RFC 3339's offset grammar, so `…Z` is accepted for as_of/known_at/from/to and echoed unchanged (own probe). Strictly invalid RFC 3339 spellings (no seconds, `+0000`, Feb 30, offset +25:00) are 422 (own probe)."),
        ("S3-015", "With corrections, \"payment with created_at at or before as_of\" means the selected revision's effective_at (S3-069); without corrections they coincide."),
        ("S3-023", "Ties on effective_at are ordered by payment id as strings; the probes only create ties among seeded payments with equal-length ids. Inside a tie `balance_after` is the running sum in that order (it may be transiently negative where the combined effect at the instant is not)."),
        ("S3-004", "Request-paid and seeded payments are \"eligible\": only settlement members and captures are linked (S3-093, S3-096)."),
        ("S3-041", "Receiver and operator get exactly 403 on corrections; a stranger gets 403 on a public payment and 403 or 404 on a private one."),
        ("S3-043", "Wrong JSON types for expected_revision, reason and effective_at are accepted as 400 or 422; wrong types for `amount` are 422 (S1-076). Non-object or unparseable bodies are 400."),
        ("S3-056", "A correction's debit is checked against the debited user's `available` (holds are not spendable), as for every stage-2 insufficient_funds."),
        ("S3-046", "A correction may keep the amount and only change effective_at (difference zero, nothing moves now); effective_at may precede the payment's created_at."),
        ("S3-063", "Revision records returned by the revisions endpoint carry revision, amount, effective_at, recorded_at, reason (payment_id, if present, must match)."),
        ("S3-076", "Inside a statement entry only `payment.amount` is the selected amount; the other payment fields (including created_at) are the original payment's."),
        ("S3-082", "Snapshot pages carry opening_balance, closing_balance, entries and has_more of the frozen result; whether they repeat the token is not asserted."),
        ("S3-083", "`as_of` is not a statement parameter; it is neither probed with a snapshot nor with a plain statement."),
        ("S3-094", "Needs running stage-1 / stage-2 services (`PROBE_STAGE1_BASE_URL`, `PROBE_STAGE2_BASE_URL`; `run_docker.sh --stage1-ref/--stage2-ref` builds them from git refs); SKIP otherwise. The opening balance of an imported account is its balance minus the net effect of every payment in the export, which equals the fixture's ending balance minus the net effect of the seeded payments."),
        ("S3-106", "closed_at of a final-captured authorization is compared to the capture payment's created_at within one second; for imported (older) closed authorizations only the presence of the key is asserted."),
        ("S3-099", "The capture instant is the capture payment's created_at: at exactly that as_of the payment counts and the hold has already been reduced."),
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
        if any(i.startswith("S3-") for i in ids):
            w("| `%s::%s` | %s |" % (fn.__module__, fn.__name__, ", ".join(i for i in ids if i.startswith("S3-"))))
    w("")
    w("## Probe design notes")
    w("")
    w("- Every probe resets the service to its own fixture. Seeded histories are built by `lib3.hist_world`, which derives ending balances from "
      "opening balances and refuses inconsistent histories; API-created events are separated by at least 1.1 s wherever a probe needs distinct "
      "instants (so second-resolution clocks do not blur them).")
    w("- `lib3.Model` is an independent model of the bitemporal rules written from the specification text (selected revision = latest recorded at or "
      "before known_at; applied from its effective_at; half-open windows; ties by payment id). `test_bitemporal_grid_against_the_model` compares "
      "`GET /me` for ~1900 (user, as_of, known_at) triples and ~300 statements (windows x known_at x users) with it, and checks that the sum of "
      "balances equals the seeded total in every view. Hand-computed scenarios (`test_known_at_and_effective_time_hand_computed`, the overdraft "
      "scenarios, the hold timelines) pin down the same rules without the model.")
    w("- Rejection probes (`r_overdraft.rejected`) compare /me, full statements, feeds and revision histories before and after every 409/422 and "
      "re-use the failed idempotency key later.")
    w("- Concurrency: 14 corrections with one expected revision (exactly one 201), 15 identical retries (one 201), corrections racing the receiver's "
      "spending (exactly one side may win), snapshot readers and live-statement samplers during a payment/correction storm, and a mixed storm of "
      "payments, corrections, authorizations and captures with a final cross-check of statement closing_balance against GET /me.")
    w("- Upgrade: a stage-3 state (seeded history, corrections, receipts, failed keys, holds, a settlement) is exported, the service reset to another "
      "fixture, the export imported and everything compared byte-for-byte; stage-1 and stage-2 exports are imported when the old services are "
      "reachable (`PROBE_STAGE1_BASE_URL`, `PROBE_STAGE2_BASE_URL`).")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    open(OUT, "w", encoding="utf-8").write("\n".join(out) + "\n")
    print("wrote", OUT, "rows=%d stars=%d" % (len(rows), stars))
    print(dict(kinds))


if __name__ == "__main__":
    main()
