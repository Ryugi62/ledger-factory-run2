"""Stage 4: POST /correction-batches."""
from datetime import timedelta, timezone

from lib4 import *  # noqa: F401,F403


def world_b(extra=None, ops=("op",)):
    bal = {"ada": 10000, "bob": 1000, "cy": 1000, "dan": 0, "op": 0, "op2": 0}
    if extra:
        bal.update(extra)
    return lib2.world2(bal, operators=list(ops))


def rev_count(owner, pid):
    return len(revisions(owner, pid))


@test("S4-003", "S4-021", "S4-029", "S4-034", "S4-035", "S4-036", "S4-038", "S4-039", "S4-040", "S4-041", "S4-025", "S4-004")
def test_batch_corrects_ordinary_and_request_payments_and_keeps_originals():
    w = world_b()
    ada, bob, cy, dan, op = w["ada"], w["bob"], w["cy"], w["dan"], w["op"]
    k1, k2 = fresh_key(), fresh_key()
    b1 = {"to_handle": "bob", "amount": 100, "note": "a"}
    p1 = expect(ada.post("/payments", b1, key=k1), 201).json
    rq = expect(bob.request("ada", 50), 201).json["request_id"]
    kq = fresh_key()
    q = expect(ada.post("/requests/%s/pay" % rq, {}, key=kq), 201).json
    b2 = {"to_handle": "dan", "amount": 200}
    p2 = expect(cy.post("/payments", b2, key=k2), 201).json
    snap_before = {h: stmt(u, limit=2) for h, u in w.items() if h in ("ada", "bob", "cy", "dan")}
    full_before = {h: full_stmt(u)["entries"] for h, u in w.items() if h in ("ada", "bob", "cy", "dan")}
    sleep_gap(1.1)
    r_pre = expect(correct(ada, p1["payment_id"], 1, 90, p1["created_at"], "single first"), 201).json     # p1 is at revision 2 before the batch
    sleep_gap(1.1)
    start = {h: lib2.me_ok(u)["balance"] for h, u in w.items()}
    plus2 = timezone(timedelta(hours=2))
    items = [item(p1["payment_id"], 2, 60, inst(p1["created_at"]).astimezone(plus2).isoformat(timespec="microseconds"), "reduce", ignored=True),
             item(q["payment_id"], 1, 0, q["created_at"], "reverse the request payment"),
             item(p2["payment_id"], 1, 150, fmt(inst(p2["created_at"]) - timedelta(hours=1)), "earlier and smaller")]
    key = fresh_key()
    body = {"corrections": items, "ignored_top_level": [1, 2]}
    r = expect(op.post("/correction-batches", body, key=key), 201, msg="batch by an operator who is a party to nothing")
    j = r.json
    check_batch(j, 3)
    bid = j["correction_batch_id"]
    eq([x["payment_id"] for x in j["revisions"]], [p1["payment_id"], q["payment_id"], p2["payment_id"]], "revisions in input order")
    eq([(x["revision"], x["amount"], x["reason"]) for x in j["revisions"]], [(3, 60, "reduce"), (2, 0, "reverse the request payment"), (2, 150, "earlier and smaller")], "revision numbers, amounts, reasons")
    same_instant(j["revisions"][0]["effective_at"], p1["created_at"], "effective_at as a +02:00 spelling")
    same_instant(j["revisions"][2]["effective_at"], inst(p2["created_at"]) - timedelta(hours=1), "effective_at of item 3")
    lib.check_ts(j["recorded_at"])
    ok(inst(j["recorded_at"]) > inst(r_pre["recorded_at"]), "recorded_at is strictly later than the previous recorded_at of a member that already had a correction")
    ok(inst(j["recorded_at"]) > inst(q["created_at"]) and inst(j["recorded_at"]) > inst(p2["created_at"]), "...and later than the other members' revision 1")
    now_ = {h: lib2.me_ok(u)["balance"] for h, u in w.items()}
    eq({h: now_[h] - start[h] for h in w}, {"ada": 30 + 50, "bob": -30 - 50, "cy": 50, "dan": -50, "op": 0, "op2": 0},
       "each item moved its difference between its own two wallets")
    eq(sum(now_.values()), sum(start.values()), "conservation")
    # revision histories expose the batch id; single corrections do not
    rv = revisions(ada, p1["payment_id"])
    eq([x["revision"] for x in rv], [1, 2, 3], "history")
    eq(rv[2].get("correction_batch_id"), bid, "revision 3 exposes the batch id")
    ok(rv[1].get("correction_batch_id") in (None,), "a single correction has no batch id")
    ok(rv[0].get("correction_batch_id") in (None,), "revision 1 has no batch id")
    same_instant(rv[2]["recorded_at"], j["recorded_at"], "same recorded_at")
    eq(revisions(bob, q["payment_id"])[1].get("correction_batch_id"), bid, "the other party sees it as well")
    # replay: 200, identical, even after newer revisions
    expect(correct(cy, p2["payment_id"], 2, 140, p2["created_at"], "after the batch"), 201)
    rp = expect(op.post("/correction-batches", body, key=key), 200, msg="replay")
    eq(rp.json, j, "replay returns the original batch response")
    expect(op.post("/correction-batches", {"corrections": items[:2]}, key=key), 409, "idempotency_key_reuse", "same key, different body")
    # original receipts and the feed
    eq(expect(ada.post("/payments", b1, key=k1), 200).json, p1, "original POST /payments response unchanged (amount 100)")
    eq(expect(ada.post("/requests/%s/pay" % rq, {}, key=kq), 200).json, q, "original request-pay response unchanged (amount 50, request_id kept)")
    eq(expect(cy.post("/payments", b2, key=k2), 200).json, p2, "original response of the third payment")
    feed = {x["payment_id"]: x for x in feed4(ada)}
    eq((feed[p1["payment_id"]]["amount"], feed[q["payment_id"]]["amount"]), (100, 50), "GET /activity shows the original payments")
    got = [x for x in bob.all_requests() if x["request_id"] == rq][0]
    eq((got["status"], got["payment_id"]), ("paid", q["payment_id"]), "the request stays paid")
    # saved statements keep their original content
    for h in ("ada", "bob", "cy", "dan"):
        j0 = snap_before[h]
        got = stmt(w[h], snapshot=j0["snapshot"], limit=200)
        eq(got["entries"], full_before[h], "an old snapshot pages its frozen entries for %s" % h)
        eq((got["opening_balance"], got["closing_balance"]), (j0["opening_balance"], j0["closing_balance"]), "old snapshot balances for %s" % h)
    # new statements reflect the revisions: compare with the model built from the revision records
    bal0 = {"ada": 10000, "bob": 1000, "cy": 1000, "dan": 0, "op": 0, "op2": 0}
    owners = {p1["payment_id"]: ada, q["payment_id"]: ada, p2["payment_id"]: cy}
    sources = {p1["payment_id"]: (ada.id, bob.id), q["payment_id"]: (ada.id, bob.id), p2["payment_id"]: (cy.id, dan.id)}
    m = model_from_service({uid(h): v for h, v in bal0.items()}, sources, owners)
    for h, u in w.items():
        got = full_stmt(u)
        want = m.statement(uid(h))
        eq((got["opening"], got["closing"]), (want["opening"], want["closing"]), "statement balances of %s" % h)
        compare_entries(got["entries"], want["entries"], "statement of %s" % h)


@test("S4-021", "S4-022", "S4-029", "S4-042", "S4-048", "S4-052")
def test_batch_authorization_and_shape_rules():
    w = world_b()
    ada, bob, op = w["ada"], w["bob"], w["op"]
    ps = [new_payment(ada, "bob", 10) for _ in range(34)]
    eff = ps[0]["created_at"]
    one = [item(ps[0]["payment_id"], 1, 5, ps[0]["created_at"])]
    before = observe_state(w, [(ada, ps[0]["payment_id"])])
    expect(http("POST", "/correction-batches", body={"corrections": one}, key=fresh_key()), 401, "unauthenticated", "no token")
    expect(http("POST", "/correction-batches", body={"corrections": one}, key=fresh_key(), token="nope"), 401, "unauthenticated", "bad token")
    expect(ada.post("/correction-batches", {"corrections": one}, key=fresh_key()), 403, "forbidden", "the payment's own sender is not an operator")
    expect(bob.post("/correction-batches", {"corrections": one}, key=fresh_key()), 403, "forbidden", "a non-operator")
    expect(http("POST", "/correction-batches", body={"corrections": one}, token=op.token), 400, "missing_idempotency_key", "no key")
    expect(http("POST", "/correction-batches", body={"corrections": one}, token=op.token, key=""), 400, "missing_idempotency_key", "empty key")
    expect(http("POST", "/correction-batches", body={"corrections": one}, token=op.token, key="k" * 256), 422, "validation_failed", "256-character key")
    expect(op.post("/correction-batches", [], key=fresh_key()), 400, "malformed_request", "array body")
    expect(op.post("/correction-batches", raw='{"corrections": [', key=fresh_key()), 400, "malformed_request", "unparseable body")
    expect(op.post("/correction-batches", {}, key=fresh_key()), 422, "validation_failed", "no corrections")
    expect(op.post("/correction-batches", {"corrections": []}, key=fresh_key()), 422, "validation_failed", "0 corrections")
    fake33 = [item("p_zz%d" % i, 1, 1, eff) for i in range(33)]
    expect(op.post("/correction-batches", {"corrections": fake33}, key=fresh_key()), 422, "validation_failed", "33 corrections")
    expect(op.post("/correction-batches", {"corrections": [item(ps[0]["payment_id"], 1, 5, eff), item(ps[0]["payment_id"], 1, 4, eff)]}, key=fresh_key()),
           422, "validation_failed", "duplicate payment_ids")
    for bad in ("x", {}, 5, None, True):
        r = op.post("/correction-batches", {"corrections": bad}, key=fresh_key())
        ok(r.status in (400, 422) and r.code in ("malformed_request", "validation_failed"), "corrections = %r: got %r" % (bad, r))
    for bad in (5, "x", None, [], True):
        r = op.post("/correction-batches", {"corrections": [bad]}, key=fresh_key())
        ok(r.status in (400, 422) and r.code in ("malformed_request", "validation_failed"), "item = %r: got %r" % (bad, r))
    eq(observe_state(w, [(ada, ps[0]["payment_id"])]), before, "rejections leave no trace")
    # 1 and 32 items are accepted
    k = fresh_key()
    r = expect(http("POST", "/correction-batches", body={"corrections": one}, token=op.token, key="k" * 255), 201, msg="255-character key, 1 item")
    check_batch(r.json, 1)
    thirty2 = [item(p["payment_id"], 1, 5, p["created_at"], "bulk %d" % i) for i, p in enumerate(ps[1:33])]
    r = expect(op.post("/correction-batches", {"corrections": thirty2}, key=fresh_key()), 201, msg="32 items")
    check_batch(r.json, 32)
    eq([x["payment_id"] for x in r.json["revisions"]], [p["payment_id"] for p in ps[1:33]], "input order for 32 items")
    eq(lib2.me_ok(ada)["balance"], 10000 - 5 * 33 - 10 * 1, "ada: 33 payments reduced from 10 to 5, one untouched")
    expect(op.post("/correction-batches", {"corrections": [item(ps[33]["payment_id"], 1, 5, ps[33]["created_at"])] + [item(p["payment_id"], 2, 5, p["created_at"]) for p in ps[1:32]]},
                   key=fresh_key()), 201, msg="32 distinct payments again, mixed revisions")


@test("S4-023", "S4-024", "S4-025", "S4-030", "S4-031", "S4-033", "S4-046", "S4-019", "S4-018", "S4-037")
def test_batch_item_errors_in_input_order_and_nothing_changes():
    w = world_b({"bob": 5000})
    ada, bob, cy, op = w["ada"], w["bob"], w["cy"], w["op"]
    a, b, c = (new_payment(ada, "bob", 100) for _ in range(3))
    rf = expect(bob.post("/payments/%s/refunds" % c["payment_id"], {"amount": 50}, key=fresh_key()), 201).json         # refund payment
    au = expect(lib2.authorize(ada, "bob", 500), 201).json
    cap = expect(lib2.capture(bob, au["authorization_id"], {"amount": 300}), 201).json                                   # capture payment
    pids = [a["payment_id"], b["payment_id"], c["payment_id"]]
    watch = [(ada, x) for x in pids] + [(bob, rf["payment_id"]), (ada, cap["payment_id"])]

    def good(p, rev=1, amount=60):
        return item(p["payment_id"], rev, amount, p["created_at"])

    def fails(items, status, code, why):
        before = observe_state(w, watch)
        k = fresh_key()
        expect(op.post("/correction-batches", {"corrections": items}, key=k), status, code, why)
        eq(observe_state(w, watch), before, why + ": a rejected batch leaves history, balances and statements unchanged")
        return k

    now = utcnow()
    base = good(a)
    for field in ("payment_id", "expected_revision", "amount", "effective_at", "reason"):
        bad = dict(base)
        del bad[field]
        fails([good(b), bad], 422, "validation_failed", "item without %s" % field)
    for field, values in (("expected_revision", [0, -1, 1.5]), ("amount", [-1, 1_000_000_001, 1.5, "60", True, None, []]),
                          ("reason", ["", "x" * 201]), ("effective_at", [fmts(now + timedelta(hours=1)), "", "yesterday", "2026-09-24T13:20:00"])):
        for v in values:
            bad = dict(base)
            bad[field] = v
            fails([good(b), bad], 422, "validation_failed", "item %s = %r" % (field, v))
    for field, values in (("payment_id", [5, None, []]), ("expected_revision", ["1", True, None]), ("reason", [5, None])):
        for v in values:
            bad = dict(base)
            bad[field] = v
            r = op.post("/correction-batches", {"corrections": [good(b), bad]}, key=fresh_key())
            ok(r.status in (400, 422) and r.code in ("malformed_request", "validation_failed"), "item %s = %r: got %r" % (field, v, r))
    fails([good(b), item("p_unknown", 1, 1, a["created_at"])], 404, "not_found", "unknown payment")
    fails([good(b), good(a, rev=2)], 409, "stale_revision", "stale expected revision")
    fails([good(b), item(cap["payment_id"], 1, 10, cap["created_at"])], 422, "linked_payment_immutable", "a capture is immutable")
    fails([good(b), item(rf["payment_id"], 1, 10, rf["created_at"])], 422, "linked_payment_immutable", "a refund payment is immutable")
    fails([good(b), item(c["payment_id"], 1, 49, c["created_at"])], 422, "refund_exceeds_payment", "below the refunded 50")
    # item errors are reported in input order, whatever their kind
    bad_amount = dict(good(b))
    bad_amount["amount"] = -1
    fails([good(a, rev=2), bad_amount], 409, "stale_revision", "item 1 stale, item 2 invalid -> item 1's error")
    fails([bad_amount, good(a, rev=2)], 422, "validation_failed", "item 1 invalid, item 2 stale -> item 1's error")
    fails([item("p_unknown", 1, 1, a["created_at"]), item(cap["payment_id"], 1, 10, cap["created_at"])], 404, "not_found", "unknown before linked")
    fails([item(cap["payment_id"], 1, 10, cap["created_at"]), item("p_unknown", 1, 1, a["created_at"])], 422, "linked_payment_immutable", "linked before unknown")
    fails([item(c["payment_id"], 1, 49, c["created_at"]), good(a, rev=2)], 422, "refund_exceeds_payment", "refund cap before stale")
    # the same key can be used for a valid batch afterwards (key of a failed batch is a first use)
    k = fails([good(a), good(b, rev=2)], 409, "stale_revision", "one stale item")
    r = expect(op.post("/correction-batches", {"corrections": [good(a), good(b)]}, key=k), 201, msg="the key of the rejected batch is a first use")
    check_batch(r.json, 2)
    # a capture / refund / settlement-free ordinary payment keep their single-correction rules
    expect(correct(ada, cap["payment_id"], 1, 10, cap["created_at"]), 422, "linked_payment_immutable", "single correction of a capture")
    expect(correct(bob, rf["payment_id"], 1, 10, rf["created_at"]), 422, "linked_payment_immutable", "single correction of a refund")
    eq(sum(lib2.me_ok(u)["balance"] for u in w.values()), 10000 + 5000 + 1000, "conservation")


@test("S4-026", "S4-027", "S4-028", "S4-043", "S4-039", "S4-038", "S4-030", "S4-019", "S4-036", "S4-035", "S4-004")
def test_batch_settlements_need_every_member_at_one_instant():
    w = world_b({"dan": 15})
    ada, bob, cy, dan, op = w["ada"], w["bob"], w["cy"], w["dan"], w["op"]
    body = {"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 100, "visibility": "private", "note": "x"},
                          {"from_handle": "bob", "to_handle": "cy", "amount": 40},
                          {"from_handle": "cy", "to_handle": "dan", "amount": 10},
                          {"from_handle": "ada", "to_handle": "dan", "amount": 5}]}
    skey = fresh_key()
    st = expect(op.post("/settlements", body, key=skey), 201).json
    m = st["payments"]
    mp = [x["payment_id"] for x in m]
    body2 = {"transfers": [{"from_handle": "ada", "to_handle": "cy", "amount": 20}, {"from_handle": "cy", "to_handle": "bob", "amount": 5}]}
    st2 = expect(op.post("/settlements", body2, key=fresh_key()), 201).json
    m2 = [x["payment_id"] for x in st2["payments"]]
    n1 = new_payment(ada, "bob", 30)
    C = inst(st["committed_at"])
    E = whole(C) - timedelta(hours=1)
    watch = [(ada, mp[0]), (bob, mp[1]), (cy, mp[2]), (ada, mp[3]), (ada, m2[0]), (ada, n1["payment_id"])]

    def items_for(amounts, eff_fn=lambda i: fmts(E)):
        return [item(mp[i], 1, amounts[i], eff_fn(i), "settlement fix") for i in range(4)]

    def fails(items, status, code, why):
        before = observe_state(w, watch)
        k = fresh_key()
        expect(op.post("/correction-batches", {"corrections": items}, key=k), status, code, why)
        eq(observe_state(w, watch), before, why + ": no trace")
        return k

    # single corrections of members stay refused; the non-member can be corrected singly
    expect(correct(ada, mp[0], 1, 90, st["payments"][0]["created_at"]), 422, "linked_payment_immutable", "stage-3 rule for members")
    # incomplete batches
    fails(items_for([80, 20, 0, 5])[:1], 422, "incomplete_settlement", "only one member")
    fails(items_for([80, 20, 0, 5])[:3], 422, "incomplete_settlement", "three of four members")
    fails(items_for([80, 20, 0, 5]) + [item(m2[0], 1, 10, fmts(E))], 422, "incomplete_settlement", "all of one settlement but one member of another")
    fails(items_for([80, 20, 0, 5])[::-1][:3], 422, "incomplete_settlement", "three members in another order")
    # identical effective instants
    fails(items_for([80, 20, 0, 5], lambda i: fmts(E + timedelta(seconds=1 if i == 3 else 0))), 422, "validation_failed", "one member one second apart")
    # precedence: item errors beat settlement completeness
    fails(items_for([80, 20, 0, 5])[:1] + [item(n1["payment_id"], 2, 10, n1["created_at"])], 409, "stale_revision", "a stale item is reported before incompleteness")
    # complete, with the same instant in different spellings
    plus9, minus5 = timezone(timedelta(hours=9)), timezone(timedelta(hours=-5))
    spell = [E.astimezone(UTC).isoformat(timespec="seconds"), E.astimezone(plus9).isoformat(timespec="seconds"),
             E.astimezone(minus5).isoformat(timespec="microseconds"), E.astimezone(timezone(timedelta(hours=2))).isoformat(timespec="seconds")]
    start = {h: lib2.me_ok(u)["balance"] for h, u in w.items()}
    key = fresh_key()
    items = [item(mp[i], 1, a_, spell[i], "settlement fix") for i, a_ in enumerate([80, 20, 0, 5])] + [item(n1["payment_id"], 1, 25, n1["created_at"], "non-member in the same batch")]
    r = expect(op.post("/correction-batches", {"corrections": items}, key=key), 201, msg="every member, one instant in four spellings, plus a non-member")
    j = r.json
    check_batch(j, 5)
    for x, i in zip(j["revisions"][:4], range(4)):
        same_instant(x["effective_at"], E, "member effective_at")
        eq((x["payment_id"], x["revision"]), (mp[i], 2), "member revision 2")
    now_ = {h: lib2.me_ok(u)["balance"] for h, u in w.items()}
    # ada: p1 100->80 (+20), p4 5->5, n1 30->25 (+5); bob: -20 +20(40->20 means bob pays less to cy) -5 ; cy: -20 (receives less) +10 (pays less) ; dan: -10
    eq({h: now_[h] - start[h] for h in ("ada", "bob", "cy", "dan")}, {"ada": 25, "bob": -20 + 20 - 5, "cy": -20 + 10, "dan": -10}, "differences between the members' wallets")
    eq(sum(now_.values()), sum(start.values()), "conservation")
    for i in range(4):
        rv = revisions(ada if i in (0, 3) else (bob if i == 1 else cy), mp[i])
        eq((rv[1]["amount"], rv[1].get("correction_batch_id")), ([80, 20, 0, 5][i], j["correction_batch_id"]), "history of member %d" % i)
        same_instant(rv[1]["recorded_at"], j["recorded_at"], "member %d recorded_at" % i)
        same_instant(rv[1]["effective_at"], E, "member %d effective_at" % i)
        same_instant(rv[0]["effective_at"], st["committed_at"], "revision 1 of a member: effective_at = committed_at")
    # original receipts
    rp = expect(op.post("/settlements", body, key=skey), 200)
    eq(rp.json, st, "the settlement receipt replays unchanged (original amounts, committed_at, member list)")
    for x in rp.json["payments"]:
        check_payment4(x, refund_of=None)
    eq(expect(op.post("/correction-batches", {"corrections": items}, key=key), 200).json, j, "batch replay")
    feeds = {x["payment_id"]: x for x in feed4(ada)}
    eq(feeds[mp[0]]["amount"], 100, "activity shows the original member payment")
    ok(mp[0] not in [x["payment_id"] for x in op.all_payments()], "the private member stays hidden from the operator's feed")
    # statements reflect the revisions
    es = {e["payment"]["payment_id"]: e for e in full_stmt(ada)["entries"]}
    eq((es[mp[0]]["revision"], es[mp[0]]["payment"]["amount"], es[mp[0]]["delta"]), (2, 80, -80), "ada's entry for member 1")
    same_instant(es[mp[0]]["effective_at"], E, "entry effective_at")
    eq(full_stmt(op)["entries"], [], "the operator is no party: empty statement")
    # members stay immutable for single corrections after the batch; non-members are still correctable
    expect(correct(ada, mp[0], 2, 70, fmts(E)), 422, "linked_payment_immutable", "still a member")
    expect(correct(ada, n1["payment_id"], 2, 20, n1["created_at"]), 201, msg="a nonmember single correction")
    # refunds on a settlement payment never change membership; reducing below the refunded amount is refused
    rfd = expect(bob.post("/payments/%s/refunds" % mp[0], {"amount": 30}, key=fresh_key()), 201).json
    eq((rfd["refund_of"], rfd["settlement_id"]), (mp[0], None), "refund of a member")
    items3 = [item(mp[i], 2, a_, fmts(E), "again") for i, a_ in enumerate([25, 20, 0, 5])]
    fails(items3, 422, "refund_exceeds_payment", "member 1 reduced to 25 < 30 refunded")
    fails(items3[:1], 422, "refund_exceeds_payment", "item errors (refund cap) are reported before incompleteness")
    items3[0] = item(mp[0], 2, 30, fmts(E), "again")
    r3 = expect(op.post("/correction-batches", {"corrections": items3}, key=fresh_key()), 201, msg="membership is unchanged by the refund: the original four members suffice")
    check_batch(r3.json, 4)
    eq([x["revision"] for x in r3.json["revisions"]], [3, 3, 3, 3], "revision 3")
    eq(expect(op.post("/settlements", body, key=skey), 200).json, st, "settlement receipt still unchanged")


@test("S4-030", "S4-031", "S4-032", "S4-033", "S4-025")
def test_batch_error_precedence_and_combined_affordability():
    # incomplete_settlement before insufficient_funds
    w = lib2.world2({"ada": 1000, "bob": 0, "cy": 0, "dan": 0, "op": 0}, operators=["op"])
    ada, bob, cy, dan, op = (w[h] for h in ("ada", "bob", "cy", "dan", "op"))
    st = expect(op.post("/settlements", {"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 100}, {"from_handle": "ada", "to_handle": "cy", "amount": 50}]}, key=fresh_key()), 201).json
    mp = [x["payment_id"] for x in st["payments"]]
    expect(bob.pay("dan", 100), 201)                                              # bob has nothing left
    watch = [(ada, mp[0]), (ada, mp[1])]
    before = observe_state(w, watch)
    expect(op.post("/correction-batches", {"corrections": [item(mp[0], 1, 0, st["committed_at"])]}, key=fresh_key()), 422, "incomplete_settlement",
           "the member would also be unaffordable (bob is empty), but completeness is checked first")
    eq(observe_state(w, watch), before, "no trace")
    expect(op.post("/correction-batches", {"corrections": [item(mp[0], 1, 0, st["committed_at"]), item(mp[1], 1, 0, st["committed_at"])]}, key=fresh_key()), 409, "insufficient_funds",
           "complete, now the current funds decide: bob cannot give back 100")
    eq(observe_state(w, watch), before, "no trace")
    # insufficient_funds before historical_overdraft
    t = whole(utcnow()) - timedelta(days=5)
    t1, t2, t3 = t + timedelta(hours=1), t + timedelta(hours=2), t + timedelta(hours=3)
    opening = {"ada": 100, "bob": 0, "cy": 0, "dan": 100, "op": 0}
    pays = [fx_pay("p_001", "ada", "bob", 100, created_at=t1), fx_pay("p_002", "bob", "cy", 100, created_at=t2), fx_pay("p_003", "dan", "bob", 100, created_at=t3)]
    w = hist_world(opening, pays, operators=["op"])
    ada, bob, cy, dan, op = (w[h] for h in ("ada", "bob", "cy", "dan", "op"))
    watch = [(ada, "p_001"), (dan, "p_003")]
    before = observe_state(w, watch)
    expect(op.post("/correction-batches", {"corrections": [item("p_001", 1, 0, fmts(t1))]}, key=fresh_key()), 409, "historical_overdraft",
           "currently affordable (bob holds 100) but bob would be at -100 between t2 and t3")
    eq(observe_state(w, watch), before, "no trace")
    expect(op.post("/correction-batches", {"corrections": [item("p_001", 1, 0, fmts(t1)), item("p_003", 1, 0, fmts(t3))]}, key=fresh_key()), 409, "insufficient_funds",
           "both reversals: bob would end at -100 now AND be negative in history: the current check wins")
    eq(observe_state(w, watch), before, "no trace")
    # the combined effect decides: moving dan's payment earlier makes the reversal sound, in ONE batch
    r = expect(op.post("/correction-batches", {"corrections": [item("p_001", 1, 0, fmts(t1)), item("p_003", 1, 100, fmts(t1))]}, key=fresh_key()), 201,
               msg="historically bob stays nonnegative only with both revisions together")
    check_batch(r.json, 2)
    eq(me_at(bob, as_of=fmts(t2))["balance"], 0, "bob at t2 under the new revisions")
    eq(lib2.me_ok(bob)["balance"], 0, "bob now")
    # current funds netting: reverse a payment and the payment its receiver made out of it, together
    t = whole(utcnow()) - timedelta(days=5)
    opening = {"ada": 1000, "bob": 0, "cy": 1000, "op": 0}
    pays = [fx_pay("p_a", "ada", "bob", 100, created_at=t + timedelta(hours=1)), fx_pay("p_b", "bob", "cy", 100, created_at=t + timedelta(hours=2))]
    w = hist_world(opening, pays, operators=["op"])
    ada, bob, cy, op = w["ada"], w["bob"], w["cy"], w["op"]
    eq(lib2.me_ok(bob)["balance"], 0, "bob is empty")
    watch = [(ada, "p_a"), (bob, "p_b")]
    before = observe_state(w, watch)
    expect(op.post("/correction-batches", {"corrections": [item("p_a", 1, 0, fmts(t + timedelta(hours=1)))]}, key=fresh_key()), 409, "insufficient_funds", "alone, p_a's reversal is unaffordable")
    expect(op.post("/correction-batches", {"corrections": [item("p_a", 1, 0, fmts(t + timedelta(hours=1))), item("p_b", 1, 50, fmts(t + timedelta(hours=2)))]}, key=fresh_key()),
           409, "insufficient_funds", "net -100 + 50 = -50 for bob")
    eq(observe_state(w, watch), before, "no trace")
    r = expect(op.post("/correction-batches", {"corrections": [item("p_a", 1, 0, fmts(t + timedelta(hours=1))), item("p_b", 1, 0, fmts(t + timedelta(hours=2)))]}, key=fresh_key()), 201,
               msg="net effect for bob is -100 + 100 = 0 now and 0 at every boundary")
    eq((lib2.me_ok(ada)["balance"], lib2.me_ok(bob)["balance"], lib2.me_ok(cy)["balance"]), (1000, 0, 1000), "both payments reversed")
    # the debit of a batch is checked against AVAILABLE funds, not total
    w = lib2.world2({"ada": 1000, "bob": 0, "cy": 0, "op": 0}, operators=["op"])
    ada, bob, cy, op = w["ada"], w["bob"], w["cy"], w["op"]
    p = new_payment(ada, "bob", 500)
    expect(lib2.authorize(bob, "cy", 450), 201)
    before = observe_state(w, [(ada, p["payment_id"])])
    expect(op.post("/correction-batches", {"corrections": [item(p["payment_id"], 1, 400, p["created_at"])]}, key=fresh_key()), 409, "insufficient_funds",
           "bob's total is 500 but only 50 is available")
    eq(observe_state(w, [(ada, p["payment_id"])]), before, "no trace")
    expect(op.post("/correction-batches", {"corrections": [item(p["payment_id"], 1, 450, p["created_at"])]}, key=fresh_key()), 201, msg="exactly the available 50")


@test("S4-035", "S4-036", "S4-040", "S4-041", "S4-038", "S4-004", "S4-050")
def test_batch_revisions_in_historical_views_and_old_snapshots():
    base = whole(utcnow()) - timedelta(days=8)
    h = lambda x: base + timedelta(hours=x)
    opening = {"ada": 1000, "bob": 500, "cy": 300, "dan": 200, "op": 0}
    pays = [fx_pay("p_001", "ada", "bob", 200, created_at=h(1)), fx_pay("p_002", "bob", "cy", 150, created_at=h(2)),
            fx_pay("p_003", "cy", "dan", 100, created_at=h(2)), fx_pay("p_004", "dan", "ada", 50, created_at=h(3)),
            fx_pay("p_005", "bob", "ada", 80, created_at=h(4), visibility="private")]
    w = hist_world(opening, pays, operators=["op"])
    ada, bob, cy, dan, op = (w[x] for x in ("ada", "bob", "cy", "dan", "op"))
    total = sum(opening.values())
    snaps = {x: stmt(w[x], limit=2) for x in ("ada", "bob", "cy", "dan")}
    frozen = {x: pages_all(w[x], snaps[x]["snapshot"]) for x in snaps}
    sleep_gap(1.1)
    pre = expect(correct(bob, "p_002", 1, 140, fmts(h(2)), "single earlier"), 201).json
    sleep_gap(1.1)
    items = [item("p_001", 1, 150, fmts(h(1.5)), "later and smaller"), item("p_002", 2, 0, fmts(h(2)), "reverse"), item("p_003", 1, 130, fmts(h(1)), "earlier and bigger"),
             item("p_004", 1, 70, fmts(h(3.5)), "later and bigger")]
    r = expect(op.post("/correction-batches", {"corrections": items}, key=fresh_key()), 201)
    j = check_batch(r.json, 4)
    rec = inst(j["recorded_at"])
    ok(rec > inst(pre["recorded_at"]), "strictly later than the previous recorded_at of member p_002")
    owners = {"p_001": ada, "p_002": bob, "p_003": cy, "p_004": dan, "p_005": bob}
    parties = {"p_001": ("u_ada", "u_bob"), "p_002": ("u_bob", "u_cy"), "p_003": ("u_cy", "u_dan"), "p_004": ("u_dan", "u_ada"), "p_005": ("u_bob", "u_ada")}
    model = model_from_service({uid(k): v for k, v in opening.items()}, parties, owners)
    sec = timedelta(seconds=1)
    for k_ in (None, rec - sec, rec, rec + sec, inst(pre["recorded_at"]), inst(pre["recorded_at"]) - sec, h(0)):
        for as_of in (None, h(0), h(1), h(1.5), h(2), h(3), h(3.5), h(5)):
            tot = 0
            for x, u in w.items():
                got = me_at(u, as_of=None if as_of is None else fmt(as_of), known_at=None if k_ is None else fmt(k_))["balance"]
                eq(got, model.balance(uid(x), as_of, k_), "%s as of %s known_at %s" % (x, as_of, k_))
                tot += got
            eq(tot, total, "sum of balances (as_of %s known_at %s)" % (as_of, k_))
    # the batch becomes known atomically at its single recorded_at
    before_rec = full_stmt(ada, known_at=fmt(rec - sec))["entries"]
    after_rec = full_stmt(ada, known_at=fmt(rec))["entries"]
    ok([e["revision"] for e in before_rec] != [e["revision"] for e in after_rec], "known_at = recorded_at includes the batch, one second earlier does not")
    for x, u in w.items():
        for k_ in (None, rec - sec, rec):
            q = {} if k_ is None else {"known_at": fmt(k_)}
            got = full_stmt(u, **q)
            want = model.statement(uid(x), None, None, k_)
            eq((got["opening"], got["closing"]), (want["opening"], want["closing"]), "statement balances %s %s" % (x, k_))
            compare_entries(got["entries"], want["entries"], "statement of %s known_at %s" % (x, k_))
    # saved statements keep their original form
    for x in snaps:
        eq(pages_all(w[x], snaps[x]["snapshot"]), frozen[x], "snapshot of %s unchanged after the batch" % x)
    # revision histories carry the batch id on the batch revision only
    for pid in ("p_001", "p_002", "p_003", "p_004"):
        rv = revisions(owners[pid], pid)
        eq(rv[-1].get("correction_batch_id"), j["correction_batch_id"], "last revision of %s" % pid)
        same_instant(rv[-1]["recorded_at"], j["recorded_at"], "recorded_at of %s" % pid)
        for older in rv[:-1]:
            ok(older.get("correction_batch_id") in (None,), "older revisions have no batch id")
    eq(revisions(bob, "p_005"), [revisions(bob, "p_005")[0]], "the untouched payment has revision 1 only")
    # opening balances unchanged
    for x, v in opening.items():
        eq(me_at(w[x], as_of="1970-01-01T00:00:00+00:00")["balance"], v, "opening balance of %s" % x)


def pages_all(u, token):
    out, off = [], 0
    while True:
        j = stmt(u, snapshot=token, limit=3, offset=off)
        out += j["entries"]
        if not j["has_more"]:
            return {"entries": out, "opening": j["opening_balance"], "closing": j["closing_balance"]}
        off += 3


@test("S4-005", "S4-042", "S4-041", "S4-046", "S4-047", "S4-048", "S4-044")
def test_batch_idempotency_matrix():
    w = world_b({"ada": 100000}, ops=("op", "op2", "ada"))
    ada, bob, op, op2 = w["ada"], w["bob"], w["op"], w["op2"]
    ps = [new_payment(ada, "bob", 100) for _ in range(6)]
    it = lambda i, rev=1, amount=50: item(ps[i]["payment_id"], rev, amount, ps[i]["created_at"], "idem %d" % i)
    k = fresh_key()
    body = {"corrections": [it(0), it(1)]}
    r = expect(op.post("/correction-batches", body, key=k), 201)
    eq(expect(op.post("/correction-batches", dict(reversed(list(body.items()))), key=k), 200).json, r.json, "replay (key order does not matter), identical body")
    expect(op.post("/correction-batches", {"corrections": [it(0), it(1, amount=49)]}, key=k), 409, "idempotency_key_reuse", "changed amount")
    expect(op.post("/correction-batches", {"corrections": [it(1), it(0)]}, key=k), 409, "idempotency_key_reuse", "reordered items are a different body")
    expect(op.post("/correction-batches", {"corrections": [{"payment_id": "x"}]}, key=k), 409, "idempotency_key_reuse", "an invalid body with a claimed key")
    # replay after the members changed again
    expect(op.post("/correction-batches", {"corrections": [it(0, rev=2, amount=30)]}, key=fresh_key()), 201)
    eq(expect(op.post("/correction-batches", body, key=k), 200).json, r.json, "replay after newer revisions: still the original response")
    eq(rev_count(ada, ps[0]["payment_id"]), 3, "no extra revisions from replays")
    # the key is scoped to the user: another operator may use the same string for a different batch
    expect(op2.post("/correction-batches", {"corrections": [it(2)]}, key=k), 201)
    # the same key on another path is a different request
    expect(ada.post("/payments/%s/corrections" % ps[3]["payment_id"], corr_body(1, 60, ps[3]["created_at"], "single"), key=k), 201,
           msg="same key on the single-correction path (ada is an operator too)")
    # failed batches do not claim keys
    kf = fresh_key()
    expect(op.post("/correction-batches", {"corrections": [it(4, rev=3)]}, key=kf), 409, "stale_revision")
    expect(op.post("/correction-batches", {"corrections": [it(4, amount=-1)]}, key=kf), 422, "validation_failed")
    expect(op.post("/correction-batches", {"corrections": [item("p_unknown", 1, 1, ps[4]["created_at"])]}, key=kf), 404, "not_found")
    expect(op.post("/correction-batches", {"corrections": [it(4)]}, key=kf), 201, msg="after 409, 422 and 404 the key is still a first use")
    expect(bob.post("/correction-batches", {"corrections": [it(5)]}, key=fresh_key()), 403, "forbidden")
    # lengths
    expect(http("POST", "/correction-batches", body={"corrections": [it(5)]}, token=op.token, key="k" * 255), 201, msg="255 characters")
    expect(http("POST", "/correction-batches", body={"corrections": [it(5, rev=2)]}, token=op.token, key="k" * 256), 422, "validation_failed", "256 characters")
    # concurrent identical requests
    ps2 = [new_payment(ada, "bob", 100) for _ in range(3)]
    kc = fresh_key()
    cb = {"corrections": [item(p["payment_id"], 1, 10, p["created_at"], "burst") for p in ps2]}
    res = burst([(lambda: op.post("/correction-batches", cb, key=kc)) for _ in range(15)])
    eq(sorted(x.status for x in res), [200] * 14 + [201], "one 201, the others replay")
    eq(len({json_dump(x.json) for x in res}), 1, "identical bodies")
    for p in ps2:
        eq(rev_count(ada, p["payment_id"]), 2, "one revision per payment")


def json_dump(o):
    import json
    return json.dumps(o, sort_keys=True)
