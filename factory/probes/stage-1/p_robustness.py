"""No 5xx, whatever the client sends (section 5), and a few cross-cutting error rules."""
import json
from http.client import HTTPException

from lib import *  # noqa: F401,F403


def below_500(r, why):
    if r is None:        # the server dropped an oversized request; not a 5xx
        return r
    ok(r.status < 500, "%s -> %s %s" % (why, r.status, r.text[:150]))
    return r


def soft(fn, *a, **kw):
    """Oversized inputs may be refused by closing the connection; that is not a 5xx."""
    try:
        return fn(*a, **kw)
    except (OSError, HTTPException):
        return None


@test("S1-083", "S1-075", "S1-080", "S1-068", "S1-078", "S1-067")
def test_hostile_input_never_produces_5xx():
    w = world({"ada": 1000, "bob": 0, "op": 0}, operators=["op"])
    ada, bob, op = w["ada"], w["bob"], w["op"]

    def post(path, raw=None, body=None, key="auto", token=None, headers=None):
        return soft(http, "POST", path, raw=raw, body=body, token=token or ada.token,
                    key=fresh_key() if key == "auto" else key, headers=headers)

    # huge / odd numbers
    for lit in ("1e999", "-1e999", "99999999999999999999999999", "-99999999999999999999", "1" + "0" * 400,
                "0.00000000000000000000001", "123456789012345678901234567890.5"):
        for path, tmpl in (("/payments", '{"to_handle":"bob","amount":%s}'),
                           ("/requests", '{"payer_handle":"bob","amount":%s}'),
                           ("/splits", '{"participant_handles":["bob"],"amount":%s}')):
            r = below_500(post(path, raw=tmpl % lit), "%s amount %s" % (path, lit[:30]))
            ok(r is None or r.status in (400, 422), "%s amount %s must be rejected, got %s" % (path, lit[:30], r and r.status))
        r = below_500(post("/settlements", raw='{"transfers":[{"from_handle":"ada","to_handle":"bob","amount":%s}]}' % lit, token=op.token),
                      "settlement amount " + lit[:30])
        ok(r is None or r.status in (400, 422), "settlement amount %s must be rejected" % lit[:30])
    # strange strings
    for note in ("\u0000", "a\u0000b", "\ud800", "\udfff", "\U0010ffff", "  ", "\x7f", "\x1b[31m"):
        raw = '{"to_handle":"bob","amount":1,"note":%s}' % json.dumps(note)
        below_500(post("/payments", raw=raw), "note %r" % note)
    below_500(post("/payments", raw='{"to_handle":"bob","amount":1,"note":"\\ud800"}'), "lone surrogate escape")
    below_500(post("/payments", raw='{"to_handle":"bob","amount":1,"note":"\\u0000"}'), "NUL escape")
    for h in ("../../etc/passwd", "bob'; DROP TABLE users;--", "%00", "x" * 10000, "BOB", "bob ", " bob", "\u0000", "böb", "", "\U0001F600"):
        r = below_500(post("/payments", body={"to_handle": h, "amount": 1}), "to_handle %r" % h[:30])
        ok(r.status in (404, 422, 400), "weird handle %r must be a 4xx, got %s" % (h[:30], r.status))
        eq(r.status == 201, False, "must not succeed")
    below_500(post("/payments", body={"to_handle": "bob", "amount": 1, "note": "n" * 2_000_000}), "2 MB note")
    expect(http("GET", "/health"), 200, msg="alive after the 2 MB body")
    below_500(post("/payments", raw="[" * 20000 + "]" * 20000), "deeply nested array")
    below_500(post("/payments", raw='{"a":' * 5000 + "1" + "}" * 5000), "deeply nested object")
    below_500(post("/payments", raw="\xff\xfe\x00{".encode("latin-1")), "binary body")
    below_500(post("/payments", raw=b'{"to_handle":"bob","amount":1,"note":"\xff\xfe"}'), "invalid utf-8 inside a string")
    below_500(post("/payments", raw='{"to_handle":"bob","amount":1,"amount":2}'), "duplicate keys")
    below_500(post("/payments", raw='{"to_handle":"bob","amount":1} trailing'), "trailing garbage")
    below_500(post("/payments", raw="{'to_handle': 'bob'}"), "single quotes")
    below_500(post("/payments", raw='{"to_handle":"bob","amount":NaN}'), "NaN")
    below_500(post("/payments", raw='{"to_handle":"bob","amount":Infinity}'), "Infinity")
    # content types and methods
    r = below_500(http("POST", "/payments", raw='{"to_handle":"bob","amount":1}', token=ada.token, key=fresh_key(),
                       headers={"Content-Type": "text/plain"}), "text/plain content type")
    r = below_500(http("POST", "/payments", raw='{"to_handle":"bob","amount":1}', token=ada.token, key=fresh_key(),
                       headers={"Content-Type": "application/json; charset=latin-1"}), "latin-1 charset")
    for m, path in (("GET", "/payments"), ("DELETE", "/me"), ("PUT", "/requests"), ("PATCH", "/activity"),
                    ("GET", "/requests/rq_1/pay"), ("GET", "/settlements"), ("GET", "/splits"), ("POST", "/me")):
        below_500(http(m, path, token=ada.token), "%s %s" % (m, path))
    # ids in the path
    for rid in ("x" * 5000, "%00", "..%2F..%2Fme", "rq%201", "%C3%A9", "rq_1%2Fpay"):
        for action in ("pay", "decline", "cancel"):
            below_500(http("POST", "/requests/%s/%s" % (rid, action), body={}, token=ada.token, key=fresh_key()),
                      "id %r %s" % (rid[:20], action))
    r = http("POST", "/requests/%s/pay" % ("x" * 5000), body={}, token=ada.token, key=fresh_key())
    expect(r, 404, "not_found", "a very long id is just unknown")
    # idempotency key abuse
    expect(post("/payments", body={"to_handle": "bob", "amount": 1}, key="k" * 10000), 422, "validation_failed", "10k key")
    expect(post("/payments", body={"to_handle": "bob", "amount": 1}, key="k" * 300), 422, "validation_failed", "300 key")
    # query abuse
    for q in ("limit=99999999999999999999", "limit=%00", "limit=-0", "limit=0x10", "limit=1_0", "limit=%EF%BC%91",
              "offset=99999999999999999999", "offset=-99999999999999999999", "limit=1&limit=2", "limit[]=1"):
        r = below_500(ada.get("/activity?" + q), "GET /activity?" + q)
        r = below_500(ada.get("/requests?" + q), "GET /requests?" + q)
    for q in ("limit=99999999999999999999", "limit=%00", "limit=0x10", "limit=1_0", "limit=%EF%BC%91", "limit=-0"):
        expect(ada.get("/activity?" + q), 422, "validation_failed", "GET /activity?" + q)
    below_500(ada.get("/requests?direction=%FF&status=%00"), "binary enum values")
    # auth header abuse
    for h in ("Bearer " + "A" * 3000, "Bearer %00", "bearer " + ada.token, "BEARER " + ada.token,
              "Bearer  " + ada.token, "Bearer\t" + ada.token):
        below_500(soft(http, "GET", "/me", headers={"Authorization": h}), "Authorization %r" % h[:30])
    expect(http("GET", "/me", headers={"Authorization": "Bearer " + "A" * 3000}), 401, "unauthenticated", "long unknown token")
    # signup / login abuse
    for email in ("a" * 5000 + "@example.com", "x@" + "d" * 5000, "q@x.com\u0000", "\U0001F600\U0001F600@example.com", "a@b@c.com"):
        r = below_500(soft(signup, email), "signup %r" % email[:20])
    below_500(soft(signup, "pw1@example.com", password="p" * 100000), "100k password")
    below_500(soft(signup, "pw2@example.com", display_name="d" * 100000), "100k display name")
    below_500(soft(http, "POST", "/auth/signup",
                   raw='{"email":"pw3@example.com","password":"correct horse","display_name":"\\u0000\\ud800"}'), "odd display name")
    below_500(soft(http, "POST", "/auth/login", body={"email": "a" * 100000, "password": "x"}), "huge login email")
    below_500(soft(http, "POST", "/auth/login", body={"email": "ada@example.com", "password": "p" * 100000}), "huge login password")
    # reset / import abuse
    for body in ({}, {"users": "x"}, {"currency": "EUR", "minor_units": 2}, {"currency": "EUR", "minor_units": 2, "users": [{}]},
                 {"currency": "EUR", "minor_units": 7, "users": []}, {"currency": 5, "minor_units": 2, "users": []},
                 {"currency": "EUR", "minor_units": 2, "users": [fx_user("zed", 1), fx_user("zed", 2)]},
                 {"currency": "EUR", "minor_units": 2, "users": [fx_user("a", 1, uid="u1"), fx_user("b", 1, uid="u1")]},
                 {"currency": "EUR", "minor_units": 2, "users": [fx_user("Bad Handle!", 1)]}):
        below_500(http("POST", "/_test/reset", body=body), "reset %s" % str(body)[:60])
    below_500(http("POST", "/_test/reset", raw="{"), "reset unparseable")
    # service still healthy and serving after all of that
    expect(http("GET", "/health"), 200)
    reset(fixture([fx_user("ada", 1)]))
    login("ada@example.com", handle="ada")


@test("S1-068", "S1-079", "S1-067")
def test_unparseable_reset_is_400():
    expect(http("POST", "/_test/reset", raw="{"), 400, "malformed_request")
    expect(http("POST", "/_test/reset", raw=""), 400, "malformed_request")
    reset(fixture([fx_user("ada", 1)]))
    expect(http("POST", "/_test/reset", raw="{"), 400, "malformed_request", "and a rejected reset keeps state")
    login("ada@example.com", handle="ada")


@test("S1-067", "S1-070", "S1-071", "S1-072", "S1-074")
def test_every_error_has_the_standard_body():
    """Error bodies are checked on every request by the probe client; this probe visits each documented status."""
    w = world({"ada": 10, "bob": 0}, operators=[])
    ada, bob = w["ada"], w["bob"]
    seen = {}
    seen[400] = ada.post("/payments", {"to_handle": 5, "amount": 1}, key=fresh_key())
    seen["400k"] = ada.post("/payments", {"to_handle": "bob", "amount": 1})
    seen[401] = http("GET", "/me")
    seen[403] = bob.post("/settlements", {"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 1}]}, key=fresh_key())
    seen[404] = ada.post("/payments", {"to_handle": "zzz", "amount": 1}, key=fresh_key())
    k = fresh_key()
    expect(ada.pay("bob", 1, key=k), 201)
    seen[409] = ada.pay("bob", 2, key=k)
    seen[422] = ada.post("/payments", {"to_handle": "bob", "amount": 0}, key=fresh_key())
    want = {400: "malformed_request", "400k": "missing_idempotency_key", 401: "unauthenticated", 403: "forbidden",
            404: "not_found", 409: "idempotency_key_reuse", 422: "validation_failed"}
    for k_, r in seen.items():
        expect(r, int(str(k_)[:3]), want[k_], "status %s" % k_)
        eq(sorted(r.json.keys()), ["error"], "only an error object")
