import json, urllib.request, sys
from playwright.sync_api import sync_playwright
B = sys.argv[1]
def call(m, p, body=None):
    r = urllib.request.Request(B + p, method=m, data=json.dumps(body).encode() if body is not None else None, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r) as x: d = x.read(); return json.loads(d) if d else None
for label, extra in (("data", True), ("empty", False)):
    fx = {"currency": "EUR", "minor_units": 2, "users": [
      {"id": "u_a", "email": "a@x.io", "password": "correct horse", "display_name": "Ada", "handle": "ada", "balance": 12000},
      {"id": "u_b", "email": "b@x.io", "password": "correct horse", "display_name": "Bob", "handle": "bob", "balance": 500}]}
    if extra:
        fx["requests"] = [{"id": "rq_1", "requester_id": "u_a", "payer_id": "u_b", "amount": 700, "note": "", "status": "pending"},
                          {"id": "rq_2", "requester_id": "u_b", "payer_id": "u_a", "amount": 70, "note": "x", "status": "paid"}]
        fx["authorizations"] = [{"id": "a_1", "from_user_id": "u_a", "to_user_id": "u_b", "amount": 2000, "status": "captured", "captured_amount": 1500, "expires_at": "2020-01-01T00:00:00+00:00"}]
    call("POST", "/_test/reset", fx)
    tok = call("POST", "/auth/login", {"email": "a@x.io", "password": "correct horse"})["token"]
    with sync_playwright() as p:
        b = p.chromium.launch(); pg = b.new_page()
        pg.goto(B + "/login"); pg.evaluate("t => localStorage.setItem('pocketful.token', t)", tok)
        for route in ("/", "/requests", "/split", "/authorizations", "/login", "/signup"):
            pg.goto(B + route); pg.wait_for_timeout(600)
            t = pg.evaluate("document.body.innerText")
            bad = [w for w in ("null", "undefined", "NaN", "[object") if w in t]
            print(label, route, "LEAK " + str(bad) if bad else "ok")
        b.close()
