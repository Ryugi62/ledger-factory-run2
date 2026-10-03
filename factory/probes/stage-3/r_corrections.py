"""Stage 3: POST /payments/{id}/corrections and GET /payments/{id}/revisions."""
import json
from datetime import timedelta

from lib3 import *  # noqa: F401,F403


def world_c(extra=None):
    bal = {"ada": 10000, "bob": 5000, "cy": 300, "dan": 0, "op": 0}
    if extra:
        bal.update(extra)
    return lib2.world2(bal, operators=["op"])


@test("S3-049", "S3-054", "S3-055", "S3-048", "S3-061", "S3-062", "S3-063", "S3-034", "S3-014", "S3-076", "S3-077", "S3-078", "S3-060")
def test_correction_cycle_decrease_increase_reverse():
    w = world_c()
    ada, bob, cy = w["ada"], w["bob"], w["cy"]
    key0 = fresh_key()
    body0 = {"to_handle": "bob", "amount": 500, "note": "lunch", "visibility": "private"}
    r0 = expect(ada.post("/payments", body0, key=key0), 201)
    p = r0.json
    pid, c1 = p["payment_id"], p["created_at"]
    feed_ada, feed_bob = ada.all_payments(), bob.all_payments()
    ok(pid not in [x["payment_id"] for x in cy.all_payments()], "(control) private payment hidden from a third party")
    revs = revisions(ada, pid)
    eq(len(revs), 1, "a payment starts with revision 1")
    eq((revs[0]["amount"], revs[0]["reason"]), (500, ""), "revision 1: amount as paid, reason empty string")
    same_instant(revs[0]["effective_at"], c1, "revision 1 effective_at = created_at")
    same_instant(revs[0]["recorded_at"], c1, "revision 1 recorded_at = created_at")
    eq(revisions(bob, pid), revs, "the receiver reads the same history")
    start = {h: lib2.me_ok(u)["balance"] for h, u in w.items()}
    eq((start["ada"], start["bob"]), (9500, 5500), "after the payment")
    total0 = sum(start.values())
    seen = []
    for step, (rev, amount, ada_bal, bob_bal) in enumerate([(1, 400, 9600, 5400), (2, 700, 9300, 5700), (3, 0, 10000, 5000)]):
        r = expect(correct(ada, pid, rev, amount, c1, "step %d" % step), 201, msg="correction %d" % rev)
        check_correction(r.json, pid, rev + 1, amount, c1, "step %d" % step)
        ok(inst(r.json["recorded_at"]) > inst(c1), "recorded_at of a correction is later than revision 1's")
        seen.append(r.json)
        eq((lib2.me_ok(ada)["balance"], lib2.me_ok(bob)["balance"]), (ada_bal, bob_bal),
           "difference moves between the same two wallets (increase debits the sender, decrease debits the receiver); amount %d" % amount)
        eq(lib2.me_ok(cy)["balance"], 300, "third parties are untouched")
        eq(sum(lib2.me_ok(u)["balance"] for u in w.values()), total0, "conservation")
        eq(ada.all_payments(), feed_ada, "GET /activity still shows the original payment (original amount, order, count)")
        eq(bob.all_payments(), feed_bob, "...also for the receiver")
        ok(pid not in [x["payment_id"] for x in cy.all_payments()], "visibility unchanged: still hidden from a third party")
        rv = revisions(ada, pid)
        eq(len(rv), rev + 1, "history grows by one")
        eq(rv[:rev], revs[:rev] if len(revs) >= rev else rv[:rev], "earlier revisions are immutable")
        revs = rv
    # the original response is unchanged: a replay of the original POST /payments
    r = expect(ada.post("/payments", body0, key=key0), 200, msg="replay of the original payment")
    eq(r.json, p, "original idempotent response is unchanged (amount 500)")
    # statement: one entry for the payment, with the selected (zero) amount and a zero delta
    e = [x for x in full_stmt(ada)["entries"] if x["payment"]["payment_id"] == pid]
    eq(len(e), 1, "no correction is counted alongside the revision it replaces: one entry per payment")
    eq((e[0]["revision"], e[0]["delta"], e[0]["payment"]["amount"]), (4, 0, 0), "zero-amount revision still appears, with zero delta")
    for k in ("from_user_id", "to_user_id", "visibility", "note", "created_at", "payment_id", "from_handle", "to_handle"):
        eq(e[0]["payment"][k], p[k], "payment.%s is the original payment's" % k)
    eq(me_at(ada)["balance"], 10000, "plain GET /me reports current corrected values")
    eq(me_at(bob)["balance"], 5000, "plain GET /me (receiver)")
    # a payment made after the corrections still sorts by created_at in the feed
    p2 = new_payment(bob, "cy", 10)
    ok(p2["payment_id"] in [x["payment_id"] for x in bob.all_payments()][:3], "the new payment is among the newest")


@test("S3-004", "S3-037", "S3-035", "S3-034", "S3-014")
def test_request_paid_and_seeded_payments_are_correctable_and_openings_hold():
    # request-paid payment
    w = world_c()
    ada, bob = w["ada"], w["bob"]
    rq = expect(bob.request("ada", 300), 201).json["request_id"]
    q = expect(ada.pay_request(rq), 201).json
    r = expect(correct(ada, q["payment_id"], 1, 200, q["created_at"], "discount"), 201, msg="a request-paid payment is correctable by its payer")
    eq(lib2.me_ok(ada)["balance"], 10000 - 200, "ada")
    got = [x for x in bob.all_requests() if x["request_id"] == rq][0]
    eq((got["status"], got["payment_id"]), ("paid", q["payment_id"]), "the request stays paid with the same payment")
    eq([x for x in ada.all_payments() if x["payment_id"] == q["payment_id"]][0]["amount"], 300, "feed shows the original amount")
    # seeded payments: revision 1 = supplied created_at; corrections never change the opening balance
    t = whole(utcnow()) - timedelta(days=10)
    opening = {"ada": 1000, "bob": 0, "cy": 50}
    pays = [fx_pay("p_001", "ada", "bob", 100, created_at=t + timedelta(hours=1)),
            fx_pay("p_002", "bob", "cy", 40, created_at=t + timedelta(hours=2))]
    w = hist_world(opening, pays)
    ada, bob, cy = w["ada"], w["bob"], w["cy"]
    rv = revisions(ada, "p_001")
    eq(len(rv), 1, "seeded payment: revision 1 only")
    same_instant(rv[0]["effective_at"], t + timedelta(hours=1), "seeded revision 1 effective_at = supplied created_at")
    same_instant(rv[0]["recorded_at"], t + timedelta(hours=1), "seeded revision 1 recorded_at = supplied created_at")
    eq((rv[0]["amount"], rv[0]["reason"]), (100, ""), "seeded revision 1")
    eff = fmts(t + timedelta(hours=1, minutes=30))
    expect(correct(ada, "p_001", 1, 70, eff, "seeded fix"), 201)
    expect(correct(bob, "p_002", 1, 40, fmts(t + timedelta(hours=1, minutes=45)), "move earlier"), 201)
    eq(lib2.me_ok(ada)["balance"], 930, "ada: 1000 - 70")
    eq(lib2.me_ok(bob)["balance"], 30, "bob: 0 + 70 - 40")
    for h, want in opening.items():
        eq(me_at(w[h], as_of="1970-01-01T00:00:00+00:00")["balance"], want, "opening balance of %s is unchanged by corrections" % h)
        j = full_stmt(w[h], to="1970-01-01T00:00:00+00:00")
        eq((j["opening"], j["closing"], j["entries"]), (want, want, []), "statement before everything: opening == closing == opening balance (%s)" % h)
        eq(full_stmt(w[h])["opening"], want, "opening_balance of the whole statement (%s)" % h)
        eq(me_at(w[h], as_of=fmts(t))["balance"], want, "as_of before the earliest effective instant (%s)" % h)


@test("S3-041", "S3-064", "S3-065", "S3-040", "S3-059", "S3-117", "S3-033")
def test_correction_and_revisions_permissions_and_lookup():
    w = world_c()
    ada, bob, cy, op = w["ada"], w["bob"], w["cy"], w["op"]
    pub = new_payment(ada, "bob", 100)
    prv = new_payment(ada, "bob", 120, visibility="private")
    before = observe_state({"ada": ada, "bob": bob, "cy": cy}, [(ada, pub["payment_id"]), (ada, prv["payment_id"])])
    eff = pub["created_at"]
    expect(correct(bob, pub["payment_id"], 1, 50, eff), 403, "forbidden", "the receiver is not the sender")
    expect(correct(bob, prv["payment_id"], 1, 50, eff), 403, "forbidden", "the receiver of a private payment")
    expect(correct(cy, pub["payment_id"], 1, 50, eff), 403, "forbidden", "a third party on a public payment")
    r = correct(cy, prv["payment_id"], 1, 50, eff)
    ok(r.status in (403, 404) and r.code in ("forbidden", "not_found"), "a third party on a private payment: 403 or 404, got %r" % r)
    expect(correct(op, pub["payment_id"], 1, 50, eff), 403, "forbidden", "an operator is not the sender")
    expect(correct(ada, "p_does_not_exist", 1, 50, eff), 404, "not_found", "unknown payment")
    expect(correct(cy, "p_does_not_exist", 1, 50, eff), 404, "not_found", "unknown payment, other caller")
    path = "/payments/%s/corrections" % pub["payment_id"]
    body = corr_body(1, 50, eff)
    expect(http("POST", path, body=body, key=fresh_key()), 401, "unauthenticated", "no token")
    expect(http("POST", path, body=body, key=fresh_key(), token="nope"), 401, "unauthenticated", "unknown token")
    expect(http("POST", path, body=body, token=ada.token), 400, "missing_idempotency_key", "no key")
    expect(http("POST", path, body=body, token=ada.token, key=""), 400, "missing_idempotency_key", "empty key")
    expect(http("POST", path, body=body, token=ada.token, key="k" * 255), 201, msg="a 255-character key is fine")
    expect(http("POST", path, body=corr_body(2, 60, eff), token=ada.token, key="k" * 256), 422, "validation_failed", "256-character key")
    # revisions
    one = revisions(ada, pub["payment_id"])
    eq(len(one), 2, "only the 255-key correction was applied")
    eq(revisions(bob, pub["payment_id"]), one, "the receiver can read it")
    eq(len(revisions(bob, prv["payment_id"])), 1, "the receiver of a private payment can read it")
    expect(cy.get("/payments/%s/revisions" % pub["payment_id"]), 404, "not_found", "a third party gets 404 even for a public payment")
    expect(cy.get("/payments/%s/revisions" % prv["payment_id"]), 404, "not_found", "a third party, private payment")
    expect(op.get("/payments/%s/revisions" % pub["payment_id"]), 404, "not_found", "an operator who is not a party")
    expect(ada.get("/payments/p_does_not_exist/revisions"), 404, "not_found", "unknown payment")
    expect(http("GET", "/payments/%s/revisions" % pub["payment_id"]), 401, "unauthenticated", "no token")
    expect(http("GET", "/payments/%s/revisions" % pub["payment_id"], token="nope"), 401, "unauthenticated", "bad token")
    # the failed attempts left the second payment untouched
    after = observe_state({"ada": ada, "bob": bob, "cy": cy}, [(ada, prv["payment_id"])])
    eq(after["rev:" + prv["payment_id"]], before["rev:" + prv["payment_id"]], "rejected corrections leave the revision history alone")


@test("S3-042", "S3-043", "S3-044", "S3-045", "S3-046", "S3-047", "S3-115", "S3-059", "S3-049")
def test_correction_validation_matrix():
    w = lib2.world2({"ada": 3_000_000_000, "bob": 5000})
    ada, bob = w["ada"], w["bob"]
    p = new_payment(ada, "bob", 500)
    pid, c1 = p["payment_id"], p["created_at"]
    path = "/payments/%s/corrections" % pid
    good = corr_body(1, 400, c1, "ok")
    before = observe_state({"ada": ada, "bob": bob}, [(ada, pid)])
    now = utcnow()

    def post(body, key=None):
        return ada.post(path, body, key=key or fresh_key())

    for field in good:
        b = dict(good)
        del b[field]
        expect(post(b), 422, "validation_failed", "missing %s" % field)
    cases422 = [("expected_revision", [0, -1, 1.5]),
                ("amount", [-1, 1_000_000_001, 1.5, "400", True, False, None, [], {}]),
                ("reason", ["", "x" * 201, "\U0001F600" * 201]),
                ("effective_at", [fmts(now + timedelta(hours=1)), fmts(now + timedelta(days=1)), "2999-01-01T00:00:00+00:00",
                                  "", "yesterday", "2026-09-24", "2026-09-24T13:20:00"])]
    for field, values in cases422:
        for v in values:
            b = dict(good)
            b[field] = v
            expect(post(b), 422, "validation_failed", "%s = %r" % (field, v))
    for field, values in (("expected_revision", ["1", True, None, [], {}]), ("reason", [5, None, [], True]), ("effective_at", [5, None, True, []])):
        for v in values:
            b = dict(good)
            b[field] = v
            r = post(b)
            ok(r.status in (400, 422) and r.code in ("malformed_request", "validation_failed"),
               "wrong JSON type for %s = %r: 400 malformed_request or 422 validation_failed, got %r" % (field, v, r))
    expect(post([]), 400, "malformed_request", "array body")
    expect(ada.post(path, raw='{"expected_revision": 1,', key=fresh_key()), 400, "malformed_request", "unparseable body")
    eq(observe_state({"ada": ada, "bob": bob}, [(ada, pid)]), before, "every rejected correction leaves no trace")
    # a key of a rejected request is a first use afterwards
    k = fresh_key()
    b = dict(good)
    b["amount"] = -5
    expect(ada.post(path, b, key=k), 422, "validation_failed")
    expect(ada.post(path, dict(good), key=k), 201, msg="key of a 422 is reusable with a valid body")
    # boundaries and representations on fresh payments
    p2, p3, p4, p5, p6, p7 = [new_payment(ada, "bob", 3) for _ in range(6)]

    def ok_corr(pp, amount=None, reason="r", eff=None, raw=None):
        body = corr_body(1, 1 if amount is None else amount, eff or pp["created_at"], reason)
        if raw:
            return ada.post("/payments/%s/corrections" % pp["payment_id"], raw=raw % (pp["created_at"],), key=fresh_key())
        return ada.post("/payments/%s/corrections" % pp["payment_id"], body, key=fresh_key())

    expect(ok_corr(p2, 0), 201, msg="amount 0 reverses the payment")
    expect(ok_corr(p3, 1_000_000_000), 201, msg="amount 1000000000 is the maximum")
    expect(ok_corr(p4, reason="x" * 200), 201, msg="reason of 200 characters")
    expect(ok_corr(p5, reason="\U0001F600" * 200), 201, msg="reason of 200 code points (800 bytes)")
    r = expect(ok_corr(p6, raw='{"expected_revision": 1, "amount": 400.0, "effective_at": "%s", "reason": "int-valued"}'), 201, msg="400.0 is a valid integral amount")
    eq(r.json["amount"], 400, "amount echoed as an integer")
    expect(ok_corr(p7, raw='{"expected_revision": 1, "amount": 4e2, "effective_at": "%s", "reason": "int-valued"}'), 201, msg="4e2 is a valid integral amount")
    # effective_at: not later than now, but may precede the payment itself
    p8 = new_payment(ada, "bob", 3)
    expect(ok_corr(p8, 3, eff=fmts(inst(p8["created_at"]) - timedelta(days=3))), 201, msg="effective_at earlier than the payment's created_at is allowed")
    p9 = new_payment(ada, "bob", 3)
    expect(ok_corr(p9, 4, eff=p9["created_at"]), 201, msg="effective_at equal to created_at")


@test("S3-051", "S3-052", "S3-053", "S3-115", "S3-116", "S3-040", "S3-049")
def test_stale_revision_replay_and_key_reuse():
    w = world_c()
    ada, bob = w["ada"], w["bob"]
    pa = new_payment(ada, "bob", 500)
    pb = new_payment(ada, "bob", 300)
    pid = pa["payment_id"]
    eff = fmts(utcnow() - timedelta(days=1))
    k1, k2 = fresh_key(), fresh_key()
    b1, b2 = corr_body(1, 450, eff, "first"), corr_body(2, 420, eff, "second")
    r1 = expect(ada.post("/payments/%s/corrections" % pid, b1, key=k1), 201)
    r2 = expect(ada.post("/payments/%s/corrections" % pid, b2, key=k2), 201)
    eq((r1.json["revision"], r2.json["revision"]), (2, 3), "revisions")
    ok(inst(r2.json["recorded_at"]) > inst(r1.json["recorded_at"]), "recorded_at strictly increases per payment")
    state = observe_state({"ada": ada, "bob": bob}, [(ada, pid)])
    # replay of the first correction after a newer revision exists
    rp = expect(ada.post("/payments/%s/corrections" % pid, b1, key=k1), 200, msg="successful replay returns 200 even after newer revisions")
    eq(rp.json, r1.json, "replay returns that original revision")
    eq(observe_state({"ada": ada, "bob": bob}, [(ada, pid)]), state, "a replay changes nothing")
    # stale expected revision
    ks = fresh_key()
    expect(ada.post("/payments/%s/corrections" % pid, corr_body(1, 100, eff, "stale"), key=ks), 409, "stale_revision", "revision 1 is stale")
    expect(ada.post("/payments/%s/corrections" % pid, corr_body(2, 100, eff, "stale"), key=ks), 409, "stale_revision", "revision 2 is stale too")
    eq(observe_state({"ada": ada, "bob": bob}, [(ada, pid)]), state, "stale attempts change nothing")
    r3 = expect(ada.post("/payments/%s/corrections" % pid, corr_body(3, 100, eff, "now current"), key=ks), 201, msg="a key that failed with 409 is a first use afterwards")
    eq(r3.json["revision"], 4, "revision 4")
    # same key, different body
    expect(ada.post("/payments/%s/corrections" % pid, corr_body(1, 451, eff, "first"), key=k1), 409, "idempotency_key_reuse", "changed amount")
    expect(ada.post("/payments/%s/corrections" % pid, corr_body(1, 450, eff, "first!"), key=k1), 409, "idempotency_key_reuse", "changed reason")
    expect(ada.post("/payments/%s/corrections" % pid, {"expected_revision": 1, "amount": -3, "effective_at": "junk", "reason": ""}, key=k1),
           409, "idempotency_key_reuse", "an invalid body with a claimed key is still a key conflict (resolved before validation)")
    expect(ada.post("/payments/%s/corrections" % pid, b1, key=k1), 200, msg="the original body still replays")
    eq(len(revisions(ada, pid)), 4, "four revisions, no extras")
    # same key and body on another payment's path is a different request, not a replay
    kx = fresh_key()
    bx = corr_body(1, 250, eff, "same body")
    ra = expect(ada.post("/payments/%s/corrections" % pb["payment_id"], bx, key=kx), 201)
    pc = new_payment(ada, "bob", 260)
    rb = expect(ada.post("/payments/%s/corrections" % pc["payment_id"], bx, key=kx), 201, msg="same key + body on another payment is a first use")
    eq((ra.json["payment_id"], rb.json["payment_id"]), (pb["payment_id"], pc["payment_id"]), "each revision is on its own payment")
    # keys are scoped to the user: another user can neither replay nor read ada's receipt
    expect(bob.post("/payments/%s/corrections" % pid, b1, key=k1), 403, "forbidden", "bob replaying ada's key+body is not a replay for him")


@test("S3-050", "S3-049")
def test_recorded_at_strictly_increases_within_one_second():
    w = world_c()
    ada = w["ada"]
    p = new_payment(ada, "bob", 500)
    pid = p["payment_id"]
    rec = [inst(p["created_at"])]
    amounts = [490, 480, 470, 460, 450, 440, 430, 420, 410, 400]
    for i, a in enumerate(amounts):
        r = expect(correct(ada, pid, i + 1, a, p["created_at"], "n%d" % i), 201, msg="correction %d" % (i + 2))
        rec.append(inst(r.json["recorded_at"]))
        lib.check_ts(r.json["recorded_at"])
    for a, b in zip(rec, rec[1:]):
        ok(b > a, "recorded times for one payment strictly increase: %s then %s (the service must not tie, even within one second)" % (a, b))
    rv = revisions(ada, pid)
    eq([inst(x["recorded_at"]) for x in rv], rec, "revisions endpoint reports the same recorded_at values")
    eq([x["amount"] for x in rv], [500] + amounts, "amounts in revision order")
    eq([x["reason"] for x in rv], [""] + ["n%d" % i for i in range(10)], "reasons in revision order")
