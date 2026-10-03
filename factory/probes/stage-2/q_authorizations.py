"""Stage 2 API: authorizations, captures, voids, listing, /me fields."""
from lib2 import *  # noqa: F401,F403


@test("S2-086", "S2-087", "S2-093", "S2-094", "S2-111", "S2-112", "S2-113", "S2-114", "S2-115", "S2-116", "S2-122", "S2-135")
def test_authorize_shape_and_hold():
    w = world2({"ada": 10000, "bob": 500, "cy": 0}, ttl=600)
    ada, bob, cy = w["ada"], w["bob"], w["cy"]
    m0 = me_ok(ada)
    eq((m0["balance"], m0["total"], m0["available"], m0["held"]), (10000, 10000, 10000, 0), "no holds: all agree")
    r = expect(authorize(ada, "bob", 2000, note="deposit", visibility="private"), 201)
    a = check_auth(r.json, frm=ada, to=bob, amount=2000, status="open", captured=0, remaining=2000, note="deposit",
                   visibility="private", payment_ids=[])
    eq(r.json["currency"], "EUR", "currency")
    eq(r.json["payment_id"], None, "payment_id null")
    eq(seconds_between(r.json["created_at"], r.json["expires_at"]), 600, "expires_at = created_at + authorization_ttl_seconds")
    m = me_ok(ada)
    eq((m["balance"], m["total"], m["available"], m["held"]), (10000, 10000, 8000, 2000), "hold: total unchanged, available down")
    mb = me_ok(bob)
    eq((mb["balance"], mb["available"], mb["held"]), (500, 500, 0), "receiver untouched")
    for u in (ada, bob, cy):
        eq(u.all_payments(), [], "an open authorization is never a feed item (%s)" % u.handle)
    # defaults
    r2 = expect(ada.post("/authorizations", {"to_handle": "bob", "amount": 5}, key=fresh_key()), 201)
    check_auth(r2.json, note="", visibility="public", status="open")
    ok(r2.json["authorization_id"] != a["authorization_id"], "unique ids")
    r3 = expect(authorize(ada, "bob", 1, bogus=1, another=[1]), 201, msg="unknown fields ignored")
    m = me_ok(ada)
    eq((m["available"], m["held"]), (10000 - 2000 - 5 - 1, 2006), "holds add up")
    # GET /authorizations shows the same objects
    listed = {x["authorization_id"]: x for x in all_auths(ada)}
    eq(listed[a["authorization_id"]], a, "list item equals the POST response")
    eq(len(listed), 3, "three authorizations")
    eq(len(all_auths(bob)), 3, "receiver sees them too")
    eq(all_auths(cy), [], "strangers see none")


@test("S2-117", "S2-118", "S2-119", "S2-120", "S2-121", "S2-087", "S2-091", "S2-099", "S2-113")
def test_authorize_validation():
    w = world2({"ada": 10000, "bob": 0})
    ada, bob = w["ada"], w["bob"]
    before = me_ok(ada)

    def bad(body, status=422, code="validation_failed", why=""):
        expect(ada.post("/authorizations", body, key=fresh_key()), status, code, why or repr(body))
        eq(me_ok(ada), before, "a refused authorization must change nothing: %r" % (body,))
        eq(all_auths(ada), [], "no authorization is created by a refusal")

    for amt in (0, -1, 1_000_000_001, 1.5, "10", True, None, [], {}):
        bad({"to_handle": "bob", "amount": amt}, why="amount %r" % (amt,))
    bad({"to_handle": "bob"}, why="missing amount")
    bad({"amount": 5}, why="missing to_handle")
    for note in (None, 5, [], {}, True, "x" * 201):
        bad({"to_handle": "bob", "amount": 1, "note": note}, why="note %r" % (str(note)[:10],))
    for vis in ("PUBLIC", "friends", "", None, 5, []):
        bad({"to_handle": "bob", "amount": 1, "visibility": vis}, why="visibility %r" % (vis,))
    for h in (5, ["bob"], True):
        bad({"to_handle": h, "amount": 1}, 400, "malformed_request", "to_handle %r" % (h,))
    bad({"to_handle": "nobody", "amount": 1}, 404, "not_found")
    bad({"to_handle": "ada", "amount": 1}, 422, "self_payment")
    bad({"to_handle": "ada", "amount": 50000}, 422, "self_payment", "self authorization above available")
    bad({"to_handle": "nobody", "amount": 50000}, 404, "not_found", "unknown handle above available")
    bad({"to_handle": "bob", "amount": 10001}, 409, "insufficient_funds", "one above available")
    for raw in ("{", "", "[]", "7"):
        expect(http("POST", "/authorizations", raw=raw, token=ada.token, key=fresh_key()), 400, "malformed_request", repr(raw))
    expect(http("POST", "/authorizations", body={"to_handle": "bob", "amount": 1}, token=ada.token), 400, "missing_idempotency_key")
    expect(http("POST", "/authorizations", body={"to_handle": "bob", "amount": 1}, token=ada.token, key=""), 400, "missing_idempotency_key")
    expect(http("POST", "/authorizations", body={"to_handle": "bob", "amount": 1}, key=fresh_key()), 401, "unauthenticated")
    eq(me_ok(ada), before, "nothing changed")
    # boundaries: note, numeric forms, exactly the available amount, held funds are not spendable
    expect(authorize(ada, "bob", 1, note="x" * 200), 201, msg="200-char note")
    expect(authorize(ada, "bob", 1, note="\U0001F600" * 200), 201, msg="200 emoji")
    for lit in ("1000.0", "1e3"):
        r = expect(http("POST", "/authorizations", raw='{"to_handle":"bob","amount":%s}' % lit, token=ada.token,
                        key=fresh_key()), 201, msg="amount literal " + lit)
        eq(r.json["amount"], 1000, "amount echoed as an integer")
    m = me_ok(ada)
    eq(m["available"], 10000 - 1 - 1 - 2000, "available after four holds")
    expect(authorize(ada, "bob", m["available"] + 1), 409, "insufficient_funds", "one above the remaining available")
    r = expect(authorize(ada, "bob", m["available"]), 201, msg="exactly the available amount")
    m = me_ok(ada)
    eq((m["available"], m["held"], m["total"]), (0, 10000, 10000), "everything held")
    expect(authorize(ada, "bob", 1), 409, "insufficient_funds", "held funds cannot back another hold, though total is 10000")
    expect(ada.pay("bob", 1), 409, "insufficient_funds", "held funds cannot fund a payment")
    eq(me_ok(bob)["total"], 0, "no money moved")
    big = world2({"ada": 5_000_000_000, "bob": 0})
    expect(authorize(big["ada"], "bob", 1_000_000_000), 201, msg="max amount")
    expect(authorize(big["ada"], "bob", 1_000_000_001), 422, "validation_failed", "max + 1")


@test("S2-088", "S2-124", "S2-126", "S2-127", "S2-128", "S2-129", "S2-134", "S2-135", "S2-123", "S2-122", "S2-087")
def test_capture_default_is_one_final_capture():
    w = world2({"ada": 10000, "bob": 500, "cy": 0})
    ada, bob, cy = w["ada"], w["bob"], w["cy"]
    # full capture, public, note copied
    a = expect(authorize(ada, "bob", 2000, note="deposit", visibility="public"), 201).json
    r = expect(capture(bob, a["authorization_id"], {}), 201, msg="capture with an empty body")
    p = check_payment2(r.json, frm=ada, to=bob, amount=2000, note="deposit", visibility="public", request_id=None,
                       settlement=None, currency="EUR")
    eq(p["authorization_id"], a["authorization_id"], "authorization_id on the capture payment")
    ma, mb = me_ok(ada), me_ok(bob)
    eq((ma["total"], ma["available"], ma["held"]), (8000, 8000, 0), "payer after full capture")
    eq((mb["total"], mb["available"]), (2500, 2500), "receiver credited")
    got = auth_by_id(bob, a["authorization_id"])
    check_auth(got, status="captured", captured=2000, remaining=0, payment_ids=[p["payment_id"]])
    eq(got["payment_id"], p["payment_id"], "payment_id")
    ok(p["payment_id"] in {x["payment_id"] for x in cy.all_payments()}, "public capture payment is in a third party's feed")
    expect(capture(bob, a["authorization_id"], {}), 409, "authorization_not_open", "second capture after a final capture")
    expect(capture(bob, a["authorization_id"], {"amount": 1}), 409, "authorization_not_open", "any further capture")
    eq(me_ok(ada)["total"], 8000, "no extra movement")
    # partial default capture releases the remainder in the same step
    b = expect(authorize(ada, "bob", 2000, note="hold", visibility="private"), 201).json
    m = me_ok(ada)
    eq((m["available"], m["held"]), (6000, 2000), "before capture")
    r = expect(capture(bob, b["authorization_id"], {"amount": 1500}), 201)
    pb = check_payment2(r.json, frm=ada, to=bob, amount=1500, note="hold", visibility="private", request_id=None)
    m = me_ok(ada)
    eq((m["total"], m["available"], m["held"]), (6500, 6500, 0), "capturing 1500 of 2000 returns 500 to available in the same step")
    got = auth_by_id(ada, b["authorization_id"])
    check_auth(got, status="captured", captured=1500, remaining=0, payment_ids=[pb["payment_id"]])
    ok(pb["payment_id"] not in {x["payment_id"] for x in cy.all_payments()}, "private capture payment hidden from a stranger")
    ok(pb["payment_id"] in {x["payment_id"] for x in ada.all_payments()}, "visible to the payer")
    ok(pb["payment_id"] in {x["payment_id"] for x in bob.all_payments()}, "visible to the receiver")
    expect(capture(bob, b["authorization_id"], {"amount": 500}), 409, "authorization_not_open", "remainder was released")
    # explicit amount equal to the authorisation / explicit final true
    c = expect(authorize(ada, "bob", 300), 201).json
    expect(capture(bob, c["authorization_id"], {"amount": 300, "final": True}), 201)
    d = expect(authorize(ada, "bob", 300), 201).json
    expect(capture(bob, d["authorization_id"], {"amount": 100, "final": True}), 201)
    eq(me_ok(ada)["held"], 0, "explicit final releases the rest")
    eq(me_ok(ada)["total"], 6500 - 300 - 100, "totals")
    eq(len(cy.all_payments()), 3, "cy sees the three public captures (2000, 300, 100)")


@test("S2-130", "S2-131", "S2-132", "S2-133", "S2-134", "S2-135", "S2-124", "S2-088", "S2-092")
def test_capture_extended_mode():
    w = world2({"ada": 10000, "bob": 0})
    ada, bob = w["ada"], w["bob"]
    a = expect(authorize(ada, "bob", 2000), 201).json
    aid = a["authorization_id"]
    r1 = expect(capture(bob, aid, {"amount": 700, "final": False}), 201)
    check_payment2(r1.json, frm=ada, to=bob, amount=700, request_id=None)
    eq(r1.json["authorization_id"], aid, "authorization_id")
    got = auth_by_id(bob, aid)
    check_auth(got, status="open", captured=700, remaining=1300, payment_ids=[r1.json["payment_id"]])
    m = me_ok(ada)
    eq((m["total"], m["held"], m["available"]), (9300, 1300, 8000), "remainder stays held")
    r2 = expect(capture(bob, aid, {"amount": 500, "final": False}), 201)
    got = auth_by_id(bob, aid)
    check_auth(got, status="open", captured=1200, remaining=800, payment_ids=[r1.json["payment_id"], r2.json["payment_id"]])
    eq(got["payment_id"], r2.json["payment_id"], "payment_id is the latest capture")
    # exceeding the REMAINING amount (not the authorised amount)
    expect(capture(bob, aid, {"amount": 801}), 422, "capture_exceeds_authorization", "above the remainder 800")
    expect(capture(bob, aid, {"amount": 801, "final": False}), 422, "capture_exceeds_authorization")
    expect(capture(bob, aid, {"amount": 2000}), 422, "capture_exceeds_authorization", "the original amount is no longer available")
    eq(auth_by_id(bob, aid)["captured_amount"], 1200, "refusals change nothing")
    eq(me_ok(ada)["held"], 800, "hold unchanged by refusals")
    # omitted amount defaults to the remainder, default final
    r3 = expect(capture(bob, aid, {}), 201)
    eq(r3.json["amount"], 800, "default amount is the remaining amount")
    got = auth_by_id(bob, aid)
    check_auth(got, status="captured", captured=2000, remaining=0,
               payment_ids=[r1.json["payment_id"], r2.json["payment_id"], r3.json["payment_id"]])
    expect(capture(bob, aid, {}), 409, "authorization_not_open")
    m = me_ok(ada)
    eq((m["total"], m["held"], m["available"]), (8000, 0, 8000), "all captured")
    eq(me_ok(bob)["total"], 2000, "receiver got all three")
    # capturing the entire remainder closes it even with final:false
    b = expect(authorize(ada, "bob", 1000), 201).json["authorization_id"]
    expect(capture(bob, b, {"amount": 400, "final": False}), 201)
    expect(capture(bob, b, {"amount": 600, "final": False}), 201)
    check_auth(auth_by_id(bob, b), status="captured", captured=1000, remaining=0)
    expect(capture(bob, b, {"amount": 1, "final": False}), 409, "authorization_not_open")
    # an explicit final capture releases whatever is left
    c = expect(authorize(ada, "bob", 1000), 201).json["authorization_id"]
    expect(capture(bob, c, {"amount": 300, "final": False}), 201)
    m = me_ok(ada)
    eq(m["held"], 700, "700 still held")
    expect(capture(bob, c, {"amount": 200, "final": True}), 201)
    m2 = me_ok(ada)
    eq(m2["held"], 0, "final capture released the rest")
    eq(m2["available"], m["available"] - 200 + 700, "available rose by the released 500 net of the 200 moved")
    check_auth(auth_by_id(ada, c), status="captured", captured=500, remaining=0)
    eq(m2["total"], 8000 - 1000 - 500, "totals")
    # a non-final capture after a non-final one that leaves exactly the rest keeps working
    d = expect(authorize(ada, "bob", 100), 201).json["authorization_id"]
    for _ in range(4):
        expect(capture(bob, d, {"amount": 10, "final": False}), 201)
    check_auth(auth_by_id(ada, d), status="open", captured=40, remaining=60)
    eq(len(auth_by_id(ada, d)["payment_ids"]), 4, "four captures recorded in order")
    ids = auth_by_id(ada, d)["payment_ids"]
    feed = [p["payment_id"] for p in ada.all_payments() if p["authorization_id"] == d]
    eq(sorted(ids), sorted(feed), "payment_ids are exactly the capture payments")


@test("S2-091", "S2-096", "S2-088", "S2-092")
def test_captures_may_spend_reserved_money():
    w = world2({"ada": 1000, "bob": 0, "cy": 0, "op": 0}, operators=["op"])
    ada, bob, cy, op = w["ada"], w["bob"], w["cy"], w["op"]
    a = expect(authorize(ada, "bob", 1000), 201).json["authorization_id"]
    eq(me_ok(ada)["available"], 0, "fully held")
    expect(ada.pay("bob", 1), 409, "insufficient_funds")
    expect(authorize(ada, "cy", 1), 409, "insufficient_funds")
    rid = expect(cy.request("ada", 1), 201).json["request_id"]
    expect(ada.pay_request(rid), 409, "insufficient_funds")
    expect(op.settle([{"from_handle": "ada", "to_handle": "cy", "amount": 1}]), 409, "insufficient_funds")
    eq(me_ok(ada)["total"], 1000, "nothing moved by the refusals")
    expect(capture(bob, a, {}), 201, msg="the capture spends the reserved money")
    m = me_ok(ada)
    eq((m["total"], m["available"], m["held"]), (0, 0, 0), "drained exactly, never negative")
    eq(me_ok(bob)["total"], 1000, "receiver")
    # money that is not held can still be spent while a hold exists
    w = world2({"ada": 1500, "bob": 0})
    ada, bob = w["ada"], w["bob"]
    a = expect(authorize(ada, "bob", 1000), 201).json["authorization_id"]
    expect(ada.pay("bob", 500), 201, msg="available 500 can be spent")
    expect(ada.pay("bob", 1), 409, "insufficient_funds")
    expect(capture(bob, a, {"amount": 1000}), 201, msg="the held 1000 is still there for the capture")
    eq(me_ok(ada)["total"], 0, "ada ends at zero")
    eq(me_ok(bob)["total"], 1500, "bob")


@test("S2-138", "S2-139", "S2-140", "S2-141", "S2-142", "S2-143", "S2-148", "S2-137", "S2-123", "S2-076")
def test_capture_errors_and_permissions():
    w = world2({"ada": 10000, "bob": 0, "cy": 0})
    ada, bob, cy = w["ada"], w["bob"], w["cy"]
    aid = expect(authorize(ada, "bob", 2000), 201).json["authorization_id"]
    snap = (me_ok(ada), me_ok(bob), auth_by_id(ada, aid))

    def same():
        eq((me_ok(ada), me_ok(bob), auth_by_id(ada, aid)), snap, "a refused capture must change nothing")

    expect(capture(ada, aid, {}), 403, "forbidden", "the payer may not capture")
    expect(capture(cy, aid, {}), 403, "forbidden", "a stranger may not capture")
    expect(capture(bob, "a_missing", {}), 404, "not_found")
    expect(capture(bob, "x" * 70, {}), 404, "not_found")
    for amt in (0, -1, 1.5, "100", True, False, None, [], {}):
        expect(capture(bob, aid, {"amount": amt}), 422, "validation_failed", "capture amount %r" % (amt,))
    for fin in ("false", "true", 1, 0, "yes", [], {"a": 1}):
        expect(capture(bob, aid, {"amount": 100, "final": fin}), 400, "malformed_request", "final %r" % (fin,))
    expect(capture(bob, aid, {"amount": 2001}), 422, "capture_exceeds_authorization")
    expect(capture(bob, aid, {"amount": 10**9}), 422, "capture_exceeds_authorization", "huge but valid-looking amount")
    for raw in ("{", "", "[]", "7"):
        expect(http("POST", "/authorizations/%s/capture" % aid, raw=raw, token=bob.token, key=fresh_key()), 400, "malformed_request", repr(raw))
    expect(http("POST", "/authorizations/%s/capture" % aid, body={}, token=bob.token), 400, "missing_idempotency_key")
    expect(http("POST", "/authorizations/%s/capture" % aid, body={}, token=bob.token, key=""), 400, "missing_idempotency_key")
    expect(http("POST", "/authorizations/%s/capture" % aid, body={}, key=fresh_key()), 401, "unauthenticated")
    same()
    # unknown fields are ignored
    r = expect(capture(bob, aid, {"amount": 100, "final": False, "bogus": 1}), 201, msg="unknown fields ignored")
    # not open: captured / voided
    expect(capture(bob, aid, {"amount": 1900}), 201)
    expect(capture(bob, aid, {"amount": 1}), 409, "authorization_not_open")
    expect(capture(cy, aid, {}), 403, "forbidden", "stranger on a closed authorization")
    expect(capture(ada, aid, {}), 403, "forbidden", "payer on a closed authorization")
    v = expect(authorize(ada, "bob", 500), 201).json["authorization_id"]
    expect(void(ada, v), 200)
    expect(capture(bob, v, {}), 409, "authorization_not_open", "voided")
    # replay rule: {} and {"amount": N} and {"amount": N, "final": ...} are different bodies
    w2 = world2({"ada": 10000, "bob": 0})
    ada, bob = w2["ada"], w2["bob"]
    a1 = expect(authorize(ada, "bob", 2000), 201).json["authorization_id"]
    k = fresh_key()
    r = expect(capture(bob, a1, {}, key=k), 201)
    expect(capture(bob, a1, {"amount": 2000}, key=k), 409, "idempotency_key_reuse", "{} vs {amount: 2000}")
    expect(capture(bob, a1, {"final": True}, key=k), 409, "idempotency_key_reuse", "{} vs {final: true}")
    eq(expect(capture(bob, a1, {}, key=k), 200).json, r.json, "identical body replays")
    a2 = expect(authorize(ada, "bob", 2000), 201).json["authorization_id"]
    k2 = fresh_key()
    r = expect(capture(bob, a2, {"amount": 700, "final": False}, key=k2), 201)
    expect(capture(bob, a2, {"amount": 700}, key=k2), 409, "idempotency_key_reuse", "final:false vs default")
    expect(capture(bob, a2, {"amount": 700, "final": True}, key=k2), 409, "idempotency_key_reuse", "final:false vs final:true")
    expect(capture(bob, a2, {"amount": 701, "final": False}, key=k2), 409, "idempotency_key_reuse")
    eq(expect(capture(bob, a2, {"final": False, "amount": 700}, key=k2), 200).json, r.json, "key order does not matter")
    eq(auth_by_id(bob, a2)["captured_amount"], 700, "replays captured once")


@test("S2-144", "S2-145", "S2-146", "S2-147", "S2-148", "S2-143", "S2-136", "S2-135", "S2-088")
def test_void():
    w = world2({"ada": 10000, "bob": 0, "cy": 0})
    ada, bob, cy = w["ada"], w["bob"], w["cy"]
    a = expect(authorize(ada, "bob", 2000), 201).json["authorization_id"]
    eq(me_ok(ada)["held"], 2000, "held")
    r = expect(void(ada, a), 200, msg="void without key or body")
    check_auth(r.json, frm=ada, to=bob, amount=2000, status="voided", captured=0, remaining=0, payment_ids=[])
    m = me_ok(ada)
    eq((m["total"], m["available"], m["held"]), (10000, 10000, 0), "hold released, nothing moved")
    r2 = expect(void(ada, a, {}), 200, msg="voiding twice")
    eq(r2.json, r.json, "current state returned")
    expect(void(ada, a, None), 200)
    eq(auth_by_id(ada, a)["status"], "voided", "listed as voided")
    expect(void(bob, a), 403, "forbidden", "the receiver may not void")
    expect(void(cy, a), 403, "forbidden", "a stranger may not void")
    expect(void(ada, "a_missing"), 404, "not_found")
    expect(http("POST", "/authorizations/%s/void" % a, token=ada.token, key=fresh_key()), 200, msg="a key is tolerated")
    expect(http("POST", "/authorizations/%s/void" % a), 401, "unauthenticated")
    # captured: not open
    b = expect(authorize(ada, "bob", 1000), 201).json["authorization_id"]
    expect(capture(bob, b, {}), 201)
    expect(void(ada, b), 409, "authorization_not_open")
    expect(void(bob, b), 403, "forbidden")
    expect(void(cy, b), 403, "forbidden")
    # partially captured: void releases only the remainder and keeps the records
    c = expect(authorize(ada, "bob", 2000), 201).json["authorization_id"]
    p1 = expect(capture(bob, c, {"amount": 700, "final": False}), 201).json
    p2 = expect(capture(bob, c, {"amount": 300, "final": False}), 201).json
    before = me_ok(ada)
    eq((before["held"], before["total"]), (1000, 10000 - 1000 - 1000), "state before void")
    r = expect(void(ada, c), 200)
    check_auth(r.json, status="voided", captured=1000, remaining=0, payment_ids=[p1["payment_id"], p2["payment_id"]])
    eq(r.json["payment_id"], p2["payment_id"], "latest capture preserved")
    after = me_ok(ada)
    eq((after["held"], after["total"], after["available"]), (0, 8000, 8000), "only the remainder was released; captured money stays moved")
    eq(me_ok(bob)["total"], 1000 + 1000, "payments stand")
    ok({p1["payment_id"], p2["payment_id"]} <= {x["payment_id"] for x in bob.all_payments()}, "capture payments remain in the feed")
    expect(capture(bob, c, {"amount": 1}), 409, "authorization_not_open")
    expect(void(ada, c), 200, msg="void is repeatable on a voided authorization")


@test("S2-149", "S2-150", "S2-151", "S2-152", "S2-153", "S2-135", "S2-148")
def test_list_authorizations():
    w = world2({"ada": 100000, "bob": 100000, "cy": 100000, "dee": 0})
    ada, bob, cy, dee = w["ada"], w["bob"], w["cy"], w["dee"]
    ids = {}

    def mk(src, to, amt, name):
        ids[name] = expect(authorize(src, to, amt), 201).json["authorization_id"]

    mk(ada, "bob", 10, "a1")
    mk(ada, "bob", 20, "a2")
    mk(ada, "bob", 30, "a3")
    mk(bob, "ada", 40, "b1")
    mk(cy, "dee", 50, "c1")
    expect(capture(bob, ids["a1"], {}), 201)
    expect(void(ada, ids["a2"]), 200)
    ids_of = lambda u, **q: [x["authorization_id"] for x in auths(u, limit=200, **q)["authorizations"]]
    eq(ids_of(ada), [ids["b1"], ids["a3"], ids["a2"], ids["a1"]], "ada: payer or receiver, newest first (strict creation order)")
    eq(ids_of(bob), [ids["b1"], ids["a3"], ids["a2"], ids["a1"]], "bob")
    eq(ids_of(cy), [ids["c1"]], "cy")
    eq(ids_of(dee), [ids["c1"]], "dee: receiver sees it")
    eq(ids_of(ada, direction="outgoing"), [ids["a3"], ids["a2"], ids["a1"]], "outgoing = caller is the payer")
    eq(ids_of(ada, direction="incoming"), [ids["b1"]], "incoming = caller is the receiver")
    eq(ids_of(bob, direction="outgoing"), [ids["b1"]], "bob outgoing")
    eq(ids_of(bob, direction="incoming"), [ids["a3"], ids["a2"], ids["a1"]], "bob incoming")
    eq(ids_of(dee, direction="outgoing"), [], "dee has none outgoing")
    eq(ids_of(ada, status="open"), [ids["b1"], ids["a3"]], "open")
    eq(ids_of(ada, status="captured"), [ids["a1"]], "captured")
    eq(ids_of(ada, status="voided"), [ids["a2"]], "voided")
    eq(ids_of(ada, status="expired"), [], "expired")
    eq(ids_of(ada, direction="outgoing", status="open"), [ids["a3"]], "combined")
    for x in auths(ada, limit=200)["authorizations"]:
        check_auth(x)
    # paging reassembles the order, has_more boundaries
    pages = []
    for off in (0, 2):
        r = auths(ada, limit=2, offset=off)
        pages += [x["authorization_id"] for x in r["authorizations"]]
        eq(r["has_more"], off == 0, "has_more at offset %d" % off)
    eq(pages, ids_of(ada), "pages reassemble the list")
    eq(auths(ada, limit=4)["has_more"], False, "exactly limit remaining")
    eq(auths(ada, limit=3)["has_more"], True, "one more beyond")
    eq(auths(ada, offset=4), {"authorizations": [], "has_more": False}, "offset at the end")
    eq(auths(ada, offset=99, limit=3), {"authorizations": [], "has_more": False}, "offset beyond the end")
    r = auths(ada, direction="outgoing", status="open", limit=50, offset=0, whatever="x")
    eq(len(r["authorizations"]), 1, "unknown params ignored")
    for q in ("direction=sideways", "direction=INCOMING", "status=pending", "status=PAID", "limit=0", "limit=201",
              "offset=-1", "limit=x", "limit=1e2", "limit=4.0", "limit=+4", "offset=+1", "offset=1.0", "offset=1e0"):
        expect(ada.get("/authorizations?" + q), 422, "validation_failed", "GET /authorizations?" + q)
    expect(http("GET", "/authorizations"), 401, "unauthenticated")
    # default page size 50
    w = world2({"ada": 100000, "bob": 0})
    ada = w["ada"]
    res = burst([lambda i=i: authorize(ada, "bob", i + 1) for i in range(51)], workers=17)
    ok(all(r.status == 201 for r in res), "51 authorizations created")
    r = auths(ada)
    eq((len(r["authorizations"]), r["has_more"]), (50, True), "default limit 50")
    eq(len(auths(ada, limit=200)["authorizations"]), 51, "all 51")
    eq(len({x["authorization_id"] for x in auths(ada, limit=200)["authorizations"]}), 51, "unique ids")


@test("S2-127", "S2-095", "S2-003")
def test_every_payment_exposes_authorization_id_null():
    w = world2({"ada": 10000, "bob": 1000, "op": 0}, operators=["op"],
               payments=[{"id": "p_seed", "from_user_id": "u_ada", "to_user_id": "u_bob", "amount": 5, "note": "s", "visibility": "public"}])
    ada, bob, op = w["ada"], w["bob"], w["op"]
    direct = expect(ada.pay("bob", 10), 201).json
    rid = expect(bob.request("ada", 20), 201).json["request_id"]
    paid = expect(ada.pay_request(rid), 201).json
    st = expect(op.settle([{"from_handle": "ada", "to_handle": "bob", "amount": 1}]), 201).json["payments"][0]
    a = expect(authorize(ada, "bob", 100), 201).json["authorization_id"]
    cap = expect(capture(bob, a, {}), 201).json
    for name, p in (("direct", direct), ("request", paid), ("settlement", st)):
        check_payment2(p, request_id=p["request_id"])
        eq(p["authorization_id"], None, "%s payment: authorization_id null" % name)
    eq(paid["request_id"], rid, "request_id semantics unchanged")
    eq(direct["request_id"], None, "request_id null for direct payments")
    eq(cap["request_id"], None, "capture payments have request_id null")
    eq(cap["authorization_id"], a, "capture payment links its authorization")
    feed = {p["payment_id"]: p for p in ada.all_payments()}
    eq(feed["p_seed"]["authorization_id"], None, "seeded payment")
    for p in feed.values():
        ok("authorization_id" in p, "every feed payment has authorization_id")
    # an immediate payment leaves no hold behind and creates no authorization
    eq(me_ok(ada)["held"], 0, "held after direct payments")
    eq([x["authorization_id"] for x in all_auths(ada)], [a], "only the explicit authorization exists")


@test("S2-093", "S2-094", "S2-111", "S2-112")
def test_me_fields_with_multiple_holds():
    w = world2({"ada": 10000, "bob": 0, "cy": 0})
    ada, bob, cy = w["ada"], w["bob"], w["cy"]
    m = me_ok(ada)
    eq((m["balance"], m["total"], m["available"], m["held"]), (10000,) * 3 + (0,), "no holds")
    ids = [expect(authorize(ada, h, amt), 201).json["authorization_id"] for h, amt in (("bob", 1000), ("cy", 2000), ("bob", 500))]
    m = me_ok(ada)
    eq((m["total"], m["held"], m["available"]), (10000, 3500, 6500), "three holds")
    expect(void(ada, ids[1]), 200)
    m = me_ok(ada)
    eq((m["held"], m["available"]), (1500, 8500), "void released one")
    expect(capture(bob, ids[0], {"amount": 400, "final": False}), 201)
    m = me_ok(ada)
    eq((m["total"], m["held"], m["available"]), (9600, 1100, 8500), "partial capture")
    expect(capture(bob, ids[0], {"amount": 100}), 201)
    m = me_ok(ada)
    eq((m["total"], m["held"], m["available"]), (9500, 500, 9000), "final capture released 500")
    # receivers and others: holds of other people do not show up
    for u in (bob, cy):
        mu = me_ok(u)
        eq(mu["held"], 0, "only the payer's wallet is held")
    eq(me_ok(bob)["total"], 500, "bob received 400+100")
    eq(sum(me_ok(u)["total"] for u in (ada, bob, cy)), 10000, "conservation of totals")
