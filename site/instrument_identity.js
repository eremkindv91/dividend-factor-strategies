(function instrumentIdentityModule(root, factory) {
  const api = factory(root && root.InstrumentLogoRegistry);
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.InstrumentIdentity = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function createInstrumentIdentity(runtimeRegistry) {
  'use strict';

  const SIZE_PX = Object.freeze({ xs: 20, sm: 24, md: 32, lg: 40 });
  const TYPE_LABELS = Object.freeze({
    equity: 'АО', preferred_equity: 'АП', fund: 'БПИФ', index: 'Индекс',
    bond: 'Облигация', currency: 'Валюта', commodity: 'Товар', unknown: 'Инструмент',
  });
  const PALETTE = Object.freeze([
    ['#E6F4EE', '#176448'], ['#E8EEF9', '#31558D'], ['#F7EBDD', '#8A5723'],
    ['#F5E8EE', '#8A3F61'], ['#E9F1F4', '#276477'], ['#EEEAF8', '#594594'],
    ['#F1EFE4', '#6A6429'], ['#F4E9E5', '#8A4938'],
  ]);

  // Identity lineage is explicit and independent from price-series continuity.
  const LINEAGE = Object.freeze({
    TCS: { canonical: 'T', kind: 'rename' },
    TCSG: { canonical: 'T', kind: 'rename' },
    YNDX: { canonical: 'YDEX', kind: 'redomiciliation_identity' },
    FIVE: { canonical: 'X5', kind: 'redomiciliation_identity' },
  });
  const ISSUER_ASSET = Object.freeze({
    BANEP: 'BANE', BSPBP: 'BSPB', CNTLP: 'CNTL', DZRDP: 'DZRD', IGSTP: 'IGST',
    JNOSP: 'JNOS', KAZTP: 'KAZT', KCHEP: 'KCHE', KGKCP: 'KGKC', KRKNP: 'KRKN',
    KROTP: 'KROT', KRSBP: 'KRSB', KZOSP: 'KZOS', LNZLP: 'LNZL', LSNGP: 'LSNG',
    MAGEP: 'MAGE', MFGSP: 'MFGS', MGTSP: 'MGTS', MISBP: 'MISB', MTLRP: 'MTLR',
    NKNCP: 'NKNC', NNSBP: 'NNSB', PMSBP: 'PMSB', RTKMP: 'RTKM', RTSBP: 'RTSB',
    SAGOP: 'SAGO', SAREP: 'SARE', SBERP: 'SBER', SNGSP: 'SNGS', TATNP: 'TATN',
    TGKBP: 'TGKB', TORSP: 'TORS', TRNFP: 'TRNF', VGSBP: 'VGSB', VJGZP: 'VJGZ',
    VRSBP: 'VRSB', VSYDP: 'VSYD', WTCMP: 'WTCM', YKENP: 'YKEN', YRSBP: 'YRSB',
  });
  const PREFERRED = new Set(Object.keys(ISSUER_ASSET));
  const FUNDS = new Set(['EQMX', 'DIVD']);
  const INDICES = new Set(['IMOEX', 'MCFTR', 'RTSI', 'RTS', 'RGBI', 'RVI', 'MOEXBC']);

  const CORE_REGISTRY = Object.freeze({
    T: {
      secid: 'T', type: 'equity', name: 'Т-Технологии', logo_path: 'assets/instruments/t.svg',
      logo_source: 'https://www.tbank.ru/about/brand/', logo_status: 'official', updated_at: '2026-07-31',
    },
    IMOEX: generated('IMOEX', 'index', 'Индекс МосБиржи', 'assets/instruments/index-imoex.svg'),
    MCFTR: generated('MCFTR', 'index', 'Индекс полной доходности МосБиржи', 'assets/instruments/index-mcftr.svg'),
    RTSI: generated('RTSI', 'index', 'Индекс РТС', 'assets/instruments/index-rtsi.svg'),
    RTS: generated('RTS', 'index', 'Индекс РТС', 'assets/instruments/index-rtsi.svg'),
    RGBI: generated('RGBI', 'index', 'Индекс государственных облигаций', 'assets/instruments/index-rgbi.svg'),
    RVI: generated('RVI', 'index', 'Индекс волатильности', 'assets/instruments/index-rvi.svg'),
    MOEXBC: generated('MOEXBC', 'index', 'Индекс широкого рынка', 'assets/instruments/index-moexbc.svg'),
    EQMX: generated('EQMX', 'fund', 'БПИФ на индекс МосБиржи', 'assets/instruments/fund-eqmx.svg'),
    DIVD: generated('DIVD', 'fund', 'БПИФ дивидендных акций РФ', 'assets/instruments/fund-divd.svg'),
  });

  const GENERIC_ASSETS = Object.freeze({
    bond: 'assets/instruments/type-bond.svg',
    currency: 'assets/instruments/type-currency.svg',
    commodity: 'assets/instruments/type-commodity.svg',
  });

  function generated(secid, type, name, logoPath) {
    return {
      secid, type, name, logo_path: logoPath, logo_source: 'project-generated',
      logo_status: 'generated', updated_at: '2026-07-31',
    };
  }

  function safeLogoPath(value) {
    const path = String(value || '').trim();
    if (!/^assets\/instruments\/(?:[a-z0-9._-]+\/)*[a-z0-9._-]+\.(?:svg|png|webp)$/i.test(path)) return '';
    return path.split('/').some((part) => part === '.' || part === '..') ? '' : path;
  }

  function sanitizeRuntimeRegistry(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
    const clean = {};
    Object.entries(value).forEach(([key, row]) => {
      const secid = cleanSecid(key);
      const path = safeLogoPath(row && row.logo_path);
      if (!secid || !path || !path.startsWith('assets/instruments/companies/')) return;
      clean[secid] = Object.freeze({
        secid,
        type: normalizeType(row.type, secid),
        name: String(row.name || '').trim(),
        logo_path: path,
        logo_source: String(row.logo_source || 'T-Invest instrument catalogue').trim(),
        logo_status: 'broker_catalog',
        updated_at: /^\d{4}-\d{2}-\d{2}$/.test(String(row.updated_at || '')) ? row.updated_at : '',
      });
    });
    return clean;
  }

  // Generated broker-catalog assets extend the checked-in core set, but never
  // override hand-curated project assets (T, indices and generic type glyphs).
  const REGISTRY = Object.freeze({ ...sanitizeRuntimeRegistry(runtimeRegistry), ...CORE_REGISTRY });

  function cleanSecid(value) {
    return String(value || '').toUpperCase().replace(/\s+/g, '').replace(/[^A-Z0-9._/-]/g, '');
  }

  function canonicalSecid(value) {
    const input = cleanSecid(value);
    return LINEAGE[input] ? LINEAGE[input].canonical : input;
  }

  function normalizeType(value, secid) {
    const raw = String(value || '').toLowerCase().trim();
    const aliases = {
      share: 'equity', stock: 'equity', equity_ordinary: 'equity', ordinary: 'equity',
      equity_preferred: 'preferred_equity', preferred: 'preferred_equity', pref: 'preferred_equity',
      etf: 'fund', bpif: 'fund', бпиф: 'fund', price_index: 'index', total_return_index: 'index',
      corporate_bond: 'bond', government_bond: 'bond', ofz: 'bond', fx: 'currency',
    };
    if (PREFERRED.has(secid)) return 'preferred_equity';
    if (TYPE_LABELS[raw]) return raw;
    if (aliases[raw]) return aliases[raw];
    if (FUNDS.has(secid)) return 'fund';
    if (INDICES.has(secid)) return 'index';
    if (/^(RU000A|SU\d|XS\d|RU\d)/.test(secid)) return 'bond';
    if (/^(USD|EUR|CNY|HKD|GBP|JPY)[A-Z0-9_/.-]*/.test(secid) || secid.includes('/RUB')) return 'currency';
    if (secid) return 'equity';
    return 'unknown';
  }

  function fallbackLabel(secid, custom) {
    const explicit = String(custom || '').trim();
    if (explicit) return explicit.slice(0, 2).toUpperCase();
    const clean = cleanSecid(secid).replace(/[^A-Z0-9]/g, '');
    return clean ? clean.slice(0, 2) : '?';
  }

  function hashSecid(secid) {
    let hash = 2166136261;
    for (const char of String(secid || '')) {
      hash ^= char.charCodeAt(0);
      hash = Math.imul(hash, 16777619);
    }
    return hash >>> 0;
  }

  function fallbackColors(secid) {
    const pair = PALETTE[hashSecid(cleanSecid(secid)) % PALETTE.length];
    return { background: pair[0], color: pair[1] };
  }

  function resolve(input) {
    const source = typeof input === 'string' ? { secid: input } : (input || {});
    const requested = cleanSecid(source.secid || source.ticker);
    const canonical = canonicalSecid(requested);
    const assetSecid = ISSUER_ASSET[canonical] || canonical;
    const registered = REGISTRY[canonical] || REGISTRY[assetSecid] || null;
    const type = normalizeType(source.type || source.instrument_type || (registered && registered.type), canonical);
    const logoPath = safeLogoPath(source.logoPath || source.logo_path
      || (registered && registered.logo_path) || GENERIC_ASSETS[type] || '');
    return {
      requested_secid: requested,
      secid: canonical || requested,
      asset_secid: assetSecid,
      lineage: LINEAGE[requested] || null,
      name: String(source.name || (registered && registered.name) || '').trim(),
      type,
      type_label: TYPE_LABELS[type] || TYPE_LABELS.unknown,
      logo_path: logoPath,
      logo_source: registered ? registered.logo_source : (logoPath ? 'project-generated' : ''),
      logo_status: registered ? registered.logo_status : (logoPath ? 'generated' : 'fallback'),
      updated_at: registered ? registered.updated_at : '',
      fallback_label: source.fallbackLabel || source.fallback_label
        ? fallbackLabel(canonical || requested, source.fallbackLabel || source.fallback_label)
        : ({ index: 'IX', bond: 'BD', currency: 'FX', commodity: 'CM' }[type] || fallbackLabel(canonical || requested)),
    };
  }

  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, (char) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    })[char]);
  }

  function avatarHTML(options) {
    const opts = typeof options === 'string' ? { secid: options } : (options || {});
    const item = resolve(opts);
    const size = SIZE_PX[opts.size] ? opts.size : 'sm';
    const standalone = Boolean(opts.standalone);
    const label = item.name || item.secid || 'Инструмент';
    const colors = fallbackColors(item.secid);
    const image = item.logo_path
      ? `<img class="instrument-avatar__img" src="${esc(item.logo_path)}" alt="${standalone ? esc(label) : ''}" loading="lazy" decoding="async" data-instrument-logo>`
      : '';
    const aria = standalone ? ` role="img" aria-label="${esc(label)}"` : ' aria-hidden="true"';
    const showTypeBadge = opts.showTypeBadge == null
      ? ['fund', 'preferred_equity'].includes(item.type)
      : Boolean(opts.showTypeBadge);
    const badge = showTypeBadge
      ? `<span class="instrument-avatar__type">${esc(opts.typeBadge || item.type_label)}</span>` : '';
    return `<span class="instrument-avatar instrument-avatar--${size} instrument-avatar--${esc(item.type)}" data-secid="${esc(item.secid)}"${aria}>`
      + `<span class="instrument-avatar__fallback" style="--avatar-bg:${colors.background};--avatar-ink:${colors.color}">${esc(item.fallback_label)}</span>`
      + image + badge + '</span>';
  }

  function identityHTML(options) {
    const opts = options || {};
    const item = resolve(opts);
    const compact = opts.variant === 'compact';
    const name = item.name || item.secid || 'Инструмент';
    const sub = [item.secid, opts.showTypeText === false ? '' : item.type_label].filter(Boolean).join(' · ');
    const showTypeBadge = opts.showTypeBadge == null
      ? ['fund', 'preferred_equity'].includes(item.type)
      : Boolean(opts.showTypeBadge);
    return `<span class="instrument-identity${compact ? ' instrument-identity--compact' : ''}">`
      + avatarHTML({ ...opts, secid: item.secid, name, standalone: false, showTypeBadge })
      + `<span class="instrument-identity__text"><b>${esc(compact ? item.secid : name)}</b>`
      + `<span>${esc(compact ? name : sub)}</span></span></span>`;
  }

  function updateImage(image) {
    if (!image || !image.closest) return;
    const avatar = image.closest('.instrument-avatar');
    if (!avatar) return;
    const loaded = image.complete && image.naturalWidth > 0;
    avatar.classList.toggle('has-logo', loaded);
    image.hidden = !loaded;
  }

  function mount(scope) {
    if (typeof document === 'undefined') return;
    const doc = scope && scope.ownerDocument ? scope.ownerDocument : document;
    if (!doc.documentElement.dataset.instrumentIdentityWired) {
      doc.documentElement.dataset.instrumentIdentityWired = '1';
      doc.addEventListener('load', (event) => {
        if (event.target && event.target.matches && event.target.matches('[data-instrument-logo]')) updateImage(event.target);
      }, true);
      doc.addEventListener('error', (event) => {
        if (event.target && event.target.matches && event.target.matches('[data-instrument-logo]')) updateImage(event.target);
      }, true);
    }
    const rootNode = scope && scope.querySelectorAll ? scope : doc;
    rootNode.querySelectorAll('[data-instrument-logo]').forEach(updateImage);
  }

  if (typeof document !== 'undefined') mount(document);

  return Object.freeze({
    SIZE_PX, TYPE_LABELS, REGISTRY, LINEAGE, canonicalSecid, normalizeType, fallbackLabel,
    fallbackColors, safeLogoPath, resolve, avatarHTML, identityHTML, mount,
  });
});
