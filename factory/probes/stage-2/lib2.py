"""Stage-2 helpers on top of the stage-1 probe library (imported as `lib`)."""
import os
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
S1 = os.path.abspath(os.path.join(HERE, "..", "stage-1"))
for _p in (S1, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import lib  # noqa: E402
from lib import *  # noqa: F401,F403,E402

AUTH_KEYS = {"authorization_id", "from_user_id", "from_handle", "to_user_id", "to_handle", "amount",
             "captured_amount", "currency", "note", "visibility", "status", "expires_at", "payment_id",
             "created_at", "remaining_amount", "payment_ids"}


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def now_utc():
    return datetime.now(timezone.utc)


def in_(**kw):
    return iso(now_utc() + timedelta(**kw))


def fx_auth(aid, frm, to, amount, status="open", expires_at=None, note="", visibility="public", **extra):
    a = {"id": aid, "from_user_id": "u_" + frm, "to_user_id": "u_" + to, "amount": amount, "note": note,
         "visibility": visibility, "status": status, "expires_at": expires_at or in_(hours=2)}
    a.update(extra)
    return a


def world2(balances, ttl=None, auths=None, operators=(), **kw):
    extra = dict(kw)
    if ttl is not None:
        extra["authorization_ttl_seconds"] = ttl
    if auths is not None:
        extra["authorizations"] = auths
    return world(balances, operators=operators, **extra)


# ---- raw HTTP for HTML responses (lib.http insists on JSON bodies)
def http_raw(method, path, headers=None, body=None, token=None):
    import http.client as hc
    u = urllib.parse.urlsplit(lib.base())
    h = {"Connection": "close"}
    if token:
        h["Authorization"] = "Bearer " + token
    if headers:
        h.update(headers)
    conn = hc.HTTPConnection(u.hostname, u.port or 80, timeout=15)
    try:
        conn.request(method, path, body=body, headers=h)
        r = conn.getresponse()
        data = r.read()
        return r.status, {k.lower(): v for k, v in r.getheaders()}, data
    finally:
        conn.close()


# ---- User helpers (functions, so the stage-1 User class stays untouched)
def authorize(u, to, amount, key="auto", **extra):
    body = {"to_handle": to, "amount": amount}
    body.update(extra)
    return u.post("/authorizations", body, key=fresh_key() if key == "auto" else key)


def capture(u, aid, body=None, key="auto"):
    return u.post("/authorizations/%s/capture" % aid, {} if body is None else body,
                  key=fresh_key() if key == "auto" else key)


def void(u, aid, body=None):
    return u.post("/authorizations/%s/void" % aid, body)


def auths(u, **q):
    qs = ("?" + urllib.parse.urlencode(q)) if q else ""
    r = expect(u.get("/authorizations" + qs), 200, msg="/authorizations")
    j = r.json
    ok(isinstance(j, dict) and set(j) == {"authorizations", "has_more"} and isinstance(j["authorizations"], list)
       and isinstance(j["has_more"], bool),
       "GET /authorizations must return {\"authorizations\": [...], \"has_more\": bool}, got %r" % r.text[:200])
    return j


def all_auths(u, **q):
    out, off = [], 0
    while True:
        j = auths(u, limit=200, offset=off, **q)
        out += j["authorizations"]
        if not j["has_more"]:
            return out
        off += 200


def auth_by_id(u, aid):
    for a in all_auths(u):
        if a["authorization_id"] == aid:
            return a
    raise AssertionError("authorization %s not listed for %s" % (aid, u.handle))


def me_ok(u):
    """GET /me with the stage-2 consistency rules checked."""
    j = expect(u.get("/me"), 200, msg="/me").json
    check_me(j)
    return j


def check_me(j):
    for k in ("user_id", "display_name", "handle", "balance", "total", "available", "held", "currency", "minor_units"):
        ok(k in j, "/me lacks %s: %r" % (k, j))
    for k in ("balance", "total", "available", "held", "minor_units"):
        ok(is_int(j[k]), "/me %s must be an integer: %r" % (k, j[k]))
    eq(j["balance"], j["total"], "balance must equal total")
    eq(j["available"], j["total"] - j["held"], "available must be total - held")
    ok(j["held"] >= 0 and j["available"] >= 0, "held/available must not be negative: %r" % (j,))
    return j


def check_payment2(p, **kw):
    check_payment(p, **kw)
    ok("authorization_id" in p, "payment must expose authorization_id: %r" % p)
    return p


def check_auth(a, frm=None, to=None, amount=None, status=None, captured=None, remaining=None, note=None,
               visibility=None, payment_ids=None):
    ok(isinstance(a, dict), "authorization must be an object")
    missing = AUTH_KEYS - set(a)
    ok(not missing, "authorization missing keys %s: %r" % (sorted(missing), a))
    ok(isinstance(a["authorization_id"], str) and 0 < len(a["authorization_id"]) <= 64, "authorization_id")
    for k in ("amount", "captured_amount", "remaining_amount"):
        ok(is_int(a[k]), "%s must be an integer: %r" % (k, a[k]))
    ok(a["status"] in ("open", "captured", "voided", "expired"), "bad status %r" % a["status"])
    ok(isinstance(a["payment_ids"], list), "payment_ids must be a list")
    check_ts(a["created_at"])
    parse_ts(a["expires_at"])
    if a["status"] == "open":
        eq(a["remaining_amount"], a["amount"] - a["captured_amount"], "remaining_amount of an open authorization")
    else:
        eq(a["remaining_amount"], 0, "remaining_amount of a closed authorization")
    if a["payment_ids"]:
        eq(a["payment_id"], a["payment_ids"][-1], "payment_id is the latest capture")
    else:
        eq(a["payment_id"], None, "payment_id without captures")
        eq(a["captured_amount"], 0, "captured_amount without captures")
    if frm is not None:
        eq((a["from_handle"], a["from_user_id"]), (frm.handle, frm.id), "payer")
    if to is not None:
        eq((a["to_handle"], a["to_user_id"]), (to.handle, to.id), "receiver")
    if amount is not None:
        eq(a["amount"], amount, "amount")
    if status is not None:
        eq(a["status"], status, "status")
    if captured is not None:
        eq(a["captured_amount"], captured, "captured_amount")
    if remaining is not None:
        eq(a["remaining_amount"], remaining, "remaining_amount")
    if note is not None:
        eq(a["note"], note, "note")
    if visibility is not None:
        eq(a["visibility"], visibility, "visibility")
    if payment_ids is not None:
        eq(a["payment_ids"], payment_ids, "payment_ids")
    return a


def seconds_between(a, b):
    return (parse_ts(b) - parse_ts(a)).total_seconds()


def sleep_until(ts, extra=1.3):
    """Sleep until the instant `ts` (RFC 3339) has passed on the probe host's clock (+extra seconds; the
    extra second covers services that truncate timestamps to whole seconds)."""
    dt = parse_ts(ts)
    d = (dt - now_utc()).total_seconds() + extra
    if d > 0:
        time.sleep(d)
