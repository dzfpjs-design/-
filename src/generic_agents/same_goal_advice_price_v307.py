"""Register/read the one fixed official DeepSeek CNY price source.

No caller URL, rates or price authority is accepted. No model call, CloudCall,
money/token reservation, consent or send permission is created. Historical
reads preserve their original Basis/Preview time; they never renew a price.
"""
import copy
import hashlib
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from html.parser import HTMLParser
from typing import Annotated, Literal

import httpx
from fastapi import HTTPException
from pydantic import Field, StrictInt, StrictStr
from sqlalchemy import func, select

from . import evidence
from . import same_goal_advice_contracts_v302 as c
from . import same_goal_advice_money_v305 as money
from . import same_goal_advice_store_v302 as store
from .contracts import digest
from .models import Audit, Capability, uid
from .relations import lock_current_actor
from .security import Principal, require
from .services import published

URL = money.OFFICIAL_URL
MAX_BYTES = 1024 * 1024
PRICE_BASIS = 'same_goal_advice.official_price_registered_v307'
SCHEMA = 'same-goal-advice-official-price-registration-v307'
PARSER = 'deepseek-cn-v4-pro-rowspan-peak-v307'
ERROR = '固定官网价格、完整原件、实际价格审计或当前有效依据无法核对；未猜价格、换汇或发送模型'


def need(value):
    if not value: raise HTTPException(409, ERROR)


def _clock(db):
    value = db.scalar(select(func.clock_timestamp()))
    need(type(value) is datetime and value.tzinfo is not None and value.utcoffset() is not None)
    return value.astimezone(timezone.utc)


def _time(value):
    need(type(value) is str)
    value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    need(value.tzinfo is not None and value.utcoffset() is not None)
    return value.astimezone(timezone.utc)


def _parse(model, raw):
    c.base._json(raw)
    value = model.model_validate(copy.deepcopy(raw)).model_dump(mode='json')
    need(digest(value) == digest(raw))
    return value


class Authorize(c.Closed):
    request_id: c.ID
    model_pin: c.CapabilityPin
    validity_seconds: Annotated[StrictInt, Field(ge=60, le=3600)]
    reason: Annotated[StrictStr, Field(min_length=1, max_length=2000, pattern=r'\S')]


class Rates(c.Closed):
    schema: Literal['deepseek-cn-v4-pro-rowspan-peak-v307']
    provider: Literal['deepseek']
    model: Literal['deepseek-v4-pro']
    provider_model_revision: StrictStr
    currency: Literal['CNY']
    rate_unit_tokens: Literal[1000000]
    input_cache_miss_minor_per_million: Annotated[StrictInt, Field(ge=0, le=10**12)]
    output_minor_per_million: Annotated[StrictInt, Field(ge=0, le=10**12)]
    cache_discount_assumed: Literal[False]
    off_peak_discount_assumed: Literal[False]


class HTTPReceipt(c.Closed):
    method: Literal['GET']
    url: Literal['https://api-docs.deepseek.com/zh-cn/quick_start/pricing/']
    status_code: Literal[200]
    content_type: Annotated[StrictStr, Field(min_length=1, max_length=200)]
    body_representation: Literal['decoded-http-entity-utf8']
    sha256: c.Hash
    size_bytes: Annotated[StrictInt, Field(ge=1, le=MAX_BYTES)]
    requested_at: StrictStr
    fetched_at: StrictStr


class OfficialEvidenceRef(c.Closed):
    """Full fixed official HTML, separate from 20KB business text inputs."""
    evidence_id: c.ID
    sha256: c.Hash
    size_bytes: Annotated[StrictInt, Field(ge=1, le=MAX_BYTES)]
    media_type: Literal['text/plain']


class Registration(c.Closed):
    schema: Literal['same-goal-advice-official-price-registration-v307']
    workspace_id: c.ID
    actor_id: c.ID
    request_id: c.ID
    body: Authorize
    body_hash: c.Hash
    model_pin: c.CapabilityPin
    official_evidence_ref: OfficialEvidenceRef
    upload_audit_ref: c.Ref
    http_receipt: HTTPReceipt
    parsed_rates: Rates
    checked_at: StrictStr
    expires_at: StrictStr


def _normal(text): return re.sub(r'\s+', '', text).lower()


class _Tables(HTMLParser):
    """Bounded static HTML tables; no scripts, links or external fetches."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []; self.table = None; self.row = None; self.cell = None
        self.hidden = []; self.count = 0

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style', 'sup'}:
            self.hidden.append(tag); return
        if self.hidden: return
        if tag == 'table':
            need(self.table is None and len(self.tables) < 32)
            self.table = []
        elif self.table is not None and tag == 'tr':
            need(self.row is None and self.cell is None and len(self.table) < 256)
            self.row = []
        elif self.table is not None and tag in {'td', 'th'}:
            need(self.row is not None and self.cell is None)
            fields = dict(attrs)
            need(len(fields) == len(attrs))
            spans = []
            for key in ('rowspan', 'colspan'):
                raw = fields.get(key, '1')
                need(type(raw) is str and re.fullmatch(r'[1-9][0-9]?', raw) is not None)
                number = int(raw); need(number <= 16); spans.append(number)
            self.cell = {'tag': tag, 'parts': [], 'rowspan': spans[0], 'colspan': spans[1]}
            self.count += 1; need(self.count <= 4096)
        elif self.cell is not None and tag in {'br', 'p', 'div'}:
            self.cell['parts'].append(' ')

    def handle_endtag(self, tag):
        if self.hidden:
            if tag == self.hidden[-1]: self.hidden.pop()
            return
        if tag in {'td', 'th'} and self.table is not None:
            need(self.cell is not None and self.row is not None)
            self.cell['text'] = _normal(''.join(self.cell.pop('parts')))
            self.row.append(self.cell); self.cell = None
        elif tag == 'tr' and self.table is not None:
            need(self.row is not None and self.cell is None)
            self.table.append(self.row); self.row = None
        elif tag == 'table':
            need(self.table is not None and self.row is None and self.cell is None)
            self.tables.append(self.table); self.table = None

    def handle_data(self, data):
        if self.cell is not None and not self.hidden: self.cell['parts'].append(data)


def _grid(rows):
    need(0 < len(rows) <= 256)
    result = [[None] * 32 for _ in rows]
    for index, row in enumerate(rows):
        column = 0
        for cell in row:
            while column < 32 and result[index][column] is not None: column += 1
            height, width = cell['rowspan'], cell['colspan']
            need(index + height <= len(rows) and column + width <= 32)
            for y in range(index, index + height):
                for x in range(column, column + width):
                    need(result[y][x] is None)
                    result[y][x] = {'text': cell['text'], 'tag': cell['tag']}
            column += width
    used = max((i + 1 for row in result for i, cell in enumerate(row) if cell is not None), default=0)
    need(used > 0 and all(all(cell is not None for cell in row[:used]) for row in result))
    return [row[:used] for row in result]


def _minor(text):
    need(re.fullmatch(r'(?:0|[1-9][0-9]*)(?:\.[0-9]{1,6})?元', text) is not None)
    amount = Decimal(text[:-1]) * 100
    need(amount.is_finite() and amount >= 0 and amount == amount.to_integral_value() and amount <= 10**12)
    return int(amount)


@store.checked
def parse_official(raw):
    """Exact public-page table interpretation; no supplied rates are trusted."""
    need(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES and b'\x00' not in raw)
    text = raw.decode('utf-8-sig', errors='strict')
    parser = _Tables(); parser.feed(text); parser.close()
    need(parser.table is None and parser.row is None and parser.cell is None and not parser.hidden)
    candidates = []
    for rows in parser.tables:
        if not any(cell['text'] == money.MODEL for row in rows for cell in row): continue
        grid = _grid(rows)
        locations = [(y, x) for y, row in enumerate(grid) for x, cell in enumerate(row)
            if len(row) == 5 and x == 4 and cell['text'] == money.MODEL
            and row[3]['text'] == 'deepseek-flash' and all(row[i]['text'] == '模型' for i in range(3))]
        if not locations: continue
        need(len(locations) == 1 and len(grid[0]) == 5)
        header, column = locations[0]
        need(column == 4 and grid[header][3]['text'] == 'deepseek-flash'
            and all(grid[header][i]['text'] == '模型' for i in range(3)))
        revisions = [row[column]['text'] for row in grid if all(row[i]['text'] == '模型版本' for i in range(3))]
        need(len(revisions) == 1 and re.fullmatch(r'deepseek-v4(?:\.[0-9]+)?-pro-[0-9]{4,8}', revisions[0]) is not None)
        values = {}
        names = {'百万tokens输入（缓存命中）': 'hit', '百万tokens输入（缓存未命中）': 'miss', '百万tokens输出': 'output'}
        for row in grid:
            if row[0]['text'] != '价格': continue
            name, period = row[1]['text'], row[2]['text']
            need(name in names and period in {'空闲时段', '高峰时段'})
            key = (names[name], period)
            need(key not in values)
            # Both actual model columns must be explicit yuan values, not an
            # ambiguous USD/FX display. Only the Pro column determines rates.
            _minor(row[3]['text']); values[key] = _minor(row[column]['text'])
        need(set(values) == {(name, period) for name in ('hit', 'miss', 'output')
            for period in ('空闲时段', '高峰时段')})
        need(all(values[(name, '高峰时段')] >= values[(name, '空闲时段')] for name in ('hit', 'miss', 'output')))
        need(values[('miss', '高峰时段')] > 0 and values[('output', '高峰时段')] > 0)
        candidates.append({'schema': PARSER, 'provider': 'deepseek', 'model': money.MODEL,
            'provider_model_revision': revisions[0], 'currency': 'CNY', 'rate_unit_tokens': 1_000_000,
            'input_cache_miss_minor_per_million': values[('miss', '高峰时段')],
            'output_minor_per_million': values[('output', '高峰时段')],
            'cache_discount_assumed': False, 'off_peak_discount_assumed': False})
    need(len(candidates) == 1)
    return _parse(Rates, candidates[0])


def _fetch():
    """One fixed public GET, bounded while streaming and without credentials."""
    try:
        with httpx.Client(timeout=10.0, follow_redirects=False, trust_env=False) as client:
            with client.stream('GET', URL) as response:
                kind = response.headers.get('content-type')
                need(response.status_code == 200 and str(response.url) == URL
                    and type(kind) is str and kind.split(';', 1)[0].strip().lower() == 'text/html'
                    and len(kind) <= 200)
                parts = []; count = 0
                for part in response.iter_bytes():
                    need(type(part) is bytes)
                    count += len(part); need(count <= MAX_BYTES)
                    parts.append(part)
                raw = b''.join(parts); need(bool(raw))
                return raw, kind
    except httpx.HTTPError:
        raise HTTPException(409, '固定官网价格暂时无法读取；原依据保留，未使用管理员报价或其它网址') from None


def _model(db, p, model_pin, *, write=False, current=True):
    pin = _parse(c.CapabilityPin, model_pin)
    statement = select(Capability).where(Capability.workspace_id == p.workspace_id, Capability.id == pin['id'])
    statement = statement.with_for_update() if write else statement.with_for_update(read=True)
    cap = db.scalar(statement.execution_options(populate_existing=True))
    need(cap is not None and cap.kind == 'foundation_model'
        and store._pin(cap) == pin and cap.content_hash == cap.validated_hash == digest(cap.data)
        and cap.data['kind'] == 'foundation_model' and 'business' in cap.data['allowed_agent_roles']
        and cap.data['execution'].get('provider') == 'deepseek'
        and cap.data['execution'].get('model') == money.MODEL
        and cap.data['execution'].get('task') != 'embedding'
        and cap.data['data_policy'].get('cloud_allowed') is True)
    if current: need(published(db, p, cap.id, 'foundation_model') is cap)
    return cap


def _upload(db, p, file, actor):
    rows = store._rows(db, p, Audit, Audit.action == 'evidence.upload', Audit.object_id == file.id)
    expected = {'media_type': 'text/plain', 'sha256': file.sha256, 'cloud_model_id': None, 'consent': False}
    need(len(rows) == 1 and rows[0].actor_id == actor and digest(rows[0].data) == digest(expected))
    return rows[0]


def _fingerprint(event):
    return digest({key: getattr(event, key) for key in ('id', 'workspace_id', 'actor_id', 'action', 'object_id', 'data')})


def _events(db, p, model):
    return store._rows(db, p, Audit, Audit.action == PRICE_BASIS, Audit.object_id == model.id)


def _read(db, p, model_pin, event, *, now, priced_at, current):
    value = _parse(Registration, event.data)
    pin = _parse(c.CapabilityPin, model_pin)
    need(event.workspace_id == p.workspace_id and event.object_id == pin['id']
        and event.actor_id == value['actor_id'] and value['workspace_id'] == p.workspace_id
        and value['model_pin'] == value['body']['model_pin'] == pin
        and value['body_hash'] == digest(value['body']) and value['request_id'] == value['body']['request_id'])
    receipt = value['http_receipt']; checked, expires = _time(value['checked_at']), _time(value['expires_at'])
    need(receipt['content_type'].split(';', 1)[0].strip().lower() == 'text/html'
        and receipt['fetched_at'] == value['checked_at']
        and _time(receipt['requested_at']) <= checked <= event.created_at <= now
        and expires == checked + timedelta(seconds=value['body']['validity_seconds'])
        and event.created_at <= priced_at <= now and checked <= priced_at < expires)
    if current: store._member(db, p, event.actor_id, admin=True)
    file = store._fresh(db, p, evidence.Evidence, value['official_evidence_ref']['evidence_id'])
    actual, raw = evidence.evidence_bytes(db, p, file.id)
    # Later evidence consent is an independent permission ledger. It does not
    # change the captured HTML or its original no-consent upload/price Audit.
    # This reader neither verifies nor grants that later model permission.
    need(actual.id == file.id and actual.workspace_id == p.workspace_id
        and type(file.version) is int and file.version >= 1
        and actual.media_type == 'text/plain'
        and actual.data.get('uploaded_by') == event.actor_id
        and actual.data.get('price_schema') == SCHEMA
        and actual.data.get('http_original_content_type') == receipt['content_type']
        and actual.data.get('official_source_url') == URL)
    reference = _parse(OfficialEvidenceRef, {'evidence_id': actual.id, 'sha256': actual.sha256,
        'size_bytes': actual.size_bytes, 'media_type': actual.media_type})
    need(digest(reference) == digest(value['official_evidence_ref'])
        and actual.sha256 == receipt['sha256'] == hashlib.sha256(raw).hexdigest()
        and actual.size_bytes == receipt['size_bytes'] == len(raw))
    rates = parse_official(raw); need(digest(rates) == digest(value['parsed_rates']))
    upload = _upload(db, p, actual, event.actor_id)
    need(value['upload_audit_ref'] == {'id': upload.id, 'record_hash': _fingerprint(upload)})
    basis = {'id': event.id, 'record_hash': digest(event.data)}
    unsigned = {'schema': money.PRICE, 'basis_ref': basis,
        'official_original': {'url': URL, 'sha256': actual.sha256, 'size_bytes': actual.size_bytes},
        'provider': 'deepseek', 'model': money.MODEL, 'currency': 'CNY', 'rate_unit_tokens': 1_000_000,
        'input_cache_miss_minor_per_million': rates['input_cache_miss_minor_per_million'],
        'output_minor_per_million': rates['output_minor_per_million'],
        'cache_discount_assumed': False, 'off_peak_discount_assumed': False,
        'checked_at': value['checked_at'], 'expires_at': value['expires_at'],
        'database_price_authorization_verified': False, 'provider_price_lock_guaranteed': False}
    price = money.seal_price(unsigned, now=priced_at)
    return {'schema': SCHEMA + '/read', 'purpose': 'current-price-read' if current else 'historical-not-current-price-authorization',
        'price': price, 'basis_ref': basis, 'evidence_ref': reference, **money.FLAGS}


@store.checked
def authorize(db, p, body):
    require(p, 'admin'); need(type(p) is Principal)
    body = _parse(Authorize, body)
    store._reader(db, p, write=True)
    # An original retry can return its receipt after retirement/expiry. A NEW
    # registration still requires the real published model before any GET.
    model = _model(db, p, body['model_pin'], write=True, current=False)
    lock_current_actor(db, p); store._member(db, p, p.user_id, admin=True)
    now = _clock(db); events = _events(db, p, model)
    prior = [event for event in events if event.data.get('request_id') == body['request_id']]
    need(len(prior) <= 1)
    if prior:
        event = prior[0]
        need(event.actor_id == p.user_id and event.data['body_hash'] == digest(body))
        result = _read(db, p, body['model_pin'], event, now=now, priced_at=event.created_at, current=False)
        return {**result, 'purpose': 'original-registration-receipt-not-send-permission'}
    need(published(db, p, model.id, 'foundation_model') is model)
    requested = _clock(db)
    raw, content_type = _fetch()
    fetched = _clock(db); rates = parse_official(raw)
    # Fixed internal processor validates complete UTF-8 HTML before bypassing
    # the ordinary 20KB user-text upload limit; no general upload is expanded.
    file = evidence.save_immutable(db, p, raw, 'text/plain', {
        'modality': 'text', 'characters': len(raw.decode('utf-8-sig')),
        'price_schema': SCHEMA, 'http_original_content_type': content_type, 'official_source_url': URL})
    db.flush(); upload = _upload(db, p, file, p.user_id)
    reference = _parse(OfficialEvidenceRef, {'evidence_id': file.id, 'sha256': file.sha256,
        'size_bytes': file.size_bytes, 'media_type': file.media_type})
    value = {'schema': SCHEMA, 'workspace_id': p.workspace_id, 'actor_id': p.user_id,
        'request_id': body['request_id'], 'body': body, 'body_hash': digest(body), 'model_pin': body['model_pin'],
        'official_evidence_ref': reference, 'upload_audit_ref': {'id': upload.id, 'record_hash': _fingerprint(upload)},
        'http_receipt': {'method': 'GET', 'url': URL, 'status_code': 200, 'content_type': content_type,
            'body_representation': 'decoded-http-entity-utf8', 'sha256': file.sha256, 'size_bytes': len(raw),
            'requested_at': requested.isoformat(), 'fetched_at': fetched.isoformat()},
        'parsed_rates': rates, 'checked_at': fetched.isoformat(),
        'expires_at': (fetched + timedelta(seconds=body['validity_seconds'])).isoformat()}
    created = _clock(db)
    event = Audit(id=uid(), workspace_id=p.workspace_id, actor_id=p.user_id,
        action=PRICE_BASIS, object_id=model.id, created_at=created, data=_parse(Registration, value))
    with db.begin_nested():
        db.add(event); db.flush()
        return _read(db, p, body['model_pin'], event, now=_clock(db), priced_at=created, current=True)


@store.checked
def current(db, p, model_pin, *, basis_ref=None):
    with db.no_autoflush:
        store._reader(db, p); model = _model(db, p, model_pin, current=True)
        now = _clock(db); events = _events(db, p, model)
        need(bool(events) and all(event.created_at is not None and event.created_at.tzinfo is not None
            and event.created_at <= now for event in events))
        latest = max(event.created_at for event in events)
        selected = [event for event in events if event.created_at == latest]; need(len(selected) == 1)
        event = selected[0]
        expected = {'id': event.id, 'record_hash': digest(event.data)}
        if basis_ref is not None: need(_parse(c.Ref, basis_ref) == expected)
        return _read(db, p, model_pin, event, now=now, priced_at=now, current=True)


@store.checked
def checked_original(db, p, model_pin, *, basis_ref, priced_at):
    """Priced_at must come from the caller's real immutable Preview Audit.

    This method never authenticates that outer Audit or supplies a current send
    permit. New sends must call current with the exact original basis_ref.
    """
    with db.no_autoflush:
        store._reader(db, p); model = _model(db, p, model_pin, current=False)
        basis = _parse(c.Ref, basis_ref); now = _clock(db)
        need(type(priced_at) is datetime and priced_at.tzinfo is not None and priced_at.utcoffset() is not None)
        events = [event for event in _events(db, p, model) if event.id == basis['id']]
        need(len(events) == 1 and basis['record_hash'] == digest(events[0].data))
        return _read(db, p, model_pin, events[0], now=now,
            priced_at=priced_at.astimezone(timezone.utc), current=False)

