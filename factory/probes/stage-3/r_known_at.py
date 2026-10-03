"""Stage 3: the bitemporal views — as_of (effective time) x known_at (recorded time) — against an independent model."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from lib3 import *  # noqa: F401,F403


@test("S3-067", "S3-068", "S3-069", "S3-071", "S3-073", "S3-014", "S3-015", "S3-016", "S3-018", "S3-037", "S3-060", "S3-088", "S3-032", "S3-066")
def test_known_at_and_effective_time_hand_computed():
    now = whole(utcnow())
    t1, tx = now - timedelta(days=3), now - timedelta(days=1)
    opening = {"ada": 500, "bob": 0, "cy": 7}
    w = hist_world(opening, [fx_pay("p_001", "ada", "bob", 100, created_at=t1)])
    ada, bob, cy = w["ada"], w["bob"], w["cy"]
    r = expect(correct(ada, "p_001", 1, 40, fmts(tx), "took effect a day ago"), 201)
    rc = inst(r.json["recorded_at"])
    total = sum(opening.values())

    def view(as_of=None, known_at=None):
        out = {h: me_at(u, as_of=as_of, known_at=known_at)["balance"] for h, u in w.items()}
        eq(sum(out.values()), total, "the sum of balances in every view equals the seeded total (as_of=%s known_at=%s)" % (as_of, known_at))
        return out

    ancient = "1970-01-01T00:00:00+00:00"
    far = "2999-01-01T00:00:00+00:00"
    # nothing was recorded yet: the payment contributes nothing, whatever as_of says
    for as_of in (None, fmts(t1), fmts(tx), far):
        eq(view(as_of, fmts(t1 - timedelta(seconds=1))), {"ada": 500, "bob": 0, "cy": 7}, "known_at before the payment was recorded (as_of=%s)" % as_of)
    eq(view(None, ancient), {"ada": 500, "bob": 0, "cy": 7}, "known_at in 1970")
    # known_at exactly at the first recording: revision 1 is selected ("at or before")
    eq(view(None, fmts(t1)), {"ada": 400, "bob": 100, "cy": 7}, "known_at exactly at recorded_at of revision 1")
    eq(view(far, fmts(t1)), {"ada": 400, "bob": 100, "cy": 7}, "...with as_of in the future")
    just_before = fmt(rc - timedelta(seconds=1))
    eq(view(None, just_before), {"ada": 400, "bob": 100, "cy": 7}, "the correction was not known one second before it was recorded")
    # known_at exactly at the correction's recorded_at: revision 2, applied at ITS effective time
    k = r.json["recorded_at"]
    eq(view(None, k), {"ada": 460, "bob": 40, "cy": 7}, "known_at exactly at recorded_at of revision 2")
    eq(view(None, None), {"ada": 460, "bob": 40, "cy": 7}, "everything known now")
    eq(view(far, far), {"ada": 460, "bob": 40, "cy": 7}, "known_at and as_of in the future")
    eq(view(None, far), {"ada": 460, "bob": 40, "cy": 7}, "known_at in the future")
    # effective time of the corrected revision: not yet effective two days ago, effective from tx on (inclusive)
    mid = fmts(t1 + timedelta(days=1))
    eq(view(mid, None), {"ada": 500, "bob": 0, "cy": 7}, "as_of between the original and the corrected effective time, correction known")
    eq(view(mid, fmts(t1)), {"ada": 400, "bob": 100, "cy": 7}, "same as_of, but only revision 1 known")
    eq(view(fmts(tx - timedelta(seconds=1)), None), {"ada": 500, "bob": 0, "cy": 7}, "one second before the corrected effective time")
    eq(view(fmts(tx), None), {"ada": 460, "bob": 40, "cy": 7}, "exactly at the corrected effective time")
    eq(view(fmts(t1), None), {"ada": 500, "bob": 0, "cy": 7}, "at the ORIGINAL effective time the corrected payment has not happened yet")
    # opening balances stay what they were
    for h, want in opening.items():
        eq(me_at(w[h], as_of=ancient)["balance"], want, "opening balance of %s after the correction" % h)
        eq(me_at(w[h], as_of=ancient, known_at=far)["balance"], want, "opening balance of %s, known_at future" % h)
    # statements: a correction moves the payment into or out of a window
    f_in, t_in = fmts(t1 + timedelta(days=1)), fmts(now + timedelta(hours=1))          # [t1+1d, now+1h): only revision 2 lies inside
    f_out, t_out = fmts(t1 - timedelta(days=1)), fmts(t1 + timedelta(days=1))          # [t1-1d, t1+1d): only revision 1 lies inside
    j = full_stmt(ada, **{"from": f_in, "to": t_in, "known_at": fmts(t1)})
    eq((j["opening"], j["entries"], j["closing"]), (400, [], 400), "revision 1 (effective t1) is outside [t1+1d, now+1h)")
    j = full_stmt(ada, **{"from": f_in, "to": t_in})
    eq((j["opening"], j["closing"], len(j["entries"])), (500, 460, 1), "revision 2 (effective t1+2d) is inside")
    e = j["entries"][0]
    eq((e["revision"], e["delta"], e["balance_after"], e["payment"]["amount"], e["payment"]["payment_id"]), (2, -40, 460, 40, "p_001"), "entry for revision 2")
    same_instant(e["effective_at"], tx, "entry effective_at")
    same_instant(e["recorded_at"], r.json["recorded_at"], "entry recorded_at")
    same_instant(e["payment"]["created_at"], t1, "payment.created_at stays the original")
    j = full_stmt(ada, **{"from": f_out, "to": t_out})
    eq((j["opening"], j["closing"], j["entries"]), (500, 500, []), "now the payment has moved out of [t1-1d, t1+1d)")
    j = full_stmt(ada, **{"from": f_out, "to": t_out, "known_at": fmts(t1)})
    eq((j["opening"], j["closing"], len(j["entries"])), (500, 400, 1), "known_at at revision 1: it is inside")
    e = j["entries"][0]
    eq((e["revision"], e["delta"], e["balance_after"], e["payment"]["amount"]), (1, -100, 400, 100), "entry for revision 1")
    same_instant(e["effective_at"], t1, "revision 1 effective_at")
    same_instant(e["recorded_at"], t1, "revision 1 recorded_at")
    # bob's statement mirrors it
    j = full_stmt(bob, known_at=fmts(t1))
    eq((j["opening"], j["closing"], [(x["delta"], x["revision"]) for x in j["entries"]]), (0, 100, [(100, 1)]), "bob, known_at t1")
    j = full_stmt(bob)
    eq((j["opening"], j["closing"], [(x["delta"], x["revision"]) for x in j["entries"]]), (0, 40, [(40, 2)]), "bob, now")
    j = full_stmt(cy)
    eq((j["opening"], j["closing"], j["entries"]), (7, 7, []), "cy is not a party")
    # echoes, exactly as given
    for kn in (far, "2026-09-24T13:20:00Z", "2026-09-24T15:20:00.250000+02:00"):
        eq(stmt(ada, known_at=kn)["known_at"], kn, "statement echoes known_at")
        j = me_at(ada, known_at=kn)
        eq(j["known_at"], kn, "/me echoes known_at")
    # known_at alone means: as of the instant the request began
    eq(me_at(ada, known_at=fmts(t1))["balance"], 400, "known_at alone: as_of is now")


@test("S3-005", "S3-060", "S3-067", "S3-069", "S3-074", "S3-075", "S3-076", "S3-077", "S3-078", "S3-088", "S3-015", "S3-024", "S3-025", "S3-026", "S3-037", "S3-023", "S3-032", "S3-033")
def test_bitemporal_grid_against_the_model():
    base = whole(utcnow()) - timedelta(days=8)
    h = lambda x: base + timedelta(hours=x)
    opening = {"ada": 1000, "bob": 500, "cy": 300, "dan": 200}
    pays = [fx_pay("p_001", "ada", "bob", 200, created_at=h(1)),
            fx_pay("p_002", "bob", "cy", 150, created_at=h(2)),
            fx_pay("p_003", "cy", "dan", 100, created_at=h(2)),
            fx_pay("p_004", "dan", "ada", 50, created_at=h(3)),
            fx_pay("p_005", "bob", "ada", 80, created_at=h(4), visibility="private")]
    w = hist_world(opening, pays)
    ada, bob, cy, dan = w["ada"], w["bob"], w["cy"], w["dan"]
    total = sum(opening.values())
    q1 = new_payment(ada, "cy", 120)
    q2 = new_payment(cy, "bob", 60)
    plan = [(ada, "p_001", 150, h(1.5), "later"), (bob, "p_002", 0, h(2), "reverse"), (cy, "p_003", 130, h(1), "earlier and bigger"),
            (ada, q1["payment_id"], 100, inst(q1["created_at"]), "smaller"), (dan, "p_004", 70, h(3.5), "later and bigger"),
            (ada, "p_001", 250, h(0.5), "earlier again"), (cy, q2["payment_id"], 60, base, "moved days earlier")]
    revno = {}
    for owner, pid, amount, eff, why in plan:
        cur = revno.get(pid, 1)
        sleep_gap(1.1)
        r = expect(correct(owner, pid, cur, amount, fmt(eff) if eff.microsecond else fmts(eff), why), 201, msg="correction of %s: %s" % (pid, why))
        revno[pid] = r.json["revision"]
    parties = {"p_001": ("u_ada", "u_bob", ada), "p_002": ("u_bob", "u_cy", bob), "p_003": ("u_cy", "u_dan", cy),
               "p_004": ("u_dan", "u_ada", dan), "p_005": ("u_bob", "u_ada", bob),
               q1["payment_id"]: ("u_ada", "u_cy", ada), q2["payment_id"]: ("u_cy", "u_bob", cy)}
    model = model_from_service({uid(k): v for k, v in opening.items()},
                               {pid: (p[0], p[1]) for pid, p in parties.items()}, {pid: p[2] for pid, p in parties.items()})
    ok(model.min_balance_ok(), "the scenario is consistent (every correction was accepted)")
    # sanity of the model's inputs: revision 1 of the seeded payments is (amount, created_at, created_at)
    for p in pays:
        r1 = model.pays[p["id"]]["revs"][0]
        eq((r1[0], r1[1], r1[2], r1[3]), (1, p["amount"], inst(p["created_at"]), inst(p["created_at"])), "revision 1 of %s" % p["id"])
    effs = sorted({r[2] for p in model.pays.values() for r in p["revs"]})
    recs = sorted({r[3] for p in model.pays.values() for r in p["revs"]})
    ok(len(effs) >= 10 and len(recs) >= 10, "the scenario spreads over many instants")
    as_ofs = [None, base - timedelta(days=30), base - timedelta(seconds=1)] + effs + [utcnow() + timedelta(days=400)]
    as_ofs = sorted({a for a in as_ofs if a is not None}) + [None]
    corr_recs = sorted({r[3] for p in model.pays.values() for r in p["revs"][1:]})
    mids = [a + (b - a) / 2 for a, b in zip(corr_recs, corr_recs[1:]) if (b - a).total_seconds() > 0.4]
    known_ats = [None, datetime_min(), base - timedelta(hours=1)] + recs + mids + [utcnow() + timedelta(days=400)]
    known_ats = [k for k in known_ats if k is not None]

    def s(dt):
        return None if dt is None else fmt(dt)

    jobs = [(h_, a, k) for h_ in w for a in as_ofs for k in known_ats + [None]]

    def one(job):
        hh, a, k = job
        got = me_at(w[hh], as_of=s(a), known_at=s(k))
        want = model.balance(uid(hh), a, k)
        return job, got["balance"], want

    with ThreadPoolExecutor(max_workers=8) as ex:
        res = list(ex.map(one, jobs))
    bad = [(j, g, wnt) for j, g, wnt in res if g != wnt]
    ok(not bad, "/me balance differs from the model for %d of %d (user, as_of, known_at) triples; first: user=%s as_of=%s known_at=%s got %s want %s" % (
        len(bad), len(res), bad[0][0][0], s(bad[0][0][1]), s(bad[0][0][2]), bad[0][1], bad[0][2]) if bad else "")
    sums = {}
    for (hh, a, k), g, _ in res:
        sums[(a, k)] = sums.get((a, k), 0) + g
    bad = [key for key, v in sums.items() if v != total]
    ok(not bad, "the sum of balances must equal the seeded total in every historical view; violated for (as_of, known_at) = %r" % (
        (s(bad[0][0]), s(bad[0][1])) if bad else None))
    # the model's current view equals plain /me
    for hh, u in w.items():
        eq(lib2.me_ok(u)["balance"], model.balance(uid(hh)), "current corrected balance of %s" % hh)
    # statements over windows x known_at
    wins = [(None, None), (h(2), h(4)), (h(1), None), (None, h(3)), (h(1.5), h(1.5)), (h(3.5), h(3.5) + timedelta(seconds=1)),
            (base - timedelta(days=1), base), (h(0.5) - timedelta(seconds=1), h(0.5) + timedelta(seconds=1))]
    kn_sub = [None, datetime_min(), base - timedelta(hours=1), recs[-1], recs[len(recs) // 2]] + mids[:3]
    jobs = [(hh, f, to, k) for hh in w for (f, to) in wins for k in kn_sub]

    def one_stmt(job):
        hh, f, to, k = job
        q = {}
        if f is not None:
            q["from"] = fmt(f)
        if to is not None:
            q["to"] = fmt(to)
        if k is not None:
            q["known_at"] = fmt(k)
        got = full_stmt(w[hh], **q)
        want = model.statement(uid(hh), f, to, k)
        return job, got, want

    with ThreadPoolExecutor(max_workers=8) as ex:
        res = list(ex.map(one_stmt, jobs))
    for job, got, want in res:
        label = "statement user=%s window=(%s,%s) known_at=%s" % (job[0], s(job[1]), s(job[2]), s(job[3]))
        eq((got["opening"], got["closing"]), (want["opening"], want["closing"]), label + " opening/closing")
        compare_entries(got["entries"], want["entries"], label)
    # the feed is untouched by all of it
    for u in w.values():
        feed = u.all_payments()
        eq([p["amount"] for p in feed if p["payment_id"] == "p_001"] or [200], [200], "the original amount in the feed")
    # revisions are returned in revision order with strictly increasing recorded_at
    for pid, (_, _, owner) in parties.items():
        rv = revisions(owner, pid)
        for a, b in zip(rv, rv[1:]):
            ok(inst(b["recorded_at"]) > inst(a["recorded_at"]), "recorded_at strictly increases for %s" % pid)


def datetime_min():
    from datetime import datetime, timezone
    return datetime(1970, 1, 1, tzinfo=timezone.utc)
