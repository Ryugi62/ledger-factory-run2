"""Stage 2: the seven idempotent write paths. The stage-1 matrix is re-run over the two new paths."""
import json
import sys

from lib2 import *  # noqa: F401,F403
import p_idempotency as P   # stage-1 matrix definitions (Path, over, the checks)


class Authorize(P.Path):
    name = "POST /authorizations"

    def fresh(self):
        self.w = world2({"ada": 100_000, "bob": 0, "cy": 100_000})
        self.users = {1: self.w["ada"], 2: self.w["cy"]}
        self.seed = 200_000
        return self

    def body(self, variant, idx=0, actor=1):
        return {"A": {"to_handle": "bob", "amount": 10, "note": "a"},
                "B": {"to_handle": "bob", "amount": 11, "note": "a"},
                "bad": {"to_handle": "bob", "amount": 0},
                "nf": {"to_handle": "nobody", "amount": 10}}[variant]

    def url(self, idx=0, actor=1):
        return "/authorizations"

    def effect(self):
        return len(all_auths(self.w["bob"]))

    def mutate(self):
        for actor in (1,):
            u = self.users[actor]
            for a in all_auths(u, direction="outgoing"):
                expect(void(u, a["authorization_id"]), 200, msg="void the created authorization")


class Capture(P.Path):
    name = "POST /authorizations/{id}/capture"
    nf_url = "/authorizations/a_missing/capture"

    def fresh(self):
        self.w = world2({"ada": 100_000, "bob": 0, "cy": 100_000, "dee": 0, "obs": 0})
        ada, cy, bob, dee = self.w["ada"], self.w["cy"], self.w["bob"], self.w["dee"]
        self.users = {1: bob, 2: dee}
        self.aids = {1: [expect(authorize(ada, "bob", 100), 201).json["authorization_id"] for _ in range(4)],
                     2: [expect(authorize(cy, "dee", 100), 201).json["authorization_id"] for _ in range(4)]}
        self.seed = 200_000
        return self

    def body(self, variant, idx=0, actor=1):
        return {"A": {"amount": 10}, "B": {"amount": 11}, "bad": {"amount": 0}, "nf": {"amount": 12}}[variant]

    def url(self, idx=0, actor=1):
        return "/authorizations/%s/capture" % self.aids[actor][idx]

    def effect(self):
        return len(self.w["obs"].all_payments())

    def mutate(self):
        ada = self.w["ada"]
        expect(ada.pay("cy", me_ok(ada)["available"]), 201, msg="drain the payer's available funds")


NEW = [Authorize, Capture]
MATRIX = [
    ("test_missing_or_empty_key", ("S2-099", "S2-113", "S2-123")),
    ("test_first_use_then_replay", ("S2-099",)),
    ("test_same_key_different_body_is_a_conflict", ("S2-099", "S2-125")),
    ("test_key_of_a_failed_request_is_reusable", ("S2-099",)),
    ("test_keys_are_scoped_to_the_user", ("S2-099",)),
    ("test_key_length_limits", ("S2-099",)),
    ("test_concurrent_identical_requests_take_effect_once", ("S2-099", "S2-171", "S2-092")),
    ("test_replay_survives_later_changes", ("S2-099",)),
]
for _name, _ids in MATRIX:
    _fn = getattr(P, _name).__wrapped__
    _t = P.over(NEW, _fn)
    _t.__name__ = _name + "_new_paths"
    _t.__module__ = __name__
    test(*_ids)(_t)


@test("S2-099", "S2-125")
def test_same_key_on_different_paths_new_paths():
    w = world2({"ada": 1000, "bob": 0, "cy": 0})
    ada, bob = w["ada"], w["bob"]
    k = fresh_key()
    body = {"to_handle": "bob", "amount": 10}
    r_pay = expect(ada.post("/payments", body, key=k), 201)
    r_auth = expect(ada.post("/authorizations", body, key=k), 201, msg="same key and body on /authorizations is a different request")
    ok("payment_id" in r_pay.json and "authorization_id" in r_auth.json and "status" in r_auth.json, "own responses")
    eq(expect(ada.post("/payments", body, key=k), 200).json, r_pay.json, "payments replay")
    eq(expect(ada.post("/authorizations", body, key=k), 200).json, r_auth.json, "authorizations replay")
    eq(len(all_auths(ada)), 1, "one authorization")
    eq(me_ok(ada)["held"], 10, "one hold")
    # the same key and body {} on two capture paths
    a1 = expect(authorize(ada, "bob", 100), 201).json["authorization_id"]
    a2 = expect(authorize(ada, "bob", 200), 201).json["authorization_id"]
    k2 = fresh_key()
    c1 = expect(capture(bob, a1, {}, key=k2), 201, msg="capture a1").json
    c2 = expect(capture(bob, a2, {}, key=k2), 201, msg="capture a2: different path, same key and body").json
    eq((c1["amount"], c2["amount"]), (100, 200), "two payments")
    eq(expect(capture(bob, a1, {}, key=k2), 200).json, c1, "replay a1")
    eq(expect(capture(bob, a2, {}, key=k2), 200).json, c2, "replay a2")
    # the same key used for a request pay with body {} is yet another path
    rid = expect(bob.request("ada", 7), 201).json["request_id"]
    expect(ada.pay_request(rid, {}, key=k2), 201, msg="same key, /requests/{id}/pay")
    eq(me_ok(ada)["total"], 1000 - 10 - 100 - 200 - 7, "totals")


@test("S2-099", "S2-171", "S2-092")
def test_retried_captures_with_many_keys():
    """Ten captures, each key sent five times concurrently: ten payments, never fifty."""
    w = world2({"ada": 100_000, "bob": 0})
    ada, bob = w["ada"], w["bob"]
    aid = expect(authorize(ada, "bob", 5_000), 201).json["authorization_id"]
    keys = [fresh_key() for _ in range(10)]
    fns = [(lambda k=k, i=i: capture(bob, aid, {"amount": 100 + i, "final": False}, key=k)) for i, k in enumerate(keys) for _ in range(5)]
    res = burst(fns, workers=50)
    eq(sum(1 for r in res if r.status == 201), 10, "one 201 per key")
    ok(all(r.status in (200, 201) for r in res), "others replay: %r" % sorted({r.status for r in res}))
    total_captured = sum(100 + i for i in range(10))
    a = auth_by_id(bob, aid)
    check_auth(a, status="open", captured=total_captured, remaining=5_000 - total_captured)
    eq(len(a["payment_ids"]), 10, "ten capture payments")
    m = me_ok(ada)
    eq((m["total"], m["held"]), (100_000 - total_captured, 5_000 - total_captured), "money moved once per key")
