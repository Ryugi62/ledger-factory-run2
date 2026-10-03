"""Accept-header negotiation: the browser and the API share /requests and /authorizations."""
from lib2 import *  # noqa: F401,F403

BROWSER = "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"


def is_html(status, headers, body):
    ct = headers.get("content-type", "").lower()
    return status == 200 and ct.startswith("text/html") and b"<" in body and b"html" in body.lower()


@test("S2-012", "S2-169", "S2-005", "S2-006", "S2-007", "S2-008", "S2-009")
def test_html_for_text_html_json_otherwise():
    w = world2({"ada": 1000, "bob": 0})
    ada, bob = w["ada"], w["bob"]
    expect(bob.request("ada", 5), 201)
    expect(authorize(ada, "bob", 5), 201)
    for path in ("/requests", "/authorizations"):
        for accept in ("text/html", BROWSER, "text/html;q=0.9, application/json;q=0.1"):
            st, h, body = http_raw("GET", path, headers={"Accept": accept})
            ok(is_html(st, h, body), "GET %s Accept %r without a token must return the HTML UI, got %s %s %r" % (
                path, accept[:30], st, h.get("content-type"), body[:80]))
            ok("charset=utf-8" in h.get("content-type", "").lower().replace(" ", ""), "HTML must declare utf-8: %r" % h.get("content-type"))
            st, h, body = http_raw("GET", path, headers={"Accept": accept}, token=ada.token)
            ok(is_html(st, h, body), "HTML also with a token: %s %s" % (st, h.get("content-type")))
        # JSON for everything else
        for accept in (None, "application/json", "*/*", "application/json, text/plain, */*", "text/plain"):
            hd = {"Accept": accept} if accept else {}
            st, h, body = http_raw("GET", path, headers=hd)
            ok(st == 401 and "application/json" in h.get("content-type", ""), "GET %s Accept %r no token must be a JSON 401, got %s %s" % (path, accept, st, h.get("content-type")))
            ok(b'"unauthenticated"' in body, "401 body: %r" % body[:100])
            st, h, body = http_raw("GET", path, headers=hd, token=ada.token)
            ok(st == 200 and h.get("content-type", "").lower().startswith("application/json"),
               "GET %s Accept %r with token must be JSON 200, got %s %s" % (path, accept, st, h.get("content-type")))
    # API payload still correct after all that
    eq(len(ada.all_requests()), 1, "requests json")
    eq(len(all_auths(ada)), 1, "authorizations json")
    for path in ("/", "/split", "/login", "/signup"):
        st, h, body = http_raw("GET", path, headers={"Accept": BROWSER})
        ok(is_html(st, h, body), "GET %s must serve the UI for a browser: %s %s" % (path, st, h.get("content-type")))
    # the write side of /requests is unaffected by the UI
    r = expect(bob.request("ada", 7), 201)
    ok("request_id" in r.json, "POST /requests is the API")
    st, h, body = http_raw("POST", "/requests", headers={"Accept": "application/json", "Content-Type": "application/json",
                                                       "Idempotency-Key": fresh_key()}, body=b'{"payer_handle":"ada","amount":1}', token=bob.token)
    eq(st, 201, "POST /requests with Accept: application/json")
