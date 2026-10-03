"""Stage 2: export/import with authorizations, expiry through import, upgrade from a stage-1 export."""
import copy
import json
import os
import threading
import time

from lib2 import *  # noqa: F401,F403

SEED = {"ada": 10000, "bob": 3000, "cy": 0, "op": 0}
SEED_TOTAL = sum(SEED.values())


def dumps(o):
    return json.dumps(o, sort_keys=True, ensure_ascii=False)


class Ctx:
    pass


def build2():
    exp_future, exp_past = in_(hours=3), in_(hours=-4)
    users = [fx_user(h, b) for h, b in SEED.items()]
    seeded = [fx_auth("a_s1", "ada", "bob", 500, "open", exp_future, note="seeded", visibility="private"),
              fx_auth("a_s2", "ada", "bob", 40000, "open", exp_past),
              fx_auth("a_s3", "ada", "cy", 70, "captured", exp_future)]
    reset(fixture(users, operators=["u_op"], authorization_ttl_seconds=900, authorizations=seeded))
    c = Ctx()
    c.u = {h: login(h + "@example.com", handle=h) for h in SEED}
    ada, bob, cy, op = c.u["ada"], c.u["bob"], c.u["cy"], c.u["op"]
    c.receipts = []

    def rec(u, path, body):
        key = fresh_key()
        r = expect(u.post(path, body, key=key), 201, msg="build " + path)
        c.receipts.append((u, path, key, body, r.json))
        return r.json

    open_a = rec(ada, "/authorizations", {"to_handle": "bob", "amount": 1000, "note": "x", "visibility": "private"})
    part = rec(ada, "/authorizations", {"to_handle": "cy", "amount": 800, "note": "partial"})
    rec(cy, "/authorizations/%s/capture" % part["authorization_id"], {"amount": 300, "final": False})
    full = rec(ada, "/authorizations", {"to_handle": "bob", "amount": 400})
    rec(bob, "/authorizations/%s/capture" % full["authorization_id"], {})
    gone = rec(ada, "/authorizations", {"to_handle": "bob", "amount": 200})
    expect(void(ada, gone["authorization_id"]), 200)
    rec(ada, "/payments", {"to_handle": "bob", "amount": 50})
    rec(bob, "/requests", {"payer_handle": "ada", "amount": 70})
    rec(op, "/settlements", {"transfers": [{"from_handle": "ada", "to_handle": "cy", "amount": 25, "visibility": "private"}]})
    c.open_id, c.part_id = open_a["authorization_id"], part["authorization_id"]
    c.kf1 = fresh_key()
    expect(ada.post("/authorizations", {"to_handle": "bob", "amount": 0}, key=c.kf1), 422)
    c.kf2 = fresh_key()
    expect(ada.post("/authorizations", {"to_handle": "bob", "amount": 10**9}, key=c.kf2), 409, "insufficient_funds")
    c.kf3 = fresh_key()
    expect(capture(bob, c.open_id, {"amount": 5000}, key=c.kf3), 422, "capture_exceeds_authorization")
    c.obs = observe2(c)
    eq(sum(o["me"]["total"] for o in c.obs.values()), SEED_TOTAL, "build: totals")
    return c


def observe2(c):
    out = {}
    for h, u in c.u.items():
        out[h] = {"me": me_ok(u), "payments": u.all_payments(), "requests": u.all_requests(),
                  "auths": all_auths(u)}
    return out


def alt_body2(path, body):
    b = copy.deepcopy(body)
    if path == "/settlements":
        b["transfers"][0]["amount"] += 1
    elif path.endswith("/capture"):
        b = {"amount": b.get("amount", 1) + 1, "final": False} if "final" in b else {"amount": 1}
    else:
        b["amount"] += 1
    return b


def all_ids2(obs, c):
    ids = set()
    for o in obs.values():
        ids |= {p["payment_id"] for p in o["payments"]}
        ids |= {r["request_id"] for r in o["requests"]}
        ids |= {a["authorization_id"] for a in o["auths"]}
    for _, path, _, _, j in c.receipts:
        for k in ("split_id", "settlement_id"):
            if k in j:
                ids.add(j[k])
    return ids


def verify2(c, label, mutate=True, ttl=900):
    now = observe2(c)
    eq(dumps(now), dumps(c.obs),
       "%s: balances, holds, payments, requests and authorizations (ids, timestamps, statuses, captures) must be exactly as exported" % label)
    eq(sum(o["me"]["total"] for o in now.values()), SEED_TOTAL, "%s: totals" % label)
    for h in SEED:
        expect(http("POST", "/auth/login", body={"email": h + "@example.com", "password": PASSWORD}), 200, msg="%s: login %s" % (label, h))
    for u, path, key, body, original in c.receipts:
        r = expect(u.post(path, body, key=key), 200, msg="%s: retry of %s" % (label, path))
        eq(r.json, original, "%s: replayed response of %s" % (label, path))
        expect(u.post(path, alt_body2(path, body), key=key), 409, "idempotency_key_reuse", "%s: %s with changed body" % (label, path))
    eq(dumps(observe2(c)), dumps(c.obs), "%s: replays must not change anything" % label)
    if not mutate:
        return
    ada, bob, cy = c.u["ada"], c.u["bob"], c.u["cy"]
    # failed keys are first uses
    expect(ada.post("/authorizations", {"to_handle": "bob", "amount": 1}, key=c.kf1), 201, msg="%s: failed 422 key reusable" % label)
    expect(ada.post("/authorizations", {"to_handle": "bob", "amount": 2}, key=c.kf2), 201, msg="%s: failed 409 key reusable" % label)
    expect(capture(bob, c.open_id, {"amount": 100, "final": False}, key=c.kf3), 201, msg="%s: failed capture key reusable" % label)
    # configuration and ids
    n = expect(authorize(ada, "cy", 5), 201).json
    eq(seconds_between(n["created_at"], n["expires_at"]), ttl, "%s: authorization_ttl_seconds preserved" % label)
    new_ids = {n["authorization_id"]}
    new_ids.add(expect(ada.pay("bob", 1), 201).json["payment_id"])
    new_ids.add(expect(bob.request("cy", 2), 201).json["request_id"])
    ok(not (new_ids & all_ids2(c.obs, c)), "%s: ids created after import collide with imported ones" % label)
    # imported open authorizations are alive: seeded one, the partial one, the open one
    r = expect(capture(bob, "a_s1", {}), 201, msg="%s: seeded open authorization still capturable" % label)
    eq(r.json["authorization_id"], "a_s1", "authorization_id")
    r = expect(capture(cy, c.part_id, {}), 201, msg="%s: partially captured authorization still capturable" % label)
    eq(r.json["amount"], 500, "remaining of the partial authorization")
    got = auth_by_id(cy, c.part_id)
    eq((got["status"], got["captured_amount"], len(got["payment_ids"])), ("captured", 800, 2), "%s: capture records accumulate" % label)
    expect(void(ada, c.open_id), 200, msg="%s: open authorization voidable" % label)
    expect(capture(bob, "a_s2", {}), 409, "authorization_expired", "%s: past-expiry seeded authorization stays expired" % label)
    eq(sum(me_ok(u)["total"] for u in c.u.values()), SEED_TOTAL, "%s: totals after new activity" % label)


def export():
    r = expect(http("GET", "/_test/export"), 200, msg="export")
    return r


@test("S2-172", "S2-173", "S2-100", "S2-102", "S2-136", "S2-125", "S2-099")
def test_roundtrip_restores_authorizations_holds_ttl_and_receipts():
    c = build2()
    ex = export()
    ada = c.u["ada"]
    # later activity, then a reset to a different universe with a different TTL and its own users
    late = new_user("late@example.com")
    expect(authorize(ada, "bob", 77), 201)
    reset(fixture([fx_user("ada", 1, uid="u_other_ada", email="ada@example.com"), fx_user("dest", 9)], authorization_ttl_seconds=60))
    dest = login("dest@example.com", handle="dest")
    expect(http("POST", "/_test/import", body=ex.json), 204, msg="import")
    expect(http("GET", "/me", token=dest.token), 401, "unauthenticated", "destination-only token is gone")
    expect(http("GET", "/me", token=late.token), 401, "unauthenticated", "post-export user is gone")
    verify2(c, "after import", mutate=False)
    expect(http("POST", "/_test/import", body=ex.json), 204)
    verify2(c, "after a repeated import", mutate=False)
    expect(http("POST", "/_test/import", body=ex.json), 204)
    verify2(c, "third import", mutate=True)
    # reset clears authorizations, holds and the TTL (omitted -> 600)
    reset(fixture([fx_user("ada", 100, uid="u_ada"), fx_user("bob", 0, uid="u_bob")]))
    a2, b2 = login("ada@example.com", handle="ada"), login("bob@example.com", handle="bob")
    eq(all_auths(a2), [], "no authorizations after reset")
    eq(me_ok(a2)["held"], 0, "no holds after reset")
    n = expect(authorize(a2, "bob", 5), 201).json
    eq(seconds_between(n["created_at"], n["expires_at"]), 600, "TTL back to the default")
    ok(not {"a_s1", "a_s2", "a_s3"} & {x["authorization_id"] for x in all_auths(a2)}, "seeded authorizations of the old world are gone")


@test("S2-172", "S2-108", "S2-089")
def test_expiry_is_absolute_and_survives_import():
    w = world2({"ada": 1000, "bob": 0}, ttl=4)
    ada, bob = w["ada"], w["bob"]
    old = expect(authorize(ada, "bob", 300), 201).json
    sleep_until(old["expires_at"], 1.3)                 # already expired when exported
    fresh = expect(authorize(ada, "bob", 200), 201).json  # created just now, expires in 4 s
    ex = export()
    expect(http("POST", "/_test/import", body=ex.json), 204)
    a = {x["authorization_id"]: x for x in all_auths(ada)}
    eq(a[old["authorization_id"]]["status"], "expired", "an expired authorization is not revived by import")
    eq(parse_ts(a[fresh["authorization_id"]]["expires_at"]), parse_ts(fresh["expires_at"]), "expires_at is not regenerated")
    eq(a[fresh["authorization_id"]]["status"], "open", "still open right after import")
    m = me_ok(ada)
    eq((m["held"], m["available"]), (200, 800), "only the unexpired hold counts after import")
    sleep_until(fresh["expires_at"], 1.3)
    m = me_ok(ada)
    eq((m["held"], m["available"]), (0, 1000), "the imported hold expires at its original deadline")
    eq(auth_by_id(ada, fresh["authorization_id"])["status"], "expired", "expired on the clock after import")


@test("S2-172", "S2-171", "S2-090", "S2-091", "S2-112")
def test_exports_taken_during_a_hold_storm_are_consistent():
    seed = dict((h, 800) for h in ("a", "b", "c", "d"))
    w = world2(seed)
    us = [w[h] for h in seed]
    stop, exports, made, lock = [], [], [], threading.Lock()

    def storm(k):
        i = k
        while not stop:
            a, b = us[i % 4], us[(i + 1) % 4]
            kind = i % 4
            if kind == 0:
                r = authorize(a, b.handle, 60)
                if r.status == 201:
                    with lock:
                        made.append((a, b, r.json["authorization_id"]))
            elif kind == 1:
                with lock:
                    pick = made[(i // 4) % len(made)] if made else None
                if pick:
                    capture(pick[1], pick[2], {"amount": 20, "final": (i // 4) % 3 == 0})
            elif kind == 2:
                a.pay(b.handle, 7)
            else:
                with lock:
                    pick = made[(i // 3) % len(made)] if made else None
                if pick and (i // 4) % 5 == 0:
                    void(pick[0], pick[2])
            i += 4
            time.sleep(0.03)

    ts = [threading.Thread(target=storm, args=(k,)) for k in range(8)]
    for t in ts:
        t.start()
    try:
        for _ in range(6):
            time.sleep(0.4)
            exports.append(export())
    finally:
        stop.append(1)
        for t in ts:
            t.join()
    for i, e in enumerate(exports):
        expect(http("POST", "/_test/import", body=e.json), 204, msg="import snapshot %d" % i)
        eq(sum(me_ok(u)["total"] for u in us), 3200, "snapshot %d: totals" % i)
        ledger_consistent(us, seed, "snapshot %d" % i)
        for u in us:
            m = me_ok(u)
            rem = sum(a["remaining_amount"] for a in all_auths(u, direction="outgoing", status="open"))
            eq(m["held"], rem, "snapshot %d: %s's held equals the remaining of their open authorizations" % (i, u.handle))
            for a in all_auths(u, direction="outgoing"):
                paid = sum(p["amount"] for p in u.all_payments() if p["authorization_id"] == a["authorization_id"])
                eq(paid, a["captured_amount"], "snapshot %d: captured_amount vs capture payments" % i)


@test("S2-080", "S2-081", "S2-082", "S2-172", "S2-094", "S2-003", "S2-106")
def test_stage1_export_is_accepted_by_stage2():
    s1 = os.environ.get("PROBE_STAGE1_BASE_URL")
    if not s1:
        raise Skip("set PROBE_STAGE1_BASE_URL to a running stage-1 service (run_docker.sh --stage1-ref <git-ref>) to test the upgrade")
    import p_export as E1      # stage-1 state builder and its helpers
    with using_base(s1):
        c = E1.build()
        ex = E1.export()
        before = {h: {"me": u.me(), "payments": u.all_payments(), "requests": u.all_requests()} for h, u in c.u.items()}
    # a stage-2 service in an unrelated state receives the old export
    reset(fixture([fx_user("ada", 1, uid="u_other_ada", email="ada@example.com"), fx_user("dest", 9)]))
    dest = login("dest@example.com", handle="dest")
    expect(http("POST", "/_test/import", body=ex.json), 204, msg="a stage-1 export must be accepted")
    expect(http("GET", "/me", token=dest.token), 401, "unauthenticated", "import replaces the destination")

    def strip(o):
        o = copy.deepcopy(o)
        if isinstance(o, dict):
            for nk in ("authorization_id", "refund_of", "correction_batch_id"):     # keys added by later stages, null on old payments
                if o.get(nk, 1) is None:
                    o.pop(nk)
            for k in ("total", "available", "held"):
                o.pop(k, None) if "user_id" in o else None
            return {k: strip(v) for k, v in o.items()}
        if isinstance(o, list):
            return [strip(x) for x in o]
        return o

    after = {h: {"me": u.me(), "payments": u.all_payments(), "requests": u.all_requests()} for h, u in c.u.items()}
    eq(dumps(strip(after)), dumps(strip(before)), "accounts, tokens (same tokens still work), balances, payments and requests carry over")
    for h, u in c.u.items():
        m = me_ok(u)
        eq((m["balance"], m["total"], m["available"], m["held"]), (before[h]["me"]["balance"],) * 3 + (0,), "stage-2 fields derived for %s" % h)
        eq(all_auths(u), [], "no authorizations in a stage-1 state")
    for h in ("ada", "bob", "cy", "op"):
        expect(http("POST", "/auth/login", body={"email": h + "@example.com", "password": PASSWORD}), 200, msg="login " + h)
    # idempotent receipts: replays answer 200 with the original body (a null authorization_id may have been added)
    for u, path, key, body, original in c.receipts:
        r = expect(u.post(path, body, key=key), 200, msg="stage-1 receipt replay " + path)
        eq(dumps(strip(r.json)), dumps(strip(original)), "replay body of " + path)
    # the old pending request is payable, failed keys reusable, operator permission preserved, new features work
    ada, bob = c.u["ada"], c.u["bob"]
    expect(ada.pay_request(c.pending), 201, msg="pending request from the stage-1 state is payable")
    expect(ada.post("/payments", {"to_handle": "bob", "amount": 1}, key=c.kf1), 201, msg="failed key reusable")
    a = expect(authorize(ada, "bob", 100), 201).json["authorization_id"]
    expect(capture(bob, a, {"amount": 60}), 201, msg="authorizations work on the upgraded state")
    seed_total = sum(h["me"]["balance"] for h in before.values())
    eq(sum(me_ok(u)["total"] for u in c.u.values()), seed_total, "totals conserved")
    expect(c.u["op"].post("/settlements", {"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 1}]}, key=fresh_key()),
           201, msg="settlement operator permission preserved")
