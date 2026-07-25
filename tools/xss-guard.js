// XSS-guard (редизайн, Итерация 7, §6.4) — ЭВРИСТИЧЕСКАЯ проверка.
// Находит интерполяции ${...} в шаблонных строках site/app.js и классифицирует: проходит ли
// значение через санитайзер/форматтер из белого списка (esc/ru/PU/... — возвращают безопасный
// текст/число), или это «сырая» вставка, требующая ручного просмотра.
//
// ОГРАНИЧЕНИЯ (честно): это НЕ полный статический анализ и НЕ таблица потоков данных.
//  - сканируются ВСЕ шаблонные литералы, а не только присваиваемые в innerHTML (консервативно);
//  - «сырая» вставка ≠ уязвимость: почти все данные — из доверенных JSON-пайплайнов (MOEX ISS,
//    собственные), НЕ из ввода пользователя. Единственный пользовательский ввод — состав портфеля
//    (тикеры), он проходит через esc()/парсер;
//  - вложенные шаблоны и тернарники разбираются приближённо.
// Цель — список кандидатов на ревью, а не вердикт.
const fs = require('fs');
const path = require('path');

const SRC = path.join(__dirname, '..', 'site', 'app.js');
const OUT = path.join(__dirname, '..', 'artifacts', 'xss-guard.json');

// Обёртки/форматтеры, возвращающие безопасный HTML/текст/число.
const SAFE = ['esc', 'encodeURIComponent', 'ru', 'rub0', 'rub', 'fmtRub', 'fmtPct', 'fmtPctSigned',
  'PU', 'PN', 'PP', 'cellNum', 'riskBadge', 'verdictChip', 'statusChipHTML', 'stabilityCell',
  'officialRatingHTML', 'shortIsoDate', 'shortDate', 'sawDate', 'sawPct', 'pc', 'pct',
  'Math.round', 'Math.max', 'Math.min', 'Math.abs', 'Number', 'parseInt', 'parseFloat', 'String'];

const src = fs.readFileSync(SRC, 'utf8');
const lines = src.split('\n');

// Извлечь ${...} с балансировкой одной вложенности фигурных скобок.
function findInterps(text) {
  const res = [];
  for (let i = 0; i < text.length - 1; i++) {
    if (text[i] === '$' && text[i + 1] === '{') {
      let depth = 1, j = i + 2;
      for (; j < text.length && depth > 0; j++) {
        if (text[j] === '{') depth++;
        else if (text[j] === '}') depth--;
      }
      res.push(text.slice(i + 2, j - 1).trim());
      i = j - 1;
    }
  }
  return res;
}

const isNumericLiteral = (e) => /^-?[\d.]+$/.test(e);
const isStringLiteral = (e) => /^(['"`]).*\1$/.test(e);
const startsWithSafe = (e) => SAFE.some((fn) => e.startsWith(fn + '(') || e.startsWith(fn + ' ('))
  // тернарник, обе ветви которого — литералы/классы (напр. cond ? 'good' : 'risk')
  || (/\?/.test(e) && /['"`]/.test(e) && !/\$\{/.test(e) && !/</.test(e));

let total = 0, safe = 0;
const review = [];
lines.forEach((line, idx) => {
  if (!line.includes('${')) return;
  findInterps(line).forEach((expr) => {
    total++;
    if (isNumericLiteral(expr) || isStringLiteral(expr) || startsWithSafe(expr)) { safe++; return; }
    // отсечь заведомо безопасные: одиночные идентификаторы классов/тонов, булевы, тернар без HTML
    review.push({ line: idx + 1, expr: expr.length > 90 ? expr.slice(0, 90) + '…' : expr });
  });
});

// ── inline-обработчики: под CSP (script-src без 'unsafe-inline') они МОЛЧА не работают ──
// Именно так сломался переключатель MCFTR/IMOEX после ввода CSP. Здесь — жёсткий гард:
// любой on*="..." в шаблонах app.js или в index.html = сломанная кнопка, а не стилистика.
const INLINE_RE = /\bon(?:click|change|input|submit|keydown|keyup|keypress|focus|blur|mouseover|mouseout|load|error)\s*=\s*["']/gi;
const inlineHandlers = [];
const scanInline = (text, label) => {
  text.split('\n').forEach((line, i) => {
    const m = line.match(INLINE_RE);
    if (m) inlineHandlers.push({ file: label, line: i + 1, handlers: m.map((s) => s.replace(/\s*=\s*["']$/, '')) });
  });
};
scanInline(src, 'site/app.js');
const INDEX = path.join(__dirname, '..', 'site', 'index.html');
if (fs.existsSync(INDEX)) scanInline(fs.readFileSync(INDEX, 'utf8'), 'site/index.html');

const report = {
  generated_at: new Date().toISOString(),
  source: 'site/app.js',
  csp_inline_handlers: {
    count: inlineHandlers.length,
    note: 'CSP (script-src без unsafe-inline) блокирует inline-обработчики — они не сработают. '
        + 'Выносить в делегированные слушатели (см. initRouter: data-saw-index / data-divcal-tab / data-goto).',
    findings: inlineHandlers,
  },
  heuristic: 'template-literal ${} interpolation classification (см. шапку tools/xss-guard.js)',
  limitations: [
    'сканируются все шаблонные литералы, не только innerHTML-контекст (консервативно, возможны ложные срабатывания)',
    'данные — из доверенных JSON-пайплайнов; единственный пользовательский ввод (портфель) проходит через esc()/парсер',
    '«review» — кандидаты на ручной просмотр, не подтверждённые уязвимости',
  ],
  totals: { interpolations: total, safe_or_formatted: safe, needs_review: review.length },
  sample_needs_review: review.slice(0, 40),
};
fs.mkdirSync(path.dirname(OUT), { recursive: true });
fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
console.log(`[xss-guard] интерполяций: ${total} · безопасных/форматированных: ${safe} · на ревью: ${review.length} → ${path.relative(path.join(__dirname, '..'), OUT)}`);
if (inlineHandlers.length) {
  console.error(`[xss-guard] ❌ CSP-РЕГРЕСС: найдено ${inlineHandlers.length} inline-обработчик(ов) — под CSP они НЕ работают:`);
  inlineHandlers.forEach((f) => console.error(`    ${f.file}:${f.line} → ${f.handlers.join(', ')}`));
  process.exit(1);
}
console.log('[xss-guard] inline-обработчиков нет — CSP не ломает интерактив ✓');
