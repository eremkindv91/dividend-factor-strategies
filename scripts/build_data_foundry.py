#!/usr/bin/env python3
"""Build an additive, provenance-preserving financial layer through public MCP."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import hashlib
import re
import sqlite3
from pathlib import Path
import sys
import time

import requests

ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = 'https://mcp.vrednyj-investor.ru/mcp'
METRICS = {'fin.revenue': 'Выручка', 'fin.net_income': 'Чистая прибыль группы'}


class Client:
    def __init__(self):
        self.session = requests.Session()
        self.version = None
        self.dataset = None

    def call(self, name, arguments):
        payload = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                   'params': {'name': name, 'arguments': arguments}}
        for attempt in range(3):
            try:
                response = self.session.post(ENDPOINT, json=payload,
                    headers={'Accept': 'application/json, text/event-stream'}, timeout=45)
                response.raise_for_status()
                raw = response.text
                if raw.startswith('event:') or raw.startswith('data:'):
                    packets = [json.loads(line[5:].strip()) for line in raw.splitlines()
                               if line.startswith('data:')]
                    packet = next(x for x in reversed(packets) if 'result' in x or 'error' in x)
                else:
                    packet = response.json()
                if 'error' in packet:
                    raise ValueError(str(packet['error']))
                result = packet['result']
                if result.get('isError'):
                    raise ValueError(str(result.get('content')))
                data = result.get('structuredContent')
                if data is None:
                    data = json.loads(next(x['text'] for x in result['content'] if x['type'] == 'text'))
                dataset = data.get('dataset', {})
                version = dataset.get('version')
                if not version:
                    raise ValueError('Missing dataset version')
                if self.version and version != self.version:
                    raise ValueError('Dataset version changed during import')
                self.version, self.dataset = version, dataset
                if data.get('status') not in ('ok', 'not_found', 'ambiguous', None):
                    raise ValueError(f"{name}: {data.get('status')}")
                return data
            except (requests.RequestException, ValueError, KeyError, StopIteration):
                if attempt == 2:
                    raise
                time.sleep(attempt + 1)

    def pages(self, name, arguments):
        items, cursor, seen = [], None, set()
        while True:
            data = self.call(name, dict(arguments, limit=100, **({'cursor': cursor} if cursor else {})))
            if data.get('status') == 'ambiguous':
                raise ValueError(f'Unresolved ambiguity in {name}')
            items.extend(data.get('items', []))
            cursor = data.get('page', {}).get('next_cursor')
            if not cursor:
                return items
            if cursor in seen:
                raise ValueError('Repeated pagination cursor')
            seen.add(cursor)


def accepted_fact(fact):
    """Keep published units, reporting scope and verified formula results distinct."""
    try:
        number = Decimal(fact['value'])
        return (number.is_finite() and bool(fact.get('accounting_basis'))
            and bool(fact.get('consolidation_scope'))
            and fact.get('review_status') in ('reviewed', 'reviewed_owner_approved', 'formula_verified')
            and not fact.get('has_source_conflict') and bool(fact.get('source_id'))
            and bool(fact.get('unit'))
            and fact['period']['end'] <= datetime.now(timezone.utc).date().isoformat())
    except (KeyError, InvalidOperation, TypeError):
        return False


def collect_company(ticker, periods):
    client = Client()
    companies = client.pages('search_companies', {'query_text': ticker})
    company = next((c for c in companies if c['ticker'] == ticker), None)
    if not company:
        return {'ticker': ticker, 'status': 'not_found', 'facts': []}, client.dataset
    facts, rejected = [], 0
    for code in METRICS:
        definitions = client.pages('find_metrics', {'query_text': code, 'companies': [ticker]})
        definition = next((x for x in definitions if x['code'] == code), None)
        if not definition:
            continue
        for variant in definition.get('variants', []):
            if variant.get('accounting_basis') != 'IFRS' or variant.get('consolidation_scope') != 'consolidated':
                continue
            rows = client.pages('get_series', {'companies': [ticker], 'metrics': [code],
                'periods': periods, 'variants': [variant['selector']]})
            for fact in rows:
                if accepted_fact(fact):
                    fact['variant'] = variant['selector']
                    facts.append(fact)
                else:
                    rejected += 1
    facts = list({x['id']: x for x in facts}.values())
    return {**company, 'status': 'available' if facts else 'missing', 'facts': facts,
            'rejected_count': rejected}, client.dataset


def universe(path):
    data = json.loads(path.read_text())
    rows = data.get('tickers', data.get('rows', []))
    return sorted({x['ticker'] for x in rows if x.get('ticker')})


def build(tickers, output):
    year = datetime.now(timezone.utc).year
    periods = [f'{y}-{p}' for y in range(year - 3, year + 1) for p in ('FY', 'H1', '9M')]
    companies, datasets = [], []
    with ThreadPoolExecutor(max_workers=3) as pool:
        for company, dataset in pool.map(lambda t: collect_company(t, periods), tickers):
            companies.append(company)
            if dataset:
                datasets.append(dataset)
            print(f"[foundry] {company['ticker']}: {company['status']} ({len(company['facts'])})", flush=True)
    if len({d['version'] for d in datasets}) != 1:
        raise ValueError('Mixed or absent dataset versions; previous output preserved')
    facts = [f for c in companies for f in c['facts']]
    if not facts:
        raise ValueError('Empty import; previous output preserved')
    ids = sorted({f['source_id'] for f in facts})
    client, evidence = Client(), {}
    client.version = datasets[0]['version']
    for start in range(0, len(ids), 20):
        for item in client.pages('get_evidence', {'source_ids': ids[start:start + 20]}):
            evidence[item['source_id']] = item
    if any(f['source_id'] not in evidence for f in facts):
        raise ValueError('Missing evidence; previous output preserved')
    result = {'schema_version': 1, 'dataset': datasets[0], 'metrics': METRICS,
        'meta': {'fetched_at': datetime.now(timezone.utc).isoformat(),
            'requested_companies': len(tickers), 'covered_companies': sum(bool(c['facts']) for c in companies),
            'facts': len(facts), 'periods': periods, 'source': ENDPOINT,
            'note': 'Отдельный слой отчётности. Входы ML и оценки стоимости не заменены.'},
        'companies': companies, 'evidence': evidence}
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix('.tmp')
    temp.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    temp.replace(output)
    return result


def build_from_database(tickers, output, database=None):
    """One versioned export avoids per-fact MCP rate limits for the whole universe."""
    export = Client().call('get_database_download', {})
    dataset = export['dataset']
    if database is None:
        directory = ROOT / '.cache/data-foundry'
        directory.mkdir(parents=True, exist_ok=True)
        database = directory / export['filename']
    def verified(path):
        if not path.exists() or path.stat().st_size != export['size_bytes']:
            return False
        digest = hashlib.sha256()
        with path.open('rb') as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                digest.update(chunk)
        return digest.hexdigest() == export['sha256']
    if not verified(database):
        temporary = database.with_suffix('.download')
        with requests.get(export['url'], stream=True, timeout=(30, 90)) as response:
            response.raise_for_status()
            with temporary.open('wb') as handle:
                for chunk in response.iter_content(1024 * 1024):
                    handle.write(chunk)
        if not verified(temporary):
            temporary.unlink(missing_ok=True)
            raise ValueError('Database size or SHA-256 mismatch')
        temporary.replace(database)
    connection = sqlite3.connect(f'file:{database}?mode=ro', uri=True)
    metrics = {row[0]: json.loads(row[1]) for row in connection.execute('SELECT code,payload FROM metrics')}
    names = {code: definition.get('name_ru', code) for code, definition in metrics.items()}
    accepted, companies, source_ids = [], [], set()
    year = datetime.now(timezone.utc).year
    for ticker in tickers:
        row = connection.execute('SELECT payload FROM companies WHERE ticker=?', (ticker,)).fetchone()
        company = json.loads(row[0]) if row else {'ticker': ticker}
        facts, rejected = [], 0
        if row:
            for item in connection.execute('SELECT payload FROM facts WHERE ticker=? AND period_end>=?', (ticker, f'{year-3}-01-01')):
                fact = json.loads(item[0])
                if fact.get('metric_code') not in names or fact.get('status') not in ('canonical', 'computed'):
                    continue
                if accepted_fact(fact):
                    facts.append(fact)
                    source_ids.add(fact['source_id'])
                else:
                    rejected += 1
        companies.append({**company, 'ticker': ticker, 'facts': facts, 'rejected_count': rejected,
                          'status': 'available' if facts else ('missing' if row else 'not_found')})
        accepted.extend(facts)
    evidence = {}
    for source_id in source_ids:
        row = connection.execute('SELECT payload FROM evidence WHERE id=?', (source_id,)).fetchone()
        if not row:
            raise ValueError('Missing source evidence')
        evidence[source_id] = json.loads(row[0])
    connection.close()
    if not accepted:
        raise ValueError('Empty accepted import; previous file preserved')
    result = {'schema_version': 1, 'dataset': dataset, 'metrics': names,
        'meta': {'fetched_at': datetime.now(timezone.utc).isoformat(), 'source': ENDPOINT,
                 'requested_companies': len(tickers), 'covered_companies': sum(bool(c['facts']) for c in companies),
                 'facts': len(accepted), 'database_sha256': export['sha256'],
                 'note': 'Все доступные проверенные метрики с исходными единицами и областью отчётности; входы ML и оценки стоимости не заменены.'},
        'companies': companies, 'evidence': evidence}
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix('.tmp')
    temp.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    temp.replace(output)
    return result


def build_summary(data, output):
    """Latest observations for EVERY metric and reporting variant, without full history."""
    companies, sources = [], set()
    keep = ('id', 'source_id', 'metric_code', 'period', 'value', 'unit', 'currency',
            'accounting_basis', 'consolidation_scope', 'dimensions', 'methodology', 'layer')
    for company in data['companies']:
        facts, groups = company['facts'], {}
        for fact in facts:
            key = (fact['metric_code'], fact.get('accounting_basis'), fact.get('consolidation_scope'),
                   fact.get('unit'), fact.get('currency'),
                   json.dumps(fact.get('dimensions', {}), sort_keys=True),
                   (fact.get('methodology') or {}).get('code'))
            groups.setdefault(key, []).append(fact)
        selected = []
        for group in groups.values():
            end = max(f['period']['end'] for f in group)
            selected.extend({k: f.get(k) for k in keep} for f in group if f['period']['end'] == end)
        sources.update(f['source_id'] for f in selected)
        companies.append({**{k: v for k, v in company.items() if k != 'facts'},
            'facts': selected, 'fact_count': len(facts),
            'latest_report_end': max((f['period']['end'] for f in facts), default=None)})
    evidence_keys = ('source_id', 'kind', 'official_url', 'locator', 'title', 'verification_scope')
    summary = {**data, 'companies': companies,
        'evidence': {key: {k: data['evidence'][key].get(k) for k in evidence_keys} for key in sources},
        'is_summary': True}
    temporary = output.with_suffix('.tmp')
    temporary.write_text(json.dumps(summary, ensure_ascii=False, separators=(',', ':')) + '\n')
    temporary.replace(output)
    return summary


def partition_snapshot(data, output):
    """Keep each company's history separate: no giant browser/GitHub payload."""
    directory = output.parent / 'data_foundry/companies'
    directory.mkdir(parents=True, exist_ok=True)
    companies = []
    for company in data['companies']:
        ticker = company['ticker']
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', ticker):
            raise ValueError('Invalid company file identifier')
        source_ids = {f['source_id'] for f in company['facts']}
        payload = {'schema_version': 1, 'dataset': data['dataset'], 'company': company,
                   'evidence': {key: data['evidence'][key] for key in source_ids}}
        target = directory / (ticker + '.json')
        temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(',', ':')) + '\n')
        if temporary.stat().st_size > 50 * 1024 * 1024:
            raise ValueError(f'Company payload too large: {ticker}')
        temporary.replace(target)
        companies.append({**{k: v for k, v in company.items() if k != 'facts'},
                          'facts': [], 'fact_count': len(company['facts'])})
    index = {**data, 'companies': companies, 'evidence': {}, 'is_index': True}
    temporary = output.with_suffix('.tmp')
    temporary.write_text(json.dumps(index, ensure_ascii=False, separators=(',', ':')) + '\n')
    temporary.replace(output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--universe', type=Path, default=ROOT / 'site/data.json')
    parser.add_argument('--tickers', nargs='+')
    parser.add_argument('--output', type=Path, default=ROOT / 'site/data_foundry.json')
    parser.add_argument('--database', type=Path, help='Already downloaded SQLite; size and SHA-256 are checked')
    parser.add_argument('--summary-only', action='store_true', help='Rebuild summary from an existing accepted snapshot')
    args = parser.parse_args()
    try:
        result = (json.loads(args.output.read_text()) if args.summary_only else
                  build_from_database(args.tickers or universe(args.universe), args.output, args.database))
        if result.get('is_index'):
            companies, evidence = [], {}
            for entry in result['companies']:
                path = args.output.parent / 'data_foundry/companies' / (entry['ticker'] + '.json')
                payload = json.loads(path.read_text())
                companies.append(payload['company'])
                evidence.update(payload['evidence'])
            result = {**result, 'companies': companies, 'evidence': evidence}
            result.pop('is_index', None)
        build_summary(result, args.output.with_name('data_foundry_summary.json'))
        partition_snapshot(result, args.output)
        print(json.dumps(result['meta'], ensure_ascii=False))
    except Exception as exc:
        print(f'[foundry] import failed; previous file untouched: {exc}', file=sys.stderr)
        sys.exit(1)
