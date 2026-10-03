"""Stage-3 helpers on top of the stage-2 library (lib2) and the stage-1 library (lib).

Includes an independent *model* of the specification's bitemporal rules (`Model`): it is fed only with facts the
specification defines (opening balances, payments, revisions with their recorded/effective instants) and answers
"what is X's balance as of T given what was known at K" and "what is X's statement over [F, T) given K". The probes
compare the service's answers with the model over a grid of instants. It never looks at implementation output other
than the revision records the specification itself exposes (GET /payments/{id}/revisions).
"""
import os
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
S2 = os.path.abspath(os.path.join(HERE, "..", "stage-2"))
S1 = os.path.abspath(os.path.join(HERE, "..", "stage-1"))
for _p in (S1, S2, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import lib  # noqa: E402
import lib2  # noqa: E402
from lib2 import *  # noqa: F401,F403,E402

UTC = timezone.utc
PAY_KEYS = set(lib.PAYMENT_KEYS) | {"settlement_id", "authorization_id"}
REV_KEYS = {"revision", "amount", "effective_at", "recorded_at", "reason"}
CORR_KEYS = {"payment_id", "revision", "amount", "effective_at", "recorded_at", "reason"}
ENTRY_KEYS = {"payment", "delta", "balance_after", "revision", "effective_at", "recorded_at"}


# ---------------------------------------------------------------- time
def utcnow():
    return datetime.now(UTC)


def whole(dt):
    return dt.replace(microsecond=0)


def fmt(dt):
    """RFC 3339 with microseconds and +00:00."""
    return dt.astimezone(UTC).isoformat(timespec="microseconds")


def fmts(dt):
    """RFC 3339, whole seconds, +00:00."""
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def inst(s):
    """Parse a response timestamp (numeric offset required, like every stage)."""
    return lib.parse_ts(s)


def same_instant(a, b, msg=""):
    da, db = (inst(a) if isinstance(a, str) else a), (inst(b) if isinstance(b, str) else b)
    if da != db:
        raise AssertionError("%s: expected the same instant, got %s and %s" % (msg, a, b))


def near(a, b, tol, msg=""):
    da, db = (inst(a) if isinstance(a, str) else a), (inst(b) if isinstance(b, str) else b)
    if abs((da - db).total_seconds()) > tol:
        raise AssertionError("%s: %s and %s differ by more than %ss" % (msg, a, b, tol))


def qs(**kw):
    return urllib.parse.urlencode({k: v for k, v in kw.items() if v is not None})


# ---------------------------------------------------------------- fixtures with history
def fx_pay(pid, frm, to, amount, created_at=None, note="", visibility="public", **extra):
    p = {"id": pid, "from_user_id": "u_" + frm, "to_user_id": "u_" + to, "amount": amount, "note": note,
         "visibility": visibility}
    if created_at is not None:
        p["created_at"] = created_at if isinstance(created_at, str) else fmts(created_at)
    p.update(extra)
    return p


def ending_balances(opening, pays):
    """Ending balance per handle after all seeded payments; raises if the history is inconsistent: the balance at
    every instant (combined effect of all movements at that instant) must be nonnegative."""
    bal = dict(opening)
    by_t = {}
    for p in pays:
        by_t.setdefault(inst(p["created_at"]), []).append(p)
    for t in sorted(by_t):
        for p in by_t[t]:
            bal[p["from_user_id"][2:]] -= p["amount"]
            bal[p["to_user_id"][2:]] += p["amount"]
        for h, v in bal.items():
            if v < 0:
                raise ValueError("inconsistent seeded history: %s is %d at %s" % (h, v, t))
    return bal


def hist_world(opening, pays, operators=(), **extra):
    """Reset to a fixture whose seeded payments have explicit created_at (all of `pays` must). `opening` is the
    balance before anything moved; fixture balances are the ending balances. Returns {handle: User}."""
    end = ending_balances(opening, pays)
    return lib2.world2(end, payments=pays, operators=operators, **extra)


# ---------------------------------------------------------------- API helpers
def me_at(u, as_of=None, known_at=None, **extra):
    q = qs(as_of=as_of, known_at=known_at, **extra)
    r = expect(u.get("/me" + ("?" + q if q else "")), 200, msg="GET /me?" + q)
    j = r.json
    check_me(j)
    if as_of is not None:
        eq(j.get("as_of"), as_of, "/me must echo as_of exactly")
    if known_at is not None:
        eq(j.get("known_at"), known_at, "/me must echo known_at exactly")
    return j


def me_raw(u, **q):
    return u.get("/me?" + qs(**q))


def stmt_raw(u, **q):
    qq = qs(**q)
    return u.get("/statement" + ("?" + qq if qq else ""))


def check_entry(e, u=None):
    ok(isinstance(e, dict), "entry must be an object: %r" % (e,))
    missing = ENTRY_KEYS - set(e)
    ok(not missing, "entry missing %s: %r" % (sorted(missing), e))
    p = e["payment"]
    ok(isinstance(p, dict), "entry.payment must be an object")
    missing = PAY_KEYS - set(p)
    ok(not missing, "entry.payment missing %s: %r" % (sorted(missing), p))
    for k in ("delta", "balance_after", "revision"):
        ok(is_int(e[k]), "entry.%s must be an integer: %r" % (k, e[k]))
    ok(e["revision"] >= 1, "revision is a positive integer")
    ok(is_int(p["amount"]), "payment.amount must be an integer")
    inst(e["effective_at"])
    inst(e["recorded_at"])
    inst(p["created_at"])
    eq(abs(e["delta"]), p["amount"], "|delta| must be the selected payment.amount")
    if u is not None and p["amount"] > 0:
        if p["from_user_id"] == u.id:
            ok(e["delta"] < 0, "a sent payment has a negative delta: %r" % (e,))
        else:
            eq(p["to_user_id"], u.id, "statement entry of a payment the caller is not a party to")
            ok(e["delta"] > 0, "a received payment has a positive delta: %r" % (e,))
    return e


def check_stmt(j, u=None, offset=0, limit=None, snapshot=True):
    ok(isinstance(j, dict), "statement must be an object")
    need = {"opening_balance", "entries", "closing_balance", "has_more"} | ({"snapshot"} if snapshot else set())
    missing = need - set(j)
    ok(not missing, "statement missing %s: %r" % (sorted(missing), list(j)))
    ok(is_int(j["opening_balance"]) and is_int(j["closing_balance"]), "balances must be integers")
    ok(isinstance(j["has_more"], bool), "has_more must be a boolean")
    ok(isinstance(j["entries"], list), "entries must be a list")
    if snapshot:
        ok(isinstance(j["snapshot"], str) and j["snapshot"], "snapshot must be a non-empty string")
    if limit is not None:
        ok(len(j["entries"]) <= limit, "more entries than limit")
    run = None
    for i, e in enumerate(j["entries"]):
        check_entry(e, u)
        if i > 0:
            eq(e["balance_after"], run + e["delta"], "balance_after must be the previous balance_after + delta")
        elif offset == 0:
            eq(e["balance_after"], j["opening_balance"] + e["delta"], "first balance_after = opening_balance + delta")
        run = e["balance_after"]
    return j


def stmt(u, **q):
    r = expect(stmt_raw(u, **q), 200, msg="GET /statement?" + qs(**q))
    j = r.json
    check_stmt(j, u, offset=int(q.get("offset", 0) or 0), limit=int(q["limit"]) if "limit" in q else None,
               snapshot="snapshot" not in q)
    if q.get("known_at") is not None:
        eq(j.get("known_at"), q["known_at"], "statement must echo known_at exactly")
    return j


def full_stmt(u, **q):
    """The whole window, reassembled from pages of a snapshot (so that pagination itself is exercised). Checks that
    opening/closing/balance_after never change between pages, the chain, and opening + sum(delta) == closing."""
    first = stmt(u, limit=200, **q)
    entries = list(first["entries"])
    page = first
    off = 200
    while page["has_more"]:
        page = stmt(u, snapshot=first["snapshot"], limit=200, offset=off)
        eq((page["opening_balance"], page["closing_balance"]), (first["opening_balance"], first["closing_balance"]),
           "balances must not change between pages")
        entries += page["entries"]
        off += 200
    for i, e in enumerate(entries):
        if i == 0:
            eq(e["balance_after"], first["opening_balance"] + e["delta"], "first balance_after")
        else:
            eq(e["balance_after"], entries[i - 1]["balance_after"] + e["delta"], "balance_after chain at %d" % i)
    eq(first["opening_balance"] + sum(e["delta"] for e in entries), first["closing_balance"],
       "opening_balance + sum(delta) must equal closing_balance")
    return {"opening": first["opening_balance"], "closing": first["closing_balance"], "entries": entries,
            "snapshot": first["snapshot"], "raw": first}


def ids_of_entries(entries):
    return [e["payment"]["payment_id"] for e in entries]


def corr_body(rev, amount, eff, reason="correction"):
    return {"expected_revision": rev, "amount": amount, "effective_at": eff, "reason": reason}


def correct(u, pid, rev, amount, eff, reason="correction", key="auto", **extra):
    body = corr_body(rev, amount, eff, reason)
    body.update(extra)
    return u.post("/payments/%s/corrections" % pid, body, key=fresh_key() if key == "auto" else key)


def check_correction(j, pid=None, revision=None, amount=None, eff=None, reason=None):
    ok(isinstance(j, dict), "correction must be an object")
    missing = CORR_KEYS - set(j)
    ok(not missing, "correction response missing %s: %r" % (sorted(missing), j))
    ok(is_int(j["revision"]) and is_int(j["amount"]), "revision/amount must be integers")
    lib.check_ts(j["recorded_at"])
    inst(j["effective_at"])
    if pid is not None:
        eq(j["payment_id"], pid, "payment_id")
    if revision is not None:
        eq(j["revision"], revision, "revision")
    if amount is not None:
        eq(j["amount"], amount, "amount")
    if eff is not None:
        same_instant(j["effective_at"], eff, "effective_at")
    if reason is not None:
        eq(j["reason"], reason, "reason")
    return j


def revisions(u, pid):
    r = expect(u.get("/payments/%s/revisions" % pid), 200, msg="revisions of " + pid)
    j = r.json
    ok(isinstance(j, dict) and isinstance(j.get("revisions"), list), "revisions body must be {\"revisions\": [...]}: %r" % r.text[:200])
    for i, rv in enumerate(j["revisions"]):
        missing = REV_KEYS - set(rv)
        ok(not missing, "revision record missing %s: %r" % (sorted(missing), rv))
        eq(rv["revision"], i + 1, "revisions are in revision order, starting at 1")
        if "payment_id" in rv:
            eq(rv["payment_id"], pid, "revision.payment_id")
        inst(rv["effective_at"])
        inst(rv["recorded_at"])
    return j["revisions"]


def pay_obj(u, pid):
    for p in u.all_payments():
        if p["payment_id"] == pid:
            return p
    raise AssertionError("payment %s not in %s's feed" % (pid, u.handle))


def new_payment(u, to, amount, **extra):
    r = expect(u.pay(to, amount, **extra), 201, msg="pay")
    return r.json


def observe_state(users, pids=()):
    """Everything a failed correction must leave untouched."""
    out = {}
    for h, u in users.items():
        out[h] = {"me": lib2.me_ok(u), "stmt": full_stmt(u)["entries"], "feed": u.all_payments()}
    for owner, pid in pids:
        out["rev:" + pid] = revisions(owner, pid)
    return out


def hold_totals(users):
    return sum(me_at(u)["total"] for u in users.values())


# ---------------------------------------------------------------- the model
class Model:
    """Independent model of the bitemporal ledger.

    opening: {user_id: int}
    pays: {payment_id: {"from": uid, "to": uid, "revs": [(revision, amount, effective_at, recorded_at), ...]}}
    Instants are aware datetimes. Selection and application follow the specification text:
      * the selected revision of a payment is its latest revision recorded at or before known_at (none: no effect);
      * a selected revision counts from its effective_at (inclusive) onward;
      * statement windows are half-open on effective_at; ties are ordered by payment id.
    """
    FUTURE = datetime(9999, 12, 31, tzinfo=UTC)
    PAST = datetime(1, 1, 2, tzinfo=UTC)

    def __init__(self, opening):
        self.opening = dict(opening)
        self.pays = {}

    def add(self, pid, frm, to, revs):
        self.pays[pid] = {"from": frm, "to": to, "revs": list(revs)}

    def add_rev(self, pid, rev):
        self.pays[pid]["revs"].append(rev)

    def selected(self, known_at=None):
        out = {}
        for pid, p in self.pays.items():
            cand = [r for r in p["revs"] if known_at is None or r[3] <= known_at]
            if cand:
                out[pid] = max(cand, key=lambda r: r[0])
        return out

    def balance(self, uid, as_of=None, known_at=None):
        t = self.FUTURE if as_of is None else as_of
        bal = self.opening.get(uid, 0)
        for pid, r in self.selected(known_at).items():
            p = self.pays[pid]
            if r[2] <= t:
                if p["to"] == uid:
                    bal += r[1]
                if p["from"] == uid:
                    bal -= r[1]
        return bal

    def before(self, uid, t, known_at=None):
        """Balance immediately before instant t (movements strictly earlier)."""
        bal = self.opening.get(uid, 0)
        for pid, r in self.selected(known_at).items():
            p = self.pays[pid]
            if r[2] < t:
                if p["to"] == uid:
                    bal += r[1]
                if p["from"] == uid:
                    bal -= r[1]
        return bal

    def statement(self, uid, frm=None, to=None, known_at=None):
        f = self.PAST if frm is None else frm
        t = self.FUTURE if to is None else to
        sel = self.selected(known_at)
        rows = []
        for pid, r in sel.items():
            p = self.pays[pid]
            if uid in (p["from"], p["to"]) and f <= r[2] < t:
                rows.append((r[2], pid, r))
        rows.sort(key=lambda x: (x[0], x[1]))
        opening = self.before(uid, f, known_at)
        bal = opening
        entries = []
        for eff, pid, r in rows:
            p = self.pays[pid]
            d = -r[1] if p["from"] == uid else r[1]
            bal += d
            entries.append({"payment_id": pid, "delta": d, "balance_after": bal, "revision": r[0],
                            "effective_at": r[2], "recorded_at": r[3], "amount": r[1]})
        return {"opening": opening, "entries": entries, "closing": self.before(uid, t, known_at)}

    def min_balance_ok(self, known_at=None):
        """Every user's balance at every effective boundary (combined effect of all movements at an instant)."""
        sel = self.selected(known_at)
        times = sorted({r[2] for r in sel.values()})
        for uid in set(self.opening) | {p["from"] for p in self.pays.values()} | {p["to"] for p in self.pays.values()}:
            if self.opening.get(uid, 0) < 0:
                return False
            for t in times:
                if self.balance(uid, t, known_at) < 0:
                    return False
        return True


def compare_entries(got, want, label):
    eq([e["payment"]["payment_id"] for e in got], [w["payment_id"] for w in want], label + ": entry order / membership")
    for e, w in zip(got, want):
        pid = w["payment_id"]
        eq((e["delta"], e["balance_after"], e["revision"], e["payment"]["amount"]),
           (w["delta"], w["balance_after"], w["revision"], w["amount"]), "%s: entry for %s" % (label, pid))
        same_instant(e["effective_at"], w["effective_at"], "%s: effective_at of %s" % (label, pid))
        same_instant(e["recorded_at"], w["recorded_at"], "%s: recorded_at of %s" % (label, pid))


def model_from_service(opening_by_uid, sources, owners):
    """Build a Model from the revisions endpoint of every payment in `sources` = {pid: (from_uid, to_uid)};
    `owners` = {pid: User who is a party}."""
    m = Model(opening_by_uid)
    for pid, (frm, to) in sources.items():
        revs = [(r["revision"], r["amount"], inst(r["effective_at"]), inst(r["recorded_at"])) for r in revisions(owners[pid], pid)]
        m.add(pid, frm, to, revs)
    return m


def sleep_gap(seconds=1.2):
    time.sleep(seconds)


def model_for(opening, pays):
    """Model of a seeded history: `opening` is {handle: balance before anything moved}, `pays` are fx_pay dicts with
    created_at (revision 1 of each: amount as paid, effective = recorded = created_at)."""
    m = Model({"u_" + h: v for h, v in opening.items()})
    for p in pays:
        t = inst(p["created_at"])
        m.add(p["id"], p["from_user_id"], p["to_user_id"], [(1, p["amount"], t, t)])
    return m


def uid(h):
    return "u_" + h


def grid(times, pad=1):
    """Every instant, one second before and one second after it (sorted, unique)."""
    out = set()
    for t in times:
        out |= {t - timedelta(seconds=pad), t, t + timedelta(seconds=pad)}
    return sorted(out)
