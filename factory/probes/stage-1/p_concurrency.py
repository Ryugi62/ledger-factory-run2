"""Concurrent writes: overdraft, conservation, unique ids, retries mixed with fresh work."""
import random
import threading
import time

from lib import *  # noqa: F401,F403


@test("S1-006", "S1-111", "S1-005", "S1-117", "S1-016")
def test_overdraft_burst():
    w = world({"ada": 1000, "b": 0, "c": 0, "d": 0, "e": 0})
    ada = w["ada"]
    targets = ["b", "c", "d", "e"]
    res = burst([lambda i=i: ada.pay(targets[i % 4], 100) for i in range(40)], workers=40)
    codes = sorted(r.status for r in res)
    eq(codes.count(201), 10, "exactly ten 100-unit payments fit in 1000: %r" % codes)
    for r in res:
        if r.status != 201:
            expect(r, 409, "insufficient_funds", "the rest")
    eq(ada.balance, 0, "drained, never negative")
    eq(sum(w[t].balance for t in targets), 1000, "everything arrived")
    # odd amounts: 1000 / 300 => three succeed, 100 left
    w = world({"ada": 1000, "b": 0})
    ada = w["ada"]
    res = burst([lambda: ada.pay("b", 300) for _ in range(25)], workers=25)
    eq(sorted(r.status for r in res).count(201), 3, "three 300-unit payments fit")
    eq(ada.balance, 100, "100 left")


@test("S1-006", "S1-005", "S1-117", "S1-016", "S1-017")
def test_two_way_traffic_conserves_money_and_does_not_deadlock():
    w = world({"ada": 500, "bob": 500})
    ada, bob = w["ada"], w["bob"]
    fns = []
    for i in range(50):
        fns.append((lambda: ada.pay("bob", 3)) if i % 2 == 0 else (lambda: bob.pay("ada", 5)))
    res = burst(fns, workers=50)
    ok(all(r.status == 201 for r in res), "all fit: %r" % sorted({r.status for r in res}))
    eq(ada.balance, 500 - 25 * 3 + 25 * 5, "ada")
    eq(bob.balance, 500 + 25 * 3 - 25 * 5, "bob")
    ledger_consistent([ada, bob], {"ada": 500, "bob": 500}, "two-way")
    # a tight loop of opposite payments with barely enough money
    w = world({"ada": 10, "bob": 10})
    ada, bob = w["ada"], w["bob"]
    res = burst([(lambda: ada.pay("bob", 10)) if i % 2 == 0 else (lambda: bob.pay("ada", 10)) for i in range(40)], workers=40)
    for r in res:
        ok(r.status in (201, 409), "unexpected %s" % r.status)
    eq(ada.balance + bob.balance, 20, "conservation")
    ok(ada.balance >= 0 and bob.balance >= 0, "never negative")
    ledger_consistent([ada, bob], {"ada": 10, "bob": 10}, "tight two-way")


@test("S1-034", "S1-105")
def test_ids_are_unique_under_concurrency():
    w = world({"ada": 100_000, "bob": 0, "cy": 0})
    ada, bob, cy = w["ada"], w["bob"], w["cy"]
    res = burst([lambda: ada.pay("bob", 1) for _ in range(50)], workers=50)
    ids = [r.json["payment_id"] for r in res]
    eq(len(set(ids)), 50, "payment ids unique")
    res = burst([lambda: bob.request("ada", 1) for _ in range(50)], workers=50)
    ids = [r.json["request_id"] for r in res]
    eq(len(set(ids)), 50, "request ids unique")
    res = burst([lambda: ada.split(10, ["ada", "bob", "cy"]) for _ in range(30)], workers=30)
    ids = [r.json["split_id"] for r in res]
    eq(len(set(ids)), 30, "split ids unique")
    rids = [q["request_id"] for r in res for q in r.json["requests"]]
    eq(len(set(rids)), 60, "split request ids unique")
    eq(len(ada.all_requests(direction="outgoing")), 60, "all split requests exist exactly once")
    eq(bob.balance, 50, "balances")


@test("S1-005", "S1-006", "S1-007", "S1-009", "S1-105", "S1-117", "S1-207", "S1-016")
def test_mixed_storm_with_retries_and_a_sampler():
    seed = dict((h, 300) for h in ("a", "b", "c", "d", "e", "f"))
    w = world(seed)
    us = [w[h] for h in seed]
    rng = random.Random(7)
    stop = []
    low = []

    def sampler():
        while not stop:
            for u in us:
                r = u.get("/me")
                if r.status == 200 and r.json["balance"] < 0:
                    low.append(r.json["balance"])

    t = threading.Thread(target=sampler)
    t.start()
    try:
        # requests to be paid/declined/cancelled concurrently
        pending = []
        for i in range(30):
            a, b = us[i % 6], us[(i + 1 + i % 4) % 6]
            if a is b:
                b = us[(i + 2) % 6]
            r = expect(a.request(b.handle, 40 + i % 7), 201).json
            pending.append((a, b, r["request_id"]))
        fns = []
        # each unit of work is sent three times with the same key (a client retrying)
        for i in range(30):
            a, b = us[rng.randrange(6)], us[rng.randrange(6)]
            if a is b:
                continue
            k = fresh_key()
            amt = rng.choice([1, 7, 50, 120, 299, 300])
            fns += [lambda a=a, b=b, k=k, amt=amt: a.pay(b.handle, amt, key=k) for _ in range(3)]
        for a, b, rid in pending:
            k = fresh_key()
            fns += [lambda b=b, rid=rid, k=k: b.pay_request(rid, key=k) for _ in range(2)]
            if rng.random() < 0.5:
                fns.append(lambda b=b, rid=rid: b.post("/requests/%s/decline" % rid))
            else:
                fns.append(lambda a=a, rid=rid: a.post("/requests/%s/cancel" % rid))
        for i in range(10):
            fns.append(lambda i=i: us[i % 6].split(30, [u.handle for u in us[:3 + i % 3]]))
        rng.shuffle(fns)
        res = burst(fns[:50] if len(fns) > 50 else fns, workers=50)
        res2 = burst(fns[50:100], workers=50) if len(fns) > 50 else []
        res3 = burst(fns[100:150], workers=50) if len(fns) > 100 else []
    finally:
        stop.append(1)
        t.join()
    ok(not low, "a balance below zero was observed: %r" % low[:5])
    for r in res + res2 + res3:
        ok(r.status in (200, 201, 409), "unexpected status %s: %s" % (r.status, r.text[:120]))
    eq(total(us), 1800, "conservation")
    ledger_consistent(us, seed, "storm")
    # every paid request has exactly one payment, nothing else has any
    pays = {}
    for u in us:
        for p in u.all_payments():
            if p["request_id"]:
                pays.setdefault(p["request_id"], set()).add(p["payment_id"])
    for rid, ids in pays.items():
        eq(len(ids), 1, "request %s moved money more than once" % rid)
    for u in us:
        for q in u.all_requests():
            if q["status"] == "paid":
                ok(q["payment_id"] in pays.get(q["request_id"], set()), "paid request %s has no matching payment" % q["request_id"])
            else:
                ok(q["request_id"] not in pays, "request %s is %s but has a payment" % (q["request_id"], q["status"]))
                eq(q["payment_id"], None, "payment_id only on paid requests")
