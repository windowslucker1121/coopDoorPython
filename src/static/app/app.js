/* Dinky Coop — web UI (single-page app, no build step).
 *
 * Live data arrives on the Socket.IO "data" event (every second).  Commands
 * are Socket.IO events with acknowledgements ({ok, error}); settings that
 * have REST endpoints use fetch().  Each page is an object with render()
 * (static markup), enter()/leave() (timers, one-off loads) and update(d)
 * (applies the latest live data).
 */
(function () {
  'use strict';

  // ── helpers ──────────────────────────────────────────────────────────
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const MASK = '********';
  const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  const ICONS = {
    home: '<path d="M3 10.5 12 3l9 7.5V21h-6v-6H9v6H3z"/>',
    thermo: '<path d="M14 14.8V4a2 2 0 0 0-4 0v10.8a4 4 0 1 0 4 0z"/>',
    chart: '<path d="M3 3v18h18M7 15l4-4 3 3 5-6"/>',
    camera: '<path d="M23 7l-7 5 7 5V7z"/><rect x="1" y="5" width="15" height="14" rx="2"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    wifi: '<path d="M5 12.6a10 10 0 0 1 14 0M8.5 16a5 5 0 0 1 7 0M2 8.8a15 15 0 0 1 20 0M12 20h.01"/>',
    chip: '<rect x="5" y="5" width="14" height="14" rx="2"/><path d="M9 1v4M15 1v4M9 19v4M15 19v4M1 9h4M1 15h4M19 9h4M19 15h4"/>',
    list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
    sliders: '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/>',
    menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
    up: '<path d="M12 19V5M5 12l7-7 7 7"/>',
    down: '<path d="M12 5v14M19 12l-7 7-7-7"/>',
    stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
    hand: '<path d="M18 11V6a2 2 0 0 0-4 0v5M14 10V4a2 2 0 0 0-4 0v6M10 10.5V6a2 2 0 0 0-4 0v8a8 8 0 0 0 16 0v-3a2 2 0 0 0-4 0"/>',
    alert: '<path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
    expand: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
    refresh: '<path d="M21 12a9 9 0 1 1-2.6-6.4M21 3v6h-6"/>',
    lock: '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
    x: '<path d="M18 6 6 18M6 6l12 12"/>',
    chevron: '<path d="m9 6 6 6-6 6"/>',
  };
  const icon = (name, size = 20, sw = 1.9) =>
    `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${sw}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name]}</svg>`;

  // ── state ────────────────────────────────────────────────────────────
  const S = {
    data: null,          // last "data" payload
    settings: null,      // /api/settings
    connected: false,
    disconnectedAt: Date.now(),
    page: null,
    pageName: null,
    camFrame: null,
    camFrames: [],
    dismissed: {},
    installPrompt: null,
  };

  // ── toasts ───────────────────────────────────────────────────────────
  function toast(message, kind = 'ok', ms = 3500) {
    const el = document.createElement('div');
    el.className = 'toast';
    el.dataset.kind = kind;
    el.innerHTML = `${icon(kind === 'error' ? 'alert' : 'check', 18, 2.2)}<span>${esc(message)}</span>`;
    $('#toasts').appendChild(el);
    setTimeout(() => el.remove(), ms);
  }

  // ── dialog ───────────────────────────────────────────────────────────
  function confirmDialog({ title, body, confirm = 'Confirm', danger = false, cancel = 'Cancel' }) {
    const dlg = $('#dialog');
    dlg.innerHTML = `<form method="dialog" class="dlg">
      <h2>${esc(title)}</h2>${body ? `<p>${body}</p>` : ''}
      <div class="form-actions">
        <button class="btn" value="cancel" type="submit">${esc(cancel)}</button>
        <button class="btn ${danger ? 'btn-danger' : 'btn-primary'}" value="ok" type="submit" data-confirm>${esc(confirm)}</button>
      </div></form>`;
    return new Promise((resolve) => {
      dlg.addEventListener('close', () => resolve(dlg.returnValue === 'ok'), { once: true });
      dlg.returnValue = '';
      dlg.showModal();
      $('[data-confirm]', dlg).focus();
    });
  }

  function overlay(title, text) {
    const el = document.createElement('div');
    el.className = 'overlay';
    el.innerHTML = `<div class="card" role="alertdialog" aria-live="assertive"><span class="btn" style="background:none;min-height:0"><span class="spin" style="width:32px;height:32px"></span></span><h2>${esc(title)}</h2><p class="muted">${esc(text)}</p></div>`;
    document.body.appendChild(el);
    return el;
  }

  // ── network ──────────────────────────────────────────────────────────
  const socket = io({ transports: ['websocket', 'polling'] });

  function ack(event, payload, timeout = 8000) {
    return new Promise((resolve) => {
      let done = false;
      const timer = setTimeout(() => { if (!done) { done = true; resolve({ ok: false, error: 'No answer from the coop controller' }); } }, timeout);
      const cb = (res) => { if (done) return; done = true; clearTimeout(timer); resolve(res || { ok: true }); };
      if (!S.connected) { clearTimeout(timer); resolve({ ok: false, error: 'Not connected to the coop controller' }); return; }
      if (payload === undefined) socket.emit(event, cb); else socket.emit(event, payload, cb);
    });
  }

  async function api(path, options = {}) {
    const opts = { headers: { Accept: 'application/json' }, ...options };
    if (opts.body && typeof opts.body !== 'string') {
      opts.body = JSON.stringify(opts.body);
      opts.headers['Content-Type'] = 'application/json';
    }
    let res;
    try { res = await fetch(path, opts); } catch (e) { throw new Error('Network error - is the coop controller reachable?'); }
    let body = null;
    try { body = await res.json(); } catch (e) { /* not JSON */ }
    if (!res.ok) throw new Error((body && body.error) || `Request failed (${res.status})`);
    return body;
  }

  async function loadSettings() {
    try { S.settings = await api('/api/settings'); } catch (e) { /* retried on next connect */ }
    return S.settings;
  }

  // Button busy state around an async action.
  async function busy(btn, fn) {
    if (!btn) return fn();
    if (btn.dataset.busy) return new Promise(() => {});  // ignore double clicks
    btn.dataset.busy = '1';
    const html = btn.innerHTML;
    btn.setAttribute('aria-busy', 'true');
    btn.innerHTML = '<span class="spin"></span>' + btn.textContent.trim();
    try { return await fn(); } finally {
      btn.innerHTML = html;
      btn.removeAttribute('aria-busy');
      delete btn.dataset.busy;
    }
  }

  // ── formatting ───────────────────────────────────────────────────────
  const STATE_LABEL = { open: 'Open', closed: 'Closed', opening: 'Opening…', closing: 'Closing…', stopped: 'Stopped' };
  const MODE_LABEL = { manual: 'Manual', auto: 'Sun', timer: 'Timer' };
  const deg = (s) => (s ? String(s).replace('°C', '°') : '—');
  const pct = (s) => (s ? String(s).replace('%', ' %') : '—');
  const num = (s) => { const n = parseFloat(String(s ?? '').replace(/[^\d.\-]/g, '')); return Number.isFinite(n) ? n : null; };

  // "6:51:56 AM" / "18:52:10" → "06:51" / "18:52" (the UI uses 24 h HH:MM everywhere)
  function hhmm(t) {
    const m = /^(\d{1,2}):(\d{2})(?::\d{2})?\s*([AP]M)?$/i.exec(String(t || '').trim());
    if (!m) return t || '—';
    let h = +m[1];
    if (m[3]) h = (h % 12) + (/pm/i.test(m[3]) ? 12 : 0);
    return `${String(h).padStart(2, '0')}:${m[2]}`;
  }
  function duration(hms) {
    const m = /^(\d+):(\d+):(\d+)$/.exec(hms || '');
    if (!m) return null;
    const h = +m[1], min = +m[2], s = +m[3];
    if (h) return `${h} h ${String(min).padStart(2, '0')} min`;
    if (min) return `${min} min`;
    return `${s} s`;
  }
  function uptimeShort(u) {
    const m = /(\d+) day.*?(\d+) hour.*?(\d+) minute/.exec(u || '');
    if (!m) return '—';
    return +m[1] ? `${m[1]} d ${m[2]} h` : `${m[2]} h ${m[3]} min`;
  }
  function doorPosition(d) {
    const p = parseFloat(d.door_position_estimate);
    if (Number.isFinite(p) && p >= 0) return p;
    return d.state === 'open' ? 1 : d.state === 'closed' ? 0 : 0.5;
  }
  function nextText(d) {
    if (d.errorstate) return 'Stopped for safety - see the message above.';
    if (d.reference_running) return 'Calibrating - the door moves fully down and up.';
    if (d.override_active) return 'Controlled by the switch on the coop.';
    if (d.retry_pending) return `Something blocked the door - closing again (attempt ${d.retry_count}/${d.retry_max}).`;
    if (d.mode === 'manual') return 'Manual mode - the door only moves when you tell it to.';
    const how = d.mode === 'auto' ? 'at sunset' : 'by timer';
    const howOpen = d.mode === 'auto' ? 'at sunrise' : 'by timer';
    const closeIn = duration(d.tu_close), openIn = duration(d.tu_open);
    if (['open', 'opening'].includes(d.state) || (d.state === 'stopped' && d.tu_open === 'passed')) {
      if (closeIn) return `Closes automatically at <strong>${esc(d.close_time)}</strong> ${how} - in ${closeIn}`;
      return `Opens again tomorrow ${howOpen}.`;
    }
    if (openIn) return `Opens automatically at <strong>${esc(d.open_time)}</strong> ${howOpen} - in ${openIn}`;
    return `Opens again tomorrow ${howOpen}.`;
  }

  // ── door illustration ────────────────────────────────────────────────
  let doorSeq = 0;
  function doorSvg(size = 148) {
    const id = `dclip${++doorSeq}`;
    return `<svg class="door" width="${size}" height="${Math.round(size * 190 / 168)}" viewBox="0 0 168 190" role="img" aria-label="Door illustration" data-door-svg>
      <defs><clipPath id="${id}"><rect x="34" y="22" width="100" height="160"/></clipPath></defs>
      <rect class="ground" x="8" y="182" width="152" height="6" rx="3"/>
      <rect class="frame" x="18" y="6" width="132" height="176" rx="10"/>
      <rect class="opening" x="34" y="22" width="100" height="160"/>
      <g clip-path="url(#${id})"><g class="panel-group" data-door-panel>
        <rect class="panel" x="34" y="22" width="100" height="160" rx="3"/>
        <rect class="slat" x="42" y="40" width="84" height="10" rx="2"/><rect class="slat" x="42" y="70" width="84" height="10" rx="2"/>
        <rect class="slat" x="42" y="100" width="84" height="10" rx="2"/><rect class="slat" x="42" y="130" width="84" height="10" rx="2"/>
        <rect class="slat" x="42" y="160" width="84" height="10" rx="2"/>
      </g></g>
      <polygon class="arrow arrow-up" points="84,70 104,96 92,96 92,124 76,124 76,96 64,96"/>
      <polygon class="arrow arrow-down" points="84,134 104,108 92,108 92,80 76,80 76,108 64,108"/>
    </svg>`;
  }
  function updateDoorSvg(root, d) {
    const svg = $('[data-door-svg]', root);
    if (!svg) return;
    const pos = doorPosition(d);
    $('[data-door-panel]', svg).style.transform = `translateY(${-pos * 134}px)`;
    svg.dataset.moving = d.state === 'opening' ? 'up' : d.state === 'closing' ? 'down' : '';
    svg.setAttribute('aria-label', `Door ${STATE_LABEL[d.state] || d.state}, ${Math.round(pos * 100)} % open`);
  }

  // ── navigation ───────────────────────────────────────────────────────
  const NAV = [
    { group: 'Coop', items: [['home', 'Home', 'home'], ['climate', 'Climate', 'thermo'], ['history', 'History', 'chart'], ['camera', 'Camera', 'camera']] },
    { group: 'Setup', items: [['schedule', 'Schedule & location', 'clock'], ['network', 'Network', 'wifi'], ['hardware', 'Door & hardware', 'chip']] },
    { group: 'Device', items: [['logs', 'Logs', 'list'], ['system', 'System & updates', 'sliders']] },
  ];
  const TABS = [['home', 'Home', 'home'], ['climate', 'Climate', 'thermo'], ['history', 'History', 'chart'], ['more', 'More', 'menu']];
  const href = (name) => (name === 'home' ? '#/' : `#/${name}`);

  function renderNav() {
    $('#sidenav').innerHTML = NAV.map((g) => `<span class="nav-group">${esc(g.group)}</span>` +
      g.items.map(([name, label, ic]) => `<a href="${href(name)}" data-nav="${name}">${icon(ic)}${esc(label)}</a>`).join('')).join('');
    $('#tabbar').innerHTML = TABS.map(([name, label, ic]) => `<a href="${href(name)}" data-nav="${name}">${icon(ic, 22)}${esc(label)}</a>`).join('');
  }
  const MORE_PAGES = ['camera', 'schedule', 'network', 'hardware', 'logs', 'system', 'more'];
  function markNav(name) {
    $$('[data-nav]').forEach((a) => {
      const inTabbar = a.closest('#tabbar');
      const active = a.dataset.nav === name || (inTabbar && a.dataset.nav === 'more' && MORE_PAGES.includes(name));
      if (active) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    });
  }

  // ── theme ────────────────────────────────────────────────────────────
  const themePref = () => { try { return localStorage.getItem('coop-theme') || 'system'; } catch (e) { return 'system'; } };
  const effectiveTheme = () => {
    const pref = themePref();
    if (pref !== 'system') return pref;
    return matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  };
  function setTheme(pref) {
    try { if (pref === 'system') localStorage.removeItem('coop-theme'); else localStorage.setItem('coop-theme', pref); } catch (e) { /* private mode */ }
    if (pref === 'system') delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = pref;
    paintThemeButton();
    if (S.page && S.page.themeChanged) S.page.themeChanged();
  }
  function paintThemeButton() {
    const dark = effectiveTheme() === 'dark';
    const btn = $('#theme-toggle');
    btn.innerHTML = icon(dark ? 'sun' : 'moon');
    btn.setAttribute('aria-label', dark ? 'Switch to light theme' : 'Switch to dark theme');
    btn.dataset.theme = effectiveTheme();
  }

  // ── global banners ───────────────────────────────────────────────────
  function faultHelp(err) {
    if (/prematurely/i.test(err)) return 'The door kept stopping early while closing - probably a chicken or an object in the doorway. Check the doorway, then clear the error.';
    if (/not reached/i.test(err)) return "The door didn't reach its end position in time. Check that nothing blocks the door and the rope isn't tangled, then clear the error.";
    if (/Reference/i.test(err)) return "Calibration couldn't find the end position. Check the endstop switches, then clear the error and calibrate again.";
    if (/Test Error/i.test(err)) return 'A test error was triggered from Door & hardware.';
    return 'The motor is locked until the error is cleared.';
  }
  function banners(d) {
    const list = [];
    if (!S.connected && Date.now() - S.disconnectedAt > 2500) {
      list.push({ key: 'offline', kind: 'danger', ic: 'wifi', title: 'Connection lost', sub: 'Reconnecting to the coop controller… The values shown may be out of date.' });
    }
    if (!d) return list;
    if (d.errorstate) list.push({ key: 'fault', kind: 'danger', ic: 'alert', title: 'The door stopped for safety', sub: faultHelp(d.errorstate), tech: d.errorstate, action: ['clear-error', 'Clear error'] });
    if (d.reference_running) list.push({ key: 'calibrating', kind: 'info', ic: 'info', title: 'Calibrating the door…', sub: 'It moves fully down, then fully up, and measures the travel time.' });
    else if (d.reference_door_endstops_ms === 'Not set' && !d.errorstate) list.push({ key: 'calibrate', kind: 'warn', ic: 'alert', title: 'Calibrate the door', sub: 'The door needs one calibration run before Sun and Timer mode can be used.', action: ['calibrate', 'Calibrate now'] });
    if (d.override_active) list.push({ key: 'override', kind: 'info', ic: 'hand', title: 'Manual switch is active', sub: 'The switch on the coop controls the door - app controls are paused until it is back in the middle position.' });
    if (d.retry_pending) list.push({ key: 'retry', kind: 'warn', ic: 'alert', title: 'Something is blocking the door', sub: `Closing again in a few seconds (attempt ${d.retry_count}/${d.retry_max}).` });
    const skew = Math.round(Date.now() / 1000 - d.os_timestamp);
    if (Math.abs(skew) > 120 && !S.dismissed.clock) {
      const mins = Math.round(Math.abs(skew) / 60);
      list.push({ key: 'clock', kind: 'warn', ic: 'clock', title: `The coop's clock is ${mins} min ${skew > 0 ? 'behind' : 'ahead'}`, sub: 'Schedules use the device clock. Set it to this device\'s time?', action: ['fix-clock', "Use this device's time"], dismiss: true });
    }
    return list;
  }
  let bannerSig = '';
  function renderBanners() {
    const list = banners(S.data);
    const sig = JSON.stringify(list);
    if (sig === bannerSig) return;
    bannerSig = sig;
    $('#banners').innerHTML = list.map((b) => `<div class="banner banner-${b.kind}" data-banner="${b.key}" role="${b.kind === 'danger' ? 'alert' : 'status'}">
      <span class="b-icon">${icon(b.ic, 22, 2.2)}</span>
      <span class="b-text"><strong>${esc(b.title)}</strong><span class="b-sub">${esc(b.sub)}</span>${b.tech ? `<span class="b-tech">${esc(b.tech)}</span>` : ''}</span>
      ${b.action ? `<button class="btn ${b.kind === 'danger' ? 'btn-danger' : 'btn-primary'}" type="button" data-action="${b.action[0]}">${esc(b.action[1])}</button>` : ''}
      ${b.dismiss ? `<button class="icon-btn" type="button" data-dismiss="${b.key}" aria-label="Dismiss">${icon('x', 18)}</button>` : ''}
    </div>`).join('');
  }

  // Shared actions (banners + pages).
  const ACTIONS = {
    async 'clear-error'(btn) {
      const r = await busy(btn, () => ack('clear_error'));
      r.ok ? toast('Error cleared') : toast(r.error, 'error');
    },
    async calibrate(btn) {
      const ok = await confirmDialog({
        title: 'Calibrate the door?',
        body: 'The door will move <strong>fully down and then fully up</strong> to measure its travel time. Make sure nothing is in the doorway.',
        confirm: 'Start calibration',
      });
      if (!ok) return;
      const r = await busy(btn, () => ack('reference_endstops'));
      r.ok ? toast('Calibration started') : toast(r.error, 'error');
    },
    async 'fix-clock'(btn) {
      await setDeviceTime(btn, new Date());
    },
    async 'test-error'(btn) {
      const ok = await confirmDialog({ title: 'Trigger a test error?', body: 'This stops the door and locks the motor until you clear the error. Useful to test notifications.', confirm: 'Trigger test error', danger: true });
      if (!ok) return;
      const r = await busy(btn, () => ack('generate_error'));
      r.ok ? toast('Test error triggered') : toast(r.error, 'error');
    },
  };

  function localTimeString(date) {
    const p = (n) => String(n).padStart(2, '0');
    return `${date.getFullYear()}-${p(date.getMonth() + 1)}-${p(date.getDate())} ${p(date.getHours())}:${p(date.getMinutes())}:${p(date.getSeconds())}`;
  }
  async function setDeviceTime(btn, date) {
    try {
      await busy(btn, () => api('/api/system/time', { method: 'POST', body: { time: localTimeString(date) } }));
      toast('Device time updated');
      S.dismissed.clock = false;
    } catch (e) { toast(e.message, 'error'); }
  }

  document.addEventListener('click', (ev) => {
    const a = ev.target.closest('[data-action]');
    if (a && ACTIONS[a.dataset.action]) { ev.preventDefault(); ACTIONS[a.dataset.action](a); return; }
    const dis = ev.target.closest('[data-dismiss]');
    if (dis) { S.dismissed[dis.dataset.dismiss] = true; renderBanners(); }
  });

  // ── door command buttons (home) ──────────────────────────────────────
  async function doorCommand(btn, cmd) {
    const r = await busy(btn, () => ack(cmd));
    if (!r.ok) { toast(r.error, 'error'); return; }
    const wasAuto = S.data && S.data.mode !== 'manual';
    toast({ open: 'Opening the door', close: 'Closing the door', stop: 'Door stopped' }[cmd] + (wasAuto ? ' - switched to Manual' : ''));
  }
  async function setMode(mode, btn) {
    const r = await busy(btn, () => ack('set_mode', { mode }));
    if (!r.ok) { toast(r.error, 'error'); return false; }
    toast(`${{ manual: 'Manual mode', auto: 'Following the sun', timer: 'Timer mode' }[mode]} on`);
    await loadSettings();
    return true;
  }

  // ══════════════════════════════ pages ════════════════════════════════

  function tiles(d) {
    return `<div class="tile"><span class="t-label">In the coop</span><span class="t-value" data-bind="temp_in">${deg(d && d.temp_in)}</span><span class="t-sub"><span data-bind="hum_in">${pct(d && d.hum_in)}</span> humidity</span><span class="t-foot">Today <span data-bind="temp_in_min">${deg(d && d.temp_in_min)}</span> – <span data-bind="temp_in_max">${deg(d && d.temp_in_max)}</span></span></div>
      <div class="tile"><span class="t-label">Outside</span><span class="t-value" data-bind="temp_out">${deg(d && d.temp_out)}</span><span class="t-sub"><span data-bind="hum_out">${pct(d && d.hum_out)}</span> humidity</span><span class="t-foot">Today <span data-bind="temp_out_min">${deg(d && d.temp_out_min)}</span> – <span data-bind="temp_out_max">${deg(d && d.temp_out_max)}</span></span></div>`;
  }
  function bindValues(root, d) {
    $$('[data-bind]', root).forEach((el) => {
      const k = el.dataset.bind;
      if (!(k in d)) return;
      const v = k.startsWith('temp') || k.startsWith('cpu_temp') ? deg(d[k]) : k.startsWith('hum') ? pct(d[k]) : (d[k] ?? '—');
      if (el.textContent !== String(v)) el.textContent = v;
    });
  }
  function eventsHtml(d, { limit = 6, today = true, upcoming = true } = {}) {
    const todayStr = new Date().toDateString();
    let evs = (d.events || []).slice();
    if (today) evs = evs.filter((e) => new Date(e.ts).toDateString() === todayStr);
    evs = evs.slice(0, limit).reverse();
    let html = evs.map((e) => `<div class="event" data-kind="${esc(e.kind)}"><time>${esc(e.time)}</time><span class="ev-dot"></span><span>${esc(e.text)}</span></div>`).join('');
    if (upcoming && d.mode !== 'manual' && !d.errorstate) {
      if (duration(d.tu_open) && d.open_time) html += `<div class="event upcoming"><time>${esc(d.open_time)}</time><span class="ev-dot"></span><span>Opens ${d.mode === 'auto' ? 'at sunrise' : 'by timer'}</span></div>`;
      if (duration(d.tu_close) && d.close_time) html += `<div class="event upcoming"><time>${esc(d.close_time)}</time><span class="ev-dot"></span><span>Closes ${d.mode === 'auto' ? 'at sunset' : 'by timer'}</span></div>`;
    }
    return html || '<p class="muted">Nothing happened yet today.</p>';
  }

  // ── Home ─────────────────────────────────────────────────────────────
  const Home = {
    title: 'Home',
    render() {
      return `<div class="home">
        <section class="card door-card" aria-label="Door" data-door-card>
          <div class="door-hero">${doorSvg()}
            <div>
              <div class="row"><span class="eyebrow">Coop door</span><span class="chip chip-accent" data-mode-chip></span></div>
              <h2 data-door-title>—</h2>
              <p class="next" data-door-next>Connecting…</p>
            </div>
          </div>
          <div class="door-actions">
            <button class="btn btn-lg" type="button" data-cmd="open">${icon('up', 22, 2.2)}Open</button>
            <button class="btn btn-lg btn-outline act-stop" type="button" data-cmd="stop">${icon('stop', 20, 2.2)}Stop</button>
            <button class="btn btn-lg" type="button" data-cmd="close">${icon('down', 22, 2.2)}Close</button>
          </div>
          <div class="mode-box">
            <div class="row"><strong>Who decides</strong><span class="spacer"></span><span class="muted" style="font-size:14px" data-manual-hint></span></div>
            <div class="seg" role="radiogroup" aria-label="Door mode">
              <button type="button" role="radio" data-mode="manual" aria-checked="false">${icon('hand', 18)}Manual</button>
              <button type="button" role="radio" data-mode="auto" aria-checked="false">${icon('sun', 18)}Sun</button>
              <button type="button" role="radio" data-mode="timer" aria-checked="false">${icon('clock', 18)}Timer</button>
            </div>
            <div class="mode-summary" data-mode-summary></div>
          </div>
        </section>
        <div class="side-col">
          <a class="cam-thumb" href="#/camera" data-cam-thumb hidden aria-label="Open camera">
            <span data-cam-empty>Waiting for the camera…</span><img alt="Live camera image" data-cam-img hidden>
            <span class="cam-chip"><span class="rec"></span>Camera</span>
            <span class="icon-btn" aria-hidden="true">${icon('expand')}</span>
          </a>
          <div class="grid-2">${tiles(S.data)}</div>
          <section class="card" aria-label="Today">
            <div class="card-head"><h2>Today</h2><a href="#/history">All history</a></div>
            <div class="events" data-events></div>
          </section>
        </div>
      </div>`;
    },
    enter(root) {
      $$('[data-cmd]', root).forEach((b) => b.addEventListener('click', () => doorCommand(b, b.dataset.cmd)));
      $$('[data-mode]', root).forEach((b) => b.addEventListener('click', async () => {
        if (b.getAttribute('aria-checked') === 'true') return;
        await setMode(b.dataset.mode, b);
      }));
    },
    update(d, root) {
      bindValues(root, d);
      updateDoorSvg(root, d);
      const title = d.errorstate ? 'Needs attention' : d.reference_running ? 'Calibrating…' : STATE_LABEL[d.state] || d.state;
      $('[data-door-title]', root).textContent = title;
      $('[data-door-next]', root).innerHTML = nextText(d);
      const chip = $('[data-mode-chip]', root);
      chip.textContent = d.override_active ? 'Switch' : MODE_LABEL[d.mode] || d.mode;

      const locked = !!d.errorstate || d.reference_running || d.override_active;
      const [bOpen, bStop, bClose] = ['open', 'stop', 'close'].map((c) => $(`[data-cmd="${c}"]`, root));
      const openish = ['open', 'opening'].includes(d.state);
      const closedish = ['closed', 'closing'].includes(d.state);
      bOpen.disabled = locked || d.state === 'open';
      bClose.disabled = locked || d.state === 'closed';
      bStop.disabled = !!d.errorstate || d.reference_running || d.override_active;
      bOpen.classList.toggle('btn-primary', !openish && !bOpen.disabled);
      bClose.classList.toggle('btn-primary', !closedish && !bClose.disabled && (openish || d.state === 'stopped') && !bOpen.classList.contains('btn-primary'));
      if (d.state === 'stopped' && !locked) { bClose.classList.add('btn-primary'); bOpen.classList.remove('btn-primary'); }
      bClose.innerHTML = `${icon('down', 22, 2.2)}${openish ? 'Close now' : 'Close'}`;
      bOpen.innerHTML = `${icon('up', 22, 2.2)}${closedish ? 'Open now' : 'Open'}`;

      $$('[data-mode]', root).forEach((b) => b.setAttribute('aria-checked', String(b.dataset.mode === d.mode)));
      $('[data-manual-hint]', root).textContent = d.mode !== 'manual' ? 'Open / Close / Stop switch to Manual' : '';
      const s = S.settings || {};
      const off = (v) => `${v >= 0 ? '+' : '−'} ${Math.abs(v || 0)} min`;
      let summary;
      if (d.mode === 'auto') summary = `<span>Sunrise <strong>${esc(hhmm(d.sunrise))}</strong> ${off(s.sunrise_offset)}</span><span>Sunset <strong>${esc(hhmm(d.sunset))}</strong> ${off(s.sunset_offset)}</span>`;
      else if (d.mode === 'timer') summary = `<span>Opens <strong>${esc(d.timer_open_time)}</strong></span><span>Closes <strong>${esc(d.timer_close_time)}</strong></span>`;
      else summary = '<span>Choose Sun or Timer to open and close the door automatically.</span>';
      summary += '<a href="#/schedule" style="margin-left:auto">Adjust</a>';
      const box = $('[data-mode-summary]', root);
      if (box.innerHTML !== summary) box.innerHTML = summary;

      const cam = $('[data-cam-thumb]', root);
      cam.hidden = d.camera_enabled !== 'True';
      const ev = $('[data-events]', root);
      const evHtml = eventsHtml(d);
      if (ev.innerHTML !== evHtml) ev.innerHTML = evHtml;
    },
  };

  // ── charts ───────────────────────────────────────────────────────────
  const SERIES = [
    ['temp_in', 'Coop °C', '#E0A43A', 'y'], ['temp_out', 'Outside °C', '#3E7CB1', 'y'],
    ['hum_in', 'Coop humidity %', '#2E9A62', 'y1'], ['hum_out', 'Outside humidity %', '#8A6FD1', 'y1'],
    ['cpu_temp', 'CPU °C', '#D0513B', 'y'],
  ];
  const BAND = { open: 'rgba(46,154,98,0.14)', opening: 'rgba(224,164,58,0.18)', closing: 'rgba(224,164,58,0.18)', stopped: 'rgba(208,81,59,0.12)' };
  const bandPlugin = {
    id: 'doorBands',
    beforeDatasetsDraw(chart, _args, opts) {
      const rows = opts.rows || [];
      if (!rows.length) return;
      const { ctx, chartArea: a, scales: { x } } = chart;
      ctx.save();
      let start = 0;
      for (let i = 1; i <= rows.length; i++) {
        if (i === rows.length || rows[i].state !== rows[start].state) {
          const color = BAND[rows[start].state];
          if (color) {
            const x0 = x.getPixelForValue(start), x1 = x.getPixelForValue(Math.min(i, rows.length - 1));
            ctx.fillStyle = color;
            ctx.fillRect(x0, a.top, Math.max(1, x1 - x0), a.bottom - a.top);
          }
          start = i;
        }
      }
      ctx.restore();
    },
  };
  function makeChart(canvas, rows, enabled, { bands = true } = {}) {
    const text = cssVar('--muted'), grid = cssVar('--chart-grid');
    const datasets = SERIES.filter(([k]) => enabled.includes(k)).map(([k, label, color, axis]) => ({
      label, data: rows.map((r) => r[k]), borderColor: color, backgroundColor: color, yAxisID: axis,
      borderWidth: 2.2, pointRadius: 0, tension: 0.3, spanGaps: true,
    }));
    const usesHum = datasets.some((ds) => ds.yAxisID === 'y1');
    return new Chart(canvas, {
      type: 'line',
      data: { labels: rows.map((r) => r.time.slice(0, 5)), datasets },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        interaction: { mode: 'index', intersect: false },
        plugins: { legend: { display: false }, doorBands: { rows: bands ? rows : [] }, tooltip: { padding: 10 } },
        scales: {
          x: { ticks: { color: text, maxTicksLimit: 8, maxRotation: 0 }, grid: { color: grid } },
          y: { title: { display: true, text: '°C', color: text }, ticks: { color: text }, grid: { color: grid } },
          y1: { display: usesHum, position: 'right', min: 0, max: 100, title: { display: true, text: '%', color: text }, ticks: { color: text }, grid: { display: false } },
        },
      },
      plugins: [bandPlugin],
    });
  }

  // ── Climate ──────────────────────────────────────────────────────────
  const Climate = {
    title: 'Climate',
    render() {
      const d = S.data || {};
      const block = (title, key, hum) => `<section class="tile big-climate" aria-label="${title}">
        <span class="t-label">${title}</span><span class="t-value" data-bind="${key}">${deg(d[key])}</span>
        <span class="t-sub"><span data-bind="${hum}">${pct(d[hum])}</span> humidity</span>
        <div class="minmax"><span class="chip">Low <span data-bind="${key}_min">${deg(d[key + '_min'])}</span></span><span class="chip">High <span data-bind="${key}_max">${deg(d[key + '_max'])}</span></span>
        <span class="chip">Humidity <span data-bind="${hum}_min">${pct(d[hum + '_min'])}</span> – <span data-bind="${hum}_max">${pct(d[hum + '_max'])}</span></span></div></section>`;
      return `<div class="stack" style="gap:20px">
        <div class="grid-2">${block('In the coop', 'temp_in', 'hum_in')}${block('Outside', 'temp_out', 'hum_out')}</div>
        <section class="card" aria-label="Today's temperatures">
          <div class="card-head"><h2>Today</h2><span class="hint" data-chart-info>Loading…</span></div>
          <div class="chart-box"><canvas data-chart aria-label="Temperature chart for today"></canvas></div>
        </section>
        <section class="tile" aria-label="Controller"><span class="t-label">Controller (CPU)</span><span class="t-value" data-bind="cpu_temp">${deg(d.cpu_temp)}</span><span class="t-foot">Today <span data-bind="cpu_temp_min">${deg(d.cpu_temp_min)}</span> – <span data-bind="cpu_temp_max">${deg(d.cpu_temp_max)}</span></span></section>
      </div>`;
    },
    async load(root) {
      try {
        const files = await api('/api/csv');
        if (!files.length) { $('[data-chart-info]', root).textContent = 'No data recorded yet.'; return; }
        const data = await api('/api/csv/' + encodeURIComponent(files[0].name));
        if (!root.isConnected) return;
        this.chart && this.chart.destroy();
        this.chart = makeChart($('[data-chart]', root), data.rows, ['temp_in', 'temp_out'], { bands: false });
        $('[data-chart-info]', root).textContent = `${data.total} readings`;
      } catch (e) { $('[data-chart-info]', root).textContent = e.message; }
    },
    enter(root) { this.load(root); this.timer = setInterval(() => this.load(root), 60000); },
    leave() { clearInterval(this.timer); this.chart && this.chart.destroy(); this.chart = null; },
    update(d, root) { bindValues(root, d); },
    themeChanged() { if (this.root) this.load(this.root); },
  };

  // ── History ──────────────────────────────────────────────────────────
  const History = {
    title: 'History',
    enabled: ['temp_in', 'temp_out', 'hum_in', 'hum_out'],
    render() {
      return `<div class="stack" style="gap:20px">
        <section class="card" aria-label="Chart">
          <div class="row">
            <label class="sr-only" for="hist-day">Day</label>
            <select class="input" id="hist-day" style="width:auto;min-width:180px" data-day></select>
            <button class="btn" type="button" data-refresh>${icon('refresh', 18)}Refresh</button>
            <span class="spacer"></span><span class="muted" data-info></span>
          </div>
          <div class="series-toggles" data-series>${SERIES.map(([k, label, color]) => `<button type="button" data-key="${k}" aria-pressed="${this.enabled.includes(k)}"><span class="sw" style="background:${color}"></span>${label}</button>`).join('')}</div>
          <div class="chart-box"><canvas data-chart aria-label="History chart"></canvas></div>
          <div class="legend-bands"><strong>Door:</strong><span><i style="background:${BAND.open}"></i>open</span><span><i style="background:${BAND.opening}"></i>moving</span><span><i style="background:${BAND.stopped}"></i>stopped</span><span><i style="border:1px solid var(--border)"></i>closed</span></div>
        </section>
        <section class="card" aria-label="Door events"><div class="card-head"><h2>Recent door events</h2><span class="hint">since the last restart</span></div><div class="events" data-events></div></section>
      </div>`;
    },
    async loadFiles(root) {
      try {
        const files = await api('/api/csv');
        const sel = $('[data-day]', root);
        const cur = sel.value;
        sel.innerHTML = files.map((f) => `<option value="${esc(f.name)}">${esc(f.name.replace('.csv', '').replace(/_/g, '-'))}</option>`).join('') || '<option value="">No data yet</option>';
        if (cur && files.some((f) => f.name === cur)) sel.value = cur;
        await this.loadDay(root);
      } catch (e) { $('[data-info]', root).textContent = e.message; }
    },
    async loadDay(root) {
      const name = $('[data-day]', root).value;
      if (!name) { $('[data-info]', root).textContent = 'No data recorded yet.'; return; }
      try {
        this.rows = (await api('/api/csv/' + encodeURIComponent(name))).rows;
        if (!root.isConnected) return;
        $('[data-info]', root).textContent = `${this.rows.length} data points`;
        this.draw(root);
      } catch (e) { $('[data-info]', root).textContent = e.message; }
    },
    draw(root) {
      this.chart && this.chart.destroy();
      this.chart = makeChart($('[data-chart]', root), this.rows || [], this.enabled);
    },
    enter(root) {
      $('[data-day]', root).addEventListener('change', () => this.loadDay(root));
      $('[data-refresh]', root).addEventListener('click', (e) => busy(e.currentTarget, () => this.loadFiles(root)));
      $$('[data-series] button', root).forEach((b) => b.addEventListener('click', () => {
        const k = b.dataset.key;
        this.enabled = this.enabled.includes(k) ? this.enabled.filter((x) => x !== k) : [...this.enabled, k];
        b.setAttribute('aria-pressed', String(this.enabled.includes(k)));
        this.draw(root);
      }));
      this.loadFiles(root);
    },
    leave() { this.chart && this.chart.destroy(); this.chart = null; },
    update(d, root) {
      const ev = $('[data-events]', root);
      const html = eventsHtml(d, { limit: 20, today: false, upcoming: false }).replace('Nothing happened yet today.', 'No door events since the last restart.');
      if (ev.innerHTML !== html) ev.innerHTML = html;
    },
    themeChanged() { if (this.root) this.draw(this.root); },
  };

  // ── Camera ───────────────────────────────────────────────────────────
  const Camera = {
    title: 'Camera',
    render() {
      return `<section class="card" aria-label="Camera">
        <div class="card-head"><h2>Live view</h2><span class="hint" data-cam-status>Waiting for the camera…</span>
          <button class="icon-btn" type="button" data-fullscreen aria-label="Fullscreen">${icon('expand')}</button></div>
        <div class="cam-view" data-cam-box><span data-cam-empty>Waiting for the camera…</span><img alt="Live camera image" data-cam-img hidden></div>
      </section>
      <section class="card" data-cam-off hidden><div class="empty"><p><strong>The camera is switched off.</strong></p><p>Set <span class="mono">enable_camera: true</span> in <span class="mono">config.yaml</span> and restart the controller to see the coop here.</p></div></section>`;
    },
    enter(root) {
      $('[data-fullscreen]', root).addEventListener('click', () => {
        const box = $('[data-cam-box]', root);
        (box.requestFullscreen || box.webkitRequestFullscreen || (() => {})).call(box);
      });
    },
    update(d, root) {
      const on = d.camera_enabled === 'True';
      $('[data-cam-off]', root).hidden = on;
      $('section[aria-label="Camera"]', root).hidden = !on;
      const now = Date.now();
      S.camFrames = S.camFrames.filter((t) => now - t < 2000);
      const fps = S.camFrames.length / 2;
      $('[data-cam-status]', root).textContent = S.camFrame && fps ? `Live · ${fps.toFixed(0)} fps` : 'Waiting for the camera…';
    },
  };

  // ── Schedule & location ──────────────────────────────────────────────
  const Schedule = {
    title: 'Schedule & location',
    render() {
      const s = S.settings || {};
      const loc = s.location || {};
      return `<div class="stack" style="gap:20px">
        <section class="card" aria-label="Who decides">
          <div class="card-head"><h2>Who decides when the door moves</h2></div>
          <div class="mode-cards" role="radiogroup" aria-label="Door mode">
            <button class="mode-card" type="button" role="radio" data-mode="auto" aria-checked="false">${icon('sun', 26)}<span><strong>Follow the sun</strong><span>Opens at sunrise, closes at sunset</span></span></button>
            <button class="mode-card" type="button" role="radio" data-mode="timer" aria-checked="false">${icon('clock', 26)}<span><strong>Fixed times</strong><span>Opens and closes at set times</span></span></button>
            <button class="mode-card" type="button" role="radio" data-mode="manual" aria-checked="false">${icon('hand', 26)}<span><strong>Only by hand</strong><span>You open and close it</span></span></button>
          </div>
        </section>
        <div class="grid-2">
          <section class="card" aria-label="Sun settings">
            <div class="card-head"><h2>Sun settings</h2></div>
            <p class="muted">Shift the opening and closing relative to sunrise and sunset. Negative numbers mean earlier.</p>
            <form class="stack" data-offsets novalidate>
              <div class="grid-2">
                <div class="field"><label for="off-rise">Open, minutes after sunrise</label><input class="input" id="off-rise" name="sunrise_offset" type="number" min="-720" max="720" step="1" value="${esc(s.sunrise_offset ?? 0)}" required></div>
                <div class="field"><label for="off-set">Close, minutes after sunset</label><input class="input" id="off-set" name="sunset_offset" type="number" min="-720" max="720" step="1" value="${esc(s.sunset_offset ?? 0)}" required></div>
              </div>
              <p class="muted" data-sun-preview></p>
              <div class="form-actions"><button class="btn btn-primary" type="submit">Save sun settings</button><span class="form-msg" data-msg></span></div>
            </form>
          </section>
          <section class="card" aria-label="Timer settings">
            <div class="card-head"><h2>Fixed times</h2></div>
            <p class="muted">Closing time may be earlier than opening time to keep the door open overnight.</p>
            <form class="stack" data-timer novalidate>
              <div class="grid-2">
                <div class="field"><label for="t-open">Open at</label><input class="input" id="t-open" name="open_time" type="time" value="${esc(s.timer_open_time || '07:00')}" required></div>
                <div class="field"><label for="t-close">Close at</label><input class="input" id="t-close" name="close_time" type="time" value="${esc(s.timer_close_time || '20:00')}" required></div>
              </div>
              <div class="form-actions"><button class="btn btn-primary" type="submit">Save times</button><span class="form-msg" data-msg></span></div>
            </form>
          </section>
        </div>
        <section class="card" aria-label="Location">
          <div class="card-head"><h2>Location</h2><span class="hint">used for sunrise and sunset</span></div>
          <p>Current: <strong data-loc-current>${esc(loc.city)}${loc.region ? ', ' + esc(loc.region) : ''}</strong> <span class="muted">· ${esc(loc.timezone)}</span> <span class="muted" data-loc-sun></span></p>
          <div class="field"><label for="loc-search">Find a city</label><input class="input" id="loc-search" type="search" placeholder="e.g. Berlin, Denver, London" autocomplete="off" data-loc-search></div>
          <div class="loc-results" data-loc-results hidden></div>
          <form class="stack" data-location novalidate>
            <div class="grid-2">
              <div class="field"><label for="loc-city">City</label><input class="input" id="loc-city" name="city" value="${esc(loc.city)}" required></div>
              <div class="field"><label for="loc-region">Region</label><input class="input" id="loc-region" name="region" value="${esc(loc.region)}"></div>
              <div class="field"><label for="loc-lat">Latitude</label><input class="input" id="loc-lat" name="latitude" type="number" step="any" min="-90" max="90" value="${esc(loc.latitude)}" required></div>
              <div class="field"><label for="loc-lon">Longitude</label><input class="input" id="loc-lon" name="longitude" type="number" step="any" min="-180" max="180" value="${esc(loc.longitude)}" required></div>
            </div>
            <div class="field"><label for="loc-tz">Time zone</label><input class="input" id="loc-tz" name="timezone" value="${esc(loc.timezone)}" list="tz-list" required><datalist id="tz-list"></datalist><span class="help">IANA name, e.g. Europe/Berlin</span></div>
            <div class="form-actions"><button class="btn btn-primary" type="submit">Save location</button><span class="form-msg" data-msg></span></div>
          </form>
        </section>
      </div>`;
    },
    async enter(root) {
      if (!S.settings) { await loadSettings(); if (root.isConnected) { renderPage(); return; } }
      $$('[data-mode]', root).forEach((b) => b.addEventListener('click', async () => {
        if (b.getAttribute('aria-checked') === 'true') return;
        await setMode(b.dataset.mode, b);
      }));
      const msg = (form, text, ok) => { const m = $('[data-msg]', form); m.textContent = text; m.className = 'form-msg ' + (ok ? 'ok' : 'error'); };

      const offsets = $('[data-offsets]', root);
      const preview = () => {
        const d = S.data || {};
        const r = parseInt(offsets.sunrise_offset.value, 10) || 0, s = parseInt(offsets.sunset_offset.value, 10) || 0;
        $('[data-sun-preview]', root).textContent = d.sunrise ? `Today: sunrise ${hhmm(d.sunrise)} → opens ${r >= 0 ? r + ' min later' : -r + ' min earlier'}; sunset ${hhmm(d.sunset)} → closes ${s >= 0 ? s + ' min later' : -s + ' min earlier'}.` : '';
      };
      offsets.addEventListener('input', preview);
      preview();
      offsets.addEventListener('submit', async (e) => {
        e.preventDefault();
        const r = +offsets.sunrise_offset.value, s = +offsets.sunset_offset.value;
        const bad = [offsets.sunrise_offset, offsets.sunset_offset].filter((i) => !i.value || !Number.isInteger(+i.value) || Math.abs(+i.value) > 720);
        [offsets.sunrise_offset, offsets.sunset_offset].forEach((i) => i.setAttribute('aria-invalid', String(bad.includes(i))));
        if (bad.length) { msg(offsets, 'Offsets must be whole minutes between -720 and 720.', false); return; }
        const res = await busy(e.submitter, () => ack('auto_offsets', { sunrise_offset: r, sunset_offset: s }));
        if (res.ok) { msg(offsets, 'Saved', true); await loadSettings(); } else msg(offsets, res.error, false);
      });

      const timer = $('[data-timer]', root);
      timer.addEventListener('submit', async (e) => {
        e.preventDefault();
        if (!timer.open_time.value || !timer.close_time.value) { msg(timer, 'Please enter both times.', false); return; }
        const res = await busy(e.submitter, () => ack('timer_times', { open_time: timer.open_time.value, close_time: timer.close_time.value }));
        if (res.ok) { msg(timer, 'Saved', true); await loadSettings(); } else msg(timer, res.error, false);
      });

      const locForm = $('[data-location]', root);
      const search = $('[data-loc-search]', root);
      const results = $('[data-loc-results]', root);
      let locations = null;
      const pretty = (n) => { const [grp, city] = String(n).split(' - '); const t = (x) => (x || '').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()); return city ? `${t(city)} (${t(grp)})` : t(grp); };
      search.addEventListener('input', async () => {
        const q = search.value.trim().toLowerCase();
        if (q.length < 2) { results.hidden = true; return; }
        if (!locations) {
          try { locations = await api('/api/locations'); } catch (e) { toast(e.message, 'error'); return; }
          $('#tz-list').innerHTML = [...new Set(locations.map((l) => l.timezone))].sort().map((t) => `<option value="${esc(t)}">`).join('');
        }
        const hits = locations.filter((l) => l.name.toLowerCase().includes(q) || String(l.region).toLowerCase().includes(q)).slice(0, 8);
        results.innerHTML = hits.map((l, i) => `<button type="button" data-hit="${i}"><span>${esc(pretty(l.name))}</span><span class="muted">${esc(l.region)} · ${esc(l.timezone)}</span></button>`).join('') || '<div class="empty">No match - enter the coordinates below.</div>';
        results.hidden = false;
        $$('[data-hit]', results).forEach((b) => b.addEventListener('click', () => {
          const l = hits[+b.dataset.hit];
          const city = pretty(l.name).replace(/ \(.*\)$/, '');
          locForm.city.value = city; locForm.region.value = l.region; locForm.latitude.value = l.latitude;
          locForm.longitude.value = l.longitude; locForm.timezone.value = l.timezone;
          results.hidden = true; search.value = '';
          msg(locForm, `${city} selected - save to apply.`, true);
          $('button[type="submit"]', locForm).focus();
        }));
      });
      locForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const f = locForm;
        const invalid = [f.city, f.latitude, f.longitude, f.timezone].filter((i) => !i.checkValidity() || !i.value.trim());
        [f.city, f.latitude, f.longitude, f.timezone].forEach((i) => i.setAttribute('aria-invalid', String(invalid.includes(i))));
        if (invalid.length) { msg(f, 'Please check the highlighted fields.', false); return; }
        const payload = { city: f.city.value.trim(), region: f.region.value.trim(), timezone: f.timezone.value.trim(), latitude: parseFloat(f.latitude.value), longitude: parseFloat(f.longitude.value) };
        const res = await busy(e.submitter, () => ack('update_location', payload));
        if (!res.ok) { msg(f, res.error, false); f.timezone.setAttribute('aria-invalid', String(/timezone/i.test(res.error))); return; }
        msg(f, 'Location saved', true);
        await loadSettings();
        $('[data-loc-current]', root).textContent = `${payload.city}${payload.region ? ', ' + payload.region : ''}`;
      });
    },
    update(d, root) {
      $$('[data-mode]', root).forEach((b) => b.setAttribute('aria-checked', String(b.dataset.mode === d.mode)));
      const sun = $('[data-loc-sun]', root);
      const text = d.sunrise ? `· today ${hhmm(d.sunrise)} – ${hhmm(d.sunset)}` : '';
      if (sun.textContent !== text) sun.textContent = text;
    },
  };

  // ── Network ──────────────────────────────────────────────────────────
  const Network = {
    title: 'Network',
    render() {
      return `<div class="stack" style="gap:20px">
        <section class="card" aria-label="Status"><div class="card-head"><h2>Status</h2><button class="btn btn-ghost" type="button" data-status-refresh>${icon('refresh', 18)}Refresh</button></div>
          <dl class="kv" data-status><dt>Loading…</dt><dd></dd></dl></section>
        <div class="grid-2">
          <section class="card" aria-label="Wi-Fi">
            <div class="card-head"><h2>Wi-Fi</h2></div>
            <form class="stack" data-wifi novalidate>
              <div class="field"><label for="wf-ssid">Network name (SSID)</label><input class="input" id="wf-ssid" name="ssid" autocomplete="off"></div>
              <div class="field"><label for="wf-pw">Password</label><input class="input" id="wf-pw" name="password" type="password" autocomplete="new-password"><span class="help" data-pw-help></span></div>
              <div class="field"><label for="wf-timeout">Give up connecting after (seconds)</label><input class="input" id="wf-timeout" name="timeout" type="number" min="1" step="1"></div>
              <div class="form-actions"><button class="btn btn-primary" type="submit">Save</button><button class="btn" type="button" data-connect>${icon('wifi', 18)}Connect now</button><span class="form-msg" data-msg></span></div>
            </form>
          </section>
          <section class="card" aria-label="Nearby networks">
            <div class="card-head"><h2>Nearby networks</h2><button class="btn" type="button" data-scan>${icon('refresh', 18)}Scan</button></div>
            <div class="list" data-networks><p class="muted">Scan to see the networks the coop can reach.</p></div>
          </section>
        </div>
        <section class="card" aria-label="Hotspot">
          <div class="card-head"><h2>Fallback hotspot</h2><span class="hint">used when the Wi-Fi can't be reached</span></div>
          <form class="stack" data-ap novalidate>
            <div class="grid-2">
              <div class="field"><label for="ap-ssid">Hotspot name</label><input class="input" id="ap-ssid" name="ap_ssid" autocomplete="off" required></div>
              <div class="field"><label for="ap-pw">Hotspot password</label><input class="input" id="ap-pw" name="ap_password" type="password" autocomplete="new-password"><span class="help">8–63 characters. Leave empty to keep the current one.</span></div>
            </div>
            <div class="form-actions"><button class="btn btn-primary" type="submit">Save</button><button class="btn" type="button" data-start-ap>Start hotspot now</button><span class="form-msg" data-msg></span></div>
          </form>
        </section>
      </div>`;
    },
    async loadStatus(root) {
      try {
        const st = await api('/api/wifi-status');
        const conn = st.current_connection ? `Wi-Fi “${esc(st.current_connection.ssid)}”` : 'No Wi-Fi';
        $('[data-status]', root).innerHTML = `<dt>Wi-Fi</dt><dd>${conn}</dd><dt>Ethernet</dt><dd>${st.ethernet_connected ? 'Connected' : 'Not connected'}</dd><dt>Hotspot</dt><dd>${st.ap_mode_active ? 'Active' : 'Off'}</dd>`;
      } catch (e) { $('[data-status]', root).innerHTML = `<dt>Error</dt><dd>${esc(e.message)}</dd>`; }
    },
    async loadConfig(root) {
      try {
        const c = await api('/api/wifi-config');
        const f = $('[data-wifi]', root), ap = $('[data-ap]', root);
        f.ssid.value = c.ssid || ''; f.timeout.value = c.timeout;
        f.password.value = ''; f.password.placeholder = c.password === MASK ? 'Saved - leave empty to keep' : 'No password';
        $('[data-pw-help]', root).textContent = c.password === MASK ? 'A password is saved.' : '';
        ap.ap_ssid.value = c.ap_ssid || ''; ap.ap_password.value = ''; ap.ap_password.placeholder = c.ap_password === MASK ? 'Saved - leave empty to keep' : '';
        this.saved = c;
      } catch (e) { toast(e.message, 'error'); }
    },
    enter(root) {
      const msg = (form, text, ok) => { const m = $('[data-msg]', form); m.textContent = text; m.className = 'form-msg ' + (ok ? 'ok' : 'error'); };
      this.loadStatus(root); this.loadConfig(root);
      $('[data-status-refresh]', root).addEventListener('click', (e) => busy(e.currentTarget, () => this.loadStatus(root)));
      $('[data-scan]', root).addEventListener('click', (e) => busy(e.currentTarget, async () => {
        try {
          const nets = await api('/api/wifi-scan');
          const bars = (s) => [25, 50, 75, 100].map((t, i) => `<i class="${s >= t - 12 ? 'on' : ''}" style="height:${5 + i * 3}px"></i>`).join('');
          $('[data-networks]', root).innerHTML = nets.map((n) => `<div class="list-row"><span class="signal" aria-label="Signal ${n.signal} %">${bars(n.signal)}</span><span class="spacer"><strong>${esc(n.ssid)}</strong><br><span class="muted" style="font-size:13px">${n.security ? icon('lock', 13) + ' ' + esc(n.security) : 'Open network'}</span></span><button class="btn btn-ghost" type="button" data-use="${esc(n.ssid)}">Use</button></div>`).join('') || '<p class="muted">No networks found.</p>';
          $$('[data-use]', root).forEach((b) => b.addEventListener('click', () => {
            const f = $('[data-wifi]', root);
            f.ssid.value = b.dataset.use; f.password.value = ''; f.password.placeholder = 'Password for ' + b.dataset.use;
            f.password.focus();
          }));
        } catch (err) { toast(err.message, 'error'); }
      }));
      const wifi = $('[data-wifi]', root);
      const wifiPayload = () => {
        const p = { ssid: wifi.ssid.value.trim(), timeout: wifi.timeout.value };
        p.password = wifi.password.value || (this.saved && this.saved.password === MASK && wifi.ssid.value.trim() === this.saved.ssid ? MASK : '');
        return p;
      };
      wifi.addEventListener('submit', async (e) => {
        e.preventDefault();
        try {
          const r = await busy(e.submitter, () => api('/api/wifi-config', { method: 'POST', body: wifiPayload() }));
          msg(wifi, 'Saved', true); this.saved = r.wifi; this.loadConfig(root);
        } catch (err) { msg(wifi, err.message, false); }
      });
      $('[data-connect]', root).addEventListener('click', async (e) => {
        const btn = e.currentTarget;  // reset to null once the handler awaits
        const ssid = wifi.ssid.value.trim();
        if (!ssid) { msg(wifi, 'Enter a network name first.', false); return; }
        const ok = await confirmDialog({ title: `Connect to “${esc(ssid)}” now?`, body: 'This page may lose its connection while the coop switches networks. If the connection fails, the coop starts its hotspot after a few seconds so you can reach it again.', confirm: 'Connect' });
        if (!ok) return;
        try {
          const pw = wifi.password.value;
          const r = await busy(btn, () => api('/api/wifi-connect', { method: 'POST', body: { ssid, password: pw || null } }));
          r.success ? toast(`Connected to ${ssid}`) : toast(`Could not connect to ${ssid} - hotspot started`, 'error');
          this.loadStatus(root);
        } catch (err) { toast(err.message, 'error'); }
      });
      const ap = $('[data-ap]', root);
      ap.addEventListener('submit', async (e) => {
        e.preventDefault();
        const body = { ap_ssid: ap.ap_ssid.value.trim() };
        if (ap.ap_password.value) body.ap_password = ap.ap_password.value;
        try { await busy(e.submitter, () => api('/api/wifi-config', { method: 'POST', body })); msg(ap, 'Saved', true); this.loadConfig(root); } catch (err) { msg(ap, err.message, false); }
      });
      $('[data-start-ap]', root).addEventListener('click', async (e) => {
        const btn = e.currentTarget;
        const ok = await confirmDialog({ title: 'Start the hotspot now?', body: 'The coop leaves your Wi-Fi and opens its own network. Connect your phone to the hotspot to reach this page again.', confirm: 'Start hotspot' });
        if (!ok) return;
        try {
          const r = await busy(btn, () => api('/api/wifi-ap', { method: 'POST' }));
          r.success ? toast('Hotspot started') : toast('The hotspot could not be started', 'error');
          this.loadStatus(root);
        } catch (err) { toast(err.message, 'error'); }
      });
    },
  };

  // ── Door & hardware ──────────────────────────────────────────────────
  const PIN_FIELDS = [
    ['Motor', [['motor_in1', 'Direction A (in1)'], ['motor_in2', 'Direction B (in2)'], ['motor_ena', 'Enable']]],
    ['Endstops', [['endstop_up', 'Upper endstop (open)'], ['endstop_down', 'Lower endstop (closed)']]],
    ['Manual switch', [['override_open', 'Switch: open'], ['override_close', 'Switch: close']]],
    ['Sensors', [['dht11_data', 'Coop sensor data (DHT11)'], ['dht22_data', 'Outside sensor data (DHT22)'], ['dht22_power', 'Outside sensor power (empty = none)']]],
  ];
  const Hardware = {
    title: 'Door & hardware',
    render() {
      return `<div class="stack" style="gap:20px">
        <div class="grid-2">
          <section class="card" aria-label="Calibration">
            <div class="card-head"><h2>Calibration</h2></div>
            <dl class="kv"><dt>Door travel time</dt><dd data-travel>—</dd><dt>Door</dt><dd data-hw-state>—</dd></dl>
            <p class="muted">Calibration moves the door fully down and up and measures how long it takes. Sun and Timer mode use it to detect a stuck door.</p>
            <div class="form-actions"><button class="btn btn-primary" type="button" data-action="calibrate">Calibrate door</button></div>
          </section>
          <section class="card" aria-label="Diagnostics">
            <div class="card-head"><h2>Diagnostics</h2></div>
            <p class="muted">Check the push notifications and the error handling by triggering a harmless test error.</p>
            <div class="form-actions"><button class="btn btn-danger" type="button" data-action="test-error">Trigger test error</button><a class="btn btn-ghost" href="#/logs">Open logs</a></div>
          </section>
        </div>
        <section class="card" aria-label="Simulator" data-sim hidden>
          <div class="card-head"><h2>Door simulator</h2><span class="chip chip-accent">mock hardware</span></div>
          <p class="muted">No real hardware is connected. Use these controls to act like the switch and endstops on the coop.</p>
          <div class="sim-grid">
            <button class="btn" type="button" data-hold="override_open">Hold: switch to open</button>
            <button class="btn" type="button" data-hold="override_close">Hold: switch to close</button>
            <button class="btn" type="button" data-toggle="endstop_up" aria-pressed="false">Upper endstop</button>
            <button class="btn" type="button" data-toggle="endstop_down" aria-pressed="false">Lower endstop</button>
          </div>
        </section>
        <section class="card" aria-label="Live pins">
          <div class="card-head"><h2>Live GPIO pins</h2><span class="hint">updates every second</span></div>
          <div style="overflow-x:auto"><table class="pin-table"><thead><tr><th>Pin</th><th>Function</th><th>Direction</th><th>Level</th></tr></thead><tbody data-pins><tr><td colspan="4" class="muted">Loading…</td></tr></tbody></table></div>
        </section>
        <section class="card" aria-label="Pin configuration">
          <div class="card-head"><h2>Pin configuration</h2><span class="hint">BCM numbering</span></div>
          <form class="stack" data-gpio novalidate>
            ${PIN_FIELDS.map(([group, fields]) => `<fieldset style="border:0;padding:0;margin:0" class="stack"><legend class="eyebrow" style="margin-bottom:8px">${group}</legend><div class="grid-3">${fields.map(([k, label]) => `<div class="field"><label for="g-${k}">${label}</label><input class="input" id="g-${k}" name="${k}" type="number" min="0" max="40" step="1"></div>`).join('')}</div></fieldset>`).join('')}
            <div class="grid-3">
              <label class="check"><input type="checkbox" name="invert_end_up">Invert upper endstop</label>
              <label class="check"><input type="checkbox" name="invert_end_down">Invert lower endstop</label>
              <div class="field"><label for="g-timeout">Calibration timeout (s)</label><input class="input" id="g-timeout" name="reference_timeout" type="number" min="5" max="600" step="1"></div>
            </div>
            <p class="muted">Invert flags and the timeout apply immediately; pin numbers after a restart.</p>
            <div class="form-actions"><button class="btn btn-primary" type="submit">Save pin configuration</button><button class="btn" type="button" data-reload>Reset</button><span class="form-msg" data-msg></span></div>
          </form>
        </section>
      </div>`;
    },
    async loadGpio(root) {
      try {
        this.gpio = await api('/api/gpio-config');
        const f = $('[data-gpio]', root);
        Object.entries(this.gpio).forEach(([k, v]) => {
          const el = f.elements[k];
          if (!el) return;
          if (el.type === 'checkbox') el.checked = !!v; else el.value = v ?? '';
          el.removeAttribute('aria-invalid');
        });
      } catch (e) { toast(e.message, 'error'); }
    },
    enter(root) {
      this.loadGpio(root);
      const f = $('[data-gpio]', root);
      const msg = (text, ok) => { const m = $('[data-msg]', f); m.textContent = text; m.className = 'form-msg ' + (ok ? 'ok' : 'error'); };
      $('[data-reload]', root).addEventListener('click', () => { this.loadGpio(root); msg('', true); });
      f.addEventListener('submit', async (e) => {
        e.preventDefault();
        const body = {};
        PIN_FIELDS.forEach(([, fields]) => fields.forEach(([k]) => { body[k] = f.elements[k].value === '' ? (k === 'dht22_power' ? null : '') : +f.elements[k].value; }));
        body.invert_end_up = f.elements.invert_end_up.checked;
        body.invert_end_down = f.elements.invert_end_down.checked;
        body.reference_timeout = f.elements.reference_timeout.value === '' ? '' : +f.elements.reference_timeout.value;
        try {
          const r = await busy(e.submitter, () => api('/api/gpio-config', { method: 'POST', body }));
          msg(r.message, true);
          $$('.input', f).forEach((i) => i.removeAttribute('aria-invalid'));
          this.gpio = r.gpio;
        } catch (err) {
          msg(err.message, false);
          $$('.input', f).forEach((i) => i.setAttribute('aria-invalid', String(err.message.includes(`'${i.name}'`))));
        }
      });
      // simulator
      if (window.COOP.hardwareMock) {
        $('[data-sim]', root).hidden = false;
        const pin = (k) => (this.gpio ? this.gpio[k] : null);
        $$('[data-hold]', root).forEach((b) => {
          const press = (v) => { if (pin(b.dataset.hold) != null) socket.emit('mock_trigger_pin', { pin: pin(b.dataset.hold), state: v ? 'HIGH' : 'LOW' }); b.setAttribute('aria-pressed', String(v)); };
          b.addEventListener('pointerdown', (e) => { e.preventDefault(); b.setPointerCapture && b.setPointerCapture(e.pointerId); press(true); });
          ['pointerup', 'pointercancel', 'lostpointercapture'].forEach((ev) => b.addEventListener(ev, () => press(false)));
          b.addEventListener('keydown', (e) => { if ((e.key === ' ' || e.key === 'Enter') && !e.repeat) { e.preventDefault(); press(true); } });
          b.addEventListener('keyup', (e) => { if (e.key === ' ' || e.key === 'Enter') press(false); });
        });
        $$('[data-toggle]', root).forEach((b) => b.addEventListener('click', () => {
          const on = b.getAttribute('aria-pressed') !== 'true';
          if (pin(b.dataset.toggle) != null) socket.emit('mock_trigger_pin', { pin: pin(b.dataset.toggle), state: on ? 'HIGH' : 'LOW' });
          b.setAttribute('aria-pressed', String(on));
        }));
      }
      // live pins
      this.onDebug = (p) => {
        if (!root.isConnected) return;
        $('[data-pins]', root).innerHTML = p.pins.map((r) => `<tr><td class="mono">${r.pin}</td><td>${esc(r.purpose)}</td><td>${esc(r.direction)}</td><td><span class="badge badge-${r.state === 'HIGH' ? 'high' : r.state === 'LOW' ? 'low' : 'na'}" data-pin-state="${esc(r.name)}">${esc(r.state)}</span></td></tr>`).join('');
        $$('[data-toggle]', root).forEach((b) => {
          const row = p.pins.find((r) => r.name === b.dataset.toggle);
          if (row) b.setAttribute('aria-pressed', String(row.state === 'HIGH'));
        });
      };
      socket.on('debug_data', this.onDebug);
      const poll = () => S.connected && socket.emit('get_debug_data');
      poll();
      this.timer = setInterval(poll, 1000);
    },
    leave() { clearInterval(this.timer); socket.off('debug_data', this.onDebug); },
    update(d, root) {
      $('[data-travel]', root).textContent = d.reference_door_endstops_ms === 'Not set' ? 'Not calibrated' : `${(parseFloat(d.reference_door_endstops_ms) / 1000).toFixed(1)} s`;
      $('[data-hw-state]', root).textContent = d.reference_running ? 'Calibrating…' : STATE_LABEL[d.state] || d.state;
    },
  };

  // ── Logs ─────────────────────────────────────────────────────────────
  const LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR'];
  const Logs = {
    title: 'Logs',
    level: 'INFO', newest: true, lines: [], auto: false,
    render() {
      return `<section class="card" aria-label="Logs">
        <div class="log-toolbar">
          <label class="sr-only" for="log-file">Log file</label><select class="input" id="log-file" data-file></select>
          <label class="sr-only" for="log-search">Search</label><input class="input" id="log-search" type="search" placeholder="Search messages" data-search>
          <label class="sr-only" for="log-logger">Component</label><select class="input" id="log-logger" data-logger><option value="">All components</option></select>
        </div>
        <div class="row">
          <div class="level-chips" role="group" aria-label="Minimum level">${LEVELS.map((l) => `<button type="button" data-level="${l}" aria-pressed="${l === this.level}">${l === 'WARNING' ? 'WARN' : l}+</button>`).join('')}</div>
          <span class="spacer"></span>
          <button class="btn" type="button" data-sort>${this.newest ? 'Newest first' : 'Oldest first'}</button>
          <button class="btn" type="button" data-auto aria-pressed="${this.auto}">${icon('refresh', 18)}Auto-refresh ${this.auto ? 'on' : 'off'}</button>
        </div>
        <div class="log-list" data-lines role="log" aria-label="Log lines"><p class="empty">Loading…</p></div>
        <p class="muted" data-count></p>
      </section>`;
    },
    async loadFiles(root) {
      try {
        const files = await api('/api/logs');
        const sel = $('[data-file]', root);
        sel.innerHTML = files.map((f) => `<option value="${esc(f.name)}">${esc(f.name)} (${(f.size / 1024).toFixed(1)} KB)</option>`).join('') || '<option value="">No log files</option>';
        await this.loadLines(root);
      } catch (e) { $('[data-lines]', root).innerHTML = `<p class="empty">${esc(e.message)}</p>`; }
    },
    async loadLines(root) {
      const name = $('[data-file]', root).value;
      if (!name) { $('[data-lines]', root).innerHTML = '<p class="empty">No log files yet.</p>'; return; }
      try {
        this.lines = await api('/api/logs/' + encodeURIComponent(name));
        if (!root.isConnected) return;
        const loggers = [...new Set(this.lines.map((l) => l.lg).filter(Boolean))].sort();
        const sel = $('[data-logger]', root), cur = sel.value;
        sel.innerHTML = '<option value="">All components</option>' + loggers.map((l) => `<option>${esc(l)}</option>`).join('');
        sel.value = loggers.includes(cur) ? cur : '';
        this.draw(root);
      } catch (e) { $('[data-lines]', root).innerHTML = `<p class="empty">${esc(e.message)}</p>`; }
    },
    draw(root) {
      const q = $('[data-search]', root).value.trim().toLowerCase();
      const lg = $('[data-logger]', root).value;
      const min = LEVELS.indexOf(this.level);
      const rank = (lv) => (lv === 'CRITICAL' ? 3 : lv === 'RAW' ? 1 : LEVELS.indexOf(lv));
      let rows = this.lines.filter((l) => rank(l.lv) >= min && (!lg || l.lg === lg) && (!q || l.m.toLowerCase().includes(q)));
      const total = rows.length;
      if (this.newest) rows = rows.slice().reverse();
      rows = rows.slice(0, 1500);
      $('[data-lines]', root).innerHTML = rows.map((l) => `<div class="log-line lvl-${esc(l.lv)}" data-log-line><span class="ts">${esc(l.t)}</span><span class="lv lv-${esc(l.lv)}">${esc(l.lv)}</span><span class="lg" title="${esc(l.lg)}">${esc(l.lg)}</span><span class="msg">${esc(l.m)}</span></div>`).join('') || '<p class="empty">No matching lines.</p>';
      $('[data-count]', root).textContent = `Showing ${rows.length} of ${total} matching lines (${this.lines.length} in file).`;
    },
    enter(root) {
      this.loadFiles(root);
      $('[data-file]', root).addEventListener('change', () => this.loadLines(root));
      $('[data-search]', root).addEventListener('input', () => this.draw(root));
      $('[data-logger]', root).addEventListener('change', () => this.draw(root));
      $$('[data-level]', root).forEach((b) => b.addEventListener('click', () => {
        this.level = b.dataset.level;
        $$('[data-level]', root).forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
        this.draw(root);
      }));
      $('[data-sort]', root).addEventListener('click', (e) => { this.newest = !this.newest; e.currentTarget.textContent = this.newest ? 'Newest first' : 'Oldest first'; this.draw(root); });
      const autoBtn = $('[data-auto]', root);
      const setAuto = (on) => {
        this.auto = on;
        clearInterval(this.timer);
        if (on) this.timer = setInterval(() => this.loadLines(root), 3000);
        autoBtn.setAttribute('aria-pressed', String(on));
        autoBtn.innerHTML = `${icon('refresh', 18)}Auto-refresh ${on ? 'on' : 'off'}`;
      };
      autoBtn.addEventListener('click', () => setAuto(!this.auto));
      setAuto(this.auto);
    },
    leave() { clearInterval(this.timer); },
  };

  // ── System & updates ─────────────────────────────────────────────────
  function urlBase64ToUint8Array(b64) {
    const pad = '='.repeat((4 - (b64.length % 4)) % 4);
    const raw = atob((b64 + pad).replace(/-/g, '+').replace(/_/g, '/'));
    return Uint8Array.from(raw, (c) => c.charCodeAt(0));
  }
  async function waitForServer(overlayEl, previousVersion) {
    const started = Date.now();
    await new Promise((r) => setTimeout(r, 4000));
    while (Date.now() - started < 180000) {
      try {
        const v = await api('/version');
        if (v) { overlayEl.remove(); location.reload(); return; }
      } catch (e) { /* still restarting */ }
      await new Promise((r) => setTimeout(r, 3000));
    }
    overlayEl.remove();
    toast('The coop controller did not come back yet - try reloading later.', 'error', 8000);
  }
  const System = {
    title: 'System & updates',
    render() {
      const pref = themePref();
      return `<div class="stack" style="gap:20px">
        <div class="grid-2">
          <section class="card" aria-label="Device">
            <div class="card-head"><h2>Device</h2></div>
            <dl class="kv">
              <dt>Version</dt><dd data-version>—</dd>
              <dt>Running for</dt><dd data-bind="uptime">—</dd>
              <dt>Python</dt><dd data-bind="python_version">—</dd>
              <dt>CPU temperature</dt><dd data-bind="cpu_temp">—</dd>
            </dl>
            <div class="stack" style="gap:10px">
              <div><div class="row"><span>CPU load</span><span class="spacer"></span><strong data-cpu>—</strong></div><div class="bar"><span data-bar="cpu" style="width:0"></span></div></div>
              <div><div class="row"><span>Memory</span><span class="spacer"></span><strong data-ram>—</strong></div><div class="bar"><span data-bar="ram" style="width:0"></span></div></div>
              <div><div class="row"><span>Storage</span><span class="spacer"></span><strong data-disk>—</strong></div><div class="bar"><span data-bar="disk" style="width:0"></span></div></div>
            </div>
          </section>
          <section class="card" aria-label="Health">
            <div class="card-head"><h2>Background services</h2><button class="btn btn-ghost" type="button" data-health-refresh>${icon('refresh', 18)}Check</button></div>
            <div class="list" data-health><p class="muted">Checking…</p></div>
          </section>
        </div>
        <div class="grid-2">
          <section class="card" aria-label="Time">
            <div class="card-head"><h2>Date &amp; time</h2></div>
            <dl class="kv"><dt>Coop controller</dt><dd data-bind="os_time_local_str">—</dd><dt>This device</dt><dd data-local-time>—</dd><dt>Difference</dt><dd data-skew>—</dd></dl>
            <div class="form-actions"><button class="btn btn-primary" type="button" data-sync-time>Use this device's time</button></div>
            <form class="row" data-set-time novalidate style="align-items:flex-end">
              <div class="field" style="flex-grow:1"><label for="sys-time">Or set a time</label><input class="input" id="sys-time" type="datetime-local" step="1" required></div>
              <button class="btn" type="submit">Apply</button>
            </form>
          </section>
          <section class="card" aria-label="Updates">
            <div class="card-head"><h2>Updates &amp; power</h2></div>
            <p class="muted">Updating downloads the latest version and restarts the controller (about 15 seconds). The door keeps its position.</p>
            <div class="form-actions"><button class="btn btn-primary" type="button" data-update>Update &amp; restart</button><button class="btn btn-danger" type="button" data-reboot>Restart device</button></div>
          </section>
        </div>
        <div class="grid-2">
          <section class="card" aria-label="Appearance">
            <div class="card-head"><h2>Appearance</h2></div>
            <div class="seg" role="radiogroup" aria-label="Theme">${[['light', 'Light', 'sun'], ['dark', 'Dark', 'moon'], ['system', 'Automatic', 'sliders']].map(([v, l, ic]) => `<button type="button" role="radio" data-theme-pref="${v}" aria-checked="${pref === v}">${icon(ic, 18)}${l}</button>`).join('')}</div>
          </section>
          <section class="card" aria-label="Notifications">
            <div class="card-head"><h2>Notifications</h2></div>
            <p data-push-status class="muted">Checking…</p>
            <div class="form-actions"><button class="btn btn-primary" type="button" data-push>Enable notifications</button><button class="btn" type="button" data-install hidden>Install as app</button></div>
          </section>
        </div>
        <section class="card" aria-label="Internal state">
          <details data-internals><summary style="cursor:pointer;font-weight:700;min-height:44px;display:flex;align-items:center">Show internal state (for troubleshooting)</summary><pre class="mono" style="font-size:12px;overflow:auto;max-height:420px;white-space:pre-wrap" data-internals-body>Loading…</pre></details>
        </section>
      </div>`;
    },
    async loadHealth(root) {
      try {
        const h = await fetch('/api/health').then((r) => r.json());
        const names = { door: 'Door control', environment: 'Sensors', broadcast: 'Live updates', 'csv-log': 'Data logging', camera: 'Camera', 'door-simulator': 'Door simulator', 'wifi-watchdog': 'Wi-Fi watchdog' };
        $('[data-health]', root).innerHTML = Object.entries(h.workers).map(([n, alive]) => `<div class="list-row"><span class="spacer">${esc(names[n] || n)}</span><span class="chip ${alive ? 'chip-ok' : 'chip-danger'}" data-worker="${esc(n)}">${alive ? 'Running' : 'Stopped'}</span></div>`).join('') || '<p class="muted">No background services reported.</p>';
      } catch (e) { $('[data-health]', root).innerHTML = `<p class="muted">${esc(e.message)}</p>`; }
    },
    async pushStatus(root) {
      const el = $('[data-push-status]', root), btn = $('[data-push]', root);
      if (!window.COOP.vapidPublicKey) { el.textContent = 'Push notifications are not set up on the controller (VAPID keys missing - run generateVapidPair.py).'; btn.disabled = true; return; }
      if (!('serviceWorker' in navigator) || !('PushManager' in window)) { el.textContent = "This browser doesn't support push notifications (on iPhone: add the app to the home screen first)."; btn.disabled = true; return; }
      if (Notification.permission === 'denied') { el.textContent = 'Notifications are blocked in the browser settings for this site.'; btn.disabled = true; return; }
      try {
        const reg = await navigator.serviceWorker.getRegistration();
        const sub = reg && await reg.pushManager.getSubscription();
        el.textContent = sub ? 'This device receives alerts for door errors and blocked schedules.' : 'Get an alert on this device when the door has a problem.';
        btn.textContent = sub ? 'Re-register this device' : 'Enable notifications';
      } catch (e) { el.textContent = 'Get an alert on this device when the door has a problem.'; }
    },
    enter(root) {
      api('/version').then((v) => { $('[data-version]', root).textContent = v.version; this.version = v.version; }).catch(() => {});
      this.loadHealth(root);
      $('[data-health-refresh]', root).addEventListener('click', (e) => busy(e.currentTarget, () => this.loadHealth(root)));
      $$('[data-theme-pref]', root).forEach((b) => b.addEventListener('click', () => {
        setTheme(b.dataset.themePref);
        $$('[data-theme-pref]', root).forEach((x) => x.setAttribute('aria-checked', String(x === b)));
      }));
      $('[data-sync-time]', root).addEventListener('click', (e) => setDeviceTime(e.currentTarget, new Date()));
      const setForm = $('[data-set-time]', root);
      setForm.addEventListener('submit', (e) => {
        e.preventDefault();
        const v = $('#sys-time').value;
        if (!v) { $('#sys-time').setAttribute('aria-invalid', 'true'); toast('Pick a date and time first', 'error'); return; }
        $('#sys-time').removeAttribute('aria-invalid');
        setDeviceTime(e.submitter, new Date(v));
      });
      $('[data-update]', root).addEventListener('click', async (e) => {
        const btn = e.currentTarget;
        const ok = await confirmDialog({ title: 'Update and restart?', body: 'The controller downloads the latest version and restarts. This page reconnects automatically.', confirm: 'Update now' });
        if (!ok) return;
        try {
          await busy(btn, () => api('/update', { method: 'POST' }));
          waitForServer(overlay('Updating…', 'The coop controller is restarting. This page reconnects automatically.'), this.version);
        } catch (err) { toast(err.message, 'error', 6000); }
      });
      $('[data-reboot]', root).addEventListener('click', async (e) => {
        const btn = e.currentTarget;
        const ok = await confirmDialog({ title: 'Restart the device?', body: 'The Raspberry Pi reboots. The door stays where it is; the page reconnects in about a minute.', confirm: 'Restart', danger: true });
        if (!ok) return;
        try {
          await busy(btn, () => api('/api/restart', { method: 'POST' }));
          waitForServer(overlay('Restarting…', 'The device is rebooting. This page reconnects automatically.'));
        } catch (err) { toast(err.message, 'error', 6000); }
      });
      this.pushStatus(root);
      $('[data-push]', root).addEventListener('click', (e) => busy(e.currentTarget, async () => {
        try {
          const perm = await Notification.requestPermission();
          if (perm !== 'granted') { toast('Notifications were not allowed', 'error'); this.pushStatus(root); return; }
          const reg = await navigator.serviceWorker.ready;
          const sub = (await reg.pushManager.getSubscription()) || await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlBase64ToUint8Array(window.COOP.vapidPublicKey) });
          await api('/subscribe', { method: 'POST', body: sub.toJSON() });
          toast('Notifications enabled on this device');
        } catch (err) { toast(`Could not enable notifications: ${err.message}`, 'error', 6000); }
        this.pushStatus(root);
      }));
      const install = $('[data-install]', root);
      install.hidden = !S.installPrompt;
      install.addEventListener('click', async () => { if (!S.installPrompt) return; S.installPrompt.prompt(); await S.installPrompt.userChoice; S.installPrompt = null; install.hidden = true; });
      const det = $('[data-internals]', root);
      this.onDebug = (p) => { if (det.open) $('[data-internals-body]', root).textContent = JSON.stringify({ state: p.global_vars, door: p.door_constants, system: p.system, threads: p.threads }, null, 2); };
      socket.on('debug_data', this.onDebug);
      det.addEventListener('toggle', () => { if (det.open) socket.emit('get_debug_data'); });
      this.clock = setInterval(() => { const el = $('[data-local-time]', root); if (el) el.textContent = localTimeString(new Date()); }, 1000);
    },
    leave() { socket.off('debug_data', this.onDebug); clearInterval(this.clock); },
    update(d, root) {
      bindValues(root, d);
      $('[data-local-time]', root).textContent = localTimeString(new Date());
      const skew = Math.round(Date.now() / 1000 - d.os_timestamp);
      $('[data-skew]', root).textContent = Math.abs(skew) <= 5 ? 'In sync' : `${Math.abs(skew)} s ${skew > 0 ? 'behind' : 'ahead'}`;
      const set = (k, text, p) => { $(`[data-${k}]`, root).textContent = text; $(`[data-bar="${k}"]`, root).style.width = `${Math.min(100, p || 0)}%`; };
      set('cpu', `${d.cpu_percent} %`, num(d.cpu_percent));
      set('ram', `${Math.round(num(d.ram_used_mb))} / ${Math.round(num(d.ram_total_mb))} MB`, num(d.ram_percent));
      set('disk', `${d.disk_used_gb} / ${d.disk_total_gb} GB`, num(d.disk_percent));
    },
  };

  // ── More (phone) ─────────────────────────────────────────────────────
  const More = {
    title: 'More',
    render() {
      const link = (name, label, ic, valAttr = '') => `<a href="${href(name)}" data-more="${name}">${icon(ic, 22)}<span>${label}</span><span class="val" ${valAttr}></span><span class="chev">${icon('chevron', 18)}</span></a>`;
      return `<div class="stack" style="gap:18px">
        <div class="card" style="padding:14px 16px;flex-direction:row;align-items:center"><span class="conn" data-conn><span class="dot"></span><span data-conn-text>—</span></span><span class="spacer"></span><span class="muted" style="font-size:13px">Version <span data-bind="version">${esc(S.version || '—')}</span></span></div>
        <nav class="more-group" aria-label="Coop"><span class="eyebrow">Coop</span><div class="card">${link('camera', 'Camera', 'camera')}</div></nav>
        <nav class="more-group" aria-label="Setup"><span class="eyebrow">Setup</span><div class="card">${link('schedule', 'Schedule & location', 'clock', 'data-more-mode')}${link('network', 'Network', 'wifi')}${link('hardware', 'Door & hardware', 'chip', 'data-more-travel')}</div></nav>
        <nav class="more-group" aria-label="Device"><span class="eyebrow">Device</span><div class="card">${link('logs', 'Logs', 'list')}${link('system', 'System & updates', 'sliders')}</div></nav>
      </div>`;
    },
    update(d, root) {
      $('[data-more-mode]', root).textContent = MODE_LABEL[d.mode] || '';
      $('[data-more-travel]', root).textContent = d.reference_door_endstops_ms === 'Not set' ? 'Not calibrated' : `${(parseFloat(d.reference_door_endstops_ms) / 1000).toFixed(1)} s`;
      paintConnection();
    },
  };

  const PAGES = { home: Home, climate: Climate, history: History, camera: Camera, schedule: Schedule, network: Network, hardware: Hardware, logs: Logs, system: System, more: More };

  // ── router ───────────────────────────────────────────────────────────
  function routeName() {
    const name = (location.hash.replace(/^#\/?/, '').split(/[?/]/)[0] || 'home').toLowerCase();
    return PAGES[name] ? name : 'home';
  }
  function renderPage() {
    const name = routeName();
    if (S.page && S.page.leave) { try { S.page.leave(); } catch (e) { console.error(e); } }
    const page = PAGES[name];
    S.page = page; S.pageName = name;
    // A fresh container per visit: async work started by a page checks
    // root.isConnected and stops touching the DOM once the user moved on.
    const root = document.createElement('div');
    root.className = 'page-root';
    root.innerHTML = page.render();
    $('#page').replaceChildren(root);
    $('#page').dataset.page = name;
    page.root = root;
    $('#page-title').textContent = page.title;
    document.title = page.title === 'Home' ? 'Dinky Coop' : `${page.title} · Dinky Coop`;
    markNav(name);
    if (page.enter) Promise.resolve(page.enter(root)).catch((e) => console.error(e));
    if (S.data && page.update) page.update(S.data, root);
    if (S.camFrame) paintCamera();
  }
  window.addEventListener('hashchange', () => { renderPage(); $('#main').focus({ preventScroll: true }); window.scrollTo(0, 0); });

  // ── live data ────────────────────────────────────────────────────────
  function paintConnection() {
    const state = S.connected ? 'live' : (Date.now() - S.disconnectedAt > 2500 ? 'offline' : 'reconnecting');
    $$('[data-conn]').forEach((el) => { el.dataset.state = state === 'live' ? '' : state; });
    $$('[data-conn-text]').forEach((el) => { el.textContent = { live: 'Connected · live', reconnecting: 'Reconnecting…', offline: 'Offline - reconnecting' }[state]; });
  }
  function paintCamera() {
    $$('[data-cam-img]').forEach((img) => { img.src = S.camFrame; img.hidden = false; });
    $$('[data-cam-empty]').forEach((el) => { el.hidden = true; });
  }
  function onData(d) {
    S.data = d;
    $$('[data-bind="uptime_short"]').forEach((el) => { el.textContent = uptimeShort(d.uptime); });
    renderBanners();
    if (S.page && S.page.update) {
      try { S.page.update(d, S.page.root); } catch (e) { console.error(e); }
    }
  }

  socket.on('connect', () => { S.connected = true; paintConnection(); renderBanners(); loadSettings(); });
  socket.on('disconnect', () => { S.connected = false; S.disconnectedAt = Date.now(); paintConnection(); setTimeout(() => { paintConnection(); renderBanners(); }, 3000); });
  socket.on('connect_error', () => { S.connected = false; paintConnection(); });
  socket.on('data', onData);
  socket.on('camera', (b64) => { S.camFrame = 'data:image/jpeg;base64,' + b64; S.camFrames.push(Date.now()); paintCamera(); });

  // ── boot ─────────────────────────────────────────────────────────────
  function boot() {
    renderNav();
    paintThemeButton();
    $('#theme-toggle').addEventListener('click', () => setTheme(effectiveTheme() === 'dark' ? 'light' : 'dark'));
    matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { if (themePref() === 'system') paintThemeButton(); if (S.page && S.page.themeChanged) S.page.themeChanged(); });
    $('#today-label').textContent = new Date().toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' });
    fetch('/version').then((r) => r.json()).then((v) => { S.version = v.version; $$('[data-bind="version"]').forEach((el) => { el.textContent = v.version; }); }).catch(() => {});
    renderPage();
    paintConnection();
    if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
    window.addEventListener('beforeinstallprompt', (e) => { e.preventDefault(); S.installPrompt = e; const b = $('[data-install]'); if (b) b.hidden = false; });
    setInterval(() => { if (!S.connected) { paintConnection(); renderBanners(); } }, 1000);
  }
  boot();
  window.__coop = { S, PAGES, ack, socket };  // for debugging & end-to-end tests
})();
