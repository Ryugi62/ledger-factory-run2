"""Stage 2 concurrency: holds, captures, voids, payments and settlements racing; every read is consistent."""
import threading
import time

from lib2 import *  # noqa: F401,F403


@test("S2-171", "S2-091", "S2-090", "S2-117")
def test_authorize_and_pay_bursts_never_over_reserve():
    w = world2({"ada": 1000, "b": 0, "c": 0})
    ada = w["ada"]
    res = burst([lambda i=i: authorize(ada, "b" if i % 2 else "c", 100) for i in range(30)], workers=30)
    codes = sorted(r.status for r in res)
    eq(codes.count(201), 10, "exactly ten 100-unit holds fit in 1000: %r" % codes)
    for r in res:
        if r.status != 201:
            expect(r, 409, "insufficient_funds", "the rest")
    m = me_ok(ada)
    eq((m["available"], m["held"], m["total"]), (0, 1000, 1000), "fully held")
    # payments and authorizations compete for the same available money
    w = world2({"ada": 1000, "b": 0})
    ada = w["ada"]
    fns = [(lambda: ada.pay("b", 100)) for _ in range(15)] + [(lambda: authorize(ada, "b", 100)) for _ in range(15)]
    res = burst(fns, workers=30)
    wins = sum(1 for r in res if r.status == 201)
    eq(wins, 10, "payments + holds share 1000 available: %r" % sorted(r.status for r in res))
    m = me_ok(ada)
    eq(m["available"], 0, "nothing left")
    eq(m["total"] + me_ok(w["b"])["total"], 1000, "conservation of totals")
    eq(m["total"], 1000 - 100 * sum(1 for r in res[:15] if r.status == 201), "only payments moved money")
    eq(m["held"], 100 * sum(1 for r in res[15:] if r.status == 201), "only authorizations hold")


@test("S2-171", "S2-092", "S2-088", "S2-090")
def test_concurrent_captures_never_exceed_the_authorization():
    w = world2({"ada": 10_000, "bob": 0})
    ada, bob = w["ada"], w["bob"]
    aid = expect(authorize(ada, "bob", 1000), 201).json["authorization_id"]
    res = burst([lambda: capture(bob, aid, {"amount": 100, "final": False}) for _ in range(25)], workers=25)
    codes = [r.status for r in res]
    eq(codes.count(201), 10, "exactly ten 100-unit captures fit: %r" % sorted(codes))
    for r in res:
        if r.status != 201:
            ok((r.status, r.code) in ((422, "capture_exceeds_authorization"), (409, "authorization_not_open")),
               "loser: %s %s" % (r.status, r.code))
    a = auth_by_id(bob, aid)
    check_auth(a, status="captured", captured=1000, remaining=0)
    eq(len(a["payment_ids"]), 10, "ten payments")
    m = me_ok(ada)
    eq((m["total"], m["held"], m["available"]), (9000, 0, 9000), "exactly 1000 moved")
    eq(me_ok(bob)["total"], 1000, "receiver")
    # concurrent full captures with default amount and different keys: one wins
    aid = expect(authorize(ada, "bob", 500), 201).json["authorization_id"]
    res = burst([lambda: capture(bob, aid, {}) for _ in range(20)], workers=20)
    eq(sorted(r.status for r in res).count(201), 1, "one final capture")
    for r in res:
        if r.status != 201:
            expect(r, 409, "authorization_not_open")
    eq(me_ok(bob)["total"], 1500, "moved once")
    # a mix of sizes: never more than the authorisation
    aid = expect(authorize(ada, "bob", 400), 201).json["authorization_id"]
    fns = [(lambda: capture(bob, aid, {"amount": 300, "final": False})) for _ in range(6)] + \
          [(lambda: capture(bob, aid, {"amount": 200, "final": False})) for _ in range(6)]
    res = burst(fns, workers=12)
    got = sum(r.json["amount"] for r in res if r.status == 201)
    ok(got <= 400, "captured %d of 400" % got)
    a = auth_by_id(bob, aid)
    eq(a["captured_amount"], got, "captured_amount equals the sum of the successful captures")
    eq(a["remaining_amount"], 400 - got, "remaining")
    m = me_ok(ada)
    eq(m["held"], 400 - got if a["status"] == "open" else 0, "held equals what is still open")


@test("S2-171", "S2-092", "S2-144", "S2-088", "S2-091")
def test_capture_versus_void_and_spending():
    w = world2({"ada": 10_000, "bob": 0})
    ada, bob = w["ada"], w["bob"]
    for round_ in range(8):
        aid = expect(authorize(ada, "bob", 500), 201).json["authorization_id"]
        fns = ([lambda: ("capture", capture(bob, aid, {})) for _ in range(4)] +
               [lambda: ("void", void(ada, aid)) for _ in range(4)])
        res = burst(fns)
        a = auth_by_id(ada, aid)
        ok(a["status"] in ("captured", "voided"), "closed: %s" % a["status"])
        caps = [r for k, r in res if k == "capture"]
        voids = [r for k, r in res if k == "void"]
        if a["status"] == "captured":
            eq(sum(1 for r in caps if r.status == 201), 1, "exactly one capture won")
            for r in voids:
                expect(r, 409, "authorization_not_open", "voids after the capture")
            eq(a["captured_amount"], 500, "captured")
        else:
            for r in caps:
                expect(r, 409, "authorization_not_open", "captures after the void")
            for r in voids:
                expect(r, 200, msg="every void of a voided authorization is 200")
            eq(a["captured_amount"], 0, "nothing captured")
        eq(me_ok(ada)["held"], 0, "nothing stays held")
    n_captured = len([a for a in all_auths(ada) if a["status"] == "captured"])
    eq(me_ok(ada)["total"], 10_000 - 500 * n_captured, "totals match the captures")
    # the receiver captures while the payer tries to spend everything else
    w = world2({"ada": 1000, "bob": 0})
    ada, bob = w["ada"], w["bob"]
    aid = expect(authorize(ada, "bob", 1000), 201).json["authorization_id"]
    fns = [(lambda: ("pay", ada.pay("bob", 50))) for _ in range(20)] + [(lambda: ("cap", capture(bob, aid, {})))]
    res = burst(fns)
    for k, r in res:
        if k == "pay":
            expect(r, 409, "insufficient_funds", "held money cannot be spent by payments")
        else:
            expect(r, 201, msg="the capture may spend the reserved money")
    eq((me_ok(ada)["total"], me_ok(bob)["total"]), (0, 1000), "all captured, nothing overspent")


def _consistent(j, label):
    for k in ("balance", "total", "available", "held"):
        if not is_int(j.get(k)):
            return "%s: %s missing/non-integer in %r" % (label, k, j)
    if j["balance"] != j["total"]:
        return "%s: balance %r != total %r" % (label, j["balance"], j["total"])
    if j["available"] != j["total"] - j["held"]:
        return "%s: available %r != total %r - held %r" % (label, j["available"], j["total"], j["held"])
    if j["held"] < 0 or j["available"] < 0:
        return "%s: negative held/available %r" % (label, j)
    return None


@test("S2-171", "S2-090", "S2-091", "S2-092", "S2-112", "S2-096")
def test_mixed_storm_with_holds_and_a_sampler():
    seed = dict((h, 600) for h in ("a", "b", "c", "d"))
    w = world2(dict(list(seed.items()) + [("op", 0)]), operators=["op"])
    us = [w[h] for h in seed]
    op = w["op"]
    problems = []
    stop = []
    made = []                    # (payer, receiver, authorization id)
    lock = threading.Lock()

    def sampler():
        while not stop:
            for u in us:
                r = u.get("/me")
                if r.status == 200:
                    msg = _consistent(r.json, u.handle)
                    if msg:
                        problems.append(msg)

    import random

    def worker(seed_):
        rg = random.Random(seed_)
        end = time.time() + 3.0
        while time.time() < end:
            a, b = rg.sample(us, 2)
            op_ = rg.choice(["pay", "auth", "auth", "cap", "cap", "capf", "void", "settle", "pay"])
            if op_ == "pay":
                r = a.pay(b.handle, rg.choice([1, 20, 90]))
            elif op_ == "auth":
                r = authorize(a, b.handle, rg.choice([10, 50, 200]))
                if r.status == 201:
                    with lock:
                        made.append((a, b, r.json["authorization_id"]))
            elif op_ in ("cap", "capf", "void"):
                with lock:
                    pick = rg.choice(made) if made else None
                if not pick:
                    continue
                payer, receiver, aid = pick
                if op_ == "cap":
                    r = capture(receiver, aid, {"amount": rg.choice([5, 25, 100]), "final": False})
                elif op_ == "capf":
                    r = capture(receiver, aid, {"amount": rg.choice([5, 25, 100])})
                else:
                    r = void(payer, aid)
            else:
                c = rg.choice([u for u in us if u not in (a, b)])
                r = op.settle([{"from_handle": a.handle, "to_handle": b.handle, "amount": rg.choice([10, 40])},
                               {"from_handle": b.handle, "to_handle": c.handle, "amount": rg.choice([5, 20])}])
            if r.status not in (200, 201, 409, 422):
                problems.append("unexpected %s %s" % (r.status, r.text[:100]))
            time.sleep(0.005)

    ts = [threading.Thread(target=sampler)] + [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in ts:
        t.start()
    for t in ts[1:]:
        t.join()
    stop.append(1)
    ts[0].join()
    ok(not problems, "inconsistent /me read or bad status during the storm: %r" % problems[:5])
    # quiescent checks
    eq(sum(me_ok(u)["total"] for u in us), 2400, "sum of totals equals the seed")
    ledger_consistent(us, seed, "after the storm")        # total == seed - sent + received from each user's feed
    for u in us:
        m = me_ok(u)
        open_out = sum(a["remaining_amount"] for a in all_auths(u, direction="outgoing", status="open"))
        eq(m["held"], open_out, "%s: held equals the remaining amounts of their open outgoing authorizations" % u.handle)
        mine = u.all_payments()
        for a in all_auths(u, direction="outgoing"):
            check_auth(a)
            caps = [p for p in mine if p["authorization_id"] == a["authorization_id"]]
            eq(sum(p["amount"] for p in caps), a["captured_amount"],
               "captured_amount equals the capture payments of %s" % a["authorization_id"])
            ok(a["captured_amount"] <= a["amount"], "captured more than authorised")
            eq(len(a["payment_ids"]), len(caps), "payment_ids count")
