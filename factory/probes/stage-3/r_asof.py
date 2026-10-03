"""Stage 3: payment timestamps, seeding with created_at, opening balances, GET /me?as_of=."""
from datetime import timedelta, timezone

from lib3 import *  # noqa: F401,F403

BAD_INSTANTS = ["", "2026-09-24", "2026-09-24T13:20:00", "yesterday", "1700000000", "2026-13-01T00:00:00+00:00",
                "2026-09-24T25:00:00+00:00", "null", "now", " "]
STRICT_BAD_INSTANTS = ["2026-09-24T13:20+00:00", "2026-09-24T13:20:00+0000", "2026-02-30T00:00:00+00:00",
                       "2026-09-24T13:20:00+25:00", "2026-09-24T13:20:00+00", "2026-09-24 13:20:00"]


def feed_keys_ok(p):
    missing = PAY_KEYS - set(p)
    ok(not missing, "payment missing %s: %r" % (sorted(missing), p))
    inst(p["created_at"])


@test("S3-006", "S3-008", "S3-009", "S3-011", "S3-007")
def test_seeded_created_at_order_and_defaults():
    t = whole(utcnow()) - timedelta(days=10)
    plus9 = timezone(timedelta(hours=9))
    pays = [fx_pay("p_003", "cy", "ada", 20, created_at=(t + timedelta(days=2)).astimezone(plus9).isoformat(timespec="seconds")),
            fx_pay("p_004", "ada", "cy", 10),                                 # omitted: reset time
            fx_pay("p_002", "bob", "cy", 50, created_at=t + timedelta(days=1)),
            fx_pay("p_001", "ada", "bob", 100, created_at=t)]
    # opening ada 1000 / bob 500 / cy 300  ->  ending 910 / 550 / 340
    w = lib2.world2({"ada": 910, "bob": 550, "cy": 340}, payments=pays)
    ada, bob, cy = w["ada"], w["bob"], w["cy"]
    for u, want in ((ada, 910), (bob, 550), (cy, 340)):
        eq(lib2.me_ok(u)["balance"], want, "fixture balance is the balance after all seeded payments (%s)" % u.handle)
    feed = ada.all_payments()
    eq([p["payment_id"] for p in feed], ["p_004", "p_003", "p_002", "p_001"],
       "GET /activity is newest first by created_at (seeded instants decide, not fixture order)")
    for p in feed:
        feed_keys_ok(p)
    by = {p["payment_id"]: p for p in feed}
    same_instant(by["p_001"]["created_at"], t, "p_001 created_at is the supplied instant")
    same_instant(by["p_002"]["created_at"], t + timedelta(days=1), "p_002 created_at")
    same_instant(by["p_003"]["created_at"], t + timedelta(days=2), "p_003 created_at (supplied with a +09:00 offset)")
    lib.check_ts(by["p_004"]["created_at"])   # omitted: reset time, i.e. now
    ok(inst(by["p_004"]["created_at"]) > inst(by["p_003"]["created_at"]), "omitted created_at (reset time) is later than the seeded past instants")
    p5 = expect(ada.pay("cy", 5), 201).json
    ok(inst(p5["created_at"]) >= inst(by["p_004"]["created_at"]), "the reset-time payment comes before subsequently API-created payments")
    ids = [p["payment_id"] for p in ada.all_payments()]
    ok(p5["payment_id"] in ids[:2], "the new payment is among the newest (order inside one second is unspecified): %r" % ids)
    # statements agree with the fixture
    for u, want in ((bob, 550), (cy, 340 + 5)):
        eq(full_stmt(u)["closing"], want, "closing_balance equals the current balance (%s)" % u.handle)


@test("S3-007", "S3-006")
def test_every_payment_endpoint_returns_created_at():
    w = lib2.world2({"ada": 10000, "bob": 5000, "cy": 0, "op": 0}, operators=["op"])
    ada, bob, cy, op = w["ada"], w["bob"], w["cy"], w["op"]
    seen = []
    seen.append(expect(ada.pay("bob", 100), 201).json)
    rq = expect(bob.request("ada", 40), 201).json["request_id"]
    seen.append(expect(ada.pay_request(rq), 201).json)
    a = expect(lib2.authorize(ada, "cy", 500), 201).json["authorization_id"]
    seen.append(expect(lib2.capture(cy, a, {"amount": 200, "final": False}), 201).json)
    st = expect(op.settle([{"from_handle": "ada", "to_handle": "bob", "amount": 7},
                           {"from_handle": "bob", "to_handle": "cy", "amount": 3}]), 201).json
    seen += st["payments"]
    seen += ada.all_payments()
    seen += [e["payment"] for e in full_stmt(ada)["entries"]]
    ok(len(seen) >= 10, "collected payments from every endpoint")
    for p in seen:
        ok("created_at" in p, "every endpoint returning a payment includes created_at: %r" % p)
        lib.check_ts(p["created_at"])


@test("S3-010")
def test_future_seeded_created_at_is_a_reset_error():
    w = lib2.world2({"ada": 500, "bob": 100})
    ada = w["ada"]
    before = (lib2.me_ok(ada), ada.all_payments())
    now = utcnow()
    far = timezone(timedelta(hours=-5))
    users = [fx_user("ada", 500), fx_user("bob", 100)]
    past = fmts(now - timedelta(days=3))
    for label, when in (("+1 h", fmts(now + timedelta(hours=1))), ("+1 day", fmts(now + timedelta(days=1))),
                        ("year 2999 with -05:00", "2999-01-01T00:00:00-05:00"),
                        ("+2 h written with +14:00", (now + timedelta(hours=2)).astimezone(timezone(timedelta(hours=14))).isoformat(timespec="seconds")),
                        ("+1 day, offset -05:00", (now + timedelta(days=1)).astimezone(far).isoformat(timespec="seconds"))):
        pays = [fx_pay("p_001", "ada", "bob", 10, created_at=past), fx_pay("p_002", "bob", "ada", 10, created_at=when)]
        r = http("POST", "/_test/reset", body=fixture(users, payments=pays))
        expect(r, 422, "validation_failed", "future seeded created_at (%s)" % label)
        eq((lib2.me_ok(ada), ada.all_payments()), before, "a rejected reset changes nothing (%s)" % label)
        expect(http("GET", "/me", token=ada.token), 200, msg="old token still valid (%s)" % label)
    # a past instant is fine, whatever its offset
    pays = [fx_pay("p_001", "ada", "bob", 10, created_at="2001-01-01T00:00:00+00:00"),
            fx_pay("p_002", "bob", "ada", 10, created_at=fmts(now - timedelta(minutes=5)))]
    expect(http("POST", "/_test/reset", body=fixture(users, payments=pays)), 204, msg="past created_at is accepted")


@test("S3-015", "S3-016", "S3-017", "S3-018", "S3-019", "S3-014", "S3-036", "S3-060", "S3-003", "S3-038")
def test_as_of_balances_and_opening_balance():
    t = whole(utcnow()) - timedelta(days=10)
    opening = {"ada": 1000, "bob": 0, "cy": 200, "dan": 70}
    pays = [fx_pay("p_001", "ada", "bob", 100, created_at=t + timedelta(hours=1)),
            fx_pay("p_002", "bob", "ada", 30, created_at=t + timedelta(hours=2)),
            fx_pay("p_003", "ada", "cy", 50, created_at=t + timedelta(hours=3)),
            fx_pay("p_004", "cy", "bob", 20, created_at=t + timedelta(hours=3), visibility="private")]
    w = hist_world(opening, pays)
    m = model_for(opening, pays)
    users = w
    eq({h: lib2.me_ok(u)["balance"] for h, u in users.items()}, {"ada": 880, "bob": 90, "cy": 230, "dan": 70},
       "balances after the seeded payments equal the fixture balances (fixture balance = ending balance)")
    seed_total = sum(opening.values())
    times = [t + timedelta(hours=h) for h in (1, 2, 3)]
    for as_of in grid(times, 1) + [t - timedelta(days=400), t + timedelta(days=3650)]:
        s = fmts(as_of) if as_of.microsecond == 0 else fmt(as_of)
        tot = 0
        for h, u in users.items():
            got = me_at(u, as_of=s)
            eq(got["balance"], m.balance(uid(h), as_of), "balance of %s as of %s" % (h, s))
            eq((got["total"], got["available"], got["held"]), (got["balance"], got["balance"], 0), "money fields describe one view")
            tot += got["balance"]
        eq(tot, seed_total, "sum of balances in the view as of %s" % s)
    # opening balance (hand computed): before anything moved
    for h, want in opening.items():
        eq(me_at(users[h], as_of=fmts(t))["balance"], want, "as_of before the earliest payment = opening balance (%s)" % h)
        eq(me_at(users[h], as_of="1970-01-01T00:00:00+00:00")["balance"], want, "as_of in 1970 = opening balance (%s)" % h)
    # inclusive at exactly the payment instant; same instant written with other offsets / precisions
    eq(me_at(users["ada"], as_of=fmts(t + timedelta(hours=1)))["balance"], 900, "a payment made at exactly as_of counts")
    eq(me_at(users["ada"], as_of=fmts(t + timedelta(hours=1) - timedelta(seconds=1)))["balance"], 1000, "one second earlier it does not")
    plus2 = (t + timedelta(hours=1)).astimezone(timezone(timedelta(hours=2))).isoformat(timespec="seconds")
    eq(me_at(users["ada"], as_of=plus2)["balance"], 900, "instants are compared as instants, not as strings (+02:00)")
    eq(me_at(users["ada"], as_of=fmt(t + timedelta(hours=1)))["balance"], 900, "fractional-second form")
    # the combined effect of two payments at one instant (p_003 / p_004 at t+3h)
    eq(me_at(users["cy"], as_of=fmts(t + timedelta(hours=3)))["balance"], 230, "cy: +50 and -20 at the same instant")
    # no temporal parameters: unchanged shape and the current balance
    cur = lib2.me_ok(users["ada"])
    eq(cur["balance"], 880, "current balance")
    eq(me_at(users["ada"], as_of="2999-01-01T00:00:00+00:00")["balance"], 880, "as_of at or after the latest payment is the current balance")
    eq(me_at(users["ada"], as_of=fmts(t + timedelta(hours=3)))["balance"], 880, "as_of exactly at the latest payment")
    for k in ("user_id", "display_name", "handle", "currency", "minor_units"):
        eq(me_at(users["ada"], as_of=fmts(t))[k], cur[k], "/me?as_of keeps " + k)
    # new accounts open at zero; an API payment counts from its created_at on
    nu = new_user("newcomer@example.com")
    eq(me_at(nu, as_of="1970-01-01T00:00:00+00:00")["balance"], 0, "a new account opens at zero")
    p = expect(users["ada"].pay(nu.handle, 5), 201).json
    c = p["created_at"]
    eq(me_at(nu, as_of=c)["balance"], 5, "API payment counts at exactly as_of = the created_at the service returned")
    eq(me_at(nu, as_of=fmt(inst(c) - timedelta(seconds=1)))["balance"], 0, "and not one second before")
    eq(me_at(nu, as_of="1970-01-01T00:00:00+00:00")["balance"], 0, "opening of the new account is still zero")
    st = full_stmt(nu)
    eq((st["opening"], st["closing"], len(st["entries"])), (0, 5, 1), "statement of the new account")


@test("S3-013", "S3-072")
def test_invalid_instants_are_422_on_me_and_statement():
    w = lib2.world2({"ada": 100, "bob": 0})
    ada = w["ada"]
    for bad in BAD_INSTANTS:
        for param in ("as_of", "known_at"):
            expect(me_raw(ada, **{param: bad}), 422, "validation_failed", "GET /me?%s=%r" % (param, bad))
        for param in ("from", "to", "known_at"):
            expect(stmt_raw(ada, **{param: bad}), 422, "validation_failed", "GET /statement?%s=%r" % (param, bad))
    expect(me_raw(ada, as_of="2026-09-24T13:20:00+00:00", known_at=""), 422, "validation_failed", "valid as_of + empty known_at")
    eq(lib2.me_ok(ada)["balance"], 100, "nothing changed")


@test("S3-013", "S3-072")
def test_strict_rfc3339_grammar_is_enforced():
    w = lib2.world2({"ada": 100, "bob": 0})
    ada = w["ada"]
    for bad in STRICT_BAD_INSTANTS:
        for param in ("as_of", "known_at"):
            expect(me_raw(ada, **{param: bad}), 422, "validation_failed", "GET /me?%s=%r (not RFC 3339)" % (param, bad))
        for param in ("from", "to", "known_at"):
            expect(stmt_raw(ada, **{param: bad}), 422, "validation_failed", "GET /statement?%s=%r (not RFC 3339)" % (param, bad))


@test("S3-012", "S3-019", "S3-073")
def test_z_designator_and_offsets_are_accepted_and_echoed():
    w = lib2.world2({"ada": 100, "bob": 0})
    ada = w["ada"]
    for v in ("2026-09-24T13:20:00Z", "2026-09-24T13:20:00+00:00", "2026-09-24T13:20:00-07:00", "2026-09-24T13:20:00.123456+05:30",
              "2026-09-24T13:20:00.5+00:00"):
        j = me_at(ada, as_of=v, known_at=v)
        eq((j["as_of"], j["known_at"]), (v, v), "echo of %s" % v)
        j = stmt(ada, known_at=v)
        eq(j.get("known_at"), v, "statement echoes known_at %s" % v)
        expect(stmt_raw(ada, **{"from": v, "to": v}), 200, msg="from/to accept " + v)
