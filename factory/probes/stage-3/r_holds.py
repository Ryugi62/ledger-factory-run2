"""Stage 3: historical holds — GET /me?as_of=&known_at= over authorization lifecycles, closed_at, statements vs holds."""
from datetime import timedelta

from lib3 import *  # noqa: F401,F403

FAR = "2999-01-01T00:00:00+00:00"


def money(u, as_of=None, known_at=None):
    j = me_at(u, as_of=as_of, known_at=known_at)
    return (j["total"], j["held"], j["available"])


def auth_row(u, aid):
    return lib2.auth_by_id(u, aid)


@test("S3-097", "S3-098", "S3-099", "S3-100", "S3-102", "S3-103", "S3-105", "S3-106", "S3-107", "S3-060")
def test_hold_views_across_authorize_capture_void():
    w = lib2.world2({"ada": 5000, "bob": 0, "cy": 0})
    ada, bob, cy = w["ada"], w["bob"], w["cy"]
    a = expect(lib2.authorize(ada, "bob", 2000), 201).json
    aid = a["authorization_id"]
    ta, exp = inst(a["created_at"]), inst(a["expires_at"])
    eq(a.get("closed_at", "MISSING"), None, "a new authorization has closed_at: null")
    sleep_gap(1.2)
    c1 = expect(lib2.capture(bob, aid, {"amount": 700, "final": False}), 201).json
    tc = inst(c1["created_at"])
    eq(auth_row(ada, aid).get("closed_at", "MISSING"), None, "a partially captured (open) authorization has closed_at: null")
    sleep_gap(1.2)
    v = expect(lib2.void(ada, aid), 200).json
    tv = inst(v["closed_at"])
    ok(ta < tc < tv, "the three event instants are ordered: %s %s %s" % (ta, tc, tv))
    sec = timedelta(seconds=1)
    now = utcnow()

    def tot(as_of=None, known_at=None):
        out = {h: money(u, as_of, known_at) for h, u in w.items()}
        eq(sum(x[0] for x in out.values()), 5000, "sum of totals in view as_of=%s known_at=%s" % (as_of, known_at))
        return out

    # effective-time axis, everything known
    cases = [(ta - sec, (5000, 0, 5000)), (ta, (5000, 2000, 3000)), (ta + (tc - ta) / 2, (5000, 2000, 3000)),
             (tc - sec / 10, (5000, 2000, 3000)), (tc, (4300, 1300, 3000)), (tc + (tv - tc) / 2, (4300, 1300, 3000)),
             (tv - sec / 10, (4300, 1300, 3000)), (tv, (4300, 0, 4300)), (tv + sec, (4300, 0, 4300)), (now + timedelta(days=1), (4300, 0, 4300))]
    for as_of, want in cases:
        got = tot(fmt(as_of))["ada"]
        eq(got, want, "ada (total, held, available) as of %s (ta=%s tc=%s tv=%s)" % (as_of, ta, tc, tv))
    eq(tot(fmt(tc))["bob"], (700, 0, 700), "bob at the capture instant")
    eq(tot(fmt(tc - sec / 10))["bob"], (0, 0, 0), "bob just before")
    eq(tot()["ada"], (4300, 0, 4300), "no as_of: the instant the request began")
    # recorded-time axis: events are known at their server-assigned time
    k = lambda x: fmt(x)
    eq(tot(None, k(ta - sec))["ada"], (5000, 0, 5000), "known_at before the authorization existed (as_of omitted)")
    eq(tot(None, k(ta))["ada"], (5000, 2000, 3000), "known_at at creation: the hold is known, the capture and the void are not (as_of now)")
    eq(tot(None, k(ta + (tc - ta) / 2))["ada"], (5000, 2000, 3000), "known_at between creation and capture")
    eq(tot(None, k(tc))["ada"], (4300, 1300, 3000), "known_at at the capture: payment and reduced hold known, void unknown")
    eq(tot(None, k(tc + (tv - tc) / 2))["ada"], (4300, 1300, 3000), "known_at between capture and void")
    eq(tot(None, k(tv))["ada"], (4300, 0, 4300), "known_at at the void")
    eq(tot(k(tv), k(tc))["ada"], (4300, 1300, 3000), "as_of after the void, but the void is not yet known")
    eq(tot(k(tc), k(ta))["ada"], (5000, 2000, 3000), "as_of at the capture, but the capture is not yet known")
    eq(tot(None, k(ta - sec))["bob"], (0, 0, 0), "bob: nothing known")
    eq(tot(None, k(ta))["bob"], (0, 0, 0), "bob: capture unknown")
    eq(tot(None, k(tc))["bob"], (700, 0, 700), "bob: capture known")
    # expiry deadline: known as soon as creation is known
    ex_after = exp + timedelta(hours=1)
    eq(tot(k(ex_after), k(ta))["ada"], (5000, 0, 5000), "creation known, as_of after the deadline: expired (the deadline is known with the creation)")
    eq(tot(k(exp - sec), k(ta))["ada"], (5000, 2000, 3000), "creation known, as_of 1 s before the deadline: still held (queries beyond now keep an open hold until its deadline)")
    eq(tot(k(exp), k(ta))["ada"], (5000, 0, 5000), "as_of exactly at expires_at: released")
    eq(tot(k(exp), k(tc))["ada"], (4300, 0, 4300), "capture known, void not: at the deadline the remainder expires")
    eq(tot(k(exp - sec), k(tc))["ada"], (4300, 1300, 3000), "...and one second before the deadline it is still held")
    # known_at alone and the plain read
    eq(money(ada, None, k(ta)), (5000, 2000, 3000), "GET /me?known_at=K: as of now under knowledge K")
    eq(money(ada), (4300, 0, 4300), "plain GET /me")
    # closed_at
    row = auth_row(ada, aid)
    eq(row["status"], "voided", "status")
    same_instant(row["closed_at"], v["closed_at"], "listed closed_at is the void instant")
    ok(ta <= tv <= utcnow(), "closed_at between creation and now")
    v2 = expect(lib2.void(ada, aid), 200).json
    same_instant(v2["closed_at"], v["closed_at"], "voiding again is 200 with the current state: closed_at is the first void's instant")
    eq(auth_row(bob, aid)["closed_at"], row["closed_at"], "the receiver sees the same closed_at")


@test("S3-104", "S3-101", "S3-103", "S3-098", "S3-102")
def test_open_hold_views_beyond_now():
    w = lib2.world2({"ada": 1000, "bob": 0})
    ada, bob = w["ada"], w["bob"]
    a = expect(lib2.authorize(ada, "bob", 400), 201).json
    exp, ta = inst(a["expires_at"]), inst(a["created_at"])
    eq((exp - ta).total_seconds(), 600, "default lifetime")
    sec = timedelta(seconds=1)
    for as_of, want in ((utcnow() + timedelta(seconds=60), (1000, 400, 600)), (exp - sec, (1000, 400, 600)), (exp, (1000, 0, 1000)),
                        (exp + sec, (1000, 0, 1000)), (exp + timedelta(days=1), (1000, 0, 1000)), (utcnow() + timedelta(days=3650), (1000, 0, 1000))):
        eq(money(ada, fmt(as_of)), want, "as of %s (expires_at %s)" % (as_of, exp))
    eq(money(ada, fmt(exp), fmt(ta - sec)), (1000, 0, 1000), "before creation was known: nothing")
    eq(money(ada, fmt(exp - sec), fmt(ta)), (1000, 400, 600), "known at creation, one second before the deadline")
    eq(money(ada, fmt(ta - sec)), (1000, 0, 1000), "before the hold started")
    eq(money(ada, fmt(ta)), (1000, 400, 600), "a hold starts at creation: as_of exactly at created_at counts")
    for h, u in w.items():
        eq(sum(me_at(u, as_of=fmt(exp - sec))["total"] for u in w.values()), 1000, "sum of totals")


@test("S3-101", "S3-099", "S3-100", "S3-106", "S3-103", "S3-102", "S3-097")
def test_expiry_and_final_capture_set_closed_at_and_release_in_history():
    w = lib2.world2({"ada": 5000, "bob": 0, "cy": 3000, "dan": 2000}, ttl=3)
    ada, bob, cy, dan = w["ada"], w["bob"], w["cy"], w["dan"]
    a1 = expect(lib2.authorize(ada, "bob", 1000), 201).json                      # will simply expire
    a2 = expect(lib2.authorize(cy, "bob", 500), 201).json                        # final capture, fast
    a3 = expect(lib2.authorize(dan, "bob", 300), 201).json                       # partial capture, then expiry
    sleep_gap(1.2)                                                               # keep the events more than a second apart
    cap2 = expect(lib2.capture(bob, a2["authorization_id"], {}), 201).json
    cap3 = expect(lib2.capture(bob, a3["authorization_id"], {"amount": 100, "final": False}), 201).json
    row2 = auth_row(cy, a2["authorization_id"])
    eq(row2["status"], "captured", "a full final capture closes the authorization")
    near(row2["closed_at"], cap2["created_at"], 1.0, "closed_at of a final capture is the capture instant")
    sec = timedelta(seconds=1)
    t2 = inst(cap2["created_at"])
    eq(money(cy, fmt(t2 - sec / 2)), (3000, 500, 2500), "cy shortly before the final capture")
    eq(money(cy, fmt(t2)), (2500, 0, 2500), "cy at the final capture: payment applied, the remainder released at that event's time")
    t3 = inst(cap3["created_at"])
    eq(money(dan, fmt(t3 - sec / 2)), (2000, 300, 1700), "dan before the partial capture")
    eq(money(dan, fmt(t3)), (1900, 200, 1700), "dan at the nonfinal capture: total -100, hold reduced by 100")
    e1, e3 = inst(a1["expires_at"]), inst(a3["expires_at"])
    sleep_until(a1["expires_at"], 1.3)
    sleep_until(a3["expires_at"], 1.3)
    r1, r3 = auth_row(ada, a1["authorization_id"]), auth_row(dan, a3["authorization_id"])
    eq(r1["status"], "expired", "a1 expired on the clock")
    same_instant(r1["closed_at"], a1["expires_at"], "closed_at of an expired authorization is its expires_at")
    eq((r3["status"], r3["captured_amount"]), ("expired", 100), "a3 expired after a partial capture")
    same_instant(r3["closed_at"], a3["expires_at"], "closed_at of a partially captured, then expired authorization")
    ta1 = inst(a1["created_at"])
    eq(money(ada, fmt(e1 - sec / 10)), (5000, 1000, 4000), "ada just before the deadline")
    eq(money(ada, fmt(e1)), (5000, 0, 5000), "expiry takes effect at expires_at")
    eq(money(ada, fmt(e1), fmt(ta1)), (5000, 0, 5000), "knowing only the creation is enough to know the deadline")
    eq(money(ada, fmt(e1 + sec), fmt(ta1 - sec)), (5000, 0, 5000), "creation not yet known: nothing")
    eq(money(dan, fmt(e3)), (1900, 0, 1900), "dan at the deadline: only the remainder (200) is released; the capture stays")
    eq(money(dan, fmt(e3 - sec / 10)), (1900, 200, 1700), "dan just before the deadline")
    eq(money(dan, fmt(e3), fmt(t3 - sec)), (2000, 0, 2000), "capture not yet known, creation known: expired hold, no payment")
    eq(money(ada), (5000, 0, 5000), "now")
    eq(sum(me_at(u, as_of=fmt(t3))["total"] for u in w.values()), 10000, "sum of totals")


@test("S3-110", "S3-102", "S3-106", "S3-097")
def test_seeded_open_holds_are_created_at_reset_unless_created_at_is_supplied():
    t0 = utcnow()
    a_reset = lib2.fx_auth("a_s1", "ada", "bob", 400, "open", lib2.in_(hours=3))
    a_past = lib2.fx_auth("a_s2", "ada", "bob", 300, "open", lib2.in_(hours=3), created_at=fmts(t0 - timedelta(minutes=30)))
    w = lib2.world2({"ada": 1000, "bob": 0}, auths=[a_reset, a_past])
    ada = w["ada"]
    eq(money(ada), (1000, 700, 300), "both holds are open now")
    eq(money(ada, fmt(t0 - timedelta(hours=1))), (1000, 0, 1000), "before either hold started")
    eq(money(ada, fmt(t0 - timedelta(minutes=10))), (1000, 300, 700), "the hold with a supplied created_at is held since then; the other did not exist yet")
    eq(money(ada, fmt(t0 - timedelta(seconds=5))), (1000, 300, 700), "just before reset: still only the supplied one")
    eq(money(ada, fmt(utcnow() + timedelta(seconds=1))), (1000, 700, 300), "after reset both")
    eq(money(ada, None, fmt(t0 - timedelta(hours=1))), (1000, 0, 1000), "known_at before the supplied created_at: unknown")
    eq(money(ada, None, fmt(t0 - timedelta(minutes=10))), (1000, 300, 700), "known_at between: only the supplied one is known")
    for row in lib2.all_auths(ada):
        ok("closed_at" in row and row["closed_at"] is None, "open seeded authorizations expose closed_at: null: %r" % (row,))
    eq(me_at(ada, as_of=fmt(t0 - timedelta(minutes=10)))["balance"], 1000, "balance = total")


@test("S3-112", "S3-114", "S3-113", "S3-081", "S3-082")
def test_statements_hold_money_movements_only_and_old_snapshots_survive_lifecycle():
    w = lib2.world2({"ada": 5000, "bob": 0}, ttl=3)
    ada, bob = w["ada"], w["bob"]
    s0a, s0b = stmt(ada, limit=5), stmt(bob, limit=5)
    eq((s0a["entries"], s0b["entries"]), ([], []), "nothing yet")
    a = expect(lib2.authorize(ada, "bob", 1000), 201).json
    eq(full_stmt(ada)["entries"], [], "authorizing is not a movement of money")
    c = expect(lib2.capture(bob, a["authorization_id"], {"amount": 400, "final": False}), 201).json
    st = full_stmt(ada)
    eq(ids_of_entries(st["entries"]), [c["payment_id"]], "the capture appears exactly once")
    eq(st["entries"][0]["payment"]["authorization_id"], a["authorization_id"], "with its link")
    s1 = stmt(ada, limit=5)
    s1b = stmt(bob, limit=5)
    sleep_gap(1.1)
    expect(lib2.void(ada, a["authorization_id"]), 200)
    eq(ids_of_entries(full_stmt(ada)["entries"]), [c["payment_id"]], "releasing the remainder is not a payment")
    b = expect(lib2.authorize(ada, "bob", 700), 201).json
    sleep_until(b["expires_at"], 1.3)
    eq(lib2.auth_by_id(ada, b["authorization_id"])["status"], "expired", "expired")
    eq(ids_of_entries(full_stmt(bob)["entries"]), [c["payment_id"]], "expiry is not a payment")
    d = expect(lib2.authorize(ada, "bob", 200), 201).json
    c2 = expect(lib2.capture(bob, d["authorization_id"], {}), 201).json
    expect(ada.pay("bob", 3), 201)
    # old snapshots are untouched by every lifecycle action
    for u, s_old, want in ((ada, s0a, []), (bob, s0b, [])):
        j = stmt(u, snapshot=s_old["snapshot"], limit=50)
        eq((j["entries"], j["opening_balance"], j["closing_balance"], j["has_more"]), ([], s_old["opening_balance"], s_old["closing_balance"], False),
           "the snapshot taken before any lifecycle action is unchanged for %s" % u.handle)
    for u, s_old in ((ada, s1), (bob, s1b)):
        j = stmt(u, snapshot=s_old["snapshot"], limit=50)
        eq([e["payment"]["payment_id"] for e in j["entries"]], [c["payment_id"]], "the snapshot after the first capture is unchanged for %s" % u.handle)
        eq((j["opening_balance"], j["closing_balance"]), (s_old["opening_balance"], s_old["closing_balance"]), "its balances")
        eq(j["entries"], s_old["entries"], "its entries")
    eq(len(full_stmt(ada)["entries"]), 3, "(control) a fresh statement shows the later movements: two captures and a payment")
