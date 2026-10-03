"""Runtime contract: health, reset/seed, conventions, resource limits, delivery files."""
import os
import time

from lib import *  # noqa: F401,F403

REPO = os.environ.get("PROBE_REPO") or os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


@test("S1-023")
def test_health_body():
    r = expect(http("GET", "/health"), 200)
    eq(r.json, {"status": "ok"}, "health body")
    r = expect(http("GET", "/health?x=1", token="not-a-token"), 200, msg="health ignores query and auth")


@test("S1-015", "S1-024", "S1-026", "S1-029")
def test_ready_means_ready():
    # The first healthy answer already means reset, signup and login work.
    expect(http("GET", "/health"), 200)
    reset(fixture([fx_user("ada", 100)]))
    u = login("ada@example.com", handle="ada")
    eq(u.balance, 100, "balance right after reset")
    s = new_user("fresh@example.com")
    eq(s.balance, 0, "new user balance")


@test("S1-010")
def test_delivery_files():
    for name in ("Dockerfile", "RUN.md"):
        p = os.path.join(REPO, name)
        ok(os.path.isfile(p), "%s missing at the repository root (%s)" % (name, p))
        ok(os.path.getsize(p) > 0, "%s is empty" % name)
    run_md = open(os.path.join(REPO, "RUN.md"), encoding="utf-8", errors="replace").read()
    ok("docker" in run_md.lower(), "RUN.md should contain the docker command that builds and starts the service")
    ok("PORT" in run_md, "RUN.md should show how the PORT variable is passed")


@test("S1-028", "S1-060", "S1-061", "S1-065", "S1-041", "S1-032")
def test_reset_seeds_fixture_exactly():
    fx = {
        "currency": "EUR", "minor_units": 2, "ignored_top_level": {"x": 1},
        "users": [
            dict(fx_user("ada", 10000), extra_field=True),
            fx_user("bob", 2500),
            fx_user("cy", 0),
        ],
        "payments": [
            {"id": "p_1", "from_user_id": "u_ada", "to_user_id": "u_bob", "amount": 500,
             "note": "coffee", "visibility": "public", "whatever": 1},
            {"id": "p_2", "from_user_id": "u_bob", "to_user_id": "u_cy", "amount": 70,
             "note": "secret", "visibility": "private"},
        ],
        "requests": [
            {"id": "rq_1", "requester_id": "u_bob", "payer_id": "u_ada", "amount": 1200,
             "note": "taxi", "status": "pending"},
            {"id": "rq_2", "requester_id": "u_bob", "payer_id": "u_ada", "amount": 5,
             "note": "d", "status": "declined"},
            {"id": "rq_3", "requester_id": "u_bob", "payer_id": "u_ada", "amount": 6,
             "note": "c", "status": "cancelled"},
        ],
    }
    reset(fx)
    reset(fx)   # repeated reset is fine and idempotent
    ada, bob, cy = (login(h + "@example.com", handle=h) for h in ("ada", "bob", "cy"))
    for u in (ada, bob, cy):
        eq(u.id, "u_" + u.handle, "user id preserved")
    me = ada.me()
    eq(me["balance"], 10000, "ada balance must be the seeded balance, not replayed")
    eq(bob.balance, 2500, "bob balance")
    eq(cy.balance, 0, "cy balance")
    eq(me["handle"], "ada", "handle")
    eq(me["display_name"], "Ada", "display_name")
    # seeded payments are real payments with their fixture ids
    pays = {p["payment_id"]: p for p in ada.all_payments()}
    ok("p_1" in pays, "seeded payment p_1 missing from sender's feed")
    check_payment(pays["p_1"], frm=ada, to=bob, amount=500, note="coffee", visibility="public",
                  currency="EUR", request_id=None, settlement=None)
    ok("p_2" not in pays, "private payment between bob and cy must not be in ada's feed")
    eq(sorted(p["payment_id"] for p in bob.all_payments()), ["p_1", "p_2"], "bob's feed")
    eq(sorted(p["payment_id"] for p in cy.all_payments()), ["p_1", "p_2"], "cy's feed (p_1 public, p_2 receiver)")
    # seeded requests keep id/status
    reqs = {r["request_id"]: r for r in ada.all_requests()}
    eq(sorted(reqs), ["rq_1", "rq_2", "rq_3"], "ada's requests")
    check_request(reqs["rq_1"], requester=bob, payer=ada, amount=1200, status="pending", note="taxi", payment_id=None)
    eq(reqs["rq_2"]["status"], "declined", "seeded declined")
    eq(reqs["rq_3"]["status"], "cancelled", "seeded cancelled")
    expect(ada.pay_request("rq_2"), 409, "request_not_pending", "pay of seeded declined request")
    expect(ada.pay_request("rq_3"), 409, "request_not_pending", "pay of seeded cancelled request")
    eq(ada.balance, 10000, "no money moved")
    r = expect(ada.pay_request("rq_1", {"visibility": "private"}), 201, msg="pay seeded pending request")
    check_payment(r.json, frm=ada, to=bob, amount=1200, request_id="rq_1", visibility="private")
    eq(ada.balance, 10000 - 1200, "ada after paying seeded request")
    eq(bob.balance, 2500 + 1200, "bob after")


@test("S1-066")
def test_reset_minimal_fixture():
    reset({"currency": "EUR", "minor_units": 2, "users": [fx_user("solo", 5)]})
    u = login("solo@example.com", handle="solo")
    eq(u.activity()["payments"], [], "no payments")
    eq(u.requests()["requests"], [], "no requests")
    expect(u.settle([{"from_handle": "solo", "to_handle": "solo", "amount": 1}]), 403, "forbidden",
           "nobody is an operator by default")
    reset({"currency": "EUR", "minor_units": 2, "users": [fx_user("solo", 5)], "settlement_operator_ids": []})
    login("solo@example.com", handle="solo")


@test("S1-027", "S1-028")
def test_reset_replaces_everything():
    w = world({"ada": 100, "bob": 50}, operators=["ada"])
    ada, bob = w["ada"], w["bob"]
    k = fresh_key()
    expect(ada.pay("bob", 10, key=k), 201)
    rq = expect(bob.request("ada", 5), 201).json["request_id"]
    zed = new_user("zed@example.com")
    zed_old_token = zed.token
    expect(ada.settle([{"from_handle": "ada", "to_handle": "bob", "amount": 1}]), 201, msg="operator before reset")
    # second reset: different universe, ada keeps her id but nothing else survives
    reset(fixture([fx_user("ada", 7), fx_user("cy", 5)]))
    expect(http("GET", "/me", token=ada.token), 401, "unauthenticated", "old token for a re-seeded user id")
    expect(http("GET", "/me", token=bob.token), 401, "unauthenticated", "old token of a removed user")
    expect(http("GET", "/me", token=zed_old_token), 401, "unauthenticated", "signup token")
    expect(http("POST", "/auth/login", body={"email": "bob@example.com", "password": PASSWORD}), 401, "unauthenticated")
    expect(http("POST", "/auth/login", body={"email": "zed@example.com", "password": PASSWORD}), 401, "unauthenticated")
    ada2 = login("ada@example.com", handle="ada")
    cy = login("cy@example.com", handle="cy")
    eq(ada2.balance, 7, "balance replaced")
    eq(ada2.all_payments(), [], "old payments gone")
    eq(ada2.all_requests(), [], "old requests gone")
    expect(ada2.pay("bob", 1), 404, "not_found", "removed handle")
    # reused key (same user id, same key, a different body) is a first use again
    expect(ada2.pay("cy", 5, key=k), 201, msg="idempotency records are cleared by reset")
    # operators replaced
    expect(ada2.settle([{"from_handle": "ada", "to_handle": "cy", "amount": 1}]), 403, "forbidden", "old operator")
    # handle taken by a signup in the previous universe is free again
    s = new_user("zed@example.com")
    eq(s.handle, "zed", "derived handle after reset")
    # request ids from the previous universe do not exist
    expect(ada2.pay_request(rq), 404, "not_found", "old request id")
    # and reset must also drop imported-style leftovers: totals equal the new seed
    eq(ada2.balance + cy.balance, 12, "sum equals the last seed")


@test("S1-062")
def test_reset_negative_balance_changes_nothing():
    w = world({"ada": 100, "bob": 50})
    ada = w["ada"]
    expect(ada.pay("bob", 10), 201)
    before = ada.me()
    pays = ada.all_payments()
    r = http("POST", "/_test/reset", body=fixture([fx_user("ada", 5), fx_user("neg", -1)]))
    expect(r, 422, "validation_failed", "negative fixture balance")
    eq(ada.me(), before, "state must be unchanged by a rejected reset")
    eq(ada.all_payments(), pays, "payments unchanged")
    expect(http("POST", "/auth/login", body={"email": "neg@example.com", "password": PASSWORD}), 401, "unauthenticated")
    # a later valid reset works normally
    reset(fixture([fx_user("ada", 5)]))


@test("S1-035", "S1-063", "S1-008", "S1-108", "S1-110", "S1-058")
def test_currency_and_minor_units():
    for cur, mu, big in (("JPY", 0, 1000), ("BHD", 3, 12345), ("EUR", 2, 1000)):
        w = world({"ada": 50000, "bob": 0}, currency=cur, minor_units=mu)
        ada, bob = w["ada"], w["bob"]
        me = ada.me()
        eq(me["currency"], cur, "currency in /me")
        eq(me["minor_units"], mu, "minor_units in /me")
        eq(me["balance"], 50000, "balance not scaled")
        p = expect(ada.pay("bob", big), 201).json
        eq(p["currency"], cur, "payment currency")
        eq(p["amount"], big, "amount unscaled")
        eq(bob.balance, big, "bob credited exactly")
        r = expect(bob.request("ada", 7), 201).json
        eq(r["currency"], cur, "request currency")
        eq(r["amount"], 7, "request amount unscaled")
        s = expect(ada.split(10, ["ada", "bob"]), 201).json
        eq(s["currency"], cur, "split currency")
        eq([x["amount"] for x in s["shares"]], [5, 5], "shares unscaled")


@test("S1-030", "S1-031", "S1-033", "S1-034", "S1-032", "S1-110")
def test_conventions_headers_timestamps_ids():
    w = world({"ada": 1000, "bob": 0})
    ada, bob = w["ada"], w["bob"]
    r = expect(ada.get("/me?bogus=1&limit=zzz"), 200, msg="unknown query params ignored")
    r = ada.pay("bob", 10, extra_field={"a": 1}, another=[1, 2])
    expect(r, 201, msg="unknown body fields ignored")
    ct = r.headers.get("content-type", "").lower().replace(" ", "")
    eq(ct, "application/json;charset=utf-8", "content type of success")
    p = r.json
    parse_ts(p["created_at"])
    dt = check_ts(p["created_at"])
    ok(len(p["payment_id"]) <= 64, "id too long")
    q = expect(bob.request("ada", 3, nope=True), 201).json
    ok(len(q["request_id"]) <= 64, "id too long")
    check_ts(q["created_at"])
    me = ada.me()
    ok(HANDLE.match(me["handle"]), "handle format")
    ok(len(me["user_id"]) <= 64, "user id too long")
    feed = expect(ada.get("/activity?limit=5&unknown=zzz"), 200).json
    ok(len(feed["payments"]) == 1, "feed")
    # an error response is JSON utf-8 as well (checked inside http())
    expect(ada.post("/payments", {"to_handle": "nobody", "amount": 1}, key=fresh_key()), 404, "not_found")
    # unknown fields on auth bodies and fixtures are ignored too
    expect(http("POST", "/auth/signup", body={"email": "x1@example.com", "password": PASSWORD,
                                              "display_name": "X", "handle": "chosen", "role": "admin"}),
           201, msg="signup ignores unknown fields (there is no handle field)")
    expect(http("POST", "/auth/login", body={"email": "x1@example.com", "password": PASSWORD, "z": 1}), 200)
    me = login("x1@example.com").me()
    eq(me["handle"], "x1", "handle comes from the email, never from a body field")


@test("S1-016", "S1-017")
def test_fifty_in_flight():
    w = world({"ada": 1_000_000, "bob": 0, "cy": 0})
    ada = w["ada"]
    res = burst([lambda: http("GET", "/health") for _ in range(50)])
    ok(all(r.status == 200 for r in res), "health under 50 concurrent requests")
    res = burst([lambda: ada.get("/me") for _ in range(50)])
    ok(all(r.status == 200 for r in res), "/me under 50 concurrent requests")
    res = burst([lambda: ada.get("/activity") for _ in range(25)] + [lambda: ada.get("/requests") for _ in range(25)])
    ok(all(r.status == 200 for r in res), "feeds under 50 concurrent requests")
    # 50 concurrent logins: password hashing must stay inside the 5 s request timeout (2 vCPU)
    t0 = time.time()
    res = burst([lambda: http("POST", "/auth/login", body={"email": "ada@example.com", "password": PASSWORD})
                 for _ in range(50)])
    ok(all(r.status == 200 for r in res), "50 concurrent logins: %r" % [r.status for r in res if r.status != 200])
    ok(max(r.elapsed for r in res) < 5.0, "a login took %.1fs" % max(r.elapsed for r in res))
    res = burst([lambda i=i: ada.pay("bob" if i % 2 else "cy", 1) for i in range(50)])
    ok(all(r.status == 201 for r in res), "50 concurrent payments: %r" % [r.status for r in res])
    eq(ada.balance, 1_000_000 - 50, "balance after 50 payments")


@test("S1-017", "S1-016", "S1-060")
def test_reset_large_fixture_within_10s():
    n = 200
    users = [fx_user("m%03d" % i, 1000 + i) for i in range(n)]
    payments = [{"id": "sp_%d" % i, "from_user_id": "u_m%03d" % (i % n), "to_user_id": "u_m%03d" % ((i + 1) % n),
                 "amount": 5, "note": "n%d" % i, "visibility": "public" if i % 2 else "private"}
                for i in range(300)]
    requests = [{"id": "sr_%d" % i, "requester_id": "u_m%03d" % i, "payer_id": "u_m%03d" % ((i + 7) % n),
                 "amount": 9, "note": "", "status": "pending"} for i in range(100)]
    t0 = time.time()
    r = http("POST", "/_test/reset", body=fixture(users, payments=payments, requests=requests))
    dt = time.time() - t0
    expect(r, 204, msg="reset")
    ok(dt < 10.0, "reset of 200 users took %.1fs (limit 10 s)" % dt)
    # every seeded user can log in immediately (first and last)
    for i in (0, 77, n - 1):
        u = login("m%03d@example.com" % i, handle="m%03d" % i)
        eq(u.balance, 1000 + i, "seeded balance")
    t0 = time.time()
    res = burst([lambda i=i: http("POST", "/auth/login", body={"email": "m%03d@example.com" % i, "password": PASSWORD})
                 for i in range(50)])
    ok(all(r.status == 200 for r in res), "50 concurrent logins after a large reset")


@test("S1-034")
def test_ids_are_short_strings():
    w = world({"ada": 100, "bob": 0})
    ada, bob = w["ada"], w["bob"]
    seen = []
    p = expect(ada.pay("bob", 1), 201).json
    seen += [p["payment_id"], p["from_user_id"], p["to_user_id"]]
    q = expect(bob.request("ada", 2), 201).json
    seen += [q["request_id"], q["requester_id"], q["payer_id"]]
    pp = expect(ada.pay_request(q["request_id"]), 201).json
    seen.append(pp["payment_id"])
    s = expect(ada.split(9, ["ada", "bob"]), 201).json
    seen.append(s["split_id"])
    seen += [x["request_id"] for x in s["requests"]]
    u = new_user("someone.with.a.long.local.part.indeed@example.com")
    seen.append(u.id)
    for i in seen:
        ok(isinstance(i, str) and 1 <= len(i) <= 64, "bad id %r" % (i,))
    eq(len(set(seen[:3] + [pp["payment_id"]] + [q["request_id"]])), 5, "ids must be distinct")
