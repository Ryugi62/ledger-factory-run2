import json, urllib.request, sys, time
from playwright.sync_api import sync_playwright
B = sys.argv[1]; OUT = sys.argv[2]
def call(m, p, body=None, tok=None, key=None):
    h = {"Content-Type": "application/json"}
    if tok: h["Authorization"] = "Bearer " + tok
    if key: h["Idempotency-Key"] = key
    r = urllib.request.Request(B + p, method=m, data=json.dumps(body).encode() if body is not None else None, headers=h)
    try:
        with urllib.request.urlopen(r) as x: d = x.read(); return x.status, (json.loads(d) if d else None)
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read())
fx = {"currency": "JPY", "minor_units": 0, "users": [
  {"id": "u_a", "email": "a@x.io", "password": "correct horse", "display_name": "Ada Lovelace", "handle": "ada", "balance": 12000},
  {"id": "u_b", "email": "b@x.io", "password": "correct horse", "display_name": "Bob", "handle": "bob", "balance": 500}],
  "payments": [{"id": "p_1", "from_user_id": "u_b", "to_user_id": "u_a", "amount": 300, "note": "lunch 🍜", "visibility": "private"}],
  "requests": [{"id": "rq_1", "requester_id": "u_b", "payer_id": "u_a", "amount": 700, "note": "taxi", "status": "pending"}],
  "authorizations": [{"id": "a_1", "from_user_id": "u_a", "to_user_id": "u_b", "amount": 2000, "note": "deposit", "visibility": "public", "status": "open", "expires_at": "2099-01-01T00:00:00+00:00"},
                     {"id": "a_2", "from_user_id": "u_b", "to_user_id": "u_a", "amount": 400, "note": "", "visibility": "public", "status": "open", "expires_at": "2099-01-01T00:00:00+00:00"}]}
print("reset", call("POST", "/_test/reset", fx))
tok = call("POST", "/auth/login", {"email": "a@x.io", "password": "correct horse"})[1]["token"]
res = {}
with sync_playwright() as p:
    b = p.chromium.launch()
    for w in (375, 1280):
        pg = b.new_page(viewport={"width": w, "height": 900})
        pg.goto(B + "/login"); pg.evaluate("t => localStorage.setItem('pocketful.token', t)", tok)
        for route in ("/", "/requests", "/split", "/authorizations"):
            pg.goto(B + route); pg.wait_for_timeout(500)
            if route == "/split":
                pg.fill("[data-testid=split-amount]", "1000"); pg.fill("[data-testid=split-handles]", "bob, ada")
                pg.wait_for_timeout(100)
            sw = pg.evaluate("document.documentElement.scrollWidth")
            res[f"{route}@{w} scrollWidth"] = sw
            pg.screenshot(path=f"{OUT}/{route.strip('/') or 'home'}-{w}.png", full_page=True)
        pg.close()
    pg = b.new_page(viewport={"width": 1280, "height": 900})
    pg.goto(B + "/login"); pg.evaluate("t => localStorage.setItem('pocketful.token', t)", tok)
    pg.goto(B + "/"); pg.wait_for_selector("[data-testid=wallet-available]")
    g = lambda t: pg.locator(f"[data-testid={t}]")
    res["balance"] = (g("wallet-balance").text_content(), g("wallet-balance").get_attribute("data-amount"))
    res["available"] = g("wallet-available").text_content(); res["held"] = g("wallet-held").text_content()
    res["handle"] = g("current-handle").text_content(); res["user"] = g("current-user").text_content()
    # decimal rule (JPY: no decimals)
    g("pay-handle").fill("bob"); g("pay-amount").fill("15.5"); g("pay-submit").click(); pg.wait_for_timeout(300)
    res["jpy 15.5 -> pay-error"] = g("pay-error").count()
    # double submit
    g("pay-amount").fill("100"); g("pay-note").fill("x"); g("pay-submit").click(); pg.wait_for_timeout(500)
    g("pay-submit").click(); pg.wait_for_timeout(500)
    res["after double submit balance"] = g("wallet-balance").get_attribute("data-amount")
    res["pay-error after double"] = g("pay-error").count()
    # uncertain: commit but drop response
    state = {"n": 0}
    def handler(route):
        state["n"] += 1
        if state["n"] == 1:
            route.fetch(); route.abort()
        else:
            route.continue_()
    pg.route("**/payments", handler)
    g("pay-amount").fill("250"); g("pay-submit").click(); pg.wait_for_timeout(500)
    res["uncertain shown"] = g("pay-uncertain").count(); res["pay-error during uncertain"] = g("pay-error").count()
    g("pay-submit").click(); pg.wait_for_timeout(700)
    res["after retry uncertain/error"] = (g("pay-uncertain").count(), g("pay-error").count())
    res["after retry balance"] = g("wallet-balance").get_attribute("data-amount")
    pg.screenshot(path=f"{OUT}/home-after-1280.png", full_page=True)
    b.close()
print("me", call("GET", "/me", tok=tok))
for k, v in res.items(): print(k, "=", v)
