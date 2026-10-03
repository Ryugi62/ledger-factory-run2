"""Shared helpers for the stage-1 black-box probes.

Standard library only (Python 3.8+). Everything goes over HTTP to a running service.
"""
import http.client as _hc
import json
import os
import re
import threading
import time
import urllib.parse
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

_cfg = {"base": os.environ.get("PROBE_BASE_URL", "http://127.0.0.1:8080")}

PASSWORD = "correct horse"
MAX_AMOUNT = 1_000_000_000

# ---------------------------------------------------------------- bookkeeping
_lock = threading.Lock()
SERVER_ERRORS = []      # (method, path, status, body[:200])
SLOW = []               # (method, path, seconds, limit)
TESTS = []              # (func, ids)
SKIPS = {}


class Skip(Exception):
    pass


def set_base(url):
    _cfg["base"] = url.rstrip("/")


def base():
    return _cfg["base"]


class using_base:
    """Context manager: temporarily point every helper at another base URL."""
    def __init__(self, url):
        self.url = url.rstrip("/")

    def __enter__(self):
        self.old = _cfg["base"]
        _cfg["base"] = self.url

    def __exit__(self, *a):
        _cfg["base"] = self.old


def test(*ids):
    """Register a probe. `ids` are the ledger rows it covers."""
    def deco(fn):
        fn.ledger_ids = ids
        TESTS.append((fn, ids))
        return fn
    return deco


# ---------------------------------------------------------------- assertions
def ok(cond, msg="assertion failed"):
    if not cond:
        raise AssertionError(msg)


def eq(actual, expected, msg=""):
    if actual != expected:
        raise AssertionError("%s: expected %r, got %r" % (msg, expected, actual))


def is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


# ---------------------------------------------------------------- HTTP
class Resp:
    def __init__(self, status, headers, raw, elapsed):
        self.status = status
        self.headers = headers
        self.raw = raw
        self.elapsed = elapsed
        self._json = None
        self._parsed = False

    @property
    def text(self):
        return self.raw.decode("utf-8", "replace")

    @property
    def json(self):
        if not self._parsed:
            self._parsed = True
            try:
                self._json = json.loads(self.raw.decode("utf-8")) if self.raw else None
            except ValueError:
                self._json = None
        return self._json

    @property
    def code(self):
        j = self.json
        if isinstance(j, dict) and isinstance(j.get("error"), dict):
            return j["error"].get("code")
        return None

    def __repr__(self):
        return "<%s %s>" % (self.status, self.text[:300])


def _dump(obj):
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


def http(method, path, body=None, raw=None, token=None, key=None, headers=None,
         base_url=None, timeout=15):
    """One request. `body` is JSON-encoded; `raw` (str/bytes) is sent verbatim.
    `key` sets Idempotency-Key (an empty string sends an empty header)."""
    b = (base_url or base())
    u = urllib.parse.urlsplit(b)
    hdrs = {"Connection": "close"}
    data = None
    if raw is not None:
        data = raw.encode("utf-8") if isinstance(raw, str) else raw
    elif body is not None:
        data = _dump(body)
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    if token is not None:
        hdrs["Authorization"] = "Bearer " + token
    if key is not None:
        hdrs["Idempotency-Key"] = key
    if headers:
        hdrs.update(headers)
    limit = 10.0 if path.startswith("/_test/") else 5.0
    t0 = time.time()
    conn = http_client(u.hostname, u.port or 80, timeout)
    try:
        conn.request(method, path, body=data, headers=hdrs)
        r = conn.getresponse()
        rawb = r.read()
        hd = {k.lower(): v for k, v in r.getheaders()}
        resp = Resp(r.status, hd, rawb, time.time() - t0)
    finally:
        conn.close()
    with _lock:
        if resp.status >= 500:
            SERVER_ERRORS.append((method, path, resp.status, resp.text[:200]))
        if resp.elapsed > limit:
            SLOW.append((method, path, round(resp.elapsed, 2), limit))
    if resp.status >= 400:
        check_error_shape(resp)
    elif resp.status != 204 and resp.raw:
        ct = resp.headers.get("content-type", "").lower().replace(" ", "")
        ok(ct == "application/json;charset=utf-8",
           "%s %s: Content-Type must be application/json; charset=utf-8, got %r" % (method, path, ct))
    return resp


def http_client(host, port, timeout):
    return _hc.HTTPConnection(host, port, timeout=timeout)


def check_error_shape(resp):
    j = resp.json
    ok(isinstance(j, dict) and set(j.keys()) == {"error"},
       "error body must be {\"error\": {...}}, got %r" % resp.text[:200])
    e = j["error"]
    ok(isinstance(e, dict) and isinstance(e.get("code"), str) and e["code"]
       and isinstance(e.get("message"), str),
       "error object needs string code and message, got %r" % resp.text[:200])
    ct = resp.headers.get("content-type", "").lower().replace(" ", "")
    ok(ct == "application/json;charset=utf-8",
       "error Content-Type must be application/json; charset=utf-8, got %r" % ct)


def expect(resp, status, code=None, msg=""):
    """Assert status (and error code). Returns the response."""
    if resp.status != status or (code is not None and resp.code != code):
        raise AssertionError("%s expected %s%s, got %s %s" % (
            msg, status, (" " + code) if code else "", resp.status, resp.text[:300]))
    return resp


def fresh_key():
    return "k-" + uuid.uuid4().hex


def burst(fns, workers=None):
    """Run callables simultaneously (barrier-released). Returns results in order;
    an exception becomes the result item."""
    n = len(fns)
    # all workers are released together only if every task gets its own thread
    barrier = threading.Barrier(n) if (workers or n) >= n else None
    out = [None] * n

    def run(i):
        try:
            if barrier is not None:
                barrier.wait(timeout=30)
            out[i] = fns[i]()
        except BaseException as e:   # noqa
            out[i] = e
    with ThreadPoolExecutor(max_workers=workers or n) as ex:
        list(ex.map(run, range(n)))
    for o in out:
        if isinstance(o, BaseException):
            raise AssertionError("burst worker failed: %r" % (o,))
    return out


# ---------------------------------------------------------------- fixtures
def fx_user(handle, balance=0, uid=None, email=None, password=PASSWORD, name=None):
    return {"id": uid or ("u_" + handle), "email": email or (handle + "@example.com"),
            "password": password, "display_name": name or handle.title(),
            "handle": handle, "balance": balance}


def fixture(users, payments=None, requests=None, currency="EUR", minor_units=2,
            operators=None, **extra):
    fx = {"currency": currency, "minor_units": minor_units, "users": users}
    if payments is not None:
        fx["payments"] = payments
    if requests is not None:
        fx["requests"] = requests
    if operators is not None:
        fx["settlement_operator_ids"] = operators
    fx.update(extra)
    return fx


def reset(fx):
    r = http("POST", "/_test/reset", body=fx)
    expect(r, 204, msg="reset")
    ok(r.raw == b"", "reset must return an empty body")
    return r


class User:
    def __init__(self, uid, handle, token, email=None, password=PASSWORD, display_name=None):
        self.id, self.handle, self.token = uid, handle, token
        self.email, self.password, self.display_name = email, password, display_name

    def get(self, path, **kw):
        return http("GET", path, token=self.token, **kw)

    def post(self, path, body=None, key=None, **kw):
        return http("POST", path, body=body, token=self.token, key=key, **kw)

    def me(self):
        r = expect(self.get("/me"), 200, msg="/me")
        return r.json

    @property
    def balance(self):
        return self.me()["balance"]

    def pay(self, to, amount, key="auto", **extra):
        body = {"to_handle": to, "amount": amount}
        body.update(extra)
        return self.post("/payments", body, key=fresh_key() if key == "auto" else key)

    def request(self, payer, amount, key="auto", **extra):
        body = {"payer_handle": payer, "amount": amount}
        body.update(extra)
        return self.post("/requests", body, key=fresh_key() if key == "auto" else key)

    def pay_request(self, rid, body=None, key="auto"):
        return self.post("/requests/%s/pay" % rid, {} if body is None else body,
                         key=fresh_key() if key == "auto" else key)

    def split(self, amount, handles, key="auto", **extra):
        body = {"amount": amount, "participant_handles": handles}
        body.update(extra)
        return self.post("/splits", body, key=fresh_key() if key == "auto" else key)

    def settle(self, transfers, key="auto", **extra):
        body = {"transfers": transfers}
        body.update(extra)
        return self.post("/settlements", body, key=fresh_key() if key == "auto" else key)

    def activity(self, **q):
        qs = ("?" + urllib.parse.urlencode(q)) if q else ""
        return expect(self.get("/activity" + qs), 200, msg="/activity").json

    def requests(self, **q):
        qs = ("?" + urllib.parse.urlencode(q)) if q else ""
        return expect(self.get("/requests" + qs), 200, msg="/requests").json

    def all_requests(self, **q):
        out, off = [], 0
        while True:
            j = self.requests(limit=200, offset=off, **q)
            out += j["requests"]
            if not j["has_more"]:
                return out
            off += 200

    def all_payments(self):
        out, off = [], 0
        while True:
            j = self.activity(limit=200, offset=off)
            out += j["payments"]
            if not j["has_more"]:
                return out
            off += 200


def login(email, password=PASSWORD, handle=None, uid=None):
    r = expect(http("POST", "/auth/login", body={"email": email, "password": password}), 200, msg="login " + email)
    j = r.json
    return User(j["user_id"], handle, j["token"], email=email, password=password,
                display_name=j.get("display_name"))


def world(balances, operators=(), currency="EUR", minor_units=2, payments=None,
          requests=None, **extra):
    """Reset to a fixture with users named by `balances` (handle -> balance) and log
    everyone in. Returns {handle: User}."""
    users = [fx_user(h, b) for h, b in balances.items()]
    ops = ["u_" + h for h in operators]
    reset(fixture(users, payments=payments, requests=requests, currency=currency,
                  minor_units=minor_units, operators=ops if ops else None, **extra))
    out = {}
    for h in balances:
        out[h] = login(h + "@example.com", handle=h)
        eq(out[h].id, "u_" + h, "login user_id must be the fixture id")
    return out


def signup(email, password=PASSWORD, display_name="New User", handle=None):
    r = http("POST", "/auth/signup", body={"email": email, "password": password,
                                           "display_name": display_name})
    return r


def new_user(email, **kw):
    r = expect(signup(email, **kw), 201, msg="signup " + email)
    j = r.json
    u = User(j["user_id"], None, j["token"], email=email,
             password=kw.get("password", PASSWORD), display_name=j.get("display_name"))
    u.handle = u.me()["handle"]
    return u


def total(users):
    return sum(u.balance for u in users)


# ---------------------------------------------------------------- shape checks
RFC3339 = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?[+-]\d\d:\d\d$")
HANDLE = re.compile(r"^[a-z0-9_]{1,20}$")
PAYMENT_KEYS = {"payment_id", "from_user_id", "from_handle", "to_user_id", "to_handle",
                "amount", "currency", "note", "visibility", "request_id", "created_at"}
REQUEST_KEYS = {"request_id", "requester_id", "requester_handle", "payer_id", "payer_handle",
                "amount", "currency", "note", "status", "payment_id", "created_at"}


def parse_ts(ts):
    ok(isinstance(ts, str) and RFC3339.match(ts),
       "timestamp must be RFC 3339 with a numeric ±HH:MM offset, got %r" % (ts,))
    s = ts
    m = re.match(r"^(.*?)(\.\d+)?([+-]\d\d:\d\d)$", s)
    frac = m.group(2) or ""
    if frac:
        frac = (frac + "000000")[:7]
    dt = datetime.strptime(m.group(1) + frac + m.group(3).replace(":", ""),
                           "%Y-%m-%dT%H:%M:%S" + (".%f" if frac else "") + "%z")
    return dt


def check_ts(ts, max_skew=300):
    dt = parse_ts(ts)
    skew = abs((datetime.now(timezone.utc) - dt).total_seconds())
    ok(skew < max_skew, "timestamp %s is %.0fs away from the probe host's clock" % (ts, skew))
    return dt


def check_payment(p, frm=None, to=None, amount=None, currency=None, note=None,
                  visibility=None, request_id="__any__", settlement="__any__"):
    ok(isinstance(p, dict), "payment must be an object: %r" % (p,))
    missing = PAYMENT_KEYS - set(p)
    ok(not missing, "payment missing keys %s: %r" % (sorted(missing), p))
    ok("settlement_id" in p, "payment must expose settlement_id (null for non-members): %r" % p)
    for k in ("payment_id", "from_user_id", "to_user_id"):
        ok(isinstance(p[k], str) and 0 < len(p[k]) <= 64, "%s must be a 1..64 char string: %r" % (k, p[k]))
    for k in ("from_handle", "to_handle"):
        ok(isinstance(p[k], str) and HANDLE.match(p[k]), "%s invalid: %r" % (k, p[k]))
    ok(is_int(p["amount"]), "amount must be a JSON integer: %r" % (p["amount"],))
    ok(isinstance(p["note"], str), "note must be string")
    ok(p["visibility"] in ("public", "private"), "visibility invalid: %r" % p["visibility"])
    check_ts(p["created_at"])
    if frm is not None:
        eq(p["from_handle"], frm.handle, "from_handle")
        eq(p["from_user_id"], frm.id, "from_user_id")
    if to is not None:
        eq(p["to_handle"], to.handle, "to_handle")
        eq(p["to_user_id"], to.id, "to_user_id")
    if amount is not None:
        eq(p["amount"], amount, "amount")
    if currency is not None:
        eq(p["currency"], currency, "currency")
    if note is not None:
        eq(p["note"], note, "note")
    if visibility is not None:
        eq(p["visibility"], visibility, "visibility")
    if request_id != "__any__":
        eq(p["request_id"], request_id, "request_id")
    if settlement != "__any__":
        eq(p["settlement_id"], settlement, "settlement_id")
    return p


def check_request(r, requester=None, payer=None, amount=None, status=None, note=None,
                  payment_id="__any__"):
    ok(isinstance(r, dict), "request must be an object: %r" % (r,))
    missing = REQUEST_KEYS - set(r)
    ok(not missing, "request missing keys %s: %r" % (sorted(missing), r))
    ok(is_int(r["amount"]), "amount must be integer: %r" % (r["amount"],))
    ok(r["status"] in ("pending", "paid", "declined", "cancelled"), "bad status %r" % r["status"])
    ok("visibility" not in r, "a request carries no visibility of its own: %r" % r)
    for k in ("request_id", "requester_id", "payer_id"):
        ok(isinstance(r[k], str) and 0 < len(r[k]) <= 64, "%s must be a 1..64 char string" % k)
    check_ts(r["created_at"])
    if requester is not None:
        eq(r["requester_handle"], requester.handle, "requester_handle")
        eq(r["requester_id"], requester.id, "requester_id")
    if payer is not None:
        eq(r["payer_handle"], payer.handle, "payer_handle")
        eq(r["payer_id"], payer.id, "payer_id")
    if amount is not None:
        eq(r["amount"], amount, "amount")
    if status is not None:
        eq(r["status"], status, "status")
    if note is not None:
        eq(r["note"], note, "note")
    if payment_id != "__any__":
        eq(r["payment_id"], payment_id, "payment_id")
    return r


def ids_of(items, key):
    return [i[key] for i in items]


def ledger_consistent(users, seed, label=""):
    """Each user's balance must equal seed - sent + received, computed from that user's own
    feed (every payment a user sent or received is in their feed whatever its visibility)."""
    for u in users:
        sent = recv = 0
        seen = set()
        for p in u.all_payments():
            if p["payment_id"] in seen:
                raise AssertionError("%s: duplicate payment %s in %s's feed" % (label, p["payment_id"], u.handle))
            seen.add(p["payment_id"])
            if p["from_user_id"] == u.id:
                sent += p["amount"]
            if p["to_user_id"] == u.id:
                recv += p["amount"]
        eq(u.balance, seed[u.handle] - sent + recv,
           "%s: %s's balance must equal seed - sent + received" % (label, u.handle))
        ok(u.balance >= 0, "%s: negative balance for %s" % (label, u.handle))
