'use strict';
// Pocketful stage 2: wallet, payments, requests, splits, settlements, payment
// authorizations (holds and captures) over HTTP, plus the browser UI.
// All state lives in memory. Every state-changing step runs synchronously on the
// event loop, so each operation is atomic with respect to every other request.

const http = require('http');
const crypto = require('crypto');
const ui = require('./ui');

const MAX_AMOUNT = 1000000000;
const MAX_ABS_BALANCE = 2 ** 53;
const MAX_NOTE = 200;
const MAX_BODY = 8 * 1024 * 1024;
const HANDLE_RE = /^[a-z0-9_]{1,20}$/;
const DIGITS_RE = /^[0-9]+$/;
const REQUEST_STATUSES = ['pending', 'paid', 'declined', 'cancelled'];
const AUTH_STATUSES = ['open', 'captured', 'voided', 'expired'];
const DEFAULT_TTL = 600;
const SERVICE_STAGE1 = 'pocketful-stage-1';
const SERVICE_STAGE2 = 'pocketful-stage-2';

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

// RFC 3339 with an explicit numeric offset; milliseconds only when nonzero.
function stampOf(ms) {
  const iso = new Date(ms).toISOString();
  const frac = iso.slice(19, 23);
  return iso.slice(0, 19) + (frac === '.000' ? '' : frac) + '+00:00';
}

function nowStamp() {
  // Second precision, so created_at + ttl is exact.
  return stampOf(Math.floor(Date.now() / 1000) * 1000);
}

const RFC3339_RE = /^\d{4}-\d\d-\d\d[Tt ]\d\d:\d\d:\d\d(\.\d+)?([Zz]|[+-]\d\d:\d\d)$/;

function parseStamp(v) {
  if (typeof v !== 'string' || !RFC3339_RE.test(v)) return null;
  const ms = Date.parse(v.replace(' ', 'T'));
  return Number.isFinite(ms) ? ms : null;
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
  if (![N, r, p].every(Number.isInteger) || N < 2 || (N & (N - 1)) !== 0 || N > 1 << 16 || r < 1 || r > 16 || p < 1 || p > 4) return null;
  if (128 * N * r > 32 * 1024 * 1024) return null;
  const salt = Buffer.from(parts[4], 'base64');
  const key = Buffer.from(parts[5], 'base64');
  if (!salt.length || !key.length) return null;
  return { N, r, p, salt, key };
}

async function verifyPassword(password, stored) {
  const h = parseHash(stored);
  if (!h) return false;
  try {
    const key = await scryptAsync(password, h.salt, { N: h.N, r: h.r, p: h.p, keylen: h.key.length });
    return crypto.timingSafeEqual(key, h.key);
  } catch (e) {
    return false;
  }
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
    auths: [],             // creation order
    authsById: new Map(),
    openAuths: new Map(),  // payer id -> Set of open authorizations
    ttl: DEFAULT_TTL,
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

function addAuth(st, a) {
  st.auths.push(a);
  st.authsById.set(a.id, a);
  if (a.status === 'open') {
    if (!st.openAuths.has(a.from)) st.openAuths.set(a.from, new Set());
    st.openAuths.get(a.from).add(a);
  }
}

function closeAuth(a, status) {
  a.status = status;
  const set = state.openAuths.get(a.from);
  if (set) set.delete(a);
}

// An open authorization whose deadline has passed is expired, whether or not
// any request happened at the deadline.
function refreshAuth(a, now = Date.now()) {
  if (a.status === 'open' && now >= a.expiresMs) closeAuth(a, 'expired');
  return a;
}

function remainingOf(a) {
  return a.status === 'open' ? a.amount - a.captured : 0;
}

function heldOf(user) {
  const set = state.openAuths.get(user.id);
  if (!set) return 0;
  const now = Date.now();
  let held = 0;
  for (const a of [...set]) {
    refreshAuth(a, now);
    held += remainingOf(a);
  }
  return held;
}

function availableOf(user) {
  return user.balance - heldOf(user);
}

function authView(a) {
  refreshAuth(a);
  const from = state.users.get(a.from);
  const to = state.users.get(a.to);
  return {
    authorization_id: a.id,
    from_user_id: a.from,
    from_handle: from.handle,
    to_user_id: a.to,
    to_handle: to.handle,
    amount: a.amount,
    captured_amount: a.captured,
    remaining_amount: remainingOf(a),
    currency: state.currency,
    note: a.note,
    visibility: a.visibility,
    status: a.status,
    expires_at: a.expires_at,
    payment_id: a.payment_ids.length ? a.payment_ids[a.payment_ids.length - 1] : null,
    payment_ids: a.payment_ids.slice(),
    created_at: a.created_at,
  };
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
    authorization_id: p.authorization_id || null,
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

function issueToken(userId, st = state) {
  const token = crypto.randomBytes(24).toString('base64url');
  st.tokens.set(token, userId);
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
  if (has(fx, 'authorization_ttl_seconds')) {
    const ttl = fx.authorization_ttl_seconds;
    if (typeof ttl !== 'number' || !Number.isInteger(ttl) || ttl < 1 || ttl > 1e12) {
      throw invalid('authorization_ttl_seconds must be a positive integer');
    }
    st.ttl = ttl;
  }
  const auths = has(fx, 'authorizations') && fx.authorizations !== null ? fx.authorizations : [];
  if (!Array.isArray(auths)) throw invalid('authorizations must be an array');
  const now = Date.now();
  const holds = new Map();
  for (const a of auths) {
    if (!isObject(a)) throw invalid('authorization must be an object');
    if (typeof a.id !== 'string' || !a.id || a.id.length > 64) throw invalid('authorization.id is invalid');
    if (st.authsById.has(a.id)) throw invalid('duplicate authorization id');
    if (!st.users.has(a.from_user_id) || !st.users.has(a.to_user_id)) throw invalid('authorization user unknown');
    if (a.from_user_id === a.to_user_id) throw invalid('authorization pays its own payer');
    if (!isExactInt(a.amount) || a.amount < 1) throw invalid('authorization.amount is invalid');
    const status = has(a, 'status') ? a.status : 'open';
    if (!AUTH_STATUSES.includes(status)) throw invalid('authorization.status is invalid');
    let expiresMs = now + st.ttl * 1000;
    if (has(a, 'expires_at')) {
      expiresMs = parseStamp(a.expires_at);
      if (expiresMs === null) throw invalid('authorization.expires_at must be an RFC 3339 timestamp');
    } else if (status === 'open') {
      throw invalid('an open authorization needs expires_at');
    }
    let captured = 0;
    if (has(a, 'captured_amount') && a.captured_amount !== null) {
      if (!isExactInt(a.captured_amount) || a.captured_amount < 0 || a.captured_amount > a.amount) {
        throw invalid('authorization.captured_amount is invalid');
      }
      captured = a.captured_amount;
    }
    const paymentIds = [];
    if (Array.isArray(a.payment_ids) && a.payment_ids.every((x) => typeof x === 'string')) paymentIds.push(...a.payment_ids);
    else if (typeof a.payment_id === 'string') paymentIds.push(a.payment_id);
    const auth = {
      id: a.id, from: a.from_user_id, to: a.to_user_id, amount: a.amount, captured,
      note: checkNote(a), visibility: checkVisibility(a), status,
      expiresMs, expires_at: stampOf(expiresMs), created_at: stamp, payment_ids: paymentIds,
    };
    if (auth.status === 'open' && now >= expiresMs) auth.status = 'expired';
    if (auth.status === 'open') holds.set(auth.from, (holds.get(auth.from) || 0) + auth.amount - auth.captured);
    addAuth(st, auth);
  }
  for (const [uid, held] of holds) {
    if (held > st.users.get(uid).balance) throw invalid('seeded open holds exceed the payer balance');
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
      service: SERVICE_STAGE2,
      currency: state.currency,
      minor_units: state.minor_units,
      authorization_ttl_seconds: state.ttl,
      authorizations: state.auths.map((a) => {
        refreshAuth(a);
        return { id: a.id, from: a.from, to: a.to, amount: a.amount, captured: a.captured, note: a.note,
          visibility: a.visibility, status: a.status, expires_at: a.expires_at, created_at: a.created_at,
          payment_ids: a.payment_ids.slice() };
      }),
      users,
      tokens,
      payments: state.payments.map((p) => ({ ...p, authorization_id: p.authorization_id || null })),
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
  if (!isObject(s) || (s.service !== SERVICE_STAGE1 && s.service !== SERVICE_STAGE2)) throw bad('not a state of this service');
  const v2 = s.service === SERVICE_STAGE2;
  if (v2 && (!Array.isArray(s.authorizations) || !Number.isSafeInteger(s.authorization_ttl_seconds)
    || s.authorization_ttl_seconds < 1)) throw bad('authorizations');
  if (typeof s.currency !== 'string' || ![0, 2, 3].includes(s.minor_units)) throw bad('currency');
  for (const f of ['users', 'tokens', 'payments', 'requests', 'splits', 'settlements', 'idempotency']) {
    if (!Array.isArray(s[f])) throw bad(f);
  }
  if (!Number.isSafeInteger(s.seq) || s.seq < 0) throw bad('seq');
  const st = emptyState();
  st.currency = s.currency;
  st.minor_units = s.minor_units;
  st.seq = s.seq;
  if (v2) st.ttl = s.authorization_ttl_seconds;
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
      || !optStr(p.request_id) || !optStr(p.settlement_id) || !str(p.created_at)
      || (has(p, 'authorization_id') && !optStr(p.authorization_id))) throw bad('payment');
    addPayment(st, { id: p.id, from: p.from, to: p.to, amount: p.amount, note: p.note,
      visibility: p.visibility, request_id: p.request_id, authorization_id: p.authorization_id || null,
      settlement_id: p.settlement_id, created_at: p.created_at });
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
  const held = new Map();
  for (const a of v2 ? s.authorizations : []) {
    const expiresMs = isObject(a) ? parseStamp(a.expires_at) : null;
    if (!isObject(a) || !str(a.id) || st.authsById.has(a.id) || !st.users.has(a.from) || !st.users.has(a.to)
      || !isExactInt(a.amount) || a.amount < 1 || !isExactInt(a.captured) || a.captured < 0 || a.captured > a.amount
      || !str(a.note) || !['public', 'private'].includes(a.visibility) || !AUTH_STATUSES.includes(a.status)
      || expiresMs === null || !str(a.created_at) || !Array.isArray(a.payment_ids) || !a.payment_ids.every(str)) {
      throw bad('authorization');
    }
    if (a.status === 'open') held.set(a.from, (held.get(a.from) || 0) + a.amount - a.captured);
    addAuth(st, { id: a.id, from: a.from, to: a.to, amount: a.amount, captured: a.captured, note: a.note,
      visibility: a.visibility, status: a.status, expiresMs, expires_at: a.expires_at, created_at: a.created_at,
      payment_ids: a.payment_ids.slice() });
  }
  for (const [uid, h] of held) {
    if (h > st.users.get(uid).balance) throw bad('holds exceed a balance');
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

function createPayment(from, to, amount, note, visibility, links = {}) {
  const p = {
    id: nextId('pay_', (id) => state.paymentsById.has(id)),
    from: from.id, to: to.id, amount, note, visibility,
    request_id: links.request_id || null,
    authorization_id: links.authorization_id || null,
    settlement_id: links.settlement_id || null,
    created_at: links.created_at || nowStamp(),
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
  if (availableOf(user) < amount) throw new ApiError(409, 'insufficient_funds', 'available balance is too low');
  return paymentView(createPayment(user, to, amount, note, visibility));
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
  if (availableOf(user) < r.amount) throw new ApiError(409, 'insufficient_funds', 'available balance is too low');
  const to = state.users.get(r.requester);
  const p = createPayment(user, to, r.amount, r.note, visibility, { request_id: r.id });
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
    if (availableOf(state.users.get(uid)) + delta < 0) {
      throw new ApiError(409, 'insufficient_funds', 'the settlement is not affordable');
    }
  }
  const id = nextId('st_', (x) => state.settlementIds.has(x));
  const committedAt = nowStamp();
  const payments = entries.map((e) => createPayment(e.from, e.to, e.amount, e.note, e.visibility,
    { settlement_id: id, created_at: committedAt }));
  state.settlements.push({ id, operator: user.id, committed_at: committedAt, payment_ids: payments.map((p) => p.id) });
  state.settlementIds.add(id);
  return { settlement_id: id, committed_at: committedAt, payments: payments.map(paymentView) };
}

function opAuthorize(user, body) {
  const handle = requireString(body, 'to_handle');
  const amount = checkAmount(body);
  const note = checkNote(body);
  const visibility = checkVisibility(body);
  const to = userByHandle(handle);
  if (!to) throw notFound('no user has that handle');
  if (to.id === user.id) throw new ApiError(422, 'self_payment', 'cannot authorize a payment to yourself');
  if (availableOf(user) < amount) throw new ApiError(409, 'insufficient_funds', 'available balance is too low');
  const createdAt = nowStamp();
  const expiresMs = Date.parse(createdAt) + state.ttl * 1000;
  const a = {
    id: nextId('auth_', (x) => state.authsById.has(x)),
    from: user.id, to: to.id, amount, captured: 0, note, visibility, status: 'open',
    expiresMs, expires_at: stampOf(expiresMs), created_at: createdAt, payment_ids: [],
  };
  addAuth(state, a);
  return authView(a);
}

function opCapture(user, authId, body) {
  let final = true;
  if (has(body, 'final')) {
    if (typeof body.final !== 'boolean') throw malformed('final must be a boolean');
    final = body.final;
  }
  let amount = null;
  if (has(body, 'amount')) {
    const v = body.amount;
    if (typeof v !== 'number' || !Number.isInteger(v) || v < 1) throw invalid('amount must be a positive integer');
    amount = v;
  }
  const a = state.authsById.get(authId);
  if (!a) throw notFound('no such authorization');
  if (a.to !== user.id) throw forbidden('only the receiver may capture this authorization');
  refreshAuth(a);
  if (a.status === 'expired') throw new ApiError(409, 'authorization_expired', 'the authorization has expired');
  if (a.status !== 'open') throw new ApiError(409, 'authorization_not_open', 'the authorization is ' + a.status);
  const remaining = remainingOf(a);
  if (amount === null) amount = remaining;
  if (amount > remaining) {
    throw new ApiError(422, 'capture_exceeds_authorization', 'amount is above the uncaptured remainder');
  }
  const payer = state.users.get(a.from);
  const p = createPayment(payer, user, amount, a.note, a.visibility, { authorization_id: a.id });
  a.captured += amount;
  a.payment_ids.push(p.id);
  if (final || a.captured === a.amount) closeAuth(a, 'captured');
  return paymentView(p);
}

function opVoid(user, authId) {
  const a = state.authsById.get(authId);
  if (!a) throw notFound('no such authorization');
  if (a.from !== user.id) throw forbidden('only the payer may void this authorization');
  refreshAuth(a);
  if (a.status === 'open') closeAuth(a, 'voided');
  else if (a.status !== 'voided') throw new ApiError(409, 'authorization_not_open', 'the authorization is ' + a.status);
  return authView(a);
}

function listAuthorizations(user, url) {
  const q = url.searchParams;
  let direction = null;
  let status = null;
  if (q.has('direction')) {
    direction = q.get('direction');
    if (direction !== 'incoming' && direction !== 'outgoing') throw invalid('direction must be incoming or outgoing');
  }
  if (q.has('status')) {
    status = q.get('status');
    if (!AUTH_STATUSES.includes(status)) throw invalid('unknown status');
  }
  const { limit, offset } = pageParams(url);
  const now = Date.now();
  const mine = state.auths.filter((a) => {
    if (a.from !== user.id && a.to !== user.id) return false;
    if (direction === 'outgoing' && a.from !== user.id) return false;
    if (direction === 'incoming' && a.to !== user.id) return false;
    refreshAuth(a, now);
    return status === null || a.status === status;
  });
  const { out, hasMore } = pageNewestFirst(mine, limit, offset);
  return { authorizations: out.map(authView), has_more: hasMore };
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
  // The signup takes effect in the state that was current when it started. If a
  // reset or import replaces that state while the password is hashed, the
  // signup is ordered before the replacement and is discarded with it.
  const target = state;
  const conflicts = () => {
    if (target.byEmail.has(emailKey(email))) throw new ApiError(409, 'email_taken', 'email is already registered');
    if (target.byHandle.has(handle)) throw new ApiError(409, 'handle_taken', 'the handle derived from this email is taken');
  };
  conflicts();
  const hash = await hashPassword(password);
  // Concurrent signups may have claimed the email or handle while hashing.
  conflicts();
  let id;
  do {
    target.seq += 1;
    id = 'u_' + target.seq;
  } while (target.users.has(id));
  const user = { id, email, hash, display_name: displayName, handle, balance: 0, operator: false };
  target.users.set(id, user);
  target.byEmail.set(emailKey(email), id);
  target.byHandle.set(handle, id);
  return { user_id: id, display_name: displayName, token: issueToken(id, target) };
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
  if (!ok) throw unauth;
  // Like signup, a login that started before a reset or import belongs to the old state.
  return { user_id: user.id, display_name: user.display_name, token: issueToken(user.id, target) };
}

// ---------------------------------------------------------------- routing

const UI_ROUTES = new Set(['/', '/requests', '/split', '/signup', '/login', '/authorizations']);
const SHARED_ROUTES = new Set(['/requests', '/authorizations']);

// A browser navigation asks for text/html; API clients (no Accept, */*, JSON) get JSON.
function wantsHtml(req) {
  const accept = req.headers.accept;
  if (typeof accept !== 'string') return false;
  return accept.split(',').some((part) => {
    const [type, ...params] = part.trim().split(';').map((x) => x.trim().toLowerCase());
    if (type !== 'text/html') return false;
    const q = params.find((x) => x.startsWith('q='));
    return !q || Number(q.slice(2)) > 0;
  });
}

function sendStatic(res, status, type, body) {
  res.writeHead(status, {
    'Content-Type': type,
    'Content-Length': Buffer.byteLength(body),
    'Cache-Control': 'no-cache',
    'X-Content-Type-Options': 'nosniff',
  });
  res.end(body);
}

const API_METHODS = {
  '/me': ['GET'],
  '/payments': ['POST'],
  '/requests': ['GET', 'POST'],
  '/splits': ['POST'],
  '/activity': ['GET'],
  '/settlements': ['POST'],
  '/authorizations': ['GET', 'POST'],
};

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

  if (method === 'GET' || method === 'HEAD') {
    const asset = ui.asset(path);
    if (asset) return sendStatic(res, 200, asset.type, asset.body);
    if (UI_ROUTES.has(path) && (!SHARED_ROUTES.has(path) || wantsHtml(req))) {
      return sendStatic(res, 200, 'text/html; charset=utf-8', ui.page());
    }
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

  const isRequestAction = segs.length === 3 && segs[0] === 'requests' && segs[1] !== ''
    && ['pay', 'decline', 'cancel'].includes(segs[2]);
  const isAuthAction = segs.length === 3 && segs[0] === 'authorizations' && segs[1] !== ''
    && ['capture', 'void'].includes(segs[2]);
  if (!API_METHODS[path] && !isRequestAction && !isAuthAction) throw notFound('no such endpoint');
  const methods = API_METHODS[path] || ['POST'];
  if (!methods.includes(method)) throw new ApiError(405, 'method_not_allowed', 'method not allowed');

  const user = authenticate(req);

  if (path === '/me') {
    const held = heldOf(user);
    return send(res, 200, {
      user_id: user.id, display_name: user.display_name, handle: user.handle,
      balance: user.balance, total: user.balance, available: user.balance - held, held,
      currency: state.currency, minor_units: state.minor_units,
    });
  }
  if (path === '/activity') return send(res, 200, listActivity(user, url));
  if (path === '/requests' && method === 'GET') return send(res, 200, listRequests(user, url));
  if (path === '/authorizations' && method === 'GET') return send(res, 200, listAuthorizations(user, url));

  let result;
  if (path === '/payments') result = idempotent(req, user, path, raw, (b) => opPayment(user, b));
  else if (path === '/requests') result = idempotent(req, user, path, raw, (b) => opRequest(user, b));
  else if (path === '/splits') result = idempotent(req, user, path, raw, (b) => opSplit(user, b));
  else if (path === '/authorizations') result = idempotent(req, user, path, raw, (b) => opAuthorize(user, b));
  else if (path === '/settlements') {
    if (!user.operator) throw forbidden('settlements require an operator');
    result = idempotent(req, user, path, raw, (b) => opSettlement(user, b));
  } else if (isAuthAction) {
    const id = decode(segs[1]);
    if (segs[2] === 'void') return send(res, 200, opVoid(user, id));
    result = idempotent(req, user, '/authorizations/' + id + '/capture', raw, (b) => opCapture(user, id, b));
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
  console.log('pocketful stage 2 listening on 0.0.0.0:' + port);
});
