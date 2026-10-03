"""Stage 3: export/import — a stage-3 state round-trips; exports of the stage-1 and stage-2 services are accepted."""
import copy
import json
import os
from datetime import timedelta

from lib3 import *  # noqa: F401,F403


def dumps(o):
    return json.dumps(o, sort_keys=True, ensure_ascii=False)


def export():
    return expect(http("GET", "/_test/export"), 200, msg="export")


def import_(body):
    return expect(http("POST", "/_test/import", body=body), 204, msg="import")


# ------------------------------------------------------------------ a stage-3 state
class Ctx:
    pass


def build3():
    t = whole(utcnow()) - timedelta(days=7)
    opening = {"ada": 10000, "bob": 4000, "cy": 500, "op": 0}
    pays = [fx_pay("p_001", "ada", "bob", 700, created_at=t + timedelta(hours=1), note="seed"),
            fx_pay("p_002", "bob", "cy", 300, created_at=t + timedelta(hours=2), visibility="private"),
            fx_pay("p_003", "cy", "ada", 100, created_at=t + timedelta(hours=2))]
    c = Ctx()
    c.t, c.opening, c.pays = t, opening, pays
    c.seed_total = sum(opening.values())
    c.w = hist_world(opening, pays, operators=["op"], authorization_ttl_seconds=900)
    ada, bob, cy, op = c.w["ada"], c.w["bob"], c.w["cy"], c.w["op"]
    c.receipts = []

    def rec(u, path, body):
        key = fresh_key()
        r = expect(u.post(path, body, key=key), 201, msg="build " + path)
        c.receipts.append((u, path, key, body, r.json))
        return r.json

    c.pay1 = rec(ada, "/payments", {"to_handle": "cy", "amount": 250, "note": "api"})
    rq = rec(bob, "/requests", {"payer_handle": "ada", "amount": 60})
    c.pay2 = rec(ada, "/requests/%s/pay" % rq["request_id"], {})
    c.st = rec(op, "/settlements", {"transfers": [{"from_handle": "ada", "to_handle": "cy", "amount": 30, "visibility": "private"},
                                                  {"from_handle": "cy", "to_handle": "bob", "amount": 10}]})
    au = rec(ada, "/authorizations", {"to_handle": "bob", "amount": 900})
    part = rec(ada, "/authorizations", {"to_handle": "cy", "amount": 500})
    c.cap = rec(cy, "/authorizations/%s/capture" % part["authorization_id"], {"amount": 200, "final": False})
    c.open_id, c.part_id = au["authorization_id"], part["authorization_id"]
    # corrections: a decrease, an increase, a move in time, a zero
    sleep_gap(1.1)
    c.corr = []
    c.corr.append(rec(ada, "/payments/p_001/corrections", corr_body(1, 650, fmts(c.t + timedelta(hours=1, minutes=30)), "first")))
    sleep_gap(1.1)
    c.corr.append(rec(ada, "/payments/p_001/corrections", corr_body(2, 660, fmts(c.t + timedelta(hours=1, minutes=30)), "second")))
    c.corr.append(rec(ada, "/payments/%s/corrections" % c.pay1["payment_id"], corr_body(1, 0, c.pay1["created_at"], "reverse")))
    c.corr.append(rec(cy, "/payments/p_003/corrections", corr_body(1, 110, fmts(c.t + timedelta(hours=3)), "later and bigger")))
    # failed attempts whose keys must stay reusable
    c.kf_stale = fresh_key()
    expect(ada.post("/payments/p_001/corrections", corr_body(1, 100, fmts(c.t), "stale"), key=c.kf_stale), 409, "stale_revision")
    c.kf_linked = fresh_key()
    expect(ada.post("/payments/%s/corrections" % c.st["payments"][0]["payment_id"], corr_body(1, 1, c.st["committed_at"], "no"), key=c.kf_linked),
           422, "linked_payment_immutable")
    c.kf_overdraft = fresh_key()
    expect(ada.post("/payments/p_001/corrections", corr_body(3, 10 ** 9, fmts(c.t), "too much"), key=c.kf_overdraft), 409, "insufficient_funds")
    c.views = [(None, None), (fmts(c.t + timedelta(hours=1)), None), (fmts(c.t + timedelta(hours=1, minutes=30)), None),
               (fmts(c.t + timedelta(hours=2)), fmts(c.t + timedelta(hours=2))), ("2999-01-01T00:00:00+00:00", c.corr[0]["recorded_at"]),
               ("1970-01-01T00:00:00+00:00", None), (None, fmts(c.t - timedelta(hours=1)))]
    c.pids = ["p_001", "p_002", "p_003", c.pay1["payment_id"], c.pay2["payment_id"], c.cap["payment_id"]] + [m["payment_id"] for m in c.st["payments"]]
    c.obs = observe3(c)
    eq(sum(o["me"]["total"] for o in c.obs.values() if "me" in o), c.seed_total, "build: totals")
    return c


def observe3(c):
    out = {}
    parties = {}
    for h, u in c.w.items():
        o = {"me": lib2.me_ok(u), "feed": u.all_payments(), "auths": lib2.all_auths(u), "requests": u.all_requests()}
        st = full_stmt(u)
        o["stmt"] = {"opening": st["opening"], "closing": st["closing"], "entries": st["entries"]}
        o["views"] = [[me_at(u, as_of=a, known_at=k)["balance"] for a, k in c.views]]
        o["windows"] = [full_stmt(u, **{"from": fmts(c.t + timedelta(hours=1, minutes=45))})["entries"],
                        full_stmt(u, to=fmts(c.t + timedelta(hours=2)), known_at=c.corr[0]["recorded_at"])["entries"]]
        out[h] = o
        for p in o["feed"]:
            if u.id in (p["from_user_id"], p["to_user_id"]):
                parties[p["payment_id"]] = u
    out["revisions"] = {pid: revisions(parties[pid], pid) for pid in c.pids}
    return out


def verify3(c, label, mutate=True):
    now = observe3(c)
    eq(dumps(now), dumps(c.obs), "%s: balances, feeds, statements, historical views and revision histories must be exactly as exported" % label)
    eq(sum(o["me"]["total"] for h, o in now.items() if h != "revisions"), c.seed_total, "%s: totals" % label)
    for h in c.w:
        expect(http("POST", "/auth/login", body={"email": h + "@example.com", "password": PASSWORD}), 200, msg="%s: login %s" % (label, h))
    for u, path, key, body, original in c.receipts:
        r = expect(u.post(path, body, key=key), 200, msg="%s: retry of %s" % (label, path))
        eq(r.json, original, "%s: replayed response of %s (including correction receipts)" % (label, path))
    ada, cy = c.w["ada"], c.w["cy"]
    u0, path0, key0, body0, _ = c.receipts[-4]                       # the first correction of p_001
    changed = dict(body0)
    changed["amount"] = body0["amount"] + 1
    expect(u0.post(path0, changed, key=key0), 409, "idempotency_key_reuse", "%s: same key, different body" % label)
    eq(dumps(observe3(c)), dumps(c.obs), "%s: replays must not change anything" % label)
    if not mutate:
        return
    # failed keys are first uses
    expect(ada.post("/payments/p_001/corrections", corr_body(3, 640, fmts(c.t + timedelta(hours=1)), "stale key reused"), key=c.kf_stale), 201,
           msg="%s: key of a 409 stale_revision is reusable" % label)
    expect(ada.post("/payments/%s/corrections" % c.st["payments"][0]["payment_id"], corr_body(1, 1, c.st["committed_at"], "no"), key=c.kf_linked),
           422, "linked_payment_immutable", "%s: settlement member is still immutable" % label)
    expect(cy.post("/payments/%s/corrections" % c.cap["payment_id"], corr_body(1, 1, c.cap["created_at"], "no"), key=fresh_key()),
           403, "forbidden", "%s: (control) the receiver of a capture is not its sender" % label)
    expect(ada.post("/payments/%s/corrections" % c.cap["payment_id"], corr_body(1, 1, c.cap["created_at"], "no"), key=fresh_key()),
           422, "linked_payment_immutable", "%s: a capture is still immutable" % label)
    rv = revisions(ada, "p_001")
    eq([r["revision"] for r in rv], [1, 2, 3, 4], "%s: revision numbering continues after import" % label)
    ok(inst(rv[-1]["recorded_at"]) > inst(rv[-2]["recorded_at"]), "%s: recorded_at keeps increasing" % label)
    # opening balances are untouched by import and by corrections; new activity works and ids do not collide
    for h, want in c.opening.items():
        eq(me_at(c.w[h], as_of="1970-01-01T00:00:00+00:00")["balance"], want, "%s: opening balance of %s" % (label, h))
    p = new_payment(ada, "cy", 1)
    ok(p["payment_id"] not in c.pids and p["payment_id"] not in ("p_001", "p_002", "p_003"), "%s: new ids do not collide with imported ones" % label)
    eq(sum(lib2.me_ok(u)["total"] for u in c.w.values()), c.seed_total, "%s: totals after new activity" % label)


@test("S3-118", "S3-040", "S3-052", "S3-115", "S3-037", "S3-034", "S3-060")
def test_stage3_state_roundtrips_through_export_import():
    c = build3()
    ex = export()
    verify3(c, "same instance", mutate=False)
    reset(fixture([fx_user("zed", 123)]))
    expect(http("POST", "/_test/import", body=ex.json), 204, msg="import")
    verify3(c, "after export -> reset(other fixture) -> import")
    # importing again restores the exported state, and does not duplicate anything
    expect(http("POST", "/_test/import", body=ex.json), 204, msg="import again")
    verify3(c, "after importing a second time", mutate=False)


# ------------------------------------------------------------------ exports of the earlier services
OLD_OPENING = {"ada": 10500, "bob": 2700, "cy": 200, "op": 0}
OLD_END = {"ada": 10000, "bob": 3000, "cy": 400, "op": 0}


def build_old(stage):
    seed = [{"id": "s_1", "from_user_id": "u_ada", "to_user_id": "u_bob", "amount": 500, "note": "seed", "visibility": "public"},
            {"id": "s_2", "from_user_id": "u_bob", "to_user_id": "u_cy", "amount": 200, "note": "seed2", "visibility": "private"}]
    w = lib2.world2(OLD_END, payments=seed, operators=["op"]) if stage == 2 else world(OLD_END, operators=["op"], payments=seed)
    ada, bob, cy, op = w["ada"], w["bob"], w["cy"], w["op"]
    c = Ctx()
    c.w, c.receipts, c.stage = w, [], stage

    def rec(u, path, body):
        key = fresh_key()
        r = expect(u.post(path, body, key=key), 201, msg="old build " + path)
        c.receipts.append((u, path, key, body, r.json))
        return r.json

    c.direct = rec(ada, "/payments", {"to_handle": "bob", "amount": 700, "note": "direct"})
    c.cy_pay = rec(bob, "/payments", {"to_handle": "cy", "amount": 100, "visibility": "private"})
    rq = rec(bob, "/requests", {"payer_handle": "ada", "amount": 50})
    c.paid = rec(ada, "/requests/%s/pay" % rq["request_id"], {})
    c.pending = rec(cy, "/requests", {"payer_handle": "bob", "amount": 40})
    c.settle = rec(op, "/settlements", {"transfers": [{"from_handle": "ada", "to_handle": "cy", "amount": 30, "visibility": "private"},
                                                      {"from_handle": "cy", "to_handle": "bob", "amount": 10}]})
    c.caps = []
    if stage == 2:
        c.a_open = rec(ada, "/authorizations", {"to_handle": "bob", "amount": 1000})
        part = rec(ada, "/authorizations", {"to_handle": "cy", "amount": 800})
        c.caps.append(rec(cy, "/authorizations/%s/capture" % part["authorization_id"], {"amount": 300, "final": False}))
        full = rec(ada, "/authorizations", {"to_handle": "bob", "amount": 400})
        c.caps.append(rec(bob, "/authorizations/%s/capture" % full["authorization_id"], {}))
        gone = rec(ada, "/authorizations", {"to_handle": "bob", "amount": 200})
        expect(lib2.void(ada, gone["authorization_id"]), 200)
        c.part_id, c.full_id, c.gone_id = part["authorization_id"], full["authorization_id"], gone["authorization_id"]
    c.obs = {}
    for h, u in w.items():
        c.obs[h] = {"me": u.me(), "feed": u.all_payments(), "requests": u.all_requests(),
                    "auths": lib2.all_auths(u) if stage == 2 else []}
    c.export = export().json
    return c


def check_imported(c):
    w = c.w
    total = sum(OLD_END.values())
    parties = {}
    for h, u in w.items():
        old = c.obs[h]
        me = u.me()
        eq({k: me[k] for k in old["me"]}, old["me"], "%s: /me (balance%s) equals the old service's" % (h, ", total, available, held" if c.stage == 2 else ""))
        newfeed = u.all_payments()
        eq([{k: x[k] for k in o_} for x, o_ in zip(newfeed, old["feed"])], old["feed"], "%s: feed equals the old service's (ids, amounts, created_at, order)" % h)
        eq(len(newfeed), len(old["feed"]), "%s: feed length" % h)
        eq(u.all_requests(), old["requests"], "%s: requests" % h)
        mine = {p["payment_id"] for p in old["feed"] if u.id in (p["from_user_id"], p["to_user_id"])}
        for pid in mine:
            parties[pid] = u
        st = full_stmt(u)
        eq(set(ids_of_entries(st["entries"])), mine, "%s: the statement lists exactly the payments the user sent or received (captures included, once each)" % h)
        eq(len(st["entries"]), len(mine), "%s: no payment twice" % h)
        eq(st["closing"], old["me"]["balance"], "%s: closing_balance == the balance of the exported state" % h)
        eq(st["opening"], OLD_OPENING[h], "%s: opening balance = ending balance - net effect of the seeded payments" % h)
        eq(me_at(u, as_of="1970-01-01T00:00:00+00:00")["balance"], OLD_OPENING[h], "%s: as_of before everything is the opening balance" % h)
        eq(me_at(u, as_of="2999-01-01T00:00:00+00:00")["balance"], old["me"]["balance"], "%s: as_of in the future" % h)
        eq(me_at(u, known_at="1970-01-01T00:00:00+00:00")["balance"], OLD_OPENING[h], "%s: nothing known in 1970" % h)
        for e in st["entries"]:
            eq((e["revision"], e["payment"]["amount"]), (1, e["payment"]["amount"]), "revision 1")
            same_instant(e["effective_at"], e["payment"]["created_at"], "%s: imported payments take effect at created_at" % h)
            same_instant(e["recorded_at"], e["payment"]["created_at"], "%s: and were recorded then" % h)
        if c.stage == 2:
            for a in lib2.all_auths(u):
                ok("closed_at" in a, "%s: authorizations expose closed_at after import: %r" % (h, a))
                if a["status"] == "open":
                    eq(a["closed_at"], None, "open authorization: closed_at null")
    eq(sum(me_at(u, as_of=a)["balance"] for u in w.values() for a in [None]), total, "total of the imported balances")
    ancient = "1970-01-01T00:00:00+00:00"
    eq(sum(me_at(u, as_of=ancient)["balance"] for u in w.values()), total, "sum of opening balances = sum of ending balances")
    if c.stage == 2:
        for cap in c.caps:
            for u in (w["ada"], w[{"cy": "cy", "bob": "bob"}[cap["to_handle"]]]):
                es = [e for e in full_stmt(u)["entries"] if e["payment"]["payment_id"] == cap["payment_id"]]
                eq(len(es), 1, "a capture appears exactly once in %s's statement" % u.handle)
                eq(es[0]["payment"]["authorization_id"], cap["authorization_id"], "with its link")
    return parties


def verify_old_receipts(c, parties):
    for u, path, key, body, original in c.receipts:
        r = expect(u.post(path, body, key=key), 200, msg="retry of %s after the upgrade" % path)
        eq(r.json, original, "the original response of %s is preserved" % path)
    # corrections on imported ordinary payments work; on settlement members and captures they are refused
    ada, bob = c.w["ada"], c.w["bob"]
    d = c.direct
    start = (ada.me()["balance"], bob.me()["balance"])
    r = expect(correct(ada, d["payment_id"], 1, 600, d["created_at"], "after upgrade"), 201, msg="a payment made on the old service is correctable")
    eq((ada.me()["balance"], bob.me()["balance"]), (start[0] + 100, start[1] - 100), "difference moved")
    eq([x["amount"] for x in revisions(ada, d["payment_id"])], [700, 600], "history of an imported payment")
    eq(pay_obj(ada, d["payment_id"])["amount"], 700, "feed shows the original")
    m = c.settle["payments"][0]
    expect(correct(ada, m["payment_id"], 1, 1, m["created_at"], "no"), 422, "linked_payment_immutable", "settlement member from the old service")
    same_instant(revisions(ada, m["payment_id"])[0]["effective_at"], c.settle["committed_at"], "member effective_at = committed_at")
    for cap in c.caps:
        expect(correct(ada, cap["payment_id"], 1, 1, cap["created_at"], "no"), 422, "linked_payment_immutable", "capture from the old service")
    p = new_payment(ada, "cy", 5)
    ok(p["payment_id"] not in parties, "new ids do not collide with imported ones")
    eq(sum(lib2.me_ok(u)["total"] for u in c.w.values()), sum(OLD_END.values()), "totals after new activity")


@test("S3-094", "S3-095", "S3-036", "S3-034", "S3-118")
def test_exports_of_the_earlier_services_are_accepted():
    ran = 0
    for stage, env in ((1, "PROBE_STAGE1_BASE_URL"), (2, "PROBE_STAGE2_BASE_URL")):
        url = os.environ.get(env)
        if not url:
            continue
        with lib.using_base(url):
            c = build_old(stage)
        reset(fixture([fx_user("zed", 123)]))
        expect(http("POST", "/_test/import", body=c.export), 204, msg="a stage-%d export must be accepted" % stage)
        parties = check_imported(c)
        verify_old_receipts(c, parties)
        ran += 1
    if not ran:
        raise lib.Skip("set PROBE_STAGE1_BASE_URL and/or PROBE_STAGE2_BASE_URL to running stage-1 / stage-2 services (run_docker.sh --stage1-ref/--stage2-ref does it)")
