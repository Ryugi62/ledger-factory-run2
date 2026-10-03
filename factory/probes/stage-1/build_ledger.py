#!/usr/bin/env python3
"""Render factory/ledger/stage-1.md from ledger_rows.txt and the probes' @test(...) ids.

    python3 factory/probes/stage-1/build_ledger.py
"""
import collections
import glob
import importlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lib  # noqa: E402

OUT = os.path.abspath(os.path.join(HERE, "..", "..", "ledger", "stage-1.md"))
KINDS = ["behaviour", "error", "concurrency", "idempotency", "time", "data-migration", "UI-state", "limit"]


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
    for path in sorted(glob.glob(os.path.join(HERE, "p_*.py"))):
        importlib.import_module(os.path.basename(path)[:-3])
    rows = parse_rows()
    by_id = collections.defaultdict(list)
    for fn, ids in lib.TESTS:
        for i in ids:
            by_id[i].append("%s::%s" % (fn.__module__, fn.__name__))
    known = {r["id"] for r in rows}
    unknown = [i for i in by_id if i not in known]
    if unknown:
        raise SystemExit("probes cite unknown ledger ids: %s" % unknown)
    kinds = collections.Counter(r["kind"] for r in rows)
    stars = sum(1 for r in rows if r["star"])
    out = []
    w = out.append
    w("# Stage 1 requirement ledger — Pocketful payments and settlements")
    w("")
    w("Source: stage-1 specification (parts 1-3), read independently of any shipped checks.")
    w("Probes: `factory/probes/stage-1/` (standard-library Python, black-box over HTTP).")
    w("Run everything: `python3 factory/probes/stage-1/run_all.py http://127.0.0.1:8080` "
      "(container-level: `factory/probes/stage-1/run_docker.sh [--offline]`).")
    w("This file is generated: edit `ledger_rows.txt` and re-run `build_ledger.py`.")
    w("")
    w("**Earlier stages:** none — stage 1 is the first stage, so there are no earlier ledger rows to carry forward "
      "(row S1-208 records this; later stages must add \"everything earlier still holds\" pointing at this file).")
    w("")
    w("## Counts")
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
    scripts = [r for r in rows if r["script"]]
    w("Rows exercised only by a shell harness: %d. Rows marked manual (nothing observable to assert): %d."
      % (len(scripts), len(manual)))
    w("")
    w("## Ambiguities and the reading chosen")
    w("")
    w("Each row's *reading* column states the interpretation the probes assert. The ones most likely to be contested:")
    w("")
    for rid, text in [
        ("S1-031", "Timestamps need a numeric `±HH:MM` offset (the spec's example); a bare `Z` fails."),
        ("S1-042", "\"character\" in the handle derivation is a Unicode code point, one `_` each."),
        ("S1-072", "Strangers calling pay/decline/cancel may get 403 or 404; the *other party* must get exactly 403."),
        ("S1-087", "Re-signup of a registered email is `email_taken` even though its derived handle is also taken."),
        ("S1-114", "Note length counts Unicode code points (200 emoji are accepted, 201 rejected)."),
        ("S1-113", "`self_payment` and unknown-handle 404 are decided before the balance check."),
        ("S1-141", "`GET /requests` ties inside one second must still come back in strict reverse creation order."),
        ("S1-149", "A split with the caller omitted divides among the listed handles only."),
        ("S1-176", "A state object this service never produced (`{\"nonsense\": true}`) is an invalid state (422)."),
        ("S1-134", "Decline/cancel take no body: a POST with no body at all must be accepted."),
        ("S1-190", "Wrong JSON types inside `transfers` are 422 (§11 \"malformed batch shape\"), not 400."),
        ("S1-027", "Reset clears tokens and idempotency records as well (they are service state)."),
    ]:
        w("- **%s** — %s" % (rid, text))
    w("")
    w("## Ledger")
    w("")
    w("| ID | section | requirement (verbatim) | kind | ★ | probe | reading / note |")
    w("|---|---|---|---|---|---|---|")
    for r in rows:
        probes = by_id.get(r["id"], [])
        cell = []
        if probes:
            cell += ["`%s`" % p for p in probes]
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
        w("| `%s::%s` | %s |" % (fn.__module__, fn.__name__, ", ".join(ids)))
    w("")
    w("## Probe design notes")
    w("")
    w("- Every probe resets the service to its own fixture first; probes are independent and can run in any order.")
    w("- The probe client records every 5xx and every request slower than 5 s (10 s for `/_test/*`); either fails the probe "
      "that caused it (S1-017, S1-083). Every 4xx/5xx body is checked against the standard error shape (S1-067).")
    w("- Concurrency probes release all workers through a barrier. Retried-write probes resend the same key and body, "
      "concurrently and sequentially, on all five idempotent paths.")
    w("- `run_docker.sh` builds the Dockerfile, starts two containers (custom `PORT`, and default 8080), applies "
      "`--cpus 2 --memory 2g`, measures time to first healthy response, uses the second container as the target of the "
      "cross-instance import probe, and with `--offline` repeats the whole run on a docker network with no outbound route.")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    open(OUT, "w", encoding="utf-8").write("\n".join(out) + "\n")
    print("wrote", OUT, "rows=%d stars=%d" % (len(rows), stars))
    print(dict(kinds))


if __name__ == "__main__":
    main()
