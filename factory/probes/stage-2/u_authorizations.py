"""Browser probes: wallet numbers (total/available/held), authorise form, /authorizations screen."""
import json

from ui_lib import *  # noqa: F401,F403
from u_wallet import open_as, body_of


def rfc3339_instant(text):
    t = text.strip()
    if t.endswith("Z") or t.endswith("z"):
        t = t[:-1] + "+00:00"
    return parse_ts(t)


def auth_ids(pg):
    return pg.ids("authorization-item-")


def authorize_page(pg):
    """The authorise form may live on / or on /authorizations."""
    for p in ("/", "/authorizations"):
        pg.go(p)
        pg.wait(lambda: pg.exists("current-user"), "signed in on " + p)
        if pg.exists("authorize-handle"):
            return p
    raise AssertionError("authorize-handle was found neither on / nor on /authorizations")


@test("S2-154", "S2-155", "S2-156", "S2-170", "S2-032", "S2-005")
def test_wallet_total_available_and_held():
    auths_fx = [fx_auth("s_open", "ada", "bob", 2000, "open", in_(hours=2)),
                fx_auth("s_past", "ada", "bob", 50000, "open", in_(hours=-3)),
                fx_auth("s_cap", "ada", "bob", 700, "captured"), fx_auth("s_void", "ada", "bob", 800, "voided"),
                fx_auth("s_exp", "ada", "bob", 900, "expired", in_(hours=-2)),
                fx_auth("b_open", "bob", "cy", 300, "open", in_(hours=2))]
    world2({"ada": 10000, "bob": 500, "cy": 0}, auths=auths_fx)
    with session() as pg:
        open_as(pg, "ada")          # the page is loaded right after the reset
        pg.wait_text("wallet-balance", "100.00 EUR")
        eq(pg.attr("wallet-balance", "data-amount"), "10000", "wallet-balance is the total")
        pg.wait_text("wallet-available", "80.00 EUR")
        eq(pg.attr("wallet-available", "data-amount"), "8000", "wallet-available data-amount")
        pg.wait_text("wallet-held", "20.00 EUR")
        eq(pg.attr("wallet-held", "data-amount"), "2000", "wallet-held data-amount")
        pg.shot("home-held-1280")
    with session() as pg:
        open_as(pg, "bob")
        pg.wait_text("wallet-balance", "5.00 EUR")
        pg.wait_text("wallet-available", "2.00 EUR")             # bob holds 3.00 for cy
        pg.wait_text("wallet-held", "3.00 EUR")
    with session() as pg:
        open_as(pg, "cy")
        pg.wait_text("wallet-balance", "0.00 EUR")
        pg.wait_text("wallet-available", "0.00 EUR")
        ok(not pg.exists("wallet-held"), "wallet-held is absent when nothing is held")
    # a JPY wallet: no decimals anywhere
    world2({"ada": 5000, "bob": 0}, currency="JPY", minor_units=0,
           auths=[fx_auth("j1", "ada", "bob", 1200, "open", in_(hours=2))])
    with session() as pg:
        open_as(pg, "ada")
        pg.wait_text("wallet-balance", "5000 JPY")
        pg.wait_text("wallet-available", "3800 JPY")
        pg.wait_text("wallet-held", "1200 JPY")


@test("S2-157", "S2-158", "S2-044", "S2-045", "S2-068", "S2-154", "S2-155", "S2-156", "S2-086", "S2-087")
def test_authorize_form():
    w = world2({"ada": 10000, "bob": 0})
    ada = w["ada"]
    with session() as pg:
        open_as(pg, "ada")
        page = authorize_page(pg)
        for t in ("authorize-handle", "authorize-amount", "authorize-note", "authorize-visibility", "authorize-submit"):
            ok(pg.visible(t), "%s must be visible on %s" % (t, page))
        opts = pg.page.evaluate("() => [...document.querySelector('[data-testid=\"authorize-visibility\"]').options].map(o => o.value)")
        eq(sorted(opts), ["private", "public"], "authorize-visibility option values")
        ok(not pg.exists("authorize-error"), "no authorize-error initially")
        fill_pay(pg, "bob", "20.00", "deposit", "private", prefix="authorize")
        pg.click("authorize-submit")
        pg.wait(lambda: len(pg.posts("/authorizations")) == 1, "POST /authorizations")
        r = pg.posts("/authorizations")[0]
        ok(r["headers"].get("idempotency-key"), "Idempotency-Key on POST /authorizations")
        b = body_of(r)
        eq((b["to_handle"], b["amount"], b["note"], b["visibility"]), ("bob", 2000, "deposit", "private"), "wire body")
        pg.wait(lambda: len(all_auths(ada)) == 1, "the authorization exists on the server")
        a = all_auths(ada)
        check_auth(a[0], status="open", amount=2000, note="deposit", visibility="private")
        pg.go("/")
        pg.wait_text("wallet-balance", "100.00 EUR")
        pg.wait_text("wallet-available", "80.00 EUR")
        pg.wait_text("wallet-held", "20.00 EUR")
        # no reload needed after the action itself
        page = authorize_page(pg)
        fill_pay(pg, "bob", "10.00", "second", "public", prefix="authorize")
        pg.click("authorize-submit")
        if page == "/":
            pg.wait_text("wallet-held", "30.00 EUR")
            pg.wait_text("wallet-available", "70.00 EUR")
            pg.wait_text("wallet-balance", "100.00 EUR")
        ok(not pg.exists("authorize-error"), "no authorize-error after success")
        pg.wait(lambda: len(all_auths(ada)) == 2, "second authorization")
        n = len(pg.posts("/authorizations"))
        # refusals
        for handle, amount, why, sent in (("bob", "70.01", "above available although total would cover it", True),
                                          ("bob", "70.005", "too many decimals", False), ("bob", "abc", "not a number", False),
                                          ("bob", "", "empty", False), ("nobody", "1.00", "unknown handle", True),
                                          ("ada", "1.00", "own handle", True), ("bob", "0", "zero", True)):
            fill_pay(pg, handle, amount, "n", "public", prefix="authorize")
            pg.click("authorize-submit")
            pg.wait(lambda: pg.visible("authorize-error"), "authorize-error for " + why)
            ok(pg.it("authorize-error") != "", "authorize-error text")
            if not sent:
                pg.page.wait_for_timeout(250)
                eq(len(pg.posts("/authorizations")), n, "nothing sent for " + why)
            n = len(pg.posts("/authorizations"))
        eq(len(all_auths(ada)), 2, "refusals created nothing")
        m = me_ok(ada)
        eq((m["total"], m["held"], m["available"]), (10000, 3000, 7000), "server state")
        fill_pay(pg, "bob", "70.00", "all the rest", "public", prefix="authorize")
        pg.click("authorize-submit")
        pg.wait(lambda: not pg.visible("authorize-error"), "authorize-error clears")
        pg.wait(lambda: me_ok(ada)["available"] == 0, "everything held")
        if page == "/":
            pg.wait_text("wallet-available", "0.00 EUR")
            pg.wait_text("wallet-held", "100.00 EUR")


def screen_fixture():
    return [fx_auth("o_open", "ada", "bob", 2000, "open", in_(hours=2), note="n o_open"),
            fx_auth("i_open", "cy", "ada", 1000, "open", in_(hours=2), note="n i_open"),
            fx_auth("o_cap", "ada", "bob", 700, "captured", in_(hours=2)),
            fx_auth("o_void", "ada", "bob", 800, "voided", in_(hours=2)),
            fx_auth("i_exp", "cy", "ada", 900, "expired", in_(hours=-2)),
            fx_auth("o_clock", "ada", "cy", 500, "open", in_(hours=-3)),
            fx_auth("x_other", "bob", "cy", 400, "open", in_(hours=2))]


@test("S2-159", "S2-160", "S2-161", "S2-162", "S2-163", "S2-164", "S2-165", "S2-166", "S2-168", "S2-010", "S2-170")
def test_authorizations_screen():
    w = world2({"ada": 10000, "bob": 10000, "cy": 10000, "dee": 0}, auths=screen_fixture())
    ada, bob, cy = w["ada"], w["bob"], w["cy"]
    partial = expect(authorize(ada, "bob", 600), 201).json["authorization_id"]       # newest
    expect(capture(bob, partial, {"amount": 100, "final": False}), 201)               # open, partially captured
    inc_partial = expect(authorize(cy, "ada", 500), 201).json["authorization_id"]
    expect(capture(ada, inc_partial, {"amount": 150, "final": False}), 201)           # incoming, remaining 350
    with session() as pg:
        open_as(pg, "ada", "/authorizations")
        pg.wait(lambda: len(auth_ids(pg)) == 8, "eight authorizations listed", 10)
        api = all_auths(ada)
        eq(auth_ids(pg), [a["authorization_id"] for a in api], "DOM order equals GET /authorizations (newest first)")
        ok("x_other" not in auth_ids(pg), "authorizations of others are not listed")
        eq(len(api), 8, "eight involve ada")
        ok(pg.exists("authorization-list") and not pg.exists("empty-authorizations"), "list present, no empty state")
        for a in api:
            aid = a["authorization_id"]
            eq(pg.attr("authorization-item-" + aid, "data-status"), a["status"], "data-status of " + aid)
            eq(pg.tc("authorization-amount-" + aid).strip(), fmt(a["amount"]), "amount of " + aid)
            eq(eq_captured(pg, aid), a["status"] == "captured", "authorization-captured-* present only when captured: " + aid)
            if a["status"] == "captured":
                eq(pg.tc("authorization-captured-" + aid).strip(), fmt(a["captured_amount"]), "captured amount of " + aid)
            txt = pg.tc("authorization-expires-" + aid).strip()
            eq(rfc3339_instant(txt), parse_ts(a["expires_at"]), "expires text is the RFC 3339 expires_at of " + aid)
            incoming = a["to_user_id"] == ada.id
            outgoing = a["from_user_id"] == ada.id
            is_open = a["status"] == "open"
            eq(pg.exists("authorization-capture-" + aid), incoming and is_open, "capture button on %s (%s)" % (aid, a["status"]))
            eq(pg.exists("authorization-capture-amount-" + aid), incoming and is_open, "capture amount input on %s" % aid)
            eq(pg.exists("authorization-void-" + aid), outgoing and is_open, "void button on %s" % aid)
            if incoming and is_open:
                val = pg.value("authorization-capture-amount-" + aid)
                ok(dec_equal(val, a["remaining_amount"]), "capture amount of %s is pre-filled with the remaining amount %s, got %r" % (
                    aid, fmt(a["remaining_amount"]), val))
        eq(pg.attr("authorization-item-o_clock", "data-status"), "expired", "expired by the clock is shown as expired")
        ok(not pg.exists("authorization-captured-" + partial), "a partially captured open authorization shows no captured amount")
        eq(pg.tc("authorization-amount-" + partial).strip(), "6.00 EUR", "authorised amount, not remaining")
        ok(not pg.exists("authorization-error"), "no authorization-error initially")
        pg.shot("authorizations-ada-1280")
        # capture part of an incoming open authorization through the UI; the capture is final: remainder released
        pg.fill("authorization-capture-amount-i_open", "4.00")
        pg.click("authorization-capture-i_open")
        pg.wait(lambda: pg.attr("authorization-item-i_open", "data-status") == "captured", "i_open captured")
        pg.wait_text("authorization-captured-i_open", "4.00 EUR")
        pg.wait(lambda: not pg.exists("authorization-capture-i_open") and not pg.exists("authorization-capture-amount-i_open"),
                "capture controls gone")
        r = pg.posts("/authorizations/i_open/capture")[0]
        ok(r["headers"].get("idempotency-key"), "Idempotency-Key on capture")
        eq(body_of(r)["amount"], 400, "capture wire amount")
        ok(not pg.exists("authorization-error"), "no error after success")
        m = me_ok(cy)
        eq((m["total"], m["held"]), (10000 - 150 - 400, 350), "payer cy: 4.00 moved, remainder 6.00 released; the 5.00 hold keeps 3.50")
        # void an outgoing open authorization
        pg.click("authorization-void-o_open")
        pg.wait(lambda: pg.attr("authorization-item-o_open", "data-status") == "voided", "o_open voided")
        pg.wait(lambda: not pg.exists("authorization-void-o_open"), "void button gone")
        eq(me_ok(ada)["held"], 600 - 100, "ada: hold of o_open released, partial authorization still holds its 5.00")
        # partial receiver view
        pg.go("/authorizations")
        pg.wait(lambda: len(auth_ids(pg)) == 8, "reloaded list")
        val = pg.value("authorization-capture-amount-" + inc_partial) if pg.exists("authorization-capture-amount-" + inc_partial) else None
        ok(val is not None, "incoming partially captured open authorization keeps its capture controls")
    with session() as pg:
        open_as(pg, "bob", "/authorizations")
        pg.wait(lambda: partial in auth_ids(pg), "bob sees the partial authorization")
        val = pg.value("authorization-capture-amount-" + partial)
        ok(dec_equal(val, 500), "pre-filled with the REMAINING amount 5.00 (not the authorised 6.00): %r" % val)
        ok(not pg.exists("authorization-void-" + partial), "the receiver has no void button")
    with session() as pg:
        open_as(pg, "dee", "/authorizations")
        pg.wait_visible("empty-authorizations")
        eq(auth_ids(pg), [], "no items")


def eq_captured(pg, aid):
    return pg.exists("authorization-captured-" + aid)


@test("S2-167", "S2-165", "S2-166", "S2-164", "S2-069", "S2-045")
def test_authorization_errors_and_stale_buttons():
    fx = [fx_auth("v1", "cy", "ada", 1000, "open", in_(hours=2)), fx_auth("v2", "ada", "bob", 1000, "open", in_(hours=2)),
          fx_auth("v3", "cy", "ada", 1000, "open", in_(hours=2)), fx_auth("v4", "cy", "ada", 1000, "open", in_(hours=2)),
          fx_auth("v5", "ada", "bob", 1000, "open", in_(hours=2))]
    w = world2({"ada": 10000, "bob": 10000, "cy": 10000}, auths=fx)
    ada, bob, cy = w["ada"], w["bob"], w["cy"]
    with session() as pg:
        open_as(pg, "ada", "/authorizations")
        pg.wait(lambda: len(auth_ids(pg)) == 5, "five authorizations")
        # voided elsewhere while the capture button is visible
        ok(pg.visible("authorization-capture-v1"), "capture button visible")
        expect(void(cy, "v1"), 200)
        pg.click("authorization-capture-v1")
        pg.wait(lambda: pg.visible("authorization-error"), "authorization-error when the authorization was voided elsewhere")
        ok(pg.it("authorization-error") != "", "authorization-error text")
        pg.wait(lambda: pg.attr("authorization-item-v1", "data-status") == "voided", "list refreshed: v1 voided")
        ok(not pg.exists("authorization-capture-v1") and not pg.exists("authorization-capture-amount-v1"), "stale capture controls gone")
        # captured elsewhere while the void button is visible
        ok(pg.visible("authorization-void-v2"), "void button visible")
        expect(capture(bob, "v2", {}), 201)
        pg.click("authorization-void-v2")
        pg.wait(lambda: pg.visible("authorization-error"), "authorization-error when voiding a captured authorization")
        pg.wait(lambda: pg.attr("authorization-item-v2", "data-status") == "captured", "refreshed: v2 captured")
        ok(not pg.exists("authorization-void-v2"), "stale void button gone")
        # amount above the remaining amount, and unparsable amounts
        pg.fill("authorization-capture-amount-v3", "20.00")
        pg.click("authorization-capture-v3")
        pg.wait(lambda: pg.visible("authorization-error"), "authorization-error for an amount above the remainder")
        eq(pg.attr("authorization-item-v3", "data-status"), "open", "still open")
        ok(pg.exists("authorization-capture-v3"), "controls stay")
        n = len(pg.posts("/authorizations/v3/capture"))
        for bad in ("5.005", "abc", "1.2.3"):
            pg.fill("authorization-capture-amount-v3", bad)
            pg.click("authorization-capture-v3")
            pg.wait(lambda: pg.visible("authorization-error"), "authorization-error for %r" % bad)
            pg.page.wait_for_timeout(200)
            eq(len(pg.posts("/authorizations/v3/capture")), n, "nothing sent for %r" % bad)
            n = len(pg.posts("/authorizations/v3/capture"))
        eq(auth_by_id(ada, "v3")["captured_amount"], 0, "refusals captured nothing")
        # success clears the error
        pg.fill("authorization-capture-amount-v3", "10.00")
        pg.click("authorization-capture-v3")
        pg.wait(lambda: pg.attr("authorization-item-v3", "data-status") == "captured", "v3 captured in full")
        pg.wait(lambda: not pg.visible("authorization-error"), "authorization-error clears after a success")
        # in flight: no optimistic change
        state = {"n": 0}

        def slow(route, request):
            if request.method == "POST":
                state["n"] += 1
                pg.page.wait_for_timeout(1500)
            route.fallback()

        pg.page.route("**/authorizations/v5/void", slow)
        pg.click("authorization-void-v5")
        pg.wait(lambda: state["n"] == 1, "void in flight")
        eq(pg.attr("authorization-item-v5", "data-status"), "open", "no optimistic status change")
        pg.wait(lambda: pg.attr("authorization-item-v5", "data-status") == "voided", "voided after the response", 10)
