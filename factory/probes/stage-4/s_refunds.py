"""Stage 4: POST /payments/{id}/refunds."""
from datetime import timedelta

from lib4 import *  # noqa: F401,F403


def world_r(extra=None):
    bal = {"ada": 10000, "bob": 500, "cy": 0, "op": 0}
    if extra:
        bal.update(extra)
    return lib2.world2(bal, operators=["op"])


@test("S4-006", "S4-012", "S4-013", "S4-016", "S4-018", "S4-010", "S4-004", "S4-014", "S4-002")
def test_refund_creates_an_opposite_payment_and_replays():
    w = world_r()
    ada, bob, cy = w["ada"], w["bob"], w["cy"]
    key0 = fresh_key()
    body0 = {"to_handle": "bob", "amount": 500, "note": "n é", "visibility": "private"}
    p = expect(ada.post("/payments", body0, key=key0), 201).json
    check_payment4(p, refund_of=None)
    pid = p["payment_id"]
    start = {h: lib2.me_ok(u)["balance"] for h, u in w.items()}
    eq((start["ada"], start["bob"]), (9500, 1000), "after the payment")
    k = fresh_key()
    r = expect(bob.post("/payments/%s/refunds" % pid, {"amount": 200}, key=k), 201)
    rf = r.json
    check_payment4(rf, refund_of=pid)
    eq((rf["from_user_id"], rf["from_handle"], rf["to_user_id"], rf["to_handle"], rf["amount"]), (bob.id, "bob", ada.id, "ada", 200), "opposite direction")
    eq((rf["request_id"], rf["authorization_id"], rf["settlement_id"]), (None, None, None), "links are null")
    eq((rf["note"], rf["visibility"]), (p["note"], "private"), "the original note and visibility")
    lib.check_ts(rf["created_at"])
    ok(rf["payment_id"] != pid, "a new payment")
    eq((lib2.me_ok(ada)["balance"], lib2.me_ok(bob)["balance"]), (9700, 800), "money moved from the receiver to the sender")
    ok(rf["payment_id"] not in [x["payment_id"] for x in cy.all_payments()], "a private target gives a private refund: hidden from third parties")
    by = {x["payment_id"]: x for x in feed4(ada)}
    eq(by[rf["payment_id"]], rf, "the refund is in the sender's feed, as returned")
    eq({x["payment_id"]: x for x in feed4(bob)}[rf["payment_id"]], rf, "...and in the receiver's feed")
    eq(by[pid], p, "the original payment is unchanged in the feed (amount 500, refund_of null)")
    ok(inst(rf["created_at"]) >= inst(p["created_at"]), "the refund is later than its target")
    # replay
    rp = expect(bob.post("/payments/%s/refunds" % pid, {"amount": 200}, key=k), 200, msg="replay")
    eq(rp.json, rf, "replay returns the original body")
    eq(lib2.me_ok(bob)["balance"], 800, "replay moves nothing")
    expect(ada.post("/payments", body0, key=key0), 200, msg="the original payment receipt still replays")
    eq(expect(ada.post("/payments", body0, key=key0), 200).json, p, "unchanged original response")
    # revision history of the refund payment: revision 1 only, immutable
    rv = revisions(bob, rf["payment_id"])
    eq(len(rv), 1, "revision 1 only")
    same_instant(rv[0]["effective_at"], rf["created_at"], "effective_at = created_at")
    same_instant(rv[0]["recorded_at"], rf["created_at"], "recorded_at = created_at")
    eq(revisions(ada, rf["payment_id"]), rv, "both parties can read it")
    before = observe_state(w, [(bob, rf["payment_id"])])
    kk = fresh_key()
    for _ in range(2):
        expect(correct(bob, rf["payment_id"], 1, 100, rf["created_at"], "no", key=kk), 422, "linked_payment_immutable", "a refund payment cannot be corrected")
    expect(correct(bob, rf["payment_id"], 1, 0, rf["created_at"], "no"), 422, "linked_payment_immutable", "nor reversed")
    eq(observe_state(w, [(bob, rf["payment_id"])]), before, "rejected corrections leave no trace")
    # statements: the refund is an ordinary movement with refund_of set
    st = full_stmt(bob)
    es = [e for e in st["entries"] if e["payment"]["payment_id"] == rf["payment_id"]]
    eq(len(es), 1, "exactly one entry")
    eq((es[0]["delta"], es[0]["revision"], es[0]["payment"]["refund_of"]), (-200, 1, pid), "bob: negative delta, refund_of kept")
    ea = [e for e in full_stmt(ada)["entries"] if e["payment"]["payment_id"] == rf["payment_id"]]
    eq(ea[0]["delta"], 200, "ada: positive delta")
    eq(st["closing"], 800, "closing balance")
    for e in st["entries"]:
        ok("refund_of" in e["payment"], "refund_of on every statement payment")
    # cumulative refunds up to exactly the payment amount, then one more
    r2 = expect(bob.post("/payments/%s/refunds" % pid, {"amount": 100}, key=fresh_key()), 201).json
    eq(r2["refund_of"], pid, "second refund")
    expect(bob.post("/payments/%s/refunds" % pid, {"amount": 201}, key=fresh_key()), 422, "refund_exceeds_payment", "300 + 201 > 500")
    expect(bob.post("/payments/%s/refunds" % pid, {"amount": 200}, key=fresh_key()), 201, msg="exactly the remaining 200")
    eq(lib2.me_ok(bob)["balance"], 500, "bob: 1000 - 500")
    before = observe_state(w, [(bob, pid)])
    expect(bob.post("/payments/%s/refunds" % pid, {"amount": 1}, key=fresh_key()), 422, "refund_exceeds_payment", "fully refunded")
    eq(observe_state(w, [(bob, pid)]), before, "a rejected refund changes nothing")
    eq(sum(lib2.me_ok(u)["balance"] for u in w.values()), 10500, "conservation")


@test("S4-006", "S4-007", "S4-009", "S4-011", "S4-014", "S4-046", "S4-048", "S4-052")
def test_refund_permissions_validation_and_refund_targets():
    w = world_r({"dan": 0})
    ada, bob, cy = w["ada"], w["bob"], w["cy"]
    pub = new_payment(ada, "bob", 300)
    pid = pub["payment_id"]
    path = "/payments/%s/refunds" % pid
    before = observe_state(w, [(bob, pid)])
    expect(ada.post(path, {"amount": 10}, key=fresh_key()), 403, "forbidden", "the original sender cannot refund")
    expect(cy.post(path, {"amount": 10}, key=fresh_key()), 403, "forbidden", "a third party cannot refund")
    expect(w["op"].post(path, {"amount": 10}, key=fresh_key()), 403, "forbidden", "an operator cannot refund")
    expect(bob.post("/payments/p_nope/refunds", {"amount": 10}, key=fresh_key()), 404, "not_found", "unknown payment")
    expect(ada.post("/payments/p_nope/refunds", {"amount": 10}, key=fresh_key()), 404, "not_found", "unknown payment, other caller")
    expect(http("POST", path, body={"amount": 10}, key=fresh_key()), 401, "unauthenticated", "no token")
    expect(http("POST", path, body={"amount": 10}, key=fresh_key(), token="nope"), 401, "unauthenticated", "bad token")
    expect(http("POST", path, body={"amount": 10}, token=bob.token), 400, "missing_idempotency_key", "no key")
    expect(http("POST", path, body={"amount": 10}, token=bob.token, key=""), 400, "missing_idempotency_key", "empty key")
    expect(http("POST", path, body={"amount": 10}, token=bob.token, key="k" * 256), 422, "validation_failed", "256-character key")
    for bad in (0, -1, 1.5, "10", True, False, None, [], {}, 1_000_000_001):
        expect(bob.post(path, {"amount": bad}, key=fresh_key()), 422, "validation_failed", "amount %r" % (bad,))
    expect(bob.post(path, {}, key=fresh_key()), 422, "validation_failed", "missing amount")
    expect(bob.post(path, [], key=fresh_key()), 400, "malformed_request", "array body")
    expect(bob.post(path, raw='{"amount": ', key=fresh_key()), 400, "malformed_request", "unparseable body")
    eq(observe_state(w, [(bob, pid)]), before, "every rejection leaves no trace")
    expect(bob.post(path, raw='{"amount": 100.0, "ignored": true}', key=fresh_key()), 201, msg="100.0 is an integral amount; unknown fields are ignored")
    expect(bob.post(path, raw='{"amount": 1e2}', key=fresh_key()), 201, msg="1e2 is an integral amount")
    expect(http("POST", path, body={"amount": 100}, token=bob.token, key="k" * 255), 201, msg="a 255-character key is fine")
    # refund of a refund
    rf = expect(bob.post("/payments/%s/refunds" % new_payment(ada, "bob", 50)["payment_id"], {"amount": 20}, key=fresh_key()), 201).json
    before = observe_state(w, [(ada, rf["payment_id"])])
    kr = fresh_key()
    for _ in range(2):
        expect(ada.post("/payments/%s/refunds" % rf["payment_id"], {"amount": 5}, key=kr), 422, "invalid_refund_target", "the receiver of a refund refunds it")
    expect(ada.post("/payments/%s/refunds" % rf["payment_id"], {"amount": 20}, key=fresh_key()), 422, "invalid_refund_target", "even the full amount")
    eq(observe_state(w, [(ada, rf["payment_id"])]), before, "a refused refund target changes nothing")
    # insufficient funds: bob spends his money, a refund then fails, and its key is a first use once funds exist
    w2 = world_r()
    ada, bob, cy = w2["ada"], w2["bob"], w2["cy"]
    p = new_payment(ada, "bob", 100)
    expect(bob.pay("cy", 600), 201)                                   # bob: 500 + 100 - 600 = 0
    eq(lib2.me_ok(bob)["balance"], 0, "bob is empty")
    kf = fresh_key()
    before = observe_state(w2, [(bob, p["payment_id"])])
    expect(bob.post("/payments/%s/refunds" % p["payment_id"], {"amount": 50}, key=kf), 409, "insufficient_funds", "nothing to refund with")
    eq(observe_state(w2, [(bob, p["payment_id"])]), before, "a failed refund leaves no trace")
    expect(cy.pay("bob", 60), 201)
    expect(bob.post("/payments/%s/refunds" % p["payment_id"], {"amount": 50}, key=kf), 201, msg="the key of a 409 is a first use")
    eq(lib2.me_ok(bob)["balance"], 10, "bob 60 - 50")
    expect(bob.post("/payments/%s/refunds" % p["payment_id"], {"amount": 11}, key=fresh_key()), 409, "insufficient_funds", "11 > 10 available")
    expect(bob.post("/payments/%s/refunds" % p["payment_id"], {"amount": 10}, key=fresh_key()), 201, msg="exactly the available amount")


@test("S4-010", "S4-019", "S4-020", "S4-018", "S4-046", "S4-033", "S4-017")
def test_refund_cap_follows_the_corrected_amount_and_corrections_respect_refunds():
    w = world_r({"bob": 3000})
    ada, bob = w["ada"], w["bob"]
    p = new_payment(ada, "bob", 500)
    pid, eff = p["payment_id"], p["created_at"]
    expect(bob.post("/payments/%s/refunds" % pid, {"amount": 200}, key=fresh_key()), 201)
    state = lambda: observe_state(w, [(ada, pid)])
    before = state()
    k = fresh_key()
    for amount in (199, 150, 0):
        expect(correct(ada, pid, 1, amount, eff, "too low", key=k if amount == 199 else "auto"), 422, "refund_exceeds_payment",
               "a correction to %d is below the refunded 200" % amount)
    eq(state(), before, "rejected corrections leave no trace")
    expect(correct(ada, pid, 1, 200, eff, "exactly the refunded amount", key=k), 201, msg="the key of a rejection is a first use; 200 == refunded is allowed")
    expect(bob.post("/payments/%s/refunds" % pid, {"amount": 1}, key=fresh_key()), 422, "refund_exceeds_payment", "cap is now 200, 200 already refunded")
    expect(correct(ada, pid, 2, 800, eff, "raise"), 201, msg="increase")
    expect(bob.post("/payments/%s/refunds" % pid, {"amount": 601}, key=fresh_key()), 422, "refund_exceeds_payment", "cap 800, refunded 200")
    expect(bob.post("/payments/%s/refunds" % pid, {"amount": 600}, key=fresh_key()), 201, msg="up to the new corrected amount")
    expect(bob.post("/payments/%s/refunds" % pid, {"amount": 1}, key=fresh_key()), 422, "refund_exceeds_payment", "fully refunded at 800")
    expect(correct(ada, pid, 3, 799, eff, "below 800 refunded"), 422, "refund_exceeds_payment", "below the refunded total")
    # a payment corrected downwards before any refund
    q = new_payment(ada, "bob", 500)
    expect(correct(ada, q["payment_id"], 1, 300, q["created_at"], "down"), 201)
    expect(bob.post("/payments/%s/refunds" % q["payment_id"], {"amount": 301}, key=fresh_key()), 422, "refund_exceeds_payment", "cap is the corrected 300")
    expect(bob.post("/payments/%s/refunds" % q["payment_id"], {"amount": 300}, key=fresh_key()), 201, msg="exactly the corrected amount")
    # a reversed payment cannot be refunded
    z = new_payment(ada, "bob", 40)
    expect(correct(ada, z["payment_id"], 1, 0, z["created_at"], "reversed"), 201)
    expect(bob.post("/payments/%s/refunds" % z["payment_id"], {"amount": 1}, key=fresh_key()), 422, "refund_exceeds_payment", "corrected amount 0")
    eq(sum(lib2.me_ok(u)["balance"] for u in w.values()), 13000, "conservation")
    for h, u in w.items():
        eq(full_stmt(u)["closing"], lib2.me_ok(u)["balance"], "statement closing == balance for %s" % h)


@test("S4-008", "S4-015", "S4-043", "S4-016", "S4-012", "S4-004", "S4-039")
def test_refund_targets_requests_captures_settlements_and_nothing_is_reopened():
    w = world_r({"bob": 2000, "cy": 100})
    ada, bob, cy, op = w["ada"], w["bob"], w["cy"], w["op"]
    # request payment
    rq = expect(bob.request("ada", 300), 201).json["request_id"]
    q = expect(ada.pay_request(rq), 201).json
    check_payment4(q, refund_of=None)
    rr = expect(bob.post("/payments/%s/refunds" % q["payment_id"], {"amount": 100}, key=fresh_key()), 201).json
    eq((rr["refund_of"], rr["request_id"]), (q["payment_id"], None), "request payment refunded; the refund has no request")
    got = [x for x in bob.all_requests() if x["request_id"] == rq][0]
    eq((got["status"], got["payment_id"]), ("paid", q["payment_id"]), "the request is not reopened")
    eq([x for x in ada.all_requests() if x["request_id"] == rq][0], got, "same for the payer")
    # capture: the remainder was released by the final capture; a refund does not restore the hold
    a = expect(lib2.authorize(ada, "bob", 1000, note="dep"), 201).json
    cap = expect(lib2.capture(bob, a["authorization_id"], {"amount": 600}), 201).json
    m0 = lib2.me_ok(ada)
    eq(m0["held"], 0, "final capture released the remainder")
    rc = expect(bob.post("/payments/%s/refunds" % cap["payment_id"], {"amount": 250}, key=fresh_key()), 201).json
    eq((rc["refund_of"], rc["authorization_id"], rc["note"]), (cap["payment_id"], None, "dep"), "capture refunded; the refund has no authorization")
    row = lib2.auth_by_id(ada, a["authorization_id"])
    eq((row["status"], row["captured_amount"], row["payment_ids"], row["remaining_amount"]), ("captured", 600, [cap["payment_id"]], 0), "the authorization is not reopened")
    m1 = lib2.me_ok(ada)
    eq((m1["held"], m1["total"]), (0, m0["total"] + 250), "no hold restored; only the money came back")
    # an open authorization with a partial capture: a refund of the capture does not touch the hold
    a2 = expect(lib2.authorize(ada, "bob", 800), 201).json
    cap2 = expect(lib2.capture(bob, a2["authorization_id"], {"amount": 300, "final": False}), 201).json
    h0 = lib2.me_ok(ada)["held"]
    expect(bob.post("/payments/%s/refunds" % cap2["payment_id"], {"amount": 100}, key=fresh_key()), 201)
    row2 = lib2.auth_by_id(ada, a2["authorization_id"])
    eq((row2["status"], row2["remaining_amount"], row2["captured_amount"]), ("open", 500, 300), "open authorization unchanged")
    eq(lib2.me_ok(ada)["held"], h0, "held unchanged by the refund")
    eq(h0, 500, "(control) the remainder is still held")
    # settlement member
    body = {"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 100, "visibility": "private", "note": "x"},
                          {"from_handle": "bob", "to_handle": "cy", "amount": 40}]}
    key = fresh_key()
    st = expect(op.post("/settlements", body, key=key), 201).json
    for m in st["payments"]:
        check_payment4(m, refund_of=None)
    m1p, m2p = st["payments"]
    rs = expect(bob.post("/payments/%s/refunds" % m1p["payment_id"], {"amount": 30}, key=fresh_key()), 201).json
    eq((rs["refund_of"], rs["settlement_id"], rs["visibility"], rs["note"]), (m1p["payment_id"], None, "private", "x"), "a settlement payment can be refunded; the refund is not a member")
    rs2 = expect(cy.post("/payments/%s/refunds" % m2p["payment_id"], {"amount": 10}, key=fresh_key()), 201).json
    eq(rs2["settlement_id"], None, "refunds never change settlement membership")
    rp = expect(op.post("/settlements", body, key=key), 200)
    eq(rp.json, st, "the settlement receipt replays unchanged (same members, amounts, created_at)")
    ok(rs["payment_id"] not in [x["payment_id"] for x in rp.json["payments"]], "the refund is not in the receipt")
    # refund_of is null on every other kind of payment
    for u in (ada, bob, cy):
        for pmt in feed4(u):
            if pmt["payment_id"] in (rr["payment_id"], rc["payment_id"], rs["payment_id"], rs2["payment_id"]) or pmt["refund_of"]:
                continue
            eq(pmt["refund_of"], None, "refund_of is null for non-refunds")
    eq(sum(lib2.me_ok(u)["total"] for u in w.values()), 12100, "conservation")


@test("S4-014", "S4-020", "S4-015", "S4-046")
def test_refund_and_correction_debits_use_available_funds_not_held_money():
    w = world_r()
    ada, bob, cy = w["ada"], w["bob"], w["cy"]
    p = new_payment(ada, "bob", 500)                 # bob: 1000 total
    pid = p["payment_id"]
    a = expect(lib2.authorize(bob, "cy", 900), 201).json
    m = lib2.me_ok(bob)
    eq((m["total"], m["held"], m["available"]), (1000, 900, 100), "bob has 900 held")
    before = observe_state(w, [(bob, pid)])
    k = fresh_key()
    expect(bob.post("/payments/%s/refunds" % pid, {"amount": 101}, key=k), 409, "insufficient_funds", "101 > available 100 although total is 1000")
    eq(observe_state(w, [(bob, pid)]), before, "nothing changed")
    expect(bob.post("/payments/%s/refunds" % pid, {"amount": 100}, key=k), 201, msg="exactly the available amount (the key of the 409 is reusable)")
    m = lib2.me_ok(bob)
    eq((m["total"], m["held"], m["available"]), (900, 900, 0), "available is zero, the hold intact")
    # a correction that decreases the payment debits bob: held money cannot fund it
    expect(correct(ada, pid, 1, 450, p["created_at"], "down by 50"), 409, "insufficient_funds", "bob has no available funds")
    expect(lib2.void(bob, a["authorization_id"]), 200)
    m = lib2.me_ok(bob)
    eq((m["total"], m["held"], m["available"]), (900, 0, 900), "the hold is released")
    # currently affordable now, but while the 900 was held bob's available amount at the refund was 0: a retroactive
    # decrease would have left it at -50 at that boundary
    expect(correct(ada, pid, 1, 450, p["created_at"], "down by 50"), 409, "historical_overdraft", "available would have been negative while the hold was open")
    eq(lib2.me_ok(bob)["total"], 900, "unchanged")


@test("S4-012", "S4-051", "S4-040", "S4-019", "S4-050", "S4-016")
def test_refunds_in_historical_views_and_the_overdraft_checks():
    t = whole(utcnow()) - timedelta(days=5)
    opening = {"ada": 1000, "bob": 0, "cy": 0}
    pays = [fx_pay("p_001", "ada", "bob", 500, created_at=t + timedelta(hours=1))]
    w = hist_world(opening, pays)
    ada, bob = w["ada"], w["bob"]
    rf = expect(bob.post("/payments/p_001/refunds", {"amount": 400}, key=fresh_key()), 201).json
    tr = inst(rf["created_at"])
    sec = timedelta(seconds=1)
    m = Model({uid(h): v for h, v in opening.items()})
    m.add("p_001", uid("ada"), uid("bob"), [(1, 500, t + timedelta(hours=1), t + timedelta(hours=1))])
    m.add(rf["payment_id"], uid("bob"), uid("ada"), [(r["revision"], r["amount"], inst(r["effective_at"]), inst(r["recorded_at"])) for r in revisions(bob, rf["payment_id"])])
    for as_of in (t, t + timedelta(hours=1), tr - sec, tr, tr + sec, None):
        for known in (None, t + timedelta(hours=1) - sec, t + timedelta(hours=1), tr - sec, tr):
            tot = 0
            for h, u in w.items():
                got = me_at(u, as_of=None if as_of is None else fmt(as_of), known_at=None if known is None else fmt(known))["balance"]
                eq(got, m.balance(uid(h), as_of, known), "balance of %s as of %s known_at %s" % (h, as_of, known))
                tot += got
            eq(tot, 1000, "sum of balances in every historical view")
    for h, u in w.items():
        got = full_stmt(u)
        want = m.statement(uid(h))
        eq((got["opening"], got["closing"]), (want["opening"], want["closing"]), "statement balances of %s" % h)
        compare_entries(got["entries"], want["entries"], "statement of %s" % h)
        eq(got["opening"], opening[h], "opening balance unchanged by the refund")
    sleep_gap(1.2)
    # moving the refunded payment to a point after the refund would leave bob at -400 at the refund's instant
    after = fmt(tr + timedelta(seconds=0.5))
    before = observe_state(w, [(ada, "p_001")])
    expect(correct(ada, "p_001", 1, 500, after, "later than the refund"), 409, "historical_overdraft", "bob would have refunded money he did not have yet")
    eq(observe_state(w, [(ada, "p_001")]), before, "no trace")
    expect(correct(ada, "p_001", 1, 500, rf["created_at"], "same instant as the refund"), 201,
           msg="at exactly the refund's instant the combined effect (+500 -400) is nonnegative")
    expect(correct(ada, "p_001", 2, 399, rf["created_at"], "below refunded"), 422, "refund_exceeds_payment", "below the refunded 400")
    expect(correct(ada, "p_001", 2, 400, rf["created_at"], "down to refunded"), 201, msg="exactly the refunded amount; bob has 100")
    eq(lib2.me_ok(bob)["balance"], 0, "bob")
    eq(lib2.me_ok(ada)["balance"], 1000, "ada: everything came back")
    eq(sum(me_at(u, as_of="1970-01-01T00:00:00+00:00")["balance"] for u in w.values()), 1000, "opening sum")


@test("S4-005", "S4-006", "S4-013", "S4-046", "S4-047", "S4-048", "S4-014")
def test_refund_idempotency_matrix():
    w = world_r({"bob": 5000})
    ada, bob = w["ada"], w["bob"]
    p1, p2 = new_payment(ada, "bob", 300), new_payment(ada, "bob", 300)
    k = fresh_key()
    b = {"amount": 100}
    r1 = expect(bob.post("/payments/%s/refunds" % p1["payment_id"], b, key=k), 201)
    r2 = expect(bob.post("/payments/%s/refunds" % p2["payment_id"], b, key=k), 201)
    ok(r1.json["payment_id"] != r2.json["payment_id"] and (r1.json["refund_of"], r2.json["refund_of"]) == (p1["payment_id"], p2["payment_id"]),
       "the same key and body on another payment's refund path is a different request")
    expect(bob.post("/payments/%s/refunds" % p1["payment_id"], b, key=k), 200, msg="replay")
    eq(expect(bob.post("/payments/%s/refunds" % p1["payment_id"], b, key=k), 200).json, r1.json, "replay body")
    expect(bob.post("/payments/%s/refunds" % p1["payment_id"], {"amount": 101}, key=k), 409, "idempotency_key_reuse", "changed body")
    expect(bob.post("/payments/%s/refunds" % p1["payment_id"], {"amount": -3}, key=k), 409, "idempotency_key_reuse", "invalid body with a claimed key is still a key conflict")
    # replay after the refund capacity is gone and after later changes
    expect(bob.post("/payments/%s/refunds" % p1["payment_id"], {"amount": 200}, key=fresh_key()), 201)
    expect(bob.post("/payments/%s/refunds" % p1["payment_id"], b, key=k), 200, msg="the replay does not hit refund_exceeds_payment")
    eq(lib2.me_ok(bob)["balance"], 5000 + 600 - 400, "money moved once per refund")
    # failed keys are first uses
    kf = fresh_key()
    expect(bob.post("/payments/%s/refunds" % p2["payment_id"], {"amount": 0}, key=kf), 422, "validation_failed")
    expect(bob.post("/payments/%s/refunds" % p2["payment_id"], {"amount": 201}, key=kf), 422, "refund_exceeds_payment")
    expect(bob.post("/payments/%s/refunds" % p2["payment_id"], {"amount": 150}, key=kf), 201, msg="after two 422s the key is still unclaimed")
    kf2 = fresh_key()
    expect(ada.post("/payments/%s/refunds" % p2["payment_id"], {"amount": 1}, key=kf2), 403, "forbidden")
    expect(bob.post("/payments/%s/refunds" % p2["payment_id"], {"amount": 1}, key=kf2), 201, msg="the key of a 403 is a first use")
    # key length
    expect(http("POST", "/payments/%s/refunds" % p2["payment_id"], body={"amount": 1}, token=bob.token, key="k" * 255), 201, msg="255 characters")
    expect(http("POST", "/payments/%s/refunds" % p2["payment_id"], body={"amount": 1}, token=bob.token, key="k" * 256), 422, "validation_failed", "256 characters")
    # many identical concurrent requests: one refund
    p3 = new_payment(ada, "bob", 1000)
    kc = fresh_key()
    res = burst([(lambda: bob.post("/payments/%s/refunds" % p3["payment_id"], {"amount": 400}, key=kc)) for _ in range(15)])
    eq(sorted(r.status for r in res), [200] * 14 + [201], "one 201, the others replay")
    eq(len({json_dump(r.json) for r in res}), 1, "identical bodies")
    refs = [x for x in feed4(bob) if x["refund_of"] == p3["payment_id"]]
    eq(len(refs), 1, "a single refund payment exists")


def json_dump(o):
    import json
    return json.dumps(o, sort_keys=True)
