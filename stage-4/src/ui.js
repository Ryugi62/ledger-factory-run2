'use strict';
// Browser UI: one HTML shell for every screen plus its script and stylesheet.
// Everything is read from the image at start-up; nothing is fetched from elsewhere.

const fs = require('fs');
const path = require('path');

const dir = path.join(__dirname, 'public');
const ASSETS = {
  '/assets/app.js': { type: 'text/javascript; charset=utf-8', file: 'app.js' },
  '/assets/app.css': { type: 'text/css; charset=utf-8', file: 'app.css' },
};
for (const a of Object.values(ASSETS)) a.body = fs.readFileSync(path.join(dir, a.file), 'utf8');

const PAGE = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pocketful</title>
<link rel="icon" href="data:,">
<link rel="stylesheet" href="/assets/app.css">
</head>
<body>
<div id="app"><p class="boot loading" role="status" aria-busy="true">Loading…</p></div>
<script src="/assets/app.js"></script>
</body>
</html>
`;

module.exports = {
  page: () => PAGE,
  asset: (p) => ASSETS[p] || null,
};
