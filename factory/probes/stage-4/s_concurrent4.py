"""Stage 4: concurrency — overlapping batches, batches vs single corrections, refunds vs refunds / corrections / spending."""
import random
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from lib4 import *  # noqa: F401,F403


def world_c4(extra=None, ops=("op",)):
    bal = {"ada": 100000, "bob": 100000, "cy": 0, "dan": 0, "op": 0}
    if extra:
        bal.update(extra)
    return lib2.world2(bal, operators=list(ops))


def reconcile(w, label):
    for h, u in w.items():
        m = lib2.me_ok(u)
        st = full_stmt(u)
        eq(st["closing"], m["balance"], "%s: statement closing == balance for %s" % (label, h))
        ok(m["balance"] >= 0 and m["available"] >= 0, "%s: %s negative: %r" % (label, h, m))


@test("S4-044", "S4-033", "S4-050", "S4-052", "S4-032")
def test_overlapping_batches_cannot_both_succeed():
    w = world_c4()
    ada, bob, op = w["ada"], w["bob"], w["op"]
    total0 = sum(lib2.me_ok(u)["total"] for u in w.values())
    for rnd in range(3):
        ps = [new_payment(ada, "bob", 1000) for _ in range(6)]
        pairs = [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 0), (0, 3), (1, 4), (2, 5), (0, 2), (1, 3), (2, 4)]
        fns, meta = [], []
        for n, (i, j) in enumerate(pairs):
            amt = 900 - 10 * n
            items = [item(ps[i]["payment_id"], 1, amt, ps[i]["created_at"], "race %d" % n), item(ps[j]["payment_id"], 1, amt - 1, ps[j]["created_at"], "race %d" % n)]
            fns.append(lambda items=items: op.post("/correction-batches", {"corrections": items}, key=fresh_key()))
            meta.append((i, j, amt))
        res = burst(fns)
        wins = [(m_, r) for m_, r in zip(meta, res) if r.status == 201]
        ok(wins, "round %d: at least one overlapping batch must succeed: %r" % (rnd, [r.status for r in res]))
        for m_, r in zip(meta, res):
            if r.status != 201:
                expect(r, 409, "stale_revision", "losers of an overlapping race are stale")
        covered = set()
        for (i, j, amt), r in wins:
            ok(not ({i, j} & covered), "round %d: two successful batches share a payment (%s)" % (rnd, (i, j)))
            covered |= {i, j}
            check_batch(r.json, 2)
        for k, p in enumerate(ps):
            rv = revisions(ada, p["payment_id"])
            eq(len(rv), 2 if k in covered else 1, "round %d: payment %d revision count" % (rnd, k))
        eq(sum(lib2.me_ok(u)["total"] for u in w.values()), total0, "conservation")
    reconcile(w, "after overlapping batches")


@test("S4-044", "S4-028", "S4-033", "S4-050")
def test_batch_against_single_corrections_and_disjoint_batches():
    w = world_c4()
    ada, op = w["ada"], w["op"]
    ada_op = w["ada"]
    for rnd in range(5):
        y1, y2 = new_payment(ada, "bob", 1000), new_payment(ada, "bob", 1000)
        fns = [lambda: op.post("/correction-batches", {"corrections": [item(y1["payment_id"], 1, 500, y1["created_at"]), item(y2["payment_id"], 1, 500, y2["created_at"])]}, key=fresh_key()),
               lambda: correct(ada, y1["payment_id"], 1, 700, y1["created_at"], "single 1"),
               lambda: correct(ada, y2["payment_id"], 1, 700, y2["created_at"], "single 2")]
        res = burst(fns)
        b, s1, s2 = res
        for r in (b, s1, s2):
            ok(r.status in (201, 409), "round %d: %r" % (rnd, r))
            if r.status == 409:
                eq(r.code, "stale_revision", "round %d: the loser is stale" % rnd)
        r1, r2 = revisions(ada, y1["payment_id"]), revisions(ada, y2["payment_id"])
        if b.status == 201:
            eq((s1.status, s2.status), (409, 409), "round %d: the batch won, so both singles are stale" % rnd)
            eq((len(r1), len(r2)), (2, 2), "round %d" % rnd)
        else:
            eq((len(r1), len(r2)), (1 + (s1.status == 201), 1 + (s2.status == 201)), "round %d: revisions match the winners" % rnd)
    # disjoint batches do not conflict
    groups = [[new_payment(ada, "bob", 100) for _ in range(3)] for _ in range(8)]
    res = burst([(lambda g=g, n=n: op.post("/correction-batches", {"corrections": [item(p["payment_id"], 1, 50 + n, p["created_at"]) for p in g]}, key=fresh_key()))
                 for n, g in enumerate(groups)])
    eq([r.status for r in res], [201] * 8, "disjoint batches all succeed")
    for g in groups:
        for p in g:
            eq(len(revisions(ada, p["payment_id"])), 2, "one revision each")
    reconcile(w, "after batch races")


@test("S4-044", "S4-010", "S4-014", "S4-019", "S4-050", "S4-052")
def test_refunds_race_each_other_corrections_batches_and_spending():
    w = world_c4()
    ada, bob, cy, op = w["ada"], w["bob"], w["cy"], w["op"]
    total0 = sum(lib2.me_ok(u)["total"] for u in w.values())
    # (a) twelve refunds of 200 against a 1000 payment: exactly five can succeed
    p = new_payment(ada, "bob", 1000)
    res = burst([(lambda: bob.post("/payments/%s/refunds" % p["payment_id"], {"amount": 200}, key=fresh_key())) for _ in range(12)])
    ok_ = [r for r in res if r.status == 201]
    eq(len(ok_), 5, "exactly five refunds of 200 fit into 1000: %r" % [r.status for r in res])
    for r in res:
        if r.status != 201:
            expect(r, 422, "refund_exceeds_payment", "the others exceed the cap")
    eq(len({r.json["payment_id"] for r in ok_}), 5, "five distinct refund payments")
    eq(sum(r.json["amount"] for r in ok_), 1000, "refunded in full")
    # (b) refund 600 vs correction to 300: not both
    pairs = [new_payment(ada, "bob", 1000) for _ in range(8)]
    fns, lab = [], []
    for q in pairs:
        fns.append(lambda q=q: bob.post("/payments/%s/refunds" % q["payment_id"], {"amount": 600}, key=fresh_key()))
        lab.append("refund")
        fns.append(lambda q=q: correct(ada, q["payment_id"], 1, 300, q["created_at"], "down"))
        lab.append("corr")
    res = burst(fns)
    for n, q in enumerate(pairs):
        rf, co = res[2 * n], res[2 * n + 1]
        ok((rf.status == 201) != (co.status == 201), "pair %d: exactly one of refund 600 and correction to 300 may succeed, got %s %s / %s %s" % (n, rf.status, rf.code, co.status, co.code))
        for r in (rf, co):
            if r.status != 201:
                expect(r, 422, "refund_exceeds_payment", "pair %d loser" % n)
    # (c) refund vs a batch correction
    pairs = [new_payment(ada, "bob", 1000) for _ in range(8)]
    fns = []
    for q in pairs:
        fns.append(lambda q=q: bob.post("/payments/%s/refunds" % q["payment_id"], {"amount": 600}, key=fresh_key()))
        fns.append(lambda q=q: op.post("/correction-batches", {"corrections": [item(q["payment_id"], 1, 300, q["created_at"], "batch down")]}, key=fresh_key()))
    res = burst(fns)
    for n in range(8):
        rf, co = res[2 * n], res[2 * n + 1]
        ok((rf.status == 201) != (co.status == 201), "batch pair %d: exactly one of refund and batch correction, got %s %s / %s %s" % (n, rf.status, rf.code, co.status, co.code))
        if co.status != 201:
            expect(co, 422, "refund_exceeds_payment", "batch loser")
        if rf.status != 201:
            expect(rf, 422, "refund_exceeds_payment", "refund loser")
    eq(sum(lib2.me_ok(u)["total"] for u in w.values()), total0, "conservation")
    reconcile(w, "after refund races")
    # (d) refund vs the receiver's own spending of exactly the same money (a new world: the reset invalidates the tokens above)
    names = ["r%d" % i for i in range(8)]
    w2 = lib2.world2(dict({"ada": 100000, "cy": 0}, **{n: 0 for n in names}))
    ada2, cy2 = w2["ada"], w2["cy"]
    ps = {n: new_payment(ada2, n, 100) for n in names}
    fns = []
    for n in names:
        fns.append(lambda n=n: w2[n].post("/payments/%s/refunds" % ps[n]["payment_id"], {"amount": 100}, key=fresh_key()))
        fns.append(lambda n=n: w2[n].pay("cy", 100))
    res = burst(fns)
    for i, n in enumerate(names):
        rf, sp = res[2 * i], res[2 * i + 1]
        ok((rf.status == 201) != (sp.status == 201), "%s: exactly one of the refund and the spend can use the 100: %s %s / %s %s" % (n, rf.status, rf.code, sp.status, sp.code))
        for r in (rf, sp):
            if r.status != 201:
                expect(r, 409, "insufficient_funds", "%s loser" % n)
        eq(lib2.me_ok(w2[n])["balance"], 0, "%s ends at zero" % n)
    eq(sum(lib2.me_ok(u)["total"] for u in w2.values()), 100000, "conservation (second world)")
    reconcile(w2, "after refund/spend races")


@test("S4-050", "S4-052", "S4-044", "S4-010", "S4-019", "S4-040", "S4-051", "S4-004")
def test_mixed_storm_of_payments_refunds_corrections_and_batches():
    handles = ["ada", "bob", "cy", "dan", "eve", "fay"]
    bal = {h: 20000 for h in handles}
    bal["op"] = 0
    w = lib2.world2(bal, operators=["op"])
    users = [w[h] for h in handles]
    op = w["op"]
    total0 = sum(bal.values())
    made, lock, problems, stop = [], threading.Lock(), [], threading.Event()
    for i in range(6):
        a_, b_ = users[i], users[(i + 1) % 6]
        r_ = expect(a_.pay(b_.handle, 100 + i), 201).json
        made.append((a_, b_, r_["payment_id"], r_["created_at"]))
    snaps = {}
    for h in handles:
        snaps[h] = stmt(w[h], limit=5)
    frozen = {h: [] for h in handles}

    def worker(seed):
        rnd = random.Random(seed)
        for step in range(40):
            u = rnd.choice(users)
            v = rnd.choice([x for x in users if x is not u])
            roll = rnd.random()
            if roll < 0.3:
                r = u.pay(v.handle, rnd.randint(1, 3000))
                if r.status == 201:
                    with lock:
                        made.append((u, v, r.json["payment_id"], r.json["created_at"]))
                elif r.status != 409:
                    problems.append("pay %r" % r)
            elif made and roll < 0.55:
                with lock:
                    snd, rcv, pid, c = rnd.choice(made)
                r = rcv.post("/payments/%s/refunds" % pid, {"amount": rnd.randint(1, 1500)}, key=fresh_key())
                if r.status not in (201, 409, 422):
                    problems.append("refund %r" % r)
                elif r.status == 422 and r.code not in ("refund_exceeds_payment", "invalid_refund_target"):
                    problems.append("refund 422 %r" % r)
            elif made and roll < 0.75:
                with lock:
                    snd, rcv, pid, c = rnd.choice(made)
                try:
                    rv = revisions(snd, pid)
                except AssertionError:
                    continue
                r = correct(snd, pid, len(rv), rnd.randint(0, 3500), fmt(inst(c) - timedelta(seconds=rnd.choice([0, 0, 5]))), "storm")
                if r.status not in (201, 409, 422):
                    problems.append("correction %r" % r)
            elif made:
                with lock:
                    picks = rnd.sample(made, min(len(made), rnd.randint(1, 3)))
                items = []
                for snd, rcv, pid, c in picks:
                    try:
                        rv = revisions(snd, pid)
                    except AssertionError:
                        break
                    items.append(item(pid, len(rv), rnd.randint(0, 3500), c, "storm batch"))
                if items:
                    r = op.post("/correction-batches", {"corrections": items}, key=fresh_key())
                    if r.status not in (201, 409, 422):
                        problems.append("batch %r" % r)

    def sampler():
        while not stop.is_set():
            u = random.choice(users)
            m = lib2.me_ok(u)
            st = stmt(u, limit=200)
            if not st["has_more"] and st["opening_balance"] + sum(e["delta"] for e in st["entries"]) != st["closing_balance"]:
                problems.append("statement arithmetic broken under load")
            if st["closing_balance"] < 0:
                problems.append("negative statement closing balance")
            for h, s in snaps.items():
                j = stmt(w[h], snapshot=s["snapshot"], limit=5)
                if j["entries"] != s["entries"]:
                    problems.append("snapshot of %s changed under load" % h)

    with ThreadPoolExecutor(max_workers=12) as ex:
        sam = [ex.submit(sampler) for _ in range(2)]
        ws = [ex.submit(worker, s) for s in range(8)]
        for f in ws:
            f.result()
        stop.set()
        for f in sam:
            f.result()
    ok(not problems, "; ".join(problems[:3]))
    eq(sum(lib2.me_ok(u)["total"] for u in w.values()), total0, "the sum of totals equals the seeded total")
    ancient = "1970-01-01T00:00:00+00:00"
    for h in handles:
        u = w[h]
        m = lib2.me_ok(u)
        st = full_stmt(u)
        eq(st["closing"], m["balance"], "closing_balance == balance for %s" % h)
        eq(st["opening"], 20000, "opening balance of %s" % h)
        eq(me_at(u, as_of=ancient)["balance"], 20000, "as_of 1970 for %s" % h)
        eq(me_at(u, as_of="2999-01-01T00:00:00+00:00", known_at="2999-01-01T00:00:00+00:00")["balance"], m["balance"], "future views")
    # refunds never exceed the corrected amount of their target
    allp = {}
    for u in users:
        for p in feed4(u):
            allp[p["payment_id"]] = p
    refunded = {}
    for p in allp.values():
        if p["refund_of"]:
            refunded[p["refund_of"]] = refunded.get(p["refund_of"], 0) + p["amount"]
            ok(allp[p["refund_of"]]["refund_of"] is None, "a refund of a refund exists")
    for pid, tot in refunded.items():
        owner = w[allp[pid]["from_handle"]]
        cur = revisions(owner, pid)[-1]["amount"]
        ok(tot <= cur, "payment %s: refunded %d exceeds its current amount %d" % (pid, tot, cur))
    eq(sum(me_at(u, as_of=ancient)["balance"] for u in w.values()), total0, "historical sum")
