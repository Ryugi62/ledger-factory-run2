"""Idempotency (section 7) as a matrix over the five write paths."""
import json

from lib import *  # noqa: F401,F403


def messy_json(obj, depth=0):
    """Same JSON value, different key order and whitespace."""
    pad = "\n" + "  " * (depth + 1)
    end = "\n" + "  " * depth
    if isinstance(obj, dict):
        items = ["%s%s :  %s" % (pad, json.dumps(k), messy_json(v, depth + 1)) for k, v in reversed(list(obj.items()))]
        return "{ " + " ,".join(items) + end + "}" if items else "{ }"
    if isinstance(obj, list):
        items = [pad + messy_json(v, depth + 1) for v in obj]
        return "[ " + " ,".join(items) + end + "]" if items else "[ ]"
    return json.dumps(obj, ensure_ascii=False)


class Path:
    """One idempotent write path with two actors, three body variants and an effect counter."""
    name = "?"

    def fresh(self):
        raise NotImplementedError

    def body(self, variant, idx=0, actor=1):
        raise NotImplementedError

    def url(self, idx=0, actor=1):
        raise NotImplementedError

    def actor(self, n):
        return self.users[n]

    def call(self, actor, key, variant, idx=0, raw=None):
        u = self.actor(actor)
        path = self.url(idx, actor)
        if raw is not None:
            return http("POST", path, raw=raw, token=u.token, key=key)
        return http("POST", path, body=self.body(variant, idx, actor), token=u.token, key=key)

    def effect(self):
        raise NotImplementedError

    def mutate(self):
        raise NotImplementedError


class Payments(Path):
    name = "POST /payments"

    def fresh(self):
        self.w = world({"ada": 100_000, "bob": 0, "cy": 100_000})
        self.users = {1: self.w["ada"], 2: self.w["cy"]}
        self.seed = 200_000
        return self

    def body(self, variant, idx=0, actor=1):
        return {"A": {"to_handle": "bob", "amount": 10, "note": "a"},
                "B": {"to_handle": "bob", "amount": 11, "note": "a"},
                "bad": {"to_handle": "bob", "amount": 0},
                "nf": {"to_handle": "nobody", "amount": 10}}[variant]

    def url(self, idx=0, actor=1):
        return "/payments"

    def effect(self):
        return len(self.w["bob"].all_payments())

    def mutate(self):
        ada, cy = self.w["ada"], self.w["cy"]
        expect(ada.pay("cy", ada.balance), 201, msg="drain the sender")
        eq(ada.balance, 0, "drained")


class Requests(Path):
    name = "POST /requests"

    def fresh(self):
        self.w = world({"ada": 1000, "bob": 0, "cy": 0})
        self.users = {1: self.w["bob"], 2: self.w["cy"]}
        self.seed = 1000
        return self

    def body(self, variant, idx=0, actor=1):
        return {"A": {"payer_handle": "ada", "amount": 10, "note": "a"},
                "B": {"payer_handle": "ada", "amount": 11, "note": "a"},
                "bad": {"payer_handle": "ada", "amount": 0},
                "nf": {"payer_handle": "nobody", "amount": 10}}[variant]

    def url(self, idx=0, actor=1):
        return "/requests"

    def effect(self):
        return len(self.w["ada"].all_requests())

    def mutate(self):
        bob = self.w["bob"]
        for q in bob.all_requests():
            expect(bob.post("/requests/%s/cancel" % q["request_id"]), 200, msg="cancel the created request")


class PayRequest(Path):
    name = "POST /requests/{id}/pay"

    def fresh(self):
        self.w = world({"ada": 100_000, "bob": 0, "cy": 100_000})
        ada, bob, cy = self.w["ada"], self.w["bob"], self.w["cy"]
        self.users = {1: ada, 2: cy}
        self.rids = {1: [expect(bob.request("ada", 10), 201).json["request_id"] for _ in range(4)],
                     2: [expect(bob.request("cy", 10), 201).json["request_id"] for _ in range(4)]}
        self.seed = 200_000
        return self

    def body(self, variant, idx=0, actor=1):
        return {"A": {}, "B": {"visibility": "private"}, "bad": {"visibility": "nope"}, "nf": {"visibility": "nope"}}[variant]

    def url(self, idx=0, actor=1):
        return "/requests/%s/pay" % self.rids[actor][idx]

    def effect(self):
        return len(self.w["bob"].all_payments())

    def mutate(self):
        ada = self.w["ada"]
        expect(ada.pay("cy", ada.balance), 201, msg="drain the payer")


class Splits(Path):
    name = "POST /splits"

    def fresh(self):
        self.w = world({"ada": 1000, "bob": 1000, "cy": 0})
        self.users = {1: self.w["ada"], 2: self.w["bob"]}
        self.seed = 2000
        return self

    def body(self, variant, idx=0, actor=1):
        return {"A": {"amount": 30, "participant_handles": ["bob", "cy"], "note": "a"},
                "B": {"amount": 31, "participant_handles": ["bob", "cy"], "note": "a"},
                "bad": {"amount": 0, "participant_handles": ["bob", "cy"]},
                "nf": {"amount": 30, "participant_handles": ["bob", "nobody"]}}[variant]

    def url(self, idx=0, actor=1):
        return "/splits"

    def effect(self):
        return len(self.w["cy"].all_requests())

    def mutate(self):
        cy = self.w["cy"]
        for q in cy.all_requests():
            expect(cy.post("/requests/%s/decline" % q["request_id"]), 200, msg="decline the split's requests")


class Settlements(Path):
    name = "POST /settlements"

    def fresh(self):
        self.w = world({"ada": 100, "bob": 0, "cy": 100_000}, operators=["ada", "bob"])
        self.users = {1: self.w["ada"], 2: self.w["bob"]}
        self.seed = 100_100
        return self

    def body(self, variant, idx=0, actor=1):
        return {"A": {"transfers": [{"from_handle": "cy", "to_handle": "bob", "amount": 10, "note": "s"}]},
                "B": {"transfers": [{"from_handle": "cy", "to_handle": "bob", "amount": 11, "note": "s"}]},
                "bad": {"transfers": []},
                "nf": {"transfers": [{"from_handle": "cy", "to_handle": "nobody", "amount": 10}]}}[variant]

    def url(self, idx=0, actor=1):
        return "/settlements"

    def effect(self):
        return len(self.w["cy"].all_payments())

    def mutate(self):
        cy = self.w["cy"]
        expect(cy.pay("ada", cy.balance), 201, msg="drain the debtor")


PATHS = [Payments, Requests, PayRequest, Splits, Settlements]


def each_path(fn):
    def run():
        for cls in PATHS:
            p = cls().fresh()
            try:
                fn(p)
                eq(total(list(p.w.values())), p.seed, "balances must still sum to the seeded total")
            except AssertionError as e:
                raise AssertionError("[%s] %s" % (p.name, e))
    run.__name__ = fn.__name__
    run.__doc__ = fn.__doc__
    return run


def same_json(a, b):
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


@test("S1-096", "S1-099", "S1-069", "S1-203")
@each_path
def test_missing_or_empty_key(p):
    zero = p.effect()
    for hdr in (None, ""):
        for variant in ("A", "B"):
            r = p.call(1, hdr, variant)
            expect(r, 400, "missing_idempotency_key", "key=%r" % (hdr,))
    eq(p.effect(), zero, "a request without a key must have no effect")
    # the very same requests with a key work
    expect(p.call(1, fresh_key(), "A"), 201)


@test("S1-096", "S1-100", "S1-101", "S1-104", "S1-203")
@each_path
def test_first_use_then_replay(p):
    zero = p.effect()
    k = fresh_key()
    r1 = expect(p.call(1, k, "A"), 201, msg="first use")
    one = p.effect()
    ok(one != zero, "the first request must take effect")
    r2 = expect(p.call(1, k, "A"), 200, msg="replay")
    ok(same_json(r1.json, r2.json), "replay body must be identical:\n%s\n%s" % (r1.text, r2.text))
    r3 = expect(p.call(1, k, "A"), 200, msg="second replay")
    ok(same_json(r1.json, r3.json), "second replay body identical")
    eq(p.effect(), one, "replays have no further effect")
    # key order and whitespace do not matter
    raw = messy_json(p.body("A"))
    r4 = expect(p.call(1, k, None, raw=raw), 200, msg="same JSON value, different layout")
    ok(same_json(r1.json, r4.json), "re-ordered body replay identical")
    eq(p.effect(), one, "still one effect")
    # and the layout of the first request does not matter either
    k2 = fresh_key()
    r5 = expect(p.call(1, k2, None, raw=messy_json(p.body("B")), idx=1), 201, msg="first use with a messy body")
    r6 = expect(p.call(1, k2, "B", idx=1), 200, msg="replay with a compact body")
    ok(same_json(r5.json, r6.json), "messy first, compact replay")


@test("S1-102", "S1-107", "S1-073")
@each_path
def test_same_key_different_body_is_a_conflict(p):
    k = fresh_key()
    r1 = expect(p.call(1, k, "A"), 201)
    after = p.effect()
    expect(p.call(1, k, "B"), 409, "idempotency_key_reuse", "different valid body")
    expect(p.call(1, k, "bad"), 409, "idempotency_key_reuse", "invalid body: a claimed key is resolved before validation")
    expect(p.call(1, k, "nf"), 409, "idempotency_key_reuse", "body naming a missing resource")
    eq(p.effect(), after, "conflicts have no effect")
    r = expect(p.call(1, k, "A"), 200, msg="the original still replays")
    ok(same_json(r.json, r1.json), "original response")
    # a body that is not a JSON object is a parse-level problem (400), decided before the key
    for raw in ("{", "[]"):
        expect(p.call(1, k, None, raw=raw), 400, "malformed_request", "raw %r with a claimed key" % raw)
    eq(p.effect(), after, "no effect")


@test("S1-103", "S1-100", "S1-101", "S1-072")
@each_path
def test_key_of_a_failed_request_is_reusable(p):
    zero = p.effect()
    k = fresh_key()
    expect(p.call(1, k, "bad"), 422, "validation_failed")
    eq(p.effect(), zero, "failed request has no effect")
    r = expect(p.call(1, k, "A"), 201, msg="same key after a 422 is a first use")
    one = p.effect()
    expect(p.call(1, k, "A"), 200, msg="and now it replays")
    eq(p.effect(), one, "one effect")
    # 404 failures do not claim the key either
    k2 = fresh_key()
    if p.name != "POST /requests/{id}/pay":
        expect(p.call(1, k2, "nf"), 404, "not_found")
        r2 = expect(p.call(1, k2, "B"), 201, msg="same key after a 404, different body")
        expect(p.call(1, k2, "B"), 200, msg="replay")
        expect(p.call(1, k2, "nf"), 409, "idempotency_key_reuse", "now claimed by B")
    else:
        # unknown request id => 404, then the key still works on a real request
        k3 = fresh_key()
        expect(http("POST", "/requests/rq_missing/pay", body={}, token=p.actor(1).token, key=k3), 404, "not_found")
        expect(p.call(1, k3, "A", idx=1), 201, msg="key after a 404")
    # an unauthenticated or forbidden attempt does not claim anyone's key
    k4 = fresh_key()
    expect(http("POST", p.url(), body=p.body("A"), key=k4), 401, "unauthenticated")


@test("S1-097", "S1-096")
@each_path
def test_keys_are_scoped_to_the_user(p):
    zero = p.effect()
    k = fresh_key()
    r1 = expect(p.call(1, k, "A"), 201, msg="user 1 first use")
    # user 2 sends the same key with a *different* body: independent first use
    r2 = expect(p.call(2, k, "B"), 201, msg="same key string, other user, different body")
    ok(not same_json(r1.json, r2.json), "different resources")
    # and with the same body shape as user 1 had: still a first use for user 2 (no cross-user replay)
    k2 = fresh_key()
    ra = expect(p.call(1, k2, "A", idx=1), 201, msg="user 1 uses k2")
    rb = expect(p.call(2, k2, "A", idx=1), 201, msg="user 2 uses k2 with the same body: first use, not a replay")
    ok(not same_json(ra.json, rb.json), "each user gets their own resource")
    # each user's own key is still bound to their own body
    expect(p.call(1, k, "B"), 409, "idempotency_key_reuse")
    expect(p.call(2, k, "A"), 409, "idempotency_key_reuse")
    expect(p.call(1, k, "A"), 200)
    expect(p.call(2, k, "B"), 200)


@test("S1-080", "S1-096", "S1-079", "S1-082")
@each_path
def test_key_length_limits(p):
    k255 = "k" * 255
    expect(p.call(1, k255, "A", idx=0), 201, msg="255-character key")
    expect(p.call(1, k255, "A", idx=0), 200, msg="255-character key replays")
    expect(p.call(1, "x", "A", idx=1), 201, msg="1-character key")
    zero = p.effect()
    expect(p.call(1, "k" * 256, "A", idx=2), 422, "validation_failed", "256-character key")
    expect(p.call(1, "k" * 1000, "A", idx=2), 422, "validation_failed", "1000-character key")
    eq(p.effect(), zero, "rejected key => no effect")
    # the 256-character key did not claim anything: idx 2 is still usable
    expect(p.call(1, fresh_key(), "A", idx=2), 201, msg="after the rejection")
    # unusual but valid characters
    expect(p.call(1, "k e/y:+=~%.-_", "A", idx=3), 201, msg="key with spaces and punctuation")


@test("S1-105", "S1-207", "S1-096", "S1-203")
@each_path
def test_concurrent_identical_requests_take_effect_once(p):
    zero = p.effect()
    k = fresh_key()
    res = burst([lambda: p.call(1, k, "A", idx=0) for _ in range(20)])
    codes = sorted(r.status for r in res)
    eq(codes, [200] * 19 + [201], "exactly one 201, the others 200")
    bodies = {json.dumps(r.json, sort_keys=True) for r in res}
    eq(len(bodies), 1, "all responses carry the same body")
    eq(p.effect(), zero + 1, "the operation takes effect only once")
    # mixed bodies, same key: one winner; same-body callers replay, others conflict
    k2 = fresh_key()
    before = p.effect()
    fns = [(lambda: p.call(1, k2, "A", idx=1)) for _ in range(10)] + [(lambda: p.call(1, k2, "B", idx=1)) for _ in range(10)]
    res = burst(fns)
    eq(p.effect(), before + 1, "the contested key took effect once")
    wins = [i for i, r in enumerate(res) if r.status == 201]
    eq(len(wins), 1, "exactly one winner for a contested key: %r" % sorted(r.status for r in res))
    winner_variant = "A" if wins[0] < 10 else "B"
    for i, r in enumerate(res):
        variant = "A" if i < 10 else "B"
        if r.status == 201:
            continue
        if variant == winner_variant:
            eq(r.status, 200, "same body as the winner replays")
        else:
            expect(r, 409, "idempotency_key_reuse", "other body conflicts")


@test("S1-106", "S1-101", "S1-133", "S1-202")
@each_path
def test_replay_survives_later_changes(p):
    k = fresh_key()
    r1 = expect(p.call(1, k, "A"), 201)
    p.mutate()
    snapshot = p.effect()
    r2 = expect(p.call(1, k, "A"), 200, msg="replay after the world changed")
    ok(same_json(r1.json, r2.json), "replay returns the original response, not the current state")
    eq(p.effect(), snapshot, "replay makes no state change")


@test("S1-098", "S1-096")
def test_same_key_on_different_paths_is_not_a_replay():
    w = world({"ada": 1000, "bob": 0, "cy": 0})
    ada, bob, cy = w["ada"], w["bob"], w["cy"]
    k = fresh_key()
    body = {"to_handle": "bob", "payer_handle": "bob", "participant_handles": ["bob"], "amount": 10, "note": "x"}
    r_pay = expect(ada.post("/payments", body, key=k), 201, msg="/payments")
    r_req = expect(ada.post("/requests", body, key=k), 201, msg="/requests with the same key and body")
    r_spl = expect(ada.post("/splits", body, key=k), 201, msg="/splits with the same key and body")
    ok("payment_id" in r_pay.json and "request_id" in r_req.json and "split_id" in r_spl.json, "each path's own response")
    eq(bob.balance, 10, "one payment")
    # each replays on its own path with its own body
    ok(same_json(expect(ada.post("/payments", body, key=k), 200).json, r_pay.json), "payments replay")
    ok(same_json(expect(ada.post("/requests", body, key=k), 200).json, r_req.json), "requests replay")
    ok(same_json(expect(ada.post("/splits", body, key=k), 200).json, r_spl.json), "splits replay")
    eq(bob.balance, 10, "still one payment")
    eq(len(bob.all_requests()), 2, "one request + one split request")
    # pay: same key and same body {} on two different request paths => two payments
    q1 = expect(bob.request("ada", 5), 201).json["request_id"]
    q2 = expect(bob.request("ada", 6), 201).json["request_id"]
    k2 = fresh_key()
    p1 = expect(ada.post("/requests/%s/pay" % q1, {}, key=k2), 201, msg="pay q1").json
    p2 = expect(ada.post("/requests/%s/pay" % q2, {}, key=k2), 201, msg="pay q2: different path, same key and body").json
    eq((p1["request_id"], p2["request_id"], p1["amount"], p2["amount"]), (q1, q2, 5, 6), "two payments")
    eq(ada.balance, 1000 - 10 - 5 - 6, "both moved money")
    eq(expect(ada.post("/requests/%s/pay" % q1, {}, key=k2), 200).json, p1, "replay on q1's path")
    eq(expect(ada.post("/requests/%s/pay" % q2, {}, key=k2), 200).json, p2, "replay on q2's path")


@test("S1-105", "S1-006", "S1-005", "S1-117")
def test_retry_storm_of_payments_with_many_keys():
    """Each of 10 keys is sent 5 times concurrently: 10 payments, never 50."""
    w = world({"ada": 100_000, "bob": 0})
    ada, bob = w["ada"], w["bob"]
    keys = [fresh_key() for _ in range(10)]
    fns = [(lambda k=k, i=i: ada.pay("bob", 100 + i, key=k)) for i, k in enumerate(keys) for _ in range(5)]
    res = burst(fns, workers=50)
    created = [r for r in res if r.status == 201]
    eq(len(created), 10, "one 201 per key")
    ok(all(r.status in (200, 201) for r in res), "all others replay: %r" % sorted({r.status for r in res}))
    eq(bob.balance, sum(100 + i for i in range(10)), "each amount once")
    eq(ada.balance + bob.balance, 100_000, "conservation")
    eq(len(bob.all_payments()), 10, "ten payments exist")
