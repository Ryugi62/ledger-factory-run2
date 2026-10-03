"""Stage 3: settlement members and captures are linked payments — revision 1 only, no single-payment corrections."""
from datetime import timedelta

from lib3 import *  # noqa: F401,F403


@test("S3-091", "S3-092", "S3-093", "S3-034", "S3-115", "S3-059", "S3-029", "S3-030", "S3-064")
def test_settlement_members_have_committed_at_revisions_and_reject_corrections():
    w = lib2.world2({"ada": 1000, "bob": 500, "cy": 0, "op": 0}, operators=["op"])
    ada, bob, cy, op = w["ada"], w["bob"], w["cy"], w["op"]
    body = {"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 100, "visibility": "private", "note": "a"},
                          {"from_handle": "bob", "to_handle": "cy", "amount": 40},
                          {"from_handle": "ada", "to_handle": "cy", "amount": 25, "note": "c"}]}
    key = fresh_key()
    r = expect(op.post("/settlements", body, key=key), 201)
    st = r.json
    committed = st["committed_at"]
    members = st["payments"]
    eq(len(members), 3, "three member receipts")
    owners = {"ada": ada, "bob": bob}
    for m, sender in zip(members, ("ada", "bob", "ada")):
        eq(m["settlement_id"], st["settlement_id"], "settlement_id links the member")
        same_instant(m["created_at"], committed, "member created_at = committed_at")
        rv = revisions(owners[sender], m["payment_id"])
        eq(len(rv), 1, "a member has revision 1 only")
        eq((rv[0]["revision"], rv[0]["amount"], rv[0]["reason"]), (1, m["amount"], ""), "revision 1 of a settlement member")
        same_instant(rv[0]["effective_at"], committed, "member effective_at = committed_at")
        same_instant(rv[0]["recorded_at"], committed, "member recorded_at = committed_at")
        expect(op.get("/payments/%s/revisions" % m["payment_id"]), 404, "not_found", "the operator is not a party to the member payment")
    # single-payment corrections are rejected for members, whatever the amount; nothing is claimed or changed
    before = observe_state(w, [(ada, members[0]["payment_id"]), (bob, members[1]["payment_id"])])
    for m, owner in zip(members, (ada, bob, ada)):
        k = fresh_key()
        for attempt in range(2):
            for amount in (m["amount"], m["amount"] - 1, 0):
                expect(correct(owner, m["payment_id"], 1, amount, m["created_at"], "try", key=k if amount == m["amount"] else "auto"),
                       422, "linked_payment_immutable", "correction of a settlement member (amount %d)" % amount)
    eq(observe_state(w, [(ada, members[0]["payment_id"]), (bob, members[1]["payment_id"])]), before, "rejected corrections change nothing")
    # statements: the members appear with committed_at as effective and recorded time, once each, by the ordinary parties only
    sa = full_stmt(ada)["entries"]
    eq(sorted(e["payment"]["payment_id"] for e in sa), sorted([members[0]["payment_id"], members[2]["payment_id"]]), "ada: exactly her two members")
    for e in sa:
        eq((e["revision"], e["payment"]["settlement_id"]), (1, st["settlement_id"]), "revision 1, linked to the batch")
        same_instant(e["effective_at"], committed, "entry effective_at")
        same_instant(e["recorded_at"], committed, "entry recorded_at")
    eq(sorted(e["delta"] for e in sa), [-100, -25], "deltas")
    eq(full_stmt(op)["entries"], [], "the operator is no party: statements show only the caller's own payments")
    ok(members[0]["payment_id"] not in [p["payment_id"] for p in op.all_payments()], "the operator's feed still hides the private member (stage-1 privacy)")
    # the receipt is unchanged and replays
    rp = expect(op.post("/settlements", body, key=key), 200)
    eq(rp.json, st, "the settlement receipt replays unchanged")
    # views under known_at: before the commit nothing was known
    pre = fmt(inst(committed) - timedelta(seconds=1))
    eq(me_at(ada, known_at=pre)["balance"], 1000, "known_at before the settlement was recorded")
    eq(me_at(ada)["balance"], 875, "after")
    eq(me_at(ada, as_of=committed)["balance"], 875, "as_of exactly committed_at")
    eq(me_at(ada, as_of=pre)["balance"], 1000, "as_of just before committed_at")
    # a plain payment of the same sender is still correctable (control)
    p = new_payment(ada, "bob", 10)
    expect(correct(ada, p["payment_id"], 1, 5, p["created_at"]), 201)


@test("S3-096", "S3-113", "S3-112", "S3-034", "S3-115", "S3-059", "S3-099")
def test_captures_are_immutable_and_appear_exactly_once():
    w = lib2.world2({"ada": 5000, "bob": 0, "cy": 0})
    ada, bob, cy = w["ada"], w["bob"], w["cy"]
    a = expect(lib2.authorize(ada, "bob", 2000, note="dep", visibility="private"), 201).json
    aid = a["authorization_id"]
    eq(full_stmt(ada)["entries"], [], "an authorization is not a payment: nothing in the statement")
    caps = []
    for amount, final in ((700, False), (500, False), (800, True)):
        sleep_gap(1.1)
        caps.append(expect(lib2.capture(bob, aid, {"amount": amount, "final": final}), 201).json)
    ids = [c["payment_id"] for c in caps]
    for c in caps:
        eq(c["authorization_id"], aid, "capture payment links its authorization")
        rv = revisions(ada, c["payment_id"])
        eq(len(rv), 1, "a capture has revision 1 only")
        same_instant(rv[0]["effective_at"], c["created_at"], "capture effective_at = created_at")
        same_instant(rv[0]["recorded_at"], c["created_at"], "capture recorded_at = created_at")
        eq(revisions(bob, c["payment_id"]), rv, "both parties read the same history")
    before = observe_state(w, [(ada, i) for i in ids])
    for c in caps:
        k = fresh_key()
        expect(correct(ada, c["payment_id"], 1, c["amount"] - 1, c["created_at"], "no", key=k), 422, "linked_payment_immutable", "correction of a capture")
        expect(correct(ada, c["payment_id"], 1, c["amount"] - 1, c["created_at"], "no", key=k), 422, "linked_payment_immutable", "same key again: still a first use")
        expect(correct(ada, c["payment_id"], 1, 0, c["created_at"], "no"), 422, "linked_payment_immutable", "reversal of a capture")
    eq(observe_state(w, [(ada, i) for i in ids]), before, "rejected corrections change nothing")
    for u, sign in ((ada, -1), (bob, 1)):
        st = full_stmt(u)
        eq(ids_of_entries(st["entries"]), ids, "%s: each capture exactly once, in order (no entries for the authorization or its release)" % u.handle)
        eq([e["delta"] for e in st["entries"]], [sign * 700, sign * 500, sign * 800], "deltas")
        for e in st["entries"]:
            eq(e["payment"]["authorization_id"], aid, "entries keep their link")
            eq((e["revision"], e["payment"]["visibility"], e["payment"]["note"]), (1, "private", "dep"), "copied note/visibility, revision 1")
    eq(full_stmt(cy)["entries"], [], "a third party sees nothing")
    m = lib2.me_ok(ada)
    eq((m["total"], m["held"], m["available"]), (3000, 0, 3000), "final capture released the remainder")
    p = new_payment(ada, "bob", 10)
    expect(correct(ada, p["payment_id"], 1, 5, p["created_at"]), 201, msg="(control) a plain payment is still correctable")
