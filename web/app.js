/* P2P Myjot — клиент Telegram Mini App */
(() => {
  'use strict';

  // ------------------------------------------------------------------ Telegram
  const tg = window.Telegram && window.Telegram.WebApp;
  if (tg) {
    try {
      tg.ready();
      tg.expand();
      tg.setHeaderColor('#0d1220');
      tg.setBackgroundColor('#0d1220');
      if (tg.setBottomBarColor) tg.setBottomBarColor('#151c30');
    } catch (e) { /* старые клиенты Telegram */ }
  }
  const initData = (tg && tg.initData) || '';
  const devId = new URLSearchParams(location.search).get('dev'); // только для DEV_MODE на сервере
  const tgUser = (tg && tg.initDataUnsafe && tg.initDataUnsafe.user) ||
    (devId ? { id: Number(devId), first_name: 'Dev' } : null);
  // ID нужен только для адресов API. Доступ проверяет сервер по подписи initData.
  const UID = tgUser ? tgUser.id : 0;
  const TZ = -new Date().getTimezoneOffset();

  // ------------------------------------------------------------------ Утилиты
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

  const CURRENCIES = { all: ['RUB', 'USD', 'TON', 'USDT', 'BTC', 'ETH'], nft: ['TON', 'USD', 'RUB', 'USDT'] };
  const PAIRS = ['USD→TON', 'TON→USD', 'RUB→TON', 'TON→RUB', 'USDT→TON', 'TON→USDT', 'BTC→USDT', 'ETH→USDT'];
  const DEC = { RUB: 2, USD: 2, USDT: 2, TON: 4, BTC: 8, ETH: 6 };
  const TYPE_NAME = { p2p: 'P2P', nft: 'NFT', guarantee: 'Гарант', exchange: 'Обмен' };
  const NBSP = '\u00A0';

  function num(v, dec) {
    const n = Number(v);
    if (!Number.isFinite(n)) return '0';
    let s = Math.abs(n).toFixed(dec);
    if (s.indexOf('.') >= 0) s = s.replace(/0+$/, '').replace(/\.$/, '');
    let [int, frac] = s.split('.');
    int = int.replace(/\B(?=(\d{3})+(?!\d))/g, NBSP);
    return frac ? int + '.' + frac : int;
  }
  function money(v, cur, sign) {
    const n = Number(v) || 0;
    const body = num(n, DEC[cur] != null ? DEC[cur] : 2);
    const isZero = body === '0';
    const sg = isZero ? '' : (n < 0 ? '−' : (sign ? '+' : ''));
    if (cur === 'RUB') return sg + body + NBSP + '₽';
    if (cur === 'USD') return sg + '$' + body;
    return sg + body + NBSP + cur;
  }
  const pct = (v, sign) => { const n = Number(v) || 0; return (sign && n > 0 ? '+' : n < 0 ? '−' : '') + num(Math.abs(n), 2) + '%'; };
  const cls = (v) => (Number(v) > 0 ? 'pos' : Number(v) < 0 ? 'neg' : '');
  function fmtDate(iso) {
    const d = new Date(iso);
    const p = (x) => String(x).padStart(2, '0');
    return `${p(d.getDate())}.${p(d.getMonth() + 1)}.${d.getFullYear()} ${p(d.getHours())}:${p(d.getMinutes())}`;
  }
  function val(sel) {
    const s = $(sel).value.trim().replace(/\s/g, '').replace(',', '.');
    if (s === '') return null;
    const n = Number(s);
    return Number.isFinite(n) ? n : NaN;
  }
  const isNum = (n) => n !== null && !Number.isNaN(n);

  let toastTimer;
  function toast(msg, isErr) {
    const el = $('#toast');
    el.textContent = msg;
    el.classList.toggle('err', !!isErr);
    el.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove('show'), 2400);
    if (tg && tg.HapticFeedback) { try { isErr ? tg.HapticFeedback.notificationOccurred('error') : tg.HapticFeedback.notificationOccurred('success'); } catch (e) { /* noop */ } }
  }
  function ask(msg) {
    return new Promise((resolve) => {
      if (tg && tg.showConfirm) { try { tg.showConfirm(msg, (ok) => resolve(!!ok)); return; } catch (e) { /* fallthrough */ } }
      resolve(window.confirm(msg));
    });
  }
  function openUrl(url) {
    if (!/^https?:\/\//i.test(url)) return;
    if (tg && /^https:\/\/t\.me\//i.test(url) && tg.openTelegramLink) tg.openTelegramLink(url);
    else if (tg && tg.openLink) tg.openLink(url);
    else window.open(url, '_blank', 'noopener');
  }

  // ------------------------------------------------------------------ API
  const banner = $('#banner');
  function showBanner(text) { banner.textContent = text; banner.hidden = false; }
  if (!initData && !devId) showBanner('Откройте приложение через бота в Telegram — иначе данные не сохранятся');

  async function api(path, opts) {
    const { method = 'GET', body } = opts || {};
    const headers = {};
    if (initData) headers['X-Telegram-Init-Data'] = initData;
    else if (devId) headers['X-Dev-Telegram-Id'] = devId;
    if (body) headers['Content-Type'] = 'application/json';
    let res;
    try {
      res = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : undefined });
    } catch (e) {
      throw new Error('Нет соединения с сервером');
    }
    let data = null;
    try { data = await res.json(); } catch (e) { /* пустой ответ */ }
    if (!res.ok) {
      const msg = (data && data.detail) || `Ошибка сервера (${res.status})`;
      if (res.status === 401) showBanner(msg);
      throw new Error(typeof msg === 'string' ? msg : 'Проверьте введённые данные');
    }
    return data;
  }

  // ------------------------------------------------------------------ Состояние
  const S = { rates: null, fees: [], stats: null, chartPeriod: 'daily', chartMetric: 'profit', exFeeKind: 'auto' };
  const H = { type: 'all', period: 'all' };

  // ------------------------------------------------------------------ Элементы управления
  function fillSelects() {
    $$('select[data-cur]').forEach((sel) => {
      sel.innerHTML = CURRENCIES[sel.dataset.cur].map((c) => `<option value="${c}">${c}</option>`).join('');
    });
    $('#p2p-buy-cur').value = 'RUB'; $('#p2p-sell-cur').value = 'RUB';
    $('#nft-buy-cur').value = 'TON'; $('#nft-sell-cur').value = 'TON';
    $('#g-cur').value = 'RUB';
    $('#ex-pair').innerHTML = PAIRS.map((p) => `<option value="${p}">${p.replace('→', ' → ')}</option>`).join('');
  }
  function segVal(sel) { const b = $(sel + ' .on'); return b ? b.dataset.v : null; }
  function bindSeg(sel, cb) {
    $(sel).addEventListener('click', (e) => {
      const b = e.target.closest('button');
      if (!b) return;
      $$(sel + ' button').forEach((x) => x.classList.toggle('on', x === b));
      cb(b.dataset.v);
    });
  }
  function bindNumeric() {
    $$('input[inputmode="decimal"]').forEach((inp) => {
      inp.addEventListener('input', () => {
        const clean = inp.value.replace(/[^\d.,]/g, '');
        if (clean !== inp.value) inp.value = clean;
        inp.setAttribute('aria-invalid', Number.isNaN(val('#' + inp.id)) ? 'true' : 'false');
      });
    });
  }

  // ------------------------------------------------------------------ Результаты
  const rowHtml = (label, value, c, strong) =>
    `<div class="row${strong ? ' strong' : ''}"><span>${label}</span><b class="${c || ''}">${value}</b></div>`;
  const staleNote = (r) => (r && r.stale ? '<p class="muted small" style="margin-top:10px">⚠️ Курс не обновлён — использован последний сохранённый</p>' : '');

  function makePreview(boxSel, emptyText, getBody, endpoint, render) {
    let n = 0;
    return debounce(async () => {
      const el = $(boxSel);
      const body = getBody();
      if (!body) { n++; el.className = 'result empty'; el.textContent = emptyText; return; }
      const id = ++n;
      try {
        const r = await api(endpoint, { method: 'POST', body: Object.assign({}, body, { save: false }) });
        if (id !== n) return;
        el.className = 'result';
        el.innerHTML = render(r.result, body) + staleNote(r);
      } catch (e) {
        if (id !== n) return;
        el.className = 'result empty';
        el.innerHTML = `<span class="neg">${esc(e.message)}</span>`;
      }
    }, 350);
  }
  async function saveOp(btn, body, endpoint, onDone) {
    if (!body) { toast('Заполните все обязательные поля', true); return; }
    btn.disabled = true;
    try {
      await api(endpoint, { method: 'POST', body: Object.assign({}, body, { save: true }) });
      toast('Сохранено');
      S.stats = null;
      if (onDone) onDone();
    } catch (e) { toast(e.message, true); }
    btn.disabled = false;
  }

  // ------------------------------------------------------------------ P2P
  function p2pMode() { return segVal('#p2p-mode') || 'percent'; }
  function p2pUnits() {
    const fixed = p2pMode() === 'fixed';
    $$('.unit[data-unit="buy"]').forEach((u) => { u.textContent = fixed ? $('#p2p-buy-cur').value : '%'; });
    $$('.unit[data-unit="sell"]').forEach((u) => { u.textContent = fixed ? $('#p2p-sell-cur').value : '%'; });
  }
  function p2pBody() {
    const buy = val('#p2p-buy'); const sell = val('#p2p-sell');
    const bf = val('#p2p-buyfee'); const sf = val('#p2p-sellfee'); const ex = val('#p2p-extra');
    if (!isNum(buy) || buy <= 0 || !isNum(sell)) return null;
    if ([bf, sf, ex].some(Number.isNaN)) return null;
    return {
      buy_amount: buy, buy_currency: $('#p2p-buy-cur').value,
      sell_amount: sell, sell_currency: $('#p2p-sell-cur').value,
      buy_fee: bf || 0, sell_fee: sf || 0, extra_fee: ex || 0, fee_mode: p2pMode(),
    };
  }
  function renderP2P(r) {
    const c = r.currency;
    let h = `<div class="headline">💰 Чистая прибыль</div><div class="value ${cls(r.profit)}">${money(r.profit, c, true)}</div><div class="rows">`;
    h += rowHtml('📈 ROI', pct(r.roi, true), cls(r.roi));
    h += rowHtml('💵 Оборот', money(r.turnover, c));
    h += rowHtml('Сумма до комиссии', money(r.sell_in_buy_currency, c));
    if (r.buy_fee > 0) h += rowHtml('Комиссия покупки', money(r.buy_fee, c));
    if (r.sell_fee > 0) h += rowHtml('Комиссия продажи', money(r.sell_fee, r.sell_fee_currency));
    if (r.extra_fee > 0) h += rowHtml('Дополнительная комиссия', money(r.extra_fee, r.sell_fee_currency));
    h += rowHtml('💳 Комиссии', money(r.fees_total, c));
    h += rowHtml('💰 Итог после комиссии', money(r.total_after_fees, c), '', true);
    return h + '</div>';
  }
  const p2pPreview = makePreview('#p2p-result', 'Введите суммы — результат появится здесь', p2pBody, '/api/calculate', renderP2P);

  // ------------------------------------------------------------------ NFT
  function nftHtml(it, withActions) {
    const link = it.link && /^https?:\/\//i.test(it.link) ? it.link : null;
    let h = `<div class="op nft-card"><div class="name">🖼 ${esc(it.name)}</div><div class="rows">`;
    h += rowHtml('Покупка', money(it.buy_price, it.buy_currency));
    h += rowHtml('Продажа', money(it.sell_price, it.sell_currency));
    h += rowHtml('💰 Прибыль', money(it.profit, it.buy_currency, true), cls(it.profit), true);
    h += rowHtml('📈 ROI', pct(it.roi, true), cls(it.roi));
    h += rowHtml('Дата', fmtDate(it.created_at));
    h += '</div>';
    if (link || withActions) {
      h += '<div class="op-actions">';
      if (link) h += `<button class="btn ghost sm" data-open="${esc(link)}">🔗 Открыть NFT</button>`;
      if (withActions) h += `<button class="btn danger sm" data-del="nft:${it.id}">Удалить</button>`;
      h += '</div>';
    }
    return h + '</div>';
  }
  async function loadNft() {
    const box = $('#nft-list');
    try {
      const r = await api(`/api/nft/${UID}`);
      box.innerHTML = r.items.length ? r.items.map((i) => nftHtml(i, true)).join('') : '<div class="empty-note">Пока нет сохранённых NFT</div>';
    } catch (e) { box.innerHTML = `<div class="empty-note neg">${esc(e.message)}</div>`; }
  }
  async function saveNft() {
    const name = $('#nft-name').value.trim();
    const buy = val('#nft-buy'); const sell = val('#nft-sell');
    if (!name) { toast('Введите название NFT', true); return; }
    if (!isNum(buy) || !isNum(sell)) { toast('Введите цену покупки и продажи', true); return; }
    const btn = $('#nft-save');
    btn.disabled = true;
    try {
      const r = await api('/api/nft', {
        method: 'POST',
        body: {
          name, link: $('#nft-link').value.trim() || null,
          buy_price: buy, buy_currency: $('#nft-buy-cur').value,
          sell_price: sell, sell_currency: $('#nft-sell-cur').value,
        },
      });
      $('#nft-result').innerHTML = '<div style="margin-top:14px">' + nftHtml(r.item, false) + staleNote(r) + '</div>';
      ['#nft-name', '#nft-link', '#nft-buy', '#nft-sell'].forEach((s) => { $(s).value = ''; });
      S.stats = null;
      toast('NFT сохранён');
      loadNft();
    } catch (e) { toast(e.message, true); }
    btn.disabled = false;
  }

  // ------------------------------------------------------------------ Гарант
  function gBody() {
    const amount = val('#g-amount'); const pp = val('#g-profit'); const fee = val('#g-fee');
    if (!isNum(amount) || amount <= 0 || !isNum(pp) || Number.isNaN(fee)) return null;
    return { amount, currency: $('#g-cur').value, profit_percent: pp, fee_percent: fee || 0, mode: segVal('#g-mode') || 'included' };
  }
  function renderG(r, body) {
    const c = r.currency;
    const onTop = body.mode === 'on_top';
    let h = `<div class="headline">🛡 ${onTop ? 'Клиент платит' : 'Получите'}</div><div class="value">${money(r.total, c)}</div><div class="rows">`;
    h += rowHtml('💰 Прибыль', money(r.profit, c), cls(r.profit));
    h += rowHtml('🛡 Комиссия', money(r.guarantee_fee, c));
    h += rowHtml(onTop ? '💵 Клиент платит' : '💵 Получите', money(r.total, c), '', true);
    h += rowHtml('Чистая прибыль', money(r.net_profit, c, true), cls(r.net_profit));
    return h + '</div>';
  }
  const gPreview = makePreview('#g-result', 'Введите сумму — результат появится здесь', gBody, '/api/guarantee', renderG);
  function opMini(it) { return opHtml(it, false); }
  async function loadGuarantees() {
    const box = $('#g-list');
    try {
      const r = await api(`/api/guarantee/${UID}?limit=5`);
      box.innerHTML = r.items.length ? r.items.map(opMini).join('') : '<div class="empty-note">Сохранённых гарант-сделок пока нет</div>';
    } catch (e) { box.innerHTML = `<div class="empty-note neg">${esc(e.message)}</div>`; }
  }

  // ------------------------------------------------------------------ Обмен
  function exService() { return segVal('#ex-service') || 'Send'; }
  function exFeeKind() { return exService() === 'Своя комиссия' ? 'custom' : S.exFeeKind; }
  function feeFor(service, pair) {
    const f = S.fees.find((x) => x.service === service && x.pair === pair);
    return f ? f.fee_percent : null;
  }
  function autoRate(from, to) {
    if (!S.rates) return null;
    const usd = (c) => {
      if (c === 'USD') return 1;
      if (c === 'RUB') return 1 / S.rates.usd_rub;
      return S.rates.coins[c] ? S.rates.coins[c].usd : null;
    };
    const a = usd(from); const b = usd(to);
    return a && b ? a / b : null;
  }
  function exUi() {
    const [from, to] = $('#ex-pair').value.split('→');
    $('#ex-from-unit').textContent = from;
    $('#ex-rate-unit').textContent = `(${to} за 1 ${from})`;
    const ar = autoRate(from, to);
    $('#ex-rate').placeholder = ar ? `Авто ≈ ${num(ar, ar < 1 ? 6 : 4)}` : 'Авто';
    const custom = exService() === 'Своя комиссия';
    $('#ex-feekind-wrap').hidden = custom;
    $('#ex-fee-wrap').hidden = exFeeKind() === 'auto';
  }
  function exBody() {
    const amount = val('#ex-amount'); const rate = val('#ex-rate'); const fee = val('#ex-fee');
    if (!isNum(amount) || amount <= 0 || Number.isNaN(rate) || Number.isNaN(fee)) return null;
    let feePercent = null;
    if (exFeeKind() === 'custom') feePercent = fee || 0;
    return {
      service: exService(), pair: $('#ex-pair').value, amount,
      rate: rate && rate > 0 ? rate : null, fee_percent: feePercent, fee_mode: segVal('#ex-mode') || 'deducted',
    };
  }
  function renderEx(r, body) {
    const src = r.from; const dst = r.to;
    let h = `<div class="headline">Итого</div><div class="value">${money(r.result, dst)}</div><div class="rows">`;
    h += rowHtml('Получите', money(r.gross, dst));
    h += rowHtml('Комиссия', num(r.fee_percent, 4) + '%');
    h += rowHtml('Комиссия', money(r.fee_amount, dst));
    h += rowHtml('Курс', `1 ${src} = ${num(r.rate, r.rate < 1 ? 8 : 4)} ${dst}${r.auto_rate ? ' (авто)' : ''}`);
    if (body && body.fee_mode === 'added') h += rowHtml('Заплатите', money(r.pay_total, src));
    h += rowHtml('Итого', money(r.result, dst), '', true);
    if (r.fee_source === 'none') h += `<p class="muted small" style="margin-top:10px">Для этой пары нет комиссии в таблице — считаем 0%.</p>`;
    return h + '</div>';
  }
  const exPreview = makePreview('#ex-result', 'Введите сумму — результат появится здесь', exBody, '/api/exchange', renderEx);

  function updateExFeeHint() {
    const kind = exFeeKind();
    $('#ex-fee-wrap').hidden = kind === 'auto';
    const hint = $('#ex-fee-hint');
    if (kind === 'auto') {
      const f = feeFor(exService(), $('#ex-pair').value);
      hint.textContent = f === null ? '' : `Автоматическая комиссия: ${num(f, 4)}%`;
    } else hint.textContent = '';
  }
  async function loadExchanges() {
    const box = $('#ex-list');
    try {
      const r = await api(`/api/exchange/${UID}?limit=5`);
      box.innerHTML = r.items.length ? r.items.map(opMini).join('') : '<div class="empty-note">Сохранённых обменов пока нет</div>';
    } catch (e) { box.innerHTML = `<div class="empty-note neg">${esc(e.message)}</div>`; }
  }

  // ------------------------------------------------------------------ Курсы
  async function loadRates() {
    try {
      const r = await api('/api/rates');
      S.rates = r;
      renderRates();
      exUi();
    } catch (e) {
      $('#rates-warning').hidden = false;
      $('#rates-warning').textContent = '⚠️ Не удалось обновить курс';
    }
  }
  function renderRates() {
    const r = S.rates;
    if (!r) return;
    $('#rates-warning').hidden = !r.stale;
    if (r.stale) $('#rates-warning').textContent = r.warning || '⚠️ Не удалось обновить курс';
    const order = ['TON', 'USDT', 'BTC', 'ETH'];
    $('#rates-list').innerHTML = order.map((sym) => {
      const c = r.coins[sym];
      if (!c) return '';
      const usd = num(c.usd, c.usd < 1 ? 4 : 2);
      const rub = num(c.rub, c.rub < 1 ? 4 : 2);
      const ch = Number(c.change_24h) || 0;
      const sub = sym === 'TON' ? 'TON → USD, TON → RUB' : `${sym} → USD, ${sym} → RUB`;
      return `<div class="op rate-card"><div><div class="sym">${sym}</div><div class="op-sub">${sub}</div></div>` +
        `<div class="nums"><div>$${NBSP}${usd}</div><div>₽${NBSP}${rub}</div><div class="${cls(ch)} small">24h: ${pct(ch, true)}</div></div></div>`;
    }).join('');
    $('#rates-updated').textContent = r.updated_at ? 'Обновлено: ' + fmtDate(r.updated_at) + ' · источник CoinGecko' : 'Курсы ещё не загружались';
  }

  // ------------------------------------------------------------------ Статистика
  async function loadStats() {
    try {
      S.stats = await api(`/api/stats/${UID}?tz=${TZ}`);
    } catch (e) { toast(e.message, true); return null; }
    return S.stats;
  }
  function renderHome() {
    const s = S.stats;
    if (!s) return;
    const p = s.total_profit_rub;
    $('#home-profit').innerHTML = `<span class="cur">₽</span>${p < 0 ? '−' : ''}${num(Math.abs(p), 2)}`;
    $('#home-deals').textContent = s.total_deals;
    $('#home-nft').textContent = s.nft_count;
    $('#home-ex').textContent = s.exchange_count;
    $('#home-profit').className = 'hero-num ' + cls(p);
  }
  function tile(label, value, c, big) {
    return `<div class="tile"><span class="tile-label">${label}</span><b class="${c || ''}${big ? ' big' : ''}">${value}</b></div>`;
  }
  function renderStats() {
    const s = S.stats;
    if (!s) return;
    const best = s.best ? `${money(s.best.profit_rub, 'RUB', true)}<br><span class="muted small">${TYPE_NAME[s.best.type]}</span>` : '—';
    $('#stats-tiles').innerHTML =
      tile('📊 Всего сделок', s.total_deals) +
      tile('💰 Всего прибыли', money(s.total_profit_rub, 'RUB', true), cls(s.total_profit_rub), true) +
      tile('💵 Всего оборота', money(s.total_turnover_rub, 'RUB'), '', true) +
      tile('📈 Средний ROI', pct(s.avg_roi, true), cls(s.avg_roi)) +
      tile('🏆 Лучший результат', best, s.best ? cls(s.best.profit_rub) : '', true) +
      tile('🖼 Количество NFT', s.nft_count) +
      tile('🖼 Прибыль NFT', money(s.nft_profit_rub, 'RUB', true), cls(s.nft_profit_rub), true) +
      tile('🔄 Обменов', s.exchange_count);
    const names = { p2p: '💰 P2P', nft: '🖼 NFT', guarantee: '🛡 Гарант', exchange: '🔄 Обмен' };
    $('#stats-breakdown').innerHTML = Object.keys(names).map((k) => {
      const b = s.breakdown[k];
      return `<div class="bd-row"><span>${names[k]}</span><b class="${cls(b.profit_rub)}">${money(b.profit_rub, 'RUB', true)}</b>` +
        `<span class="muted">Операций: ${b.count} · оборот ${money(b.turnover_rub, 'RUB')}</span></div>`;
    }).join('');
    renderChart();
  }
  function compact(v, isCount) {
    if (isCount) return String(Math.round(v));
    const a = Math.abs(v);
    const sg = v < 0 ? '−' : '';
    if (a >= 1e6) return sg + (a / 1e6).toFixed(1).replace('.0', '') + 'M';
    if (a >= 1e3) return sg + (a / 1e3).toFixed(1).replace('.0', '') + 'K';
    return sg + String(Math.round(a));
  }
  function renderChart() {
    const s = S.stats;
    const box = $('#chart');
    if (!s) { box.innerHTML = ''; return; }
    const metric = S.chartMetric;
    const series = (S.chartPeriod === 'daily' ? s.daily : s.weekly).map((p) => ({ date: p.date, v: p[metric] }));
    if (series.every((p) => p.v === 0)) { box.innerHTML = '<div class="empty-note">Пока нет данных для графика</div>'; return; }
    const W = 320; const Hh = 170; const top = 16; const bottom = 18; const plotH = Hh - top - bottom;
    const max = Math.max(0, ...series.map((p) => p.v));
    const min = Math.min(0, ...series.map((p) => p.v));
    const span = max - min || 1;
    const y = (v) => top + ((max - v) / span) * plotH;
    const y0 = y(0);
    const slot = W / series.length; const bw = Math.max(2, slot * 0.66);
    const isCount = metric === 'count';
    let svg = `<svg viewBox="0 0 ${W} ${Hh}" role="img" aria-label="График">`;
    svg += `<line class="axis" x1="0" x2="${W}" y1="${y0}" y2="${y0}"/>`;
    svg += `<text x="0" y="10">${compact(max, isCount)}${metric === 'count' ? '' : ' ₽'}</text>`;
    series.forEach((p, i) => {
      const x = i * slot + (slot - bw) / 2;
      const yy = y(p.v);
      const h = Math.max(Math.abs(yy - y0), p.v === 0 ? 0 : 1);
      const yTop = p.v >= 0 ? y0 - h : y0;
      const c = metric === 'profit' ? (p.v >= 0 ? 'bar-pos' : 'bar-neg') : 'bar-acc';
      if (p.v !== 0) svg += `<rect class="${c}" x="${x.toFixed(1)}" y="${yTop.toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}" rx="2"/>`;
    });
    const lab = (i, anchor) => {
      const d = series[i].date.split('-');
      const x = i * slot + slot / 2;
      return `<text x="${x.toFixed(1)}" y="${Hh - 4}" text-anchor="${anchor}">${d[2]}.${d[1]}</text>`;
    };
    svg += lab(0, 'start') + lab(Math.floor(series.length / 2), 'middle') + lab(series.length - 1, 'end');
    box.innerHTML = svg + '</svg>';
  }

  // ------------------------------------------------------------------ История
  function opHtml(it, withActions) {
    let title; let amount; let sub; let ac = '';
    if (it.type === 'p2p') {
      title = '💰 P2P сделка'; amount = money(it.profit, it.currency, true); ac = cls(it.profit);
      sub = `${money(it.amount, it.currency)} → ${money(it.sell_amount, it.sell_currency)}`;
    } else if (it.type === 'nft') {
      title = '🖼 NFT · ' + esc(it.name); amount = money(it.profit, it.currency, true); ac = cls(it.profit);
      sub = `${money(it.buy_price, it.buy_currency)} → ${money(it.sell_price, it.sell_currency)}`;
    } else if (it.type === 'guarantee') {
      title = '🛡 Гарант сделка'; amount = money(it.profit, it.currency, true); ac = cls(it.profit);
      sub = `${money(it.amount, it.currency)} · комиссия ${num(it.fee_percent, 2)}% (${money(it.guarantee_fee, it.currency)})`;
    } else {
      title = '🔄 Обмен · ' + esc(it.service); amount = money(it.result, it.to);
      sub = `${money(it.amount, it.from)} → ${money(it.result, it.to)} · ${esc(it.pair.replace('→', ' → '))}`;
    }
    const link = it.type === 'nft' && it.link && /^https?:\/\//i.test(it.link) ? it.link : null;
    let h = `<div class="op"><div class="op-head"><span class="op-title">${title}</span><span class="op-amount ${ac}">${amount}</span></div>`;
    h += `<div class="op-sub">${sub}</div>`;
    if (it.roi !== null && it.roi !== undefined && it.type !== 'exchange') h += `<div class="op-sub">ROI ${pct(it.roi, true)}</div>`;
    h += `<div class="op-sub">${fmtDate(it.created_at)}</div>`;
    if (withActions) {
      h += '<div class="op-actions">';
      if (link) h += `<button class="btn ghost sm" data-open="${esc(link)}">Открыть</button>`;
      h += `<button class="btn danger sm" data-del="${it.type}:${it.id}">Удалить</button></div>`;
    }
    return h + '</div>';
  }
  async function loadHistory() {
    const box = $('#hist-list');
    try {
      const r = await api(`/api/history/${UID}?type=${H.type}&period=${H.period}&tz=${TZ}`);
      box.innerHTML = r.items.length ? r.items.map((i) => opHtml(i, true)).join('') : '<div class="empty-note">Операций не найдено</div>';
    } catch (e) { box.innerHTML = `<div class="empty-note neg">${esc(e.message)}</div>`; }
  }
  async function deleteItem(type, id) {
    if (!(await ask('Удалить запись?'))) return;
    try {
      if (type === 'nft') await api(`/api/nft/${id}`, { method: 'DELETE' });
      else await api(`/api/history/${UID}/${type}/${id}`, { method: 'DELETE' });
      S.stats = null;
      toast('Удалено');
      refreshCurrent();
    } catch (e) { toast(e.message, true); }
  }

  // ------------------------------------------------------------------ Профиль
  function renderProfile() {
    const u = tgUser || {};
    const name = [u.first_name, u.last_name].filter(Boolean).join(' ') || 'Гость';
    $('#pf-name').textContent = name;
    $('#pf-user').textContent = u.username ? '@' + u.username : '';
    $('#pf-id').textContent = u.id ? 'ID ' + u.id : '';
    $('#pf-avatar').textContent = name.charAt(0).toUpperCase();
  }

  // ------------------------------------------------------------------ Навигация
  let current = 'home';
  const enter = {
    home: async () => { if (!S.stats) await loadStats(); renderHome(); },
    nft: loadNft,
    guarantee: loadGuarantees,
    exchange: () => { loadExchanges(); if (!S.rates) loadRates(); },
    rates: loadRates,
    stats: async () => { if (!S.stats) await loadStats(); renderStats(); },
    history: loadHistory,
    profile: renderProfile,
  };
  function refreshCurrent() { if (enter[current]) enter[current](); }
  function go(page) {
    current = page;
    $$('.page').forEach((p) => p.classList.toggle('active', p.id === 'page-' + page));
    $$('#nav button').forEach((b) => {
      const on = b.dataset.page === page;
      b.classList.toggle('on', on);
      if (on) b.scrollIntoView({ inline: 'center', block: 'nearest', behavior: 'smooth' });
    });
    window.scrollTo(0, 0);
    refreshCurrent();
  }

  // ------------------------------------------------------------------ Инициализация
  function init() {
    fillSelects();
    bindNumeric();
    renderProfile();
    p2pUnits();

    $('#nav').addEventListener('click', (e) => { const b = e.target.closest('button'); if (b) go(b.dataset.page); });
    $$('[data-go]').forEach((b) => b.addEventListener('click', () => go(b.dataset.go)));

    // P2P
    bindSeg('#p2p-mode', () => { p2pUnits(); p2pPreview(); });
    ['#p2p-buy', '#p2p-sell', '#p2p-buyfee', '#p2p-sellfee', '#p2p-extra'].forEach((s) => $(s).addEventListener('input', p2pPreview));
    ['#p2p-buy-cur', '#p2p-sell-cur'].forEach((s) => $(s).addEventListener('change', () => { p2pUnits(); p2pPreview(); }));
    $('#p2p-save').addEventListener('click', () => saveOp($('#p2p-save'), p2pBody(), '/api/calculate'));

    // NFT
    $('#nft-save').addEventListener('click', saveNft);

    // Гарант
    bindSeg('#g-mode', gPreview);
    ['#g-amount', '#g-profit', '#g-fee'].forEach((s) => $(s).addEventListener('input', gPreview));
    $('#g-cur').addEventListener('change', gPreview);
    $('#g-save').addEventListener('click', () => saveOp($('#g-save'), gBody(), '/api/guarantee', loadGuarantees));

    // Обмен
    bindSeg('#ex-service', () => { exUi(); updateExFeeHint(); exPreview(); });
    bindSeg('#ex-feekind', (v) => { S.exFeeKind = v; exUi(); updateExFeeHint(); exPreview(); });
    bindSeg('#ex-mode', exPreview);
    $('#ex-pair').addEventListener('change', () => { exUi(); updateExFeeHint(); exPreview(); });
    ['#ex-amount', '#ex-rate', '#ex-fee'].forEach((s) => $(s).addEventListener('input', exPreview));
    $('#ex-save').addEventListener('click', () => saveOp($('#ex-save'), exBody(), '/api/exchange', loadExchanges));
    exUi(); updateExFeeHint();

    // Курсы
    $('#rates-refresh').addEventListener('click', async () => { await loadRates(); toast('Курсы обновлены'); });
    setInterval(() => { if (current === 'rates' && !document.hidden) loadRates(); }, 60000);

    // Статистика
    bindSeg('#chart-period', (v) => { S.chartPeriod = v; renderChart(); });
    bindSeg('#chart-metric', (v) => { S.chartMetric = v; renderChart(); });

    // История
    bindSeg('#hist-type', (v) => { H.type = v; loadHistory(); });
    bindSeg('#hist-period', (v) => { H.period = v; loadHistory(); });

    // Профиль
    $('#pf-clear').addEventListener('click', async () => {
      if (!(await ask('Удалить всю историю операций? Это действие нельзя отменить.'))) return;
      try {
        const r = await api(`/api/history/${UID}`, { method: 'DELETE' });
        S.stats = null;
        toast(`Удалено записей: ${r.deleted}`);
      } catch (e) { toast(e.message, true); }
    });

    // Открытие ссылок и удаление записей (делегирование)
    document.addEventListener('click', (e) => {
      const o = e.target.closest('[data-open]');
      if (o) { openUrl(o.dataset.open); return; }
      const d = e.target.closest('[data-del]');
      if (d) { const [type, id] = d.dataset.del.split(':'); deleteItem(type, Number(id)); }
    });

    // Фоновая подгрузка курсов и таблицы комиссий
    loadRates();
    api('/api/fees').then((f) => { S.fees = f; updateExFeeHint(); }).catch(() => {});
    go('home');
  }

  init();
})();
