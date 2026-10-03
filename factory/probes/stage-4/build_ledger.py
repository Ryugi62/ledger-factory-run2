#!/usr/bin/env python3
"""Render factory/ledger/stage-4.md from ledger_rows.txt and the probes' @test(...) ids.

    python3 factory/probes/stage-4/build_ledger.py
"""
import collections
import glob
import importlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lib4  # noqa: E402,F401
import lib   # noqa: E402

OUT = os.path.abspath(os.path.join(HERE, "..", "..", "ledger", "stage-4.md"))
KINDS = ["behaviour", "error", "concurrency", "idempotency", "time", "data-migration", "UI-state", "limit"]

CHANGED = [
    ("S1-110, S1-199, S2-127, S3-006", "payment object shape", "Adds `refund_of` (null except on refund payments, which name their target) to every payment object (S4-012, S4-016)."),
    ("S1-096, S1-203, S2-099, S3-040", "eight idempotent write paths", "Ten: + `POST /payments/{id}/refunds` and `POST /correction-batches` (S4-005, S4-006, S4-042)."),
    ("S3-093", "single-payment corrections reject settlement members with 422 linked_payment_immutable", "Unchanged for the single endpoint; the batch endpoint can correct settlement members together (S4-026, S4-027, S4-028)."),
    ("S3-096", "captures are immutable linked payments", "Refund payments are immutable too (S4-018); a capture can be refunded (S4-008)."),
    ("S3-044", "a correction may set any amount 0..1000000000", "A correction cannot go below the already-refunded amount: 422 refund_exceeds_payment (S4-019)."),
    ("S3-056, S3-109", "a correction's debit is checked against available; insufficient_funds wins over historical_overdraft", "Restated and extended to batches with the combined effect of all revisions (S4-020, S4-030, S4-032)."),
    ("S3-063, S3-049", "revision records: revision, amount, effective_at, recorded_at, reason", "A revision made by a batch also exposes `correction_batch_id` (S4-036)."),
    ("S3-050", "recorded times for one payment strictly increase", "Batch members share one recorded_at, strictly later than each member's previous one (S4-035)."),
    ("S3-082, S3-114", "snapshots page a frozen result", "Unchanged by refunds and batches (S4-040); snapshots now also survive export/import (S4-045, S4-049)."),
    ("S3-091, S3-092", "settlement receipts and member revision 1", "Receipts replay unchanged after refunds and batch corrections; refunds never change membership (S4-039, S4-043)."),
    ("S1-171 … S1-184, S2-172, S3-118, S3-094", "export / import", "Must accept exports of stages 1-3 retaining settlement membership, corrections and snapshots; a stage-4 state (refunds, batches, snapshots) round-trips (S4-045, S4-049)."),
    ("S1-005, S2-090, S3-060", "sum of balances / totals equals the seeded total (in every historical view)", "Also with refunds and batches (S4-050, S4-051)."),
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
    for path in sorted(glob.glob(os.path.join(HERE, "s_*.py"))):
        importlib.import_module(os.path.basename(path)[:-3])
    rows = parse_rows()
    by_id = collections.defaultdict(list)
    for fn, ids in lib.TESTS:
        for i in ids:
            if i.startswith("S4-"):
                by_id[i].append("%s::%s" % (fn.__module__, fn.__name__))
    known = {r["id"] for r in rows}
    unknown = [i for i in by_id if i not in known]
    if unknown:
        raise SystemExit("probes cite unknown ledger ids: %s" % unknown)
    kinds = collections.Counter(r["kind"] for r in rows)
    stars = sum(1 for r in rows if r["star"])
    out = []
    w = out.append
    w("# Stage 4 requirement ledger — refunds and batch corrections")
    w("")
    w("Source: stage-4 specification (part 1/1) plus, by reference, every row of `factory/ledger/stage-1.md`, `stage-2.md` and `stage-3.md`.")
    w("Probes: `factory/probes/stage-4/` — API probes `s_*.py` (standard library only), the stage-3 suite (`../stage-3/r_*.py`), the stage-2 suite "
      "(`../stage-2/q_*.py` API and `u_*.py` browser probes) and the stage-1 suite (`../stage-1/p_*.py`), all started by one command:")
    w("")
    w("    python3 factory/probes/stage-4/run_all.py http://127.0.0.1:8080")
    w("")
    w("Container level: `factory/probes/stage-4/run_docker.sh [--offline] [--stage1-ref <git-ref>] [--stage2-ref <git-ref>] [--stage3-ref <git-ref>]`. "
      "This file is generated: edit `ledger_rows.txt`, re-run `build_ledger.py`.")
    w("")
    w("## Everything earlier still holds")
    w("")
    w("Row **S4-001**: every row of `factory/ledger/stage-1.md` (S1-001 … S1-208), `stage-2.md` (S2-001 … S2-173) and `stage-3.md` (S3-001 … S3-120) "
      "continues to apply, probed by the unchanged earlier suites (run_all.py executes all of them as part of stage 4; `--no-stage1`, `--no-stage2`, "
      "`--no-stage3`, `--no-ui` narrow it) — except where the table below says the text changed.")
    w("")
    w("## Earlier rows changed by stage 4")
    w("")
    w("| earlier row(s) | what it said | change in stage 4 |")
    w("|---|---|---|")
    for a_, b_, c_ in CHANGED:
        w("| %s | %s | %s |" % (esc(a_), esc(b_), esc(c_)))
    w("")
    w("## Counts (stage-4 rows)")
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
        ("S4-007", "On an existing payment the original sender, third parties and operators get exactly 403 on a refund; the receiver of a refund payment (who would be refunding it) gets 422 invalid_refund_target (S4-011), not 403."),
        ("S4-009", "\"Invalid amount\" follows the payment amount rules: integer 1..1000000000 (`100.0`, `1e2` valid; 0, negatives, fractions, strings, booleans, null, arrays, a missing amount and 1000000001 are 422 validation_failed). A refund larger than the remaining cap but within range is refund_exceeds_payment."),
        ("S4-010", "The cap is the payment's *current* (latest-revision) amount, minus refunds already made; a payment corrected to 0 cannot be refunded."),
        ("S4-012", "The refund's settlement_id is null (it is not a settlement member, S4-043); its visibility and note are the target's."),
        ("S4-022", "Non-array `corrections` and non-object items are accepted as 400 or 422 (S1-190 reads them as 422); empty, more than 32 and duplicate payment_ids are exactly 422 validation_failed and are decided before any payment lookup (the probes use unknown ids for the 33-item case)."),
        ("S4-030", "\"Item errors in input order\": the first failing item decides, whatever its kind (e.g. a stale item 1 beats an invalid item 2, an unknown payment 1 beats a capture item 2), and item errors beat settlement completeness. Completeness beats current funds; current funds (combined effect, `available`) beat historical total/available."),
        ("S4-027", "Members of a settlement need the same instant to the second as spelled in the request (offsets may differ); the probe uses a one-second difference as the violation."),
        ("S4-036", "Revisions made by single corrections carry no `correction_batch_id` or null; both are accepted."),
        ("S4-034", "Each element of `revisions` has the single-correction fields plus correction_batch_id; recorded_at is identical across the batch and equals the top-level recorded_at."),
        ("S4-045", "Needs running services of stages 1-3 (`PROBE_STAGE1_BASE_URL`, `PROBE_STAGE2_BASE_URL`, `PROBE_STAGE3_BASE_URL`; `run_docker.sh --stage1-ref/--stage2-ref/--stage3-ref` builds them from git refs); SKIP otherwise. For a stage-3 export, snapshot tokens issued by the old service must keep paging their frozen entries after the import; stage-1/2 exports have no snapshots."),
        ("S4-049", "Snapshot tokens survive export -> reset -> import within the same service (stage 4 retains snapshots in exports)."),
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
        if any(i.startswith("S4-") for i in ids):
            w("| `%s::%s` | %s |" % (fn.__module__, fn.__name__, ", ".join(i for i in ids if i.startswith("S4-"))))
    w("")
    w("## Probe design notes")
    w("")
    w("- Every probe resets the service to its own fixture; seeded histories come from `lib3.hist_world`; events that must be distinguishable by time "
      "are at least 1.1 s apart. Rejection probes compare /me, full statements, feeds and revision histories before and after, and re-use the failed key.")
    w("- Refund probes: opposite-direction payment with `refund_of`, cumulative cap against the *corrected* amount (both directions: refunds then "
      "corrections, corrections then refunds), available-funds rule with a real hold, request/capture/settlement targets and the \"nothing is reopened\" "
      "checks, historical views of refund payments against the independent bitemporal model (`lib3.Model`), the full idempotency matrix.")
    w("- Batch probes: response shape and shared recorded_at, item-error order, settlement completeness and identical instants (four offset spellings), "
      "pairwise error precedence, combined affordability, historical views against the model, snapshot tokens issued before the batch, the idempotency matrix.")
    w("- Concurrency: 12 overlapping two-item batches (successful ones must be pairwise disjoint), a batch against single corrections, disjoint batches "
      "(all succeed), 12 refunds of 200 against 1000 (exactly 5), refund vs correction, refund vs batch, refund vs spend, and a mixed storm with snapshot samplers "
      "and a final cross-check of statement closing_balance against GET /me and of refund totals against corrected amounts.")
    w("- Upgrade: a stage-4 state with snapshots is exported, the service reset, the export imported and everything compared byte-for-byte (including "
      "snapshot pages); exports of stages 1-3 are imported when the old services are reachable.")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    open(OUT, "w", encoding="utf-8").write("\n".join(out) + "\n")
    print("wrote", OUT, "rows=%d stars=%d" % (len(rows), stars))
    print(dict(kinds))


if __name__ == "__main__":
    main()
