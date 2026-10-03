"""Browser probes: competing clients, lost responses (uncertain outcomes), upgrade by export/import."""
import json
import re

from ui_lib import *  # noqa: F401,F403
from u_wallet import open_as, body_of

PAY_URL = re.compile(r".*/payments$")


def two_sessions(handle):
    """A second API session for the same user (another client)."""
    return login(handle + "@example.com", handle=handle)


@test("S2-071", "S2-079", "S2-068", "S2-155", "S2-156")
def test_wallet_refresh_updates_without_clearing_the_form():
    w = world2({"ada": 10000, "bob": 0, "cy": 5000})
    cy = w["cy"]
    other = two_sessions("ada")
    with session() as pg:
        open_as(pg, "ada")
        pg.wait_text("wallet-balance", "100.00 EUR")
        ok(pg.visible("wallet-refresh"), "wallet-refresh must be visible on /")
        fill_pay(pg, "bo", "1.5", "draft note", "private")
        pg.fill("request-handle", "cy")
        expect(cy.pay("ada", 200), 201)                       # another client pays ada
        expect(authorize(other, "bob", 1000), 201)            # and another client of ada places a hold
        pg.click("wallet-refresh")
        pg.wait_text("wallet-balance", "102.00 EUR")
        pg.wait_text("wallet-available", "92.00 EUR")
        pg.wait_text("wallet-held", "10.00 EUR")
        pg.wait(lambda: len(activity_ids(pg)) == 1, "the new payment appears in the feed")
        eq((pg.value("pay-handle"), pg.value("pay-amount"), pg.value("pay-note"), pg.value("pay-visibility")),
           ("bo", "1.5", "draft note", "private"), "wallet-refresh must not clear the pay form")
        eq(pg.value("request-handle"), "cy", "nor the request form")
        eq(len(pg.posts("/payments")), 0, "refresh sends no payment")
        ok(not pg.exists("pay-error"), "no pay-error")
        # a second refresh after the hold is voided
        a = all_auths(other)[0]["authorization_id"]
        expect(void(other, a), 200)
        pg.click("wallet-refresh")
        pg.wait(lambda: not pg.exists("wallet-held"), "wallet-held disappears when nothing is held")
        pg.wait_text("wallet-available", "102.00 EUR")


@test("S2-072", "S2-079", "S2-071")
def test_latest_refresh_wins_when_responses_arrive_out_of_order():
    w = world2({"ada": 10000, "bob": 0, "cy": 5000})
    cy = w["cy"]
    other = two_sessions("ada")
    with session() as pg:
        open_as(pg, "ada")
        pg.wait_text("wallet-balance", "100.00 EUR")
        phase = {"p": 0, "held": 0}

        def handler(route, request):
            if request.resource_type in ("fetch", "xhr") and request.method == "GET" and phase["p"] == 1:
                real = route.fetch()            # the stale snapshot: taken now, before the other client acts
                phase["held"] += 1
                pg.page.wait_for_timeout(2500)
                route.fulfill(response=real)
            else:
                route.fallback()

        pg.page.route("**/*", handler)
        phase["p"] = 1
        pg.click("wallet-refresh")                                 # refresh 1: its responses are delayed (stale data)
        pg.wait(lambda: phase["held"] >= 1, "refresh 1 reads are in flight")
        pg.page.wait_for_timeout(400)                              # all of refresh 1's reads have taken their snapshot
        phase["p"] = 2
        expect(cy.pay("ada", 500), 201)                            # state changes after refresh 1 read it
        expect(authorize(other, "bob", 1500), 201)
        try:
            pg.click("wallet-refresh", timeout=1500)               # refresh 2: fast, newer data (may be refused if the button is busy)
        except Exception:  # noqa
            pass
        new = "105.00 EUR"
        seen_new = False
        reverted = []
        for _ in range(60):                                        # ~3.3 s covering the arrival of the stale responses
            pg.page.wait_for_timeout(55)
            cur = pg.tc("wallet-balance").strip() if pg.exists("wallet-balance") else None
            if cur == new:
                seen_new = True
            elif seen_new and cur is not None:
                reverted.append(cur)
        ok(not reverted, "a delayed earlier read overwrote the later refresh: balance went back to %r" % (reverted[:2],))
        pg.page.unroute("**/*", handler)
        phase["p"] = 0
        if not seen_new:                                           # the button was busy: an explicit refresh must now show it
            pg.click("wallet-refresh")
        pg.wait_text("wallet-balance", new)
        pg.wait_text("wallet-available", "90.00 EUR")
        pg.wait_text("wallet-held", "15.00 EUR")
        pg.wait(lambda: len(activity_ids(pg)) == 1, "the feed shows the newer payment")
        pg.page.wait_for_timeout(300)
        eq(pg.tc("wallet-balance").strip(), new, "final balance")
        eq(len(activity_ids(pg)), 1, "final feed")


@test("S2-073", "S2-069", "S2-036", "S2-091")
def test_refused_payment_after_another_client_spent_the_balance():
    w = world2({"ada": 5000, "bob": 0, "cy": 0})
    other = two_sessions("ada")
    with session() as pg:
        open_as(pg, "ada")
        pg.wait_text("wallet-balance", "50.00 EUR")
        expect(other.pay("cy", 4500, note="elsewhere"), 201)            # spent by another client after this browser read it
        fill_pay(pg, "bob", "20.00", "too much now", "private")
        pg.click("pay-submit")
        pg.wait(lambda: pg.visible("pay-error"), "pay-error for the refused payment")
        ok(not pg.exists("pay-uncertain") or pg.it("pay-uncertain") == "" or not pg.visible("pay-uncertain"), "a refusal is not an uncertain outcome")
        pg.wait_text("wallet-balance", "5.00 EUR")
        pg.wait(lambda: len(activity_ids(pg)) == 1, "the feed is refreshed and shows the other client's payment")
        eq((pg.value("pay-handle"), pg.value("pay-amount"), pg.value("pay-note"), pg.value("pay-visibility")),
           ("bob", "20.00", "too much now", "private"), "all pay inputs preserved after the refusal")
        eq(w["bob"].balance, 0, "bob got nothing")
        # refused because of a hold placed elsewhere: available, not total, decides
        w2 = world2({"ada": 5000, "bob": 0})
    with session() as pg:
        other = two_sessions("ada")
        open_as(pg, "ada")
        pg.wait_text("wallet-available", "50.00 EUR")
        expect(authorize(other, "bob", 4500), 201)
        fill_pay(pg, "bob", "20.00", "held elsewhere", "public")
        pg.click("pay-submit")
        pg.wait(lambda: pg.visible("pay-error"), "pay-error when the money is held by another client's authorization")
        pg.wait_text("wallet-available", "5.00 EUR")
        pg.wait_text("wallet-held", "45.00 EUR")
        pg.wait_text("wallet-balance", "50.00 EUR")
        eq(pg.value("pay-note"), "held elsewhere", "inputs preserved")


def lost_response_scenario(after_commit):
    """Return (pg-driven) helper state: the first POST /payments loses its response."""
    state = {"n": 0}

    def handler(route, request):
        if request.method == "POST":
            state["n"] += 1
            if state["n"] == 1:
                if after_commit:
                    route.fetch()                       # the server commits ...
                route.abort("connectionreset")          # ... the browser never sees the answer
                return
        route.fallback()

    return state, handler


@test("S2-075", "S2-076", "S2-077", "S2-041", "S2-040", "S2-019")
def test_lost_response_is_uncertain_and_retry_moves_money_once():
    for after_commit in (True, False):
        w = world2({"ada": 10000, "bob": 0})
        ada = w["ada"]
        with session() as pg:
            open_as(pg, "ada")
            pg.wait_text("wallet-balance", "100.00 EUR")
            state, handler = lost_response_scenario(after_commit)
            pg.page.route(PAY_URL, handler)
            fill_pay(pg, "bob", "15.00", "lost one", "public")
            pg.click("pay-submit")
            pg.wait(lambda: pg.visible("pay-uncertain"), "pay-uncertain after a lost response (commit=%s)" % after_commit)
            ok(pg.it("pay-uncertain") != "", "pay-uncertain needs text")
            ok(not pg.visible("pay-error"), "an unknown outcome is not a rejection: pay-error must be absent")
            eq((pg.value("pay-handle"), pg.value("pay-amount"), pg.value("pay-note"), pg.value("pay-visibility")),
               ("bob", "15.00", "lost one", "public"), "the form stays as typed, ready to retry")
            eq(ada.balance, 8500 if after_commit else 10000, "server state (committed=%s)" % after_commit)
            pg.shot("home-uncertain-1280") if after_commit else None
            # retry: unchanged form, same key and body
            pg.click("pay-submit")
            pg.wait(lambda: not pg.visible("pay-uncertain"), "pay-uncertain disappears after a successful retry")
            ok(not pg.visible("pay-error"), "no pay-error after the retry")
            pg.wait_text("wallet-balance", "85.00 EUR")
            pg.wait(lambda: len(activity_ids(pg)) == 1, "exactly one payment in the feed")
            posts = pg.posts("/payments")
            ok(len(posts) >= 2, "the retry must actually be sent")
            first, second = posts[0], posts[-1]
            ok(first["headers"].get("idempotency-key"), "key on the first attempt")
            eq(second["headers"].get("idempotency-key"), first["headers"].get("idempotency-key"), "the retry uses the same Idempotency-Key")
            eq(body_of(second), body_of(first), "and the same body")
            eq(ada.balance, 8500, "money moved exactly once (committed=%s)" % after_commit)
            eq(len(ada.all_payments()), 1, "one payment on the server")
            # changing a field after an uncertain outcome is a new payment with a new key
    # uncertain, then the user edits a field
    w = world2({"ada": 10000, "bob": 0})
    ada = w["ada"]
    with session() as pg:
        open_as(pg, "ada")
        state, handler = lost_response_scenario(True)
        pg.page.route(PAY_URL, handler)
        fill_pay(pg, "bob", "15.00", "first", "public")
        pg.click("pay-submit")
        pg.wait(lambda: pg.visible("pay-uncertain"), "pay-uncertain")
        pg.fill("pay-note", "second")
        pg.click("pay-submit")
        pg.wait(lambda: not pg.visible("pay-uncertain"), "uncertain state cleared by the new submission")
        pg.wait_text("wallet-balance", "70.00 EUR")
        posts = pg.posts("/payments")
        ok(posts[-1]["headers"].get("idempotency-key") != posts[0]["headers"].get("idempotency-key"), "a changed field uses a new key")
        eq(len(ada.all_payments()), 2, "both payments exist (the first had been committed)")


@test("S2-081", "S2-082", "S2-083", "S2-085", "S2-075", "S2-076", "S2-172")
def test_browser_survives_export_import_and_recovers_the_lost_payment():
    for after_commit in (True, False):
        reqs = [{"id": "rq_up", "requester_id": "u_bob", "payer_id": "u_ada", "amount": 500, "note": "pending", "status": "pending"}]
        w = world2({"ada": 10000, "bob": 0, "cy": 5000}, requests=reqs)
        ada = w["ada"]
        with session() as pg:
            open_as(pg, "ada")
            pg.wait_text("wallet-balance", "100.00 EUR")
            state, handler = lost_response_scenario(after_commit)
            pg.page.route(PAY_URL, handler)
            fill_pay(pg, "bob", "15.00", "across the upgrade", "private")
            pg.click("pay-submit")
            pg.wait(lambda: pg.visible("pay-uncertain"), "pay-uncertain")
            ex = expect(http("GET", "/_test/export"), 200, msg="export")
            # the destination is something else entirely when the old state arrives
            reset(fixture([fx_user("ada", 1, uid="u_stranger", email="ada@example.com"), fx_user("dest", 9)]))
            expect(http("POST", "/_test/import", body=ex.json), 204, msg="import")
            # the browser did not reload: still signed in, form and retry identity intact
            ok(pg.visible("current-user"), "still signed in after the upgrade")
            eq((pg.value("pay-handle"), pg.value("pay-amount"), pg.value("pay-note"), pg.value("pay-visibility")),
               ("bob", "15.00", "across the upgrade", "private"), "form survives the upgrade")
            pg.click("pay-submit")
            pg.wait(lambda: not pg.visible("pay-uncertain"), "the UI recovers the original payment (commit=%s)" % after_commit)
            ok(not pg.visible("pay-error"), "no pay-error after recovery")
            pg.wait_text("wallet-balance", "85.00 EUR")
            pg.wait(lambda: len(activity_ids(pg)) == 1, "one payment in the refreshed feed")
            posts = pg.posts("/payments")
            eq(posts[-1]["headers"].get("idempotency-key"), posts[0]["headers"].get("idempotency-key"), "same key after the upgrade")
            eq(body_of(posts[-1]), body_of(posts[0]), "same body after the upgrade")
            me = http_me(ada)
            eq(me["balance"], 8500, "money moved exactly once, from the imported balance")
            ok(pg.path == "/", "no redirect to a login screen: at %s" % pg.path)
            # explicit refresh still works with the old session
            pg.click("wallet-refresh")
            pg.wait_text("wallet-balance", "85.00 EUR")
            # existing pending request is payable through the request screen
            pg.go("/requests")
            pg.wait(lambda: pg.visible("request-pay-rq_up"), "the imported pending request has a pay button")
            pg.click("request-pay-rq_up")
            pg.wait(lambda: pg.attr("request-item-rq_up", "data-status") == "paid", "request paid through the screen")
            eq(http_me(ada)["balance"], 8000, "request payment moved money once")


def http_me(u):
    return expect(http("GET", "/me", token=u.token), 200).json
