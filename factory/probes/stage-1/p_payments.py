"""POST /payments, amounts, notes, visibility and the activity feed."""
import re
import time

from lib import *  # noqa: F401,F403


def feed_ids(u):
    return {p["payment_id"] for p in u.all_payments()}


@test("S1-001", "S1-039", "S1-008", "S1-044", "S1-109", "S1-110", "S1-117", "S1-100", "S1-199", "S1-108")
def test_payment_happy_path_and_defaults():
    w = world({"ada": 10000, "bob": 2500, "cy": 0})
    ada, bob, cy = w["ada"], w["bob"], w["cy"]
    r = expect(ada.pay("bob", 1500, note="dinner", visibility="public"), 201)
    p = check_payment(r.json, frm=ada, to=bob, amount=1500, currency="EUR", note="dinner",
                      visibility="public", request_id=None, settlement=None)
    eq(ada.balance, 8500, "sender debited")
    eq(bob.balance, 4000, "receiver credited")
    me = ada.me()
    for k in ("user_id", "display_name", "handle", "balance", "currency", "minor_units"):
        ok(k in me, "/me lacks %s" % k)
    ok(is_int(me["balance"]) and is_int(me["minor_units"]), "/me integer types")
    # defaults
    r = expect(ada.post("/payments", {"to_handle": "cy", "amount": 7}, key=fresh_key()), 201)
    check_payment(r.json, amount=7, note="", visibility="public", request_id=None, settlement=None)
    # visible to a brand-new third party because default is public
    third = new_user("third@example.com")
    ids = feed_ids(third)
    ok(r.json["payment_id"] in ids and p["payment_id"] in ids, "default-visibility payment must be public")
    eq(cy.balance, 7, "receiver credited")
    # unknown fields ignored, payment ids unique
    r2 = expect(ada.pay("cy", 1, bogus="x", visibility="private"), 201)
    ok(r2.json["payment_id"] not in {p["payment_id"], r.json["payment_id"]}, "payment ids must be unique")
    # feed entries have the same shape as the POST response
    feed = {x["payment_id"]: x for x in ada.all_payments()}
    eq(feed[p["payment_id"]], p, "feed entry equals the payment returned by POST /payments")


@test("S1-112", "S1-076", "S1-077", "S1-115", "S1-114", "S1-116", "S1-113", "S1-075", "S1-068",
      "S1-074", "S1-037", "S1-117", "S1-079")
def test_payment_validation_and_no_trace():
    w = world({"ada": 100, "bob": 0})
    ada, bob = w["ada"], w["bob"]

    def bad(body, status=422, code="validation_failed", why=""):
        r = expect(ada.post("/payments", body, key=fresh_key()), status, code, why or repr(body))
        eq(ada.balance, 100, "failed payment must not move money: %r" % (body,))
        eq(bob.balance, 0, "failed payment must not credit: %r" % (body,))
        return r

    for amt in (0, -1, -100, 1_000_000_001, 1.5, 0.5, "10", "1e3", True, False, None, [], {}, [5]):
        bad({"to_handle": "bob", "amount": amt}, why="amount %r" % (amt,))
    bad({"to_handle": "bob"}, why="missing amount")
    bad({"amount": 5}, why="missing to_handle")
    for note in (None, 5, True, [], {}, ["x"], 1.5):
        bad({"to_handle": "bob", "amount": 1, "note": note}, why="note %r" % (note,))
    bad({"to_handle": "bob", "amount": 1, "note": "x" * 201}, why="201-char note")
    bad({"to_handle": "bob", "amount": 1, "note": "\U0001F600" * 201}, why="201 emoji note")
    for vis in ("PUBLIC", "Private", "friends", "", None, 5, True, [], {}, "public ", " private"):
        bad({"to_handle": "bob", "amount": 1, "visibility": vis}, why="visibility %r" % (vis,))
    for h in (5, ["bob"], {"h": "bob"}, True):
        bad({"to_handle": h, "amount": 1}, 400, "malformed_request", "to_handle wrong type %r" % (h,))
    bad({"to_handle": "nobody", "amount": 1}, 404, "not_found")
    bad({"to_handle": "ada", "amount": 1}, 422, "self_payment")
    # decided before the balance check
    bad({"to_handle": "ada", "amount": 5000}, 422, "self_payment", "self payment above balance")
    bad({"to_handle": "nobody", "amount": 5000}, 404, "not_found", "unknown handle above balance")
    # unparseable / non-object bodies
    for raw in ("{", "not json", "", "[]", "5", "\"str\"", "null"):
        r = expect(http("POST", "/payments", raw=raw, token=ada.token, key=fresh_key()), 400, "malformed_request",
                   "raw body %r" % raw)
    eq(ada.all_payments(), [], "no payment may exist after any of the failures")
    eq(bob.all_payments(), [], "no payment in the receiver's feed either")
    # the boundaries themselves succeed
    expect(ada.pay("bob", 1, note="x" * 200), 201, msg="200-char note")
    expect(ada.pay("bob", 1, note="\U0001F600" * 200), 201, msg="200 emoji = 200 characters")
    expect(ada.pay("bob", 1, note="hé" * 100), 201, msg="200 non-ascii characters")
    eq(ada.balance, 97, "three successful payments")


@test("S1-036", "S1-037", "S1-008", "S1-058", "S1-059")
def test_amount_representations_and_limits():
    w = world({"ada": 5_000_000_000, "bob": 0, "cy": 0})
    ada, bob = w["ada"], w["bob"]
    for lit in ("1000.0", "1e3", "1E3", "1.0e3", "1000e0", "10e2", "1000"):
        r = expect(http("POST", "/payments", raw='{"to_handle":"bob","amount":%s}' % lit, token=ada.token,
                        key=fresh_key()), 201, msg="amount literal %s" % lit)
        ok(re.search(r'"amount"\s*:\s*1000(?![\d.eE])', r.text), "response amount must be integer 1000 for %s: %s" % (lit, r.text))
        eq(r.json["amount"], 1000, "amount value")
    eq(bob.balance, 7000, "seven payments of 1000")
    for lit in ("1000.5", "1e-1", "0.0", "-0", "1e10", "1000000001.0", "1.0000000001", "-1000"):
        expect(http("POST", "/payments", raw='{"to_handle":"bob","amount":%s}' % lit, token=ada.token,
                    key=fresh_key()), 422, "validation_failed", "amount literal %s" % lit)
    # max amount
    r = expect(ada.pay("bob", 1_000_000_000), 201, msg="the maximum amount")
    eq(r.json["amount"], 1_000_000_000, "max amount echoed")
    expect(ada.pay("bob", 1_000_000_001), 422, "validation_failed", "max + 1")
    expect(ada.pay("bob", 1), 201, msg="min amount")
    eq(ada.balance, 5_000_000_000 - 7000 - 1_000_000_000 - 1, "exact arithmetic")
    # same rules on requests and splits
    for lit in ("1000.0", "1e3"):
        r = expect(http("POST", "/requests", raw='{"payer_handle":"cy","amount":%s}' % lit, token=bob.token,
                        key=fresh_key()), 201, msg="request amount %s" % lit)
        eq(r.json["amount"], 1000, "request amount")
        ok(re.search(r'"amount"\s*:\s*1000(?![\d.eE])', r.text), "request amount must be an integer literal")
        r = expect(http("POST", "/splits", raw='{"amount":%s,"participant_handles":["bob","cy"]}' % lit,
                        token=bob.token, key=fresh_key()), 201, msg="split amount %s" % lit)
        eq([s["amount"] for s in r.json["shares"]], [500, 500], "split shares")
        eq(r.json["amount"], 1000, "split amount")
    for amt in ("1000.5", "true", "\"1000\"", "null", "0", "-5", "1000000001", "1e10"):
        expect(http("POST", "/requests", raw='{"payer_handle":"cy","amount":%s}' % amt, token=bob.token,
                    key=fresh_key()), 422, "validation_failed", "request amount %s" % amt)
        expect(http("POST", "/splits", raw='{"amount":%s,"participant_handles":["cy"]}' % amt, token=bob.token,
                    key=fresh_key()), 422, "validation_failed", "split amount %s" % amt)
    r = expect(bob.request("cy", 1_000_000_000), 201, msg="request at max")
    r = expect(bob.split(1_000_000_000, ["bob", "cy", "ada"]), 201, msg="split at max")
    eq([s["amount"] for s in r.json["shares"]], [333_333_334, 333_333_333, 333_333_333], "max split shares")


@test("S1-111", "S1-117", "S1-006", "S1-049")
def test_balance_boundary_and_failure_leaves_no_trace():
    w = world({"ada": 100, "bob": 0})
    ada, bob = w["ada"], w["bob"]
    expect(ada.pay("bob", 101), 409, "insufficient_funds")
    eq((ada.balance, bob.balance), (100, 0), "nothing moved")
    eq(ada.all_payments(), [], "no feed entry for a failed payment")
    eq(bob.all_payments(), [], "no feed entry for a failed payment")
    r = expect(ada.pay("bob", 100), 201, msg="paying exactly the balance")
    eq((ada.balance, bob.balance), (0, 100), "all moved")
    expect(ada.pay("bob", 1), 409, "insufficient_funds")
    expect(bob.pay("ada", 100), 201, msg="and back again")
    eq((ada.balance, bob.balance), (100, 0), "round trip")
    ids = [p["payment_id"] for p in ada.all_payments()]
    eq(len(ids), 2, "exactly the two successful payments")


@test("S1-118")
def test_note_is_verbatim():
    w = world({"ada": 100000, "bob": 0, "cy": 0})
    ada, bob, cy = w["ada"], w["bob"], w["cy"]
    notes = [
        "", " ", "  leading and trailing  ", "\ttabbed\t", "line1\nline2\r\nline3",
        "<script>alert(1)</script> & &amp; &lt; \"quoted\" 'single'",
        "back\\slash \\n literal \\u00e9 \\\"",
        "\U0001F600 \U0001F44D\U0001F3FD \U0001F468‍\U0001F469‍\U0001F467‍\U0001F466 \U0001F1F0\U0001F1F7",
        "é", "é", "Å", "Å", "Å",
        "한국어 日本語 العربية עברית",
        "​‍⁠zero width", "﻿BOM first", " nbsp ",
        "{\"json\": true, \"n\": [1, 2]}", "%20 %2F ?x=1&y=2 #frag", "'; DROP TABLE payments; --",
        "Ünïcödé ✓ ✗ ½ ™", "a" * 200,
    ]
    sent = {}
    for n in notes:
        r = expect(ada.pay("bob", 1, note=n, visibility="public"), 201, msg="note %r" % n)
        eq(r.json["note"], n, "note in POST response")
        sent[r.json["payment_id"]] = n
    for u in (ada, bob, cy):
        got = {p["payment_id"]: p["note"] for p in u.all_payments()}
        for pid, n in sent.items():
            eq(got[pid], n, "note in %s's feed" % u.handle)
    # NFC and NFD forms are stored distinct
    vals = list(sent.values())
    ok("é" in vals and "é" in vals, "distinct forms were sent")
    # raw non-ASCII bytes in the request (not \u escapes)
    raw = ('{"to_handle":"bob","amount":1,"note":"%s"}' % "café \U0001F600").encode("utf-8")
    r = expect(http("POST", "/payments", raw=raw, token=ada.token, key=fresh_key()), 201)
    eq(r.json["note"], "café \U0001F600", "raw utf-8 note")
    # same for request and split notes
    rq = expect(bob.request("ada", 5, note=" <b>spaced</b>  \U0001F600 "), 201).json
    eq(rq["note"], " <b>spaced</b>  \U0001F600 ", "request note")
    eq([x["note"] for x in ada.all_requests() if x["request_id"] == rq["request_id"]], [" <b>spaced</b>  \U0001F600 "], "request note in list")
    sp = expect(ada.split(9, ["ada", "bob"], note=" é & "), 201).json
    eq(sp["note"], " é & ", "split note")


@test("S1-052", "S1-053", "S1-056", "S1-057", "S1-002", "S1-054", "S1-050", "S1-051", "S1-055")
def test_feed_visibility_matrix():
    w = world({"ada": 1000, "bob": 1000, "cy": 1000})
    ada, bob, cy = w["ada"], w["bob"], w["cy"]
    dee = new_user("dee@example.com")                       # no relationship to anyone
    p1 = expect(ada.pay("bob", 1, visibility="public"), 201).json["payment_id"]
    p2 = expect(ada.pay("bob", 2, visibility="private"), 201).json["payment_id"]
    p3 = expect(bob.pay("cy", 3, visibility="private"), 201).json["payment_id"]
    p4 = expect(cy.pay("ada", 4), 201).json["payment_id"]               # default public
    rq = expect(bob.request("ada", 10), 201).json["request_id"]
    p5 = expect(ada.pay_request(rq, {"visibility": "private"}), 201).json["payment_id"]
    rq2 = expect(cy.request("bob", 11), 201).json["request_id"]
    p6 = expect(bob.pay_request(rq2), 201).json["payment_id"]            # default public
    expect(ada.split(30, ["ada", "bob", "cy"]), 201)                      # not a feed item
    expect(dee.request("ada", 5), 201)
    expect(cy.request("dee", 5), 201)
    expect(bob.request("cy", 3), 201)
    want = {
        "ada": {p1, p2, p4, p5, p6},
        "bob": {p1, p2, p3, p5, p4, p6},
        "cy": {p1, p3, p4, p6},
        "dee": {p1, p4, p6},
    }
    users = {"ada": ada, "bob": bob, "cy": cy, "dee": dee}
    for name, u in users.items():
        got = u.all_payments()
        eq({p["payment_id"] for p in got}, want[name], "feed of %s" % name)
        eq(len(got), len(want[name]), "no duplicates in %s's feed" % name)
        by = {p["payment_id"]: p for p in got}
        for pid, vis in ((p1, "public"), (p2, "private"), (p3, "private"), (p4, "public"), (p5, "private"), (p6, "public")):
            if pid in by:
                eq(by[pid]["visibility"], vis, "visibility of %s seen by %s" % (pid, name))
    # requests and splits are not feed items: every feed entry is a payment object
    for name, u in users.items():
        for p in u.all_payments():
            ok("payment_id" in p and "request_id" in p and "status" not in p and "shares" not in p,
               "feed item must be a payment: %r" % p)
    # one value for everyone: the private payment's receiver sees 'private'
    eq({p["payment_id"]: p["visibility"] for p in bob.all_payments()}[p2], "private", "receiver sees private")


@test("S1-157", "S1-159", "S1-081", "S1-082", "S1-078", "S1-144", "S1-145", "S1-033")
def test_activity_pagination_and_ranges():
    n = 60
    users = [fx_user("ada", 100000), fx_user("bob", 0), fx_user("cy", 0)]
    pays = [{"id": "sp%02d" % i, "from_user_id": "u_ada", "to_user_id": "u_bob", "amount": 1,
             "note": "n%d" % i, "visibility": "public"} for i in range(n)]
    reset(fixture(users, payments=pays))
    cy = login("cy@example.com", handle="cy")
    r = cy.activity()
    eq(len(r["payments"]), 50, "default limit is 50")
    eq(r["has_more"], True, "has_more with 60 items and limit 50")
    eq(set(r.keys()), {"payments", "has_more"}, "feed envelope")
    eq(len(cy.activity(limit=200)["payments"]), 60, "limit=200")
    eq(cy.activity(limit=200)["has_more"], False, "has_more false when all returned")
    eq(cy.activity(limit=60)["has_more"], False, "exactly limit items remaining")
    eq(cy.activity(limit=59)["has_more"], True, "one item beyond the page")
    r = cy.activity(limit=1)
    eq((len(r["payments"]), r["has_more"]), (1, True), "limit=1")
    r = cy.activity(offset=55, limit=10)
    eq((len(r["payments"]), r["has_more"]), (5, False), "offset 55 limit 10")
    r = cy.activity(offset=59, limit=1)
    eq((len(r["payments"]), r["has_more"]), (1, False), "offset 59 limit 1")
    r = cy.activity(offset=60)
    eq((r["payments"], r["has_more"]), ([], False), "offset at the end")
    r = cy.activity(offset=1000, limit=5)
    eq((r["payments"], r["has_more"]), ([], False), "offset beyond the end is not an error")
    r = cy.activity(offset=0, limit=50, unknown="1", zzz="abc")
    eq(len(r["payments"]), 50, "unknown params ignored")
    # pages are disjoint
    seen = []
    for off in range(0, 60, 20):
        seen += [p["payment_id"] for p in cy.activity(limit=20, offset=off)["payments"]]
    eq(len(seen), 60, "three pages of 20")
    eq(len(set(seen)), 60, "pages must not overlap")
    for q in ("limit=0", "limit=201", "limit=-1", "limit=abc", "limit=1e2", "limit=4.0", "limit=+4",
              "limit=1000", "offset=-1", "offset=abc", "offset=+1", "offset=1e0", "offset=1.0",
              "offset=0x1", "limit=5&offset=-5", "limit=0&offset=0"):
        expect(cy.get("/activity?" + q), 422, "validation_failed", "GET /activity?" + q)
    for q in ("limit=1", "limit=200", "offset=0", "limit=200&offset=0"):
        expect(cy.get("/activity?" + q), 200, msg="GET /activity?" + q)
    # ordering: non-increasing created_at
    stamps = [parse_ts(p["created_at"]) for p in cy.all_payments()]
    ok(all(stamps[i] >= stamps[i + 1] for i in range(len(stamps) - 1)), "feed must be newest first")


@test("S1-157")
def test_activity_newest_first_across_seconds():
    w = world({"ada": 100, "bob": 0})
    ada, bob = w["ada"], w["bob"]
    ids = []
    for i in range(3):
        ids.append(expect(ada.pay("bob", i + 1), 201).json["payment_id"])
        time.sleep(1.1)
    got = [p["payment_id"] for p in bob.all_payments()]
    eq(got, list(reversed(ids)), "payments created in different seconds come back newest first")
    ts = [parse_ts(p["created_at"]) for p in bob.all_payments()]
    ok(ts[0] > ts[1] > ts[2], "created_at must reflect real creation time")
    # limit/offset walk the same order
    first = bob.activity(limit=1)["payments"][0]["payment_id"]
    eq(first, ids[-1], "limit=1 is the newest")
    eq(bob.activity(limit=1, offset=2)["payments"][0]["payment_id"], ids[0], "offset=2 is the oldest")
