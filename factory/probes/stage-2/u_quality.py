"""Browser probes for the product-quality requirements (objective proxies) and cross-screen consistency.

These are heuristics: they catch the unmistakable failures (horizontal scrolling at 375 px, unlabeled inputs, no focus
indicator, low contrast, indistinguishable states, raw ids/timestamps, external assets). Screenshots saved with
--screenshots DIR let a reviewer judge the rest."""
import re

from ui_lib import *  # noqa: F401,F403
from u_wallet import open_as
from u_races import lost_response_scenario, PAY_URL

ROUTES = ["/", "/requests", "/split", "/authorizations"]
LONG = "W" * 150


def rich_world():
    reqs = [{"id": "q_pend", "requester_id": "u_bob", "payer_id": "u_ada", "amount": 1200, "note": "taxi " + LONG, "status": "pending"},
            {"id": "q_paid", "requester_id": "u_bob", "payer_id": "u_ada", "amount": 700, "note": "p", "status": "paid"},
            {"id": "q_decl", "requester_id": "u_bob", "payer_id": "u_ada", "amount": 300, "note": "d", "status": "declined"},
            {"id": "q_canc", "requester_id": "u_bob", "payer_id": "u_ada", "amount": 100, "note": "c", "status": "cancelled"},
            {"id": "q_out", "requester_id": "u_ada", "payer_id": "u_bob", "amount": 900, "note": "o", "status": "pending"}]
    pays = [{"id": "pp_1", "from_user_id": "u_ada", "to_user_id": "u_bob", "amount": 1500, "note": LONG, "visibility": "public"},
            {"id": "pp_2", "from_user_id": "u_bob", "to_user_id": "u_ada", "amount": 250, "note": "private thing", "visibility": "private"}]
    auths = [fx_auth("qa_open", "ada", "bob", 2000, "open", in_(hours=2), note="deposit " + LONG),
             fx_auth("qa_in", "bob", "ada", 1000, "open", in_(hours=2), note="in"),
             fx_auth("qa_cap", "ada", "bob", 700, "captured"), fx_auth("qa_void", "ada", "bob", 800, "voided"),
             fx_auth("qa_exp", "bob", "ada", 900, "expired", in_(hours=-2))]
    users = [fx_user("ada", 100000, name="Alexandria Montgomery-Featherstonehaugh"), fx_user("bob", 50000, name="Bob"),
             fx_user("abcdefghijklmnopqrst", 0, name="Twenty Chars Handle")]
    reset(fixture(users, payments=pays, requests=reqs, authorizations=auths))


JS_SCROLL = """() => ({sw: document.documentElement.scrollWidth, bw: document.body ? document.body.scrollWidth : 0,
  iw: window.innerWidth,
  wide: [...document.querySelectorAll('[data-testid]')].filter(e => ['INPUT','SELECT','BUTTON','TEXTAREA','A'].includes(e.tagName))
        .filter(e => e.getClientRects().length).map(e => ({t: e.getAttribute('data-testid'), l: e.getBoundingClientRect().left, r: e.getBoundingClientRect().right}))
        .filter(x => x.l < -1 || x.r > window.innerWidth + 1)})"""


@test("S2-021", "S2-023", "S2-005", "S2-006", "S2-007", "S2-008", "S2-009")
def test_no_horizontal_scrolling_at_375_768_1280():
    rich_world()
    for width in (375, 768, 1280):
        with session(viewport=(width, 900)) as pg:
            for path in ("/login", "/signup"):
                pg.go(path)
                pg.page.wait_for_timeout(200)
                m = pg.page.evaluate(JS_SCROLL)
                ok(m["sw"] <= m["iw"] + 1 and m["bw"] <= m["iw"] + 1, "%s at %dpx scrolls horizontally: scrollWidth %s > %s" % (path, width, m["sw"], m["iw"]))
                ok(not m["wide"], "%s at %dpx: controls outside the viewport: %r" % (path, width, m["wide"][:3]))
                pg.shot("%s-%d" % (path.strip("/"), width))
            open_as(pg, "ada")
            for path in ROUTES:
                pg.go(path)
                pg.wait(lambda: pg.visible("current-user"), "current-user on " + path)
                pg.page.wait_for_timeout(400)
                m = pg.page.evaluate(JS_SCROLL)
                ok(m["sw"] <= m["iw"] + 1 and m["bw"] <= m["iw"] + 1, "%s at %dpx scrolls horizontally: scrollWidth %s > viewport %s (long notes and names must wrap)" % (
                    path, width, m["sw"], m["iw"]))
                ok(not m["wide"], "%s at %dpx: controls outside the viewport: %r" % (path, width, m["wide"][:3]))
                pg.shot("%s-%d" % (path.strip("/") or "home", width))
            # the required flows remain usable: the pay form can be completed at this width
            pg.go("/")
            pg.wait_visible("pay-submit")
            for t in ("pay-handle", "pay-amount", "pay-note", "pay-visibility", "pay-submit", "wallet-available", "wallet-refresh"):
                pg.tid(t).first.scroll_into_view_if_needed()
                box = pg.tid(t).first.bounding_box()
                ok(box and box["width"] > 20 and box["height"] > 14, "%s has a usable size at %dpx: %r" % (t, width, box))


JS_LABELS = """() => [...document.querySelectorAll('input,select,textarea')].filter(e => e.type !== 'hidden' && e.getClientRects().length)
  .map(e => { const vis = n => n && n.getClientRects().length && (n.textContent || '').trim().length > 0;
              const labs = e.labels ? [...e.labels].filter(vis) : [];
              const by = (e.getAttribute('aria-labelledby') || '').split(/\\s+/).map(id => document.getElementById(id)).filter(vis);
              return {t: e.getAttribute('data-testid') || e.name || e.id || e.tagName, ok: labs.length + by.length > 0}; })"""

JS_PRE = """const focusables = () => [...document.querySelectorAll('a[href],button,input,select,textarea')].filter(e => !e.disabled && e.type !== 'hidden' && e.getClientRects().length);
  const style = e => { const c = getComputedStyle(e); return [c.outlineStyle, c.outlineWidth, c.outlineColor, c.boxShadow, c.borderTopColor, c.borderBottomColor, c.backgroundColor, c.textDecorationLine, c.color].join('|'); };"""
JS_STYLES = "() => { " + JS_PRE + " return focusables().map(style); }"
JS_ACTIVE = "() => { " + JS_PRE + " const f = focusables(), a = document.activeElement; return {k: f.indexOf(a), now: a ? style(a) : '', tag: a ? a.tagName : '', tid: a ? (a.getAttribute('data-testid') || (a.textContent || '').trim().slice(0, 20)) : ''}; }"

JS_CONTRAST = """() => {
  const parse = c => { const m = c.match(/rgba?\\(([^)]+)\\)/); if (!m) return null; const p = m[1].split(/[ ,\\/]+/).filter(Boolean).map(parseFloat); return {r:p[0], g:p[1], b:p[2], a: p.length > 3 ? p[3] : 1}; };
  const over = (f, b) => ({r: f.r*f.a + b.r*(1-f.a), g: f.g*f.a + b.g*(1-f.a), b: f.b*f.a + b.b*(1-f.a), a: 1});
  const lum = c => { const f = v => { v /= 255; return v <= 0.03928 ? v/12.92 : Math.pow((v+0.055)/1.055, 2.4); }; return 0.2126*f(c.r) + 0.7152*f(c.g) + 0.0722*f(c.b); };
  const bg = el => { const layers = []; let e = el;
    while (e) { const cs = getComputedStyle(e); if (cs.backgroundImage && cs.backgroundImage !== 'none') return null;
      const c = parse(cs.backgroundColor); if (c && c.a > 0) { layers.push(c); if (c.a >= 1) break; } e = e.parentElement; }
    let base = {r:255, g:255, b:255, a:1}; for (let i = layers.length - 1; i >= 0; i--) base = over(layers[i], base); return base; };
  const out = [];
  for (const el of document.querySelectorAll('body *')) {
    if (!el.getClientRects().length) continue;
    const own = [...el.childNodes].filter(n => n.nodeType === 3 && n.textContent.trim().length > 0).map(n => n.textContent.trim()).join(' ');
    if (!own) continue;
    if (el.disabled || el.closest('[disabled],[aria-disabled="true"]')) continue;
    const cs = getComputedStyle(el); if (cs.visibility === 'hidden' || parseFloat(cs.opacity) === 0) continue;
    const b = bg(el); if (!b) continue;
    let f = parse(cs.color); if (!f) continue; f = over(f, b);
    const l1 = lum(f), l2 = lum(b); const ratio = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
    const size = parseFloat(cs.fontSize), bold = parseInt(cs.fontWeight) >= 700;
    const need = (size >= 24 || (size >= 18.66 && bold)) ? 3 : 4.5;
    if (ratio + 0.01 < need) out.push({tag: el.tagName, tid: el.getAttribute('data-testid'), text: own.slice(0, 40), ratio: Math.round(ratio*100)/100, need: need, fg: cs.color, bg: `rgb(${Math.round(b.r)},${Math.round(b.g)},${Math.round(b.b)})`});
  }
  return out; }"""


@test("S2-022", "S2-018", "S2-021")
def test_labels_focus_and_contrast():
    rich_world()
    with session(viewport=(1280, 900)) as pg:
        pages = []
        for path in ("/login", "/signup"):
            pg.go(path)
            pg.page.wait_for_timeout(200)
            pages.append(path)
            check_page_a11y(pg, path)
        open_as(pg, "ada")
        for path in ROUTES:
            pg.go(path)
            pg.wait(lambda: pg.visible("current-user"), "current-user on " + path)
            pg.page.wait_for_timeout(500)
            check_page_a11y(pg, path)
        # error and uncertain feedback elements are text too: provoke them and re-check contrast
        pg.go("/")
        pg.wait_visible("pay-submit")
        fill_pay(pg, "nobody", "1.00", "x", "public")
        pg.click("pay-submit")
        pg.wait(lambda: pg.visible("pay-error"), "pay-error")
        bad = pg.page.evaluate(JS_CONTRAST)
        ok(not bad, "low contrast with an error shown on /: %r" % bad[:4])


def check_page_a11y(pg, path):
    labels = pg.page.evaluate(JS_LABELS)
    missing = [x["t"] for x in labels if not x["ok"]]
    ok(not missing, "%s: inputs without a visible associated label (placeholder alone is not a label): %r" % (path, missing))
    # keyboard focus must be apparent for every focusable control
    pg.page.evaluate("() => { document.activeElement && document.activeElement.blur(); window.scrollTo(0, 0); }")
    styles = pg.page.evaluate(JS_STYLES)
    ok(len(styles) > 0, "%s has focusable controls" % path)
    pg.page.keyboard.press("Tab")
    weak, seen = [], set()
    for _ in range(min(len(styles), 45) + 1):
        info = pg.page.evaluate(JS_ACTIVE)
        if info["k"] >= 0 and info["k"] not in seen:
            seen.add(info["k"])
            if info["now"] == styles[info["k"]]:
                weak.append("%s:%s" % (info["tag"], info["tid"]))
        pg.page.keyboard.press("Tab")
    ok(len(seen) >= max(1, len(styles) // 2), "%s: Tab reaches the controls (reached %d of %d)" % (path, len(seen), len(styles)))
    ok(not weak, "%s: keyboard focus is not visibly indicated on: %r" % (path, weak[:6]))
    bad = pg.page.evaluate(JS_CONTRAST)
    ok(not bad, "%s: insufficient text contrast (WCAG AA): %r" % (path, bad[:5]))


JS_SIG = """sel => { const e = document.querySelector(sel); if (!e) return null; const sig = new Set();
  const walk = n => { if (['BUTTON','INPUT','SELECT','TEXTAREA'].includes(n.tagName)) return; const c = getComputedStyle(n);
    sig.add([c.color, c.backgroundColor, c.borderTopColor, c.borderLeftColor, c.borderTopWidth, c.borderLeftWidth, c.opacity, c.textDecorationLine, c.fontWeight, c.fontStyle].join('|'));
    for (const k of n.children) walk(k); };
  walk(e); return [...sig].sort().join('~'); }"""

JS_FONT = """sel => { const e = document.querySelector(sel); if (!e) return null; const c = getComputedStyle(e); return {size: parseFloat(c.fontSize), weight: parseInt(c.fontWeight) || 400, family: c.fontFamily, color: c.color, bg: c.backgroundColor}; }"""

STATUS_WORDS = {"pending": r"pending|awaiting|open|waiting", "paid": r"paid|complete|settled", "declined": r"declin|reject|refus",
                "cancelled": r"cancel|withdrawn", "open": r"open|active|held|pending", "captured": r"captur|collect|paid|complete",
                "voided": r"void|releas|cancel", "expired": r"expire|lapsed|timed"}


@test("S2-016", "S2-017", "S2-018", "S2-019", "S2-155", "S2-156")
def test_states_are_visually_distinct_and_available_is_the_headline():
    rich_world()
    with session(viewport=(1280, 900)) as pg:
        open_as(pg, "ada")
        pg.wait_visible("wallet-held")
        a, b, h = (pg.page.evaluate(JS_FONT, '[data-testid="%s"]' % t) for t in ("wallet-available", "wallet-balance", "wallet-held"))
        ok(a["size"] > b["size"] and a["size"] > h["size"], "wallet-available must be the clearest value: font-size %s vs total %s vs held %s" % (a["size"], b["size"], h["size"]))
        ok(a["weight"] >= b["weight"] and a["weight"] >= h["weight"], "available must not be lighter than total/held: %r %r %r" % (a["weight"], b["weight"], h["weight"]))
        sa, sh = (pg.page.evaluate(JS_SIG, '[data-testid="%s"]' % t) for t in ("wallet-available", "wallet-held"))
        ok(sa != sh or a["size"] != h["size"], "available and held must look different")
        # font family is one system, not the browser default serif
        fam = {}
        for path in ROUTES:
            pg.go(path)
            pg.wait_visible("current-user")
            fam[path] = pg.page.evaluate("() => getComputedStyle(document.body).fontFamily")
        ok(len(set(fam.values())) == 1, "one typography system across routes: %r" % fam)
        first = list(fam.values())[0].split(",")[0].strip().strip('"\'').lower()
        ok(first not in ("times new roman", "times", "serif"), "the browser's default serif font is in use: %r" % list(fam.values())[0])
        # primary vs secondary controls
        pg.go("/")
        pg.wait_visible("pay-submit")
        ps = pg.page.evaluate(JS_FONT, '[data-testid="pay-submit"]')
        m = re.match(r"rgba?\(([^)]+)\)", ps["bg"])
        alpha = float(m.group(1).split(",")[3]) if m and len(m.group(1).split(",")) > 3 else 1.0
        ok(alpha > 0, "the primary action (pay-submit) needs a filled background, got %r" % ps["bg"])
        # request statuses: pending / paid / declined / cancelled are visually different and say so in words
        pg.go("/requests")
        pg.wait(lambda: len(request_ids(pg)) == 5, "five requests")
        sigs, words = {}, {}
        for rid, st in (("q_pend", "pending"), ("q_paid", "paid"), ("q_decl", "declined"), ("q_canc", "cancelled")):
            sigs[st] = pg.page.evaluate(JS_SIG, '[data-testid="request-item-%s"]' % rid)
            words[st] = pg.it("request-item-" + rid)
        for st, txt in words.items():
            ok(re.search(STATUS_WORDS[st], txt, re.I), "the %s request must say so in words, text: %r" % (st, txt[:160]))
        ok(len(set(sigs.values())) == 4, "request statuses must be visually distinct (colour/weight/decoration), signatures: %r" % {k: len(v) for k, v in sigs.items()})
        pg.shot("requests-states-1280")
        # primary / secondary buttons on the same item
        pay = pg.page.evaluate(JS_FONT, '[data-testid="request-pay-q_pend"]')
        dec = pg.page.evaluate(JS_FONT, '[data-testid="request-decline-q_pend"]')
        ok((pay["bg"], pay["color"]) != (dec["bg"], dec["color"]), "pay and decline must be distinguishable (primary vs secondary): %r %r" % (pay, dec))
        # authorization statuses
        pg.go("/authorizations")
        pg.wait(lambda: len(auth_ids_(pg)) == 5, "five authorizations")
        sigs, words = {}, {}
        for aid, st in (("qa_open", "open"), ("qa_cap", "captured"), ("qa_void", "voided"), ("qa_exp", "expired")):
            sigs[st] = pg.page.evaluate(JS_SIG, '[data-testid="authorization-item-%s"]' % aid)
            words[st] = pg.it("authorization-item-" + aid)
        for st, txt in words.items():
            ok(re.search(STATUS_WORDS[st], txt, re.I), "the %s authorization must say so in words, text: %r" % (st, txt[:160]))
        ok(len(set(sigs.values())) == 4, "authorization statuses must be visually distinct: %r" % {k: len(v) for k, v in sigs.items()})
        pg.shot("authorizations-states-1280")
    # error vs uncertain vs success feedback
    world2({"ada": 10000, "bob": 0})
    with session(viewport=(1280, 900)) as pg:
        open_as(pg, "ada")
        pg.wait_visible("pay-submit")
        fill_pay(pg, "nobody", "1.00", "x", "public")
        pg.click("pay-submit")
        pg.wait(lambda: pg.visible("pay-error"), "pay-error")
        err = pg.page.evaluate(JS_SIG, '[data-testid="pay-error"]')
        state, handler = lost_response_scenario(False)
        pg.page.route(PAY_URL, handler)
        fill_pay(pg, "bob", "1.00", "u", "public")
        pg.click("pay-submit")
        pg.wait(lambda: pg.visible("pay-uncertain"), "pay-uncertain")
        unc = pg.page.evaluate(JS_SIG, '[data-testid="pay-uncertain"]')
        ok(err != unc, "an uncertain outcome must not look like a refusal (pay-error vs pay-uncertain share one style)")
        pg.shot("home-uncertain-1280")


def auth_ids_(pg):
    return pg.ids("authorization-item-")


@test("S2-020", "S2-017")
def test_people_first_formatting_no_raw_ids_or_timestamps():
    rich_world()
    bad_text = re.compile(r"\b\d{4}-\d\d-\d\dT\d\d:\d\d|\b(p|pp|u|rq|q|qa|a)_[A-Za-z0-9]+\b|\{\s*\"|undefined|NaN|\[object Object\]|\bnull\b")
    with session(viewport=(1280, 900)) as pg:
        open_as(pg, "ada")
        for path, prefixes in (("/", ["activity-item-"]), ("/requests", ["request-item-"]), ("/authorizations", ["authorization-item-"])):
            pg.go(path)
            pg.wait(lambda: pg.visible("current-user"), "signed in on " + path)
            pg.wait(lambda: all(pg.ids(p) for p in prefixes), "items on " + path)
            for p in prefixes:
                for suffix in pg.ids(p):
                    # authorization-expires is specified as RFC 3339 text; everything else is for people
                    txt = pg.page.evaluate("""([sel]) => { const e = document.querySelector(sel); if (!e) return ''; const c = e.cloneNode(true);
                        c.querySelectorAll('[data-testid^="authorization-expires-"]').forEach(x => x.remove()); return c.innerText; }""", ['[data-testid="%s%s"]' % (p, suffix)])
                    m = bad_text.search(txt)
                    ok(not m, "%s: item text exposes raw data (%r): %r" % (path, m.group(0) if m else "", txt[:200]))
            body = pg.page.evaluate("() => document.body.innerText")
            for token in ("undefined", "NaN", "[object Object]"):
                ok(token not in body, "%s shows %r" % (path, token))
        # no visible JSON dump anywhere on /
        pg.go("/")
        pg.wait_visible("wallet-balance")
        ok('"balance"' not in pg.page.evaluate("() => document.body.innerText"), "raw API JSON visible")


@test("S2-023", "S2-010", "S2-021", "S2-015")
def test_navigation_empty_loading_and_error_states_and_assets():
    rich_world()
    with session(viewport=(1280, 900)) as pg:
        for path, other in (("/login", "/signup"), ("/signup", "/login")):
            pg.go(path)
            hrefs = pg.page.evaluate("() => [...document.querySelectorAll('a[href]')].map(a => new URL(a.href).pathname)")
            ok(other in hrefs, "%s links to %s: %r" % (path, other, hrefs))
            ok(pg.page.title().strip() != "", "%s has a document title" % path)
            ok(pg.page.evaluate("() => !!document.querySelector('meta[name=viewport]')"), "%s has a viewport meta tag" % path)
        open_as(pg, "ada")
        navs = {}
        for path in ROUTES:
            pg.go(path)
            pg.wait(lambda: pg.visible("current-user"), "current-user on " + path)
            navs[path] = sorted(set(pg.page.evaluate("() => [...document.querySelectorAll('a[href]')].map(a => new URL(a.href).pathname)")))
            for target in ROUTES:
                ok(target in navs[path], "%s has no link to %s (navigation must be consistent): %r" % (path, target, navs[path]))
            ok(pg.page.title().strip() != "", "%s has a document title" % path)
            ok(pg.page.evaluate("() => !!document.querySelector('html[lang]')"), "%s: <html lang> missing" % path)
            ok(pg.page.evaluate("() => !!document.querySelector('meta[name=viewport]')"), "%s: viewport meta tag missing" % path)
            ok(pg.page.evaluate("() => !!document.querySelector('h1,h2,[role=heading]')"), "%s has no heading" % path)
        # every route is reachable by clicking links
        pg.go("/")
        for target in ROUTES[1:]:
            pg.page.locator('a[href="%s"]' % target).first.click()
            pg.wait(lambda: pg.path == target, "link navigation to " + target)
            pg.wait(lambda: pg.visible("current-user"), "current-user after navigating to " + target)
        # loading indicator while the data is slow
        def slow(route, request):
            if request.resource_type in ("fetch", "xhr") and request.method == "GET":
                pg.page.wait_for_timeout(1800)
            route.fallback()

        pg.page.route("**/*", slow)
        pg.page.goto(pg.base + "/", wait_until="commit")
        found = False
        for _ in range(14):
            pg.page.wait_for_timeout(100)
            found = pg.page.evaluate("""() => { const q = '[aria-busy="true"],[role="progressbar"],[role="status"],[class*="loading" i],[class*="spinner" i],[class*="skeleton" i],[data-loading]';
                  return [...document.querySelectorAll(q)].some(e => e.getClientRects().length) || /loading|please wait|fetching/i.test(document.body ? document.body.innerText : ''); }""")
            if found:
                break
        ok(found, "no loading indicator while the balance and feed are slow to arrive")
        pg.wait(lambda: pg.visible("wallet-balance") and not pg.visible("pay-error"), "the slow page to finish loading", 15)
        pg.page.wait_for_timeout(400)                         # let every delayed request finish before removing the route
        pg.page.unroute("**/*", slow)
        # error state when a data request fails
        def broken(route, request):
            if request.resource_type in ("fetch", "xhr") and request.method == "GET" and request.url.split("?")[0].endswith("/activity"):
                route.fulfill(status=500, content_type="application/json; charset=utf-8",
                              body='{"error":{"code":"internal","message":"boom"}}')
            else:
                route.fallback()

        pg.page.route("**/*", broken)
        pg.go("/", settle=False)
        pg.wait_visible("wallet-balance")
        pg.wait(lambda: pg.page.evaluate("""() => [...document.querySelectorAll('[role=alert],[class*="error" i],[data-testid*="error" i]')].some(e => e.getClientRects().length && e.innerText.trim().length > 0)
              || /error|couldn.t|could not|failed|unable|try again|something went wrong/i.test(document.body.innerText)"""),
                "a visible error message when the feed cannot be loaded", 6)
        pg.page.unroute("**/*", broken)
    # assets: nothing is fetched from outside the service; no console errors; no 5xx from the app
    with session(viewport=(375, 800)) as pg:
        for path in ("/login", "/signup"):
            pg.go(path)
        open_as(pg, "ada")
        for path in ROUTES:
            pg.go(path)
            pg.wait_visible("current-user")
            pg.page.wait_for_timeout(300)
        eq(pg.external, [], "the UI must not load anything from outside the service (fonts, scripts, styles, images)")
        ok(not pg.console_errors, "console errors: %r" % pg.console_errors[:4])
        ok(not pg.failed, "5xx responses while browsing: %r" % pg.failed[:3])
