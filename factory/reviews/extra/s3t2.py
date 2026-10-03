import json, urllib.request, urllib.parse, sys, datetime as dt
B = sys.argv[1]
def call(m, p, body=None, tok=None, key=None):
    h = {"Content-Type": "application/json"}
    if tok: h["Authorization"] = "Bearer " + tok
    if key: h["Idempotency-Key"] = key
    r = urllib.request.Request(B + p, method=m, data=json.dumps(body).encode() if body is not None else None, headers=h)
    try:
        with urllib.request.urlopen(r) as x: d = x.read(); return x.status, (json.loads(d) if d else None)
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read())
now = dt.datetime.now(dt.timezone.utc); iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%S+00:00")
T1, T2 = now - dt.timedelta(days=3), now - dt.timedelta(days=2)
fx = {"currency": "EUR", "minor_units": 2, "users": [
  {"id": "u_a", "email": "a@x.io", "password": "correct horse", "display_name": "A", "handle": "ada", "balance": 1000},
  {"id": "u_b", "email": "b@x.io", "password": "correct horse", "display_name": "B", "handle": "bob", "balance": 500}],
  "payments": [{"id": "p_1", "from_user_id": "u_a", "to_user_id": "u_b", "amount": 400, "note": "", "visibility": "public", "created_at": iso(T1)},
               {"id": "p_2", "from_user_id": "u_b", "to_user_id": "u_a", "amount": 300, "note": "", "visibility": "public", "created_at": iso(T2)}]}
call("POST", "/_test/reset", fx)
tb = call("POST", "/auth/login", {"email": "b@x.io", "password": "correct horse"})[1]["token"]
ta = call("POST", "/auth/login", {"email": "a@x.io", "password": "correct horse"})[1]["token"]
# bob opening = 500 - 400 + 300 = 400. bob@T1 = 800, @T2 = 500.
# move p_2 (bob->ada 300) to before T1 with amount 450: current debit 150 affordable (500); at T1-1d bob 400-450 = -50 -> historical_overdraft
body = {"expected_revision": 1, "amount": 450, "effective_at": iso(T1 - dt.timedelta(days=1)), "reason": "x"}
print("historical", call("POST", "/payments/p_2/corrections", body, tok=tb, key="h1"))
print("bob after", call("GET", "/me", tok=tb)[1]["balance"], "revs", len(call("GET", "/payments/p_2/revisions", tok=tb)[1]["revisions"]))
# boundary combined: move p_2 to exactly T1 with amount 450: at T1 bob 400+400-450 = 350 OK -> 201
body2 = dict(body, effective_at=iso(T1)); print("same-instant combined", call("POST", "/payments/p_2/corrections", body2, tok=tb, key="h1")[0])
# ada decrease on p_1 (ada->bob) to 0 debits bob 400; bob current 500-150=350 -> insufficient_funds
print("receiver-debit", call("POST", "/payments/p_1/corrections", {"expected_revision": 1, "amount": 0, "effective_at": iso(T1), "reason": "x"}, tok=ta, key="h3"))
