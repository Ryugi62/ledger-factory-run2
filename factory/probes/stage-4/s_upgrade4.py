"""Stage 4: export/import — a stage-4 state round-trips (snapshots included); exports of stages 1-3 are accepted."""
import json
import os
from datetime import timedelta

from lib4 import *  # noqa: F401,F403
import r_upgrade3 as up3  # noqa: E402


def dumps(o):
    return json.dumps(o, sort_keys=True, ensure_ascii=False)


def pages_all(u, token, limit=3):
    out, off = [], 0
    while True:
        j = stmt(u, snapshot=token, limit=limit, offset=off)
        out.append((j["opening_balance"], j["closing_balance"], j["entries"]))
        if not j["has_more"]:
            return out
        off += limit


class Ctx:
    pass


def build4():
    t = whole(utcnow()) - timedelta(days=7)
    opening = {"ada": 10000, "bob": 4000, "cy": 500, "dan": 300, "op": 0}
    pays = [fx_pay("p_001", "ada", "bob", 700, created_at=t + timedelta(hours=1), note="seed"),
            fx_pay("p_002", "bob", "cy", 300, created_at=t + timedelta(hours=2), visibility="private"),
            fx_pay("p_003", "cy", "ada", 100, created_at=t + timedelta(hours=2))]
    c = Ctx()
    c.t, c.opening = t, opening
    c.seed_total = sum(opening.values())
    c.w = hist_world(opening, pays, operators=["op"], authorization_ttl_seconds=900)
    ada, bob, cy, dan, op = (c.w[h] for h in ("ada", "bob", "cy", "dan", "op"))
    c.receipts = []

    def rec(u, path, body, status=201):
        key = fresh_key()
        r = expect(u.post(path, body, key=key), status, msg="build " + path)
        c.receipts.append((u, path, key, body, r.json))
        return r.json

    c.pay1 = rec(ada, "/payments", {"to_handle": "cy", "amount": 250, "note": "api"})
    rq = rec(bob, "/requests", {"payer_handle": "ada", "amount": 60})
    c.pay2 = rec(ada, "/requests/%s/pay" % rq["request_id"], {})
    c.st = rec(op, "/settlements", {"transfers": [{"from_handle": "ada", "to_handle": "cy", "amount": 30, "visibility": "private"},
                                                  {"from_handle": "cy", "to_handle": "bob", "amount": 10},
                                                  {"from_handle": "bob", "to_handle": "dan", "amount": 20},
                                                  {"from_handle": "ada", "to_handle": "dan", "amount": 5}]})
    au = rec(ada, "/authorizations", {"to_handle": "bob", "amount": 900})
    part = rec(ada, "/authorizations", {"to_handle": "cy", "amount": 500})
    c.cap = rec(cy, "/authorizations/%s/capture" % part["authorization_id"], {"amount": 200, "final": False})
    sleep_gap(1.1)
    c.single = rec(ada, "/payments/p_001/corrections", corr_body(1, 650, fmts(t + timedelta(hours=1, minutes=30)), "first"))
    snap_a = {h: stmt(c.w[h], limit=2) for h in ("ada", "bob", "cy")}                     # tokens that predate the refunds and the batch
    c.snap_frozen = {h: pages_all(c.w[h], snap_a[h]["snapshot"]) for h in snap_a}
    c.snaps = snap_a
    c.refunds = [rec(bob, "/payments/p_001/refunds", {"amount": 100}),
                 rec(cy, "/payments/%s/refunds" % c.pay1["payment_id"], {"amount": 50}),
                 rec(cy, "/payments/%s/refunds" % c.st["payments"][0]["payment_id"], {"amount": 7})]
    sleep_gap(1.1)
    mem = c.st["payments"]
    E = inst(c.st["committed_at"]) - timedelta(minutes=30)
    c.batch = rec(op, "/correction-batches", {"corrections": [item(mem[0]["payment_id"], 1, 20, fmts(E), "settlement fix"), item(mem[1]["payment_id"], 1, 10, fmts(E), "settlement fix"),
                                                             item(mem[2]["payment_id"], 1, 20, fmts(E), "settlement fix"), item(mem[3]["payment_id"], 1, 5, fmts(E), "settlement fix"),
                                                             item("p_002", 1, 280, fmts(t + timedelta(hours=2)), "plus a seeded payment")]})
    snap_b = {h: stmt(c.w[h], limit=3) for h in ("ada", "bob", "cy", "dan")}               # tokens taken after everything
    c.snaps.update({h + "_late": snap_b[h] for h in snap_b})
    c.snap_frozen.update({h + "_late": pages_all(c.w[h], snap_b[h]["snapshot"]) for h in snap_b})
    # failed attempts whose keys must stay reusable
    c.kf_stale = fresh_key()
    expect(op.post("/correction-batches", {"corrections": [item("p_002", 1, 100, fmts(t))]}, key=c.kf_stale), 409, "stale_revision")
    c.kf_incomplete = fresh_key()
    expect(op.post("/correction-batches", {"corrections": [item(mem[0]["payment_id"], 2, 25, fmts(E))]}, key=c.kf_incomplete), 422, "incomplete_settlement")
    c.kf_refund = fresh_key()
    expect(bob.post("/payments/p_001/refunds", {"amount": 10 ** 6}, key=c.kf_refund), 422, "refund_exceeds_payment")
    c.views = [(None, None), (fmts(t + timedelta(hours=1)), None), (fmts(t + timedelta(hours=1, minutes=30)), None),
               ("2999-01-01T00:00:00+00:00", c.single["recorded_at"]), (fmts(E), c.batch["recorded_at"]), ("1970-01-01T00:00:00+00:00", None), (None, fmts(t - timedelta(hours=1)))]
    c.pids = ["p_001", "p_002", "p_003", c.pay1["payment_id"], c.pay2["payment_id"], c.cap["payment_id"]] + [m["payment_id"] for m in mem] + [r["payment_id"] for r in c.refunds]
    c.obs = observe4(c)
    return c


def observe4(c):
    out = {}
    parties = {}
    for h, u in c.w.items():
        o = {"me": lib2.me_ok(u), "feed": feed4(u), "auths": lib2.all_auths(u), "requests": u.all_requests()}
        st = full_stmt(u)
        o["stmt"] = {"opening": st["opening"], "closing": st["closing"], "entries": st["entries"]}
        o["views"] = [me_at(u, as_of=a, known_at=k)["balance"] for a, k in c.views]
        out[h] = o
        for p in o["feed"]:
            if u.id in (p["from_user_id"], p["to_user_id"]):
                parties[p["payment_id"]] = u
    out["revisions"] = {pid: revisions(parties[pid], pid) for pid in c.pids}
    out["snapshots"] = {name: pages_all(c.w[name.split("_")[0]], s["snapshot"]) for name, s in c.snaps.items()}
    return out


def verify4(c, label, mutate=True):
    now = observe4(c)
    eq(dumps(now["snapshots"]), dumps(c.snap_frozen), "%s: saved statements (snapshot tokens) page exactly the frozen entries" % label)
    eq(dumps(now), dumps(c.obs), "%s: balances, feeds, statements, views, revision histories (incl. correction_batch_id) and snapshot pages must be exactly as exported" % label)
    eq(sum(o["me"]["total"] for h, o in now.items() if h not in ("revisions", "snapshots")), c.seed_total, "%s: totals" % label)
    for h in c.w:
        expect(http("POST", "/auth/login", body={"email": h + "@example.com", "password": PASSWORD}), 200, msg="%s: login %s" % (label, h))
    for u, path, key, body, original in c.receipts:
        r = expect(u.post(path, body, key=key), 200, msg="%s: retry of %s" % (label, path))
        eq(r.json, original, "%s: replayed response of %s" % (label, path))
    op, bob = c.w["op"], c.w["bob"]
    ub, pb, kb, bb, _ = c.receipts[-1]                                                  # the batch receipt
    changed = {"corrections": [dict(bb["corrections"][0], amount=19)] + bb["corrections"][1:]}
    expect(ub.post(pb, changed, key=kb), 409, "idempotency_key_reuse", "%s: same batch key, different body" % label)
    eq(dumps(observe4(c)), dumps(c.obs), "%s: replays change nothing" % label)
    if not mutate:
        return
    mem = c.st["payments"]
    E = inst(c.st["committed_at"]) - timedelta(minutes=30)
    # failed keys are first uses
    expect(op.post("/correction-batches", {"corrections": [item("p_002", 2, 270, fmts(c.t + timedelta(hours=2)), "reuse of a stale key")]}, key=c.kf_stale), 201, msg="%s: key of a 409 is reusable" % label)
    expect(op.post("/correction-batches", {"corrections": [item(mem[0]["payment_id"], 2, 25, fmts(E))]}, key=c.kf_incomplete), 422, "incomplete_settlement",
           "%s: settlement membership survived the import" % label)
    expect(bob.post("/payments/p_001/refunds", {"amount": 10 ** 6}, key=c.kf_refund), 422, "refund_exceeds_payment", "%s: refund cap is known" % label)
    # membership: the original four members, no more and no fewer
    full = [item(mem[i]["payment_id"], 2, a, fmts(E), "again") for i, a in enumerate([20, 10, 20, 5])]
    expect(op.post("/correction-batches", {"corrections": full[:3]}, key=fresh_key()), 422, "incomplete_settlement", "%s: three of four members" % label)
    r = expect(op.post("/correction-batches", {"corrections": full}, key=fresh_key()), 201, msg="%s: all four members" % label)
    eq([x["revision"] for x in r.json["revisions"]], [3, 3, 3, 3], "%s: revision numbering continues" % label)
    # refunds still work; immutability still holds; the cap uses the imported refunds
    cy = c.w["cy"]
    expect(cy.post("/payments/%s/refunds" % c.pay1["payment_id"], {"amount": 201}, key=fresh_key()), 422, "refund_exceeds_payment", "%s: 50 already refunded of 250" % label)
    expect(cy.post("/payments/%s/refunds" % c.pay1["payment_id"], {"amount": 200}, key=fresh_key()), 201, msg="%s: exactly the rest" % label)
    expect(correct(cy, c.refunds[1]["payment_id"], 1, 1, c.refunds[1]["created_at"]), 422, "linked_payment_immutable", "%s: an imported refund payment is immutable" % label)
    expect(c.w["ada"].post("/payments/%s/corrections" % c.cap["payment_id"], corr_body(1, 1, c.cap["created_at"], "no"), key=fresh_key()), 422, "linked_payment_immutable",
           "%s: an imported capture is immutable" % label)
    eq(sum(lib2.me_ok(u)["total"] for u in c.w.values()), c.seed_total, "%s: totals after new activity" % label)


@test("S4-049", "S4-004", "S4-045", "S4-043", "S4-036", "S4-039", "S4-046", "S4-040")
def test_stage4_state_roundtrips_with_snapshots_and_membership():
    c = build4()
    ex = expect(http("GET", "/_test/export"), 200, msg="export")
    verify4(c, "same instance", mutate=False)
    reset(fixture([fx_user("zed", 123)]))
    expect(http("POST", "/_test/import", body=ex.json), 204, msg="import")
    verify4(c, "after export -> reset(other fixture) -> import")
    expect(http("POST", "/_test/import", body=ex.json), 204, msg="import again")
    verify4(c, "after importing a second time", mutate=False)


@test("S4-045", "S4-004", "S4-043", "S4-026", "S4-012", "S4-018", "S4-049")
def test_exports_of_stages_1_to_3_are_accepted():
    ran = 0
    for stage, env in ((1, "PROBE_STAGE1_BASE_URL"), (2, "PROBE_STAGE2_BASE_URL"), (3, "PROBE_STAGE3_BASE_URL")):
        url = os.environ.get(env)
        if not url:
            continue
        label = "stage-%d export" % stage
        if stage < 3:
            with lib.using_base(url):
                c = up3.build_old(stage)
            reset(fixture([fx_user("zed", 123)]))
            expect(http("POST", "/_test/import", body=c.export), 204, msg="a stage-%d export must be accepted" % stage)
            parties = up3.check_imported(c)
            up3.verify_old_receipts(c, parties)
            mem = c.settle["payments"]
            ada, bob, cy, op = c.w["ada"], c.w["bob"], c.w["cy"], c.w["op"]
            E = inst(c.settle["committed_at"]) - timedelta(minutes=30)
            owners = {mem[0]["payment_id"]: ada, mem[1]["payment_id"]: cy}
            items = [item(m["payment_id"], 1, m["amount"] - 1, fmts(E), "after upgrade") for m in mem]
            expect(op.post("/correction-batches", {"corrections": items[:1]}, key=fresh_key()), 422, "incomplete_settlement", "%s: settlement membership is retained" % label)
            expect(op.post("/correction-batches", {"corrections": [dict(items[1], effective_at=fmts(E + timedelta(seconds=1))), items[0]]}, key=fresh_key()), 422, "validation_failed",
                   "%s: members need identical instants" % label)
            r = expect(op.post("/correction-batches", {"corrections": items}, key=fresh_key()), 201, msg="%s: the complete imported settlement can be corrected" % label)
            check_batch(r.json, 2)
            # refunds on imported payments
            d = c.direct
            rf = expect(bob.post("/payments/%s/refunds" % d["payment_id"], {"amount": 100}, key=fresh_key()), 201, msg="%s: refund of an imported payment" % label).json
            eq((rf["refund_of"], rf["from_handle"]), (d["payment_id"], "bob"), "refund")
            if stage == 2:
                cap = c.caps[0]
                rcv = {"cy": cy, "bob": bob}[cap["to_handle"]]
                rc = expect(rcv.post("/payments/%s/refunds" % cap["payment_id"], {"amount": 1}, key=fresh_key()), 201, msg="%s: refund of an imported capture" % label).json
                eq((rc["refund_of"], rc["authorization_id"]), (cap["payment_id"], None), "refund of a capture")
                row = lib2.auth_by_id(ada, cap["authorization_id"])
                ok(row["status"] in ("open", "captured"), "authorization not reopened or changed in kind")
            eq(sum(lib2.me_ok(u)["total"] for u in c.w.values()), sum(up3.OLD_END.values()), "%s: totals" % label)
        else:
            with lib.using_base(url):
                c = up3.build3()
                c.w_old = c.w
                snaps = {h: stmt(c.w[h], limit=2) for h in ("ada", "bob", "cy")}
                frozen = {h: pages_all(c.w[h], snaps[h]["snapshot"]) for h in snaps}
                export_ = expect(http("GET", "/_test/export"), 200, msg="export").json
            reset(fixture([fx_user("zed", 123)]))
            expect(http("POST", "/_test/import", body=export_), 204, msg="a stage-3 export must be accepted")
            up3.verify3(c, label)
            # KNOWN GAP (coordinator decision): a stage-3 export never contained the snapshot tokens, so a stage-4 service cannot
            # reconstruct them. If the tokens do page after the import they must page the same frozen entries; if they are
            # unknown (404) it is reported, not failed.
            for h in snaps:
                r_ = stmt_raw(c.w[h], snapshot=snaps[h]["snapshot"], limit="3")
                if r_.status == 404:
                    print("       KNOWN GAP (S4-045): stage-3 snapshot token of %s unknown after the import (stage-3 exports carry no snapshots)" % h)
                    continue
                eq(pages_all(c.w[h], snaps[h]["snapshot"]), frozen[h], "%s: saved statement tokens of %s page the same entries" % (label, h))
            op = c.w["op"]
            mem = c.st["payments"]
            E = inst(c.st["committed_at"]) - timedelta(minutes=30)
            items = [item(m["payment_id"], 1, max(1, m["amount"] - 1), fmts(E), "after upgrade") for m in mem]
            expect(op.post("/correction-batches", {"corrections": items[:1]}, key=fresh_key()), 422, "incomplete_settlement", "%s: settlement membership is retained" % label)
            r = expect(op.post("/correction-batches", {"corrections": items}, key=fresh_key()), 201, msg="%s: complete settlement" % label)
            check_batch(r.json, len(mem))
            cy = c.w["cy"]
            rf = expect(cy.post("/payments/p_002/refunds", {"amount": 50}, key=fresh_key()), 201, msg="%s: refund of an imported seeded payment" % label).json
            eq(rf["refund_of"], "p_002", "refund_of")
            for h in snaps:
                if stmt_raw(c.w[h], snapshot=snaps[h]["snapshot"], limit="3").status == 404:
                    continue                                   # known gap, see above
                eq(pages_all(c.w[h], snaps[h]["snapshot"]), frozen[h], "%s: old snapshots still frozen after batches and refunds" % label)
        ran += 1
    if not ran:
        raise lib.Skip("set PROBE_STAGE1_BASE_URL / PROBE_STAGE2_BASE_URL / PROBE_STAGE3_BASE_URL to running services of the earlier stages (run_docker.sh --stage1-ref/--stage2-ref/--stage3-ref does it)")
