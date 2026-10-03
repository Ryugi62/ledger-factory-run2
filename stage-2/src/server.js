'use strict';
// Pocketful stage 1: wallet, payments, requests, splits and settlements over HTTP.
// All state lives in memory. Every state-changing step runs synchronously on the
// event loop, so each operation is atomic with respect to every other request.

const http = require('http');
const crypto = require('crypto');

const MAX_AMOUNT = 1000000000;
const MAX_ABS_BALANCE = 2 ** 53;
const MAX_NOTE = 200;
const MAX_BODY = 8 * 1024 * 1024;
const HANDLE_RE = /^[a-z0-9_]{1,20}$/;
const DIGITS_RE = /^[0-9]+$/;
const REQUEST_STATUSES = ['pending', 'paid', 'declined', 'cancelled'];

// ---------------------------------------------------------------- errors

class ApiError extends Error {
  constructor(status, code, message) {
    super(message || code);
    this.status = status;
    this.code = code;
  }
}

const malformed = (m) => new ApiError(400, 'malformed_request', m || 'request body is malformed');
const invalid = (m) => new ApiError(422, 'validation_failed', m || 'validation failed');
const notFound = (m) => new ApiError(404, 'not_found', m || 'not found');
const forbidden = (m) => new ApiError(403, 'forbidden', m || 'forbidden');

// ---------------------------------------------------------------- helpers

function nowStamp() {
  // RFC 3339, second precision, explicit numeric offset.
  return new Date().toISOString().slice(0, 19) + '+00:00';
}

function codePoints(s) {
  let n = 0;
  for (const _ of s) n++; // eslint-disable-line no-unused-vars
  return n;
}

function isObject(v) {
  return v !== null && typeof v === 'object' && !Array.isArray(v);
}

function has(o, k) {
  return Object.prototype.hasOwnProperty.call(o, k);
}

// Canonical JSON: object keys sorted, so equal JSON values give equal strings.
function canonical(v) {
  if (Array.isArray(v)) return '[' + v.map(canonical).join(',') + ']';
  if (isObject(v)) {
    const keys = Object.keys(v).sort();
    return '{' + keys.map((k) => JSON.stringify(k) + ':' + canonical(v[k])).join(',') + '}';
  }
  return JSON.stringify(v);
}

function deepCopy(v) {
  return JSON.parse(JSON.stringify(v));
}

function isWholeAmount(v) {
  return typeof v === 'number' && Number.isInteger(v) && v >= 1 && v <= MAX_AMOUNT;
}

function isExactInt(v) {
  return typeof v === 'number' && Number.isInteger(v) && Math.abs(v) <= MAX_ABS_BALANCE;
}

// Payment-style field rules shared by payments, requests, splits and settlement entries.
function checkAmount(body) {
  if (!has(body, 'amount')) throw invalid('amount is required');
  if (!isWholeAmount(body.amount)) throw invalid('amount must be an integer from 1 to ' + MAX_AMOUNT);
  return body.amount;
}

function checkNote(body) {
  if (!has(body, 'note')) return '';
  const note = body.note;
  if (typeof note !== 'string') throw invalid('note must be a string');
  if (codePoints(note) > MAX_NOTE) throw invalid('note is longer than ' + MAX_NOTE + ' characters');
  return note;
}

function checkVisibility(body) {
  if (!has(body, 'visibility')) return 'public';
  const v = body.visibility;
  if (v !== 'public' && v !== 'private') throw invalid('visibility must be public or private');
  return v;
}

function requireString(body, field) {
  if (!has(body, field)) throw invalid(field + ' is required');
  if (typeof body[field] !== 'string') throw malformed(field + ' must be a string');
  return body[field];
}

function deriveHandle(email) {
  const local = email.slice(0, email.lastIndexOf('@'));
  let out = '';
  for (const ch of local.toLowerCase()) out += /^[a-z0-9_]$/.test(ch) ? ch : '_';
  return Array.from(out).slice(0, 20).join('');
}

function validEmail(email) {
  const at = email.indexOf('@');
  if (at <= 0 || at !== email.lastIndexOf('@') || at === email.length - 1) return false;
  return !/[\s\u0000-\u001f\u007f]/.test(email);
}

const emailKey = (email) => email.toLowerCase();

// ---------------------------------------------------------------- passwords

const SCRYPT = { N: 8192, r: 8, p: 1, keylen: 32 };

function scryptAsync(password, salt, params) {
  return new Promise((resolve, reject) => {
    crypto.scrypt(password, salt, params.keylen,
      { N: params.N, r: params.r, p: params.p, maxmem: 64 * 1024 * 1024 },
      (err, key) => (err ? reject(err) : resolve(key)));
  });
}

async function hashPassword(password) {
  const salt = crypto.randomBytes(16);
  const key = await scryptAsync(password, salt, SCRYPT);
  return ['scrypt', SCRYPT.N, SCRYPT.r, SCRYPT.p, salt.toString('base64'), key.toString('base64')].join('$');
}

function parseHash(stored) {
  const parts = typeof stored === 'string' ? stored.split('$') : [];
  if (parts.length !== 6 || parts[0] !== 'scrypt') return null;
  const [N, r, p] = parts.slice(1, 4).map(Number);
  if (![N, r, p].every(Number.isInteger) || N < 2 || (N & (N - 1)) !== 0 || N > 1 << 20 || r < 1 || r > 32 || p < 1 || p > 16) return null;
  const salt = Buffer.from(parts[4], 'base64');
  const key = Buffer.from(parts[5], 'base64');
  if (!salt.length || !key.length) return null;
  return { N, r, p, salt, key };
}

async function verifyPassword(password, stored) {
  const h = parseHash(stored);
  if (!h) return false;
  const key = await scryptAsync(password, h.salt, { N: h.N, r: h.r, p: h.p, keylen: h.key.length });
  return crypto.timingSafeEqual(key, h.key);
}

// ---------------------------------------------------------------- state

function emptyState() {
  return {
    currency: 'EUR',
    minor_units: 2,
    users: new Map(),      // id -> user
    byEmail: new Map(),    // lowercased email -> id
    byHandle: new Map(),   // handle -> id
    tokens: new Map(),     // token -> user id
    payments: [],          // creation order
    paymentsById: new Map(),
    requests: [],          // creation order
    requestsById: new Map(),
    splits: [],
    splitIds: new Set(),
    settlements: [],
    settlementIds: new Set(),
    idem: new Map(),       // user \n path \n key -> { hash, status, body }
    seq: 0,
  };
}

let state = emptyState();

function nextId(prefix, taken) {
  let id;
  do {
    state.seq += 1;
    id = prefix + state.seq;
  } while (taken(id));
  return id;
}

function addPayment(st, p) {
  st.payments.push(p);
  st.paymentsById.set(p.id, p);
}

function addRequest(st, r) {
  st.requests.push(r);
  st.requestsById.set(r.id, r);
}

function paymentView(p) {
  const from = state.users.get(p.from);
  const to = state.users.get(p.to);
  return {
    payment_id: p.id,
    from_user_id: p.from,
    from_handle: from.handle,
    to_user_id: p.to,
    to_handle: to.handle,
    amount: p.amount,
    currency: state.currency,
    note: p.note,
    visibility: p.visibility,
    request_id: p.request_id,
    settlement_id: p.settlement_id,
    created_at: p.created_at,
  };
}

function requestView(r) {
  const requester = state.users.get(r.requester);
  const payer = state.users.get(r.payer);
  return {
    request_id: r.id,
    requester_id: r.requester,
    requester_handle: requester.handle,
    payer_id: r.payer,
    payer_handle: payer.handle,
    amount: r.amount,
    currency: state.currency,
    note: r.note,
    status: r.status,
    payment_id: r.payment_id,
    created_at: r.created_at,
  };
}

function issueToken(userId) {
  const token = crypto.randomBytes(24).toString('base64url');
  state.tokens.set(token, userId);
  return token;
}

// ---------------------------------------------------------------- reset fixture

async function buildFromFixture(fx) {
  if (!isObject(fx)) throw malformed('fixture must be a JSON object');
  if (typeof fx.currency !== 'string' || !fx.currency) throw invalid('currency is required');
  if (![0, 2, 3].includes(fx.minor_units)) throw invalid('minor_units must be 0, 2 or 3');
  if (!Array.isArray(fx.users)) throw invalid('users must be an array');
  const st = emptyState();
  st.currency = fx.currency;
  st.minor_units = fx.minor_units;
  const pending = [];
  for (const u of fx.users) {
    if (!isObject(u)) throw invalid('user must be an object');
    for (const f of ['id', 'email', 'password', 'display_name', 'handle']) {
      if (typeof u[f] !== 'string') throw invalid('user.' + f + ' must be a string');
    }
    if (!u.id || u.id.length > 64) throw invalid('user.id must be 1..64 characters');
    if (!HANDLE_RE.test(u.handle)) throw invalid('user.handle is invalid');
    if (!isExactInt(u.balance)) throw invalid('user.balance must be an integer');
    if (u.balance < 0) throw invalid('user.balance must not be negative');
    if (st.users.has(u.id)) throw invalid('duplicate user id');
    if (st.byHandle.has(u.handle)) throw invalid('duplicate handle');
    if (st.byEmail.has(emailKey(u.email))) throw invalid('duplicate email');
    const user = {
      id: u.id, email: u.email, hash: null, display_name: u.display_name,
      handle: u.handle, balance: u.balance, operator: false,
    };
    st.users.set(user.id, user);
    st.byEmail.set(emailKey(user.email), user.id);
    st.byHandle.set(user.handle, user.id);
    pending.push([user, u.password]);
  }
  const stamp = nowStamp();
  const payments = has(fx, 'payments') && fx.payments !== null ? fx.payments : [];
  if (!Array.isArray(payments)) throw invalid('payments must be an array');
  for (const p of payments) {
    if (!isObject(p)) throw invalid('payment must be an object');
    if (typeof p.id !== 'string' || !p.id || p.id.length > 64) throw invalid('payment.id is invalid');
    if (st.paymentsById.has(p.id)) throw invalid('duplicate payment id');
    if (!st.users.has(p.from_user_id) || !st.users.has(p.to_user_id)) throw invalid('payment user unknown');
    if (!isExactInt(p.amount) || p.amount < 1) throw invalid('payment.amount is invalid');
    addPayment(st, {
      id: p.id, from: p.from_user_id, to: p.to_user_id, amount: p.amount,
      note: checkNote(p), visibility: checkVisibility(p),
      request_id: null, settlement_id: null, created_at: stamp,
    });
  }
  const requests = has(fx, 'requests') && fx.requests !== null ? fx.requests : [];
  if (!Array.isArray(requests)) throw invalid('requests must be an array');
  for (const r of requests) {
    if (!isObject(r)) throw invalid('request must be an object');
    if (typeof r.id !== 'string' || !r.id || r.id.length > 64) throw invalid('request.id is invalid');
    if (st.requestsById.has(r.id)) throw invalid('duplicate request id');
    if (!st.users.has(r.requester_id) || !st.users.has(r.payer_id)) throw invalid('request user unknown');
    if (!isExactInt(r.amount) || r.amount < 0) throw invalid('request.amount is invalid');
    const status = has(r, 'status') ? r.status : 'pending';
    if (!REQUEST_STATUSES.includes(status)) throw invalid('request.status is invalid');
    let paymentId = null;
    if (has(r, 'payment_id') && r.payment_id !== null) {
      if (typeof r.payment_id !== 'string') throw invalid('request.payment_id is invalid');
      paymentId = r.payment_id;
    }
    addRequest(st, {
      id: r.id, requester: r.requester_id, payer: r.payer_id, amount: r.amount,
      note: checkNote(r), status, payment_id: paymentId, created_at: stamp,
    });
  }
  const ops = has(fx, 'settlement_operator_ids') && fx.settlement_operator_ids !== null ? fx.settlement_operator_ids : [];
  if (!Array.isArray(ops) || !ops.every((x) => typeof x === 'string')) {
    throw invalid('settlement_operator_ids must be an array of user ids');
  }
  for (const id of ops) {
    const u = st.users.get(id);
    if (u) u.operator = true;
  }
  const hashes = await Promise.all(pending.map(([, pw]) => hashPassword(pw)));
  pending.forEach(([user], i) => { user.hash = hashes[i]; });
  return st;
}

// ---------------------------------------------------------------- export / import

function exportState() {
  const users = [];
  for (const u of state.users.values()) {
    users.push({ id: u.id, email: u.email, hash: u.hash, display_name: u.display_name,
      handle: u.handle, balance: u.balance, operator: u.operator });
  }
  const tokens = [];
  for (const [t, uid] of state.tokens) tokens.push([t, uid]);
  const idem = [];
  for (const [k, v] of state.idem) idem.push({ scope: k, hash: v.hash, status: v.status, body: v.body });
  return {
    track: 'pocketful',
    format_version: 1,
    state: {
      service: 'pocketful-stage-1',
      currency: state.currency,
      minor_units: state.minor_units,
      users,
      tokens,
      payments: state.payments.map((p) => ({ ...p })),
      requests: state.requests.map((r) => ({ ...r })),
      splits: deepCopy(state.splits),
      settlements: deepCopy(state.settlements),
      idempotency: deepCopy(idem),
      seq: state.seq,
    },
  };
}

function importState(doc) {
  if (!isObject(doc)) throw invalid('import body must be an export object');
  if (doc.track !== 'pocketful') throw invalid('track must be pocketful');
  if (doc.format_version !== 1) throw invalid('format_version must be 1');
  const s = doc.state;
  const bad = (m) => invalid('invalid state: ' + m);
  if (!isObject(s) || s.service !== 'pocketful-stage-1') throw bad('not a state of this service');
  if (typeof s.currency !== 'string' || ![0, 2, 3].includes(s.minor_units)) throw bad('currency');
  for (const f of ['users', 'tokens', 'payments', 'requests', 'splits', 'settlements', 'idempotency']) {
    if (!Array.isArray(s[f])) throw bad(f);
  }
  if (!Number.isSafeInteger(s.seq) || s.seq < 0) throw bad('seq');
  const st = emptyState();
  st.currency = s.currency;
  st.minor_units = s.minor_units;
  st.seq = s.seq;
  const str = (v) => typeof v === 'string';
  const optStr = (v) => v === null || typeof v === 'string';
  for (const u of s.users) {
    if (!isObject(u) || !str(u.id) || !str(u.email) || !str(u.display_name) || !str(u.handle)
      || !HANDLE_RE.test(u.handle) || !isExactInt(u.balance) || u.balance < 0
      || typeof u.operator !== 'boolean' || !parseHash(u.hash)) throw bad('user');
    if (st.users.has(u.id) || st.byHandle.has(u.handle) || st.byEmail.has(emailKey(u.email))) throw bad('duplicate user');
    st.users.set(u.id, { id: u.id, email: u.email, hash: u.hash, display_name: u.display_name,
      handle: u.handle, balance: u.balance, operator: u.operator });
    st.byEmail.set(emailKey(u.email), u.id);
    st.byHandle.set(u.handle, u.id);
  }
  for (const t of s.tokens) {
    if (!Array.isArray(t) || t.length !== 2 || !str(t[0]) || !st.users.has(t[1])) throw bad('token');
    st.tokens.set(t[0], t[1]);
  }
  for (const p of s.payments) {
    if (!isObject(p) || !str(p.id) || st.paymentsById.has(p.id) || !st.users.has(p.from) || !st.users.has(p.to)
      || !isExactInt(p.amount) || !str(p.note) || !['public', 'private'].includes(p.visibility)
      || !optStr(p.request_id) || !optStr(p.settlement_id) || !str(p.created_at)) throw bad('payment');
    addPayment(st, { id: p.id, from: p.from, to: p.to, amount: p.amount, note: p.note,
      visibility: p.visibility, request_id: p.request_id, settlement_id: p.settlement_id, created_at: p.created_at });
  }
  for (const r of s.requests) {
    if (!isObject(r) || !str(r.id) || st.requestsById.has(r.id) || !st.users.has(r.requester) || !st.users.has(r.payer)
      || !isExactInt(r.amount) || !str(r.note) || !REQUEST_STATUSES.includes(r.status)
      || !optStr(r.payment_id) || !str(r.created_at)) throw bad('request');
    addRequest(st, { id: r.id, requester: r.requester, payer: r.payer, amount: r.amount, note: r.note,
      status: r.status, payment_id: r.payment_id, created_at: r.created_at });
  }
  for (const sp of s.splits) {
    if (!isObject(sp) || !str(sp.id) || st.splitIds.has(sp.id)) throw bad('split');
    st.splits.push(sp);
    st.splitIds.add(sp.id);
  }
  for (const se of s.settlements) {
    if (!isObject(se) || !str(se.id) || st.settlementIds.has(se.id)) throw bad('settlement');
    st.settlements.push(se);
    st.settlementIds.add(se.id);
  }
  for (const e of s.idempotency) {
    if (!isObject(e) || !str(e.scope) || !str(e.hash) || ![201].includes(e.status) || !isObject(e.body)) throw bad('idempotency');
    st.idem.set(e.scope, { hash: e.hash, status: e.status, body: e.body });
  }
  return st;
}

// ---------------------------------------------------------------- HTTP plumbing

function send(res, status, body) {
  if (status === 204) {
    res.writeHead(204);
    res.end();
    return;
  }
  const data = Buffer.from(JSON.stringify(body), 'utf8');
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': data.length,
  });
  res.end(data);
}

function sendError(res, err) {
  send(res, err.status, { error: { code: err.code, message: err.message || err.code } });
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    let tooBig = false;
    req.on('data', (c) => {
      if (tooBig) return;
      size += c.length;
      if (size > MAX_BODY) {
        tooBig = true;
        chunks.length = 0;
        return;
      }
      chunks.push(c);
    });
    req.on('end', () => {
      if (tooBig) reject(new ApiError(413, 'payload_too_large', 'request body is too large'));
      else resolve(Buffer.concat(chunks).toString('utf8'));
    });
    req.on('error', reject);
  });
}

function parseJson(raw) {
  if (raw.trim() === '') throw malformed('request body is empty');
  try {
    return JSON.parse(raw);
  } catch (e) {
    throw malformed('request body is not valid JSON');
  }
}

function parseObject(raw) {
  const v = parseJson(raw);
  if (!isObject(v)) throw malformed('request body must be a JSON object');
  return v;
}

function authenticate(req) {
  const h = req.headers.authorization;
  const m = typeof h === 'string' ? /^Bearer +(\S+) *$/i.exec(h) : null;
  const uid = m ? state.tokens.get(m[1]) : undefined;
  const user = uid !== undefined ? state.users.get(uid) : undefined;
  if (!user) throw new ApiError(401, 'unauthenticated', 'missing or invalid bearer token');
  return user;
}

function idempotencyKey(req) {
  const k = req.headers['idempotency-key'];
  if (typeof k !== 'string' || k === '') {
    throw new ApiError(400, 'missing_idempotency_key', 'Idempotency-Key header is required');
  }
  if (k.length > 255) throw invalid('Idempotency-Key must be 1 to 255 characters');
  return k;
}

// Runs an idempotent write. `run(body)` must be synchronous and return the 201 body.
function idempotent(req, user, path, raw, run) {
  const key = idempotencyKey(req);
  const body = parseObject(raw);
  let hash;
  try {
    hash = canonical(body);
  } catch (e) {
    throw malformed('request body is too deeply nested');
  }
  const scope = user.id + '\n' + path + '\n' + key;
  const prior = state.idem.get(scope);
  if (prior) {
    if (prior.hash !== hash) throw new ApiError(409, 'idempotency_key_reuse', 'Idempotency-Key was used with a different request body');
    return [200, prior.body];
  }
  const result = run(body);
  state.idem.set(scope, { hash, status: 201, body: deepCopy(result) });
  return [201, result];
}

function pageParams(url) {
  const q = url.searchParams;
  let limit = 50;
  let offset = 0;
  if (q.has('limit')) {
    const v = q.get('limit');
    if (!DIGITS_RE.test(v)) throw invalid('limit must be an integer from 1 to 200');
    limit = Number(v);
    if (limit < 1 || limit > 200) throw invalid('limit must be an integer from 1 to 200');
  }
  if (q.has('offset')) {
    const v = q.get('offset');
    if (!DIGITS_RE.test(v)) throw invalid('offset must be an integer of 0 or more');
    offset = Number(v);
  }
  return { limit, offset };
}

// Items are in creation order; returns newest first.
function pageNewestFirst(items, limit, offset) {
  const out = [];
  let skipped = 0;
  let hasMore = false;
  for (let i = items.length - 1; i >= 0; i--) {
    if (skipped < offset) { skipped++; continue; }
    if (out.length === limit) { hasMore = true; break; }
    out.push(items[i]);
  }
  return { out, hasMore };
}

function userByHandle(handle) {
  const id = state.byHandle.get(handle);
  return id === undefined ? null : state.users.get(id);
}

// ---------------------------------------------------------------- operations

function createPayment(from, to, amount, note, visibility, requestId, settlementId, createdAt) {
  const p = {
    id: nextId('pay_', (id) => state.paymentsById.has(id)),
    from: from.id, to: to.id, amount, note, visibility,
    request_id: requestId, settlement_id: settlementId, created_at: createdAt || nowStamp(),
  };
  from.balance -= amount;
  to.balance += amount;
  addPayment(state, p);
  return p;
}

function opPayment(user, body) {
  const handle = requireString(body, 'to_handle');
  const amount = checkAmount(body);
  const note = checkNote(body);
  const visibility = checkVisibility(body);
  const to = userByHandle(handle);
  if (!to) throw notFound('no user has that handle');
  if (to.id === user.id) throw new ApiError(422, 'self_payment', 'cannot pay yourself');
  if (user.balance < amount) throw new ApiError(409, 'insufficient_funds', 'balance is too low');
  return paymentView(createPayment(user, to, amount, note, visibility, null, null));
}

function opRequest(user, body) {
  const handle = requireString(body, 'payer_handle');
  const amount = checkAmount(body);
  const note = checkNote(body);
  const payer = userByHandle(handle);
  if (!payer) throw notFound('no user has that handle');
  if (payer.id === user.id) throw new ApiError(422, 'self_request', 'cannot request money from yourself');
  const r = {
    id: nextId('rq_', (id) => state.requestsById.has(id)),
    requester: user.id, payer: payer.id, amount, note,
    status: 'pending', payment_id: null, created_at: nowStamp(),
  };
  addRequest(state, r);
  return requestView(r);
}

function opPayRequest(user, requestId, body) {
  const visibility = checkVisibility(body);
  const r = state.requestsById.get(requestId);
  if (!r) throw notFound('no such request');
  if (r.payer !== user.id) throw forbidden('only the payer may pay this request');
  if (r.status !== 'pending') throw new ApiError(409, 'request_not_pending', 'request is ' + r.status);
  if (user.balance < r.amount) throw new ApiError(409, 'insufficient_funds', 'balance is too low');
  const to = state.users.get(r.requester);
  const p = createPayment(user, to, r.amount, r.note, visibility, r.id, null);
  r.status = 'paid';
  r.payment_id = p.id;
  return paymentView(p);
}

function opSplit(user, body) {
  if (!has(body, 'participant_handles')) throw invalid('participant_handles is required');
  const handles = body.participant_handles;
  if (!Array.isArray(handles) || !handles.every((h) => typeof h === 'string')) {
    throw malformed('participant_handles must be an array of strings');
  }
  const amount = checkAmount(body);
  const note = checkNote(body);
  if (handles.length === 0) throw invalid('participant_handles must not be empty');
  if (new Set(handles).size !== handles.length) throw invalid('participant_handles contains a duplicate');
  const people = handles.map((h) => {
    const u = userByHandle(h);
    if (!u) throw notFound('no user has handle ' + h);
    return u;
  });
  const n = people.length;
  const base = Math.floor(amount / n);
  const extra = amount - base * n;
  const createdAt = nowStamp();
  const shares = [];
  const requests = [];
  people.forEach((u, i) => {
    const share = base + (i < extra ? 1 : 0);
    shares.push({ handle: u.handle, amount: share });
    if (u.id === user.id) return;
    const r = {
      id: nextId('rq_', (id) => state.requestsById.has(id)),
      requester: user.id, payer: u.id, amount: share, note,
      status: 'pending', payment_id: null, created_at: createdAt,
    };
    addRequest(state, r);
    requests.push(r);
  });
  const id = nextId('sp_', (x) => state.splitIds.has(x));
  const view = {
    split_id: id, amount, currency: state.currency, note, shares,
    requests: requests.map(requestView), created_at: createdAt,
  };
  state.splits.push({ id, requester: user.id, amount, note, shares: deepCopy(shares),
    request_ids: requests.map((r) => r.id), created_at: createdAt });
  state.splitIds.add(id);
  return view;
}

function opSettlement(user, body) {
  if (!has(body, 'transfers') || !Array.isArray(body.transfers)) throw invalid('transfers must be an array');
  const list = body.transfers;
  if (list.length < 1 || list.length > 32) throw invalid('transfers must contain 1 to 32 entries');
  const entries = list.map((t, i) => {
    if (!isObject(t)) throw invalid('transfer ' + i + ' must be an object');
    for (const f of ['from_handle', 'to_handle']) {
      if (typeof t[f] !== 'string') throw invalid('transfer ' + i + ' ' + f + ' must be a string');
    }
    const amount = checkAmount(t);
    const note = checkNote(t);
    const visibility = checkVisibility(t);
    const from = userByHandle(t.from_handle);
    const to = userByHandle(t.to_handle);
    if (!from || !to) throw notFound('transfer ' + i + ' names an unknown handle');
    if (from.id === to.id) throw new ApiError(422, 'self_payment', 'transfer ' + i + ' pays its own sender');
    return { from, to, amount, note, visibility };
  });
  const net = new Map();
  for (const e of entries) {
    net.set(e.from.id, (net.get(e.from.id) || 0) - e.amount);
    net.set(e.to.id, (net.get(e.to.id) || 0) + e.amount);
  }
  for (const [uid, delta] of net) {
    if (state.users.get(uid).balance + delta < 0) {
      throw new ApiError(409, 'insufficient_funds', 'the settlement is not affordable');
    }
  }
  const id = nextId('st_', (x) => state.settlementIds.has(x));
  const committedAt = nowStamp();
  const payments = entries.map((e) => createPayment(e.from, e.to, e.amount, e.note, e.visibility, null, id, committedAt));
  state.settlements.push({ id, operator: user.id, committed_at: committedAt, payment_ids: payments.map((p) => p.id) });
  state.settlementIds.add(id);
  return { settlement_id: id, committed_at: committedAt, payments: payments.map(paymentView) };
}

function opDecline(user, requestId) {
  const r = state.requestsById.get(requestId);
  if (!r) throw notFound('no such request');
  if (r.payer !== user.id) throw forbidden('only the payer may decline this request');
  if (r.status === 'pending') r.status = 'declined';
  else if (r.status !== 'declined') throw new ApiError(409, 'request_not_pending', 'request is ' + r.status);
  return requestView(r);
}

function opCancel(user, requestId) {
  const r = state.requestsById.get(requestId);
  if (!r) throw notFound('no such request');
  if (r.requester !== user.id) throw forbidden('only the requester may cancel this request');
  if (r.status === 'pending') r.status = 'cancelled';
  else if (r.status !== 'cancelled') throw new ApiError(409, 'request_not_pending', 'request is ' + r.status);
  return requestView(r);
}

function listRequests(user, url) {
  const q = url.searchParams;
  let direction = null;
  let status = null;
  if (q.has('direction')) {
    direction = q.get('direction');
    if (direction !== 'incoming' && direction !== 'outgoing') throw invalid('direction must be incoming or outgoing');
  }
  if (q.has('status')) {
    status = q.get('status');
    if (!REQUEST_STATUSES.includes(status)) throw invalid('unknown status');
  }
  const { limit, offset } = pageParams(url);
  const mine = state.requests.filter((r) => {
    if (r.payer !== user.id && r.requester !== user.id) return false;
    if (direction === 'incoming' && r.payer !== user.id) return false;
    if (direction === 'outgoing' && r.requester !== user.id) return false;
    return status === null || r.status === status;
  });
  const { out, hasMore } = pageNewestFirst(mine, limit, offset);
  return { requests: out.map(requestView), has_more: hasMore };
}

function listActivity(user, url) {
  const { limit, offset } = pageParams(url);
  const visible = state.payments.filter((p) => p.visibility === 'public' || p.from === user.id || p.to === user.id);
  const { out, hasMore } = pageNewestFirst(visible, limit, offset);
  return { payments: out.map(paymentView), has_more: hasMore };
}

// ---------------------------------------------------------------- auth endpoints

function readCredentials(body, fields) {
  for (const f of fields) {
    if (has(body, f) && typeof body[f] !== 'string') throw malformed(f + ' must be a string');
  }
  for (const f of fields) {
    if (!has(body, f)) throw invalid(f + ' is required');
  }
}

async function signup(raw) {
  const body = parseObject(raw);
  readCredentials(body, ['email', 'password', 'display_name']);
  const { email, password } = body;
  const displayName = body.display_name;
  if (!validEmail(email)) throw invalid('email must look like local@domain');
  if (codePoints(password) < 8) throw invalid('password must be at least 8 characters');
  const handle = deriveHandle(email);
  const conflicts = () => {
    if (state.byEmail.has(emailKey(email))) throw new ApiError(409, 'email_taken', 'email is already registered');
    if (state.byHandle.has(handle)) throw new ApiError(409, 'handle_taken', 'the handle derived from this email is taken');
  };
  conflicts();
  const hash = await hashPassword(password);
  // Concurrent signups (or a reset) may have changed the state while hashing.
  conflicts();
  const id = nextId('u_', (x) => state.users.has(x));
  const user = { id, email, hash, display_name: displayName, handle, balance: 0, operator: false };
  state.users.set(id, user);
  state.byEmail.set(emailKey(email), id);
  state.byHandle.set(handle, id);
  return { user_id: id, display_name: displayName, token: issueToken(id) };
}

async function login(raw) {
  const body = parseObject(raw);
  readCredentials(body, ['email', 'password']);
  const uid = state.byEmail.get(emailKey(body.email));
  const user = uid === undefined ? null : state.users.get(uid);
  const unauth = new ApiError(401, 'unauthenticated', 'wrong email or password');
  if (!user) throw unauth;
  const target = state;
  const ok = await verifyPassword(body.password, user.hash);
  if (!ok || state !== target || state.users.get(uid) !== user) throw unauth;
  return { user_id: user.id, display_name: user.display_name, token: issueToken(user.id) };
}

// ---------------------------------------------------------------- routing

async function route(req, res) {
  const url = new URL(req.url, 'http://localhost');
  const segs = url.pathname.split('/').slice(1);
  const method = req.method;
  const decode = (s) => {
    try { return decodeURIComponent(s); } catch (e) { return s; }
  };
  const path = url.pathname;

  if (path === '/health') {
    if (method !== 'GET') throw new ApiError(405, 'method_not_allowed', 'method not allowed');
    return send(res, 200, { status: 'ok' });
  }

  const raw = method === 'POST' || method === 'PUT' || method === 'PATCH' ? await readBody(req) : '';

  if (path === '/_test/reset' && method === 'POST') {
    const st = await buildFromFixture(parseJson(raw));
    state = st;
    return send(res, 204);
  }
  if (path === '/_test/export' && method === 'GET') return send(res, 200, exportState());
  if (path === '/_test/import' && method === 'POST') {
    state = importState(parseJson(raw));
    return send(res, 204);
  }
  if (path === '/auth/signup' && method === 'POST') return send(res, 201, await signup(raw));
  if (path === '/auth/login' && method === 'POST') return send(res, 200, await login(raw));

  const known = ['/me', '/payments', '/requests', '/splits', '/activity', '/settlements'];
  const isRequestAction = segs.length === 3 && segs[0] === 'requests' && segs[1] !== ''
    && ['pay', 'decline', 'cancel'].includes(segs[2]);
  if (!known.includes(path) && !isRequestAction) throw notFound('no such endpoint');

  const allowed = {
    '/me': 'GET', '/payments': 'POST', '/splits': 'POST', '/activity': 'GET', '/settlements': 'POST',
  };
  if (path !== '/requests' && !isRequestAction && allowed[path] !== method) {
    throw new ApiError(405, 'method_not_allowed', 'method not allowed');
  }
  if ((path === '/requests' && method !== 'GET' && method !== 'POST') || (isRequestAction && method !== 'POST')) {
    throw new ApiError(405, 'method_not_allowed', 'method not allowed');
  }

  const user = authenticate(req);

  if (path === '/me') {
    return send(res, 200, {
      user_id: user.id, display_name: user.display_name, handle: user.handle,
      balance: user.balance, currency: state.currency, minor_units: state.minor_units,
    });
  }
  if (path === '/activity') return send(res, 200, listActivity(user, url));
  if (path === '/requests' && method === 'GET') return send(res, 200, listRequests(user, url));

  let result;
  if (path === '/payments') result = idempotent(req, user, path, raw, (b) => opPayment(user, b));
  else if (path === '/requests') result = idempotent(req, user, path, raw, (b) => opRequest(user, b));
  else if (path === '/splits') result = idempotent(req, user, path, raw, (b) => opSplit(user, b));
  else if (path === '/settlements') {
    if (!user.operator) throw forbidden('settlements require an operator');
    result = idempotent(req, user, path, raw, (b) => opSettlement(user, b));
  } else {
    const id = decode(segs[1]);
    if (segs[2] === 'decline') return send(res, 200, opDecline(user, id));
    if (segs[2] === 'cancel') return send(res, 200, opCancel(user, id));
    const body = raw.trim() === '' ? '{}' : raw;
    result = idempotent(req, user, '/requests/' + id + '/pay', body, (b) => opPayRequest(user, id, b));
  }
  return send(res, result[0], result[1]);
}

const server = http.createServer((req, res) => {
  route(req, res).catch((err) => {
    if (res.headersSent) return;
    if (err instanceof ApiError) return sendError(res, err);
    console.error(err);
    sendError(res, new ApiError(500, 'internal_error', 'internal error'));
  });
});
server.keepAliveTimeout = 5000;
server.requestTimeout = 30000;
server.headersTimeout = 20000;

const port = Number(process.env.PORT) || 8080;
server.listen(port, '0.0.0.0', () => {
  console.log('pocketful stage 1 listening on 0.0.0.0:' + port);
});
