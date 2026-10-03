"""GET /_test/export and POST /_test/import (section 10), plus numeric range."""
import copy
import json
import os
import threading
import time

from lib import *  # noqa: F401,F403

SEED = {"ada": 5000, "bob": 3000, "cy": 0, "op": 0}
SEED_TOTAL = sum(SEED.values())
ZED_PW = "zed password 1"


def dumps(o):
    return json.dumps(o, sort_keys=True, ensure_ascii=False)


class Ctx:
    pass


def build():
    """A rich state: seeded + signup user, payments of every kind, a split, a settlement,
    pending/cancelled/declined/paid requests, successful and failed idempotency keys."""
    users = [fx_user(h, b) for h, b in SEED.items()]
    payments = [
        {"id": "p_1", "from_user_id": "u_ada", "to_user_id": "u_bob", "amount": 500, "note": "coffee", "visibility": "public"},
        {"id": "p_2", "from_user_id": "u_bob", "to_user_id": "u_cy", "amount": 70, "note": "secret", "visibility": "private"},
    ]
    requests = [
        {"id": "rq_1", "requester_id": "u_bob", "payer_id": "u_ada", "amount": 1200, "note": "taxi", "status": "pending"},
        {"id": "rq_2", "requester_id": "u_bob", "payer_id": "u_ada", "amount": 5, "note": "x", "status": "declined"},
    ]
    reset(fixture(users, payments=payments, requests=requests, operators=["u_op"]))
    c = Ctx()
    c.u = {h: login(h + "@example.com", handle=h) for h in SEED}
    ada, bob, cy, op = c.u["ada"], c.u["bob"], c.u["cy"], c.u["op"]
    c.u["zed"] = zed = new_user("zed@example.com", password=ZED_PW)
    c.receipts = []

    def rec(u, path, body):
        key = fresh_key()
        r = expect(u.post(path, body, key=key), 201, msg="build " + path)
        c.receipts.append((u, path, key, body, r.json))
        return r.json

    rec(ada, "/payments", {"to_handle": "bob", "amount": 100, "note": "lunch \U0001F600", "visibility": "public"})
    rec(ada, "/payments", {"to_handle": "cy", "amount": 50, "note": "", "visibility": "private"})
    rec(ada, "/requests/rq_1/pay", {"visibility": "private"})
    c.pending = rec(bob, "/requests", {"payer_handle": "ada", "amount": 200, "note": "pending one"})["request_id"]
    cancelled = rec(bob, "/requests", {"payer_handle": "cy", "amount": 5})["request_id"]
    expect(bob.post("/requests/%s/cancel" % cancelled), 200)
    declined = rec(zed, "/requests", {"payer_handle": "ada", "amount": 9})["request_id"]
    expect(ada.post("/requests/%s/decline" % declined), 200)
    rec(ada, "/splits", {"amount": 301, "participant_handles": ["ada", "bob", "cy"], "note": "split"})
    rec(op, "/settlements", {"transfers": [
        {"from_handle": "ada", "to_handle": "bob", "amount": 40, "note": "s1", "visibility": "private"},
        {"from_handle": "bob", "to_handle": "cy", "amount": 20, "note": "s2"},
        {"from_handle": "cy", "to_handle": "ada", "amount": 10}]})
    rec(zed, "/requests", {"payer_handle": "bob", "amount": 3})
    # failed keys (must stay reusable)
    c.kf1 = fresh_key()
    expect(ada.post("/payments", {"to_handle": "bob", "amount": 0}, key=c.kf1), 422)
    c.kf2 = fresh_key()
    expect(zed.post("/payments", {"to_handle": "bob", "amount": 1}, key=c.kf2), 409, "insufficient_funds")
    c.seed_total = SEED_TOTAL
    c.obs = observe(c)
    eq(sum(o["me"]["balance"] for o in c.obs.values()), SEED_TOTAL, "build: conservation")
    return c


def observe(c):
    out = {}
    for h, u in c.u.items():
        out[h] = {"me": u.me(), "payments": u.all_payments(), "requests": u.all_requests()}
    return out


def alt_body(path, body):
    b = copy.deepcopy(body)
    if path == "/settlements":
        b["transfers"][0]["amount"] += 1
    elif path.endswith("/pay"):
        b = {} if body.get("visibility") == "private" else {"visibility": "private"}
    else:
        b["amount"] += 1
    return b


def all_ids(obs, c):
    ids = set()
    for o in obs.values():
        ids |= {p["payment_id"] for p in o["payments"]}
        ids |= {r["request_id"] for r in o["requests"]}
    for _, path, _, _, j in c.receipts:
        for k in ("split_id", "settlement_id"):
            if k in j:
                ids.add(j[k])
    return ids


def verify(c, label, mutate=True):
    """Everything the spec says must survive an import."""
    now = observe(c)
    eq(dumps(now), dumps(c.obs), "%s: balances, payments (ids, timestamps, order) and requests must be exactly as exported" % label)
    eq(sum(o["me"]["balance"] for o in now.values()), c.seed_total, "%s: conservation" % label)
    # hashed-password login for seeded and signup accounts
    for h in SEED:
        expect(http("POST", "/auth/login", body={"email": h + "@example.com", "password": PASSWORD}), 200, msg="%s: login %s" % (label, h))
        expect(http("POST", "/auth/login", body={"email": h + "@example.com", "password": PASSWORD + "x"}), 401, "unauthenticated")
    expect(http("POST", "/auth/login", body={"email": "zed@example.com", "password": ZED_PW}), 200, msg="%s: signup user login" % label)
    expect(http("POST", "/auth/login", body={"email": "zed@example.com", "password": PASSWORD}), 401, "unauthenticated")
    # handles and currency
    eq(now["zed"]["me"]["handle"], "zed", "derived handle preserved")
    eq((now["ada"]["me"]["currency"], now["ada"]["me"]["minor_units"]), ("EUR", 2), "currency preserved")
    # every completed idempotent request replays with its original response; changed bodies conflict
    for u, path, key, body, original in c.receipts:
        r = expect(u.post(path, body, key=key), 200, msg="%s: retry of %s after import" % (label, path))
        eq(r.json, original, "%s: replayed response of %s" % (label, path))
        expect(u.post(path, alt_body(path, body), key=key), 409, "idempotency_key_reuse", "%s: %s with changed body" % (label, path))
    eq(dumps(observe(c)), dumps(c.obs), "%s: replays must not change anything" % label)
    # settlement membership and operator permission preserved
    sid = [j for _, p, _, _, j in c.receipts if p == "/settlements"][0]
    ada = c.u["ada"]
    members = {p["payment_id"]: p for p in ada.all_payments() if p["settlement_id"] == sid["settlement_id"]}
    ok(len(members) >= 2, "%s: settlement members keep their settlement_id" % label)
    expect(ada.post("/settlements", {"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 1}]}, key=fresh_key()),
           403, "forbidden", "%s: non-operator still forbidden" % label)
    if not mutate:
        return
    op = c.u["op"]
    r = expect(op.post("/settlements", {"transfers": [{"from_handle": "ada", "to_handle": "cy", "amount": 1}]}, key=fresh_key()),
               201, msg="%s: operator permission preserved" % label)
    new_ids = {r.json["settlement_id"]} | {p["payment_id"] for p in r.json["payments"]}
    # failed keys are still first uses
    expect(ada.post("/payments", {"to_handle": "bob", "amount": 1}, key=c.kf1), 201, msg="%s: failed key reusable" % label)
    r = expect(c.u["bob"].post("/payments", {"to_handle": "cy", "amount": 1}, key=c.kf2), 201, msg="%s: failed key reusable (409 case)" % label)
    new_ids.add(r.json["payment_id"])
    # new resources get new, non-colliding ids
    q = expect(c.u["bob"].request("cy", 2), 201).json
    s = expect(ada.split(10, ["ada", "bob"]), 201).json
    pid = expect(ada.pay("cy", 1), 201).json["payment_id"]
    new_ids |= {q["request_id"], s["split_id"], pid}
    old = all_ids(c.obs, c)
    ok(not (new_ids & old), "%s: ids created after import collide with imported ones: %r" % (label, new_ids & old))
    # the imported pending request is payable exactly once
    expect(ada.pay_request(c.pending), 201, msg="%s: imported pending request is payable" % label)
    expect(ada.pay_request(c.pending), 409, "request_not_pending")
    expect(c.u["bob"].pay_request(c.pending), 403, "forbidden")
    eq(sum(u.balance for u in c.u.values()), c.seed_total, "%s: conservation after new activity" % label)


def export():
    r = expect(http("GET", "/_test/export"), 200, msg="export")
    ok(isinstance(r.json, dict), "export is a JSON object")
    return r


def import_(obj_or_resp, status=204, code=None, raw=None):
    if raw is not None:
        r = http("POST", "/_test/import", raw=raw)
    else:
        body = obj_or_resp.json if isinstance(obj_or_resp, Resp) else obj_or_resp
        r = http("POST", "/_test/import", body=body)
    expect(r, status, code, "import")
    if status == 204:
        eq(r.raw, b"", "import returns an empty body")
    return r


@test("S1-170", "S1-171")
def test_export_envelope():
    reset(fixture([fx_user("ada", 5)]))
    r = export()
    j = r.json
    eq(j["track"], "pocketful", "track")
    ok(is_int(j["format_version"]) and j["format_version"] == 1, "format_version must be the integer 1: %r" % (j["format_version"],))
    ok(isinstance(j["state"], dict), "state must be a JSON object")
    ok("application/json" in r.headers.get("content-type", ""), "json content type")
    # unauthenticated, repeatable, and read-only
    r2 = expect(http("GET", "/_test/export", headers={"Authorization": "Bearer junk"}), 200)
    ada = login("ada@example.com", handle="ada")
    eq(ada.balance, 5, "export changed nothing")
    expect(http("POST", "/_test/import", body=j), 204, msg="an unchanged export is accepted")
    eq(login("ada@example.com", handle="ada").balance, 5, "state after re-import")


@test("S1-172", "S1-174", "S1-179", "S1-180", "S1-181", "S1-182", "S1-183", "S1-204", "S1-005", "S1-007")
def test_roundtrip_restores_everything():
    c = build()
    ex = export()
    verify(c, "baseline (read-only)", mutate=False)      # exporting changed nothing and replays work
    ex = export()                                         # the baseline verify made no changes; re-export for a clean copy
    # later activity in the source, then a reset to a DIFFERENT universe with its own users and keys
    late = new_user("late@example.com")
    expect(c.u["ada"].pay("bob", 7), 201)
    reset(fixture([fx_user("ada", 1, uid="u_other_ada", email="ada@example.com"), fx_user("dest", 9)]))
    dest = login("dest@example.com", handle="dest")
    dest_key = fresh_key()
    expect(dest.pay("ada", 1, key=dest_key), 201)
    dest_token = dest.token
    # import into the (different) destination
    import_(ex)
    expect(http("GET", "/me", token=dest_token), 401, "unauthenticated", "destination-only tokens are gone")
    expect(http("GET", "/me", token=late.token), 401, "unauthenticated", "post-export users are gone")
    expect(http("POST", "/auth/login", body={"email": "dest@example.com", "password": PASSWORD}), 401, "unauthenticated")
    expect(http("POST", "/auth/login", body={"email": "late@example.com", "password": PASSWORD}), 401, "unauthenticated")
    verify(c, "after first import", mutate=False)
    # repeating the import restores the same state, nothing duplicated
    import_(ex)
    verify(c, "after repeated import", mutate=False)
    import_(ex)
    import_(ex)
    verify(c, "after four imports", mutate=True)
    # post-import activity does not leak into a later import of the same export
    import_(ex)
    verify(c, "import is replacement, not merge", mutate=False)
    # destination-only handle is free again
    s = new_user("dest@example.com")
    eq(s.handle, "dest", "handle of a removed destination user is free")


@test("S1-178", "S1-174", "S1-171")
def test_export_is_a_frozen_read_only_snapshot():
    w = world({"ada": 1000, "bob": 0})
    ada, bob = w["ada"], w["bob"]
    expect(ada.pay("bob", 10), 201)
    before = (ada.me(), ada.all_payments())
    e1 = export()
    eq((ada.me(), ada.all_payments()), before, "export is read-only")
    expect(ada.pay("bob", 20), 201)
    zed = new_user("zed@example.com")
    e2 = export()
    eq(ada.balance, 970, "second export is read-only too")
    import_(e1)
    eq((ada.me(), ada.all_payments()), before, "later writes do not alter an earlier export")
    expect(http("GET", "/me", token=zed.token), 401, "unauthenticated", "user created after e1")
    import_(e2)
    eq(ada.balance, 970, "e2 contains the later write")
    eq(len(ada.all_payments()), 2, "both payments")
    expect(http("GET", "/me", token=zed.token), 200, msg="e2 contains zed")
    import_(e1)
    eq(ada.balance, 990, "and back to e1")


@test("S1-178", "S1-005", "S1-006", "S1-117", "S1-205", "S1-174")
def test_exports_taken_during_a_storm_are_consistent_snapshots():
    seed = {"a": 800, "b": 800, "c": 800, "d": 800, "op": 0}
    w = world(seed, operators=["op"])
    us = [w[h] for h in "abcd"]
    op = w["op"]
    stop = []
    exports = []

    def storm(worker):
        i = worker
        while not stop:
            a, b, c_ = us[i % 4], us[(i + 1) % 4], us[(i + 2) % 4]
            if i % 3 == 0:
                a.pay(b.handle, 37)
            elif i % 3 == 1:
                op.settle([{"from_handle": a.handle, "to_handle": b.handle, "amount": 25},
                           {"from_handle": b.handle, "to_handle": c_.handle, "amount": 10}])
            else:
                a.pay(c_.handle, 3)
            i += 4
            time.sleep(0.04)

    threads = [threading.Thread(target=storm, args=(k,)) for k in range(8)]
    for t in threads:
        t.start()
    try:
        for _ in range(6):
            time.sleep(0.4)
            exports.append(export())
    finally:
        stop.append(1)
        for t in threads:
            t.join()
    ledger_consistent(us, seed, "live service after the storm")
    eq(total(us), 3200, "live conservation")
    for i, e in enumerate(exports):
        import_(e)
        ledger_consistent(us, seed, "snapshot %d" % i)
        eq(total(us), 3200, "snapshot %d conserves money" % i)
    # settlement membership in a snapshot is all-or-nothing (2 members per settlement, public)
    import_(exports[-1])
    per = {}
    for p in us[0].all_payments():
        if p["settlement_id"]:
            per.setdefault(p["settlement_id"], []).append(p)
    for sid, ps in per.items():
        eq(len(ps), 2, "settlement %s in a snapshot has both members" % sid)


@test("S1-175", "S1-176", "S1-172")
def test_import_rejects_invalid_input_without_touching_state():
    c = build()
    ex = export()
    good = ex.json

    def untouched(why):
        eq(dumps(observe(c)), dumps(c.obs), "destination changed by a rejected import: " + why)

    for raw in ("{", "", "not json", "[1,", "{\"track\": "):
        import_(None, 400, "malformed_request", raw=raw)
        untouched(repr(raw))

    def mutated(fn):
        o = copy.deepcopy(good)
        fn(o)
        return o

    cases = [
        ("empty object", {}),
        ("missing track", mutated(lambda o: o.pop("track"))),
        ("missing format_version", mutated(lambda o: o.pop("format_version"))),
        ("missing state", mutated(lambda o: o.pop("state"))),
        ("wrong track", mutated(lambda o: o.__setitem__("track", "other"))),
        ("track number", mutated(lambda o: o.__setitem__("track", 7))),
        ("track null", mutated(lambda o: o.__setitem__("track", None))),
        ("version 2", mutated(lambda o: o.__setitem__("format_version", 2))),
        ("version 0", mutated(lambda o: o.__setitem__("format_version", 0))),
        ("version string", mutated(lambda o: o.__setitem__("format_version", "1"))),
        ("version null", mutated(lambda o: o.__setitem__("format_version", None))),
        ("version float", mutated(lambda o: o.__setitem__("format_version", 1.5))),
        ("state string", mutated(lambda o: o.__setitem__("state", "garbage"))),
        ("state number", mutated(lambda o: o.__setitem__("state", 5))),
        ("state null", mutated(lambda o: o.__setitem__("state", None))),
        ("state array", mutated(lambda o: o.__setitem__("state", []))),
        ("state is not one of ours", mutated(lambda o: o.__setitem__("state", {"nonsense": True}))),
    ]
    for why, body in cases:
        import_(body, 422, "validation_failed")
        untouched(why)
        expect(http("GET", "/me", token=c.u["ada"].token), 200, msg="tokens still valid after rejected import: " + why)
    # extra top-level fields are ignored
    o = copy.deepcopy(good)
    o["comment"] = "ignored"
    import_(o)
    verify(c, "after import with an unknown top-level field", mutate=False)


@test("S1-184", "S1-183", "S1-027", "S1-182")
def test_reset_clears_imported_state():
    c = build()
    ex = export()
    reset(fixture([fx_user("ada", 3)]))
    import_(ex)
    verify(c, "imported", mutate=False)
    # one more key and token that exist only after the import
    extra = new_user("extra@example.com")
    k = fresh_key()
    expect(c.u["ada"].pay("bob", 1, key=k), 201)
    reset(fixture([fx_user("ada", 3, uid="u_ada"), fx_user("bob", 0), fx_user("op", 0)]))
    for h, u in c.u.items():
        expect(http("GET", "/me", token=u.token), 401, "unauthenticated", "imported token of %s after reset" % h)
    expect(http("GET", "/me", token=extra.token), 401, "unauthenticated")
    for e in ("cy@example.com", "zed@example.com", "extra@example.com"):
        expect(http("POST", "/auth/login", body={"email": e, "password": PASSWORD}), 401, "unauthenticated", e)
    ada = login("ada@example.com", handle="ada")
    bob = login("bob@example.com", handle="bob")
    eq(ada.all_payments(), [], "imported payments are gone")
    eq(ada.all_requests(), [], "imported requests are gone")
    eq(ada.balance, 3, "fixture balance")
    # keys from the imported state are first uses again (same user id, same key, different body)
    expect(ada.pay("bob", 2, key=k), 201, msg="imported idempotency record cleared by reset")
    # imported operator is no longer an operator
    op = login("op@example.com", handle="op")
    expect(op.post("/settlements", {"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 1}]}, key=fresh_key()),
           403, "forbidden", "operator list replaced by reset")
    # imported ids do not linger
    expect(ada.pay_request("rq_1"), 404, "not_found")
    s = new_user("zed@example.com")
    eq(s.handle, "zed", "handle free after reset")


@test("S1-177", "S1-172", "S1-017")
def test_export_import_of_a_large_state_within_10s():
    n = 200
    users = [fx_user("m%03d" % i, 1000 + i) for i in range(n)]
    payments = [{"id": "sp_%d" % i, "from_user_id": "u_m%03d" % (i % n), "to_user_id": "u_m%03d" % ((i + 1) % n),
                 "amount": 5, "note": "n%d" % i, "visibility": "public" if i % 2 else "private"} for i in range(300)]
    requests = [{"id": "sr_%d" % i, "requester_id": "u_m%03d" % i, "payer_id": "u_m%03d" % ((i + 7) % n),
                 "amount": 9, "note": "", "status": "pending"} for i in range(100)]
    reset(fixture(users, payments=payments, requests=requests))
    u0 = login("m000@example.com", handle="m000")
    expect(u0.pay("m001", 3), 201)
    t0 = time.time()
    ex = export()
    t_export = time.time() - t0
    ok(t_export < 10.0, "export took %.1fs" % t_export)
    reset(fixture([fx_user("tiny", 1)]))
    t0 = time.time()
    import_(ex)
    t_import = time.time() - t0
    ok(t_import < 10.0, "import took %.1fs" % t_import)
    eq(u0.balance, 1000 - 3, "state restored")
    for i in (5, 123, 199):
        u = login("m%03d@example.com" % i, handle="m%03d" % i)
        ok(u.balance >= 1000 + i - 5 - 9 - 5, "balance of %d" % i)
    ok(len(u0.all_requests()) >= 1, "requests listed")
    res = burst([lambda i=i: http("POST", "/auth/login", body={"email": "m%03d@example.com" % i, "password": PASSWORD})
                 for i in range(50)])
    ok(all(r.status == 200 for r in res), "logins after a large import")


@test("S1-059", "S1-058", "S1-005", "S1-172", "S1-008")
def test_exact_arithmetic_at_the_edge_of_the_range():
    big_a = 9_007_199_254_740_001          # odd, just below 2**53
    big_b = 4_503_599_627_370_497          # 2**52 + 1
    reset(fixture([fx_user("ada", big_a), fx_user("bob", big_b), fx_user("cy", 3), fx_user("op", 0)], operators=["u_op"]))
    ada, bob, cy, op = (login(h + "@example.com", handle=h) for h in ("ada", "bob", "cy", "op"))
    eq((ada.balance, bob.balance, cy.balance), (big_a, big_b, 3), "seeded values exact")
    expect(ada.pay("bob", 1_000_000_000), 201)
    eq((ada.balance, bob.balance), (big_a - 10**9, big_b + 10**9), "exact after a payment")
    expect(bob.pay("ada", 1), 201)
    eq((ada.balance, bob.balance), (big_a - 10**9 + 1, big_b + 10**9 - 1), "exact after a 1-unit payment")
    rid = expect(bob.request("ada", 999_999_999), 201).json["request_id"]
    expect(ada.pay_request(rid), 201)
    expect(op.settle([{"from_handle": "ada", "to_handle": "cy", "amount": 777_777_777},
                      {"from_handle": "bob", "to_handle": "ada", "amount": 1}]), 201)
    a = big_a - 10**9 + 1 - 999_999_999 - 777_777_777 + 1
    b = big_b + 10**9 - 1 + 999_999_999 - 1
    c = 3 + 777_777_777
    eq((ada.balance, bob.balance, cy.balance), (a, b, c), "exact after request pay and settlement")
    eq(ada.balance + bob.balance + cy.balance, big_a + big_b + 3, "exact conservation (python integers)")
    ex = export()
    reset(fixture([fx_user("zzz", 1)]))
    import_(ex)
    eq((ada.balance, bob.balance, cy.balance), (a, b, c), "exact after export/import")
    # a balance of exactly 2**53 - 1 can still send and receive exactly
    reset(fixture([fx_user("ada", 2**53 - 1), fx_user("bob", 0)]))
    ada, bob = login("ada@example.com", handle="ada"), login("bob@example.com", handle="bob")
    eq(ada.balance, 2**53 - 1, "2**53-1 seeded")
    expect(ada.pay("bob", 1), 201)
    eq((ada.balance, bob.balance), (2**53 - 2, 1), "exact")
    expect(ada.pay("bob", 1_000_000_000), 201)
    eq(ada.balance, 2**53 - 2 - 1_000_000_000, "exact")
    # values above 2**31 behave like any other
    reset(fixture([fx_user("ada", 3_000_000_000), fx_user("bob", 0)]))
    ada, bob = login("ada@example.com", handle="ada"), login("bob@example.com", handle="bob")
    for _ in range(3):
        expect(ada.pay("bob", 1_000_000_000), 201)
    eq((ada.balance, bob.balance), (0, 3_000_000_000), "no 32-bit overflow")
    expect(ada.pay("bob", 1), 409, "insufficient_funds")


@test("S1-173", "S1-179", "S1-182", "S1-183", "S1-204")
def test_import_into_a_different_instance():
    base2 = os.environ.get("PROBE_BASE_URL_2")
    if not base2:
        raise Skip("set PROBE_BASE_URL_2 to a second, independently started container to exercise cross-instance import")
    c = build()
    ex = export()
    with using_base(base2):
        for _ in range(60):
            try:
                if http("GET", "/health", timeout=2).status == 200:
                    break
            except Exception:  # noqa
                time.sleep(0.5)
        reset(fixture([fx_user("ada", 1, uid="u_other"), fx_user("dest", 9)]))
        dest = login("dest@example.com", handle="dest")
        import_(ex)
        expect(http("GET", "/me", token=dest.token), 401, "unauthenticated", "destination-only token gone")
        verify(c, "second container", mutate=True)
