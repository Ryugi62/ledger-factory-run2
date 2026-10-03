"""POST /settlements: operators, net affordability, atomicity, receipts and visibility."""
import json

from lib import *  # noqa: F401,F403


def T(frm, to, amount, **extra):
    d = {"from_handle": frm, "to_handle": to, "amount": amount}
    d.update(extra)
    return d


def no_trace(users, balances, why=""):
    for u in users:
        eq(u.balance, balances[u.handle], "balance of %s changed %s" % (u.handle, why))
        ok(all(p["settlement_id"] is None for p in u.all_payments()), "a member payment leaked %s" % why)


@test("S1-186", "S1-187", "S1-189", "S1-070", "S1-071", "S1-003")
def test_only_operators_may_settle():
    w = world({"ada": 100, "bob": 0, "op": 0}, operators=["op"])
    ada, bob, op = w["ada"], w["bob"], w["op"]
    body = {"transfers": [T("ada", "bob", 10)]}
    expect(http("POST", "/settlements", body=body, key=fresh_key()), 401, "unauthenticated")
    expect(http("POST", "/settlements", body=body, key=fresh_key(), headers={"Authorization": "Bearer nope"}), 401, "unauthenticated")
    expect(ada.post("/settlements", body, key=fresh_key()), 403, "forbidden", "a party to the transfer is not an operator")
    expect(bob.post("/settlements", body, key=fresh_key()), 403, "forbidden", "the receiver is not an operator")
    signed = new_user("signedup@example.com")
    expect(signed.post("/settlements", body, key=fresh_key()), 403, "forbidden", "signed up users are never operators")
    eq((ada.balance, bob.balance), (100, 0), "forbidden attempts moved nothing")
    # the operator need not be a party and holds no money
    r = expect(op.post("/settlements", body, key=fresh_key()), 201, msg="operator settles across wallets")
    eq((ada.balance, bob.balance, op.balance), (90, 10, 0), "wallets moved")
    # a 403 does not claim the key for the operator either
    k = fresh_key()
    expect(ada.post("/settlements", body, key=k), 403, "forbidden")
    expect(op.post("/settlements", body, key=k), 201, msg="same key string, different user")
    # no operators at all by default
    reset(fixture([fx_user("ada", 100), fx_user("bob", 0)]))
    ada = login("ada@example.com", handle="ada")
    expect(ada.post("/settlements", body, key=fresh_key()), 403, "forbidden", "default settlement_operator_ids is []")
    # operator ids that name several users
    reset(fixture([fx_user("ada", 100), fx_user("bob", 0), fx_user("cy", 0)], operators=["u_ada", "u_cy"]))
    ada, bob, cy = (login(h + "@example.com", handle=h) for h in ("ada", "bob", "cy"))
    expect(ada.settle([T("ada", "bob", 1)]), 201, msg="operator and party")
    expect(cy.settle([T("ada", "bob", 1)]), 201, msg="second operator")
    expect(bob.settle([T("ada", "bob", 1)]), 403, "forbidden")


@test("S1-198", "S1-199", "S1-200", "S1-201", "S1-188", "S1-191", "S1-052", "S1-110", "S1-100", "S1-202", "S1-109")
def test_settlement_receipts_shape_and_visibility():
    w = world({"ada": 1000, "bob": 0, "cy": 0, "dee": 500, "op": 0}, operators=["op"])
    ada, bob, cy, dee, op = w["ada"], w["bob"], w["cy"], w["dee"], w["op"]
    ordinary = expect(ada.pay("dee", 1), 201).json
    eq(ordinary["settlement_id"], None, "ordinary payments expose settlement_id: null")
    rq = expect(dee.request("ada", 2), 201).json["request_id"]
    paid = expect(ada.pay_request(rq), 201).json
    eq(paid["settlement_id"], None, "paid-request payments expose settlement_id: null")
    transfers = [T("ada", "bob", 100, note="n1", visibility="private"),
                 T("bob", "cy", 50),
                 T("cy", "dee", 25, note="  last \U0001F600 ", visibility="public", ignored_field=7)]
    k = fresh_key()
    r = expect(op.settle(transfers, key=k, ignored_at_top_level=True), 201)
    j = r.json
    ok(isinstance(j["settlement_id"], str) and 0 < len(j["settlement_id"]) <= 64, "settlement_id")
    commit = check_ts(j["committed_at"])
    eq(len(j["payments"]), 3, "one receipt per transfer, input order")
    sid = j["settlement_id"]
    p1, p2, p3 = j["payments"]
    check_payment(p1, frm=ada, to=bob, amount=100, note="n1", visibility="private", request_id=None, settlement=sid, currency="EUR")
    check_payment(p2, frm=bob, to=cy, amount=50, note="", visibility="public", request_id=None, settlement=sid)
    check_payment(p3, frm=cy, to=dee, amount=25, note="  last \U0001F600 ", visibility="public", request_id=None, settlement=sid)
    eq(len({p["payment_id"] for p in j["payments"]}), 3, "distinct payment ids")
    for p in j["payments"]:
        eq(parse_ts(p["created_at"]), commit, "every member's created_at equals committed_at")
    # balances: ada -100, bob +100-50, cy +50-25, dee +25
    eq((ada.balance, bob.balance, cy.balance, dee.balance, op.balance), (1000 - 1 - 2 - 100, 50, 25, 500 + 1 + 2 + 25, 0), "exact balances")
    # feeds follow the ordinary rule
    ids = lambda u: {p["payment_id"] for p in u.all_payments()}
    pid = [p["payment_id"] for p in j["payments"]]
    eq(ids(ada) & set(pid), set(pid), "ada: sender of 1, public others")
    eq(ids(bob) & set(pid), set(pid), "bob: party to the private one")
    eq(ids(cy) & set(pid), {pid[1], pid[2]}, "cy: the private transfer between ada and bob is hidden")
    eq(ids(dee) & set(pid), {pid[1], pid[2]}, "dee: the private transfer is hidden")
    eq(ids(op) & set(pid), {pid[1], pid[2]}, "the operator is not a party: the private transfer is hidden from them too")
    feed = {p["payment_id"]: p for p in bob.all_payments()}
    eq(feed[pid[0]], p1, "feed entry equals the receipt")
    eq(feed[pid[0]]["settlement_id"], sid, "settlement_id in the feed")
    eq({p["payment_id"]: p["settlement_id"] for p in dee.all_payments()}[ordinary["payment_id"]], None, "non-member stays null")
    # members show up identically for everyone who can see them
    eq({p["payment_id"]: p for p in cy.all_payments()}[pid[1]], p2, "public member as seen by a third party")
    # replay: 200, original complete response
    r2 = expect(op.settle(transfers, key=k, ignored_at_top_level=True), 200, msg="replay")
    eq(r2.json, j, "replay returns the original response, including private receipts and committed_at")
    eq(len(ada.all_payments()), 5, "no new payments from the replay: ordinary 2 + 3 members")
    # operator's own requests/feed contain nothing of others'
    eq(op.all_requests(), [], "operator sees no requests")
    eq(len(op.all_payments()), 4, "operator's feed: 2 ordinary public + 2 public members")
    # two settlements get different ids and timestamps order
    r3 = expect(op.settle([T("dee", "ada", 1)]), 201).json
    ok(r3["settlement_id"] != sid, "settlement ids are unique")
    ok(parse_ts(r3["committed_at"]) >= commit, "committed_at moves forward")


@test("S1-195", "S1-196", "S1-197", "S1-103", "S1-006", "S1-005")
def test_net_affordability():
    # (a) chain through a wallet that holds nothing: net 0 after receiving and sending
    for order in ("forward", "reversed"):
        w = world({"ada": 1000, "bob": 0, "cy": 0, "op": 0}, operators=["op"])
        ada, bob, cy, op = w["ada"], w["bob"], w["cy"], w["op"]
        tr = [T("ada", "bob", 100), T("bob", "cy", 100)]
        if order == "reversed":
            tr.reverse()
        r = expect(op.settle(tr), 201, msg="chain through an empty wallet (%s)" % order)
        eq((ada.balance, bob.balance, cy.balance), (900, 0, 100), "chain result (%s)" % order)
    # (b) circular among wallets that hold nothing
    w = world({"e1": 0, "e2": 0, "e3": 0, "op": 0}, operators=["op"])
    op = w["op"]
    expect(op.settle([T("e1", "e2", 5), T("e2", "e3", 5), T("e3", "e1", 5)]), 201, msg="3-cycle among empty wallets")
    expect(op.settle([T("e1", "e2", 7), T("e2", "e1", 7)]), 201, msg="2-cycle among empty wallets")
    eq([w[h].balance for h in ("e1", "e2", "e3")], [0, 0, 0], "cycles net to zero")
    eq(len(w["e1"].all_payments()), 5, "five member payments exist")
    # (c) net -1 fails the whole batch, key stays reusable
    w = world({"ada": 1000, "bob": 0, "cy": 0, "op": 0}, operators=["op"])
    ada, bob, cy, op = w["ada"], w["bob"], w["cy"], w["op"]
    bal = {"ada": 1000, "bob": 0, "cy": 0}
    k = fresh_key()
    expect(op.settle([T("ada", "bob", 99), T("bob", "cy", 100)], key=k), 409, "insufficient_funds")
    no_trace([ada, bob, cy], bal, "after a failed settlement")
    r = expect(op.settle([T("ada", "bob", 100), T("bob", "cy", 100)], key=k), 201, msg="same key, corrected body")
    eq((ada.balance, bob.balance, cy.balance), (900, 0, 100), "corrected batch")
    # (d) one transfer larger than the balance
    expect(op.settle([T("bob", "ada", 1)]), 409, "insufficient_funds")
    expect(op.settle([T("cy", "ada", 101)]), 409, "insufficient_funds")
    expect(op.settle([T("ada", "cy", 901)]), 409, "insufficient_funds")
    eq((ada.balance, bob.balance, cy.balance), (900, 0, 100), "unchanged")
    # (e) exactly the whole balance
    expect(op.settle([T("ada", "bob", 900)]), 201, msg="whole balance")
    eq(ada.balance, 0, "drained to zero")
    # (f) outgoing exceeds the balance but incoming from the same batch covers it
    w = world({"ada": 1000, "bob": 0, "cy": 0, "op": 0}, operators=["op"])
    ada, bob, cy, op = w["ada"], w["bob"], w["cy"], w["op"]
    tr = [T("ada", "bob", 1000), T("ada", "cy", 500), T("bob", "ada", 600)]
    expect(op.settle(tr), 201, msg="net semantics: ada nets -900 although she sends 1500")
    eq((ada.balance, bob.balance, cy.balance), (100, 400, 500), "net result")
    # (g) same pair repeatedly, all-or-nothing on the last entry
    w = world({"ada": 10, "bob": 0, "op": 0}, operators=["op"])
    ada, bob, op = w["ada"], w["bob"], w["op"]
    expect(op.settle([T("ada", "bob", 3)] * 3), 201, msg="three equal transfers")
    eq((ada.balance, bob.balance), (1, 9), "3 x 3")
    expect(op.settle([T("ada", "bob", 1), T("ada", "bob", 1)]), 409, "insufficient_funds")
    eq((ada.balance, bob.balance), (1, 9), "second entry overdraws, first must not commit")
    # (h) 32 transfers all valid
    w = world({"ada": 1000, "bob": 0, "op": 0}, operators=["op"])
    ada, bob, op = w["ada"], w["bob"], w["op"]
    r = expect(op.settle([T("ada", "bob", 1)] * 32), 201, msg="32 transfers")
    eq(len(r.json["payments"]), 32, "32 receipts")
    eq((ada.balance, bob.balance), (968, 32), "32 moved")


@test("S1-190", "S1-191", "S1-192", "S1-193", "S1-194", "S1-058", "S1-114", "S1-036", "S1-037", "S1-076",
      "S1-077", "S1-115", "S1-116", "S1-197", "S1-075", "S1-074", "S1-068")
def test_settlement_validation_and_precedence():
    w = world({"ada": 5_000_000_000, "bob": 0, "cy": 0, "op": 0}, operators=["op"])
    ada, bob, cy, op = w["ada"], w["bob"], w["cy"], w["op"]
    bal = {"ada": 5_000_000_000, "bob": 0, "cy": 0}
    good = T("ada", "bob", 10)

    # shape problems: 422 validation_failed
    def shape(body, why):
        k = fresh_key()
        expect(op.post("/settlements", body, key=k), 422, "validation_failed", why)
        eq((ada.balance, bob.balance, cy.balance), (bal["ada"], bal["bob"], bal["cy"]), "balances after %s" % why)

    shape({}, "missing transfers")
    shape({"transfers": []}, "empty transfers")
    shape({"transfers": "x"}, "transfers is a string")
    shape({"transfers": {"a": 1}}, "transfers is an object")
    shape({"transfers": None}, "transfers is null")
    shape({"transfers": 5}, "transfers is a number")
    shape({"transfers": [good] * 33}, "33 transfers")
    shape({"transfers": [1]}, "entry is a number")
    shape({"transfers": ["ada"]}, "entry is a string")
    shape({"transfers": [None]}, "entry is null")
    shape({"transfers": [[good]]}, "entry is an array")
    shape({"transfers": [good, 7]}, "second entry is not an object")
    shape({"transfers": [{"to_handle": "bob", "amount": 1}]}, "missing from_handle")
    shape({"transfers": [{"from_handle": "ada", "amount": 1}]}, "missing to_handle")
    shape({"transfers": [{"from_handle": "ada", "to_handle": "bob"}]}, "missing amount")
    for amt in (0, -1, 1_000_000_001, 1.5, "10", True, False, None, [], {}):
        shape({"transfers": [T("ada", "bob", amt)]}, "amount %r" % (amt,))
        shape({"transfers": [good, T("ada", "cy", amt)]}, "amount %r in the second entry" % (amt,))
    for note in (None, 5, [], {}, True, "x" * 201, "\U0001F600" * 201):
        shape({"transfers": [T("ada", "bob", 1, note=note)]}, "note %r" % (str(note)[:20],))
    for vis in ("PUBLIC", "friends", "", None, 5, [], {}):
        shape({"transfers": [T("ada", "bob", 1, visibility=vis)]}, "visibility %r" % (vis,))
    # 404 and self_payment
    k = fresh_key()
    expect(op.post("/settlements", {"transfers": [T("ada", "nobody", 1)]}, key=k), 404, "not_found")
    expect(op.post("/settlements", {"transfers": [T("nobody", "ada", 1)]}, key=k), 404, "not_found")
    expect(op.post("/settlements", {"transfers": [T("ada", "ada", 1)]}, key=k), 422, "self_payment")
    expect(op.post("/settlements", {"transfers": [good, T("bob", "bob", 1)]}, key=k), 422, "self_payment")
    eq((ada.balance, bob.balance, cy.balance), (bal["ada"], bal["bob"], bal["cy"]), "nothing moved")
    # precedence: entry errors in input order, before insufficient funds
    huge = 1_000_000
    for transfers, status, code, why in [
        ([T("bob", "cy", huge), T("ada", "nobody", 1)], 404, "not_found", "unaffordable then unknown handle"),
        ([T("bob", "cy", huge), T("ada", "ada", 1)], 422, "self_payment", "unaffordable then self"),
        ([T("ada", "ada", 1), T("ada", "nobody", 1)], 422, "self_payment", "self then unknown"),
        ([T("ada", "nobody", 1), T("ada", "ada", 1)], 404, "not_found", "unknown then self"),
        ([T("bob", "cy", huge), good, T("ada", "bob", 0)], 422, "validation_failed", "unaffordable then bad amount"),
        ([T("bob", "cy", huge), T("ada", "bob", 1, visibility="nope")], 422, "validation_failed", "unaffordable then bad visibility"),
        ([T("bob", "cy", huge)], 409, "insufficient_funds", "plain insufficient funds"),
        ([good, good, T("ada", "zzz", 1)], 404, "not_found", "valid entries then unknown"),
    ]:
        expect(op.post("/settlements", {"transfers": transfers}, key=fresh_key()), status, code, why)
        eq((ada.balance, bob.balance, cy.balance), (bal["ada"], bal["bob"], bal["cy"]), "nothing moved: " + why)
    for u in (ada, bob, cy):
        eq([p for p in u.all_payments() if p["settlement_id"] is not None], [], "no member payment after failures")
    # numeric forms and bounds
    for lit in ("1000.0", "1e3", "1000"):
        r = expect(http("POST", "/settlements", raw='{"transfers":[{"from_handle":"ada","to_handle":"bob","amount":%s}]}' % lit,
                        token=op.token, key=fresh_key()), 201, msg="amount literal %s" % lit)
        eq(r.json["payments"][0]["amount"], 1000, "amount")
    r = expect(op.settle([T("ada", "bob", 1_000_000_000)]), 201, msg="max amount")
    eq(r.json["payments"][0]["amount"], 1_000_000_000, "max")
    expect(op.settle([T("ada", "bob", 1_000_000_001)]), 422, "validation_failed", "max + 1")
    # note limits
    expect(op.settle([T("ada", "bob", 1, note="x" * 200)]), 201, msg="200-char note")
    expect(op.settle([T("ada", "bob", 1, note="\U0001F600" * 200)]), 201, msg="200 emoji note")
    # unknown fields everywhere
    expect(http("POST", "/settlements", body={"transfers": [dict(T("ada", "cy", 5), zzz=[1])], "a": {"b": 1}},
                token=op.token, key=fresh_key()), 201, msg="unknown fields ignored")
    # 1 transfer and 32 transfers are fine
    expect(op.settle([T("ada", "bob", 1)] * 32), 201, msg="32 entries")
    # wrong JSON types at body level
    for raw in ("{", "", "[]", "7"):
        expect(http("POST", "/settlements", raw=raw, token=op.token, key=fresh_key()), 400, "malformed_request", repr(raw))


@test("S1-205", "S1-197", "S1-006", "S1-005", "S1-007")
def test_competing_settlements_and_payments_never_overdraw():
    w = world({"ada": 1000, "bob": 0, "cy": 0, "op": 0}, operators=["op"])
    ada, bob, cy, op = w["ada"], w["bob"], w["cy"], w["op"]
    batch = [T("ada", "bob", 300), T("bob", "cy", 100)]
    fns = [(lambda: ("settle", op.settle(batch))) for _ in range(12)] + \
          [(lambda: ("pay", ada.pay("cy", 250))) for _ in range(12)]
    res = burst(fns, workers=24)
    S = sum(1 for kind, r in res if kind == "settle" and r.status == 201)
    P = sum(1 for kind, r in res if kind == "pay" and r.status == 201)
    for kind, r in res:
        ok(r.status in (201, 409), "unexpected status %s: %s" % (r.status, r.text[:100]))
        if r.status == 409:
            eq(r.code, "insufficient_funds", "losers")
    eq(ada.balance, 1000 - 300 * S - 250 * P, "ada balance matches the successes")
    ok(ada.balance >= 0, "never negative")
    eq(bob.balance, 200 * S, "bob nets +200 per committed settlement")
    eq(cy.balance, 100 * S + 250 * P, "cy")
    eq(ada.balance + bob.balance + cy.balance, 1000, "conservation")
    members = [p for p in ada.all_payments() if p["settlement_id"]]
    eq(len(members), 2 * S, "every committed settlement has exactly its two members, nothing partial")
    eq(len({p["settlement_id"] for p in members}), S, "distinct settlements")
    for kind, r in res:
        if r.status == 409:
            # balances only fall, so a failed operation implies the final balance cannot afford it
            ok(ada.balance < (300 if kind == "settle" else 250), "a %s failed although the funds were there at the end" % kind)
    # opposite-direction batches issued concurrently must neither deadlock nor lose money
    w = world({"ada": 100, "bob": 100, "op": 0}, operators=["op"])
    ada, bob, op = w["ada"], w["bob"], w["op"]
    fns = [(lambda: op.settle([T("ada", "bob", 10), T("bob", "ada", 10)])) if i % 2 else
           (lambda: op.settle([T("bob", "ada", 10), T("ada", "bob", 10)])) for i in range(30)]
    res = burst(fns, workers=30)
    ok(all(r.status == 201 for r in res), "crossing settlements: %r" % sorted({r.status for r in res}))
    eq((ada.balance, bob.balance), (100, 100), "net zero")
    eq(len([p for p in ada.all_payments() if p["settlement_id"]]), 60, "all members present")


@test("S1-205", "S1-197", "S1-006", "S1-005", "S1-117")
def test_mixed_storm_conserves_money():
    names = ["a", "b", "c", "d"]
    w = world(dict([(n, 500) for n in names] + [("op", 0)]), operators=["op"])
    us = [w[n] for n in names]
    op = w["op"]
    stop = []
    samples = []

    import threading

    def sampler():
        while not stop:
            for u in us:
                r = u.get("/me")
                if r.status == 200:
                    samples.append(r.json["balance"])

    t = threading.Thread(target=sampler)
    t.start()
    try:
        fns = []
        for i in range(60):
            a, b = us[i % 4], us[(i + 1) % 4]
            if i % 3 == 0:
                fns.append(lambda a=a, b=b: a.pay(b.handle, 90))
            elif i % 3 == 1:
                fns.append(lambda a=a, b=b, c=us[(i + 2) % 4]: op.settle([T(a.handle, b.handle, 70), T(b.handle, c.handle, 40)]))
            else:
                fns.append(lambda a=a, b=b: a.pay(b.handle, 1))
        res = burst(fns, workers=50)
    finally:
        stop.append(1)
        t.join()
    for r in res:
        ok(r.status in (201, 409), "unexpected status %s %s" % (r.status, r.text[:100]))
    ok(samples and min(samples) >= 0, "a balance was observed below zero: %r" % (min(samples) if samples else None))
    eq(total(us), 2000, "conservation after the storm")
    ok(all(u.balance >= 0 for u in us), "no negative balance")
