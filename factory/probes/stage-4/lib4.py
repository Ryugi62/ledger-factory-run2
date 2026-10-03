"""Stage-4 helpers on top of the stage-3 library (lib3), stage-2 library (lib2) and stage-1 library (lib)."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
S3 = os.path.abspath(os.path.join(HERE, "..", "stage-3"))
for _p in (S3, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import lib3  # noqa: E402
from lib3 import *  # noqa: F401,F403,E402

PAY_KEYS4 = set(lib3.PAY_KEYS) | {"refund_of"}
BATCH_KEYS = {"correction_batch_id", "recorded_at", "revisions"}


def refund(u, pid, amount, key="auto", **extra):
    body = {"amount": amount}
    body.update(extra)
    return u.post("/payments/%s/refunds" % pid, body, key=fresh_key() if key == "auto" else key)


def item(pid, rev, amount, eff, reason="batch", **extra):
    d = {"payment_id": pid, "expected_revision": rev, "amount": amount, "effective_at": eff, "reason": reason}
    d.update(extra)
    return d


def batch(u, items, key="auto", **extra):
    body = {"corrections": items}
    body.update(extra)
    return u.post("/correction-batches", body, key=fresh_key() if key == "auto" else key)


def check_payment4(p, refund_of="__any__"):
    missing = PAY_KEYS4 - set(p)
    ok(not missing, "payment missing %s: %r" % (sorted(missing), p))
    if refund_of != "__any__":
        eq(p["refund_of"], refund_of, "refund_of")
    return p


def check_batch(j, n=None, bid=None):
    ok(isinstance(j, dict), "batch response must be an object")
    missing = BATCH_KEYS - set(j)
    ok(not missing, "batch response missing %s: %r" % (sorted(missing), j))
    ok(isinstance(j["correction_batch_id"], str) and 0 < len(j["correction_batch_id"]) <= 64, "correction_batch_id must be a 1..64 char string")
    lib.check_ts(j["recorded_at"])
    ok(isinstance(j["revisions"], list), "revisions must be a list")
    if n is not None:
        eq(len(j["revisions"]), n, "one revision per item, in input order")
    for rv in j["revisions"]:
        missing = lib3.CORR_KEYS - set(rv)
        ok(not missing, "batch revision missing %s: %r" % (sorted(missing), rv))
        eq(rv["correction_batch_id"], j["correction_batch_id"], "each revision exposes correction_batch_id")
        same_instant(rv["recorded_at"], j["recorded_at"], "all revisions share the batch's recorded_at")
    if bid is not None:
        eq(j["correction_batch_id"], bid, "correction_batch_id")
    return j


def feed4(u):
    """The caller's whole feed with the refund_of key checked."""
    out = u.all_payments()
    for p in out:
        check_payment4(p)
    return out


def net_for(users):
    return {h: lib2.me_ok(u) for h, u in users.items()}
