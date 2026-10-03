"""Authentication: signup, login, handles derived from email, tokens, password storage."""
from lib import *  # noqa: F401,F403


@test("S1-084", "S1-085", "S1-086", "S1-032", "S1-043")
def test_signup_and_login_shapes():
    reset(fixture([fx_user("ada", 100)]))
    r = expect(signup("a@example.com", display_name="Ann"), 201, msg="signup")
    j = r.json
    ok(isinstance(j.get("user_id"), str) and j["user_id"], "user_id")
    eq(j.get("display_name"), "Ann", "display_name echoed")
    ok(isinstance(j.get("token"), str) and j["token"], "token")
    me = expect(http("GET", "/me", token=j["token"]), 200).json
    eq(me["user_id"], j["user_id"], "/me user_id")
    eq(me["balance"], 0, "new account balance")
    eq(me["display_name"], "Ann", "/me display_name")
    eq((me["currency"], me["minor_units"]), ("EUR", 2), "currency")
    r = expect(http("POST", "/auth/login", body={"email": "a@example.com", "password": PASSWORD, "x": 1}), 200)
    eq(r.json["user_id"], j["user_id"], "login user_id")
    eq(r.json["display_name"], "Ann", "login display_name")
    ok(isinstance(r.json["token"], str) and r.json["token"], "login token")
    # seeded user: fixture display name
    r = expect(http("POST", "/auth/login", body={"email": "ada@example.com", "password": PASSWORD}), 200)
    eq(r.json["user_id"], "u_ada", "seeded id")
    eq(r.json["display_name"], "Ada", "seeded display name")
    # a new account can receive money and be asked for money straight away
    ada = login("ada@example.com", handle="ada")
    ann = User(j["user_id"], me["handle"], j["token"])
    expect(ada.pay(ann.handle, 30), 201)
    eq(ann.balance, 30, "new user receives money immediately")
    rq = expect(ada.request(ann.handle, 10), 201).json
    expect(ann.pay_request(rq["request_id"]), 201, msg="asked-for user pays")
    expect(ann.request("ada", 4), 201)


@test("S1-087", "S1-091")
def test_email_taken_and_handle_taken():
    reset(fixture([fx_user("ada", 100)]))
    expect(signup("ada@example.com"), 409, "email_taken", "email seeded in the fixture")
    expect(signup("new@example.com"), 201)
    expect(signup("new@example.com", display_name="Other"), 409, "email_taken")
    # derived handle already taken by a seeded user
    r = signup("ada@other.org")
    expect(r, 409, "handle_taken", "seeded handle")
    expect(http("POST", "/auth/login", body={"email": "ada@other.org", "password": PASSWORD}), 401, "unauthenticated")
    expect(signup("ada@other.org"), 409, "handle_taken", "still handle_taken, no account was created")
    # derived handle already taken by an earlier signup (case-insensitive derivation)
    expect(signup("NEW@elsewhere.org"), 409, "handle_taken", "handle derived from a signup")
    expect(signup("n.e.w@elsewhere.org"), 201, msg="different derived handle n_e_w")


@test("S1-042", "S1-038")
def test_derived_handles():
    reset(fixture([fx_user("ada", 10)]))
    cases = [
        ("Ada.Lovelace+Test@Example.com", "ada_lovelace_test"),
        ("MiXeD_Case99@example.com", "mixed_case99"),
        ("dash-ed.name@example.com", "dash_ed_name"),
        ("this-is-a-very-long-local-part@example.com", "this_is_a_very_long_"),
        ("abcdefghijklmnopqrstuvwxy@example.com", "abcdefghijklmnopqrst"),
        ("josé@example.com", "jos_"),
        ("a\U0001F600b@example.com", "a_b"),
        ("ÉÉx@example.com", "__x"),
        ("0123456789_0123456789_extra@example.com", "0123456789_012345678"),
        ("x@example.com", "x"),
    ]
    for email, want in cases:
        r = signup(email)
        expect(r, 201, msg="signup %r" % email)
        me = expect(http("GET", "/me", token=r.json["token"]), 200).json
        eq(me["handle"], want, "handle derived from %r" % email)
        ok(HANDLE.match(me["handle"]), "handle format %r" % me["handle"])
    # derived handle is a real handle: others can pay it
    ada = login("ada@example.com", handle="ada")
    expect(ada.pay("ada_lovelace_test", 1), 201)


@test("S1-088", "S1-089", "S1-074", "S1-068", "S1-075")
def test_signup_validation():
    reset(fixture([fx_user("ada", 0)]))
    expect(signup("p7@example.com", password="1234567"), 422, "validation_failed", "7 chars")
    expect(signup("pm7@example.com", password="é" * 7), 422, "validation_failed", "7 two-byte characters")
    expect(signup("p0@example.com", password=""), 422, "validation_failed", "empty password")
    expect(signup("p8@example.com", password="12345678"), 201, msg="8 chars")
    expect(signup("pm8@example.com", password="é" * 8), 201, msg="8 two-byte characters")
    for bad in ("plainaddress", "@example.com", "a@", "", "no-at-sign.example.com"):
        expect(signup("%s" % bad, display_name="X"), 422, "validation_failed", "email %r" % bad)
    # missing required fields are 422
    for body in ({"password": PASSWORD, "display_name": "A"},
                 {"email": "m1@example.com", "display_name": "A"},
                 {"email": "m2@example.com", "password": PASSWORD}):
        expect(http("POST", "/auth/signup", body=body), 422, "validation_failed", "missing field %r" % body)
    # wrong JSON types are 400
    for body in ({"email": 5, "password": PASSWORD, "display_name": "A"},
                 {"email": "t1@example.com", "password": 12345678, "display_name": "A"},
                 {"email": "t2@example.com", "password": PASSWORD, "display_name": ["A"]},
                 {"email": ["t3@example.com"], "password": PASSWORD, "display_name": "A"}):
        expect(http("POST", "/auth/signup", body=body), 400, "malformed_request", "wrong type %r" % body)
    expect(http("POST", "/auth/signup", raw="{not json"), 400, "malformed_request", "unparseable")
    expect(http("POST", "/auth/signup", raw=""), 400, "malformed_request", "empty body")
    expect(http("POST", "/auth/login", raw="{"), 400, "malformed_request", "unparseable login")
    expect(http("POST", "/auth/login", body={"email": "ada@example.com"}), 422, "validation_failed", "login missing password")
    expect(http("POST", "/auth/login", body={"email": 7, "password": PASSWORD}), 400, "malformed_request", "login wrong type")
    expect(http("POST", "/auth/signup", body=[1, 2]), 400, "malformed_request", "array body")
    # none of the rejected attempts created an account
    expect(http("POST", "/auth/login", body={"email": "p7@example.com", "password": "1234567"}), 401, "unauthenticated")


@test("S1-090")
def test_login_failures():
    long_pw = "P" * 80
    reset(fixture([fx_user("ada", 0), fx_user("lng", 0, password=long_pw + "one")]))
    expect(http("POST", "/auth/login", body={"email": "ada@example.com", "password": "wrong horse"}), 401, "unauthenticated")
    expect(http("POST", "/auth/login", body={"email": "ada@example.com", "password": PASSWORD + " "}), 401, "unauthenticated")
    expect(http("POST", "/auth/login", body={"email": "ada@example.com", "password": PASSWORD.upper()}), 401, "unauthenticated")
    expect(http("POST", "/auth/login", body={"email": "ghost@example.com", "password": PASSWORD}), 401, "unauthenticated")
    expect(http("POST", "/auth/login", body={"email": "ada@example.com", "password": ""}), 401, "unauthenticated")
    # bcrypt-style truncation at 72 bytes must not let a different long password in
    expect(http("POST", "/auth/login", body={"email": "lng@example.com", "password": long_pw + "one"}), 200, msg="seeded long password")
    expect(http("POST", "/auth/login", body={"email": "lng@example.com", "password": long_pw + "two"}), 401, "unauthenticated", "differs after byte 72")
    expect(http("POST", "/auth/login", body={"email": "lng@example.com", "password": long_pw}), 401, "unauthenticated", "prefix only")
    pw = "Q" * 100 + "tail-A"
    expect(signup("long@example.com", password=pw), 201, msg="100+ char password must not 5xx")
    expect(http("POST", "/auth/login", body={"email": "long@example.com", "password": pw}), 200)
    expect(http("POST", "/auth/login", body={"email": "long@example.com", "password": "Q" * 100 + "tail-B"}), 401, "unauthenticated")
    pw2 = "pässwörd-\U0001F511-" + "x" * 5
    expect(signup("uni@example.com", password=pw2), 201)
    expect(http("POST", "/auth/login", body={"email": "uni@example.com", "password": pw2}), 200)


@test("S1-094")
def test_multiple_tokens_and_sessions():
    reset(fixture([fx_user("ada", 10)]))
    s = expect(signup("multi@example.com"), 201).json
    t_signup = s["token"]
    l1 = expect(http("POST", "/auth/login", body={"email": "multi@example.com", "password": PASSWORD}), 200).json["token"]
    l2 = expect(http("POST", "/auth/login", body={"email": "multi@example.com", "password": PASSWORD}), 200).json["token"]
    for t in (t_signup, l1, l2):
        eq(expect(http("GET", "/me", token=t), 200).json["user_id"], s["user_id"], "token still valid")
    a1 = expect(http("POST", "/auth/login", body={"email": "ada@example.com", "password": PASSWORD}), 200).json["token"]
    a2 = expect(http("POST", "/auth/login", body={"email": "ada@example.com", "password": PASSWORD}), 200).json["token"]
    expect(http("GET", "/me", token=a1), 200, msg="first token survives a second login")
    expect(http("GET", "/me", token=a2), 200)
    res = burst([lambda: http("POST", "/auth/login", body={"email": "ada@example.com", "password": PASSWORD})
                 for _ in range(20)])
    toks = [r.json["token"] for r in res]
    ok(all(r.status == 200 for r in res), "concurrent logins")
    for t in toks:
        expect(http("GET", "/me", token=t), 200, msg="every concurrently issued token works")
    expect(http("GET", "/me", token=a1), 200, msg="first token still valid after 20 more sessions")
    # tokens work for writes concurrently from different sessions
    ada1, ada2 = User("u_ada", "ada", a1), User("u_ada", "ada", a2)
    bob = new_user("bobby@example.com")
    res = burst([lambda: ada1.pay(bob.handle, 1), lambda: ada2.pay(bob.handle, 1)])
    ok(all(r.status == 201 for r in res), "concurrent sessions")
    eq(ada1.balance, 8, "both sessions hit the same wallet")


def _no_auth_calls(user):
    return [
        ("GET", "/me", None, None),
        ("GET", "/activity", None, None),
        ("GET", "/requests", None, None),
        ("POST", "/payments", {"to_handle": "bob", "amount": 1}, fresh_key()),
        ("POST", "/requests", {"payer_handle": "bob", "amount": 1}, fresh_key()),
        ("POST", "/requests/rq_1/pay", {}, fresh_key()),
        ("POST", "/requests/rq_1/decline", {}, None),
        ("POST", "/requests/rq_1/cancel", {}, None),
        ("POST", "/splits", {"amount": 3, "participant_handles": ["bob"]}, fresh_key()),
        ("POST", "/settlements", {"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 1}]}, fresh_key()),
    ]


@test("S1-070", "S1-092", "S1-093")
def test_every_wallet_endpoint_needs_a_token():
    w = world({"ada": 100, "bob": 100}, operators=["ada"])
    ada = w["ada"]
    variants = [
        {},                                                   # no header
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer "},
        {"Authorization": "Basic " + ada.token},
        {"Authorization": ada.token},                         # no scheme
        {"Authorization": "Bearer definitely-not-a-token"},
        {"Authorization": "Token " + ada.token},
    ]
    for method, path, body, key in _no_auth_calls(ada):
        for h in variants:
            r = http(method, path, body=body, key=key, headers=h)
            expect(r, 401, "unauthenticated", "%s %s with %r" % (method, path, h))
    # /health, reset, export, import, signup and login do not need a token
    expect(http("GET", "/health"), 200)
    ex = expect(http("GET", "/_test/export"), 200, msg="export needs no auth")
    expect(http("POST", "/_test/import", body=ex.json), 204, msg="import needs no auth")
    reset(fixture([fx_user("ada", 1)]))
    # a token that was valid works for each of them
    ada = login("ada@example.com", handle="ada")
    expect(ada.get("/me"), 200)
    # nothing changed by the rejected calls above
    eq(ada.balance, 1, "balance")


@test("S1-095")
def test_no_plaintext_passwords_in_export():
    secret1 = "Xk9!distinctive-pw-7731"
    secret2 = "Zq4#another-secret-5520"
    reset(fixture([fx_user("ada", 5, password=secret1)]))
    expect(signup("signed@example.com", password=secret2), 201)
    ex = expect(http("GET", "/_test/export"), 200, msg="export")
    text = ex.text
    ok(secret1 not in text, "export contains the plaintext fixture password")
    ok(secret2 not in text, "export contains the plaintext signup password")
    # still able to log in with them
    expect(http("POST", "/auth/login", body={"email": "ada@example.com", "password": secret1}), 200)
    expect(http("POST", "/auth/login", body={"email": "signed@example.com", "password": secret2}), 200)
    # and after import
    expect(http("POST", "/_test/import", body=ex.json), 204)
    expect(http("POST", "/auth/login", body={"email": "signed@example.com", "password": secret2}), 200)
    expect(http("POST", "/auth/login", body={"email": "signed@example.com", "password": secret1}), 401, "unauthenticated")


@test("S1-206", "S1-038", "S1-087", "S1-091")
def test_concurrent_signup_uniqueness():
    reset(fixture([fx_user("ada", 0)]))
    # same email, 20 at once
    res = burst([lambda: signup("race@example.com") for _ in range(20)])
    created = [r for r in res if r.status == 201]
    eq(len(created), 1, "exactly one concurrent signup with the same email may succeed")
    for r in res:
        if r.status != 201:
            expect(r, 409, "email_taken", "losers of an email race")
    # different emails, same derived handle, 20 at once
    res = burst([lambda i=i: signup("dup@d%d.example.com" % i) for i in range(20)])
    created = [r for r in res if r.status == 201]
    eq(len(created), 1, "exactly one account may own the handle 'dup'")
    for r in res:
        if r.status != 201:
            expect(r, 409, "handle_taken", "losers of a handle race")
    handles = set()
    winner_token = created[0].json["token"]
    eq(expect(http("GET", "/me", token=winner_token), 200).json["handle"], "dup", "winner owns the handle")
    # losers have no account
    lost = 0
    for i, r in enumerate(res):
        if r.status != 201:
            expect(http("POST", "/auth/login", body={"email": "dup@d%d.example.com" % i, "password": PASSWORD}),
                   401, "unauthenticated", "loser has no account")
            lost += 1
    eq(lost, 19, "losers")
    # many different accounts concurrently: all fine, all unique handles and ids
    res = burst([lambda i=i: signup("user%02d@example.com" % i) for i in range(30)])
    ok(all(r.status == 201 for r in res), "30 concurrent distinct signups: %r" % [r.status for r in res])
    eq(len({r.json["user_id"] for r in res}), 30, "distinct user ids")
    eq(len({r.json["token"] for r in res}), 30, "distinct tokens")


@test("S1-038")
def test_handle_is_stable():
    w = world({"ada": 100, "bob": 0})
    ada = w["ada"]
    h0 = ada.me()["handle"]
    expect(ada.pay("bob", 5), 201)
    t2 = login("ada@example.com").token
    eq(User("u_ada", "ada", t2).me()["handle"], h0, "handle after login")
    eq(ada.me()["handle"], "ada", "handle never changes")
    # a signup cannot change or steal a handle through an unknown field
    expect(http("POST", "/auth/signup", body={"email": "thief@example.com", "password": PASSWORD,
                                              "display_name": "T", "handle": "ada"}), 201)
    eq(login("thief@example.com").me()["handle"], "thief", "handle derived from the email")
    eq(ada.me()["handle"], "ada", "ada keeps her handle")
