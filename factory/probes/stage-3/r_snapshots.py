"""Stage 3: statement snapshots — stable pagination."""
from datetime import timedelta

from lib3 import *  # noqa: F401,F403


def history_world(n=25, extra_users=None):
    t = whole(utcnow()) - timedelta(days=6)
    opening = {"ada": 50000, "bob": 50000}
    if extra_users:
        opening.update(extra_users)
    pays = [fx_pay("p_%03d" % i, "ada" if i % 2 else "bob", "bob" if i % 2 else "ada", 10 + i, created_at=t + timedelta(hours=i + 1))
            for i in range(n)]
    return t, opening, pays, hist_world(opening, pays)


def pages_of(u, token, limit):
    out, off, first = [], 0, None
    while True:
        j = stmt(u, snapshot=token, limit=limit, offset=off)
        first = first or j
        eq((j["opening_balance"], j["closing_balance"]), (first["opening_balance"], first["closing_balance"]), "balances on every snapshot page")
        out += j["entries"]
        if not j["has_more"]:
            return out, first
        off += limit


@test("S3-080", "S3-082", "S3-083", "S3-086", "S3-087", "S3-085", "S3-021", "S3-028")
def test_snapshot_paging_and_parameter_rules():
    t, opening, pays, w = history_world(25)
    m = model_for(opening, pays)
    ada, bob = w["ada"], w["bob"]
    want = m.statement(uid("ada"))
    first = stmt(ada, limit=10)
    tok = first["snapshot"]
    compare_entries(first["entries"], want["entries"][:10], "first page")
    ok(first["has_more"], "has_more on the first page")
    for limit in (1, 4, 10, 25, 200):
        got, f = pages_of(ada, tok, limit)
        compare_entries(got, want["entries"], "snapshot pages of %d" % limit)
        eq((f["opening_balance"], f["closing_balance"]), (want["opening"], want["closing"]), "snapshot balances")
    # arbitrary page positions, in any order, repeatedly
    for off, lim in ((20, 10), (0, 3), (24, 5), (25, 5), (100, 5), (5, 5), (0, 3)):
        j = stmt(ada, snapshot=tok, limit=lim, offset=off)
        compare_entries(j["entries"], want["entries"][off:off + lim], "snapshot limit=%d offset=%d" % (lim, off))
        eq(j["has_more"], off + lim < 25, "has_more at offset %d limit %d" % (off, lim))
        eq((j["opening_balance"], j["closing_balance"]), (want["opening"], want["closing"]), "balances at offset %d" % off)
    j = stmt(ada, snapshot=tok, limit=5, offset=20)
    eq(j["has_more"], False, "exactly limit items remaining: has_more false")
    j = stmt(ada, snapshot=tok, limit=5, offset=19)
    eq(j["has_more"], True, "one more beyond the page")
    j = stmt(ada, snapshot=tok, limit=5, offset=30)
    eq((j["entries"], j["has_more"]), ([], False), "offset beyond the end")
    j = stmt(ada, snapshot=tok)
    eq(len(j["entries"]), 25, "default limit 50 on a snapshot")
    # parameters: only limit and offset may accompany a snapshot
    good = fmts(t)
    for extra in ({"from": good}, {"to": good}, {"known_at": good}, {"from": good, "to": good, "known_at": good}):
        expect(stmt_raw(ada, snapshot=tok, **extra), 422, "validation_failed", "snapshot with %s" % sorted(extra))
        expect(stmt_raw(ada, snapshot=tok, limit="5", **extra), 422, "validation_failed", "snapshot + limit + %s" % sorted(extra))
    expect(stmt_raw(ada, snapshot=tok, limit="5", offset="1", whatever="x"), 200, msg="unrecognised parameters are ignored")
    for bad in ("0", "201", "-1", "1e1", "+4", "abc"):
        expect(stmt_raw(ada, snapshot=tok, limit=bad), 422, "validation_failed", "snapshot limit=%r" % bad)
    for bad in ("-1", "1e1", "abc", "4.0"):
        expect(stmt_raw(ada, snapshot=tok, offset=bad), 422, "validation_failed", "snapshot offset=%r" % bad)
    # a token on every first response, including empty and filtered windows, and each token is independent
    e = stmt(ada, **{"to": fmts(t - timedelta(days=30))})
    eq(e["entries"], [], "empty window")
    ok(isinstance(e["snapshot"], str) and e["snapshot"], "an empty window still returns a snapshot token")
    j = stmt(ada, snapshot=e["snapshot"], limit=5)
    eq((j["entries"], j["has_more"]), ([], False), "paging an empty snapshot")
    f = stmt(ada, **{"from": fmts(t + timedelta(hours=5)), "to": fmts(t + timedelta(hours=9)), "limit": 2})
    mw = m.statement(uid("ada"), t + timedelta(hours=5), t + timedelta(hours=9))
    got, ff = pages_of(ada, f["snapshot"], 3)
    compare_entries(got, mw["entries"], "snapshot of a filtered window")
    eq((ff["opening_balance"], ff["closing_balance"]), (mw["opening"], mw["closing"]), "balances of the filtered snapshot")
    # tokens are per user and unauthenticated access is refused
    expect(stmt_raw(bob, snapshot=tok), 404, "not_found", "another user's token")
    expect(stmt_raw(ada, snapshot="nope"), 404, "not_found", "unknown token")
    expect(stmt_raw(ada, snapshot="x" * 200), 404, "not_found", "unknown long token")
    expect(http("GET", "/statement?" + qs(snapshot=tok)), 401, "unauthenticated", "no bearer token")


@test("S3-081", "S3-082", "S3-088", "S3-089", "S3-084", "S3-085", "S3-116")
def test_snapshot_is_frozen_across_payments_and_corrections_and_reset():
    t, opening, pays, w = history_world(12, {"cy": 100})
    ada, bob, cy = w["ada"], w["bob"], w["cy"]
    frm, to = fmts(t + timedelta(hours=3)), fmts(t + timedelta(hours=9))
    s_win = stmt(ada, **{"from": frm, "to": to, "limit": 4})
    s_all = stmt(ada, limit=5)
    orig_win, _ = pages_of(ada, s_win["snapshot"], 4)
    orig_all, f_all = pages_of(ada, s_all["snapshot"], 5)
    bal_win = (s_win["opening_balance"], s_win["closing_balance"])
    bal_all = (f_all["opening_balance"], f_all["closing_balance"])
    eq(len(orig_all), 12, "all twelve payments")
    # changes after the reads: new payments (later than the frozen default `to`), corrections moving entries out of / into the window
    expect(ada.pay("bob", 11), 201)
    expect(bob.pay("ada", 12), 201)
    expect(ada.pay("cy", 13), 201)
    # odd payments are ada -> bob, even ones bob -> ada; the window holds p_002 .. p_008
    expect(correct(ada, "p_003", 1, 99, fmts(t + timedelta(hours=12)), "inside the window -> moved out of it"), 201)
    expect(correct(ada, "p_001", 1, 5, fmts(t + timedelta(hours=5, minutes=30)), "outside the window -> moved into it"), 201)
    expect(correct(bob, "p_004", 1, 1, fmts(t + timedelta(hours=5)), "inside the window, smaller"), 201)
    fresh_win = full_stmt(ada, **{"from": frm, "to": to})
    fresh_all = full_stmt(ada)
    ok(ids_of_entries(fresh_all["entries"]) != ids_of_entries(orig_all) or [e["delta"] for e in fresh_all["entries"]] != [e["delta"] for e in orig_all],
       "(control) a fresh statement reflects the changes")
    ok(len(fresh_all["entries"]) == 15, "(control) the three new payments are in a fresh statement: %d" % len(fresh_all["entries"]))
    # the frozen results
    got, f = pages_of(ada, s_win["snapshot"], 3)
    eq(got, orig_win, "the windowed snapshot is unchanged after payments and corrections")
    eq((f["opening_balance"], f["closing_balance"]), bal_win, "its balances are unchanged")
    got, f = pages_of(ada, s_all["snapshot"], 7)
    eq(got, orig_all, "the default-window snapshot is unchanged: later payments (after the frozen `to`) do not appear")
    eq((f["opening_balance"], f["closing_balance"]), bal_all, "its balances are unchanged")
    # a snapshot taken now is frozen from now on, independent of the older ones
    s_new = stmt(ada, limit=100)
    new_entries = s_new["entries"]
    expect(ada.pay("bob", 14), 201)
    got, _ = pages_of(ada, s_new["snapshot"], 6)
    eq(got, new_entries, "each snapshot freezes its own read")
    # tokens last until reset; a token from before a reset is unknown afterwards, even for the same user id
    tok = s_all["snapshot"]
    reset(fixture([fx_user(h, 50000) for h in ("ada", "bob", "cy")], payments=[]))
    ada2 = login("ada@example.com", handle="ada")
    eq(ada2.id, ada.id, "same user id after the reset")
    expect(stmt_raw(ada2, snapshot=tok), 404, "not_found", "a token from before the reset")
    expect(stmt_raw(ada, snapshot=tok), 401, "unauthenticated", "(control) bearer tokens do not survive a reset either")
