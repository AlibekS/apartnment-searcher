/* Агрегатор квартир — клиентская часть. Без сборки и зависимостей. */
(() => {
  'use strict';

  const CRM_KEY = 'aptCRM.v1';
  const FILTERS_KEY = 'aptFilters.v1';
  const STATUSES = [
    ['', 'Не звонил'],
    ['called', 'Звонил'],
    ['viewing', 'Просмотр назначен'],
    ['agreed', 'Договорился'],
    ['no', 'Не брать'],
  ];
  const FALLBACK_DEFAULTS = {
    rooms: [2], price_min: null, price_max: 20000000, floor_min: 1, floor_max: 3,
    not_first_floor: false, year_min: 2000, hide_unknown_year: false, region: 'all',
    secondary_only: true, hide_utility_warnings: true, only_price_drop: false,
    search: '', sort: 'astana_new', budget: 20000000,
  };

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const state = {
    listings: [],
    meta: {},
    defaults: { ...FALLBACK_DEFAULTS },
    filters: null,
    view: 'all', // all | fav | hidden
    crm: loadCRM(),
    terms: { warn: [], good: [] },
  };

  // ------------------------------------------------------------ утилиты
  function esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }
  function safeUrl(u) {
    try {
      const url = new URL(String(u), location.href);
      return url.protocol === 'http:' || url.protocol === 'https:' ? url.href : '';
    } catch { return ''; }
  }
  const fmt = (n) => (n == null || isNaN(n) ? '—' : Math.round(n).toLocaleString('ru-RU'));
  const money = (n) => `${fmt(n)} ₸`;
  const mln = (n) => (n >= 1e6 ? `${(n / 1e6).toLocaleString('ru-RU', { maximumFractionDigits: 2 })} млн` : fmt(n));
  const digits = (s) => { const d = String(s || '').replace(/\D/g, ''); return d ? Number(d) : null; };
  const numOrNull = (v) => (v === '' || v == null || isNaN(Number(v)) ? null : Number(v));
  const daysSince = (iso) => (iso ? (Date.now() - new Date(iso).getTime()) / 864e5 : 999);
  function fmtDate(iso, withTime) {
    if (!iso) return '—';
    const d = new Date(iso);
    if (isNaN(d)) return esc(iso);
    return d.toLocaleString('ru-RU', withTime
      ? { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' }
      : { day: 'numeric', month: 'short', year: 'numeric' });
  }
  function debounce(fn, ms) {
    let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
  }
  function toast(msg) {
    const el = $('#toast'); el.textContent = msg; el.hidden = false;
    clearTimeout(toast.t); toast.t = setTimeout(() => { el.hidden = true; }, 2600);
  }
  function lsGet(key) { try { return JSON.parse(localStorage.getItem(key)); } catch { return null; } }
  function lsSet(key, val) { try { localStorage.setItem(key, JSON.stringify(val)); return true; } catch { return false; } }

  // ------------------------------------------------------------ мини-CRM
  function loadCRM() {
    const d = lsGet(CRM_KEY);
    return d && typeof d === 'object' && d.items ? d : { items: {} };
  }
  function saveCRM() {
    if (!lsSet(CRM_KEY, state.crm)) toast('Не удалось сохранить заметки (память браузера недоступна)');
    updateCounters();
  }
  /** Запись CRM для карточки. Ищем по всем ID источников — если склейка дублей поменялась, заметки не теряются. */
  function crmOf(card) {
    const items = state.crm.items;
    if (items[card.id]) return items[card.id];
    for (const k of card.keys || []) {
      const alt = k.replace(':', '-');
      if (items[alt]) { items[card.id] = items[alt]; delete items[alt]; return items[card.id]; }
    }
    return null;
  }
  function crmSet(card, patch) {
    const cur = crmOf(card) || {};
    const next = { ...cur, ...patch, updated: new Date().toISOString() };
    if (!next.fav && !next.hidden && !next.status && !next.note) delete state.crm.items[card.id];
    else {
      next.title = `${card.rooms ?? '?'}-комн., ${card.area ?? '?'} м², ${card.locality || ''} ${card.complex || ''}`.trim();
      next.price = card.price;
      state.crm.items[card.id] = next;
    }
    saveCRM();
  }

  function exportCRM() {
    const blob = new Blob([JSON.stringify({ version: 1, exported_at: new Date().toISOString(), ...state.crm }, null, 1)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `kvartiry-zametki-${new Date().toISOString().slice(0, 10)}.json`;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    toast('Файл с заметками скачан');
  }
  function importCRM(file) {
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const data = JSON.parse(reader.result);
        const items = data && data.items;
        if (!items || typeof items !== 'object') throw new Error('нет поля items');
        let added = 0;
        for (const [id, it] of Object.entries(items)) {
          if (!it || typeof it !== 'object') continue;
          const cur = state.crm.items[id];
          if (!cur || String(it.updated || '') > String(cur.updated || '')) {
            state.crm.items[id] = {
              fav: !!it.fav, hidden: !!it.hidden,
              status: typeof it.status === 'string' ? it.status : '',
              note: typeof it.note === 'string' ? it.note.slice(0, 2000) : '',
              updated: String(it.updated || ''), title: String(it.title || ''), price: Number(it.price) || null,
            };
            added++;
          }
        }
        saveCRM(); render();
        toast(`Импортировано записей: ${added}`);
      } catch (e) {
        toast('Не удалось прочитать файл: ' + e.message);
      }
    };
    reader.readAsText(file);
  }

  // ------------------------------------------------------------ фильтры
  function loadFilters() {
    const saved = lsGet(FILTERS_KEY);
    state.filters = saved && typeof saved === 'object' ? { ...state.defaults, ...saved } : { ...state.defaults };
  }
  function saveFilters() { lsSet(FILTERS_KEY, state.filters); }

  function filtersToUI() {
    const f = state.filters;
    $$('#roomChips .chip').forEach((b) => b.setAttribute('aria-pressed', String(f.rooms.includes(Number(b.dataset.room)))));
    $$('#regionSeg button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.region === f.region)));
    $('#sortSel').value = f.sort;
    $('#priceMin').value = f.price_min ? fmt(f.price_min) : '';
    $('#priceMax').value = f.price_max ? fmt(f.price_max) : '';
    $('#floorMin').value = f.floor_min ?? '';
    $('#floorMax').value = f.floor_max ?? '';
    $('#notFirst').checked = !!f.not_first_floor;
    $('#yearMin').value = f.year_min ?? '';
    $('#yearKnown').checked = !!f.hide_unknown_year;
    $('#searchInp').value = f.search || '';
    $('#secondaryOnly').checked = !!f.secondary_only;
    $('#hideWarn').checked = !!f.hide_utility_warnings;
    $('#onlyDrop').checked = !!f.only_price_drop;
    const changed = countChanged();
    $('#filtersBadge').textContent = changed ? `изм. ${changed}` : '';
  }
  function countChanged() {
    const keys = ['price_min', 'price_max', 'floor_min', 'floor_max', 'not_first_floor', 'year_min', 'hide_unknown_year', 'search', 'secondary_only', 'hide_utility_warnings', 'only_price_drop'];
    return keys.filter((k) => JSON.stringify(state.filters[k] ?? null) !== JSON.stringify(state.defaults[k] ?? null)).length;
  }
  function uiToFilters() {
    const f = state.filters;
    f.price_min = digits($('#priceMin').value);
    f.price_max = digits($('#priceMax').value);
    f.floor_min = numOrNull($('#floorMin').value);
    f.floor_max = numOrNull($('#floorMax').value);
    f.not_first_floor = $('#notFirst').checked;
    f.year_min = numOrNull($('#yearMin').value);
    f.hide_unknown_year = $('#yearKnown').checked;
    f.search = $('#searchInp').value.trim();
    f.secondary_only = $('#secondaryOnly').checked;
    f.hide_utility_warnings = $('#hideWarn').checked;
    f.only_price_drop = $('#onlyDrop').checked;
    saveFilters();
    const changed = countChanged();
    $('#filtersBadge').textContent = changed ? `изм. ${changed}` : '';
    render();
  }

  function matches(c, f) {
    if (f.rooms.length) {
      const r = c.rooms || 0;
      const ok = f.rooms.some((x) => (x >= 4 ? r >= 4 : r === x));
      if (!ok) return false;
    }
    if (f.price_min && c.price < f.price_min) return false;
    if (f.price_max && c.price > f.price_max) return false;
    if (c.floor != null) {
      if (f.floor_min && c.floor < f.floor_min) return false;
      if (f.floor_max && c.floor > f.floor_max) return false;
      if (f.not_first_floor && c.floor === 1) return false;
    }
    if (f.year_min && c.year && c.year < f.year_min) return false;
    if (f.hide_unknown_year && !c.year) return false;
    if (f.region !== 'all' && c.region !== f.region) return false;
    if (f.secondary_only && c.market === 'new') return false;
    if (f.hide_utility_warnings && c.has_utility_warning) return false;
    if (f.only_price_drop && !c.price_drop) return false;
    if (f.search) {
      const hay = c._search || (c._search = [c.title, c.locality, c.district, c.complex, c.address, c.description, c.wall]
        .join(' ').toLowerCase().replace(/ё/g, 'е'));
      const words = f.search.toLowerCase().replace(/ё/g, 'е').split(/\s+/).filter(Boolean);
      if (!words.every((w) => hay.includes(w))) return false;
    }
    return true;
  }

  function sortCards(list, how) {
    const by = {
      astana_new: (a, b) => (a.region === 'astana' ? 0 : 1) - (b.region === 'astana' ? 0 : 1) || String(b.first_seen).localeCompare(String(a.first_seen)) || a.price - b.price,
      price_asc: (a, b) => a.price - b.price,
      sqm_asc: (a, b) => (a.price_per_sqm || 9e15) - (b.price_per_sqm || 9e15),
      below_market: (a, b) => (a.market_deviation_pct ?? 999) - (b.market_deviation_pct ?? 999),
      new: (a, b) => String(b.first_seen).localeCompare(String(a.first_seen)),
    }[how] || (() => 0);
    return list.sort(by);
  }

  function visibleCards() {
    const f = state.filters;
    let list;
    if (state.view === 'fav') list = state.listings.filter((c) => crmOf(c)?.fav);
    else if (state.view === 'hidden') list = state.listings.filter((c) => crmOf(c)?.hidden);
    else list = state.listings.filter((c) => !crmOf(c)?.hidden && matches(c, f));
    return sortCards(list, f.sort);
  }

  // ------------------------------------------------------------ рендер карточек
  const PAGE = 30;
  let shown = PAGE;
  let observer;

  function render(keepCount) {
    if (!keepCount) shown = PAGE;
    const list = visibleCards();
    const grid = $('#grid');
    grid.innerHTML = list.slice(0, shown).map(cardHTML).join('');
    $('#empty').hidden = list.length > 0;
    const viewName = state.view === 'fav' ? 'Избранное' : state.view === 'hidden' ? 'Скрытые' : 'По фильтрам';
    const extra = state.view === 'all' ? '' : ' (фильтры не применяются)';
    $('#resultLine').textContent = `${viewName}: ${list.length} из ${state.listings.length}${extra}`;
    updateCounters();
    // подгрузка следующих карточек при прокрутке
    if (observer) observer.disconnect();
    if (list.length > shown) {
      const sentinel = document.createElement('div');
      sentinel.style.height = '1px';
      grid.appendChild(sentinel);
      observer = new IntersectionObserver((entries) => {
        if (entries.some((e) => e.isIntersecting)) { shown += PAGE; render(true); }
      }, { rootMargin: '600px' });
      observer.observe(sentinel);
    }
  }

  function updateCounters() {
    $('#favCount').textContent = state.listings.filter((c) => crmOf(c)?.fav).length || '';
    $('#hiddenCount').textContent = state.listings.filter((c) => crmOf(c)?.hidden).length || '';
  }

  function devHTML(c) {
    if (c.market_deviation_pct == null) return '';
    const d = c.market_deviation_pct;
    const cls = d <= -3 ? 'dev--good' : d >= 8 ? 'dev--bad' : 'dev--flat';
    const sign = d > 0 ? '+' : d < 0 ? '−' : '';
    const title = `Медиана ${fmt(c.market_median_sqm)} ₸/м² по ${c.market_sample} похожим квартирам`;
    return `<span class="dev ${cls}" title="${esc(title)}">${sign}${Math.abs(d).toLocaleString('ru-RU')}% от рынка</span>`;
  }

  function dropHTML(c) {
    const d = c.price_drop;
    if (!d) return '';
    return `<div class="drop">📉 было <s>${esc(mln(d.old))}</s> → <b>${esc(mln(d.new))}</b> (${esc(String(d.pct).replace('.', ','))}%, ${esc(fmtDate(d.date))})${d.entered_budget ? ' · <span class="inbudget">вошла в бюджет</span>' : ''}</div>`;
  }

  function tagsHTML(c) {
    const t = [];
    if (c.rooms) t.push(`${c.rooms} комн.`);
    if (c.area) t.push(`${String(c.area).replace('.', ',')} м²`);
    if (c.floor) t.push(`${c.floor}${c.floors_total ? '/' + c.floors_total : ''} эт.`);
    if (c.year) t.push(`${c.year} г.`);
    if (c.wall) t.push(c.wall);
    if (c.heating) t.push(`🔥 ${c.heating}`);
    let html = t.map((x) => `<span class="tag">${esc(x)}</span>`).join('');
    if (c.market === 'new') html += '<span class="tag tag--muted" title="Похоже на стройку / ДДУ">🏗 новостройка</span>';
    return html;
  }

  function gisUrl(c) {
    const city = 'astana';
    if (c.lat && c.lon) return `https://2gis.kz/${city}/geo/${c.lon},${c.lat}?m=${c.lon},${c.lat}/17`;
    const q = [c.region === 'suburb' ? c.locality : 'Астана', c.complex, (c.address || '').replace(/^.*?р-н,?\s*/, '')]
      .filter(Boolean).join(' ');
    return `https://2gis.kz/${city}/search/${encodeURIComponent(q)}`;
  }

  function sourcesHTML(c) {
    return c.sources.map((s) => {
      const name = s.site === 'krisha' ? 'Krisha' : s.site === 'olx' ? 'OLX' : s.site;
      const url = safeUrl(s.url);
      return `<a class="btn btn--sm src src--${esc(s.site)}" href="${esc(url)}" target="_blank" rel="noopener noreferrer" data-stop>${esc(name)} · <b>${esc(mln(s.price))}</b></a>`;
    }).join('');
  }

  function crmHTML(c, crm) {
    const st = crm?.status || '';
    const cls = st === 'agreed' ? 'status-agreed' : st === 'no' ? 'status-no' : '';
    return `<div class="crm" data-stop>
      <select data-act="status" class="${cls}" aria-label="Статус">${STATUSES.map(([v, l]) => `<option value="${v}"${v === st ? ' selected' : ''}>${l}</option>`).join('')}</select>
      <input type="text" data-act="note" placeholder="Заметка…" maxlength="2000" value="${esc(crm?.note || '')}" aria-label="Заметка">
    </div>`;
  }

  function sliderHTML(c, badges) {
    const photos = (c.photos || []).map(safeUrl).filter(Boolean);
    const img = photos.length
      ? `<img src="${esc(photos[0])}" alt="Фото квартиры" loading="lazy" decoding="async" referrerpolicy="no-referrer" data-idx="0">`
      : '<div class="noimg">Нет фото</div>';
    const nav = photos.length > 1
      ? `<button class="slider__nav slider__nav--prev" data-act="prev" aria-label="Предыдущее фото" data-stop>‹</button>
         <button class="slider__nav slider__nav--next" data-act="next" aria-label="Следующее фото" data-stop>›</button>
         <span class="slider__count">1/${photos.length}</span>` : '';
    return `<div class="slider">${img}${nav}<div class="badges">${badges}</div></div>`;
  }

  function cardHTML(c) {
    const crm = crmOf(c);
    const badges = [];
    badges.push(c.region === 'astana'
      ? '<span class="badge badge--city">Астана</span>'
      : `<span class="badge badge--sub">Пригород · ${esc(c.locality)}</span>`);
    if (daysSince(c.first_seen) < 2) badges.push('<span class="badge badge--new">Новое</span>');
    if (c.price_drop) badges.push('<span class="badge badge--drop">📉 Снижение цены</span>');
    if (c.video) badges.push('<span class="badge badge--video">🎥 Есть видео</span>');
    if (c.has_utility_warning) badges.push(`<span class="badge badge--warn" title="${esc(c.utility_triggers.join(', '))}">⚠ ЖКХ</span>`);
    const classes = ['card'];
    if (crm?.fav) classes.push('is-fav');
    if (crm?.status === 'no') classes.push('is-muted');
    let addr = String(c.address || '');
    [c.district, c.locality, 'Акмолинская обл.'].forEach((x) => { if (x) addr = addr.split(x).join(''); });
    addr = addr.replace(/^[\s,·]+|[\s,·]+$/g, '').replace(/\s*,\s*,+/g, ',');
    const uniq = [c.district, c.complex, addr].filter(Boolean).join(' · ');
    return `<article class="${classes.join(' ')}" data-id="${esc(c.id)}" tabindex="0" aria-label="${esc(`${c.rooms || ''}-комн., ${money(c.price)}`)}">
      ${sliderHTML(c, badges.join(''))}
      <div class="card__body">
        <div class="price">
          <span class="price__main">${esc(money(c.price))}</span>
          ${c.price_per_sqm ? `<span class="price__sqm">${esc(fmt(c.price_per_sqm))} ₸/м²</span>` : ''}
          ${devHTML(c)}
        </div>
        ${dropHTML(c)}
        <div class="tags">${tagsHTML(c)}</div>
        ${c.has_utility_warning ? `<div class="warnline">⚠ ${esc(c.utility_triggers.join(', '))}</div>` : ''}
        <div class="loc">
          <div class="loc__text"><b>${esc(c.region === 'astana' ? 'Астана' : c.locality)}</b><span>${esc(uniq || '—')}</span></div>
          <a class="btn btn--sm" href="${esc(gisUrl(c))}" target="_blank" rel="noopener noreferrer" data-stop>📍 2ГИС</a>
        </div>
        <div class="sources">${sourcesHTML(c)}</div>
        <div class="actions" data-stop>
          <button class="btn btn--sm" data-act="fav" aria-pressed="${!!crm?.fav}">${crm?.fav ? '★ В избранном' : '☆ В избранное'}</button>
          <button class="btn btn--sm" data-act="hide" aria-pressed="${!!crm?.hidden}">${crm?.hidden ? '↩ Вернуть' : '👁 Скрыть'}</button>
          ${crmHTML(c, crm)}
        </div>
      </div>
    </article>`;
  }

  // ------------------------------------------------------------ модальное окно
  function buildHighlighter() {
    const mk = (arr) => arr.filter(Boolean).map((t) => String(t).toLowerCase().replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/е/g, '[её]'));
    const warn = mk(state.terms.warn);
    const good = mk(state.terms.good);
    const re = (parts) => (parts.length ? new RegExp(`(?<![\\p{L}\\p{N}])(?:${parts.join('|')})[\\p{L}]*`, 'giu') : null);
    try {
      state.hl = { warn: re(warn), good: re(good) };
    } catch {
      state.hl = { warn: null, good: null };
    }
  }
  function highlight(text) {
    let html = esc(text);
    const { warn, good } = state.hl || {};
    // сначала предупреждения, затем позитив только вне уже размеченных фрагментов
    if (warn) html = html.replace(warn, (m) => `<mark class="hl-warn">${m}</mark>`);
    if (good) {
      html = html.split(/(<mark[^>]*>.*?<\/mark>)/g)
        .map((part) => (part.startsWith('<mark') ? part : part.replace(good, (m) => `<mark class="hl-good">${m}</mark>`)))
        .join('');
    }
    return html;
  }

  function ytEmbed(url) {
    const m = String(url || '').match(/(?:youtube\.com\/(?:watch\?v=|embed\/|shorts\/)|youtu\.be\/)([\w-]{6,})/);
    return m ? `https://www.youtube-nocookie.com/embed/${m[1]}` : null;
  }

  function historyHTML(c) {
    const h = c.price_history || [];
    if (!h.length) return '<div class="hist">Нет данных</div>';
    let svg = '';
    if (h.length > 1) {
      const prices = h.map((x) => x.price);
      const min = Math.min(...prices); const max = Math.max(...prices);
      const pts = h.map((x, i) => {
        const X = (i / (h.length - 1)) * 300;
        const Y = max === min ? 35 : 62 - ((x.price - min) / (max - min)) * 54;
        return `${X.toFixed(1)},${Y.toFixed(1)}`;
      });
      svg = `<svg viewBox="-4 0 308 70" preserveAspectRatio="none" aria-hidden="true">
        <polyline points="${pts.join(' ')}" fill="none" stroke="#5BC0BE" stroke-width="2" vector-effect="non-scaling-stroke"/>
        ${pts.map((p) => `<circle cx="${p.split(',')[0]}" cy="${p.split(',')[1]}" r="3" fill="#5BC0BE"/>`).join('')}
      </svg>`;
    }
    const rows = h.slice().reverse().map((x, i, arr) => {
      const prev = arr[i + 1];
      const diff = prev ? x.price - prev.price : 0;
      const d = diff ? ` <span class="${diff < 0 ? 'dev--good' : 'dev--bad'}">${diff < 0 ? '−' : '+'}${esc(fmt(Math.abs(diff)))}</span>` : '';
      return `<div class="hist__row"><span>${esc(fmtDate(x.date))}</span><span>${esc(money(x.price))}${d}</span></div>`;
    }).join('');
    return `<div class="hist">${svg}${rows}</div>`;
  }

  function openModal(c) {
    const crm = crmOf(c);
    const photos = (c.photos || []).map(safeUrl).filter(Boolean);
    const yt = ytEmbed(c.video);
    const vurl = safeUrl(c.video);
    const specs = [
      ['Цена', money(c.price)],
      ['Цена за м²', c.price_per_sqm ? `${fmt(c.price_per_sqm)} ₸` : '—'],
      ['Медиана по похожим', c.market_median_sqm ? `${fmt(c.market_median_sqm)} ₸/м² (${c.market_sample} шт.)` : '—'],
      ['Комнат', c.rooms ?? '—'],
      ['Общая площадь', c.area ? `${c.area} м²` : '—'],
      ['Кухня', c.kitchen_area ? `${c.kitchen_area} м²` : '—'],
      ['Этаж', c.floor ? `${c.floor}${c.floors_total ? ' из ' + c.floors_total : ''}` : '—'],
      ['Год постройки', c.year ?? '—'],
      ['Материал стен', c.wall ?? '—'],
      ['Отопление', c.heating ?? '— (не указано)'],
      ['Тип', c.market === 'new' ? `Новостройка / стройка (${c.market_triggers.join(', ')})` : 'Вторичное жильё'],
      ['Населённый пункт', c.region === 'astana' ? 'Астана' : c.locality],
      ['Район', c.district ?? '—'],
      ['ЖК', c.complex ?? '—'],
      ['Адрес', c.address ?? '—'],
      ['ЖКХ-предупреждения', c.utility_triggers.length ? c.utility_triggers.join(', ') : 'нет'],
      ['Впервые замечено', fmtDate(c.first_seen)],
    ];
    $('#modalBody').innerHTML = `
      <div class="mgal">
        <button class="mclose" data-act="close" aria-label="Закрыть">✕</button>
        <div class="mgal__main">${photos.length ? `<img src="${esc(photos[0])}" alt="Фото квартиры" referrerpolicy="no-referrer" id="mMain">` : '<div class="noimg">Нет фото</div>'}
          ${photos.length > 1 ? `<button class="slider__nav slider__nav--prev" data-act="mprev" aria-label="Предыдущее фото" style="opacity:.9">‹</button><button class="slider__nav slider__nav--next" data-act="mnext" aria-label="Следующее фото" style="opacity:.9">›</button>` : ''}
        </div>
        ${photos.length > 1 ? `<div class="mgal__thumbs">${photos.map((p, i) => `<button data-thumb="${i}" aria-current="${i === 0}" aria-label="Фото ${i + 1}"><img src="${esc(p)}" alt="" loading="lazy" referrerpolicy="no-referrer"></button>`).join('')}</div>` : ''}
        ${yt ? `<div class="video"><iframe src="${esc(yt)}" title="Видео" allow="encrypted-media; picture-in-picture" allowfullscreen loading="lazy"></iframe></div>`
          : vurl ? `<div class="video"><a class="btn" href="${esc(vurl)}" target="_blank" rel="noopener noreferrer">🎥 Смотреть видео на сайте объявления</a></div>` : ''}
      </div>
      <div class="mside">
        <h2 id="modalTitle">${esc(c.title || `${c.rooms}-комнатная квартира`)}</h2>
        <div class="price"><span class="price__main">${esc(money(c.price))}</span>${devHTML(c)}</div>
        ${dropHTML(c)}
        <div class="tags">${tagsHTML(c)}</div>
        <div class="sources">${sourcesHTML(c)}<a class="btn btn--sm" href="${esc(gisUrl(c))}" target="_blank" rel="noopener noreferrer">📍 2ГИС</a></div>
        <div class="actions" style="border:0;padding:0">
          <button class="btn btn--sm" data-act="fav" aria-pressed="${!!crm?.fav}">${crm?.fav ? '★ В избранном' : '☆ В избранное'}</button>
          <button class="btn btn--sm" data-act="hide" aria-pressed="${!!crm?.hidden}">${crm?.hidden ? '↩ Вернуть' : '👁 Скрыть навсегда'}</button>
          ${crmHTML(c, crm)}
        </div>
        <h3>Описание</h3>
        <div class="desc">${c.description ? highlight(c.description) : 'Описание не загружено — откройте объявление на сайте.'}</div>
        <h3>Характеристики</h3>
        <table class="specs"><tbody>${specs.map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join('')}</tbody></table>
        <h3>История цены</h3>
        ${historyHTML(c)}
        ${c.sources.length > 1 ? `<h3>Источники (${c.sources.length})</h3><div class="hist">${c.sources.map((s) => `<div class="hist__row"><a href="${esc(safeUrl(s.url))}" target="_blank" rel="noopener noreferrer">${esc(s.site)} #${esc(s.id)}${s.seller === 'owner' ? ' · хозяин' : s.seller === 'agency' ? ' · агентство' : ''}</a><span>${esc(money(s.price))}</span></div>`).join('')}</div>` : ''}
      </div>`;
    const modal = $('#modal');
    modal.dataset.id = c.id;
    modal.dataset.idx = '0';
    if (!modal.open) modal.showModal();
    $('#modalBody').scrollTop = 0;
  }

  function modalPhoto(delta, abs) {
    const modal = $('#modal');
    const c = byId(modal.dataset.id);
    const photos = (c?.photos || []).map(safeUrl).filter(Boolean);
    if (!photos.length) return;
    let i = abs != null ? abs : (Number(modal.dataset.idx) + delta + photos.length) % photos.length;
    modal.dataset.idx = String(i);
    $('#mMain').src = photos[i];
    $$('.mgal__thumbs button', modal).forEach((b) => b.setAttribute('aria-current', String(Number(b.dataset.thumb) === i)));
    $(`.mgal__thumbs button[data-thumb="${i}"]`, modal)?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }

  // ------------------------------------------------------------ события
  const byId = (id) => state.listings.find((c) => c.id === id);

  function cardPhoto(cardEl, delta) {
    const c = byId(cardEl.dataset.id);
    const photos = (c?.photos || []).map(safeUrl).filter(Boolean);
    const img = $('.slider img', cardEl);
    if (!img || photos.length < 2) return;
    const i = (Number(img.dataset.idx) + delta + photos.length) % photos.length;
    img.dataset.idx = String(i);
    img.src = photos[i];
    $('.slider__count', cardEl).textContent = `${i + 1}/${photos.length}`;
  }

  function handleAction(act, c, root) {
    const crm = crmOf(c) || {};
    if (act === 'fav') {
      crmSet(c, { fav: !crm.fav });
      toast(!crm.fav ? 'Добавлено в избранное' : 'Убрано из избранного');
    } else if (act === 'hide') {
      crmSet(c, { hidden: !crm.hidden });
      toast(!crm.hidden ? 'Объявление скрыто (см. «Скрытые»)' : 'Объявление возвращено');
      if ($('#modal').open) $('#modal').close();
    } else return false;
    render(true);
    if ($('#modal').open && root === $('#modal')) openModal(c);
    return true;
  }

  function bind() {
    const grid = $('#grid');
    grid.addEventListener('click', (e) => {
      const cardEl = e.target.closest('.card');
      if (!cardEl) return;
      const c = byId(cardEl.dataset.id);
      const actEl = e.target.closest('[data-act]');
      if (actEl) {
        const act = actEl.dataset.act;
        if (act === 'prev' || act === 'next') { e.stopPropagation(); cardPhoto(cardEl, act === 'next' ? 1 : -1); return; }
        if (handleAction(act, c, grid)) return;
      }
      if (e.target.closest('[data-stop]')) return;
      openModal(c);
    });
    grid.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && e.target.classList.contains('card')) openModal(byId(e.target.dataset.id));
    });
    // свайп фото на телефоне
    let touchX = null;
    grid.addEventListener('touchstart', (e) => { if (e.target.closest('.slider')) touchX = e.touches[0].clientX; }, { passive: true });
    grid.addEventListener('touchend', (e) => {
      if (touchX == null) return;
      const dx = e.changedTouches[0].clientX - touchX; touchX = null;
      const cardEl = e.target.closest('.card');
      if (cardEl && Math.abs(dx) > 40) cardPhoto(cardEl, dx < 0 ? 1 : -1);
    }, { passive: true });
    grid.addEventListener('error', (e) => {
      const img = e.target;
      if (img.tagName !== 'IMG' || !img.closest('.slider')) return;
      const cardEl = img.closest('.card');
      const total = (byId(cardEl.dataset.id)?.photos || []).length;
      const fails = Number(img.dataset.fails || 0) + 1;
      img.dataset.fails = String(fails);
      if (fails < total) { cardPhoto(cardEl, 1); return; } // пробуем следующее фото
      img.replaceWith(Object.assign(document.createElement('div'), { className: 'noimg', textContent: 'Фото недоступно' }));
      cardEl.querySelectorAll('.slider__nav, .slider__count').forEach((el) => el.remove());
    }, true);

    // CRM-поля (карточки и модалка)
    const onCrmInput = debounce((el) => {
      const id = el.closest('[data-id]')?.dataset.id;
      const c = id && byId(id);
      if (c) crmSet(c, { note: el.value });
    }, 400);
    document.addEventListener('input', (e) => {
      if (e.target.matches('[data-act="note"]')) onCrmInput(e.target);
    });
    document.addEventListener('change', (e) => {
      if (!e.target.matches('[data-act="status"]')) return;
      const id = e.target.closest('[data-id]')?.dataset.id;
      const c = id && byId(id);
      if (!c) return;
      crmSet(c, { status: e.target.value });
      e.target.className = e.target.value === 'agreed' ? 'status-agreed' : e.target.value === 'no' ? 'status-no' : '';
      const cardEl = $(`.card[data-id="${CSS.escape(id)}"]`);
      if (cardEl) cardEl.classList.toggle('is-muted', e.target.value === 'no');
    });

    // модальное окно
    const modal = $('#modal');
    modal.addEventListener('click', (e) => {
      if (e.target === modal) { modal.close(); return; }
      const actEl = e.target.closest('[data-act]');
      const thumb = e.target.closest('[data-thumb]');
      if (thumb) { modalPhoto(0, Number(thumb.dataset.thumb)); return; }
      if (!actEl) return;
      const act = actEl.dataset.act;
      if (act === 'close') modal.close();
      else if (act === 'mprev') modalPhoto(-1);
      else if (act === 'mnext') modalPhoto(1);
      else handleAction(act, byId(modal.dataset.id), modal);
    });
    modal.addEventListener('keydown', (e) => {
      if (e.target.matches('input, select, textarea')) return;
      if (e.key === 'ArrowRight') modalPhoto(1);
      if (e.key === 'ArrowLeft') modalPhoto(-1);
    });
    modal.addEventListener('close', () => { $('#modalBody').innerHTML = ''; render(true); });

    // фильтры
    $('#roomChips').addEventListener('click', (e) => {
      const b = e.target.closest('[data-room]'); if (!b) return;
      const r = Number(b.dataset.room);
      const set = new Set(state.filters.rooms);
      set.has(r) ? set.delete(r) : set.add(r);
      state.filters.rooms = [...set].sort();
      saveFilters(); filtersToUI(); render();
    });
    $('#regionSeg').addEventListener('click', (e) => {
      const b = e.target.closest('[data-region]'); if (!b) return;
      state.filters.region = b.dataset.region;
      saveFilters(); filtersToUI(); render();
    });
    $('#sortSel').addEventListener('change', (e) => { state.filters.sort = e.target.value; saveFilters(); render(); });
    const deb = debounce(uiToFilters, 300);
    $('#filters').addEventListener('input', deb);
    $('#filters').addEventListener('change', uiToFilters);
    ['#priceMin', '#priceMax'].forEach((s) => $(s).addEventListener('blur', (e) => {
      const n = digits(e.target.value); e.target.value = n ? fmt(n) : '';
    }));
    const toggle = (open) => {
      const f = $('#filters');
      f.hidden = open === undefined ? !f.hidden : !open;
      $('#toggleFilters').setAttribute('aria-expanded', String(!f.hidden));
    };
    $('#toggleFilters').addEventListener('click', () => toggle());
    $('#closeFilters').addEventListener('click', () => { toggle(false); window.scrollTo({ top: 0, behavior: 'smooth' }); });
    const reset = () => {
      state.filters = { ...state.defaults };
      state.view = 'all';
      setView('all');
      saveFilters(); filtersToUI(); render();
      toast('Фильтры сброшены к вашим настройкам');
    };
    $('#resetBtn').addEventListener('click', reset);
    $('#emptyReset').addEventListener('click', reset);

    // режимы просмотра
    $('#viewFav').addEventListener('click', () => setView(state.view === 'fav' ? 'all' : 'fav'));
    $('#viewHidden').addEventListener('click', () => setView(state.view === 'hidden' ? 'all' : 'hidden'));

    // экспорт / импорт
    $('#exportBtn').addEventListener('click', exportCRM);
    $('#importBtn').addEventListener('click', () => $('#importFile').click());
    $('#importFile').addEventListener('change', (e) => {
      const f = e.target.files[0]; if (f) importCRM(f); e.target.value = '';
    });
  }

  function setView(v) {
    state.view = v;
    $('#viewFav').setAttribute('aria-pressed', String(v === 'fav'));
    $('#viewHidden').setAttribute('aria-pressed', String(v === 'hidden'));
    render();
  }

  // ------------------------------------------------------------ загрузка данных
  async function getJSON(url) {
    const r = await fetch(`${url}?v=${Date.now()}`, { cache: 'no-store' });
    if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
    return r.json();
  }

  function renderMeta() {
    const m = state.meta;
    const when = m.updated_at ? fmtDate(m.updated_at, true) : 'неизвестно';
    $('#metaLine').textContent = `Обновлено: ${when} · в базе ${state.listings.length} объектов`;
    const notes = [];
    if (m.demo) {
      notes.push('<div class="notice notice--warn">Сейчас показаны <b>демо-данные</b> (вымышленные объявления, фото-заглушки) — чтобы проверить сайт. Они заменятся настоящими после первого успешного запуска парсера (GitHub → Actions → «Scrape listings» → Run workflow).</div>');
    }
    const bad = Object.entries(m.sources || {}).filter(([, s]) => s && !s.ok && s.error);
    if (bad.length) {
      notes.push(`<div class="notice notice--warn">Не удалось обновить: ${bad.map(([k, s]) => `<b>${esc(k)}</b> — ${esc(s.error)}`).join('; ')}. Показаны последние сохранённые данные.</div>`);
    }
    if (m.updated_at && daysSince(m.updated_at) > 1.5 && !m.demo) {
      notes.push('<div class="notice notice--warn">База не обновлялась больше суток — проверьте вкладку Actions в репозитории.</div>');
    }
    $('#notice').innerHTML = notes.join('');
  }

  async function init() {
    bind();
    const [listings, meta, defaults, stop] = await Promise.allSettled([
      getJSON('data/listings.json'), getJSON('data/meta.json'),
      getJSON('config/default_filters.json'), getJSON('config/stopwords.json'),
    ]);
    if (defaults.status === 'fulfilled') {
      const d = { ...defaults.value };
      Object.keys(d).forEach((k) => k.startsWith('_') && delete d[k]);
      state.defaults = { ...FALLBACK_DEFAULTS, ...d };
    }
    if (stop.status === 'fulfilled') {
      state.terms.warn = [...(stop.value.utility || []), ...(stop.value.new_building || [])];
      state.terms.good = stop.value.highlight_positive || [];
    }
    buildHighlighter();
    loadFilters();
    filtersToUI();
    if (listings.status === 'fulfilled') {
      state.listings = Array.isArray(listings.value) ? listings.value : (listings.value.listings || []);
    } else {
      $('#notice').innerHTML = `<div class="notice notice--warn">Не удалось загрузить data/listings.json: ${esc(listings.reason?.message || '')}. Если вы открыли файл index.html напрямую с диска — запустите локальный сервер (см. README).</div>`;
    }
    state.meta = meta.status === 'fulfilled' ? meta.value : { updated_at: listings.value?.updated_at, demo: listings.value?.demo };
    if (listings.status === 'fulfilled') renderMeta();
    render();
  }

  init();
})();
