"""Browser probes: signup/login, balance formatting, pay and request forms, activity feed."""
import json
import re
import time

from ui_lib import *  # noqa: F401,F403


def open_as(pg, handle, path="/"):
    ui_login(pg, handle + "@example.com")
    pg.go(path)
    pg.wait_visible("current-user")


def body_of(r):
    return json.loads(r["post"])


@test("S2-025", "S2-026", "S2-027", "S2-029", "S2-030", "S2-031", "S2-010", "S2-008", "S2-009", "S2-005", "S2-006", "S2-007")
def test_signup_login_logout_and_current_user():
    world2({"ada": 1000})
    with session() as pg:
        pg.go("/signup")
        for t in ("signup-email", "signup-password", "signup-display-name", "signup-submit"):
            pg.wait_visible(t)
        ok(not pg.exists("auth-error"), "auth-error must be absent on a fresh signup page")
        pg.fill("signup-email", "New.Person+1@example.com")
        pg.fill("signup-password", "correct horse")
        pg.fill("signup-display-name", "Nina Person")
        pg.click("signup-submit")
        pg.wait(lambda: pg.visible("current-user"), "current-user after signup")
        ok("Nina Person" in pg.it("current-user"), "current-user must contain the display name: %r" % pg.it("current-user"))
        pg.go("/")
        pg.wait_visible("current-handle")
        eq(pg.tc("current-handle").strip(), "new_person_1", "current-handle is exactly the derived handle")
        # the account exists for the API with the same credentials
        r = expect(http("POST", "/auth/login", body={"email": "New.Person+1@example.com", "password": "correct horse"}), 200)
        eq(r.json["display_name"], "Nina Person", "display name stored")
        for path in ("/", "/requests", "/split", "/authorizations"):
            pg.go(path)
            pg.wait(lambda: pg.visible("current-user"), "current-user on %s after a full navigation" % path)
            ok("Nina Person" in pg.it("current-user"), "current-user text on %s: %r" % (path, pg.it("current-user")))
            eq(pg.tc("current-handle").strip(), "new_person_1", "current-handle on %s" % path)
            ok(pg.visible("logout-button"), "logout-button on %s" % path)
        # logout
        pg.go("/")
        pg.click("logout-button")
        pg.wait(lambda: not pg.visible("current-user"), "current-user gone after logout")
        pg.go("/")
        ok(not pg.exists("wallet-balance"), "the wallet must not be shown when signed out")
        ok(pg.exists("login-email") or pg.path in ("/login", "/signup"), "signed-out visitors are sent to / shown the login: at %s" % pg.path)
        # login again through the form
        ui_login(pg, "New.Person+1@example.com")
        ok("Nina Person" in pg.it("current-user"), "current-user after login")
        pg.go("/requests")
        pg.wait_visible("current-user")
        # seeded user logs in too
    with session() as pg:
        ui_login(pg, "ada@example.com")
        ok("Ada" in pg.it("current-user"), "seeded display name")
        eq(pg.tc("current-handle").strip(), "ada", "seeded handle")


@test("S2-028", "S2-025", "S2-027")
def test_auth_error_is_present_only_when_there_is_one():
    world2({"ada": 1000})
    with session() as pg:
        pg.go("/login")
        pg.wait_visible("login-email")
        ok(not pg.exists("auth-error"), "no auth-error on a fresh /login")
        for email, pw in (("ada@example.com", "wrong password"), ("ghost@example.com", "correct horse"), ("ada@example.com", "")):
            pg.fill("login-email", email)
            pg.fill("login-password", pw)
            pg.click("login-submit")
            pg.wait(lambda: pg.visible("auth-error"), "auth-error after a failed login (%r)" % email)
            ok(pg.it("auth-error") != "", "auth-error must have text")
            ok(not pg.visible("current-user"), "not signed in after a failed login")
        pg.fill("login-email", "ada@example.com")
        pg.fill("login-password", "correct horse")
        pg.click("login-submit")
        pg.wait(lambda: pg.visible("current-user"), "login after earlier failures")
        ok(not pg.visible("auth-error"), "auth-error must disappear after a successful login")
        pg.go("/")
        ok(not pg.exists("auth-error"), "no stale auth-error on later screens")
    with session() as pg:
        pg.go("/signup")
        pg.wait_visible("signup-email")
        ok(not pg.exists("auth-error"), "no auth-error on a fresh /signup")
        cases = [
            ("ada@example.com", "correct horse", "Dup", "email already registered"),
            ("short@example.com", "short", "Short", "password shorter than 8"),
            ("ada@other.org", "correct horse", "Taken", "derived handle already taken"),
            ("not-an-email", "correct horse", "Bad", "email without @"),
        ]
        for email, pw, name, why in cases:
            pg.fill("signup-email", email)
            pg.fill("signup-password", pw)
            pg.fill("signup-display-name", name)
            pg.click("signup-submit")
            pg.wait(lambda: pg.visible("auth-error"), "auth-error for signup: " + why)
            ok(pg.it("auth-error") != "", "auth-error text: " + why)
            ok(not pg.visible("current-user"), "not signed in after: " + why)
        # none of those created an account
        for email in ("short@example.com", "ada@other.org"):
            expect(http("POST", "/auth/login", body={"email": email, "password": "correct horse"}), 401, "unauthenticated")
        pg.fill("signup-email", "okay@example.com")
        pg.fill("signup-password", "correct horse")
        pg.fill("signup-display-name", "Okay")
        pg.click("signup-submit")
        pg.wait(lambda: pg.visible("current-user"), "successful signup after errors")
        ok(not pg.visible("auth-error"), "auth-error gone after success")


@test("S2-032", "S2-042", "S2-043", "S2-154", "S2-155", "S2-156")
def test_wallet_balance_formatting_and_data_amount():
    cases = [("EUR", 2, 10000, "100.00 EUR"), ("JPY", 0, 1200, "1200 JPY"), ("BHD", 3, 12345, "12.345 BHD"),
             ("EUR", 2, 0, "0.00 EUR"), ("JPY", 0, 0, "0 JPY"), ("EUR", 2, 123456789, "1234567.89 EUR"),
             ("EUR", 2, 5, "0.05 EUR"), ("BHD", 3, 7, "0.007 BHD"),
             ("EUR", 2, 9_007_199_254_740_001, "90071992547400.01 EUR")]
    for cur, mu, amount, text in cases:
        world2({"ada": amount, "bob": 0}, currency=cur, minor_units=mu)
        with session() as pg:
            open_as(pg, "ada")
            pg.wait_text("wallet-balance", text)
            eq(pg.attr("wallet-balance", "data-amount"), str(amount), "data-amount for %s" % text)
            ok(pg.exists("wallet-available"), "wallet-available is always shown")
            pg.wait_text("wallet-available", text)
            eq(pg.attr("wallet-available", "data-amount"), str(amount), "available data-amount")
            ok(not pg.exists("wallet-held"), "wallet-held is absent when held is zero")
    # the formatted amount never carries a sign, grouping or float noise
    ok(fmt(9_007_199_254_740_001, 2, "EUR") == "90071992547400.01 EUR", "(self-check of the expected formatter)")


@test("S2-033", "S2-034", "S2-035", "S2-039", "S2-040", "S2-041", "S2-044", "S2-046", "S2-047", "S2-048", "S2-049", "S2-050",
      "S2-068", "S2-005")
def test_pay_form_flow_keeps_values_and_retries_with_same_key():
    _tokens.clear()
    world2({"ada": 10000, "bob": 2500, "cy": 0})
    with session() as pg:
        open_as(pg, "ada")
        pg.wait_text("wallet-balance", "100.00 EUR")
        for t in ("pay-handle", "pay-amount", "pay-note", "pay-visibility", "pay-submit", "request-handle", "request-amount",
                  "request-note", "request-submit"):
            ok(pg.visible(t), "%s must be visible on /" % t)
        opts = pg.page.evaluate("() => [...document.querySelector('[data-testid=\"pay-visibility\"]').options].map(o => o.value)")
        eq(sorted(opts), ["private", "public"], "pay-visibility option values")
        ok(not pg.exists("pay-error"), "no pay-error initially")
        ok(pg.exists("empty-activity"), "an empty feed shows empty-activity")
        fill_pay(pg, "bob", "15.00", "lunch", "private")
        pg.click("pay-submit")
        pg.wait_text("wallet-balance", "85.00 EUR")
        eq(pg.attr("wallet-balance", "data-amount"), "8500", "data-amount after paying")
        pg.wait(lambda: len(activity_ids(pg)) == 1, "one feed item")
        pid = activity_ids(pg)[0]
        eq(pg.attr("activity-item-" + pid, "data-visibility"), "private", "data-visibility")
        parties = pg.it("activity-parties-" + pid)
        ok("ada" in parties and "bob" in parties, "parties text contains both handles: %r" % parties)
        eq(pg.tc("activity-amount-" + pid).strip(), "15.00 EUR", "activity amount")
        eq(pg.tc("activity-note-" + pid), "lunch", "activity note textContent")
        ok(not pg.exists("empty-activity"), "empty-activity is gone")
        ok(not pg.exists("pay-error"), "no pay-error after success")
        eq((pg.value("pay-handle"), pg.value("pay-amount"), pg.value("pay-note"), pg.value("pay-visibility")),
           ("bob", "15.00", "lunch", "private"), "the pay form keeps its values after success")
        posts = pg.posts("/payments")
        eq(len(posts), 1, "exactly one POST /payments")
        key1 = posts[0]["headers"].get("idempotency-key")
        ok(key1, "POST /payments must carry an Idempotency-Key")
        b = body_of(posts[0])
        eq((b["to_handle"], b["amount"], b["note"], b["visibility"]), ("bob", 1500, "lunch", "private"), "wire body")
        ok(is_int(b["amount"]), "amount is submitted as integer minor units")
        # submit again without changing anything: no second payment
        pg.click("pay-submit")
        pg.page.wait_for_timeout(1200)
        pg.settle()
        eq(pg.tc("wallet-balance").strip(), "85.00 EUR", "wallet-balance falls once")
        eq(len(activity_ids(pg)), 1, "the feed contains one payment")
        ok(not pg.exists("pay-error"), "pay-error absent after an unchanged resubmit")
        for r in pg.posts("/payments"):
            eq((r["headers"].get("idempotency-key"), body_of(r)), (key1, b), "any repeat POST must be a same-key same-body retry")
        eq(ada_bal(), 8500, "server balance")
        eq(len(http_json("/activity", "ada")["payments"]), 1, "server has one payment")
        # changing any single field makes a new payment with a new key
        keys = {key1}
        expected = 8500
        for field, change, minor in (("note", "lunch 2", 1500), ("amount", "16.00", 1600), ("visibility", "public", 1600), ("handle", "cy", 1600)):
            if field == "note":
                pg.fill("pay-note", change)
            elif field == "amount":
                pg.fill("pay-amount", change)
            elif field == "visibility":
                pg.select("pay-visibility", change)
            else:
                pg.fill("pay-handle", change)
            pg.click("pay-submit")
            expected -= minor
            pg.wait_text("wallet-balance", fmt(expected))
            posts = pg.posts("/payments")
            k = posts[-1]["headers"].get("idempotency-key")
            ok(k and k not in keys, "changing %s must send a new payment with a new Idempotency-Key" % field)
            keys.add(k)
            eq(body_of(posts[-1])["amount"], minor, "amount on the wire after changing " + field)
            ok(not pg.exists("pay-error"), "no pay-error after changing " + field)
        pg.wait(lambda: len(activity_ids(pg)) == 5, "five payments in the feed")
        newest = activity_ids(pg)[0]
        eq(pg.attr("activity-item-" + newest, "data-visibility"), "public", "newest item (public, to cy)")


def ada_bal():
    return http_json("/me", "ada")["balance"]


def http_json(path, who):
    tok = _tokens.get(who)
    if tok is None:
        tok = login(who + "@example.com").token
        _tokens[who] = tok
    return expect(http("GET", path, token=tok), 200).json


_tokens = {}


@test("S2-044", "S2-045", "S2-036", "S2-033", "S2-035")
def test_amount_input_rules_on_the_wire():
    _tokens.clear()
    world2({"ada": 1_000_000, "bob": 0})
    with session() as pg:
        open_as(pg, "ada")
        pg.wait_text("wallet-balance", "10000.00 EUR")
        spent = 0
        for text, minor in (("15.00", 1500), ("15", 1500), ("15.5", 1550), ("15.0", 1500), ("0.05", 5), ("100", 10000), ("1.25", 125)):
            n = len(pg.posts("/payments"))
            fill_pay(pg, "bob", text, "amt " + text)
            pg.click("pay-submit")
            spent += minor
            pg.wait(lambda: len(pg.posts("/payments")) == n + 1, "a POST for amount %r" % text)
            eq(body_of(pg.posts("/payments")[-1])["amount"], minor, "wire amount for %r" % text)
            pg.wait_text("wallet-balance", fmt(1_000_000 - spent))
        n = len(pg.posts("/payments"))
        for bad in ("15.005", "abc", "", "1.2.3", "1e2", "-5", "15.001", "0x10", "1,5.0"):
            fill_pay(pg, "bob", bad, "bad amount")
            pg.click("pay-submit")
            pg.wait(lambda: pg.visible("pay-error"), "pay-error for amount %r" % bad)
            ok(pg.it("pay-error") != "", "pay-error needs text")
            pg.page.wait_for_timeout(250)
            eq(len(pg.posts("/payments")), n, "nothing may be sent for the invalid amount %r" % bad)
            eq(pg.tc("wallet-balance").strip(), fmt(1_000_000 - spent), "balance unchanged after %r" % bad)
        # a valid amount clears the error
        fill_pay(pg, "bob", "2.00", "fine")
        pg.click("pay-submit")
        pg.wait(lambda: not pg.visible("pay-error"), "pay-error disappears after a valid payment")
        pg.wait_text("wallet-balance", fmt(1_000_000 - spent - 200))
    for cur, mu, good, minor, bads in (("JPY", 0, "15", 15, ("15.5", "0.5", "1.0")), ("BHD", 3, "1.234", 1234, ("1.2345", "0.0005"))):
        world2({"ada": 1_000_000, "bob": 0}, currency=cur, minor_units=mu)
        with session() as pg:
            open_as(pg, "ada")
            for bad in bads:
                fill_pay(pg, "bob", bad, "bad")
                pg.click("pay-submit")
                pg.wait(lambda: pg.visible("pay-error"), "pay-error for %s amount %r" % (cur, bad))
                pg.page.wait_for_timeout(200)
                eq(len(pg.posts("/payments")), 0, "nothing sent for %s %r" % (cur, bad))
            fill_pay(pg, "bob", good, "ok")
            pg.click("pay-submit")
            pg.wait(lambda: len(pg.posts("/payments")) == 1, "valid %s amount sent" % cur)
            eq(body_of(pg.posts("/payments")[0])["amount"], minor, "%s wire amount" % cur)
            pg.wait_text("wallet-balance", fmt(1_000_000 - minor, mu, cur))


@test("S2-036", "S2-035", "S2-039", "S2-069")
def test_pay_refusals_show_pay_error_and_keep_the_form():
    _tokens.clear()
    world2({"ada": 1000, "bob": 0})
    with session() as pg:
        open_as(pg, "ada")
        pg.wait_text("wallet-balance", "10.00 EUR")
        for handle, amount, why in (("bob", "10.01", "insufficient funds"), ("nobody", "1.00", "unknown handle"),
                                    ("ada", "1.00", "own handle"), ("bob", "0", "zero amount"), ("bob", "0.00", "zero amount")):
            fill_pay(pg, handle, amount, "keep me " + why, "private")
            pg.click("pay-submit")
            pg.wait(lambda: pg.visible("pay-error"), "pay-error for " + why)
            ok(pg.it("pay-error") != "", "pay-error text: " + why)
            eq(pg.tc("wallet-balance").strip(), "10.00 EUR", "balance unchanged: " + why)
            eq((pg.value("pay-handle"), pg.value("pay-amount"), pg.value("pay-note"), pg.value("pay-visibility")),
               (handle, amount, "keep me " + why, "private"), "inputs preserved: " + why)
            ok(pg.exists("empty-activity") or not activity_ids(pg), "no payment appeared: " + why)
        eq(http_json("/me", "ada")["balance"], 1000, "server unchanged")
        # held in flight: no optimistic update, then the success arrives
        state = {"n": 0}

        def slow(route, request):
            if request.method == "POST":
                state["n"] += 1
                pg.page.wait_for_timeout(1500)
            route.fallback()

        pg.page.route(re.compile(r".*/payments$"), slow)
        fill_pay(pg, "bob", "3.00", "slow", "public")
        pg.click("pay-submit")
        pg.wait(lambda: state["n"] == 1, "the POST is in flight")
        eq(pg.tc("wallet-balance").strip(), "10.00 EUR", "no optimistic balance change while the write is in flight")
        eq(len(activity_ids(pg)), 0, "no optimistic feed item while the write is in flight")
        pg.wait_text("wallet-balance", "7.00 EUR", 10)
        pg.page.unroute(re.compile(r".*/payments$"), slow)
        pg.wait(lambda: len(activity_ids(pg)) == 1, "feed item after success")
        ok(not pg.exists("pay-error"), "no error after success")


@test("S2-037", "S2-038", "S2-045", "S2-068")
def test_request_form():
    _tokens.clear()
    world2({"ada": 5000, "bob": 0})
    with session() as pg:
        open_as(pg, "bob")
        ok(not pg.exists("request-error"), "no request-error initially")
        pg.fill("request-handle", "ada")
        pg.fill("request-amount", "12.00")
        pg.fill("request-note", "taxi")
        pg.click("request-submit")
        pg.wait(lambda: len(pg.posts("/requests")) == 1, "POST /requests sent")
        r = pg.posts("/requests")[0]
        ok(r["headers"].get("idempotency-key"), "Idempotency-Key on POST /requests")
        eq((body_of(r)["payer_handle"], body_of(r)["amount"], body_of(r)["note"]), ("ada", 1200, "taxi"), "request wire body")
        ada = login("ada@example.com", handle="ada")
        pg.wait(lambda: len(ada.all_requests(direction="incoming")) == 1, "request exists on the server")
        q = ada.all_requests(direction="incoming")[0]
        eq((q["amount"], q["status"], q["note"], q["requester_handle"]), (1200, "pending", "taxi", "bob"), "created request")
        ok(not pg.exists("request-error"), "no request-error after success")
        n = len(pg.posts("/requests"))
        for handle, amount, why, sent in (("bob", "1.00", "own handle", True), ("nobody", "1.00", "unknown handle", True),
                                          ("ada", "1.005", "too many decimals", False), ("ada", "abc", "not a number", False),
                                          ("ada", "", "empty amount", False), ("ada", "0", "zero amount", True)):
            pg.fill("request-handle", handle)
            pg.fill("request-amount", amount)
            pg.click("request-submit")
            pg.wait(lambda: pg.visible("request-error"), "request-error for " + why)
            ok(pg.it("request-error") != "", "request-error text")
            if not sent:
                pg.page.wait_for_timeout(200)
                eq(len([x for x in pg.posts("/requests")]), n, "nothing sent for " + why)
            n = len(pg.posts("/requests"))
        eq(len(ada.all_requests(direction="incoming")), 1, "refused requests created nothing")
        pg.fill("request-handle", "ada")
        pg.fill("request-amount", "3.50")
        pg.click("request-submit")
        pg.wait(lambda: not pg.visible("request-error"), "request-error clears after a success")
        pg.wait(lambda: len(ada.all_requests(direction="incoming")) == 2, "second request created")


@test("S2-046", "S2-047", "S2-048", "S2-049", "S2-050", "S2-051", "S2-052")
def test_activity_feed_contents_order_and_privacy():
    _tokens.clear()
    w = world2({"ada": 100000, "bob": 1000, "cy": 1000, "dee": 1000, "eve": 0})
    ada, bob, cy, dee = w["ada"], w["bob"], w["cy"], w["dee"]
    xss = '<img src=x onerror="window.__xss=1"> <script>window.__xss=2</script>'
    plan = [(ada, "bob", 150, "coffee", "public"), (bob, "cy", 275, "secret", "private"), (cy, "ada", 5, "", "public"),
            (ada, "dee", 12345, " spaced <b>x</b> & \U0001F600 ", "private"), (dee, "bob", 99, xss, "public")]
    ids = []
    for src, to, amt, note, vis in plan:
        ids.append(expect(src.pay(to, amt, note=note, visibility=vis), 201).json["payment_id"])
        time.sleep(1.1)
    meta = {i: p for i, p in zip(ids, plan)}
    for who in ("ada", "bob", "cy", "dee", "eve"):
        u = w[who]
        api = u.all_payments()
        with session() as pg:
            open_as(pg, who)
            pg.wait(lambda: len(activity_ids(pg)) == len(api), "%s: %d feed items" % (who, len(api)), 10)
            dom = activity_ids(pg)
            eq(dom, [p["payment_id"] for p in api], "%s: feed items and order (newest first) equal GET /activity" % who)
            for p in api:
                pid = p["payment_id"]
                eq(pg.attr("activity-item-" + pid, "data-visibility"), p["visibility"], "%s: data-visibility of %s" % (who, pid))
                parties = pg.it("activity-parties-" + pid)
                ok(p["from_handle"] in parties and p["to_handle"] in parties, "%s: parties %r" % (who, parties))
                eq(pg.tc("activity-amount-" + pid).strip(), fmt(p["amount"]), "%s: amount of %s" % (who, pid))
                ok(pg.exists("activity-note-" + pid), "%s: note element must exist even when empty" % who)
                eq(pg.tc("activity-note-" + pid), p["note"], "%s: note textContent of %s" % (who, pid))
            # private payments of other people are absent
            for pid, (src, to, amt, note, vis) in meta.items():
                party = who in (src.handle, to)
                if vis == "private" and not party:
                    ok(pid not in dom, "%s must not see the private payment %s" % (who, pid))
                if vis == "public" or party:
                    ok(pid in dom, "%s must see payment %s" % (who, pid))
            eq(pg.page.evaluate("() => window.__xss === undefined"), True, "a note must be rendered as text, not executed")
            eq(pg.dialogs, [], "no dialog from injected markup")
            ok(not pg.exists("empty-activity"), "%s: empty-activity must not show with items" % who)
            if who == "ada":
                pg.shot("home-ada-1280")
    # empty feed: only private payments exist and the viewer is not a party
    w = world2({"ada": 1000, "bob": 0, "cy": 0})
    expect(w["ada"].pay("bob", 5, visibility="private"), 201)
    with session() as pg:
        open_as(pg, "cy")
        pg.wait_visible("empty-activity")
        eq(activity_ids(pg), [], "no items")
        ok(not pg.exists("activity-list") or pg.count("activity-list") == 1, "list absent or empty")
    with session() as pg:
        open_as(pg, "ada")
        pg.wait(lambda: len(activity_ids(pg)) == 1, "ada sees her private payment")
        ok(not pg.exists("empty-activity"), "no empty state with an item")
