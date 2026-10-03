"""Stage 3: affordability of corrections — insufficient_funds (now) and historical_overdraft (at past boundaries)."""
from datetime import timedelta

from lib3 import *  # noqa: F401,F403


def rejected(users, owner, pid, rev, amount, eff, code, why, key=None, pids=()):
    """A correction that must fail with 409 `code` and leave balances, statements, feeds and histories alone."""
    before = observe_state(users, [(owner, pid)] + list(pids))
    k = key or fresh_key()
    r = correct(owner, pid, rev, amount, eff, "attempt", key=k)
    expect(r, 409, code, why)
    eq(observe_state(users, [(owner, pid)] + list(pids)), before, why + ": a rejected correction leaves no trace")
    return k


@test("S3-056", "S3-055", "S3-054", "S3-059", "S3-115", "S3-109")
def test_insufficient_funds_for_currently_unaffordable_debits():
    # increase: the sender's balance must cover the difference (boundary: exactly the available amount)
    w = lib2.world2({"ada": 600, "bob": 0, "cy": 0, "dan": 100})
    ada, bob, cy, dan = w["ada"], w["bob"], w["cy"], w["dan"]
    p = new_payment(ada, "bob", 500)
    pid, eff = p["payment_id"], p["created_at"]
    rejected(w, ada, pid, 1, 700, eff, "insufficient_funds", "increase by 200 with 100 available")
    expect(correct(ada, pid, 1, 600, eff), 201, msg="increase by exactly the available amount")
    eq(lib2.me_ok(ada)["balance"], 0, "ada is drained to exactly zero")
    eq(lib2.me_ok(bob)["balance"], 600, "bob")
    rejected(w, ada, pid, 2, 601, eff, "insufficient_funds", "one minor unit more")
    # decrease: the receiver must be able to give the difference back
    w = lib2.world2({"ada": 600, "bob": 0, "cy": 0, "dan": 100})
    ada, bob, cy, dan = w["ada"], w["bob"], w["cy"], w["dan"]
    p = new_payment(ada, "bob", 500)
    pid, eff = p["payment_id"], p["created_at"]
    expect(bob.pay("cy", 450), 201)
    rejected(w, ada, pid, 1, 400, eff, "insufficient_funds", "decrease by 100, receiver has 50 (also a historical overdraft, but the current debit wins)")
    expect(correct(ada, pid, 1, 450, eff), 201, msg="decrease by exactly the receiver's balance")
    eq(lib2.me_ok(bob)["balance"], 0, "bob at zero")
    rejected(w, ada, pid, 2, 449, eff, "insufficient_funds", "one more unit")
    # held money cannot fund a debit: evaluated against available
    w = lib2.world2({"ada": 600, "bob": 0, "cy": 0})
    ada, bob, cy = w["ada"], w["bob"], w["cy"]
    p = new_payment(ada, "bob", 500)
    pid, eff = p["payment_id"], p["created_at"]
    expect(lib2.authorize(bob, "cy", 400), 201)
    m = lib2.me_ok(bob)
    eq((m["total"], m["held"], m["available"]), (500, 400, 100), "bob has 400 held")
    rejected(w, ada, pid, 1, 350, eff, "insufficient_funds", "decrease by 150 although total 500 would cover it: available is 100")
    expect(correct(ada, pid, 1, 400, eff), 201, msg="decrease by exactly bob's available amount")
    m = lib2.me_ok(bob)
    eq((m["total"], m["held"], m["available"]), (400, 400, 0), "bob after the correction")


@test("S3-115", "S3-056", "S3-059", "S3-057")
def test_key_of_an_insufficient_funds_rejection_is_reusable_once_funds_exist():
    w = lib2.world2({"ada": 600, "bob": 0, "dan": 400})
    ada, bob, dan = w["ada"], w["bob"], w["dan"]
    pa = new_payment(ada, "bob", 500)               # ada: 100 left
    pd = new_payment(dan, "ada", 50)                # ada: 150
    k = rejected(w, ada, pa["payment_id"], 1, 700, pa["created_at"], "insufficient_funds", "+200 with 150 available")
    # dan raises his payment retroactively to before ada's payment: ada's history now holds the money
    early = fmt(inst(pa["created_at"]) - timedelta(hours=1))
    expect(correct(dan, pd["payment_id"], 1, 250, early, "raised and moved earlier"), 201, msg="dan has the funds")
    eq(lib2.me_ok(ada)["balance"], 350, "ada 600 - 500 + 250")
    expect(correct(ada, pa["payment_id"], 1, 700, pa["created_at"], "attempt", key=k), 201,
           msg="the key that failed with insufficient_funds is a first use; the correction is now affordable and historically sound")
    eq(lib2.me_ok(ada)["balance"], 150, "ada after")
    eq(me_at(ada, as_of=pa["created_at"])["balance"], 150, "history: 850 - 700 at the payment")
    eq(sum(lib2.me_ok(u)["balance"] for u in w.values()), 1000, "conservation")


@test("S3-057", "S3-058", "S3-059", "S3-115", "S3-060", "S3-108")
def test_historical_overdraft_receiver_side_and_unlocking():
    base = whole(utcnow()) - timedelta(days=5)
    t1, t2, t3 = [base + timedelta(hours=h) for h in (1, 2, 3)]
    opening = {"ada": 100, "bob": 0, "cy": 0, "dan": 100}
    pays = [fx_pay("p_001", "ada", "bob", 100, created_at=t1), fx_pay("p_002", "bob", "cy", 100, created_at=t2),
            fx_pay("p_003", "dan", "bob", 100, created_at=t3)]
    w = hist_world(opening, pays)
    ada, bob, cy, dan = w["ada"], w["bob"], w["cy"], w["dan"]
    eq(lib2.me_ok(bob)["balance"], 100, "bob holds 100 now, so every attempt below is currently affordable")
    k = rejected(w, ada, "p_001", 1, 0, fmts(t1), "historical_overdraft", "reversing p_001 would leave bob at -100 between t2 and t3")
    rejected(w, ada, "p_001", 1, 50, fmts(t1), "historical_overdraft", "decreasing p_001 by 50 leaves bob at -50 after t2")
    rejected(w, ada, "p_001", 1, 100, fmts(t2 + timedelta(minutes=30)), "historical_overdraft",
             "moving p_001 after bob's spending at t2 leaves bob at -100 at t2")
    # a correction by dan moves his payment earlier (to t1): accepted, and it unlocks the first one
    expect(correct(dan, "p_003", 1, 100, fmts(t1), "paid earlier"), 201, msg="moving a payment earlier so that nobody goes negative")
    eq(me_at(w["bob"], as_of=fmts(t1))["balance"], 200, "bob as of t1 now includes both payments")
    expect(correct(ada, "p_001", 1, 0, fmts(t1), "attempt", key=k), 201, msg="under the latest known revisions the first correction is now fine (same key, first use)")
    eq(me_at(bob, as_of=fmts(t2))["balance"], 0, "bob is at exactly zero at t2: a boundary at zero is allowed")
    eq(lib2.me_ok(bob)["balance"], 0, "bob now")
    eq(sum(lib2.me_ok(u)["balance"] for u in w.values()), sum(opening.values()), "conservation")


@test("S3-057", "S3-056", "S3-109", "S3-059", "S3-060")
def test_historical_overdraft_sender_side_and_precedence_of_insufficient_funds():
    base = whole(utcnow()) - timedelta(days=5)
    t1, t2, t3 = [base + timedelta(hours=h) for h in (1, 2, 3)]
    opening = {"ada": 201, "bob": 0, "cy": 0}
    pays = [fx_pay("p_101", "ada", "cy", 50, created_at=t1), fx_pay("p_102", "ada", "bob", 150, created_at=t2),
            fx_pay("p_103", "bob", "ada", 120, created_at=t3)]
    w = hist_world(opening, pays)
    ada, bob, cy = w["ada"], w["bob"], w["cy"]
    eq(lib2.me_ok(ada)["balance"], 121, "ada ends at 121")
    rejected(w, ada, "p_101", 1, 52, fmts(t1), "historical_overdraft", "+2 leaves ada at -1 right after t2 although 121 is available now")
    expect(correct(ada, "p_101", 1, 51, fmts(t1), "to zero at t2"), 201, msg="+1 leaves ada at exactly 0 at t2: allowed")
    eq(me_at(ada, as_of=fmts(t2))["balance"], 0, "ada is at zero at t2")
    rejected(w, ada, "p_101", 2, 52, fmts(t1), "historical_overdraft", "one more unit")
    # precedence: bob has 30 now; decreasing p_102 by 31 is unaffordable now AND leaves him at -1 at t3
    rejected(w, ada, "p_102", 1, 119, fmts(t2), "insufficient_funds", "a current unaffordable debit wins over historical_overdraft")
    expect(correct(ada, "p_102", 1, 120, fmts(t2), "exactly what bob has"), 201, msg="decrease by 30: bob ends at 0 and is at 0 at t3")
    eq(me_at(bob, as_of=fmts(t3))["balance"], 0, "bob at t3")
    eq(lib2.me_ok(bob)["balance"], 0, "bob now")
    eq(sum(lib2.me_ok(u)["balance"] for u in w.values()), sum(opening.values()), "conservation")


@test("S3-058", "S3-057", "S3-023", "S3-074", "S3-060")
def test_balances_at_an_instant_include_the_combined_effect_of_all_movements():
    base = whole(utcnow()) - timedelta(days=5)
    t1, t2 = base + timedelta(hours=1), base + timedelta(hours=2)
    opening = {"ada": 0, "bob": 100, "cy": 0}
    # p_200 (cy -> ada) sorts BEFORE p_201 (bob -> cy): applied one by one in id order cy would dip to -100
    pays = [fx_pay("p_200", "cy", "ada", 100, created_at=t2), fx_pay("p_201", "bob", "cy", 100, created_at=t1)]
    w = hist_world(opening, pays)
    ada, bob, cy = w["ada"], w["bob"], w["cy"]
    rejected(w, cy, "p_200", 1, 100, fmts(t1 - timedelta(hours=1)), "historical_overdraft", "cy would send 100 before receiving it")
    expect(correct(cy, "p_200", 1, 100, fmts(t1), "same instant as the money arrives"), 201,
           msg="receiving and sending 100 at the same instant nets to zero: allowed")
    eq(me_at(cy, as_of=fmts(t1))["balance"], 0, "cy as of t1 includes the combined effect of both movements")
    eq(me_at(cy, as_of=fmts(t1 - timedelta(seconds=1)))["balance"], 0, "before t1")
    eq(me_at(ada, as_of=fmts(t1))["balance"], 100, "ada as of t1")
    st = full_stmt(cy)
    eq(ids_of_entries(st["entries"]), ["p_200", "p_201"], "entries at one effective instant are ordered by payment id")
    eq([e["delta"] for e in st["entries"]], [-100, 100], "deltas")
    eq(st["entries"][-1]["balance_after"], 0, "the last entry of the tied group equals the balance at the instant")
    eq((st["opening"], st["closing"]), (0, 0), "opening and closing")
    j = full_stmt(cy, **{"from": fmts(t1), "to": fmts(t1 + timedelta(seconds=1))})
    eq((j["opening"], j["closing"]), (0, 0), "window [t1, t1+1s)")
    eq(sum(lib2.me_ok(u)["balance"] for u in w.values()), 100, "conservation")


@test("S3-108", "S3-109", "S3-057", "S3-097", "S3-098", "S3-100", "S3-059")
def test_historical_overdraft_considers_available_while_a_hold_was_open():
    t = whole(utcnow()) - timedelta(hours=1)
    opening = {"ada": 1100, "bob": 0, "cy": 0}
    pays = [fx_pay("p_301", "ada", "cy", 100, created_at=t)]
    w = hist_world(opening, pays)
    ada, bob, cy = w["ada"], w["bob"], w["cy"]
    a = expect(lib2.authorize(ada, "bob", 800), 201).json
    sleep_gap(1.2)
    v = expect(lib2.void(ada, a["authorization_id"]), 200).json
    m = lib2.me_ok(ada)
    eq((m["total"], m["held"], m["available"]), (1000, 0, 1000), "the hold is released again")
    # +201: total 799 while 800 was held -> available -1 at the hold's creation boundary
    rejected(w, ada, "p_301", 1, 301, fmts(t), "historical_overdraft", "total stays >= 0 but available would be -1 while the hold was open")
    expect(correct(ada, "p_301", 1, 300, fmts(t), "available exactly 0 during the hold"), 201, msg="+200 leaves available at exactly 0")
    rejected(w, ada, "p_301", 2, 301, fmts(t), "historical_overdraft", "one more unit under the latest known revisions")
    rejected(w, ada, "p_301", 2, 1301, fmts(t), "insufficient_funds", "current unaffordable debit takes precedence")
    # and the views: during the hold the available amount is exactly zero
    during = fmt(inst(a["created_at"]) + (inst(v["closed_at"]) - inst(a["created_at"])) / 2)
    j = me_at(ada, as_of=during)
    eq((j["total"], j["held"], j["available"]), (800, 800, 0), "as_of during the hold")
    j = me_at(ada, as_of=v["closed_at"])
    eq((j["total"], j["held"], j["available"]), (800, 0, 800), "as_of at exactly the void instant: released")
