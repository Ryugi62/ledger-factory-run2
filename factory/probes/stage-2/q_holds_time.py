"""Stage 2: holds vs payments/pay/settlements, fixtures (ttl, seeded authorizations), expiry on the clock."""
import time

from lib2 import *  # noqa: F401,F403


@test("S2-096", "S2-091", "S2-117", "S2-098", "S2-097", "S2-094")
def test_insufficient_funds_is_evaluated_against_available():
    w = world2({"ada": 1000, "bob": 0, "cy": 100, "op": 0}, operators=["op"])
    ada, bob, cy, op = w["ada"], w["bob"], w["cy"], w["op"]
    a = expect(authorize(ada, "bob", 600), 201).json["authorization_id"]
    eq(me_ok(ada)["available"], 400, "available")
    # payments
    expect(ada.pay("bob", 401), 409, "insufficient_funds", "401 > available although total is 1000")
    eq(me_ok(ada)["total"], 1000, "refused payment moved nothing")
    expect(ada.pay("bob", 400), 201, msg="exactly available")
    expect(ada.pay("bob", 1), 409, "insufficient_funds")
    # request pay
    w = world2({"ada": 1000, "bob": 0, "cy": 100, "op": 0}, operators=["op"])
    ada, bob, cy, op = w["ada"], w["bob"], w["cy"], w["op"]
    a = expect(authorize(ada, "bob", 600), 201).json["authorization_id"]
    r1 = expect(bob.request("ada", 401), 201).json["request_id"]
    r2 = expect(bob.request("ada", 400), 201).json["request_id"]
    expect(ada.pay_request(r1), 409, "insufficient_funds")
    eq(auth_by_id(bob, a)["status"], "open", "hold intact")
    check_request([q for q in bob.all_requests() if q["request_id"] == r1][0], status="pending")
    expect(ada.pay_request(r2), 201, msg="request of exactly available")
    # settlements: net debit against available
    w = world2({"ada": 1000, "bob": 0, "cy": 100, "op": 0}, operators=["op"])
    ada, bob, cy, op = w["ada"], w["bob"], w["cy"], w["op"]
    expect(authorize(ada, "bob", 600), 201)
    expect(op.settle([{"from_handle": "ada", "to_handle": "bob", "amount": 401}]), 409, "insufficient_funds")
    expect(op.settle([{"from_handle": "ada", "to_handle": "bob", "amount": 500},
                      {"from_handle": "cy", "to_handle": "ada", "amount": 100}]), 201, msg="net debit 400 == available")
    eq(me_ok(ada)["total"], 600, "settled")
    m = me_ok(ada)
    eq((m["available"], m["held"]), (0, 600), "available is 0 now")
    expect(op.settle([{"from_handle": "ada", "to_handle": "cy", "amount": 1}]), 409, "insufficient_funds")
    expect(op.settle([{"from_handle": "ada", "to_handle": "cy", "amount": 101},
                      {"from_handle": "cy", "to_handle": "ada", "amount": 100}]), 409, "insufficient_funds", "net -1 on available")
    eq(me_ok(ada)["total"], 600, "unchanged")
    # splits ignore balances and holds (unchanged)
    expect(ada.split(500, ["ada", "bob", "cy"]), 201, msg="splits never check balances")
    # after void the money is spendable again
    a = all_auths(ada)[0]["authorization_id"]
    expect(void(ada, a), 200)
    expect(ada.pay("bob", 600), 201, msg="released funds are spendable")
    eq(me_ok(ada)["total"], 0, "drained")


@test("S2-100", "S2-101", "S2-116", "S2-173", "S2-102")
def test_fixture_ttl_default_override_and_validation():
    w = world2({"ada": 1000, "bob": 0})
    a = expect(authorize(w["ada"], "bob", 10), 201).json
    eq(seconds_between(a["created_at"], a["expires_at"]), 600, "default TTL is 600 when omitted")
    for ttl in (60, 7200, 1):
        w = world2({"ada": 1000, "bob": 0}, ttl=ttl)
        a = expect(authorize(w["ada"], "bob", 10), 201).json
        eq(seconds_between(a["created_at"], a["expires_at"]), ttl, "TTL %d" % ttl)
    w = world2({"ada": 1000, "bob": 0}, ttl=60)
    ada = w["ada"]
    expect(authorize(ada, "bob", 10), 201)
    before = (me_ok(ada), all_auths(ada))
    for bad in (0, -1, -600, 1.5, "600", True, False, [], {}, 0.0):
        r = http("POST", "/_test/reset", body=fixture([fx_user("zzz", 1)], authorization_ttl_seconds=bad))
        expect(r, 422, "validation_failed", "authorization_ttl_seconds %r" % (bad,))
        eq((me_ok(ada), all_auths(ada)), before, "rejected reset changes nothing (ttl %r)" % (bad,))
    # a reset without the key goes back to 600; authorizations of the old world are gone
    reset(fixture([fx_user("ada", 1000), fx_user("bob", 0)]))
    ada = login("ada@example.com", handle="ada")
    eq(all_auths(ada), [], "reset clears authorizations")
    eq(me_ok(ada)["held"], 0, "and holds")
    a = expect(authorize(ada, "bob", 10), 201).json
    eq(seconds_between(a["created_at"], a["expires_at"]), 600, "omitted again -> 600")


@test("S2-102", "S2-103", "S2-105", "S2-106", "S2-107", "S2-110", "S2-109", "S2-139", "S2-138", "S2-135", "S2-170")
def test_seeded_authorizations():
    exp_future = in_(hours=2)
    exp_past = in_(hours=-3)
    auths_fx = [
        fx_auth("a_1", "ada", "bob", 2000, "open", exp_future, note="deposit", visibility="private"),
        fx_auth("a_2", "ada", "bob", 50000, "open", exp_past),                 # expired by the clock; larger than the balance
        fx_auth("a_3", "ada", "bob", 700, "captured", exp_future),
        fx_auth("a_4", "ada", "bob", 800, "voided", exp_future),
        fx_auth("a_5", "ada", "bob", 900, "expired", exp_past),
        fx_auth("a_6", "ada", "cy", 1000, "open", exp_future, note="six", visibility="public"),
    ]
    w = world2({"ada": 10000, "bob": 500, "cy": 0}, auths=auths_fx, ttl=600)
    ada, bob, cy = w["ada"], w["bob"], w["cy"]
    m = me_ok(ada)
    eq((m["balance"], m["total"], m["held"], m["available"]), (10000, 10000, 3000, 7000), "seeded balance is total; only unexpired open holds count")
    eq(me_ok(bob)["held"], 0, "receiver holds nothing")
    listed = {x["authorization_id"]: x for x in all_auths(ada)}
    eq(sorted(listed), ["a_1", "a_2", "a_3", "a_4", "a_5", "a_6"], "all seeded authorizations are listed with their ids")
    for aid, st in (("a_1", "open"), ("a_2", "expired"), ("a_3", "captured"), ("a_4", "voided"), ("a_5", "expired"), ("a_6", "open")):
        check_auth(listed[aid], status=st)
    check_auth(listed["a_1"], frm=ada, to=bob, amount=2000, remaining=2000, note="deposit", visibility="private")
    eq(parse_ts(listed["a_1"]["expires_at"]), parse_ts(exp_future), "seeded expires_at is kept (not recomputed from the TTL)")
    eq(parse_ts(listed["a_2"]["expires_at"]), parse_ts(exp_past), "past expires_at kept")
    eq(listed["a_2"]["remaining_amount"], 0, "expired holds nothing")
    eq([x["authorization_id"] for x in auths(ada, status="expired", limit=200)["authorizations"]].count("a_2"), 1, "a_2 matches expired")
    ok("a_2" not in [x["authorization_id"] for x in auths(ada, status="open", limit=200)["authorizations"]], "never open")
    # actions on seeded authorizations
    expect(capture(bob, "a_2", {}), 409, "authorization_expired", "expired by the clock")
    r = expect(capture(bob, "a_5", {}), 409, msg="seeded status expired")
    ok(r.code in ("authorization_expired", "authorization_not_open"), "seeded expired: %s" % r.code)
    expect(capture(bob, "a_3", {}), 409, "authorization_not_open", "seeded captured")
    expect(capture(bob, "a_4", {}), 409, "authorization_not_open", "seeded voided")
    expect(void(ada, "a_3"), 409, "authorization_not_open")
    expect(void(ada, "a_5"), 409, "authorization_not_open")
    expect(void(ada, "a_2"), 409, "authorization_not_open", "expired by the clock cannot be voided")
    r = expect(capture(bob, "a_1", {"amount": 500}), 201, msg="capture a seeded open authorization")
    check_payment2(r.json, frm=ada, to=bob, amount=500, note="deposit", visibility="private", request_id=None)
    eq(r.json["authorization_id"], "a_1", "authorization_id is the seeded id")
    m = me_ok(ada)
    eq((m["total"], m["held"], m["available"]), (9500, 1000, 8500), "capture released a_1's remainder, a_6 still held")
    expect(void(ada, "a_6"), 200, msg="void a seeded open authorization")
    m = me_ok(ada)
    eq((m["total"], m["held"], m["available"]), (9500, 0, 9500), "all released")
    # an earlier (stage-1 style) fixture without authorizations
    reset(fixture([fx_user("ada", 100), fx_user("bob", 0)]))
    ada = login("ada@example.com", handle="ada")
    eq(all_auths(ada), [], "omission means an empty list")
    m = me_ok(ada)
    eq((m["balance"], m["total"], m["available"], m["held"]), (100, 100, 100, 0), "no holds")


@test("S2-104", "S2-103", "S2-105", "S2-107")
def test_seeded_holds_larger_than_balance_are_a_reset_error():
    w = world2({"ada": 1000, "bob": 50})
    ada = w["ada"]
    expect(ada.pay("bob", 10), 201)
    before = (me_ok(ada), ada.all_payments())

    def users():
        return [fx_user("ada", 1000), fx_user("bob", 1000), fx_user("cy", 0)]

    def rejected(auths_fx, why):
        r = http("POST", "/_test/reset", body=fixture(users(), authorizations=auths_fx))
        expect(r, 422, "validation_failed", why)
        eq((me_ok(ada), ada.all_payments()), before, "rejected reset changes nothing: " + why)

    rejected([fx_auth("h1", "ada", "bob", 1001)], "single hold above balance")
    rejected([fx_auth("h1", "ada", "bob", 600), fx_auth("h2", "ada", "cy", 401)], "two holds summing above balance")
    rejected([fx_auth("h1", "ada", "bob", 500), fx_auth("h2", "ada", "cy", 400), fx_auth("h3", "ada", "bob", 101)], "three holds")
    rejected([fx_auth("h1", "ada", "bob", 1500, status="open", expires_at=in_(hours=3))], "far-future expiry still counts")
    # allowed: equal to the balance; expired / captured / voided are not counted; other payers independent
    ok_fx = [fx_auth("h1", "ada", "bob", 600), fx_auth("h2", "ada", "cy", 400),
             fx_auth("h3", "ada", "bob", 9000, status="expired", expires_at=in_(hours=-2)),
             fx_auth("h4", "ada", "bob", 9000, status="open", expires_at=in_(hours=-5)),
             fx_auth("h5", "ada", "bob", 9000, status="captured"),
             fx_auth("h6", "ada", "bob", 9000, status="voided"),
             fx_auth("h7", "bob", "cy", 1000)]
    reset(fixture(users(), authorizations=ok_fx))
    ada, bob = login("ada@example.com", handle="ada"), login("bob@example.com", handle="bob")
    m = me_ok(ada)
    eq((m["total"], m["held"], m["available"]), (1000, 1000, 0), "holds equal to the balance are fine")
    m = me_ok(bob)
    eq((m["total"], m["held"], m["available"]), (1000, 1000, 0), "another payer's holds are counted separately")
    expect(ada.pay("bob", 1), 409, "insufficient_funds")


@test("S2-089", "S2-107", "S2-108", "S2-109", "S2-136", "S2-139", "S2-147", "S2-099", "S2-106")
def test_expiry_happens_on_the_clock():
    w = world2({"ada": 1000, "bob": 0, "cy": 1000}, ttl=2)
    ada, bob, cy = w["ada"], w["bob"], w["cy"]
    ka = fresh_key()
    body_a = {"to_handle": "bob", "amount": 600, "note": "A"}
    ra = expect(ada.post("/authorizations", body_a, key=ka), 201).json
    eq(seconds_between(ra["created_at"], ra["expires_at"]), 2, "ttl 2")
    rb = expect(authorize(cy, "bob", 700), 201).json
    rc = expect(authorize(ada, "bob", 300), 201).json
    p = expect(capture(bob, rc["authorization_id"], {"amount": 100, "final": False}), 201).json    # partial, stays open
    m = me_ok(ada)
    eq((m["total"], m["held"], m["available"]), (900, 800, 100), "before the deadline")
    expect(ada.pay("bob", 101), 409, "insufficient_funds", "held funds are not spendable before the deadline")
    sleep_until(max(ra["expires_at"], rb["expires_at"], rc["expires_at"], key=parse_ts), 1.3)
    # first contact after the deadline is a READ for cy and a WRITE for ada
    mc = me_ok(cy)
    eq((mc["total"], mc["held"], mc["available"]), (1000, 0, 1000), "expiry reflected by a read without any request at the deadline")
    r = expect(ada.pay("bob", 900), 201, msg="expiry reflected by a write: the released funds are spendable")
    m = me_ok(ada)
    eq((m["total"], m["held"], m["available"]), (0, 0, 0), "everything spent, nothing negative")
    listed = {x["authorization_id"]: x for x in all_auths(ada)}
    check_auth(listed[ra["authorization_id"]], status="expired", captured=0, remaining=0)
    check_auth(listed[rc["authorization_id"]], status="expired", captured=100, remaining=0, payment_ids=[p["payment_id"]])
    check_auth(auth_by_id(cy, rb["authorization_id"]), status="expired", remaining=0)
    ok(ra["authorization_id"] in [x["authorization_id"] for x in auths(ada, status="expired", limit=200)["authorizations"]], "filter expired")
    ok(ra["authorization_id"] not in [x["authorization_id"] for x in auths(ada, status="open", limit=200)["authorizations"]], "never open once expired")
    ok(p["payment_id"] in {x["payment_id"] for x in ada.all_payments()}, "partial capture payment remains")
    eq(me_ok(bob)["total"], 900 + 100, "bob holds the capture and the payment")
    expect(capture(bob, ra["authorization_id"], {}), 409, "authorization_expired")
    expect(capture(bob, rc["authorization_id"], {"amount": 1}), 409, "authorization_expired", "partially captured, expired")
    expect(void(ada, ra["authorization_id"]), 409, "authorization_not_open")
    expect(void(cy, rb["authorization_id"]), 409, "authorization_not_open")
    # the original receipt still replays with the original body
    again = expect(ada.post("/authorizations", body_a, key=ka), 200, msg="replay after expiry")
    eq(again.json, ra, "original response, not the current state")
    eq(len(all_auths(ada)), 2, "no new authorization")
    eq(sum(me_ok(u)["total"] for u in (ada, bob, cy)), 2000, "conservation")
