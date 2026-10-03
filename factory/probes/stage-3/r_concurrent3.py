"""Stage 3: concurrency — racing corrections, corrections racing spending, stable snapshots under load, mixed storm."""
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from lib3 import *  # noqa: F401,F403


@test("S3-090", "S3-052", "S3-040", "S3-049", "S3-054", "S3-119")
def test_corrections_with_one_expected_revision_cannot_both_succeed():
    w = lib2.world2({"ada": 1_000_000, "bob": 1_000_000})
    ada, bob = w["ada"], w["bob"]
    for rnd in range(4):
        p = new_payment(ada, "bob", 1000)
        pid, eff = p["payment_id"], p["created_at"]
        start = lib2.me_ok(ada)["balance"]
        amounts = [900 - 25 * i for i in range(14)]
        res = burst([(lambda a=a: correct(ada, pid, 1, a, eff, "race %d" % a)) for a in amounts])
        wins = [(a, r) for a, r in zip(amounts, res) if r.status == 201]
        eq(len(wins), 1, "round %d: exactly one of %d corrections with expected_revision 1 may succeed, got %r" % (rnd, len(amounts), [r.status for r in res]))
        for a, r in zip(amounts, res):
            if r.status != 201:
                expect(r, 409, "stale_revision", "the losers are stale")
        win_amount = wins[0][0]
        rv = revisions(ada, pid)
        eq((len(rv), rv[1]["amount"]), (2, win_amount), "exactly one new revision, the winner's")
        eq(lib2.me_ok(ada)["balance"], start + 1000 - win_amount, "the money moved exactly once")
    # the same key sent many times at once: one 201, the rest 200 with the same body, one revision
    p = new_payment(ada, "bob", 1000)
    pid, eff = p["payment_id"], p["created_at"]
    k = fresh_key()
    res = burst([(lambda: ada.post("/payments/%s/corrections" % pid, corr_body(1, 600, eff, "same"), key=k)) for _ in range(15)])
    eq(sorted(r.status for r in res), [200] * 14 + [201], "one 201, the others replay with 200")
    eq(len({json_dump(r.json) for r in res}), 1, "all bodies identical")
    eq(len(revisions(ada, pid)), 2, "one revision")
    # a mixed race: stale ones, replays and invalid ones together
    kk = fresh_key()
    fns = [(lambda: ada.post("/payments/%s/corrections" % pid, corr_body(2, 500, eff, "m"), key=kk)) for _ in range(5)]
    fns += [(lambda i=i: correct(ada, pid, 2, 400 - i, eff, "other%d" % i)) for i in range(5)]
    res = burst(fns)
    ok(sum(1 for r in res if r.status == 201) == 1, "exactly one 201 among the racing revision-2 corrections: %r" % [r.status for r in res])
    eq(len(revisions(ada, pid)), 3, "exactly one more revision")


def json_dump(o):
    import json
    return json.dumps(o, sort_keys=True)


@test("S3-056", "S3-057", "S3-054", "S3-060", "S3-120")
def test_correction_racing_the_receivers_spending_never_overdraws():
    n = 16
    names = ["r%02d" % i for i in range(n)]
    bal = {"ada": 100000, "cy": 0}
    bal.update({h: 0 for h in names})
    w = lib2.world2(bal)
    ada, cy = w["ada"], w["cy"]
    pays = {}
    for h in names:
        pays[h] = new_payment(ada, h, 100)
    seed_total = sum(bal.values())
    fns, labels = [], []
    for h in names:
        pid, eff = pays[h]["payment_id"], pays[h]["created_at"]
        fns.append(lambda pid=pid, eff=eff: correct(ada, pid, 1, 0, eff, "reverse"))
        labels.append(("corr", h))
        fns.append(lambda h=h: w[h].pay("cy", 100))
        labels.append(("pay", h))
    res = burst(fns)
    outcome = {}
    for (kind, h), r in zip(labels, res):
        outcome.setdefault(h, {})[kind] = r
    for h in names:
        c, p = outcome[h]["corr"], outcome[h]["pay"]
        ok(c.status in (201, 409) and p.status in (201, 409), "%s: statuses %s %s" % (h, c.status, p.status))
        ok((c.status == 201) != (p.status == 201), "%s: exactly one of the reversal (%s %s) and the spend (%s %s) may succeed" % (h, c.status, c.code, p.status, p.code))
        if c.status == 409:
            ok(c.code in ("insufficient_funds", "historical_overdraft"), "%s: reversal refused with %s" % (h, c.code))
        if p.status == 409:
            eq(p.code, "insufficient_funds", "%s: spend refused" % h)
        b = lib2.me_ok(w[h])["balance"]
        eq(b, 0, "%s ends at zero either way" % h)
    eq(sum(lib2.me_ok(u)["balance"] for u in w.values()), seed_total, "conservation")
    for u in w.values():
        st = full_stmt(u)
        eq(st["closing"], lib2.me_ok(u)["balance"], "statement closing == balance for %s" % u.handle)
        ok(all(e["balance_after"] >= 0 for e in st["entries"]), "no negative balance_after for %s" % u.handle)


@test("S3-089", "S3-082", "S3-081", "S3-114", "S3-120", "S3-026", "S3-028")
def test_snapshots_are_stable_and_live_statements_consistent_during_a_storm():
    t = whole(utcnow()) - timedelta(days=9)
    opening = {"ada": 200000, "bob": 200000, "cy": 200000}
    pays = [fx_pay("p_%03d" % i, ("ada", "bob", "cy")[i % 3], ("bob", "cy", "ada")[i % 3], 100 + i, created_at=t + timedelta(minutes=10 * i))
            for i in range(36)]
    w = hist_world(opening, pays)
    ada, bob, cy = w["ada"], w["bob"], w["cy"]
    first = stmt(ada, limit=10)
    tok = first["snapshot"]
    orig, f = pages_snap(ada, tok, 10)
    bal = (f["opening_balance"], f["closing_balance"])
    total0 = sum(opening.values())
    stop = threading.Event()
    problems = []

    def reader(i):
        n = 0
        while not stop.is_set() or n < 3:
            lim = (3, 7, 10, 25)[(i + n) % 4]
            got, ff = pages_snap(ada, tok, lim)
            if got != orig or (ff["opening_balance"], ff["closing_balance"]) != bal:
                problems.append("snapshot changed (reader %d, limit %d)" % (i, lim))
                return
            n += 1

    def sampler(u):
        while not stop.is_set():
            j = stmt(u, limit=200)
            if not j["has_more"]:
                if j["opening_balance"] + sum(e["delta"] for e in j["entries"]) != j["closing_balance"]:
                    problems.append("live statement of %s: opening + deltas != closing" % u.handle)
                    return
            if j["closing_balance"] < 0:
                problems.append("negative closing balance for %s" % u.handle)
                return
            m = me_at(u, as_of=fmt(t + timedelta(hours=2)))
            if m["balance"] < 0 or m["available"] < 0:
                problems.append("negative historical balance")
                return

    def writer(seed):
        rnd = random.Random(seed)
        users = [ada, bob, cy]
        for i in range(25):
            a, b = rnd.sample(users, 2)
            r = a.pay(b.handle, rnd.randint(1, 50))
            if r.status not in (201, 409):
                problems.append("payment %s" % r)

    def corrector(idx):
        # each corrects its own seeded payments once (revision 1): the sender of p_i is ("ada","bob","cy")[i % 3]
        for i in idx:
            owner = (ada, bob, cy)[i % 3]
            r = correct(owner, "p_%03d" % i, 1, 100 + i + (i % 7) - 3, fmts(t + timedelta(minutes=10 * i + (i * 7) % 300)), "storm %d" % i)
            if r.status not in (201, 409):
                problems.append("correction %s" % r)

    with ThreadPoolExecutor(max_workers=16) as ex:
        readers = [ex.submit(reader, i) for i in range(4)]
        samplers = [ex.submit(sampler, u) for u in (bob, cy)]
        work = [ex.submit(writer, s) for s in range(3)] + [ex.submit(corrector, list(range(k, 36, 4))) for k in range(4)]
        for f_ in work:
            f_.result()
        stop.set()
        for f_ in readers + samplers:
            f_.result()
    ok(not problems, "; ".join(problems[:3]))
    got, ff = pages_snap(ada, tok, 6)
    eq(got, orig, "the snapshot is unchanged after the storm")
    eq(sum(lib2.me_ok(u)["total"] for u in w.values()), total0, "conservation after the storm")
    for u in w.values():
        st = full_stmt(u)
        eq(st["closing"], lib2.me_ok(u)["balance"], "statement closing == balance for %s after the storm" % u.handle)
        eq(me_at(u, as_of="2999-01-01T00:00:00+00:00")["balance"], st["closing"], "as_of in the future == closing balance")


def pages_snap(u, token, limit):
    out, off, first = [], 0, None
    while True:
        j = stmt(u, snapshot=token, limit=limit, offset=off)
        first = first or j
        out += j["entries"]
        if not j["has_more"]:
            return out, first
        off += limit


@test("S3-120", "S3-060", "S3-054", "S3-056", "S3-057", "S3-026", "S3-089", "S3-119")
def test_mixed_storm_of_payments_corrections_and_holds_conserves_money():
    handles = ["ada", "bob", "cy", "dan", "eve", "fay"]
    bal = {h: 20000 for h in handles}
    w = lib2.world2(bal)
    users = list(w.values())
    total0 = sum(bal.values())
    made = []                       # (owner, payment_id, revision counter)
    lock = threading.Lock()
    problems = []
    stop = threading.Event()

    def worker(seed):
        rnd = random.Random(seed)
        for step in range(40):
            u = rnd.choice(users)
            v = rnd.choice([x for x in users if x is not u])
            roll = rnd.random()
            if roll < 0.35:
                r = u.pay(v.handle, rnd.randint(1, 3000))
                if r.status == 201:
                    with lock:
                        made.append((u, r.json["payment_id"], r.json["created_at"]))
                elif r.status != 409:
                    problems.append("pay %r" % r)
            elif roll < 0.7 and made:
                with lock:
                    owner, pid, c = rnd.choice(made)
                try:
                    rv = revisions(owner, pid)
                except AssertionError:
                    continue
                eff = fmt(inst(c) - timedelta(seconds=rnd.choice([0, 0, 5, 3600])))
                r = correct(owner, pid, len(rv) if rnd.random() < 0.9 else 1, rnd.randint(0, 3500), eff, "storm")
                if r.status not in (201, 409):
                    problems.append("correction %r" % r)
            elif roll < 0.85:
                r = lib2.authorize(u, v.handle, rnd.randint(1, 2000))
                if r.status == 201 and rnd.random() < 0.7:
                    a = r.json["authorization_id"]
                    r2 = lib2.capture(v, a, {"amount": rnd.randint(1, r.json["amount"]), "final": rnd.random() < 0.5})
                    if r2.status not in (201, 409, 422):
                        problems.append("capture %r" % r2)
                elif r.status not in (201, 409):
                    problems.append("authorize %r" % r)
            else:
                m = me_at(u, as_of=fmt(utcnow() - timedelta(seconds=rnd.randint(0, 30))))
                if m["available"] < 0 or m["total"] < 0 or m["held"] < 0:
                    problems.append("negative money field in a historical read: %r" % m)

    def sampler():
        while not stop.is_set():
            tot = 0
            for u in users:
                j = lib2.me_ok(u)
                tot += j["total"]
            st = stmt(random.choice(users), limit=200)
            if not st["has_more"] and st["opening_balance"] + sum(e["delta"] for e in st["entries"]) != st["closing_balance"]:
                problems.append("statement arithmetic broken under load")
            if st["closing_balance"] < 0:
                problems.append("negative statement closing balance")

    with ThreadPoolExecutor(max_workers=12) as ex:
        sam = [ex.submit(sampler) for _ in range(2)]
        ws = [ex.submit(worker, s) for s in range(8)]
        for f_ in ws:
            f_.result()
        stop.set()
        for f_ in sam:
            f_.result()
    ok(not problems, "; ".join(problems[:3]))
    eq(sum(lib2.me_ok(u)["total"] for u in users), total0, "the sum of totals equals the seeded total")
    far = "2999-01-01T00:00:00+00:00"
    ancient = "1970-01-01T00:00:00+00:00"
    for u in users:
        m = lib2.me_ok(u)
        st = full_stmt(u)
        eq(st["closing"], m["balance"], "closing_balance == balance for %s (two independent code paths)" % u.handle)
        eq(st["opening"], 20000, "opening balance is untouched by corrections (%s)" % u.handle)
        eq(me_at(u, as_of=ancient)["balance"], 20000, "as_of in 1970 (%s)" % u.handle)
        eq(me_at(u, as_of=far, known_at=far)["balance"], m["balance"], "as_of/known_at in the future == now (%s)" % u.handle)
    eq(sum(me_at(u, as_of=ancient)["balance"] for u in users), total0, "historical sum")
