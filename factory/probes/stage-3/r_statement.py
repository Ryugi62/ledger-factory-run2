"""Stage 3: GET /statement — window, order, balances, privacy, pagination."""
from datetime import timedelta, timezone

from lib3 import *  # noqa: F401,F403

OPENING = {"ada": 500, "bob": 100, "cy": 300, "dan": 0, "eve": 50}


def seeded_world():
    t = whole(utcnow()) - timedelta(days=10)
    pays = [fx_pay("p_001", "ada", "bob", 100, created_at=t + timedelta(hours=1), note="one"),
            fx_pay("p_002", "bob", "cy", 40, created_at=t + timedelta(hours=2), visibility="private", note="two"),
            fx_pay("p_003", "cy", "ada", 25, created_at=t + timedelta(hours=2), note="three"),
            fx_pay("p_004", "cy", "dan", 60, created_at=t + timedelta(hours=3), note="four"),
            fx_pay("p_005", "eve", "ada", 10, created_at=t + timedelta(hours=4), note="five"),
            fx_pay("p_006", "ada", "eve", 5, created_at=t + timedelta(hours=4), visibility="private", note="six")]
    w = hist_world(OPENING, pays)
    return t, pays, w, model_for(OPENING, pays)


@test("S3-020", "S3-022", "S3-023", "S3-024", "S3-025", "S3-026", "S3-027", "S3-029", "S3-030", "S3-031", "S3-078", "S3-003", "S3-079")
def test_statement_matches_the_specification_for_every_user():
    t, pays, w, m = seeded_world()
    feeds = {h: {p["payment_id"]: p for p in u.all_payments()} for h, u in w.items()}
    for h, u in w.items():
        got = full_stmt(u)
        want = m.statement(uid(h))
        eq((got["opening"], got["closing"]), (want["opening"], want["closing"]), "opening/closing of %s" % h)
        compare_entries(got["entries"], want["entries"], "statement of " + h)
        eq(got["closing"], lib2.me_ok(u)["balance"], "closing_balance of the default window equals GET /me (%s)" % h)
        eq(got["opening"], OPENING[h], "opening_balance of the whole history is the opening balance (%s)" % h)
        for e in got["entries"]:
            p = e["payment"]
            eq(p, feeds[h][p["payment_id"]], "statement payment object is the activity payment object (no corrections)")
            eq((p["request_id"], p["settlement_id"], p["authorization_id"]), (None, None, None), "links")
            ok(h in (p["from_handle"], p["to_handle"]), "only payments the caller sent or received appear in the statement")
    # exact expectations for a few entries (hand computed)
    cy = full_stmt(w["cy"])
    eq([(e["payment"]["payment_id"], e["delta"], e["balance_after"]) for e in cy["entries"]],
       [("p_002", 40, 340), ("p_003", -25, 315), ("p_004", -60, 255)], "cy: ties at one instant are ordered by payment id")
    ada = full_stmt(w["ada"])
    eq([(e["payment"]["payment_id"], e["delta"], e["balance_after"]) for e in ada["entries"]],
       [("p_001", -100, 400), ("p_003", 25, 425), ("p_005", 10, 435), ("p_006", -5, 430)], "ada")
    eq(ids_of_entries(full_stmt(w["dan"])["entries"]), ["p_004"], "dan sees only the payment he received, not other people's public payments")
    # the activity-feed visibility rules do not apply to statements
    ok("p_004" in feeds["ada"] and "p_004" not in ids_of_entries(ada["entries"]), "ada's feed shows public p_004 but her statement does not")
    ok("p_002" not in feeds["ada"] and "p_002" in ids_of_entries(full_stmt(w["bob"])["entries"]), "private payment: only its parties")
    ok("p_006" in ids_of_entries(ada["entries"]) and "p_006" in ids_of_entries(full_stmt(w["eve"])["entries"]),
       "a private payment appears in both parties' statements")
    ok("p_006" not in feeds["dan"], "(control) dan's feed hides the private payment")
    # shape of a bare response
    j = stmt(w["ada"])
    eq(set(j) - {"snapshot"}, {"opening_balance", "entries", "closing_balance", "has_more"} | (set(j) & {"known_at"}), "top-level keys")
    eq(j["has_more"], False, "has_more")


@test("S3-022", "S3-024", "S3-025", "S3-020", "S3-070")
def test_window_is_half_open_and_instants_may_use_any_offset():
    t, pays, w, m = seeded_world()
    t1, t2, t3, t4 = [t + timedelta(hours=h) for h in (1, 2, 3, 4)]
    plus9 = timezone(timedelta(hours=9))
    windows = [(t1, t3), (t2, t2), (t2, t4), (t1, None), (None, t3), (t3, t1), (t4, None), (t4 + timedelta(seconds=1), None),
               (None, t1), (None, t1 + timedelta(seconds=1)), (t1 - timedelta(seconds=1), t1)]
    for h, u in w.items():
        for f, to in windows:
            q = {}
            if f is not None:
                q["from"] = f.astimezone(plus9).isoformat(timespec="seconds")      # +09:00 spelling of the same instant
            if to is not None:
                q["to"] = fmts(to)
            if f is not None and to is not None and to < f:
                continue                                                           # reversed window: not specified
            got = full_stmt(u, **q)
            want = m.statement(uid(h), f, to)
            eq((got["opening"], got["closing"]), (want["opening"], want["closing"]), "%s window %s" % (h, q))
            compare_entries(got["entries"], want["entries"], "%s window %s" % (h, q))
    ada = w["ada"]
    eq(ids_of_entries(full_stmt(ada, **{"from": fmts(t1)})["entries"])[:1], ["p_001"], "a payment at exactly `from` is inside")
    eq(full_stmt(ada, to=fmts(t1))["entries"], [], "a payment at exactly `to` is outside")
    j = full_stmt(ada, **{"from": fmts(t1), "to": fmts(t1)})
    eq((j["entries"], j["opening"], j["closing"]), ([], 500, 500), "from == to is an empty window")
    j = full_stmt(ada, **{"from": fmts(t1 + timedelta(seconds=1))})
    eq(j["opening"], 400, "opening_balance includes everything strictly before `from`")


@test("S3-021", "S3-028", "S3-086", "S3-024", "S3-025", "S3-026")
def test_statement_pagination_is_over_the_full_window():
    t = whole(utcnow()) - timedelta(days=20)
    opening = {"ada": 20000, "bob": 20000}
    pays = []
    for i in range(60):
        a, b = ("ada", "bob") if i % 3 else ("bob", "ada")
        pays.append(fx_pay("p_%03d" % i, a, b, 7 + i, created_at=t + timedelta(minutes=i)))
    w = hist_world(opening, pays)
    m = model_for(opening, pays)
    ada = w["ada"]
    want = m.statement(uid("ada"))
    eq(len(want["entries"]), 60, "model")
    d = stmt(ada)
    eq(len(d["entries"]), 50, "default limit is 50")
    eq(d["has_more"], True, "has_more with 10 left")
    eq((d["opening_balance"], d["closing_balance"]), (want["opening"], want["closing"]), "balances on a partial page describe the full window")
    for limit in (1, 7, 25, 60, 61, 200):
        got, off = [], 0
        while True:
            j = stmt(ada, limit=limit, offset=off)
            eq((j["opening_balance"], j["closing_balance"]), (want["opening"], want["closing"]), "balances on every page (limit %d offset %d)" % (limit, off))
            got += j["entries"]
            remaining = 60 - (off + len(j["entries"]))
            eq(j["has_more"], remaining > 0, "has_more at limit %d offset %d" % (limit, off))
            if not j["has_more"]:
                break
            off += limit
        compare_entries(got, want["entries"], "reassembled pages (limit %d)" % limit)
    for off in (60, 61, 500):
        j = stmt(ada, limit=10, offset=off)
        eq((j["entries"], j["has_more"]), ([], False), "offset %d past the end" % off)
        eq((j["opening_balance"], j["closing_balance"]), (want["opening"], want["closing"]), "balances even on an empty page")
    j = stmt(ada, limit=10, offset=55)
    eq((len(j["entries"]), j["has_more"]), (5, False), "final partial page")
    j = stmt(ada, limit=5, offset=55)
    eq((len(j["entries"]), j["has_more"]), (5, False), "exactly limit items remaining: has_more false")
    j = stmt(ada, limit=5, offset=54)
    eq((len(j["entries"]), j["has_more"]), (5, True), "one more beyond the page: has_more true")
    # balance_after of page 2 is a running sum from the full-window opening balance
    j = stmt(ada, limit=3, offset=10)
    compare_entries(j["entries"], want["entries"][10:13], "page at offset 10")
    # windows paginate too
    f, to = fmts(t + timedelta(minutes=10)), fmts(t + timedelta(minutes=40))
    mw = m.statement(uid("ada"), inst(f), inst(to))
    eq(len(mw["entries"]), 30, "model window")
    j = stmt(ada, **{"from": f, "to": to, "limit": 8, "offset": 8})
    compare_entries(j["entries"], mw["entries"][8:16], "windowed page")
    eq((j["opening_balance"], j["closing_balance"]), (mw["opening"], mw["closing"]), "windowed balances")
    # limit / offset validation exactly as GET /requests
    for bad in ("0", "201", "-1", "1e2", "+4", "4.0", "abc", "", "1.5", " 5"):
        expect(stmt_raw(ada, limit=bad), 422, "validation_failed", "limit=%r" % bad)
    for bad in ("-1", "1e1", "+4", "4.0", "abc", "", "0.0"):
        expect(stmt_raw(ada, offset=bad), 422, "validation_failed", "offset=%r" % bad)
    expect(stmt_raw(ada, limit="1", offset="0", unknown_param="x", other="y"), 200, msg="unknown query parameters are ignored")
    expect(stmt_raw(ada, limit="200", offset="0"), 200, msg="limit 200")


@test("S3-020", "S3-031", "S3-029", "S3-038")
def test_statement_of_an_account_with_no_payments_and_authentication():
    w = lib2.world2({"ada": 100, "bob": 40, "cy": 7})
    cy = w["cy"]
    j = stmt(cy)
    eq((j["opening_balance"], j["entries"], j["closing_balance"], j["has_more"]), (7, [], 7, False), "empty statement")
    expect(http("GET", "/statement"), 401, "unauthenticated")
    expect(http("GET", "/statement", token="nope"), 401, "unauthenticated")
    expect(http("GET", "/statement", headers={"Authorization": "Basic Zm9vOmJhcg=="}), 401, "unauthenticated")
    nu = new_user("fresh@example.com")
    j = stmt(nu)
    eq((j["opening_balance"], j["entries"], j["closing_balance"]), (0, [], 0), "a new account opens at zero")
    expect(w["ada"].pay(nu.handle, 3), 201)
    j = stmt(nu)
    eq((j["opening_balance"], j["closing_balance"], [e["delta"] for e in j["entries"]]), (0, 3, [3]), "after a payment")
