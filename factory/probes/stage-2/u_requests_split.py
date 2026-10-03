"""Browser probes: /requests screen and /split screen."""
import json

from ui_lib import *  # noqa: F401,F403
from u_wallet import open_as, body_of


def container_of(pg, rid):
    return pg.page.evaluate(
        """id => { const e = document.querySelector('[data-testid="request-item-' + id + '"]'); if (!e) return null;
                   const c = e.closest('[data-testid="incoming-list"],[data-testid="outgoing-list"]');
                   return c ? c.getAttribute('data-testid') : ''; }""", rid)


def shares_of(amount, n):
    base, extra = divmod(amount, n)
    return [base + 1 if i < extra else base for i in range(n)]


def seeded_requests():
    rows = [("i_pend", "bob", "ada", 1200, "pending"), ("i_paid", "bob", "ada", 700, "paid"),
            ("i_decl", "bob", "ada", 300, "declined"), ("i_canc", "bob", "ada", 100, "cancelled"),
            ("o_pend", "ada", "bob", 5500, "pending"), ("o_paid", "ada", "bob", 800, "paid"),
            ("o_decl", "ada", "bob", 900, "declined"), ("o_canc", "ada", "bob", 1000, "cancelled"),
            ("x_other", "bob", "cy", 4000, "pending")]
    return [{"id": i, "requester_id": "u_" + a, "payer_id": "u_" + b, "amount": amt, "note": "n " + i, "status": st}
            for i, a, b, amt, st in rows]


@test("S2-053", "S2-054", "S2-055", "S2-056", "S2-057", "S2-058", "S2-060", "S2-068", "S2-006", "S2-010")
def test_requests_screen_lists_statuses_and_actions():
    world2({"ada": 10000, "bob": 10000, "cy": 0, "dee": 0}, requests=seeded_requests())
    with session() as pg:
        open_as(pg, "ada", "/requests")
        pg.wait(lambda: len(request_ids(pg)) == 8, "eight requests involving ada", 10)
        ok(pg.exists("incoming-list") and pg.exists("outgoing-list"), "both containers present")
        eq(sorted(request_ids(pg)), sorted(["i_pend", "i_paid", "i_decl", "i_canc", "o_pend", "o_paid", "o_decl", "o_canc"]), "items")
        ok("x_other" not in request_ids(pg), "requests of others are not shown")
        want = {"i_pend": ("pending", 1200, "in"), "i_paid": ("paid", 700, "in"), "i_decl": ("declined", 300, "in"),
                "i_canc": ("cancelled", 100, "in"), "o_pend": ("pending", 5500, "out"), "o_paid": ("paid", 800, "out"),
                "o_decl": ("declined", 900, "out"), "o_canc": ("cancelled", 1000, "out")}
        for rid, (st, amt, d) in want.items():
            eq(pg.attr("request-item-" + rid, "data-status"), st, "data-status of " + rid)
            eq(pg.tc("request-amount-" + rid).strip(), fmt(amt), "amount of " + rid)
            eq(container_of(pg, rid), "incoming-list" if d == "in" else "outgoing-list", "container of " + rid)
            pend = st == "pending"
            eq(pg.exists("request-pay-" + rid), pend and d == "in", "request-pay presence on %s (%s %s)" % (rid, st, d))
            eq(pg.exists("request-decline-" + rid), pend and d == "in", "request-decline presence on %s" % rid)
            eq(pg.exists("request-cancel-" + rid), pend and d == "out", "request-cancel presence on %s" % rid)
        ok(not pg.exists("empty-requests"), "empty-requests absent with items")
        ok(not pg.exists("request-error"), "no request-error initially")
        pg.shot("requests-ada-1280")
        # pay: becomes paid, buttons vanish, no reload needed
        pg.click("request-pay-i_pend")
        pg.wait(lambda: pg.attr("request-item-i_pend", "data-status") == "paid", "i_pend becomes paid")
        pg.wait(lambda: not pg.exists("request-pay-i_pend") and not pg.exists("request-decline-i_pend"), "buttons gone after paying")
        ok(not pg.exists("request-error"), "no error after a successful pay")
        eq(pg.posts("/requests/i_pend/pay")[0]["headers"].get("idempotency-key") is not None, True, "pay carries an Idempotency-Key")
        # cancel an outgoing one
        pg.click("request-cancel-o_pend")
        pg.wait(lambda: pg.attr("request-item-o_pend", "data-status") == "cancelled", "o_pend becomes cancelled")
        pg.wait(lambda: not pg.exists("request-cancel-o_pend"), "cancel button gone")
    ada = login("ada@example.com", handle="ada")
    eq(ada.balance, 10000 - 1200, "money moved once, from ada")
    # decline needs a fresh pending request
    w = world2({"ada": 10000, "bob": 10000, "cy": 0}, requests=seeded_requests()[:1])
    with session() as pg:
        open_as(pg, "ada", "/requests")
        pg.wait_visible("request-decline-i_pend")
        pg.click("request-decline-i_pend")
        pg.wait(lambda: pg.attr("request-item-i_pend", "data-status") == "declined", "declined")
        pg.wait(lambda: not pg.exists("request-pay-i_pend") and not pg.exists("request-decline-i_pend"), "buttons gone after declining")
    # the receiving side sees the same request as outgoing with a cancel button
    with session() as pg:
        open_as(pg, "bob", "/requests")
        pg.wait(lambda: "i_pend" in request_ids(pg), "bob sees the request")
        eq(container_of(pg, "i_pend"), "outgoing-list", "outgoing for the requester")
        eq(pg.attr("request-item-i_pend", "data-status"), "declined", "status as the payer left it")
    # empty state
    with session() as pg:
        open_as(pg, "cy", "/requests")
        pg.wait_visible("empty-requests")
        eq(request_ids(pg), [], "no items")
        pg.go("/")
        pg.wait_visible("wallet-balance")
        pg.go("/requests")
        pg.wait_visible("empty-requests")


@test("S2-059", "S2-074", "S2-054", "S2-056", "S2-057", "S2-058", "S2-069")
def test_request_errors_and_stale_buttons():
    rows = [("c1", "bob", "ada", 500, "pending"), ("c2", "bob", "ada", 100000, "pending"), ("c3", "bob", "ada", 200, "pending"),
            ("c4", "ada", "bob", 300, "pending"), ("c5", "bob", "ada", 400, "pending"), ("c6", "ada", "bob", 600, "pending")]
    reqs = [{"id": i, "requester_id": "u_" + a, "payer_id": "u_" + b, "amount": amt, "note": "", "status": st} for i, a, b, amt, st in rows]
    w = world2({"ada": 10000, "bob": 10000}, requests=reqs)
    ada, bob = w["ada"], w["bob"]
    with session() as pg:
        open_as(pg, "ada", "/requests")
        pg.wait(lambda: len(request_ids(pg)) == 6, "six requests")
        # cancelled elsewhere while the pay button is visible
        ok(pg.visible("request-pay-c1"), "pay button visible")
        expect(bob.post("/requests/c1/cancel"), 200)
        pg.click("request-pay-c1")
        pg.wait(lambda: pg.visible("request-error"), "request-error when the request was cancelled elsewhere")
        ok(pg.it("request-error") != "", "request-error text")
        pg.wait(lambda: pg.attr("request-item-c1", "data-status") == "cancelled", "the list is refreshed: c1 shows cancelled")
        ok(not pg.exists("request-pay-c1") and not pg.exists("request-decline-c1"), "the stale pay/decline buttons disappeared")
        eq(ada.balance, 10000, "no money moved")
        # insufficient funds on pay: error, request stays pending and payable
        pg.click("request-pay-c2")
        pg.wait(lambda: pg.visible("request-error"), "request-error for insufficient funds")
        eq(pg.attr("request-item-c2", "data-status"), "pending", "still pending")
        ok(pg.exists("request-pay-c2"), "pay button still there after insufficient funds")
        eq(ada.balance, 10000, "no money moved")
        # error clears after a later success
        pg.click("request-pay-c3")
        pg.wait(lambda: pg.attr("request-item-c3", "data-status") == "paid", "c3 paid")
        pg.wait(lambda: not pg.visible("request-error"), "request-error clears after a success")
        # paid elsewhere while the decline button is visible
        ok(pg.visible("request-decline-c5"), "decline button visible")
        expect(ada.pay_request("c5"), 201)
        pg.click("request-decline-c5")
        pg.wait(lambda: pg.visible("request-error"), "request-error when declining a request paid elsewhere")
        pg.wait(lambda: pg.attr("request-item-c5", "data-status") == "paid", "refreshed to paid")
        ok(not pg.exists("request-decline-c5"), "stale decline button gone")
        # declined elsewhere while the cancel button is visible
        ok(pg.visible("request-cancel-c4"), "cancel button visible")
        expect(bob.post("/requests/c4/decline"), 200)
        pg.click("request-cancel-c4")
        pg.wait(lambda: pg.visible("request-error"), "request-error when cancelling a request declined elsewhere")
        pg.wait(lambda: pg.attr("request-item-c4", "data-status") == "declined", "refreshed to declined")
        ok(not pg.exists("request-cancel-c4"), "stale cancel button gone")
        # while a write is held in flight nothing changes optimistically
        state = {"n": 0}

        def slow(route, request):
            if request.method == "POST":
                state["n"] += 1
                pg.page.wait_for_timeout(1500)
            route.fallback()

        pg.page.route("**/requests/c6/cancel", slow)
        pg.click("request-cancel-c6")
        pg.wait(lambda: state["n"] == 1, "cancel in flight")
        eq(pg.attr("request-item-c6", "data-status"), "pending", "no optimistic status change while in flight")
        pg.wait(lambda: pg.attr("request-item-c6", "data-status") == "cancelled", "cancelled after the response", 10)


EXPECTED_PREVIEW = [
    ("EUR", 2, "10.00", ["ada", "bob", "cy"], 1000), ("EUR", 2, "10.00", ["cy", "bob", "ada"], 1000),
    ("EUR", 2, "0.01", ["ada", "bob", "cy"], 1), ("EUR", 2, "0.10", ["bob", "cy", "ada"], 10),
    ("EUR", 2, "9.99", ["ada", "bob", "cy"], 999), ("EUR", 2, "0.05", ["ada", "bob", "cy", "dee", "eve"], 5),
    ("EUR", 2, "10.00", ["bob", "cy"], 1000), ("EUR", 2, "10.01", ["bob", "cy"], 1001), ("EUR", 2, "20", ["dee", "ada"], 2000),
    ("EUR", 2, "10000000.00", ["ada", "bob", "cy", "dee", "eve", "fay", "gus"], 1000000000),
    ("JPY", 0, "10", ["ada", "bob", "cy"], 10), ("JPY", 0, "1", ["ada", "bob", "cy"], 1), ("JPY", 0, "5", ["ada", "bob", "cy", "dee", "eve"], 5),
    ("BHD", 3, "1.000", ["ada", "bob", "cy"], 1000), ("BHD", 3, "0.001", ["bob", "cy", "ada"], 1), ("BHD", 3, "0.5", ["ada", "bob"], 500),
]
HANDLES = ["ada", "bob", "cy", "dee", "eve", "fay", "gus"]


@test("S2-061", "S2-062", "S2-063", "S2-064", "S2-065", "S2-067", "S2-007")
def test_split_preview_matches_the_server_rule():
    for cur, mu in (("EUR", 2), ("JPY", 0), ("BHD", 3)):
        w = world2(dict((h, 0) for h in HANDLES), currency=cur, minor_units=mu)
        with session() as pg:
            open_as(pg, "ada", "/split")
            for t in ("split-amount", "split-handles", "split-note", "split-submit"):
                pg.wait_visible(t)
            for _c, _m, amount, handles, minor in [x for x in EXPECTED_PREVIEW if x[0] == cur]:
                pg.fill("split-amount", amount)
                pg.fill("split-handles", ",".join(handles))
                want = dict(zip(handles, shares_of(minor, len(handles))))
                pg.wait(lambda: sorted(pg.ids("split-share-")) == sorted(handles), "preview participants for %s %s" % (amount, handles))
                for h, a in want.items():
                    ok(pg.exists("split-share-" + h), "split-share-%s missing" % h)
                    exp = fmt(a, mu, cur)
                    pg.wait(lambda h=h, exp=exp: pg.tc("split-share-" + h).strip() == exp,
                            "split-share-%s == %s (%s among %s)" % (h, exp, amount, handles), 4)
                ok(pg.visible("split-preview"), "split-preview visible")
            eq(len(pg.posts("/splits")), 0, "the preview posts nothing")
            pg.shot("split-%s-1280" % cur)
    # preview equals the submitted split
    w = world2(dict((h, 0) for h in HANDLES))
    ada = w["ada"]
    for amount, handles, minor in (("10.00", "ada,bob,cy", 1000), ("0.05", "dee,bob,ada,cy,eve", 5), ("7.77", "bob,cy", 777)):
        with session() as pg:
            open_as(pg, "ada", "/split")
            pg.wait_visible("split-amount")
            pg.fill("split-amount", amount)
            pg.fill("split-handles", handles)
            pg.fill("split-note", "dinner " + amount)
            hs = handles.split(",")
            pg.wait(lambda: sorted(pg.ids("split-share-")) == sorted(hs), "preview ready")
            preview = {h: pg.tc("split-share-" + h).strip() for h in hs}
            eq(len(pg.posts("/splits")), 0, "nothing posted before submit")
            pg.click("split-submit")
            pg.wait(lambda: len(pg.posts("/splits")) == 1, "POST /splits")
            r = pg.posts("/splits")[0]
            ok(r["headers"].get("idempotency-key"), "Idempotency-Key on POST /splits")
            b = body_of(r)
            eq((b["amount"], b["participant_handles"], b["note"]), (minor, hs, "dinner " + amount), "split wire body")
            pg.wait(lambda: not pg.exists("split-error"), "no split-error")
            pg.page.wait_for_timeout(300)
        reqs = [q for q in ada.all_requests(direction="outgoing")]
        # the shares the server computed are the ones previewed
        shares = shares_of(minor, len(hs))
        expected = {h: fmt(a) for h, a in zip(hs, shares)}
        eq(preview, expected, "the preview equals the §9 shares")
        by_handle = {}
        for q in reqs:
            by_handle.setdefault(q["payer_handle"], []).append(q["amount"])
        for h, a in zip(hs, shares):
            if h != "ada":
                ok(a in by_handle.get(h, []), "request for %s with the previewed amount %d not created (%r)" % (h, a, by_handle.get(h)))


@test("S2-066", "S2-061", "S2-062", "S2-045", "S2-069")
def test_split_errors():
    world2(dict((h, 0) for h in ("ada", "bob", "cy")))
    with session() as pg:
        open_as(pg, "ada", "/split")
        pg.wait_visible("split-amount")
        ok(not pg.exists("split-error"), "no split-error initially")
        n = 0
        for amount, handles, why, sent in (("10.00", "ada,nobody", "unknown handle", True), ("10.00", "bob,bob", "duplicate handle", True),
                                           ("10.00", "", "no participants", True), ("10.005", "ada,bob", "too many decimals", False),
                                           ("abc", "ada,bob", "not a number", False), ("", "ada,bob", "empty amount", False),
                                           ("0", "ada,bob", "zero amount", True)):
            pg.fill("split-amount", amount)
            pg.fill("split-handles", handles)
            pg.click("split-submit")
            pg.wait(lambda: pg.visible("split-error"), "split-error for " + why)
            ok(pg.it("split-error") != "", "split-error text: " + why)
            if not sent:
                pg.page.wait_for_timeout(250)
                eq(len(pg.posts("/splits")), n, "nothing sent for " + why)
            n = len(pg.posts("/splits"))
        eq(login("ada@example.com").all_requests(), [], "refused splits created nothing")
        pg.fill("split-amount", "9.00")
        pg.fill("split-handles", "ada,bob,cy")
        pg.click("split-submit")
        pg.wait(lambda: len(login("ada@example.com").all_requests(direction="outgoing")) == 2, "valid split creates two requests")
        pg.wait(lambda: not pg.visible("split-error"), "split-error clears after success")
        # the created requests are visible on the requests screen
        pg.go("/requests")
        pg.wait(lambda: len(request_ids(pg)) == 2, "two requests listed")
        for rid in request_ids(pg):
            eq(pg.tc("request-amount-" + rid).strip(), "3.00 EUR", "share amount")
            eq(pg.attr("request-item-" + rid, "data-status"), "pending", "pending")
            ok(pg.exists("request-cancel-" + rid), "cancel button for the requester")
