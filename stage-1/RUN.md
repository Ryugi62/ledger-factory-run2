# Pocketful — stage 1

Node.js HTTP service with no third-party dependencies; all state is held in memory.

## Build and start

From this folder (`stage-1/`):

```sh
docker build -t pocketful-stage-1 . && docker run --rm -e PORT=8080 -p 8080:8080 pocketful-stage-1
```

The service listens on `0.0.0.0:$PORT` (default `8080`) and needs no network access at run
time. `GET /health` returns `200 {"status": "ok"}` once it accepts requests (well under a
second after start). Seed it with `POST /_test/reset`.

## Without Docker

```sh
PORT=8080 node src/server.js
```

Requires Node.js 18 or newer.
