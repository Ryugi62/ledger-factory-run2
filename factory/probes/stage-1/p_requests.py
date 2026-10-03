"""Payment requests: create, pay, decline, cancel, list, and races between them."""
import json

from lib import *  # noqa: F401,F403


def stranger_ok(resp):
    """A caller who is neither party: 403 (endpoint table) or 404 (not visible to this caller)."""
    ok(resp.status in (403, 404) and resp.code in ("forbidden", "not_found"),
       "stranger must get 403 forbidden or 404 not_found, got %s %s" % (resp.status, resp.text[:200]))


@test("S1-119", "S1-124", "S1-047", "S1-045", "S1-001", "S1-051", "S1-054")
def test_request_create_shape_and_no_side_effects():
    w = world({"ada": 100, "bob": 2500, "cy": 0})
    ada, bob, cy = w["ada"], w["bob"], w["cy"]
    r = expect(bob.request("ada", 1200, note="taxi"), 201)
    q = check_request(r.json, requester=bob, payer=ada, amount=1200, status="pending", note="taxi",
                      payment_id=None)
    eq(r.json["currency"], "EUR", "currency")
    eq((ada.balance, bob.balance), (100, 2500), "creating a request moves nothing")
    for u in (ada, bob, cy):
        eq(u.all_payments(), [], "a request is never a feed item (%s)" % u.handle)
    # default note
    q2 = expect(bob.post("/requests", {"payer_handle": "ada", "amount": 1}, key=fresh_key()), 201).json
    eq(q2["note"], "", "default note is empty")
    # the payer is asked for far more than they hold: legal
    big = expect(bob.request("ada", MAX_AMOUNT), 201).json
    eq((big["status"], big["amount"]), ("pending", MAX_AMOUNT), "oversized request stays pending")
    eq(ada.balance, 100, "balance untouched")
    # zero balance payer
    expect(bob.request("cy", 1_000_000), 201, msg="payer with 0 balance")
    # visible to both parties, to nobody else
    mine = {x["request_id"] for x in bob.all_requests()}
    theirs = {x["request_id"] for x in ada.all_requests()}
    ok(q["request_id"] in mine and q["request_id"] in theirs, "both parties see the request")
    eq(cy.all_requests() and {x["payer_handle"] for x in cy.all_requests()}, {"cy"}, "cy only sees requests addressed to cy")
    eq(len({q["request_id"], q2["request_id"], big["request_id"]}), 3, "request ids unique")


@test("S1-120", "S1-121", "S1-122", "S1-123", "S1-074", "S1-068", "S1-076", "S1-075", "S1-077", "S1-050")
def test_request_validation():
    w = world({"ada": 100, "bob": 0})
    ada, bob = w["ada"], w["bob"]

    def bad(body, status=422, code="validation_failed", why=""):
        expect(bob.post("/requests", body, key=fresh_key()), status, code, why or repr(body))
        eq(bob.all_requests(), [], "a rejected request leaves nothing behind: %r" % (body,))

    for amt in (0, -1, 1_000_000_001, 1.5, "10", True, False, None, [], {}):
        bad({"payer_handle": "ada", "amount": amt}, why="amount %r" % (amt,))
    bad({"payer_handle": "ada"}, why="missing amount")
    bad({"amount": 5}, why="missing payer_handle")
    for note in (None, 5, True, [], {}):
        bad({"payer_handle": "ada", "amount": 1, "note": note}, why="note %r" % (note,))
    bad({"payer_handle": "ada", "amount": 1, "note": "x" * 201}, why="201-char note")
    for h in (5, ["ada"], True):
        bad({"payer_handle": h, "amount": 1}, 400, "malformed_request", "payer_handle %r" % (h,))
    bad({"payer_handle": "nobody", "amount": 1}, 404, "not_found")
    bad({"payer_handle": "bob", "amount": 1}, 422, "self_request")
    bad({"payer_handle": "bob", "amount": 10**9}, 422, "self_request", "self request, large")
    for raw in ("{", "", "[]", "7"):
        expect(http("POST", "/requests", raw=raw, token=bob.token, key=fresh_key()), 400, "malformed_request", repr(raw))
    expect(bob.request("ada", 1, note="x" * 200), 201, msg="200-char note")
    expect(bob.request("ada", 1, note="\U0001F600" * 200), 201, msg="200 emoji note")
    # a request has no visibility: the field is an unknown field and is ignored, whatever its value
    for vis in ("private", "public", "bogus", None, 5):
        r = expect(bob.request("ada", 1, visibility=vis), 201, msg="request with visibility %r" % (vis,))
        ok("visibility" not in r.json, "request must not expose a visibility: %r" % r.json)


@test("S1-128", "S1-129", "S1-130", "S1-131", "S1-132", "S1-048", "S1-049", "S1-125", "S1-103",
      "S1-133", "S1-126", "S1-007", "S1-045", "S1-072", "S1-101", "S1-100")
def test_pay_request_lifecycle():
    w = world({"ada": 1000, "bob": 50, "cy": 0, "rich": 100000})
    ada, bob, cy, rich = w["ada"], w["bob"], w["cy"], w["rich"]
    q = expect(bob.request("ada", 1200, note="taxi"), 201).json
    rid = q["request_id"]
    k = fresh_key()
    # short: 409, changes nothing
    expect(ada.pay_request(rid, key=k), 409, "insufficient_funds")
    eq((ada.balance, bob.balance), (1000, 50), "short pay moves nothing")
    check_request(bob.all_requests()[0], status="pending", payment_id=None)
    eq(ada.all_payments(), [], "no payment after a failed pay")
    eq(bob.all_payments(), [], "no payment after a failed pay")
    # money arrives later: the same request becomes payable, with the very same key
    expect(rich.pay("ada", 200), 201)
    r = expect(ada.pay_request(rid, key=k), 201, msg="same key after a 4xx failure is a first use")
    pay = check_payment(r.json, frm=ada, to=bob, amount=1200, request_id=rid, visibility="public", settlement=None)
    eq((ada.balance, bob.balance), (0, 1250), "exactly the request amount moved")
    mine = [x for x in bob.all_requests() if x["request_id"] == rid][0]
    check_request(mine, status="paid", payment_id=pay["payment_id"])
    eq([x for x in ada.all_requests() if x["request_id"] == rid][0], mine, "both parties see the same paid request")
    ok(pay["payment_id"] in {p["payment_id"] for p in bob.all_payments()}, "payment is in the feed")
    # a second pay with a different key: not pending
    expect(ada.pay_request(rid), 409, "request_not_pending")
    eq((ada.balance, bob.balance), (0, 1250), "no second movement")
    # replay of the successful pay: 200 with the original body, even though the request is paid now
    r2 = expect(ada.pay_request(rid, key=k), 200, msg="replay of successful pay")
    eq(r2.json, r.json, "replay body identical")
    eq((ada.balance, bob.balance), (0, 1250), "replay moves nothing")
    # roles
    q2 = expect(bob.request("ada", 5), 201).json["request_id"]
    expect(bob.pay_request(q2), 403, "forbidden", "the requester may not pay")
    stranger_ok(cy.pay_request(q2))
    expect(ada.pay_request("rq_does_not_exist"), 404, "not_found")
    expect(ada.pay_request("x" * 70), 404, "not_found")
    check_request([x for x in bob.all_requests() if x["request_id"] == q2][0], status="pending")
    eq((ada.balance, bob.balance), (0, 1250), "nothing moved by the failed attempts")


@test("S1-130", "S1-111", "S1-006")
def test_pay_request_exact_balance_boundary():
    w = world({"ada": 1200, "bob": 0})
    ada, bob = w["ada"], w["bob"]
    rid = expect(bob.request("ada", 1200), 201).json["request_id"]
    r2 = expect(bob.request("ada", 1), 201).json["request_id"]
    expect(ada.pay_request(rid), 201, msg="balance == amount is payable")
    eq((ada.balance, bob.balance), (0, 1200), "drained exactly")
    expect(ada.pay_request(r2), 409, "insufficient_funds")
    eq(ada.balance, 0, "never negative")


@test("S1-126", "S1-050", "S1-051", "S1-052", "S1-076", "S1-077", "S1-032", "S1-127")
def test_payer_chooses_visibility():
    w = world({"ada": 1000, "bob": 0, "cy": 0})
    ada, bob, cy = w["ada"], w["bob"], w["cy"]
    # requester tries to dictate visibility: ignored (unknown field on /requests)
    a = expect(bob.request("ada", 10, visibility="private"), 201).json["request_id"]
    b = expect(bob.request("ada", 11, visibility="public"), 201).json["request_id"]
    c = expect(bob.request("ada", 12), 201).json["request_id"]
    d = expect(bob.request("ada", 13), 201).json["request_id"]
    pa = expect(ada.pay_request(a, {}), 201).json
    pb = expect(ada.pay_request(b, {"visibility": "private"}), 201).json
    pc = expect(ada.pay_request(c, {"visibility": "public", "extra": 1}), 201).json
    eq(pa["visibility"], "public", "default visibility when the body omits it, whatever the request asked for")
    eq(pb["visibility"], "private", "payer's choice")
    eq(pc["visibility"], "public", "explicit public, unknown fields ignored")
    seen = {p["payment_id"] for p in cy.all_payments()}
    ok(pa["payment_id"] in seen and pc["payment_id"] in seen, "public ones are visible to a third party")
    ok(pb["payment_id"] not in seen, "private payment hidden from a third party")
    ok(pb["payment_id"] in {p["payment_id"] for p in bob.all_payments()}, "private payment visible to its receiver")
    ok(pb["payment_id"] in {p["payment_id"] for p in ada.all_payments()}, "private payment visible to its sender")
    for vis in ("PUBLIC", "friends", "", None, 5, [], {}):
        expect(ada.pay_request(d, {"visibility": vis}), 422, "validation_failed", "pay visibility %r" % (vis,))
    check_request([x for x in bob.all_requests() if x["request_id"] == d][0], status="pending")
    for raw in ("{", "[]", "7"):
        expect(http("POST", "/requests/%s/pay" % d, raw=raw, token=ada.token, key=fresh_key()), 400, "malformed_request", repr(raw))
    check_request([x for x in bob.all_requests() if x["request_id"] == d][0], status="pending")
    eq(ada.balance, 1000 - 10 - 11 - 12, "only three were paid")


@test("S1-127", "S1-102", "S1-104")
def test_pay_replay_requires_identical_body():
    w = world({"ada": 1000, "bob": 0})
    ada, bob = w["ada"], w["bob"]
    rid = expect(bob.request("ada", 10), 201).json["request_id"]
    k = fresh_key()
    r = expect(ada.pay_request(rid, {}, key=k), 201)
    expect(ada.pay_request(rid, {"visibility": "public"}, key=k), 409, "idempotency_key_reuse")
    expect(ada.pay_request(rid, {"visibility": "private"}, key=k), 409, "idempotency_key_reuse")
    eq(expect(ada.pay_request(rid, {}, key=k), 200).json, r.json, "identical body replays")
    eq(expect(http("POST", "/requests/%s/pay" % rid, raw=" {  } ", token=ada.token, key=k), 200).json, r.json,
       "whitespace does not matter")
    rid2 = expect(bob.request("ada", 20), 201).json["request_id"]
    k2 = fresh_key()
    r2 = expect(ada.pay_request(rid2, {"visibility": "public"}, key=k2), 201)
    expect(ada.pay_request(rid2, {}, key=k2), 409, "idempotency_key_reuse")
    eq(ada.balance, 970, "two payments only")


def _fresh_pair(a=1000, b=0):
    w = world({"ada": a, "bob": b, "cy": 0}, operators=["cy"])
    return w["ada"], w["bob"], w["cy"]


@test("S1-134", "S1-135", "S1-136", "S1-137", "S1-138", "S1-139", "S1-046", "S1-045", "S1-071", "S1-072", "S1-132")
def test_decline_and_cancel_state_machine():
    ada, bob, cy = _fresh_pair()
    fund = lambda: (ada.balance, bob.balance)

    def new(amount=10):
        return expect(bob.request("ada", amount), 201).json["request_id"]

    # --- decline
    r = new()
    d = expect(ada.post("/requests/%s/decline" % r), 200, msg="decline without any key or body").json
    check_request(d, requester=bob, payer=ada, status="declined", payment_id=None)
    eq(expect(ada.post("/requests/%s/decline" % r, {}), 200).json, d, "declining twice is not an error")
    eq(expect(ada.post("/requests/%s/decline" % r, {}, key=fresh_key()), 200).json["status"], "declined", "a key is tolerated")
    expect(bob.post("/requests/%s/cancel" % r), 409, "request_not_pending")
    expect(ada.pay_request(r), 409, "request_not_pending")
    expect(bob.post("/requests/%s/decline" % r), 403, "forbidden", "requester cannot decline")
    stranger_ok(cy.post("/requests/%s/decline" % r))
    expect(ada.post("/requests/nope/decline"), 404, "not_found")
    # --- cancel
    r = new()
    c = expect(bob.post("/requests/%s/cancel" % r), 200).json
    check_request(c, requester=bob, payer=ada, status="cancelled", payment_id=None)
    eq(expect(bob.post("/requests/%s/cancel" % r, {}), 200).json, c, "cancelling twice is 200")
    expect(ada.post("/requests/%s/decline" % r), 409, "request_not_pending")
    expect(ada.pay_request(r), 409, "request_not_pending")
    expect(ada.post("/requests/%s/cancel" % r), 403, "forbidden", "payer cannot cancel")
    stranger_ok(cy.post("/requests/%s/cancel" % r))
    expect(bob.post("/requests/nope/cancel"), 404, "not_found")
    # --- paid
    r = new()
    expect(ada.pay_request(r), 201)
    expect(ada.post("/requests/%s/decline" % r), 409, "request_not_pending")
    expect(bob.post("/requests/%s/cancel" % r), 409, "request_not_pending")
    expect(bob.post("/requests/%s/decline" % r), 403, "forbidden", "403 for the wrong party")
    # status stays terminal
    for q in ada.all_requests():
        ok(q["status"] in ("declined", "cancelled", "paid"), "terminal states stay terminal: %r" % q)
    eq(fund(), (990, 10), "only the paid request moved money")
    # one payment only
    eq(len(ada.all_payments()), 1, "exactly one payment exists")


@test("S1-140", "S1-141", "S1-142", "S1-143", "S1-146", "S1-054", "S1-188", "S1-045")
def test_request_listing_filters_order_and_privacy():
    w = world({"ada": 1000, "bob": 1000, "cy": 1000, "op": 0}, operators=["op"])
    ada, bob, cy, op = w["ada"], w["bob"], w["cy"], w["op"]
    made = []

    def mk(src, dst, amt):
        r = expect(src.request(dst.handle, amt), 201).json
        made.append((src.handle, dst.handle, r["request_id"]))
        return r["request_id"]

    a1 = mk(ada, bob, 1)
    a2 = mk(ada, bob, 2)
    a3 = mk(ada, bob, 3)
    b1 = mk(bob, ada, 4)
    b2 = mk(bob, ada, 5)
    c1 = mk(cy, ada, 6)
    c2 = mk(cy, bob, 7)
    expect(ada.post("/requests/%s/decline" % a1), 403, "forbidden", "requester cannot decline")
    expect(bob.post("/requests/%s/decline" % a2), 200)
    expect(bob.pay_request(a3), 201)
    expect(bob.post("/requests/%s/cancel" % b1), 200)
    both = lambda u, **q: [x["request_id"] for x in u.requests(limit=200, **q)["requests"]]
    # strict newest first
    eq(both(ada), [c1, b2, b1, a3, a2, a1], "ada: requester or payer, newest first (strict creation order)")
    eq(both(bob), [c2, b2, b1, a3, a2, a1], "bob: requester or payer, newest first")
    eq(both(cy), [c2, c1], "cy sees only her own")
    eq(both(op), [], "an operator sees no one else's requests")
    eq(both(ada, direction="incoming"), [c1, b2, b1], "ada incoming (payer)")
    eq(both(ada, direction="outgoing"), [a3, a2, a1], "ada outgoing (requester)")
    eq(both(bob, direction="incoming"), [c2, a3, a2, a1], "bob incoming")
    eq(both(bob, direction="outgoing"), [b2, b1], "bob outgoing")
    eq(both(ada, status="pending"), [c1, b2, a1], "pending only")
    eq(both(ada, status="declined"), [a2], "declined")
    eq(both(ada, status="paid"), [a3], "paid")
    eq(both(ada, status="cancelled"), [b1], "cancelled")
    eq(both(ada, direction="incoming", status="pending"), [c1, b2], "combined filters")
    eq(both(bob, direction="outgoing", status="cancelled"), [b1], "combined filters 2")
    # envelope & unknown params
    r = ada.requests(direction="incoming", status="pending", limit=50, offset=0, whatever="x")
    eq(set(r), {"requests", "has_more"}, "envelope")
    for x in r["requests"]:
        check_request(x)
    # bad values
    for q in ("direction=sideways", "direction=INCOMING", "direction=", "status=open", "status=PAID", "status=",
              "limit=0", "limit=201", "offset=-1", "limit=x", "limit=1e2", "limit=4.0", "limit=+4",
              "offset=+1", "offset=1.0", "offset=1e0"):
        if q in ("direction=", "status="):
            continue
        expect(ada.get("/requests?" + q), 422, "validation_failed", "GET /requests?" + q)
    # pagination reassembles the strict order
    pages = []
    for off in (0, 2, 4):
        r = ada.requests(limit=2, offset=off)
        pages += [x["request_id"] for x in r["requests"]]
        eq(r["has_more"], off < 4, "has_more at offset %d (6 items, limit 2)" % off)
    eq(pages, [c1, b2, b1, a3, a2, a1], "pages reassemble the full list")
    eq(ada.requests(limit=6)["has_more"], False, "exactly limit remaining")
    eq(ada.requests(limit=5)["has_more"], True, "one more beyond")
    eq(ada.requests(offset=6), {"requests": [], "has_more": False}, "offset at the end")
    eq(ada.requests(offset=99, limit=3), {"requests": [], "has_more": False}, "offset beyond the end")
    # the operator cannot touch requests of others
    for rid in (a1, b2, c1):
        r = op.post("/requests/%s/decline" % rid)
        stranger_ok(r)
        r = op.post("/requests/%s/cancel" % rid)
        stranger_ok(r)
        r = op.pay_request(rid)
        stranger_ok(r)
    eq(both(ada, status="pending"), [c1, b2, a1], "operator changed nothing")


@test("S1-144", "S1-145", "S1-141")
def test_request_default_page_is_50():
    w = world({"ada": 1000, "bob": 0})
    ada, bob = w["ada"], w["bob"]
    res = burst([lambda i=i: bob.request("ada", i + 1) for i in range(51)], workers=17)
    ok(all(r.status == 201 for r in res), "51 requests created")
    r = ada.requests()
    eq(len(r["requests"]), 50, "default limit 50")
    eq(r["has_more"], True, "51st request is beyond the page")
    eq(len(ada.requests(limit=51)["requests"]), 51, "limit 51")
    eq(ada.requests(limit=51)["has_more"], False, "no more")
    eq(len(ada.requests(limit=200)["requests"]), 51, "limit 200")
    eq(len(ada.requests(offset=50)["requests"]), 1, "offset 50")
    stamps = [parse_ts(x["created_at"]) for x in ada.requests(limit=200)["requests"]]
    ok(all(stamps[i] >= stamps[i + 1] for i in range(len(stamps) - 1)), "newest first")
    ids = [x["request_id"] for x in ada.requests(limit=200)["requests"]]
    eq(len(set(ids)), 51, "unique ids")


@test("S1-007", "S1-207", "S1-005", "S1-006", "S1-105")
def test_request_pays_once_under_concurrency():
    w = world({"ada": 100_000, "bob": 0})
    ada, bob = w["ada"], w["bob"]
    for round_ in range(3):
        rid = expect(bob.request("ada", 1000), 201).json["request_id"]
        before = ada.balance
        res = burst([lambda: ada.pay_request(rid) for _ in range(20)])
        codes = sorted(r.status for r in res)
        eq(codes.count(201), 1, "exactly one concurrent pay (different keys) may succeed: %r" % codes)
        for r in res:
            if r.status != 201:
                expect(r, 409, "request_not_pending", "losers")
        eq(before - ada.balance, 1000, "money moved exactly once")
        eq(len([p for p in bob.all_payments() if p["request_id"] == rid]), 1, "one payment per request")
    eq(ada.balance + bob.balance, 100_000, "conservation")
    # same key, concurrent: one 201, the rest 200 with the same body
    rid = expect(bob.request("ada", 500), 201).json["request_id"]
    k = fresh_key()
    res = burst([lambda: ada.pay_request(rid, key=k) for _ in range(20)])
    eq(sorted(r.status for r in res), [200] * 19 + [201], "same-key burst")
    eq(len({json.dumps(r.json, sort_keys=True) for r in res}), 1, "identical bodies")
    eq(ada.balance + bob.balance, 100_000, "conservation")
    eq(bob.balance, 3000 + 500, "paid once")


@test("S1-007", "S1-207", "S1-045", "S1-006", "S1-005")
def test_pay_decline_cancel_race():
    w = world({"ada": 1_000_000, "bob": 0})
    ada, bob = w["ada"], w["bob"]
    for round_ in range(6):
        rid = expect(bob.request("ada", 100), 201).json["request_id"]
        fns = ([lambda: ("pay", ada.pay_request(rid)) for _ in range(6)] +
               [lambda: ("decline", ada.post("/requests/%s/decline" % rid)) for _ in range(5)] +
               [lambda: ("cancel", bob.post("/requests/%s/cancel" % rid)) for _ in range(5)])
        res = burst(fns)
        final = [x for x in bob.all_requests() if x["request_id"] == rid][0]
        st = final["status"]
        ok(st in ("paid", "declined", "cancelled"), "request must end in a terminal state, got %s" % st)
        pays = [r for kind, r in res if kind == "pay" and r.status == 201]
        eq(len(pays), 1 if st == "paid" else 0, "a 201 pay exists iff the final state is paid (%s)" % st)
        for kind, r in res:
            ok(r.status in (200, 201, 409), "unexpected status %s for %s: %s" % (r.status, kind, r.text[:100]))
            if r.status in (200, 201):
                if kind == "decline":
                    ok(st == "declined", "a decline answered 200 but the final state is %s" % st)
                if kind == "cancel":
                    ok(st == "cancelled", "a cancel answered 200 but the final state is %s" % st)
                if kind == "pay":
                    ok(st == "paid", "a pay answered 201 but the final state is %s" % st)
            else:
                eq(r.code, "request_not_pending", "losers of the race")
        moved = 100 if st == "paid" else 0
        paid_so_far = sum(1 for x in bob.all_requests() if x["status"] == "paid") * 100
        eq(bob.balance, paid_so_far, "bob holds exactly the paid requests")
        eq(ada.balance + bob.balance, 1_000_000, "conservation")


@test("S1-006", "S1-005", "S1-007", "S1-111", "S1-130")
def test_two_requests_one_balance():
    w = world({"ada": 1200, "bob": 0, "cy": 0})
    ada, bob, cy = w["ada"], w["bob"], w["cy"]
    r1 = expect(bob.request("ada", 1200), 201).json["request_id"]
    r2 = expect(cy.request("ada", 1200), 201).json["request_id"]
    r3 = expect(cy.request("ada", 1200), 201).json["request_id"]
    res = burst([lambda: ada.pay_request(r1), lambda: ada.pay_request(r2), lambda: ada.pay_request(r3)] * 3)
    codes = sorted(r.status for r in res)
    eq(codes.count(201), 1, "balance affords exactly one of them: %r" % codes)
    for r in res:
        if r.status != 201:
            ok(r.code in ("insufficient_funds", "request_not_pending"), "loser code %s" % r.code)
    eq(ada.balance, 0, "drained, not negative")
    eq(ada.balance + bob.balance + cy.balance, 1200, "conservation")
