# Pocketful — stage 3

Node.js HTTP service with no third-party dependencies; all state is held in memory.
It serves the JSON API (including historical balances via `GET /me?as_of=&known_at=`,
`GET /statement` with snapshot paging, payment corrections and revision histories) and
the browser UI (`/`, `/requests`, `/split`, `/authorizations`, `/signup`, `/login`) from
the same port. All UI assets (script, stylesheet; system fonts only) are inside the
image, so nothing is loaded from the network at run time.

## Build and start

From this folder (`stage-3/`):

```sh
docker build -t pocketful-stage-3 . && docker run --rm -e PORT=8080 -p 8080:8080 pocketful-stage-3
```

The service listens on `0.0.0.0:$PORT` (default `8080`). `GET /health` returns
`200 {"status": "ok"}` once it accepts requests (well under a second after start).
Seed it with `POST /_test/reset`, then open http://localhost:8080/login in a browser.

## Without Docker

```sh
PORT=8080 node src/server.js
```

Requires Node.js 18 or newer.
