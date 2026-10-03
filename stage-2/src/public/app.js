'use strict';
// Pocketful browser client. Plain DOM, no dependencies; every screen is rendered
// from the API. Writes never update the screen optimistically: data is reloaded
// after the server has answered.

(function () {
  const TOKEN_KEY = 'pocketful.token';
  const app = document.getElementById('app');
  const route = location.pathname;

  // ------------------------------------------------------------ small helpers

  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k === 'text') el.textContent = v;
      else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? '' : String(v));
    }
    for (const c of children.flat()) {
      if (c === null || c === undefined || c === false) continue;
      el.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return el;
  }

  // replaceChildren() would render null as the text "null"; skip empty children.
  function put(el, ...children) {
    el.replaceChildren(...children.flat().filter((c) => c !== null && c !== undefined && c !== false));
  }

  function newKey() {
    const b = new Uint8Array(16);
    crypto.getRandomValues(b);
    return 'ui-' + Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');
  }

  const getToken = () => localStorage.getItem(TOKEN_KEY);
  const setToken = (t) => localStorage.setItem(TOKEN_KEY, t);
  const clearToken = () => localStorage.removeItem(TOKEN_KEY);

  class NetworkError extends Error {}

  // Resolves to { status, data }. Rejects with NetworkError when no answer arrived.
  async function api(method, path, body, key) {
    const headers = { Accept: 'application/json' };
    const token = getToken();
    if (token) headers.Authorization = 'Bearer ' + token;
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    if (key) headers['Idempotency-Key'] = key;
    let res;
    try {
      res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
    } catch (e) {
      throw new NetworkError('network');
    }
    let data = null;
    try {
      data = await res.json();
    } catch (e) {
      if (res.status !== 204) throw new NetworkError('unreadable response');
    }
    if (res.status === 401 && token && !path.startsWith('/auth/')) {
      clearToken();
      location.replace('/login');
      return new Promise(() => {});
    }
    return { status: res.status, data };
  }

  async function getAll(path, field) {
    const out = [];
    for (let offset = 0; offset < 5000; offset += 200) {
      const sep = path.includes('?') ? '&' : '?';
      const r = await api('GET', path + sep + 'limit=200&offset=' + offset);
      if (r.status !== 200) return r;
      out.push(...r.data[field]);
      if (!r.data.has_more) break;
    }
    return { status: 200, data: out };
  }

  // ------------------------------------------------------------ money

  let currency = { code: '', minor: 2 };

  function fmt(amount) {
    const mu = currency.minor;
    let s = String(amount);
    if (mu === 0) return s + ' ' + currency.code;
    s = s.padStart(mu + 1, '0');
    return s.slice(0, -mu) + '.' + s.slice(-mu) + ' ' + currency.code;
  }

  function decimal(amount) {
    return fmt(amount).split(' ')[0];
  }

  // Decimal text -> integer minor units, or null when it is not a valid amount.
  function parseAmount(text) {
    const t = String(text).trim();
    const mu = currency.minor;
    const re = mu === 0 ? /^(\d+)$/ : new RegExp('^(\\d+)(?:\\.(\\d{1,' + mu + '}))?$');
    const m = re.exec(t);
    if (!m) return null;
    const whole = m[1].replace(/^0+(?=\d)/, '');
    const frac = (m[2] || '').padEnd(mu, '0');
    const digits = (whole + frac).replace(/^0+(?=\d)/, '');
    if (digits.length > 15) return null;
    return Number(digits);
  }

  function amountProblem() {
    return currency.minor === 0
      ? 'Enter a whole amount in ' + currency.code + ', for example 1500.'
      : 'Enter an amount like 15.00, with at most ' + currency.minor + ' decimal places.';
  }

  function when(stamp) {
    const d = new Date(stamp);
    if (Number.isNaN(d.getTime())) return '';
    return d.toLocaleString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  }

  function relative(stamp) {
    const ms = new Date(stamp).getTime() - Date.now();
    const abs = Math.abs(ms);
    const unit = abs >= 86400000 ? [86400000, 'day'] : abs >= 3600000 ? [3600000, 'hour'] : abs >= 60000 ? [60000, 'minute'] : [1000, 'second'];
    const n = Math.max(1, Math.round(abs / unit[0]));
    const span = n + ' ' + unit[1] + (n === 1 ? '' : 's');
    return ms >= 0 ? 'in ' + span : span + ' ago';
  }

  // ------------------------------------------------------------ friendly errors

  const MESSAGES = {
    insufficient_funds: 'Not enough available funds for this.',
    not_found: 'We could not find anyone with that handle.',
    self_payment: 'You cannot send money to yourself.',
    self_request: 'You cannot request money from yourself.',
    request_not_pending: 'This request has already been settled elsewhere. The list has been refreshed.',
    authorization_not_open: 'This hold is no longer open. The list has been refreshed.',
    authorization_expired: 'This hold has expired. The list has been refreshed.',
    capture_exceeds_authorization: 'That is more than the amount still held.',
    email_taken: 'An account with this email already exists.',
    handle_taken: 'The username made from this email is already taken. Try a different email.',
    forbidden: 'You are not allowed to do that.',
    unauthenticated: 'The email or password is not right.',
  };

  function explain(r) {
    const e = r && r.data && r.data.error;
    if (e && MESSAGES[e.code]) return MESSAGES[e.code];
    if (e && e.code === 'validation_failed') return 'Please check the details: ' + e.message + '.';
    return 'Something went wrong. Please try again.';
  }

  // A feedback slot renders a message element only while there is a message.
  function slot(container) {
    return {
      set(testid, kind, text) {
        put(container, h('p', { 'data-testid': testid, class: 'notice notice-' + kind, role: kind === 'success' ? 'status' : 'alert' }, text));
      },
      clear() { put(container); },
    };
  }

  // ------------------------------------------------------------ shell

  const NAV = [['/', 'Wallet'], ['/requests', 'Requests'], ['/split', 'Split a bill'], ['/authorizations', 'Holds']];

  function shell(me) {
    const nav = h('nav', { class: 'nav', 'aria-label': 'Main' },
      NAV.map(([href, label]) => h('a', { href, class: href === route ? 'active' : null, 'aria-current': href === route ? 'page' : null }, label)));
    const who = h('div', { class: 'who' },
      h('span', { class: 'who-name' },
        h('span', { 'data-testid': 'current-user', class: 'who-display' }, me.display_name),
        h('span', { class: 'who-handle' }, '@', h('span', { 'data-testid': 'current-handle' }, me.handle))),
      h('button', { type: 'button', class: 'btn btn-quiet', 'data-testid': 'logout-button', onclick: () => { clearToken(); location.href = '/login'; } }, 'Log out'));
    const header = h('header', { class: 'topbar' },
      h('div', { class: 'topbar-inner' }, h('a', { href: '/', class: 'brand' }, h('span', { class: 'brand-mark', 'aria-hidden': 'true' }, 'P'), 'Pocketful'), nav, who));
    const main = h('main', { class: 'page' });
    put(app, header, main);
    return main;
  }

  function loadingBlock(text) {
    return h('p', { class: 'loading', role: 'status', 'aria-busy': 'true' }, h('span', { class: 'spinner', 'aria-hidden': 'true' }), text || 'Loading…');
  }

  function field(id, label, input, hint) {
    input.id = id;
    return h('div', { class: 'field' }, h('label', { for: id }, label), input, hint ? h('p', { class: 'hint' }, hint) : null);
  }

  function visibilitySelect(testid) {
    return h('select', { 'data-testid': testid },
      h('option', { value: 'public' }, 'Public — anyone can see it'),
      h('option', { value: 'private' }, 'Private — only the two of you'));
  }

  // Remembers the key of the last submission so that resubmitting the very same
  // form is a retry of that submission (same Idempotency-Key, same body).
  function retryIdentity() {
    let last = null;
    return {
      keyFor(body) {
        const fp = JSON.stringify(body);
        if (!last || last.fp !== fp) last = { fp, key: newKey() };
        return last.key;
      },
    };
  }

  // ------------------------------------------------------------ auth screens

  // `me` is set when a signed-in visitor opens /login or /signup directly: the
  // form is still shown (to switch accounts) inside the signed-in layout.
  function authScreen(kind, me) {
    const isLogin = kind === 'login';
    const card = h('section', { class: 'auth-card' });
    if (me) {
      const main = shell(me);
      main.classList.add('auth-page');
      main.append(card);
      card.append(h('p', { class: 'notice notice-info', role: 'status' }, 'You are signed in as @' + me.handle + '. ',
        isLogin ? 'Log in below to switch accounts.' : 'Create another account below, or go back to your ', isLogin ? null : h('a', { href: '/' }, 'wallet'), isLogin ? null : '.'));
    } else {
      put(app, h('main', { class: 'auth-page' },
        h('a', { href: '/', class: 'brand brand-large' }, h('span', { class: 'brand-mark', 'aria-hidden': 'true' }, 'P'), 'Pocketful'),
        card));
    }
    const email = h('input', { type: 'email', 'data-testid': kind + '-email', autocomplete: 'email' });
    const password = h('input', { type: 'password', 'data-testid': kind + '-password', autocomplete: isLogin ? 'current-password' : 'new-password' });
    const name = isLogin ? null : h('input', { type: 'text', 'data-testid': 'signup-display-name', autocomplete: 'name' });
    const feedback = h('div', { class: 'feedback' });
    const fb = slot(feedback);
    const submit = h('button', { type: 'submit', class: 'btn btn-primary btn-block', 'data-testid': kind + '-submit' }, isLogin ? 'Log in' : 'Create account');
    const form = h('form', { novalidate: true, class: 'stack' },
      field(kind + '-email-input', 'Email', email),
      name ? field('signup-name-input', 'Your name', name) : null,
      field(kind + '-password-input', 'Password', password, isLogin ? null : 'At least 8 characters.'),
      feedback, submit);
    card.append(
      h('h1', {}, isLogin ? 'Welcome back' : 'Create your wallet'),
      h('p', { class: 'muted' }, isLogin ? 'Log in to send, request and split money.' : 'Send money by handle, request it back and split bills.'),
      form,
      h('p', { class: 'switch' }, isLogin ? 'New to Pocketful? ' : 'Already have an account? ',
        h('a', { href: isLogin ? '/signup' : '/login' }, isLogin ? 'Create an account' : 'Log in')));
    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      submit.disabled = true;
      const body = isLogin
        ? { email: email.value, password: password.value }
        : { email: email.value, password: password.value, display_name: name.value };
      try {
        const r = await api('POST', isLogin ? '/auth/login' : '/auth/signup', body);
        if (r.status === 200 || r.status === 201) {
          fb.clear();
          setToken(r.data.token);
          location.href = '/';
          return;
        }
        fb.set('auth-error', 'error', isLogin && r.status === 401 ? 'The email or password is not right.' : explain(r));
      } catch (e) {
        fb.set('auth-error', 'error', 'We could not reach Pocketful. Check your connection and try again.');
      } finally {
        submit.disabled = false;
      }
    });
  }

  // ------------------------------------------------------------ wallet screen

  async function walletScreen(me, boot) {
    const main = shell(me);
    const summary = h('section', { class: 'card wallet', 'aria-labelledby': 'wallet-title' });
    const feedSection = h('section', { class: 'card feed', 'aria-labelledby': 'feed-title' });
    const payCard = h('section', { class: 'card', 'aria-labelledby': 'pay-title' });
    const requestCard = h('section', { class: 'card', 'aria-labelledby': 'request-title' });
    main.append(h('h1', { class: 'sr-only' }, 'Wallet'),
      h('div', { class: 'grid' }, h('div', { class: 'col' }, summary, payCard, requestCard), h('div', { class: 'col col-wide' }, feedSection)));

    // balance + feed, latest refresh wins
    const numbers = h('div', { class: 'numbers' }, loadingBlock('Loading your balance…'));
    const refreshBtn = h('button', { type: 'button', class: 'btn btn-secondary btn-small', 'data-testid': 'wallet-refresh' }, 'Refresh');
    summary.append(h('div', { class: 'card-head' }, h('h2', { id: 'wallet-title' }, 'Your wallet'), refreshBtn), numbers);
    const feedBody = h('div', {}, loadingBlock('Loading activity…'));
    feedSection.append(h('div', { class: 'card-head' }, h('h2', { id: 'feed-title' }, 'Activity')), feedBody);

    let generation = 0;
    function renderNumbers(m) {
      const parts = [
        h('p', { class: 'label' }, 'Available to spend'),
        h('p', { class: 'available', 'data-testid': 'wallet-available', 'data-amount': m.available }, fmt(m.available)),
        h('dl', { class: 'sub-numbers' },
          h('div', {}, h('dt', {}, 'Total balance'), h('dd', { class: 'total', 'data-testid': 'wallet-balance', 'data-amount': m.balance }, fmt(m.balance))),
          m.held > 0 ? h('div', { class: 'held-row' }, h('dt', {}, 'On hold'), h('dd', { class: 'held', 'data-testid': 'wallet-held', 'data-amount': m.held }, fmt(m.held))) : null),
      ];
      put(numbers, ...parts);
    }
    function renderFeed(list) {
      if (!list.length) {
        put(feedBody, h('div', { class: 'empty', 'data-testid': 'empty-activity' },
          h('p', { class: 'empty-title' }, 'No activity yet'),
          h('p', { class: 'muted' }, 'Payments you send or receive, and public payments by others, show up here.')));
        return;
      }
      put(feedBody, h('ol', { class: 'list', 'data-testid': 'activity-list' }, list.map(feedItem)));
    }
    function feedItem(p) {
      const out = p.from_user_id === me.user_id;
      const inn = p.to_user_id === me.user_id;
      const dir = out ? 'sent' : inn ? 'received' : 'other';
      const label = out ? 'You paid' : inn ? 'You received' : 'Payment between others';
      return h('li', { class: 'item feed-item dir-' + dir, 'data-testid': 'activity-item-' + p.payment_id, 'data-visibility': p.visibility },
        h('div', { class: 'item-main' },
          h('p', { class: 'item-kicker' }, label),
          h('p', { class: 'parties', 'data-testid': 'activity-parties-' + p.payment_id },
            h('span', {}, '@' + p.from_handle), h('span', { class: 'arrow', 'aria-label': 'paid' }, ' → '), h('span', {}, '@' + p.to_handle)),
          h('p', { class: 'note', 'data-testid': 'activity-note-' + p.payment_id }, p.note),
          h('p', { class: 'meta' }, h('span', { class: 'badge badge-' + p.visibility }, p.visibility === 'private' ? 'Private' : 'Public'),
            h('span', {}, when(p.created_at)))),
        h('p', { class: 'item-amount amount-' + dir },
          h('span', { class: 'sr-only' }, out ? 'Sent ' : inn ? 'Received ' : 'Amount '),
          h('span', { class: 'sign', 'aria-hidden': 'true' }, out ? '−' : inn ? '+' : ''),
          h('span', { 'data-testid': 'activity-amount-' + p.payment_id }, fmt(p.amount))));
    }
    async function refresh(initial) {
      const mine = ++generation;
      const meP = initial ? Promise.resolve({ status: 200, data: me }) : api('GET', '/me').catch(() => null);
      const feedP = (initial && boot.feed) || getAll('/activity', 'payments');
      const m = await meP;
      if (mine !== generation) return;
      if (m && m.status === 200) renderNumbers(m.data);
      else put(numbers, h('p', { class: 'notice notice-error', role: 'alert' }, 'We could not load your balance. Use Refresh to try again.'));
      const f = await feedP.catch(() => null);
      if (mine !== generation) return;
      if (f && f.status === 200) renderFeed(f.data);
      else put(feedBody, h('p', { class: 'notice notice-error', role: 'alert' }, 'We could not load your activity. Use Refresh to try again.'));
    }
    refreshBtn.addEventListener('click', () => { refresh(); });

    // pay form
    const pay = {
      handle: h('input', { type: 'text', 'data-testid': 'pay-handle', autocomplete: 'off', autocapitalize: 'none', spellcheck: 'false' }),
      amount: h('input', { type: 'text', inputmode: 'decimal', 'data-testid': 'pay-amount', autocomplete: 'off' }),
      note: h('input', { type: 'text', 'data-testid': 'pay-note', maxlength: '200' }),
      visibility: visibilitySelect('pay-visibility'),
    };
    const payFeedback = h('div', { class: 'feedback' });
    const payFb = slot(payFeedback);
    const paySubmit = h('button', { type: 'submit', class: 'btn btn-primary', 'data-testid': 'pay-submit' }, 'Pay');
    const payForm = h('form', { novalidate: true, class: 'stack' },
      field('pay-handle-input', 'Pay to (handle)', pay.handle),
      field('pay-amount-input', 'Amount (' + currency.code + ')', pay.amount),
      field('pay-note-input', 'Note (optional)', pay.note),
      field('pay-visibility-input', 'Who can see it', pay.visibility),
      payFeedback, h('div', { class: 'actions' }, paySubmit));
    payCard.append(h('h2', { id: 'pay-title' }, 'Send money'), payForm);
    const payRetry = retryIdentity();
    let payBusy = false;
    payForm.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      if (payBusy) return;
      const amount = parseAmount(pay.amount.value);
      if (amount === null) {
        payFb.set('pay-error', 'error', amountProblem());
        return;
      }
      const body = { to_handle: pay.handle.value.trim(), amount, note: pay.note.value, visibility: pay.visibility.value };
      const key = payRetry.keyFor(body);
      payBusy = true;
      paySubmit.disabled = true;
      paySubmit.textContent = 'Sending…';
      try {
        const r = await api('POST', '/payments', body, key);
        if (r.status === 200 || r.status === 201) {
          payFb.set('pay-success', 'success', 'Sent ' + fmt(r.data.amount) + ' to @' + r.data.to_handle + '.');
        } else if (r.status >= 500) {
          payFb.set('pay-uncertain', 'uncertain', 'We could not confirm this payment. Press Pay again to retry — it will not be sent twice.');
        } else {
          payFb.set('pay-error', 'error', explain(r));
        }
        await refresh();
      } catch (e) {
        payFb.set('pay-uncertain', 'uncertain', 'We could not confirm this payment. Press Pay again to retry — it will not be sent twice.');
      } finally {
        payBusy = false;
        paySubmit.disabled = false;
        paySubmit.textContent = 'Pay';
      }
    });

    // request form
    const rq = {
      handle: h('input', { type: 'text', 'data-testid': 'request-handle', autocomplete: 'off', autocapitalize: 'none', spellcheck: 'false' }),
      amount: h('input', { type: 'text', inputmode: 'decimal', 'data-testid': 'request-amount', autocomplete: 'off' }),
      note: h('input', { type: 'text', 'data-testid': 'request-note', maxlength: '200' }),
    };
    const rqFeedback = h('div', { class: 'feedback' });
    const rqFb = slot(rqFeedback);
    const rqSubmit = h('button', { type: 'submit', class: 'btn btn-primary', 'data-testid': 'request-submit' }, 'Request');
    const rqForm = h('form', { novalidate: true, class: 'stack' },
      field('request-handle-input', 'Ask (handle)', rq.handle),
      field('request-amount-input', 'Amount (' + currency.code + ')', rq.amount),
      field('request-note-input', 'What it is for (optional)', rq.note),
      rqFeedback, h('div', { class: 'actions' }, rqSubmit));
    requestCard.append(h('h2', { id: 'request-title' }, 'Request money'), rqForm);
    const rqRetry = retryIdentity();
    let rqBusy = false;
    rqForm.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      if (rqBusy) return;
      const amount = parseAmount(rq.amount.value);
      if (amount === null) {
        rqFb.set('request-error', 'error', amountProblem());
        return;
      }
      const body = { payer_handle: rq.handle.value.trim(), amount, note: rq.note.value };
      const key = rqRetry.keyFor(body);
      rqBusy = true;
      rqSubmit.disabled = true;
      try {
        const r = await api('POST', '/requests', body, key);
        if (r.status === 200 || r.status === 201) rqFb.set('request-success', 'success', 'Asked @' + r.data.payer_handle + ' for ' + fmt(r.data.amount) + '.');
        else if (r.status >= 500) rqFb.set('request-error', 'uncertain', 'We could not confirm this request. Press Request again to retry.');
        else rqFb.set('request-error', 'error', explain(r));
        await refresh();
      } catch (e) {
        rqFb.set('request-error', 'uncertain', 'We could not confirm this request. Press Request again to retry.');
      } finally {
        rqBusy = false;
        rqSubmit.disabled = false;
      }
    });

    await refresh(true);
  }

  // ------------------------------------------------------------ requests screen

  const REQUEST_WORDS = { pending: 'Pending', paid: 'Paid', declined: 'Declined', cancelled: 'Cancelled' };

  async function requestsScreen(me) {
    const main = shell(me);
    const feedback = h('div', { class: 'feedback' });
    const fb = slot(feedback);
    const body = h('div', {}, loadingBlock('Loading requests…'));
    main.append(h('div', { class: 'page-head' }, h('h1', {}, 'Requests'),
      h('p', { class: 'muted' }, 'Money people have asked you for, and money you have asked for.')), feedback, body);
    let generation = 0;
    let busy = false;

    async function load() {
      const mine = ++generation;
      let r;
      try { r = await getAll('/requests', 'requests'); } catch (e) { r = null; }
      if (mine !== generation) return;
      if (!r || r.status !== 200) {
        put(body, h('p', { class: 'notice notice-error', role: 'alert' }, 'We could not load your requests. Reload the page to try again.'));
        return;
      }
      const incoming = r.data.filter((q) => q.payer_id === me.user_id);
      const outgoing = r.data.filter((q) => q.requester_id === me.user_id);
      put(body, 
        !incoming.length && !outgoing.length ? h('div', { class: 'empty', 'data-testid': 'empty-requests' },
          h('p', { class: 'empty-title' }, 'No requests yet'),
          h('p', { class: 'muted' }, 'Ask someone for money from your ', h('a', { href: '/' }, 'wallet'), ', or ', h('a', { href: '/split' }, 'split a bill'), '.')) : null,
        h('div', { class: 'grid' },
          h('section', { class: 'card col', 'aria-labelledby': 'incoming-title' }, h('h2', { id: 'incoming-title' }, 'Asked of you'),
            h('ol', { class: 'list', 'data-testid': 'incoming-list' }, incoming.map((q) => requestItem(q, true))),
            incoming.length ? null : h('p', { class: 'muted list-empty' }, 'Nobody has asked you for money.')),
          h('section', { class: 'card col', 'aria-labelledby': 'outgoing-title' }, h('h2', { id: 'outgoing-title' }, 'You asked'),
            h('ol', { class: 'list', 'data-testid': 'outgoing-list' }, outgoing.map((q) => requestItem(q, false))),
            outgoing.length ? null : h('p', { class: 'muted list-empty' }, 'You have not asked anyone for money.'))));
    }

    function requestItem(q, incoming) {
      const pending = q.status === 'pending';
      const actions = [];
      if (pending && incoming) {
        actions.push(h('button', { type: 'button', class: 'btn btn-primary btn-small', 'data-testid': 'request-pay-' + q.request_id,
          onclick: () => act(q, 'pay') }, 'Pay ' + fmt(q.amount)));
        actions.push(h('button', { type: 'button', class: 'btn btn-secondary btn-small', 'data-testid': 'request-decline-' + q.request_id,
          onclick: () => act(q, 'decline') }, 'Decline'));
      }
      if (pending && !incoming) {
        actions.push(h('button', { type: 'button', class: 'btn btn-secondary btn-small', 'data-testid': 'request-cancel-' + q.request_id,
          onclick: () => act(q, 'cancel') }, 'Cancel request'));
      }
      return h('li', { class: 'item request-item status-' + q.status, 'data-testid': 'request-item-' + q.request_id, 'data-status': q.status },
        h('div', { class: 'item-main' },
          h('p', { class: 'item-kicker' }, incoming ? '@' + q.requester_handle + ' asked you' : 'You asked @' + q.payer_handle),
          q.note ? h('p', { class: 'note' }, q.note) : null,
          h('p', { class: 'meta' }, h('span', { class: 'badge badge-' + q.status }, REQUEST_WORDS[q.status] || q.status), h('span', {}, when(q.created_at))),
          actions.length ? h('div', { class: 'item-actions' }, actions) : null),
        h('p', { class: 'item-amount' }, h('span', { 'data-testid': 'request-amount-' + q.request_id }, fmt(q.amount))));
    }

    async function act(q, action) {
      if (busy) return;
      busy = true;
      fb.clear();
      for (const b of body.querySelectorAll('button')) b.disabled = true;
      let outcome;
      try {
        const r = action === 'pay'
          ? await api('POST', '/requests/' + encodeURIComponent(q.request_id) + '/pay', {}, newKey())
          : await api('POST', '/requests/' + encodeURIComponent(q.request_id) + '/' + action);
        if (r.status === 200 || r.status === 201) {
          const done = { pay: 'Paid ' + fmt(q.amount) + ' to @' + q.requester_handle + '.', decline: 'Request declined.', cancel: 'Request cancelled.' };
          outcome = ['request-success', 'success', done[action]];
        } else {
          outcome = ['request-error', 'error', explain(r)];
        }
      } catch (e) {
        outcome = ['request-error', 'uncertain', 'We could not confirm that. The list has been refreshed — check it before trying again.'];
      }
      // Show the outcome together with the refreshed list, never beside stale buttons.
      await load();
      fb.set(...outcome);
      busy = false;
    }

    await load();
  }

  // ------------------------------------------------------------ split screen

  function shares(amount, n) {
    const base = Math.floor(amount / n);
    const extra = amount - base * n;
    return Array.from({ length: n }, (_, i) => base + (i < extra ? 1 : 0));
  }

  async function splitScreen(me) {
    const main = shell(me);
    const amount = h('input', { type: 'text', inputmode: 'decimal', 'data-testid': 'split-amount', autocomplete: 'off' });
    const handles = h('input', { type: 'text', 'data-testid': 'split-handles', autocomplete: 'off', autocapitalize: 'none', spellcheck: 'false' });
    const note = h('input', { type: 'text', 'data-testid': 'split-note', maxlength: '200' });
    const preview = h('div', { class: 'preview', 'data-testid': 'split-preview', 'aria-live': 'polite' });
    const feedback = h('div', { class: 'feedback' });
    const fb = slot(feedback);
    const submit = h('button', { type: 'submit', class: 'btn btn-primary', 'data-testid': 'split-submit' }, 'Send requests');
    const form = h('form', { novalidate: true, class: 'stack' },
      field('split-amount-input', 'Total you paid (' + currency.code + ')', amount),
      field('split-handles-input', 'Who shared it', handles, 'Handles separated by commas, in order. Include yourself to take a share; earlier people get any odd ' + (currency.minor ? 'cent' : 'unit') + '.'),
      field('split-note-input', 'What it was for (optional)', note),
      h('div', {}, h('h3', { class: 'preview-title' }, 'Shares'), preview),
      feedback, h('div', { class: 'actions' }, submit));
    main.append(h('div', { class: 'page-head' }, h('h1', {}, 'Split a bill'),
      h('p', { class: 'muted' }, 'Everyone except you gets a request for their share. Nobody is charged until they pay.')),
    h('section', { class: 'card narrow' }, form));

    const parseHandles = () => handles.value.split(',').map((x) => x.trim()).filter((x) => x !== '');
    function renderPreview() {
      const minor = parseAmount(amount.value);
      const list = parseHandles();
      if (minor === null || !list.length) {
        put(preview, h('p', { class: 'muted' }, 'Enter the total and the people to see each share.'));
        return;
      }
      const s = shares(minor, list.length);
      put(preview, h('ul', { class: 'shares' }, list.map((hd, i) => h('li', {},
        h('span', { class: 'share-who' }, '@' + hd, hd === me.handle ? h('span', { class: 'you' }, ' (you)') : null),
        h('span', { class: 'share-amount', 'data-testid': 'split-share-' + hd }, fmt(s[i]))))));
    }
    amount.addEventListener('input', renderPreview);
    handles.addEventListener('input', renderPreview);
    renderPreview();

    const retry = retryIdentity();
    let busy = false;
    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      if (busy) return;
      const minor = parseAmount(amount.value);
      if (minor === null) {
        fb.set('split-error', 'error', amountProblem());
        return;
      }
      const body = { amount: minor, participant_handles: parseHandles(), note: note.value };
      const key = retry.keyFor(body);
      busy = true;
      submit.disabled = true;
      try {
        const r = await api('POST', '/splits', body, key);
        if (r.status === 200 || r.status === 201) {
          const asked = r.data.requests.map((q) => '@' + q.payer_handle + ' (' + fmt(q.amount) + ')');
          fb.set('split-success', 'success', asked.length ? 'Requests sent to ' + asked.join(', ') + '.' : 'Split recorded. Nobody else needed a request.');
        } else if (r.status >= 500) {
          fb.set('split-error', 'uncertain', 'We could not confirm the split. Press Send requests again to retry.');
        } else {
          fb.set('split-error', 'error', explain(r));
        }
      } catch (e) {
        fb.set('split-error', 'uncertain', 'We could not confirm the split. Press Send requests again to retry.');
      } finally {
        busy = false;
        submit.disabled = false;
      }
    });
  }

  // ------------------------------------------------------------ authorizations screen

  const AUTH_WORDS = { open: 'Open hold', captured: 'Captured', voided: 'Voided', expired: 'Expired' };

  async function authorizationsScreen(me) {
    const main = shell(me);
    const availableLine = h('p', { class: 'muted' }, '');
    const a = {
      handle: h('input', { type: 'text', 'data-testid': 'authorize-handle', autocomplete: 'off', autocapitalize: 'none', spellcheck: 'false' }),
      amount: h('input', { type: 'text', inputmode: 'decimal', 'data-testid': 'authorize-amount', autocomplete: 'off' }),
      note: h('input', { type: 'text', 'data-testid': 'authorize-note', maxlength: '200' }),
      visibility: visibilitySelect('authorize-visibility'),
    };
    const formFeedback = h('div', { class: 'feedback' });
    const formFb = slot(formFeedback);
    const submit = h('button', { type: 'submit', class: 'btn btn-primary', 'data-testid': 'authorize-submit' }, 'Place hold');
    const form = h('form', { novalidate: true, class: 'stack' },
      field('authorize-handle-input', 'Hold for (handle)', a.handle),
      field('authorize-amount-input', 'Amount (' + currency.code + ')', a.amount),
      field('authorize-note-input', 'Note (optional)', a.note),
      field('authorize-visibility-input', 'Who can see the payment', a.visibility),
      formFeedback, h('div', { class: 'actions' }, submit));
    const listFeedback = h('div', { class: 'feedback' });
    const listFb = slot(listFeedback);
    const listBody = h('div', {}, loadingBlock('Loading holds…'));
    main.append(h('div', { class: 'page-head' }, h('h1', {}, 'Holds'),
      h('p', { class: 'muted' }, 'Reserve money for someone to collect later. Held money stays in your wallet but cannot be spent.')),
    h('div', { class: 'grid' },
      h('section', { class: 'card col', 'aria-labelledby': 'authorize-title' }, h('h2', { id: 'authorize-title' }, 'Place a hold'), availableLine, form),
      h('section', { class: 'card col col-wide', 'aria-labelledby': 'holds-title' }, h('h2', { id: 'holds-title' }, 'Your holds'), listFeedback, listBody)));

    let generation = 0;
    let busy = false;
    async function load() {
      const mine = ++generation;
      const meP = api('GET', '/me').catch(() => null);
      let r;
      try { r = await getAll('/authorizations', 'authorizations'); } catch (e) { r = null; }
      const m = await meP;
      if (mine !== generation) return;
      if (m && m.status === 200) {
        put(availableLine, 'Available to hold: ', h('strong', {}, fmt(m.data.available)));
      }
      if (!r || r.status !== 200) {
        put(listBody, h('p', { class: 'notice notice-error', role: 'alert' }, 'We could not load your holds. Reload the page to try again.'));
        return;
      }
      if (!r.data.length) {
        put(listBody, h('div', { class: 'empty', 'data-testid': 'empty-authorizations' },
          h('p', { class: 'empty-title' }, 'No holds yet'),
          h('p', { class: 'muted' }, 'Holds you place, and holds others place for you, appear here.')));
        return;
      }
      put(listBody, h('ol', { class: 'list', 'data-testid': 'authorization-list' }, r.data.map(item)));
    }

    function item(x) {
      const id = x.authorization_id;
      const outgoing = x.from_user_id === me.user_id;
      const incoming = x.to_user_id === me.user_id;
      const open = x.status === 'open';
      const controls = [];
      if (incoming && open) {
        const input = h('input', { type: 'text', inputmode: 'decimal', 'data-testid': 'authorization-capture-amount-' + id, value: decimal(x.remaining_amount), autocomplete: 'off' });
        controls.push(h('div', { class: 'capture' },
          field('capture-' + id, 'Amount to collect', input),
          h('button', { type: 'button', class: 'btn btn-primary btn-small', 'data-testid': 'authorization-capture-' + id,
            onclick: () => capture(x, input) }, 'Collect')));
      }
      if (outgoing && open) {
        controls.push(h('button', { type: 'button', class: 'btn btn-secondary btn-small', 'data-testid': 'authorization-void-' + id,
          onclick: () => voidIt(x) }, 'Release hold'));
      }
      const who = outgoing ? 'You are holding for @' + x.to_handle : '@' + x.from_handle + ' is holding for you';
      const progress = [];
      if (open && x.captured_amount > 0) progress.push(fmt(x.captured_amount) + ' collected so far, ' + fmt(x.remaining_amount) + ' still held');
      else if (open) progress.push(fmt(x.remaining_amount) + ' held');
      if (x.status === 'voided' && x.captured_amount > 0) progress.push(fmt(x.captured_amount) + ' was collected before release');
      if (x.status === 'expired' && x.captured_amount > 0) progress.push(fmt(x.captured_amount) + ' was collected before it expired');
      return h('li', { class: 'item auth-item status-' + x.status, 'data-testid': 'authorization-item-' + id, 'data-status': x.status },
        h('div', { class: 'item-main' },
          h('p', { class: 'item-kicker' }, who),
          x.note ? h('p', { class: 'note' }, x.note) : null,
          h('p', { class: 'meta' },
            h('span', { class: 'badge badge-' + x.status }, AUTH_WORDS[x.status] || x.status),
            h('span', { class: 'badge badge-' + x.visibility }, x.visibility === 'private' ? 'Private' : 'Public'),
            x.status === 'captured' ? h('span', {}, 'Collected ', h('strong', { 'data-testid': 'authorization-captured-' + id }, fmt(x.captured_amount))) : null),
          progress.length ? h('p', { class: 'meta' }, progress.join(' · ')) : null,
          h('p', { class: 'meta' }, (open ? 'Expires ' + relative(x.expires_at) : x.status === 'expired' ? 'Expired ' + relative(x.expires_at) : 'Was due to expire') + ' · ',
            h('time', { class: 'stamp', datetime: x.expires_at, 'data-testid': 'authorization-expires-' + id }, x.expires_at)),
          controls.length ? h('div', { class: 'item-actions' }, controls) : null),
        h('p', { class: 'item-amount' }, h('span', { 'data-testid': 'authorization-amount-' + id }, fmt(x.amount))));
    }

    async function run(fn) {
      if (busy) return;
      busy = true;
      listFb.clear();
      for (const b of listBody.querySelectorAll('button')) b.disabled = true;
      let outcome;
      try {
        outcome = await fn();
      } catch (e) {
        outcome = ['authorization-error', 'uncertain', 'We could not confirm that. The list has been refreshed — check it before trying again.'];
      }
      // Show the outcome together with the refreshed list, never beside stale controls.
      await load();
      listFb.set(...outcome);
      busy = false;
    }

    function capture(x, input) {
      const minor = parseAmount(input.value);
      if (minor === null) {
        listFb.set('authorization-error', 'error', amountProblem());
        return;
      }
      run(async () => {
        const r = await api('POST', '/authorizations/' + encodeURIComponent(x.authorization_id) + '/capture', { amount: minor }, newKey());
        if (r.status === 200 || r.status === 201) return ['authorization-success', 'success', 'Collected ' + fmt(r.data.amount) + ' from @' + x.from_handle + '.'];
        return ['authorization-error', 'error', explain(r)];
      });
    }

    function voidIt(x) {
      run(async () => {
        const r = await api('POST', '/authorizations/' + encodeURIComponent(x.authorization_id) + '/void');
        if (r.status === 200) return ['authorization-success', 'success', 'Hold released. The money is available again.'];
        return ['authorization-error', 'error', explain(r)];
      });
    }

    const retry = retryIdentity();
    let formBusy = false;
    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      if (formBusy) return;
      const amount = parseAmount(a.amount.value);
      if (amount === null) {
        formFb.set('authorize-error', 'error', amountProblem());
        return;
      }
      const body = { to_handle: a.handle.value.trim(), amount, note: a.note.value, visibility: a.visibility.value };
      const key = retry.keyFor(body);
      formBusy = true;
      submit.disabled = true;
      try {
        const r = await api('POST', '/authorizations', body, key);
        if (r.status === 200 || r.status === 201) formFb.set('authorize-success', 'success', 'Holding ' + fmt(r.data.amount) + ' for @' + r.data.to_handle + '.');
        else if (r.status >= 500) formFb.set('authorize-error', 'uncertain', 'We could not confirm the hold. Press Place hold again to retry.');
        else formFb.set('authorize-error', 'error', explain(r));
      } catch (e) {
        formFb.set('authorize-error', 'uncertain', 'We could not confirm the hold. Press Place hold again to retry.');
      } finally {
        formBusy = false;
        submit.disabled = false;
      }
      await load();
    });

    await load();
  }

  // ------------------------------------------------------------ start

  async function signedIn(screen) {
    if (!getToken()) {
      location.replace('/login');
      return;
    }
    const boot = {};
    const meP = api('GET', '/me');
    if (screen === walletScreen) {
      boot.feed = getAll('/activity', 'payments');
      boot.feed.catch(() => {});
    }
    let r;
    try {
      r = await meP;
    } catch (e) {
      r = null;
    }
    if (!r || r.status !== 200) {
      put(app, h('main', { class: 'auth-page' }, h('section', { class: 'auth-card' },
        h('h1', {}, 'We could not reach Pocketful'),
        h('p', { class: 'notice notice-error', role: 'alert' }, 'Check your connection, then reload the page.'))));
      return;
    }
    currency = { code: r.data.currency, minor: r.data.minor_units };
    await screen(r.data, boot);
  }

  async function authRoute(kind) {
    let me = null;
    if (getToken()) {
      try {
        const r = await api('GET', '/me');
        if (r.status === 200) me = r.data;
      } catch (e) { /* show the plain form */ }
    }
    authScreen(kind, me);
  }

  const screens = { '/': walletScreen, '/requests': requestsScreen, '/split': splitScreen, '/authorizations': authorizationsScreen };
  if (route === '/login' || route === '/signup') authRoute(route.slice(1));
  else signedIn(screens[route] || walletScreen);
})();
