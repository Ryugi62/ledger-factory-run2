"""POST /splits and the equal-split arithmetic of section 9."""
from lib import *  # noqa: F401,F403


def expected_shares(amount, n):
    base, extra = divmod(amount, n)
    return [base + 1 if i < extra else base for i in range(n)]


def group(n, balance=10_000):
    names = ["h%d" % i for i in range(1, n + 1)]
    w = world({h: balance for h in names})
    return names, w


@test("S1-148", "S1-149", "S1-150", "S1-147", "S1-100", "S1-001", "S1-162")
def test_split_shape_caller_first():
    w = world({"ada": 5000, "bob": 2000, "cy": 0})
    ada, bob, cy = w["ada"], w["bob"], w["cy"]
    r = expect(ada.split(3000, ["ada", "bob", "cy"], note="dinner"), 201)
    j = r.json
    ok(isinstance(j["split_id"], str) and 0 < len(j["split_id"]) <= 64, "split_id")
    eq((j["amount"], j["currency"], j["note"]), (3000, "EUR", "dinner"), "split echo")
    check_ts(j["created_at"])
    eq(j["shares"], [{"handle": "ada", "amount": 1000}, {"handle": "bob", "amount": 1000},
                     {"handle": "cy", "amount": 1000}], "shares")
    eq(len(j["requests"]), 2, "one request per participant except the caller")
    check_request(j["requests"][0], requester=ada, payer=bob, amount=1000, status="pending", payment_id=None)
    check_request(j["requests"][1], requester=ada, payer=cy, amount=1000, status="pending", payment_id=None)
    # no money moved, no feed item
    eq((ada.balance, bob.balance, cy.balance), (5000, 2000, 0), "a split moves no money")
    for u in (ada, bob, cy):
        eq(u.all_payments(), [], "a split is not a feed item")
    # the requests are real: visible to their two parties, payable
    inc = bob.all_requests(direction="incoming")
    eq([x["request_id"] for x in inc], [j["requests"][0]["request_id"]], "bob's incoming request")
    out = ada.all_requests(direction="outgoing")
    eq({x["request_id"] for x in out}, {x["request_id"] for x in j["requests"]}, "ada's outgoing requests")
    eq(len(cy.all_requests()), 1, "cy sees only hers")
    expect(bob.pay_request(j["requests"][0]["request_id"]), 201, msg="bob pays his share")
    eq((ada.balance, bob.balance), (6000, 1000), "share paid")


@test("S1-149", "S1-150", "S1-161", "S1-160")
def test_split_caller_omitted_or_in_the_middle():
    w = world({"ada": 0, "bob": 0, "cy": 0, "dee": 0})
    ada, bob, cy, dee = w["ada"], w["bob"], w["cy"], w["dee"]
    j = expect(ada.split(3000, ["bob", "cy"]), 201).json
    eq(j["shares"], [{"handle": "bob", "amount": 1500}, {"handle": "cy", "amount": 1500}],
       "caller omitted: shares cover exactly the listed handles, n = number of listed handles")
    eq([(r["payer_handle"], r["amount"], r["requester_handle"]) for r in j["requests"]],
       [("bob", 1500, "ada"), ("cy", 1500, "ada")], "requests for every listed participant")
    j = expect(ada.split(1000, ["bob", "ada", "cy"]), 201).json
    eq([(s["handle"], s["amount"]) for s in j["shares"]], [("bob", 334), ("ada", 333), ("cy", 333)],
       "extra unit goes to the first listed handle even though it is not the caller")
    eq([(r["payer_handle"], r["amount"]) for r in j["requests"]], [("bob", 334), ("cy", 333)],
       "requests skip the caller, keep order")
    j = expect(ada.split(1000, ["dee", "cy", "bob", "ada"]), 201).json
    eq([s["amount"] for s in j["shares"]], [250, 250, 250, 250], "even split of four")
    eq([r["payer_handle"] for r in j["requests"]], ["dee", "cy", "bob"], "order preserved")
    j = expect(bob.split(10, ["cy", "ada", "bob"]), 201).json     # caller last
    eq([(s["handle"], s["amount"]) for s in j["shares"]], [("cy", 4), ("ada", 3), ("bob", 3)], "caller last")
    eq([(r["payer_handle"], r["requester_handle"]) for r in j["requests"]], [("cy", "bob"), ("ada", "bob")], "requester is the caller")


@test("S1-162", "S1-163", "S1-164", "S1-165", "S1-166", "S1-161", "S1-160", "S1-167")
def test_split_rounding_table():
    names, w = group(5)
    ada = w["h1"]
    table = [(1000, 3, [334, 333, 333]), (1, 3, [1, 0, 0]), (10, 3, [4, 3, 3]),
             (999, 3, [333, 333, 333]), (5, 5, [1, 1, 1, 1, 1])]
    for amount, n, want in table:
        j = expect(ada.split(amount, names[:n]), 201).json
        eq([s["amount"] for s in j["shares"]], want, "split %d among %d" % (amount, n))
        eq([s["handle"] for s in j["shares"]], names[:n], "share order")
        eq([r["amount"] for r in j["requests"]], want[1:], "requests (caller is first)")
    # reordering gives the extra unit to someone else
    for order in (["h1", "h2", "h3"], ["h2", "h3", "h1"], ["h3", "h1", "h2"], ["h3", "h2", "h1"]):
        j = expect(ada.split(10, order), 201).json
        got = {s["handle"]: s["amount"] for s in j["shares"]}
        eq(got[order[0]], 4, "first listed gets the extra unit in %r" % order)
        eq(sorted(got.values()), [3, 3, 4], "shares")
    j = expect(ada.split(11, ["h3", "h1", "h2"]), 201).json
    eq([(s["handle"], s["amount"]) for s in j["shares"]], [("h3", 4), ("h1", 4), ("h2", 3)], "two extra units go to the first two")


@test("S1-160", "S1-161", "S1-150", "S1-149", "S1-168")
def test_split_property_sums_and_order():
    names, w = group(8)
    ada = w["h1"]
    amounts = [1, 2, 3, 4, 5, 7, 10, 11, 99, 100, 101, 999, 1000, 1001, 12345, 99999, 1_000_000_000, 999_999_999]
    for amount in amounts:
        for n in range(1, 9):
            for caller_in in (True, False):
                if caller_in:
                    handles = names[:n]
                else:
                    if n > 7:
                        continue
                    handles = names[1:n + 1]
                j = expect(ada.split(amount, handles), 201, msg="split %d among %r" % (amount, handles)).json
                got = [s["amount"] for s in j["shares"]]
                want = expected_shares(amount, len(handles))
                eq(got, want, "shares of %d among %d (caller %s)" % (amount, len(handles), "in" if caller_in else "out"))
                ok(sum(got) == amount and max(got) - min(got) <= 1 and all(g >= 0 for g in got), "invariants")
                eq([s["handle"] for s in j["shares"]], handles, "order")
                non_caller = [(h, a) for h, a in zip(handles, got) if h != "h1"]
                eq([(r["payer_handle"], r["amount"]) for r in j["requests"]], non_caller, "requests")


@test("S1-155", "S1-160")
def test_split_only_the_caller():
    w = world({"ada": 10, "bob": 10})
    ada, bob = w["ada"], w["bob"]
    j = expect(ada.split(777, ["ada"], note="solo"), 201).json
    eq(j["shares"], [{"handle": "ada", "amount": 777}], "one share")
    eq(j["requests"], [], "zero requests")
    eq(ada.all_requests(), [], "nothing created")
    eq(bob.all_requests(), [], "nothing created")
    eq((ada.balance, bob.balance), (10, 10), "no money")


@test("S1-168", "S1-163", "S1-140", "S1-134")
def test_split_zero_shares_still_create_requests():
    w = world({"ada": 100, "bob": 100, "cy": 100})
    ada, bob, cy = w["ada"], w["bob"], w["cy"]
    j = expect(ada.split(1, ["ada", "bob", "cy"]), 201).json
    eq([s["amount"] for s in j["shares"]], [1, 0, 0], "1 among 3")
    eq(len(j["requests"]), 2, "a request per non-caller participant, even for 0")
    eq([r["amount"] for r in j["requests"]], [0, 0], "zero-amount requests")
    for r in j["requests"]:
        check_request(r, requester=ada, status="pending", payment_id=None)
    mine = bob.all_requests(direction="incoming")
    eq([x["amount"] for x in mine], [0], "bob sees his zero request")
    eq(mine[0]["status"], "pending", "pending")
    expect(bob.post("/requests/%s/decline" % mine[0]["request_id"]), 200, msg="a zero request can be declined")
    j = expect(ada.split(1, ["bob", "cy", "ada"]), 201).json
    eq([(s["handle"], s["amount"]) for s in j["shares"]], [("bob", 1), ("cy", 0), ("ada", 0)], "extra unit to the first listed")
    eq([(r["payer_handle"], r["amount"]) for r in j["requests"]], [("bob", 1), ("cy", 0)], "requests")
    j = expect(ada.split(2, ["ada", "bob", "cy"]), 201).json
    eq([s["amount"] for s in j["shares"]], [1, 1, 0], "2 among 3")


@test("S1-151", "S1-152", "S1-153", "S1-154", "S1-123", "S1-068", "S1-074", "S1-103", "S1-114", "S1-076",
      "S1-077", "S1-197")
def test_split_validation_and_atomicity():
    w = world({"ada": 100, "bob": 0, "cy": 0})
    ada, bob, cy = w["ada"], w["bob"], w["cy"]

    def bad(body, status=422, code="validation_failed", why="", key=None):
        expect(ada.post("/splits", body, key=key or fresh_key()), status, code, why or repr(body))
        eq(ada.all_requests(), [], "a rejected split creates no request: %r" % (body,))
        eq(bob.all_requests(), [], "a rejected split creates no request: %r" % (body,))

    for amt in (0, -3, 1_000_000_001, 2.5, "30", True, None, [], {}):
        bad({"amount": amt, "participant_handles": ["bob"]}, why="amount %r" % (amt,))
    bad({"participant_handles": ["bob"]}, why="missing amount")
    bad({"amount": 30}, why="missing participant_handles")
    bad({"amount": 30, "participant_handles": []}, why="empty list")
    bad({"amount": 30, "participant_handles": ["bob", "bob"]}, why="duplicate")
    bad({"amount": 30, "participant_handles": ["ada", "bob", "ada"]}, why="caller twice")
    bad({"amount": 30, "participant_handles": ["bob", "cy", "bob"]}, why="duplicate not adjacent")
    bad({"amount": 30, "participant_handles": ["bob"], "note": "x" * 201}, why="long note")
    for note in (None, 5, [], {}, True):
        bad({"amount": 30, "participant_handles": ["bob"], "note": note}, why="note %r" % (note,))
    for ph in ("bob", 5, {"h": "bob"}, True):
        bad({"amount": 30, "participant_handles": ph}, 400, "malformed_request", "participant_handles %r" % (ph,))
    for raw in ("{", "", "[]"):
        expect(http("POST", "/splits", raw=raw, token=ada.token, key=fresh_key()), 400, "malformed_request", repr(raw))
    # unknown handle anywhere => 404 and nothing created for the others; key stays free
    k = fresh_key()
    bad({"amount": 30, "participant_handles": ["bob", "cy", "nobody"]}, 404, "not_found", "unknown handle last", key=k)
    bad({"amount": 30, "participant_handles": ["nobody"]}, 404, "not_found", "unknown handle only")
    bad({"amount": 30, "participant_handles": ["nobody", "bob"]}, 404, "not_found", "unknown handle first")
    r = expect(ada.post("/splits", {"amount": 30, "participant_handles": ["bob", "cy"]}, key=k), 201,
               msg="the key of a failed attempt can be used for a different body")
    eq(len(r.json["requests"]), 2, "valid split")
    expect(ada.split(30, ["bob"], note="x" * 200), 201, msg="200-char note")
    expect(ada.split(30, ["bob"], note="\U0001F600" * 200), 201, msg="200 emoji note")
    expect(ada.split(1_000_000_000, ["bob", "cy"]), 201, msg="max amount")


@test("S1-156", "S1-147", "S1-055", "S1-005")
def test_split_checks_no_balance_and_moves_no_money():
    w = world({"ada": 0, "bob": 0, "cy": 0, "dee": 0})
    ada, bob, cy, dee = w["ada"], w["bob"], w["cy"], w["dee"]
    j = expect(ada.split(1_000_000_000, ["ada", "bob", "cy"]), 201, msg="nobody holds anything").json
    eq([s["amount"] for s in j["shares"]], [333_333_334, 333_333_333, 333_333_333], "shares")
    j2 = expect(bob.split(10, ["bob", "cy"]), 201, msg="caller with zero balance").json
    eq((ada.balance, bob.balance, cy.balance, dee.balance), (0, 0, 0, 0), "balances untouched")
    for u in (ada, bob, cy, dee):
        eq(u.all_payments(), [], "no payments")
    eq(len(dee.all_requests()), 0, "a non-participant sees nothing")
    eq(len(cy.all_requests()), 2, "cy sees the two requests addressed to her")
    eq(len(bob.all_requests()), 2, "bob: one incoming from ada, one outgoing to cy")
    # payers cannot pay (no money) but that is the normal 409, not a split error
    rid = j["requests"][0]["request_id"]
    expect(bob.pay_request(rid), 409, "insufficient_funds")
    eq(bob.all_requests(direction="incoming", status="pending")[0]["status"], "pending", "still pending")


@test("S1-169", "S1-005", "S1-160", "S1-167")
def test_splits_are_independent_and_conserve_money():
    names, w = group(5, balance=100_000)
    users = [w[n] for n in names]
    ada = w["h1"]
    seeded = 5 * 100_000
    for _ in range(2):
        j = expect(ada.split(1, ["h1", "h2", "h3"]), 201).json
        eq([s["amount"] for s in j["shares"]], [1, 0, 0], "remainder does not rotate between splits")
    j = expect(ada.split(2, ["h1", "h2", "h3"]), 201).json
    eq([s["amount"] for s in j["shares"]], [1, 1, 0], "2 among 3 after earlier splits")
    plan = [(1000, 3), (1, 3), (10, 3), (999, 3), (5, 5), (7, 4), (31, 5), (100, 2), (3, 3), (2, 5), (101, 4)]
    expected_in = 0
    for amount, n in plan:
        handles = names[:n]
        j = expect(ada.split(amount, handles), 201).json
        eq([s["amount"] for s in j["shares"]], expected_shares(amount, n), "split %d/%d" % (amount, n))
        for r in j["requests"]:
            payer = w[r["payer_handle"]]
            if r["amount"] > 0:
                expect(payer.pay_request(r["request_id"]), 201, msg="share paid in full")
                expected_in += r["amount"]
    eq(total(users), seeded, "after splits were paid in full the balances still sum to the seeded total")
    eq(ada.balance, 100_000 + expected_in, "caller received exactly the paid shares")
    for u in users[1:]:
        ok(u.balance <= 100_000, "no one ended with extra money")
    paid = [x for x in ada.all_requests(status="paid")]
    ok(all(x["payment_id"] for x in paid), "paid requests carry their payment id")
