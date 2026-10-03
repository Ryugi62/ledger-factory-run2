#!/usr/bin/env python3
"""Run the stage-4 probes and, by default, the stage-3, stage-2 (API + browser) and stage-1 suites against one running service.

    python3 factory/probes/stage-4/run_all.py http://127.0.0.1:8080 [--no-ui] [--no-stage1] [--no-stage2] [--no-stage3] [-k substr] [--ids S4-001,...]
                                              [--list] [--check-ledger] [--strict] [--screenshots DIR]

Environment: PROBE_BASE_URL_2 (second instance, stage-1 cross-instance import probe),
             PROBE_STAGE1/2/3_BASE_URL (running *stage-1*, *stage-2*, *stage-3* services for the upgrade probes),
             PROBE_SCREENSHOTS (same as --screenshots).
Exit status is 0 only if no probe failed (SKIPs are listed; --strict makes them failures).
"""
import argparse
import glob
import importlib
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
S1 = os.path.abspath(os.path.join(HERE, "..", "stage-1"))
S2 = os.path.abspath(os.path.join(HERE, "..", "stage-2"))
S3 = os.path.abspath(os.path.join(HERE, "..", "stage-3"))
sys.path.insert(0, S1)
sys.path.insert(0, S2)
sys.path.insert(0, S3)
sys.path.insert(0, HERE)
import lib4  # noqa: E402  (also puts the earlier libraries on the path)
import lib   # noqa: E402


def load(stage1, stage2, stage3, ui):
    mods = []
    if stage1:
        mods += sorted(glob.glob(os.path.join(S1, "p_*.py")))
    if stage2:
        mods += sorted(glob.glob(os.path.join(S2, "q_*.py")))
        if ui:
            mods += sorted(glob.glob(os.path.join(S2, "u_*.py")))
    if stage3:
        mods += sorted(glob.glob(os.path.join(S3, "r_*.py")))
    mods += sorted(glob.glob(os.path.join(HERE, "s_*.py")))
    for path in mods:
        importlib.import_module(os.path.basename(path)[:-3])


def rows(dirpath):
    ids, stars, manual, cur = [], set(), set(), None
    for line in open(os.path.join(dirpath, "ledger_rows.txt"), encoding="utf-8"):
        if line.startswith("== "):
            f = [x.strip() for x in line[3:].split("|")]
            cur = f[0]
            ids.append(cur)
            if f[3] == "★":
                stars.add(cur)
        elif (line.startswith("! ") or line.startswith("@ ")) and cur:
            manual.add(cur)
    return ids, stars, manual


def check_ledger():
    ids, stars, manual = rows(HERE)
    covered = {}
    for fn, tids in lib.TESTS:
        for i in tids:
            if i.startswith("S4-"):
                covered.setdefault(i, []).append(fn.__module__ + "." + fn.__name__)
    bad = [i for i in covered if i not in ids]
    uncovered = [i for i in ids if i not in covered and i not in manual]
    for i in bad:
        print("UNKNOWN ledger id used by probes:", i)
    for i in uncovered:
        print("ROW WITHOUT PROBE OR MANUAL REASON:", i)
    print("%d stage-4 ledger rows, %d tests registered, %d rows covered by probes, %d manual/suite" % (
        len(ids), len(lib.TESTS), len(covered), len(manual)))
    return not bad and not uncovered


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base", nargs="?", default=os.environ.get("PROBE_BASE_URL", "http://127.0.0.1:8080"))
    ap.add_argument("-k", dest="sub")
    ap.add_argument("--ids")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--check-ledger", action="store_true")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--no-wait", action="store_true")
    ap.add_argument("--no-ui", action="store_true", help="skip the stage-2 browser probes")
    ap.add_argument("--no-stage2", action="store_true", help="skip the stage-2 suite (API and browser probes)")
    ap.add_argument("--no-stage3", action="store_true", help="skip the stage-3 suite")
    ap.add_argument("--no-stage1", action="store_true", help="skip the stage-1 suite")
    ap.add_argument("--screenshots", default=os.environ.get("PROBE_SCREENSHOTS"))
    a = ap.parse_args()
    lib.set_base(a.base)
    os.environ.setdefault("PROBE_STAGE_DIR", "stage-4")
    if a.screenshots:
        os.environ["PROBE_SCREENSHOTS"] = a.screenshots
    load(not a.no_stage1, not a.no_stage2, not a.no_stage3, not a.no_ui)
    if a.screenshots:
        import ui_lib
        ui_lib.SHOT_DIR = a.screenshots
    if a.check_ledger:
        sys.exit(0 if check_ledger() else 1)
    _, s4stars, _ = rows(HERE)
    olderstars = set(rows(S1)[1]) | set(rows(S2)[1]) | set(rows(S3)[1])

    def rank(t):
        ids = set(t[1])
        is4 = any(i.startswith("S4-") for i in ids)
        star = bool((s4stars | olderstars) & ids)
        return (0 if is4 else 1, 0 if star else 1)

    tests = sorted(lib.TESTS, key=rank)
    if a.sub:
        tests = [t for t in tests if a.sub in t[0].__name__ or a.sub in t[0].__module__]
    if a.ids:
        want = set(a.ids.split(","))
        tests = [t for t in tests if want & set(t[1])]
    if a.list:
        for fn, ids in tests:
            print("%-58s %s" % (fn.__module__ + "." + fn.__name__, ",".join(ids)))
        return
    if not a.no_wait:
        t0 = time.time()
        while True:
            try:
                if lib.http("GET", "/health", timeout=3).status == 200:
                    break
            except Exception:  # noqa
                pass
            if time.time() - t0 > 60:
                raise SystemExit("service not healthy within 60 s")
            time.sleep(0.3)
        print("healthy after %.1fs  base=%s" % (time.time() - t0, a.base))
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
            tb = traceback.format_exc().splitlines()
            status, detail = "FAIL", "".join(traceback.format_exception_only(type(e), e)).strip() + " @ " + (tb[-3].strip() if len(tb) > 2 else "")
        if status == "PASS":
            if len(lib.SERVER_ERRORS) > e0:
                status, detail = "FAIL", "5xx response(s): %r" % (lib.SERVER_ERRORS[e0:e0 + 3],)
            elif len(lib.SLOW) > s0:
                status, detail = "FAIL", "request(s) over the time limit: %r" % (lib.SLOW[s0:s0 + 3],)
        print("%-4s %-60s %5.1fs  [%s]" % (status, name, time.time() - t0, ",".join(ids)))
        if detail:
            print("       " + detail.replace("\n", "\n       ")[:1800])
        if status == "PASS":
            passed += 1
        elif status == "SKIP":
            skipped += 1
        else:
            failed += 1
            failures.append((name, ids))
    print("\n%d passed, %d failed, %d skipped" % (passed, failed, skipped))
    if failures:
        print("FAILED ledger rows:", ", ".join(sorted({i for _, ids in failures for i in ids})))
    if failed or (a.strict and skipped):
        sys.exit(1)


if __name__ == "__main__":
    main()
