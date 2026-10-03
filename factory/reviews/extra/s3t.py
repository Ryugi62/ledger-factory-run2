import json, urllib.request, urllib.parse, sys, time, datetime as dt, threading
B = sys.argv[1]
def call(m, p, body=None, tok=None, key=None, raw=None):
    h = {"Content-Type": "application/json"}
    if tok: h["Authorization"] = "Bearer " + tok
    if key: h["Idempotency-Key"] = key
    data = raw.encode() if raw is not None else (json.dumps(body).encode() if body is not None else None)
    r = urllib.request.Request(B + p, method=m, data=data, headers=h)
    try:
        with urllib.request.urlopen(r) as x: d = x.read(); return x.status, (json.loads(d) if d else None)
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read())
res = []
def chk(name, cond, info=""):
    res.append((name, bool(cond))); print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  :: " + str(info)[:400]))
now = dt.datetime.now(dt.timezone.utc)
iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%S+00:00")
q = lambda **k: "?" + urllib.parse.urlencode(k)
T1, T2, T3 = now - dt.timedelta(days=3), now - dt.timedelta(days=2), now - dt.timedelta(days=1)
fx = {"currency": "EUR", "minor_units": 2, "users": [
  {"id": "u_a", "email": "a@x.io", "password": "correct horse", "display_name": "A", "handle": "ada", "balance": 1000},
  {"id": "u_b", "email": "b@x.io", "password": "correct horse", "display_name": "B", "handle": "bob", "balance": 500},
  {"id": "u_c", "email": "c@x.io", "password": "correct horse", "display_name": "C", "handle": "cy", "balance": 0}],
  "payments": [
   {"id": "p_1", "from_user_id": "u_a", "to_user_id": "u_b", "amount": 300, "note": "", "visibility": "public", "created_at": iso(T1)},
   {"id": "p_2", "from_user_id": "u_b", "to_user_id": "u_a", "amount": 100, "note": "", "visibility": "private", "created_at": iso(T2)},
   {"id": "p_3", "from_user_id": "u_a", "to_user_id": "u_b", "amount": 50, "note": "", "visibility": "public", "created_at": iso(T2)}],
  "settlement_operator_ids": ["u_a"]}
# future seeded created_at -> 422, state unchanged
bad = json.loads(json.dumps(fx)); bad["payments"][0]["created_at"] = iso(now + dt.timedelta(hours=1))
call("POST", "/_test/reset", fx)
ta = call("POST", "/auth/login", {"email": "a@x.io", "password": "correct horse"})[1]["token"]
s, _ = call("POST", "/_test/reset", bad); chk("future seeded created_at -> 422", s == 422, s)
chk("state unchanged after bad reset", call("GET", "/me", tok=ta)[0] == 200)
call("POST", "/_test/reset", fx)
ta = call("POST", "/auth/login", {"email": "a@x.io", "password": "correct horse"})[1]["token"]
tb = call("POST", "/auth/login", {"email": "b@x.io", "password": "correct horse"})[1]["token"]
tc = call("POST", "/auth/login", {"email": "c@x.io", "password": "correct horse"})[1]["token"]
# opening: ada 1000 = open -300 +100 -50 -> open 1250 ; bob 500 = open +300 -100 +50 -> 250
chk("me current", call("GET", "/me", tok=ta)[1]["balance"] == 1000)
s, m = call("GET", "/me" + q(as_of=iso(T1 - dt.timedelta(seconds=1))), tok=ta)
chk("as_of before earliest = opening 1250", m.get("balance") == 1250 and m.get("as_of") == iso(T1 - dt.timedelta(seconds=1)), m)
s, m = call("GET", "/me" + q(as_of=iso(T1)), tok=ta); chk("as_of exactly at payment inclusive 950", m.get("balance") == 950, m)
s, m = call("GET", "/me" + q(as_of=iso(T2)), tok=ta); chk("combined same instant 1000", m.get("balance") == 1000, m)
for v in ("2026-09-24T13:20:00", "2026-09-24", "", "2026-09-24 13:20:00+00:00", "2026-02-30T00:00:00Z"):
    s, _ = call("GET", "/me" + q(as_of=v), tok=ta); chk("as_of %r -> 422" % v, s == 422, s)
s, m = call("GET", "/me" + q(as_of="2026-09-24T13:20:00.5-05:30"), tok=ta); chk("odd offset echoed exactly", m.get("as_of") == "2026-09-24T13:20:00.5-05:30", m)
# statement
s, st = call("GET", "/statement", tok=ta)
chk("statement full", st["opening_balance"] == 1250 and st["closing_balance"] == 1000 and [e["delta"] for e in st["entries"]] == [-300, 100, -50] or [e["delta"] for e in st["entries"]] == [-300, -50, 100], st)
ids = [e["payment"]["payment_id"] for e in st["entries"]]; chk("tie order by id (p_2 < p_3)", ids == ["p_1", "p_2", "p_3"], ids)
chk("snapshot token present", isinstance(st.get("snapshot"), str) and st["snapshot"])
chk("balance_after running", [e["balance_after"] for e in st["entries"]] == [950, 1050, 1000], st["entries"])
s, st2 = call("GET", "/statement" + q(**{"from": iso(T2), "to": iso(T3)}), tok=ta)
chk("window [T2,T3) opening 950 closing 1000", st2["opening_balance"] == 950 and st2["closing_balance"] == 1000 and len(st2["entries"]) == 2, st2)
s, st3 = call("GET", "/statement" + q(**{"to": iso(T2)}), tok=ta)
chk("half-open: to=T2 excludes T2", len(st3["entries"]) == 1 and st3["closing_balance"] == 950, st3)
s, pg = call("GET", "/statement" + q(limit=1, offset=1), tok=ta)
chk("paged balances unchanged", pg["opening_balance"] == 1250 and pg["closing_balance"] == 1000 and pg["entries"][0]["balance_after"] == 1050 and pg["has_more"] is True, pg)
s, pg = call("GET", "/statement" + q(limit=5, offset=9), tok=ta); chk("offset beyond end", pg["entries"] == [] and pg["has_more"] is False, pg)
s, sc = call("GET", "/statement", tok=tc); chk("cy statement excludes public others", sc["entries"] == [] and sc["opening_balance"] == 0, sc)
for k, v in (("limit", "0"), ("offset", "-1"), ("from", "x"), ("limit", "1e1")):
    s, _ = call("GET", "/statement" + q(**{k: v}), tok=ta); chk("statement %s=%s -> 422" % (k, v), s == 422, s)
# snapshot
snap = st["snapshot"]
s, p4 = call("POST", "/payments", {"to_handle": "cy", "amount": 10}, tok=ta, key="k-p4")
s, sn = call("GET", "/statement" + q(snapshot=snap, limit=50), tok=ta)
chk("snapshot frozen after payment", len(sn["entries"]) == 3 and sn["closing_balance"] == 1000, sn)
s, _ = call("GET", "/statement" + q(snapshot=snap, known_at=iso(now)), tok=ta); chk("snapshot+known_at 422", s == 422, s)
s, _ = call("GET", "/statement" + q(snapshot=snap), tok=tb); chk("other user's snapshot 404", s == 404, s)
s, _ = call("GET", "/statement" + q(snapshot="nope"), tok=ta); chk("unknown snapshot 404", s == 404, s)
# corrections
s, r = call("POST", "/payments/p_1/corrections", {"expected_revision": 1, "amount": 200, "effective_at": iso(T1), "reason": "fix"}, tok=tb, key="c0")
chk("non-sender correction 403", s == 403 and r["error"]["code"] == "forbidden", r)
s, r = call("POST", "/payments/nope/corrections", {"expected_revision": 1, "amount": 200, "effective_at": iso(T1), "reason": "fix"}, tok=ta, key="c0")
chk("unknown 404", s == 404, r)
s, r = call("POST", "/payments/p_1/corrections", {"expected_revision": 1, "amount": 200, "effective_at": iso(T1), "reason": "fix"}, tok=ta)
chk("no key 400", s == 400 and r["error"]["code"] == "missing_idempotency_key", r)
for name, b in (("reason empty", {"reason": ""}), ("amount -1", {"amount": -1}), ("future eff", {"effective_at": iso(now + dt.timedelta(minutes=5))}),
                ("rev 0", {"expected_revision": 0}), ("missing reason", None), ("amount 1.5", {"amount": 1.5}), ("reason 201", {"reason": "x" * 201})):
    body = {"expected_revision": 1, "amount": 200, "effective_at": iso(T1), "reason": "fix"}
    if b is None: del body["reason"]
    else: body.update(b)
    s, r = call("POST", "/payments/p_1/corrections", body, tok=ta, key="cv-" + name)
    chk("correction %s -> 422" % name, s == 422 and r["error"]["code"] == "validation_failed", (s, r))
before_known = iso(dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=0))
time.sleep(1.1)
body = {"expected_revision": 1, "amount": 200, "effective_at": iso(T3), "reason": "fix"}
s, c1 = call("POST", "/payments/p_1/corrections", body, tok=ta, key="c1")
chk("correction 201", s == 201 and c1["revision"] == 2 and c1["amount"] == 200 and c1["payment_id"] == "p_1", (s, c1))
s, c1r = call("POST", "/payments/p_1/corrections", body, tok=ta, key="c1"); chk("replay 200 same", s == 200 and c1r == c1, (s, c1r))
s, r = call("POST", "/payments/p_1/corrections", dict(body, reason="other"), tok=ta, key="c1"); chk("reuse 409", s == 409 and r["error"]["code"] == "idempotency_key_reuse", r)
s, r = call("POST", "/payments/p_1/corrections", body, tok=ta, key="c1b"); chk("stale 409", s == 409 and r["error"]["code"] == "stale_revision", r)
ma, mb = call("GET", "/me", tok=ta)[1], call("GET", "/me", tok=tb)[1]
chk("decrease moved 100 back to sender", ma["balance"] == 1000 - 10 + 100 and mb["balance"] == 150, (ma, mb))
s, rv = call("GET", "/payments/p_1/revisions", tok=tb); chk("revisions for receiver", s == 200 and [x["revision"] for x in rv["revisions"]] == [1, 2] and rv["revisions"][0]["reason"] == "", rv)
s, _ = call("GET", "/payments/p_1/revisions", tok=tc); chk("third party revisions 404", s == 404, s)
s, _ = call("GET", "/payments/p_1/revisions"); chk("no token 401", s == 401, s)
s, act = call("GET", "/activity", tok=ta); p1 = [p for p in act["payments"] if p["payment_id"] == "p_1"][0]
chk("activity shows original amount 300", p1["amount"] == 300 and len(act["payments"]) == 4, act)
# known_at: before correction -> rev1
s, m = call("GET", "/me" + q(as_of=iso(T1 + dt.timedelta(hours=1)), known_at=before_known), tok=ta)
chk("known_at before correction: rev1 at T1 -> 950", m.get("balance") == 950 and m.get("known_at") == before_known, m)
s, m = call("GET", "/me" + q(as_of=iso(T1 + dt.timedelta(hours=1))), tok=ta)
chk("latest: p_1 moved to T3 -> 1250 at T1+1h", m.get("balance") == 1250, m)
s, m = call("GET", "/me" + q(known_at=iso(T1 - dt.timedelta(days=1))), tok=ta)
chk("known_at before anything recorded: opening + nothing (1250 - 10?)", m.get("balance") == 1250, m)
s, stc = call("GET", "/statement", tok=ta)
e1 = [e for e in stc["entries"] if e["payment"]["payment_id"] == "p_1"][0]
chk("statement entry uses rev2 at T3", e1["revision"] == 2 and e1["payment"]["amount"] == 200 and e1["delta"] == -200 and e1["effective_at"] == c1["effective_at"], e1)
chk("statement sums", stc["opening_balance"] + sum(e["delta"] for e in stc["entries"]) == stc["closing_balance"] == 1090, stc)
s, stk = call("GET", "/statement" + q(known_at=before_known), tok=ta)
chk("statement known_at old -> rev1 only", [e["revision"] for e in stk["entries"] if e["payment"]["payment_id"] == "p_1"] == [1] and stk.get("known_at") == before_known, stk)
# zero amount reversal
s, c2 = call("POST", "/payments/p_3/corrections", {"expected_revision": 1, "amount": 0, "effective_at": iso(T2), "reason": "reverse"}, tok=ta, key="c2")
chk("reverse to zero 201", s == 201, c2)
s, stz = call("GET", "/statement", tok=ta); z = [e for e in stz["entries"] if e["payment"]["payment_id"] == "p_3"]
chk("zero-amount entry present with delta 0", len(z) == 1 and z[0]["delta"] == 0, z)
# insufficient vs historical_overdraft: bob has 200 now (150+50). increase p_1 back to 300+ needs ada funds -> fine; decrease p_2? p_2 is bob->ada; bob sender.
s, r = call("POST", "/payments/p_2/corrections", {"expected_revision": 1, "amount": 100000, "effective_at": iso(T2), "reason": "x"}, tok=tb, key="c3")
chk("current unaffordable increase -> insufficient_funds", s == 409 and r["error"]["code"] == "insufficient_funds", r)
# bob opening 250; p_1(+200@T3), p_2(-100@T2), p_3(0). move p_2 to before everything and increase to 260: bob at T2-? : opening 250 -260 <0 -> historical
s, r = call("POST", "/payments/p_2/corrections", {"expected_revision": 1, "amount": 200, "effective_at": iso(T1 - dt.timedelta(days=1)), "reason": "x"}, tok=tb, key="c4")
mb2 = call("GET", "/me", tok=tb)[1]
print("   bob now", mb2["balance"], "->", s, r)
s, r = call("POST", "/payments/p_2/corrections", {"expected_revision": 1, "amount": 260, "effective_at": iso(T1 - dt.timedelta(days=1)), "reason": "x"}, tok=tb, key="c5")
chk("historical overdraft (bob opening 250 - 260 before other credits)", s == 409 and r["error"]["code"] in ("historical_overdraft", "insufficient_funds"), (s, r))
s, r = call("POST", "/payments/p_2/corrections", {"expected_revision": 1, "amount": 260, "effective_at": iso(T1 - dt.timedelta(days=1)), "reason": "x"}, tok=tb, key="c5")
chk("failed correction did not claim key (same 409 again)", s == 409, (s, r))
tot = sum(call("GET", "/me", tok=t)[1]["balance"] for t in (ta, tb, tc)); chk("sum conserved 1500", tot == 1500, tot)
for asof in (T1 - dt.timedelta(days=2), T1, T2, T3, now):
    sm = sum(call("GET", "/me" + q(as_of=iso(asof)), tok=t)[1]["balance"] for t in (ta, tb, tc))
    chk("historical sum 1500 at %s" % iso(asof), sm == 1500, sm)
# settlement member / capture immutable
s, se = call("POST", "/settlements", {"transfers": [{"from_handle": "ada", "to_handle": "cy", "amount": 5}]}, tok=ta, key="s1")
pid = se["payments"][0]["payment_id"]
s, r = call("POST", "/payments/%s/corrections" % pid, {"expected_revision": 1, "amount": 1, "effective_at": iso(T3), "reason": "x"}, tok=ta, key="c6")
chk("settlement member 422 linked", s == 422 and r["error"]["code"] == "linked_payment_immutable", r)
s, rvs = call("GET", "/payments/%s/revisions" % pid, tok=ta)
chk("settlement rev1 eff=rec=committed_at", rvs["revisions"][0]["effective_at"] == rvs["revisions"][0]["recorded_at"] == se["committed_at"], (rvs, se["committed_at"]))
s, au = call("POST", "/authorizations", {"to_handle": "bob", "amount": 50}, tok=ta, key="a1")
s, cap = call("POST", "/authorizations/%s/capture" % au["authorization_id"], {"amount": 20, "final": False}, tok=tb, key="cap1")
s, r = call("POST", "/payments/%s/corrections" % cap["payment_id"], {"expected_revision": 1, "amount": 1, "effective_at": iso(T3), "reason": "x"}, tok=ta, key="c7")
chk("capture 422 linked", s == 422 and r["error"]["code"] == "linked_payment_immutable", r)
m = call("GET", "/me", tok=ta)[1]; mh = call("GET", "/me" + q(as_of=iso(dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=1))), tok=ta)[1]
chk("historical held view matches current (held 30)", m["held"] == 30 and mh["held"] == 30 and mh["available"] == mh["total"] - 30, (m, mh))
mpast = call("GET", "/me" + q(as_of=iso(T3)), tok=ta)[1]; chk("held 0 before hold existed", mpast["held"] == 0, mpast)
mfut = call("GET", "/me" + q(as_of=iso(now + dt.timedelta(hours=1))), tok=ta)[1]; chk("held 0 after expiry deadline (ttl 600)", mfut["held"] == 0, mfut)
s, al = call("GET", "/authorizations", tok=ta); chk("closed_at null while open", al["authorizations"][0]["closed_at"] is None, al)
s, sb = call("GET", "/statement", tok=tb); caps = [e for e in sb["entries"] if e["payment"].get("authorization_id")]
chk("capture appears once in statement", len(caps) == 1 and caps[0]["delta"] == 20, sb)
# concurrent corrections same expected revision
s, p9 = call("POST", "/payments", {"to_handle": "cy", "amount": 40}, tok=ta, key="p9")
out = []
def go(i): out.append(call("POST", "/payments/%s/corrections" % p9["payment_id"], {"expected_revision": 1, "amount": 30 + i, "effective_at": iso(T3), "reason": "r%d" % i}, tok=ta, key="cc%d" % i)[0])
th = [threading.Thread(target=go, args=(i,)) for i in range(8)]; [t.start() for t in th]; [t.join() for t in th]
chk("concurrent same expected_revision: exactly one 201", out.count(201) == 1 and out.count(409) == 7, out)
print("SUMMARY", sum(1 for _, ok in res if ok), "pass /", sum(1 for _, ok in res if not ok), "fail")
