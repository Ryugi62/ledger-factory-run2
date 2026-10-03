"""Headless-browser helpers (Playwright, Chromium) for the stage-2 screen probes."""
import atexit
import contextlib
import os
import re
import time
import urllib.parse
from decimal import Decimal

from lib2 import *  # noqa: F401,F403
import lib

SHOT_DIR = os.environ.get("PROBE_SCREENSHOTS") or None
TIMEOUT = 8.0

_pw = {"p": None, "b": None}


def have_playwright():
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except Exception:  # noqa
        return False


def _browser():
    if _pw["b"] is None:
        if not have_playwright():
            raise Skip("playwright is not installed (pip install playwright && playwright install chromium); UI probes skipped")
        from playwright.sync_api import sync_playwright
        _pw["p"] = sync_playwright().start()
        try:
            _pw["b"] = _pw["p"].chromium.launch(headless=True)
        except Exception as e:  # noqa
            raise Skip("chromium cannot be launched: %s" % str(e)[:150])
        atexit.register(_close)
    return _pw["b"]


def _close():
    try:
        if _pw["b"]:
            _pw["b"].close()
        if _pw["p"]:
            _pw["p"].stop()
    except Exception:  # noqa
        pass


def fmt(amount, minor_units=2, currency="EUR"):
    """The spec's formatted amount: exactly minor_units decimals, one space, currency code; string maths only."""
    s = str(amount)
    if minor_units == 0:
        return "%s %s" % (s, currency)
    s = s.rjust(minor_units + 1, "0")
    return "%s.%s %s" % (s[:-minor_units], s[-minor_units:], currency)


def dec_equal(text, amount, minor_units=2):
    try:
        return Decimal(text.strip()) == Decimal(amount).scaleb(-minor_units)
    except Exception:  # noqa
        return False


class Pg:
    def __init__(self, ctx, page, base):
        self.ctx, self.page, self.base = ctx, page, base
        self.reqs = []
        self.console_errors = []
        self.external = []
        self.failed = []
        page.on("request", self._on_request)
        page.on("console", lambda m: self.console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: self.console_errors.append("pageerror: %s" % e))
        page.on("response", lambda r: self.failed.append((r.status, r.url)) if r.status >= 500 else None)
        self.dialogs = []
        page.on("dialog", lambda d: (self.dialogs.append(d.message), d.dismiss()))

    def _on_request(self, r):
        try:
            post = r.post_data
        except Exception:  # noqa
            post = None
        self.reqs.append({"method": r.method, "url": r.url, "headers": r.headers, "post": post, "type": r.resource_type})

    # -- navigation
    def go(self, path, settle=True):
        self.page.goto(self.base + path, wait_until="domcontentloaded")
        if settle:
            self.settle()

    def settle(self, ms=5000):
        try:
            self.page.wait_for_load_state("networkidle", timeout=ms)
        except Exception:  # noqa
            pass

    @property
    def path(self):
        return urllib.parse.urlsplit(self.page.url).path

    # -- elements
    def tid(self, name):
        return self.page.locator('[data-testid="%s"]' % name)

    def count(self, name):
        return self.tid(name).count()

    def exists(self, name):
        return self.count(name) > 0

    def visible(self, name):
        l = self.tid(name)
        return l.count() > 0 and l.first.is_visible()

    def tc(self, name):
        """Exact textContent."""
        return self.tid(name).first.text_content()

    def it(self, name):
        return self.tid(name).first.inner_text().strip()

    def attr(self, name, a):
        return self.tid(name).first.get_attribute(a)

    def value(self, name):
        return self.tid(name).first.input_value()

    def fill(self, name, v):
        self.tid(name).first.fill(v)

    def type(self, name, v):
        l = self.tid(name).first
        l.fill("")
        l.press_sequentially(v)

    def select(self, name, v):
        self.tid(name).first.select_option(v)

    def click(self, name, timeout=5000):
        self.tid(name).first.click(timeout=timeout)

    def ids(self, prefix):
        """data-testid suffixes of elements whose testid starts with `prefix`, in DOM order."""
        names = self.page.evaluate(
            "p => [...document.querySelectorAll('[data-testid]')].map(e => e.getAttribute('data-testid')).filter(t => t.startsWith(p))",
            prefix)
        return [n[len(prefix):] for n in names]

    def wait(self, cond, msg, timeout=TIMEOUT):
        t0 = time.time()
        last = None
        while time.time() - t0 < timeout:
            try:
                v = cond()
                if v:
                    return v
            except Exception as e:  # noqa
                last = e
            self.page.wait_for_timeout(60)
        raise AssertionError("timed out after %.0fs waiting for: %s%s" % (timeout, msg, (" (last error: %s)" % str(last)[:120]) if last else ""))

    def wait_visible(self, name, timeout=TIMEOUT):
        return self.wait(lambda: self.visible(name), "%s to be visible (page %s)" % (name, self.path), timeout)

    def wait_gone(self, name, timeout=TIMEOUT):
        return self.wait(lambda: not self.visible(name), "%s to disappear" % name, timeout)

    def wait_text(self, name, expected, timeout=TIMEOUT):
        def c():
            return self.exists(name) and self.tc(name) == expected
        try:
            return self.wait(c, "%s text == %r" % (name, expected), timeout)
        except AssertionError:
            got = self.tc(name) if self.exists(name) else "<absent>"
            raise AssertionError("%s text: expected %r, got %r" % (name, expected, got))

    def posts(self, suffix):
        return [r for r in self.reqs if r["method"] == "POST" and urllib.parse.urlsplit(r["url"]).path == suffix]

    def api_calls(self, method=None):
        return [r for r in self.reqs if r["type"] in ("fetch", "xhr") and (method is None or r["method"] == method)]

    def shot(self, name):
        if SHOT_DIR:
            os.makedirs(SHOT_DIR, exist_ok=True)
            self.page.screenshot(path=os.path.join(SHOT_DIR, name + ".png"), full_page=True)


@contextlib.contextmanager
def session(viewport=(1280, 900), block_external=True):
    b = _browser()
    ctx = b.new_context(viewport={"width": viewport[0], "height": viewport[1]}, locale="en-GB")
    ctx.set_default_timeout(8000)
    base = lib.base()
    page = ctx.new_page()
    pg = Pg(ctx, page, base)
    host = urllib.parse.urlsplit(base).netloc

    def ext(route):
        u = urllib.parse.urlsplit(route.request.url)
        if u.scheme in ("http", "https") and u.netloc != host:
            pg.external.append(route.request.url)
            route.abort()
        else:
            route.continue_()

    if block_external:
        ctx.route("**/*", ext)
    try:
        yield pg
    finally:
        try:
            page.unroute_all(behavior="ignoreErrors")
        except Exception:  # noqa
            pass
        ctx.close()


def ui_login(pg, email, password=PASSWORD):
    pg.go("/login")
    pg.wait_visible("login-email")
    pg.fill("login-email", email)
    pg.fill("login-password", password)
    pg.click("login-submit")
    pg.wait(lambda: pg.visible("current-user"), "current-user after login as %s (auth-error: %s)" % (
        email, pg.it("auth-error") if pg.exists("auth-error") else "none"))
    pg.settle()


def ui_logged_in(who, balances, **kw):
    """Reset the service to a fixture (via the stage-1/2 `world`), return (users, context manager value)."""
    users = world2(balances, **kw)
    return users


def goto_home(pg):
    pg.go("/")
    pg.wait_visible("wallet-balance")


def bal(pg):
    return (pg.tc("wallet-balance"), int(pg.attr("wallet-balance", "data-amount")))


def visible_testids(pg):
    return pg.page.evaluate("() => [...document.querySelectorAll('[data-testid]')].filter(e => e.offsetParent !== null || e.getClientRects().length).map(e => e.getAttribute('data-testid'))")


def fill_pay(pg, handle, amount, note="", visibility=None, prefix="pay"):
    pg.fill(prefix + "-handle", handle)
    pg.fill(prefix + "-amount", amount)
    if pg.exists(prefix + "-note"):
        pg.fill(prefix + "-note", note)
    if visibility and pg.exists(prefix + "-visibility"):
        pg.select(prefix + "-visibility", visibility)


def amount_posted(r):
    import json
    return json.loads(r["post"])


def activity_ids(pg):
    return pg.ids("activity-item-")


def request_ids(pg):
    return pg.ids("request-item-")
