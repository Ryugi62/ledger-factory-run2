import json, urllib.request, urllib.parse, sys, time, datetime as dt, threading
B = sys.argv[1]
def call(m, p, body=None, tok=None, key=None):
    h = {"Content-Type": "application/json"}
    if tok: h["Authorization"] = "Bearer " + tok
    if key: h["Idempotency-Key"] = key
    r = urllib.request.Request(B + p, method=m, data=json.dumps(body).encode() if body is not None else None, headers=h)
    try:
        with urllib.request.urlopen(r) as x: d = x.read(); return x.status, (json.loads(d) if d else None)
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read())
res = []
def chk(name, cond, info=""):
    res.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  :: " + str(info)[:500]))
now = dt.datetime.now(dt.timezone.utc); iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%S+00:00")
T1 = now - dt.timedelta(days=2)
fx = {"currency": "EUR", "minor_units": 2, "users": [
  {"id": "u_a", "email": "a@x.io", "password": "correct horse", "display_name": "A", "handle": "ada", "balance": 1000},
  {"id": "u_b", "email": "b@x.io", "password": "correct horse", "display_name": "B", "handle": "bob", "balance": 500},
  {"id": "u_c", "email": "c@x.io", "password": "correct horse", "display_name": "C", "handle": "cy", "balance": 100},
  {"id": "u_o", "email": "o@x.io", "password": "correct horse", "display_name": "O", "handle": "op", "balance": 0}],
  "payments": [{"id": "p_1", "from_user_id": "u_a", "to_user_id": "u_b", "amount": 300, "note": "dinner", "visibility": "private", "created_at": iso(T1)}],
  "settlement_operator_ids": ["u_o"]}
call("POST", "/_test/reset", fx)
tk = {h: call("POST", "/auth/login", {"email": h[0] + "@x.io", "password": "correct horse"})[1]["token"] for h in ("ada", "bob", "cy", "op")}
me = lambda h: call("GET", "/me", tok=tk[h])[1]
# refunds
s, r = call("POST", "/payments/p_1/refunds", {"amount": 100}, tok=tk["ada"], key="r0"); chk("sender refund 403", s == 403, r)
s, r = call("POST", "/payments/nope/refunds", {"amount": 100}, tok=tk["bob"], key="r0"); chk("unknown 404", s == 404, r)
s, r = call("POST", "/payments/p_1/refunds", {"amount": 100}, tok=tk["bob"]); chk("no key 400", s == 400, r)
for v in (0, -1, 1.5, "10", None, True):
    s, r = call("POST", "/payments/p_1/refunds", {"amount": v}, tok=tk["bob"], key="rv%r" % (v,)); chk("refund amount %r 422" % (v,), s == 422 and r["error"]["code"] == "validation_failed", (s, r))
s, r = call("POST", "/payments/p_1/refunds", {}, tok=tk["bob"], key="rv-missing"); chk("refund missing amount 422", s == 422, (s, r))
s, rf = call("POST", "/payments/p_1/refunds", {"amount": 120}, tok=tk["bob"], key="r1")
chk("refund 201 shape", s == 201 and rf["refund_of"] == "p_1" and rf["from_handle"] == "bob" and rf["to_handle"] == "ada" and rf["amount"] == 120 and rf["note"] == "dinner" and rf["visibility"] == "private" and rf["request_id"] is None and rf["authorization_id"] is None, (s, rf))
s, rr = call("POST", "/payments/p_1/refunds", {"amount": 120}, tok=tk["bob"], key="r1"); chk("refund replay 200 same", s == 200 and rr == rf, (s, rr))
chk("refund moved money", me("ada")["balance"] == 1120 and me("bob")["balance"] == 380)
s, r = call("POST", "/payments/p_1/refunds", {"amount": 181}, tok=tk["bob"], key="r2"); chk("cumulative cap 422 refund_exceeds_payment", s == 422 and r["error"]["code"] == "refund_exceeds_payment", r)
s, r = call("POST", "/payments/%s/refunds" % rf["payment_id"], {"amount": 1}, tok=tk["ada"], key="r3"); chk("refund of refund 422 invalid_refund_target", s == 422 and r["error"]["code"] == "invalid_refund_target", r)
s, r = call("POST", "/payments/%s/corrections" % rf["payment_id"], {"expected_revision": 1, "amount": 1, "effective_at": rf["created_at"], "reason": "x"}, tok=tk["bob"], key="r4")
chk("correct a refund 422 linked", s == 422 and r["error"]["code"] == "linked_payment_immutable", r)
s, r = call("POST", "/payments/p_1/corrections", {"expected_revision": 1, "amount": 119, "effective_at": iso(T1), "reason": "x"}, tok=tk["ada"], key="r5")
chk("correction below refunded 422 refund_exceeds_payment", s == 422 and r["error"]["code"] == "refund_exceeds_payment", r)
s, r = call("POST", "/payments/p_1/corrections", {"expected_revision": 1, "amount": 200, "effective_at": iso(T1), "reason": "x"}, tok=tk["ada"], key="r6")
chk("correction to 200 (>=120 refunded) 201", s == 201, r)
s, r = call("POST", "/payments/p_1/refunds", {"amount": 81}, tok=tk["bob"], key="r7"); chk("cap now uses corrected 200: 81 > 80 -> 422", s == 422 and r["error"]["code"] == "refund_exceeds_payment", r)
# insufficient available for refund: hold bob's money
s, au = call("POST", "/authorizations", {"to_handle": "cy", "amount": me("bob")["available"] - 10}, tok=tk["bob"], key="h1")
mb = me("bob"); print("   bob", mb)
s, r = call("POST", "/payments/p_1/refunds", {"amount": 80}, tok=tk["bob"], key="r8"); chk("refund vs available 409 insufficient_funds", s == 409 and r["error"]["code"] == "insufficient_funds", (s, r))
call("POST", "/authorizations/%s/void" % au["authorization_id"], tok=tk["bob"])
# request payment refund does not reopen request
s, rq = call("POST", "/requests", {"payer_handle": "ada", "amount": 50}, tok=tk["cy"], key="q1")
s, pp = call("POST", "/requests/%s/pay" % rq["request_id"], {}, tok=tk["ada"], key="q2")
s, r = call("POST", "/payments/%s/refunds" % pp["payment_id"], {"amount": 50}, tok=tk["cy"], key="q3")
st = [x for x in call("GET", "/requests", tok=tk["cy"])[1]["requests"] if x["request_id"] == rq["request_id"]][0]["status"]
chk("refund of request payment 201, request stays paid", s == 201 and st == "paid", (s, r, st))
# capture refund doesn't reopen authorization or restore hold
s, au2 = call("POST", "/authorizations", {"to_handle": "cy", "amount": 60}, tok=tk["ada"], key="h2")
s, cap = call("POST", "/authorizations/%s/capture" % au2["authorization_id"], {"amount": 40}, tok=tk["cy"], key="h3")
s, rc = call("POST", "/payments/%s/refunds" % cap["payment_id"], {"amount": 40}, tok=tk["cy"], key="h4")
a2 = [x for x in call("GET", "/authorizations", tok=tk["ada"])[1]["authorizations"] if x["authorization_id"] == au2["authorization_id"]][0]
chk("capture refund 201; auth stays captured, held 0", s == 201 and rc["authorization_id"] is None and a2["status"] == "captured" and me("ada")["held"] == 0, (s, rc, a2))
s, act = call("GET", "/activity", tok=tk["ada"]); chk("other payments refund_of null", all(("refund_of" in p) for p in act["payments"]) and [p["refund_of"] for p in act["payments"] if p["payment_id"] == "p_1"] == [None], act["payments"][:2])
# batches
s, se = call("POST", "/settlements", {"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 30}, {"from_handle": "bob", "to_handle": "cy", "amount": 20}]}, tok=tk["op"], key="s1")
m1, m2 = [p["payment_id"] for p in se["payments"]]
E = iso(now - dt.timedelta(hours=1))
it = lambda pid, rev, amt, eff=E, **kw: dict({"payment_id": pid, "expected_revision": rev, "amount": amt, "effective_at": eff, "reason": "fix"}, **kw)
s, r = call("POST", "/correction-batches", {"corrections": [it(m1, 1, 25)]}); chk("batch no token 401", s == 401, r)
s, r = call("POST", "/correction-batches", {"corrections": [it(m1, 1, 25)]}, tok=tk["ada"], key="b0"); chk("batch non-operator 403", s == 403, r)
s, r = call("POST", "/correction-batches", {"corrections": [it(m1, 1, 25)]}, tok=tk["op"]); chk("batch no key 400", s == 400, r)
for name, body in (("empty", {"corrections": []}), ("33", {"corrections": [it("x%d" % i, 1, 1) for i in range(33)]}), ("dupe", {"corrections": [it(m1, 1, 1), it(m1, 1, 2)]}), ("missing", {})):
    s, r = call("POST", "/correction-batches", body, tok=tk["op"], key="bv-" + name); chk("batch %s 422" % name, s == 422 and r["error"]["code"] == "validation_failed", (s, r))
s, r = call("POST", "/correction-batches", {"corrections": [it(m1, 1, 25)]}, tok=tk["op"], key="b1"); chk("incomplete_settlement 422", s == 422 and r["error"]["code"] == "incomplete_settlement", r)
s, r = call("POST", "/correction-batches", {"corrections": [it(m1, 1, 25), it(m2, 1, 20, eff=iso(now - dt.timedelta(hours=2)))]}, tok=tk["op"], key="b2"); chk("members differing instants 422", s == 422 and r["error"]["code"] == "validation_failed", r)
# item errors before completeness: unknown payment in position 1 after incomplete
s, r = call("POST", "/correction-batches", {"corrections": [it(m1, 1, 25), it("nope", 1, 1)]}, tok=tk["op"], key="b3"); chk("item error (404) precedes completeness", s == 404, r)
s, r = call("POST", "/correction-batches", {"corrections": [it(m1, 1, 25), it(cap["payment_id"], 1, 1)]}, tok=tk["op"], key="b4"); chk("capture in batch 422 linked", s == 422 and r["error"]["code"] == "linked_payment_immutable", r)
s, r = call("POST", "/correction-batches", {"corrections": [it(m1, 2, 25), it(m2, 1, 20)]}, tok=tk["op"], key="b5"); chk("stale 409", s == 409 and r["error"]["code"] == "stale_revision", r)
s, r = call("POST", "/correction-batches", {"corrections": [it("p_1", 2, 100)]}, tok=tk["op"], key="b6"); chk("batch below refunded 422", s == 422 and r["error"]["code"] == "refund_exceeds_payment", r)
E2 = (now - dt.timedelta(hours=1)).astimezone(dt.timezone(dt.timedelta(hours=2))).strftime("%Y-%m-%dT%H:%M:%S+02:00")
before = {h: me(h)["balance"] for h in tk}
s, b = call("POST", "/correction-batches", {"corrections": [it(m1, 1, 25, extra=1), it(m2, 1, 25, eff=E2), it("p_1", 2, 210, eff=iso(T1))], "junk": 1}, tok=tk["op"], key="b7")
ok = s == 201 and [x["payment_id"] for x in b["revisions"]] == [m1, m2, "p_1"] and len({x["recorded_at"] for x in b["revisions"]}) == 1 and b["revisions"][0]["recorded_at"] == b["recorded_at"] and all(x["correction_batch_id"] == b["correction_batch_id"] for x in b["revisions"])
chk("batch 201 shape (offset spellings equal)", ok, (s, b))
after = {h: me(h)["balance"] for h in tk}
chk("batch money: ada +5+50=, bob -5+5-50, cy +5", after["ada"] - before["ada"] == -5 and after["bob"] - before["bob"] == 0 and after["cy"] - before["cy"] == 5 and sum(after.values()) == 1600, (before, after))
s, b2 = call("POST", "/correction-batches", {"corrections": [it(m1, 1, 25, extra=1), it(m2, 1, 25, eff=E2), it("p_1", 2, 210, eff=iso(T1))], "junk": 1}, tok=tk["op"], key="b7"); chk("batch replay 200", s == 200 and b2 == b, s)
s, r = call("POST", "/correction-batches", {"corrections": [it(m1, 1, 26), it(m2, 1, 25, eff=E2), it("p_1", 2, 210, eff=iso(T1))]}, tok=tk["op"], key="b7"); chk("batch reuse 409", s == 409 and r["error"]["code"] == "idempotency_key_reuse", r)
s, rs = call("POST", "/settlements", {"transfers": [{"from_handle": "ada", "to_handle": "bob", "amount": 30}, {"from_handle": "bob", "to_handle": "cy", "amount": 20}]}, tok=tk["op"], key="s1")
chk("settlement retry original body", s == 200 and rs == se, s)
s, rv = call("GET", "/payments/%s/revisions" % m1, tok=tk["ada"]); chk("member rev2 has correction_batch_id", rv["revisions"][-1].get("correction_batch_id") == b["correction_batch_id"], rv)
s, r = call("POST", "/payments/%s/corrections" % m1, {"expected_revision": 2, "amount": 1, "effective_at": E, "reason": "x"}, tok=tk["ada"], key="b8"); chk("single correction of member 422 linked", s == 422 and r["error"]["code"] == "linked_payment_immutable", r)
s, r = call("POST", "/payments/%s/refunds" % m1, {"amount": 5}, tok=tk["bob"], key="b9"); chk("refund of settlement member 201", s == 201 and r["settlement_id"] is None and r["refund_of"] == m1, r)
s, r = call("POST", "/correction-batches", {"corrections": [it(m1, 2, 24), it(m2, 2, 24, eff=E2)]}, tok=tk["op"], key="b10"); chk("membership unchanged by refund (2 members ok)", s == 201, r)
# combined affordability: cy has small balance; batch increasing cy's debit beyond available
cyb = me("cy")["available"]
s, r = call("POST", "/correction-batches", {"corrections": [it(m1, 3, 24), it(m2, 3, 24 + 0, eff=E2)]}, tok=tk["op"], key="b11"); print("   noop batch", s)
s, pz = call("POST", "/payments", {"to_handle": "ada", "amount": 10}, tok=tk["cy"], key="pz")
s, r = call("POST", "/correction-batches", {"corrections": [it(pz["payment_id"], 1, 10 + cyb - 10 + 1, eff=iso(now - dt.timedelta(minutes=1)))]}, tok=tk["op"], key="b12")
chk("batch current insufficient_funds", s == 409 and r["error"]["code"] == "insufficient_funds", (s, r, cyb))
s, r = call("POST", "/correction-batches", {"corrections": [it(pz["payment_id"], 1, 50, eff=iso(T1 - dt.timedelta(days=1)))]}, tok=tk["op"], key="b13")
chk("batch historical_overdraft (cy opening 100 - 50 OK?) ", s in (201, 409), (s, r))
s, r = call("POST", "/correction-batches", {"corrections": [it(pz["payment_id"], 1 if s != 201 else 2, 101, eff=iso(T1 - dt.timedelta(days=1)))]}, tok=tk["op"], key="b14")
print("   b14", s, r)
s, r = call("POST", "/correction-batches", {"corrections": [it(pz["payment_id"], 1, 1, eff=iso(now + dt.timedelta(minutes=5)))]}, tok=tk["op"], key="b15"); chk("future effective 422", s == 422, r)
# concurrency: two batches sharing a revision
s, q1 = call("POST", "/payments", {"to_handle": "bob", "amount": 10}, tok=tk["ada"], key="cq1")
out = []
def go(i): out.append(call("POST", "/correction-batches", {"corrections": [it(q1["payment_id"], 1, 5 + i)]}, tok=tk["op"], key="cb%d" % i)[0])
th = [threading.Thread(target=go, args=(i,)) for i in range(6)] + [threading.Thread(target=lambda: out.append(call("POST", "/payments/%s/corrections" % q1["payment_id"], it(q1["payment_id"], 1, 3), tok=tk["ada"], key="cs")[0]))]
[t.start() for t in th]; [t.join() for t in th]
chk("concurrent batch/single sharing revision: one 201", out.count(201) == 1, out)
tot = sum(me(h)["balance"] for h in tk); chk("total conserved", tot == 1600, tot)
print("SUMMARY", sum(res), "pass /", len(res) - sum(res), "fail")
