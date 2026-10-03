# Pocketful — stage 4

Node.js HTTP service with no third-party dependencies; all state is held in memory.
It serves the JSON API (historical balances, statements with snapshot paging, payment
corrections, refunds via `POST /payments/{id}/refunds`, operator correction batches via
`POST /correction-batches`) and the browser UI (`/`, `/requests`, `/split`,
`/authorizations`, `/signup`, `/login`) from the same port. All UI assets (script,
stylesheet; system fonts only) are inside the image, so nothing is loaded from the
network at run time.

## Build and start

From this folder (`stage-4/`):

```sh
docker build -t pocketful-stage-4 . && docker run --rm -e PORT=8080 -p 8080:8080 pocketful-stage-4
```

The service listens on `0.0.0.0:$PORT` (default `8080`). `GET /health` returns
`200 {"status": "ok"}` once it accepts requests (well under a second after start).
Seed it with `POST /_test/reset`, then open http://localhost:8080/login in a browser.

## Without Docker

```sh
PORT=8080 node src/server.js
```

Requires Node.js 18 or newer.

## Upgrades from earlier stages

`POST /_test/import` accepts exports of this team's stage-1, stage-2, stage-3 and stage-4
services, keeping accounts, tokens, idempotent receipts, settlement membership, holds,
correction histories and opening balances. Stage-4 exports also carry statement snapshots,
so snapshot tokens keep paging their frozen entries after export → reset → import.

Known gap: the accepted stage-3 service never wrote its statement snapshots into
`GET /_test/export`, so snapshot tokens issued by a stage-3 service cannot be
reconstructed when its export is imported here (those tokens answer 404). Snapshots are
imported whenever an export contains them.
