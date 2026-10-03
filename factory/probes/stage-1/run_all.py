#!/usr/bin/env python3
"""Run every stage-1 probe against a running service.

    python3 factory/probes/stage-1/run_all.py http://127.0.0.1:8080 [-k substr] [--list] [--check-ledger]

Exit status 0 only if every probe passed (SKIPs are reported and do not fail the run,
except with --strict).  PROBE_BASE_URL_2 enables the cross-instance import probes.
"""
import argparse
import glob
import importlib
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import lib  # noqa: E402


def load():
    for path in sorted(glob.glob(os.path.join(HERE, "p_*.py"))):
        importlib.import_module(os.path.basename(path)[:-3])


def wait_health(seconds=60):
    t0 = time.time()
    last = None
    while time.time() - t0 < seconds:
        try:
            r = lib.http("GET", "/health", timeout=3)
            if r.status == 200:
                return time.time() - t0
            last = r.status
        except Exception as e:  # noqa
            last = repr(e)
        time.sleep(0.25)
    raise SystemExit("service not healthy within %ss (last: %s)" % (seconds, last))


def ledger_ids():
    ids = {}
    manual = set()
    cur = None
    for line in open(os.path.join(HERE, "ledger_rows.txt"), encoding="utf-8"):
        if line.startswith("== "):
            cur = line[3:].split("|")[0].strip()
            ids[cur] = False
        elif (line.startswith("! ") or line.startswith("@ ")) and cur:
            manual.add(cur)
    return list(ids), manual


def ledger_stars():
    stars, cur = set(), None
    for line in open(os.path.join(HERE, "ledger_rows.txt"), encoding="utf-8"):
        if line.startswith("== "):
            f = [x.strip() for x in line[3:].split("|")]
            if f[3] == "\u2605":
                stars.add(f[0])
    return stars


def check_ledger():
    ids, manual = ledger_ids()
    covered = {}
    for fn, tids in lib.TESTS:
        for i in tids:
            covered.setdefault(i, []).append(fn.__module__ + "." + fn.__name__)
    bad = [i for i in covered if i not in ids]
    uncovered = [i for i in ids if i not in covered and i not in manual]
    doubly = [i for i in ids if i in covered and i in manual]
    for i in bad:
        print("UNKNOWN ledger id used by probes:", i)
    for i in uncovered:
        print("ROW WITHOUT PROBE OR MANUAL REASON:", i)
    for i in doubly:
        print("ROW both manual and probed (fine, informational):", i)
    print("%d ledger rows, %d probes, %d rows covered, %d manual" % (
        len(ids), len(lib.TESTS), len(covered), len(manual)))
    return not bad and not uncovered


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base", nargs="?", default=os.environ.get("PROBE_BASE_URL", "http://127.0.0.1:8080"))
    ap.add_argument("-k", dest="sub", default=None, help="only probes whose name contains this")
    ap.add_argument("--ids", default=None, help="only probes covering one of these comma separated ledger ids")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--check-ledger", action="store_true")
    ap.add_argument("--strict", action="store_true", help="SKIP counts as failure")
    ap.add_argument("--no-wait", action="store_true")
    a = ap.parse_args()
    lib.set_base(a.base)
    load()
    if a.check_ledger:
        sys.exit(0 if check_ledger() else 1)
    stars = ledger_stars()
    # probes that cover a star row run first (stable otherwise)
    tests = sorted(lib.TESTS, key=lambda t: 0 if stars & set(t[1]) else 1)
    if a.sub:
        tests = [t for t in tests if a.sub in t[0].__name__ or a.sub in t[0].__module__]
    if a.ids:
        want = set(a.ids.split(","))
        tests = [t for t in tests if want & set(t[1])]
    if a.list:
        for fn, ids in tests:
            print("%-48s %s" % (fn.__module__ + "." + fn.__name__, ",".join(ids)))
        return
    if not a.no_wait:
        t = wait_health()
        print("healthy after %.1fs  base=%s" % (t, a.base))
    passed = failed = skipped = 0
    failures = []
    for fn, ids in tests:
        name = fn.__module__ + "." + fn.__name__
        e0, s0 = len(lib.SERVER_ERRORS), len(lib.SLOW)
        t0 = time.time()
        status, detail = "PASS", ""
        try:
            fn()
        except lib.Skip as e:
            status, detail = "SKIP", str(e)
        except AssertionError as e:
            status, detail = "FAIL", str(e)
        except Exception as e:  # noqa
            status, detail = "FAIL", "".join(traceback.format_exception_only(type(e), e)).strip() + \
                " @ " + traceback.format_exc().splitlines()[-3].strip()
        if status == "PASS":
            if len(lib.SERVER_ERRORS) > e0:
                status, detail = "FAIL", "5xx response(s): %r" % (lib.SERVER_ERRORS[e0:e0 + 3],)
            elif len(lib.SLOW) > s0:
                status, detail = "FAIL", "request(s) over the time limit: %r" % (lib.SLOW[s0:s0 + 3],)
        dt = time.time() - t0
        print("%-4s %-52s %5.1fs  [%s]" % (status, name, dt, ",".join(ids)))
        if detail:
            print("       " + detail.replace("\n", "\n       ")[:1500])
        if status == "PASS":
            passed += 1
        elif status == "SKIP":
            skipped += 1
        else:
            failed += 1
            failures.append((name, ids, detail))
    print("\n%d passed, %d failed, %d skipped" % (passed, failed, skipped))
    if failures:
        print("\nFAILED ledger rows:", ", ".join(sorted({i for _, ids, _ in failures for i in ids})))
    if failed or (a.strict and skipped):
        sys.exit(1)


if __name__ == "__main__":
    main()
