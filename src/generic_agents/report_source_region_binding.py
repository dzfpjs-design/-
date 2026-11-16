"""Explicit, privately saved multi-region selections, separate from source approval.

No provider, OCR, conversion, Job dispatch, report-draft or paid-row access occurs.
Existing source-review protocols are imported lazily and are never normalized here.
"""
import copy
import hashlib
import json
import math
from functools import wraps
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field, model_validator
from sqlalchemy import select, text, func

from .contracts import Contract, digest
from .db import session
from .material_ocr_read_session import read_session
from .models import SetupMaterial, SetupDraft, MaterialOcrRun as Run, MaterialOcrUnit as Unit
from .report_quote_binding_models import QuoteBindingDraft
from .security import Principal, authenticated, require
from .services import scoped, audit
from . import material_ocr as ocr

router = APIRouter()
P = Annotated[Principal, Depends(authenticated)]
DB = Annotated[object, Depends(session)]
ReadDB = Annotated[object, Depends(read_session)]
Hash = Annotated[str, Field(pattern=r'^[a-f0-9]{64}$', strict=True)]
Positive = Annotated[int, Field(ge=1, strict=True)]
STATE = 'report-quote-binding-state-v1'
DRAFT = 'report-quote-binding-draft-v1'
RECEIPT = 'report-quote-binding-receipt-v1'
OPTIONS = 'report-source-region-options-v1'
ACTION = 'quote_binding_draft_save'
MAX_STATE_BYTES = 2 * 1024 * 1024
MAX_OWNED_DRAFTS = 100


def fail(code, message, status=409):
    raise HTTPException(status, {'code': code, 'message': message}, headers={'Cache-Control': 'private, no-store'})


def _safe(value):
    if isinstance(value, str):
        value.encode('utf-8')
        if '\x00' in value: raise ValueError('NUL')
    elif isinstance(value, float):
        if not math.isfinite(value): raise ValueError('nonfinite')
    elif isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str): raise ValueError('key')
            _safe(key); _safe(item)
    elif isinstance(value, list):
        for item in value: _safe(item)
    elif value is not None and type(value) not in (bool, int): raise ValueError('JSON')
    return value


class Safe(Contract):
    @model_validator(mode='after')
    def safe_json(self):
        try: _safe(self.model_dump(mode='json'))
        except (UnicodeError, ValueError, RecursionError, OverflowError): raise ValueError('选择包含不能保存的文字或数值')
        return self


class MaterialRef(Safe):
    id: UUID
    version: Positive
    hash: Hash


class CopyAnchor(Safe):
    source_ref: MaterialRef
    source_evidence_id: UUID
    source_name: str = Field(min_length=1, max_length=250, strict=True)
    copy_material_id: UUID
    copy_sha256: Hash
    copy_evidence_id: UUID
    provenance_hash: Hash
    run_id: UUID
    generation: Positive


class DraftAnchor(Safe):
    handoff_id: UUID
    candidate_index: int = Field(ge=0, lt=20, strict=True)
    copies: list[CopyAnchor] = Field(min_length=1, max_length=50)

    @model_validator(mode='after')
    def distinct(self):
        if (len({c.source_ref.id for c in self.copies}) != len(self.copies)
                or len({c.copy_material_id for c in self.copies}) != len(self.copies)
                or any(c.source_ref.id == c.copy_material_id for c in self.copies)):
            raise ValueError('每份原件必须对应独立且唯一的副本')
        return self


class Reference(Safe):
    scope: Literal['original', 'current', 'current_flow']
    path: str = Field(pattern=r'^/', min_length=1, max_length=500, strict=True)
    material_id: UUID
    version: Positive
    hash: Hash
    evidence_id: UUID
    reading_hash: Hash | None
    block_id: str = Field(min_length=1, max_length=250, strict=True)
    location: dict
    quote: str = Field(min_length=1, max_length=4000, strict=True)

    @model_validator(mode='after')
    def real_text(self):
        if not self.quote.strip(): raise ValueError('引用不能为空白')
        return self


Coordinate = Annotated[str, Field(pattern=r'^-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$', max_length=64, strict=True)]


class RegionSpan(Safe):
    page: Positive
    unit_id: UUID
    revision_id: UUID
    revision_hash: Hash
    revision_kind: Literal['raw', 'edited', 'confirmed']
    image_sha256: Hash
    region_id: str = Field(min_length=1, max_length=100, strict=True)
    region_text_hash: Hash
    box: list[Coordinate] = Field(min_length=4, max_length=4)
    start: int = Field(ge=0, strict=True)
    end: Positive

    @model_validator(mode='after')
    def nonempty(self):
        if self.end <= self.start: raise ValueError('请选择有文字的区域片段')
        return self


class RegionBinding(Safe):
    reference: Reference
    copy_material_id: UUID
    spans: list[RegionSpan] = Field(min_length=1, max_length=500)


class BindingDraftValue(Safe):
    configuration_revision: Positive
    configuration_hash: Hash
    fact_hash: Hash
    mapping_basis_hash: Hash
    copy_refs: list[MaterialRef] = Field(min_length=1, max_length=50)
    bindings: list[RegionBinding] = Field(max_length=1000)

    @model_validator(mode='after')
    def distinct(self):
        if len({c.id for c in self.copy_refs}) != len(self.copy_refs): raise ValueError('副本不可重复')
        keys = [(b.reference.scope, b.reference.path) for b in self.bindings]
        if len(set(keys)) != len(keys): raise ValueError('同一引用位置不可重复选择')
        return self


class SaveBindingDraft(Safe):
    request_id: UUID
    expected_revision: int = Field(ge=0, strict=True)
    anchor: DraftAnchor
    value: BindingDraftValue


def integrity(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try: return function(*args, **kwargs)
        except HTTPException: raise
        except (ValueError, KeyError, TypeError, AttributeError, IndexError, UnicodeError, RecursionError, OverflowError):
            fail('binding_integrity', '区域选择或保存记录无法完整核对；原稿保留')
    return wrapped


def _h():
    from . import report_configuration_handoff
    return report_configuration_handoff


def _projection():
    from . import report_source_projection
    return report_source_projection


def _json(value):
    return value.model_dump(mode='json') if hasattr(value, 'model_dump') else copy.deepcopy(value)


def _public(value):
    # Reversible decimal strings prevent JS/Python floating-point hash drift.
    if isinstance(value, float):
        if not math.isfinite(value): raise ValueError('coordinate')
        return str(value)
    if isinstance(value, dict): return {k: _public(v) for k, v in value.items()}
    if isinstance(value, list): return [_public(v) for v in value]
    return copy.deepcopy(value)


def text_hash(value): return hashlib.sha256(value.encode('utf-8')).hexdigest()
def _box(region): return [str(v) for v in region['box']]
def _ref(material): return {'id': material.id, 'version': material.version, 'hash': material.sha256}
def _same(left, right): return digest(left) == digest(right)


def current_flow(handoff, configuration, index):
    h = _h()
    matches = [(name, h.resolve_flow(configuration.data, name)) for name in h.names(configuration.data)
        if h.resolve_flow(configuration.data, name).get('reportSource', {}).get('handoff_id') == handoff['id']
        and h.resolve_flow(configuration.data, name).get('reportSource', {}).get('candidate_index') == index]
    if len(matches) != 1: fail('binding_flow_changed', '当前业务或来源归属已变化；核对稿保留')
    return matches[0]


@integrity
def canonical_references(db, p, handoff, configuration, candidate_index, *, frozen_basis=None):
    """Ordered original/current saved refs, then all current flow citation sites.

    A current PDF citation is only a request to review, not proof from an old span
    marker. Its copy must belong to the same genuine original provenance chain.
    """
    item = _projection().selected(handoff, p, candidate_index)
    originals = {m['id']: m for m in handoff['materials']}
    refs = [Reference.model_validate(_public(r)).model_dump(mode='json') for r in item['source_refs']]
    if len({(r['scope'], r['path']) for r in refs}) != len(refs): raise ValueError('reference paths')
    if frozen_basis is None:
        _, flow = current_flow(handoff, configuration, candidate_index)
    else:
        if (set(frozen_basis) != {'business', 'steps'} or not isinstance(frozen_basis['business'], dict)
                or not isinstance(frozen_basis['steps'], list)):
            raise ValueError('frozen basis')
        flow = {**copy.deepcopy(frozen_basis['business']), 'steps': copy.deepcopy(frozen_basis['steps'])}
    for path, owner in [('/business', flow), *[('/steps/' + str(i), s) for i, s in enumerate(flow.get('steps', []))]]:
        for i, cite in enumerate(owner.get('citations') or []):
            material = scoped(db, SetupMaterial, cite['material_id'], p)
            known = [r for r in refs if r['material_id'] == material.id and r['block_id'] == cite['block_id'] and r['quote'] == cite['quote']]
            if material.id in originals:
                _projection().origin_identity(material, originals[material.id], p)
                if not known: fail('binding_current_quote_changed', '当前原 Word 引用不在已共享出处中；请保留稿并明确核对原引用')
                base = copy.deepcopy(known[0])
                if any(r['location'] != base['location'] or r['reading_hash'] != base['reading_hash'] for r in known):
                    raise ValueError('source location')
            else:
                prov = material.data.get('document_copy')
                origin_id = prov.get('source_material_id') if isinstance(prov, dict) else None
                if origin_id not in originals: fail('binding_source_outside_scope', '当前引用不属于此交接原件或其真实副本')
                origin = scoped(db, SetupMaterial, origin_id, p)
                _projection().origin_identity(origin, originals[origin_id], p)
                _projection().copy_provenance(db, p, origin, material, prov['integrity_hash'])
                block = next((b for b in material.data.get('blocks', []) if b['id'] == cite['block_id']), None)
                if block is None: fail('binding_current_quote_changed', '当前 PDF 引用锚点已变化；核对稿保留')
                base = {'material_id': material.id, 'version': material.version, 'hash': material.sha256,
                    'evidence_id': material.evidence_id, 'reading_hash': None, 'block_id': cite['block_id'],
                    'location': _public(block.get('location', {})), 'quote': cite['quote']}
            base.update(scope='current_flow', path=path + '/citations/' + str(i))
            refs.append(Reference.model_validate(base).model_dump(mode='json'))
    if len(refs) > 1000: fail('binding_capacity', '引用数量超过核对上限；原稿保留', 413)
    return refs


def _copy_anchor(origin, material, provenance, run):
    return {'source_ref': _ref(origin), 'source_evidence_id': origin.evidence_id, 'source_name': origin.name,
        'copy_material_id': material.id, 'copy_sha256': material.sha256, 'copy_evidence_id': material.evidence_id,
        'provenance_hash': provenance['integrity_hash'], 'run_id': run.id, 'generation': run.generation}


@integrity
def _chain(db, p, anchor, *, lock=False):
    anchor = CopyAnchor.model_validate(_json(anchor)).model_dump(mode='json')
    # Same fixed ordering as other material writes; preserve the original renderer.
    found = {str(m.id): m for m in db.scalars(select(SetupMaterial).where(SetupMaterial.workspace_id == p.workspace_id,
        SetupMaterial.id.in_(sorted([anchor['source_ref']['id'], anchor['copy_material_id']]))).order_by(SetupMaterial.id)
        .with_for_update().execution_options(populate_existing=True) if lock else
        select(SetupMaterial).where(SetupMaterial.workspace_id == p.workspace_id,
            SetupMaterial.id.in_([anchor['source_ref']['id'], anchor['copy_material_id']])))}
    origin = found.get(anchor['source_ref']['id']); material = found.get(anchor['copy_material_id'])
    if origin is None or material is None: fail('binding_source_changed', '原件或副本已不可用；核对稿保留')
    if (_ref(origin) != anchor['source_ref'] or origin.evidence_id != anchor['source_evidence_id']
            or origin.name != anchor['source_name'] or origin.archived or material.archived
            or material.sha256 != anchor['copy_sha256'] or material.evidence_id != anchor['copy_evidence_id']):
        fail('binding_source_changed', '原件或副本身份已变化；核对稿保留')
    _h().current_materials(db, p, [_ref(origin), _ref(material)], ready=False)
    provenance = _projection().copy_provenance(db, p, origin, material, anchor['provenance_hash'])
    run = ocr.checked_run(db, p, material, anchor['run_id'], lock=lock)
    cov = ocr.verify_coverage(run.data['coverage'])
    if (run.generation != anchor['generation'] or material.data.get('ocr_run_id') != run.id
            or cov['source_sha256'] != material.sha256 or cov.get('force_all_pages') is not True
            or cov.get('profile') != {'kind': 'document_copy_full_pages_v1', 'provenance_hash': anchor['provenance_hash']}
            or cov['unit_count'] != provenance['receipt']['page_count'] or cov.get('gaps')):
        fail('binding_run_changed', '完整页范围或识别代次已变化；核对稿保留')
    return origin, material, run


def _span(unit, version, region, start, end):
    return {'page': unit.data['descriptor']['page'], 'unit_id': unit.id, 'revision_id': version.id,
        'revision_hash': version.content_hash, 'revision_kind': version.kind, 'image_sha256': unit.data['image_sha256'],
        'region_id': region['id'], 'region_text_hash': text_hash(region['text']), 'box': _box(region), 'start': start, 'end': end}


@integrity
def validate_region_binding(db, p, anchor, binding, require_confirmed=False, lock=False):
    """Check one explicitly frozen version; never read latest as a replacement."""
    anchor = DraftAnchor.model_validate(_json(anchor)).model_dump(mode='json')
    binding = RegionBinding.model_validate(_json(binding)).model_dump(mode='json')
    choices = [a for a in anchor['copies'] if a['copy_material_id'] == binding['copy_material_id']]
    if len(choices) != 1: fail('binding_source_outside_scope', '区域不属于本次明确选择的副本')
    selected = choices[0]; origin, material, run = _chain(db, p, selected, lock=lock)
    if binding['reference']['material_id'] not in {origin.id, material.id}:
        fail('binding_source_outside_scope', '引用与区域不属于同一原件来源链')
    spans = binding['spans']; first = spans[0]
    unit = ocr.checked_unit(db, p, material, run, first['unit_id'], lock=lock)
    versions = ocr.checked_versions(db, p, run, unit)
    version = next((v for v in versions if v.id == first['revision_id']), None)
    if (version is None or version.content_hash != first['revision_hash'] or version.kind != first['revision_kind']
            or unit.data['descriptor']['kind'] != 'pdf_page'):
        fail('binding_revision_changed', '所选页面文字版本无法核对；旧稿保留')
    if require_confirmed and (version.kind != 'confirmed' or unit.data.get('confirmed_id') != version.id or unit.status != 'confirmed'):
        fail('binding_confirmation_pending', '所选引用尚未明确绑定当前人工确认版本')
    regions = version.data['regions']; positions = {r['id']: i for i, r in enumerate(regions)}
    previous = None; pieces = []; locations = []
    for n, span in enumerate(spans):
        position = positions.get(span['region_id'])
        if position is None or previous is not None and position != previous + 1:
            fail('binding_region_order', '请选择同页相邻、顺序一致的区域，不能跳过或重复')
        region = regions[position]
        if (span != _span(unit, version, region, span['start'], span['end'])
                or span['end'] > len(region['text']) or n and span['start'] != 0
                or n < len(spans)-1 and span['end'] != len(region['text'])):
            fail('binding_region_changed', '区域、坐标、文字或偏移与冻结版本不一致')
        pieces.append(region['text'][span['start']:span['end']]); previous = position
        locations.append({'page': span['page'], 'unit_id': unit.id, 'revision_id': version.id,
            'region_id': region['id'], 'box': span['box'], 'start': span['start'], 'end': span['end']})
    if ''.join(pieces) != binding['reference']['quote']:
        fail('binding_quote_mismatch', '所选区域须逐字组成完整原引用，不能改空白、数字、单位或标点')
    first_block = None
    if require_confirmed:
        _projection().confirmed_copy(db, p, origin, material, selected['provenance_hash'], lock=lock)
        matching = [b for b in material.data.get('blocks', []) if b.get('location', {}).get('ocr', {}).get('revision_id') == version.id
            and b['location']['ocr'].get('region_id') == first['region_id']]
        if len(matching) != 1: fail('binding_formal_block_changed', '正式确认区域锚点无法唯一核对')
        first_block = matching[0]['id']
    return {'binding_id': digest(binding), 'quote': binding['reference']['quote'], 'copy_material_id': material.id,
        'page': first['page'], 'unit_id': unit.id, 'revision_id': version.id, 'revision_hash': version.content_hash,
        'image_sha256': unit.data['image_sha256'], 'spans': copy.deepcopy(spans), 'locations': locations,
        'first_block_id': first_block}


def _candidates(reference, material, run, pages):
    quote = reference['quote']; candidates = []
    for unit, version in pages:
        regions = version.data['regions']; joined = ''.join(r['text'] for r in regions); starts = []; cursor = 0
        for region in regions: starts.append(cursor); cursor += len(region['text'])
        at = joined.find(quote)
        while at >= 0:
            end = at + len(quote); selected = [(i, r) for i, r in enumerate(regions)
                if starts[i] < end and starts[i] + len(r['text']) > at]
            if selected and [i for i, _ in selected] == list(range(selected[0][0], selected[-1][0]+1)):
                spans = [_span(unit, version, r, max(0, at-starts[i]), min(len(r['text']), end-starts[i])) for i, r in selected]
                binding = {'reference': copy.deepcopy(reference), 'copy_material_id': material.id, 'spans': spans}
                candidates.append({'binding': binding, 'binding_id': digest(binding),
                    'regions': [{'id': r['id'], 'text': r['text'], 'box': _box(r)} for r in regions],
                    'image_url': '/api/setup/materials/' + material.id + '/ocr/units/' + unit.id
                        + '/image?run_id=' + run.id + '&image_sha256=' + unit.data['image_sha256']})
            if len(candidates) > 100: fail('binding_candidate_capacity', '逐字候选过多，请缩小引用核对范围；原稿保留', 413)
            at = joined.find(quote, at+1)
    return candidates


@integrity
def source_region_options(db, p, handoff, candidate_index):
    h = _h(); configuration = h.configuration_row(db, p)
    refs = canonical_references(db, p, handoff, configuration, candidate_index)
    name, flow = current_flow(handoff, configuration, candidate_index)
    origins = {m['id']: m for m in handoff['materials']}
    rows = list(db.scalars(select(SetupMaterial).where(SetupMaterial.workspace_id == p.workspace_id,
        SetupMaterial.archived.is_(False), SetupMaterial.data['document_copy']['source_material_id'].as_string().in_(list(origins)))
        .order_by(SetupMaterial.id).limit(51)))
    if len(rows) > 50: fail('binding_capacity', '副本数量超过核对上限；原稿保留', 413)
    copies = []; candidates = {digest(ref): [] for ref in refs}
    for material in rows:
        prov = material.data['document_copy']; origin = scoped(db, SetupMaterial, prov['source_material_id'], p)
        _projection().origin_identity(origin, origins[origin.id], p, require_word=True)
        provenance = _projection().copy_provenance(db, p, origin, material, prov['integrity_hash'])
        run = ocr.checked_run(db, p, material, material.data.get('ocr_run_id'))
        anchor = _copy_anchor(origin, material, provenance, run)
        _chain(db, p, anchor)
        units = list(db.scalars(select(Unit).where(Unit.workspace_id == p.workspace_id, Unit.run_id == run.id)
            .order_by(Unit.unit_index)))
        if len(units) != run.data['prepared'] or [u.unit_index for u in units] != list(range(1, len(units)+1)):
            fail('binding_prepared_integrity', '已保存页面范围无法核对；原稿保留')
        pages = []; confirmed = 0
        for unit in units:
            ocr.checked_unit(db, p, material, run, unit.id, image=False)
            versions = ocr.checked_versions(db, p, run, unit)
            if versions: pages.append((unit, versions[-1]))
            if unit.status == 'confirmed': confirmed += 1
        whole = bool(material.data.get('ocr_confirmation_id')) and confirmed == run.data['coverage']['unit_count']
        if whole: _projection().confirmed_copy(db, p, origin, material, anchor['provenance_hash'])
        copies.append({'anchor': anchor, 'copy_ref': _ref(material), 'copy_name': material.name,
            'page_count': run.data['coverage']['unit_count'], 'prepared_pages': run.data['prepared'],
            'confirmed_pages': confirmed, 'whole_document_confirmed': whole,
            'reason_code': None if whole else 'binding_confirmation_pending',
            'reason': None if whole else '页面选择可保存为私有核对稿，正式来源复核仍须全部页面及整篇确认'})
        for ref in refs:
            if ref['material_id'] in {origin.id, material.id}:
                candidates[digest(ref)].extend(_candidates(ref, material, run, pages))
    value = {'schema': OPTIONS, 'workspace_id': p.workspace_id, 'handoff_id': handoff['id'],
        'candidate_index': candidate_index, 'configuration': {'revision': configuration.version, 'hash': digest(configuration.data),
            'fact_hash': h.fact_hash(name, flow), 'mapping_basis_hash': digest(_projection().flow_basis(flow))},
        'copies': copies, 'quotes': [{'reference': ref, 'reference_hash': digest(ref), 'candidates': candidates[digest(ref)]} for ref in refs],
        'can_apply': False, 'can_publish': False}
    if len(json.dumps(value, ensure_ascii=False).encode()) > MAX_STATE_BYTES:
        fail('binding_capacity', '核对候选超过整体显示上限；未截断原文或候选', 413)
    return {**value, 'options_hash': digest(value)}


def command_hash(p, handoff_id, draft_id, body):
    return digest({'action': ACTION, 'actor_id': p.user_id, 'workspace_id': p.workspace_id,
        'handoff_id': str(handoff_id), 'draft_id': str(draft_id), 'body': _json(body)})


def _draft_hash(row, anchor, value_hash, revision):
    return digest({'schema': DRAFT, 'id': row.id, 'workspace_id': row.workspace_id, 'actor_id': row.actor_id,
        'handoff_id': row.handoff_id, 'candidate_index': row.candidate_index, 'revision': revision,
        'anchor': anchor, 'value_hash': value_hash})


@integrity
def checked_draft(db, p, handoff_id, draft_id, lock=False):
    query = select(QuoteBindingDraft).where(QuoteBindingDraft.id == str(draft_id), QuoteBindingDraft.workspace_id == p.workspace_id,
        QuoteBindingDraft.actor_id == p.user_id, QuoteBindingDraft.handoff_id == str(handoff_id))
    if lock: query = query.with_for_update().execution_options(populate_existing=True)
    row = db.scalar(query)
    if row is None: fail('binding_draft_not_found', '未找到本人此范围的核对稿', 404)
    state = row.data
    if (set(state) != {'schema', 'anchor', 'history', 'state_hash'} or state['schema'] != STATE
            or state['state_hash'] != digest({k: v for k, v in state.items() if k != 'state_hash'})
            or type(row.version) is not int or row.version < 1 or len(state['history']) != row.version):
        raise ValueError('state')
    anchor = DraftAnchor.model_validate(state['anchor']).model_dump(mode='json')
    if anchor != state['anchor'] or anchor['handoff_id'] != row.handoff_id or anchor['candidate_index'] != row.candidate_index:
        raise ValueError('anchor')
    requests = set()
    for number, entry in enumerate(state['history'], 1):
        if set(entry) != {'revision', 'value', 'value_hash', 'draft_hash', 'command', 'receipt'}: raise ValueError('entry')
        cmd = SaveBindingDraft.model_validate(entry['command']).model_dump(mode='json'); receipt = entry['receipt']
        expected = {'schema': RECEIPT, 'action': ACTION, 'draft_id': row.id, 'actor_id': row.actor_id,
            'workspace_id': row.workspace_id, 'handoff_id': row.handoff_id, 'candidate_index': row.candidate_index,
            'request_id': cmd['request_id'], 'command_hash': command_hash(p, row.handoff_id, row.id, cmd),
            'accepted_revision': number, 'value_hash': digest(cmd['value']),
            'draft_hash': _draft_hash(row, anchor, digest(cmd['value']), number)}
        if (not _same(cmd, entry['command']) or cmd['anchor'] != anchor or cmd['expected_revision'] != number-1 or cmd['request_id'] in requests
                or type(entry['revision']) is not int or entry['revision'] != number or not _same(entry['value'], cmd['value'])
                or entry['value_hash'] != expected['value_hash'] or entry['draft_hash'] != expected['draft_hash']
                or not _same(receipt, {**expected, 'receipt_hash': digest(expected)})):
            raise ValueError('history receipt')
        requests.add(cmd['request_id'])
    _safe(state)
    if len(json.dumps(state, ensure_ascii=False).encode()) > MAX_STATE_BYTES: raise ValueError('state size')
    return row


def _validate_value(db, p, handoff, configuration, anchor, value, *, formal=False, lock=False):
    h = _h(); name, flow = current_flow(handoff, configuration, anchor['candidate_index'])
    if (configuration.version != value['configuration_revision'] or digest(configuration.data) != value['configuration_hash']
            or h.fact_hash(name, flow) != value['fact_hash'] or digest(_projection().flow_basis(flow)) != value['mapping_basis_hash']):
        fail('binding_configuration_changed', '业务、依据或配置版本已变化；已存稿保留，请明确重新核对')
    refs = canonical_references(db, p, handoff, configuration, anchor['candidate_index'])
    positions = {(r['scope'], r['path']): (i, r) for i, r in enumerate(refs)}
    indexes = []; output = []; copy_refs = {r['id']: r for r in value['copy_refs']}
    if set(copy_refs) != {a['copy_material_id'] for a in anchor['copies']}:
        fail('binding_copy_scope_changed', '副本清单必须完整对应本次固定来源范围')
    originals = {m['id']: m for m in handoff['materials']}
    if [r['id'] for r in value['copy_refs']] != [a['copy_material_id'] for a in anchor['copies']]:
        fail('binding_copy_scope_changed', '副本清单顺序必须对应明确选择的固定来源顺序')
    for a in sorted(anchor['copies'], key=lambda a: a['copy_material_id']):
        origin, material, _ = _chain(db, p, a, lock=lock)
        if origin.id not in originals: fail('binding_source_outside_scope', '副本原件不属于此已共享交接')
        _projection().origin_identity(origin, originals[origin.id], p, require_word=True)
        if copy_refs[material.id] != _ref(material): fail('binding_copy_changed', '副本版本已变化；原选择保留，请明确核对当前版本')
    for binding in value['bindings']:
        ref = binding['reference']; pair = positions.get((ref['scope'], ref['path']))
        if pair is None or not _same(pair[1], ref): fail('binding_reference_changed', '引用位置或原文字已变化；旧稿保留')
        indexes.append(pair[0]); output.append(validate_region_binding(db, p, anchor, binding, require_confirmed=formal, lock=lock))
    if indexes != sorted(set(indexes)) or formal and indexes != list(range(len(refs))):
        fail('binding_reference_order', '引用选择须按原位置顺序；正式复核不能遗漏任何引用')
    return output


@integrity
def draft_view(db, p, row):
    # Integrity/ownership is checked before any source-status exception is mapped.
    checked_draft(db, p, row.handoff_id, row.id)
    state = row.data; latest = state['history'][-1]; current = {'matches': True, 'reason_code': None, 'reason': None}
    try:
        h = _h(); handoff = h.owned_record(db, p, row.handoff_id).data['record']; configuration = h.configuration_row(db, p)
        _validate_value(db, p, handoff, configuration, state['anchor'], latest['value'])
        for binding in latest['value']['bindings']:
            first = binding['spans'][0]; unit = scoped(db, Unit, first['unit_id'], p)
            if unit.data.get('last_hash') != first['revision_hash']:
                fail('binding_revision_changed', '页面文字已变化，原选择保留；请明确核对新版本')
    except HTTPException as error:
        if error.status_code not in (404, 409): raise
        detail = error.detail if isinstance(error.detail, dict) else {}
        current = {'matches': False, 'reason_code': detail.get('code', 'binding_source_unavailable'),
            'reason': detail.get('message', '来源或文字版本暂不可核对，原核对稿完整保留')}
    value = {'schema': DRAFT, 'id': row.id, 'workspace_id': row.workspace_id, 'actor_id': row.actor_id,
        'handoff_id': row.handoff_id, 'candidate_index': row.candidate_index, 'revision': row.version,
        'anchor': copy.deepcopy(state['anchor']), 'value': copy.deepcopy(latest['value']), 'value_hash': latest['value_hash'],
        'draft_hash': latest['draft_hash'], 'history': [{key: copy.deepcopy(e[key]) for key in ('revision', 'value', 'value_hash', 'draft_hash')}
            | {'request_id': e['command']['request_id']} for e in state['history']], 'current': current,
        'can_save': p.role in {'admin', 'operator'}, 'can_apply': False, 'can_publish': False}
    return {**value, 'view_hash': digest(value)}


def _reply(db, p, row, entry):
    receipt = copy.deepcopy(entry['receipt']); latest = row.data['history'][-1]
    return {'draft': draft_view(db, p, row), 'receipt': receipt,
        'current_matches': row.version == receipt['accepted_revision'] and latest['value_hash'] == receipt['value_hash']
            and latest['draft_hash'] == receipt['draft_hash']}


@integrity
def save_binding_draft(db, p, handoff_id, draft_id, body):
    body = SaveBindingDraft.model_validate(_json(body)); command = body.model_dump(mode='json')
    # All writes to this ID serialize, including create, before inspecting nonce.
    db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))'), {'key': 'quote-binding:' + str(draft_id)})
    existing = db.scalar(select(QuoteBindingDraft).where(QuoteBindingDraft.id == str(draft_id)))
    if existing:
        row = checked_draft(db, p, handoff_id, draft_id)
        prior = next((e for e in row.data['history'] if e['command']['request_id'] == command['request_id']), None)
        if prior:
            if prior['receipt']['command_hash'] != command_hash(p, handoff_id, draft_id, body):
                fail('binding_request_conflict', '此原编号已接受另一份选择，不能更改请求')
            return _reply(db, p, row, prior)
    require(p, 'admin', 'operator')
    h = _h(); handoff = h.owned_record(db, p, handoff_id).data['record']
    configuration = h.configuration_row(db, p, True)
    h.setup().automation_review_actor(db, p)
    anchor = command['anchor']; value = command['value']
    if anchor['handoff_id'] != str(handoff_id): fail('binding_scope_changed', '核对稿不属于此交接范围')
    _projection().selected(handoff, p, anchor['candidate_index'])
    if existing and (row.version != body.expected_revision or row.data['anchor'] != anchor):
        fail('binding_draft_conflict', '核对稿版本或固定来源范围已变化；双方选择保留')
    if not existing and body.expected_revision != 0: fail('binding_draft_conflict', '新核对稿必须明确从版本 0 创建')
    _validate_value(db, p, handoff, configuration, anchor, value, lock=True)
    if existing:
        row = checked_draft(db, p, handoff_id, draft_id, True)
        if row.version != body.expected_revision: fail('binding_draft_conflict', '另一窗口已保存；本次选择未覆盖')
        history = copy.deepcopy(row.data['history']); revision = row.version + 1
    else:
        count = db.scalar(select(func.count()).select_from(QuoteBindingDraft).where(QuoteBindingDraft.workspace_id == p.workspace_id,
            QuoteBindingDraft.actor_id == p.user_id, QuoteBindingDraft.handoff_id == str(handoff_id)))
        if count >= MAX_OWNED_DRAFTS: fail('binding_capacity', '此交接的私人核对稿已到上限；旧稿保留', 413)
        row = QuoteBindingDraft(id=str(draft_id), workspace_id=p.workspace_id, actor_id=p.user_id,
            handoff_id=str(handoff_id), candidate_index=anchor['candidate_index'], version=1)
        history = []; revision = 1
    value_hash = digest(value); draft_hash = _draft_hash(row, anchor, value_hash, revision)
    receipt = {'schema': RECEIPT, 'action': ACTION, 'draft_id': row.id, 'actor_id': p.user_id,
        'workspace_id': p.workspace_id, 'handoff_id': str(handoff_id), 'candidate_index': anchor['candidate_index'],
        'request_id': command['request_id'], 'command_hash': command_hash(p, handoff_id, draft_id, body),
        'accepted_revision': revision, 'value_hash': value_hash, 'draft_hash': draft_hash}
    receipt['receipt_hash'] = digest(receipt)
    entry = {'revision': revision, 'value': value, 'value_hash': value_hash, 'draft_hash': draft_hash, 'command': command, 'receipt': receipt}
    history.append(entry); state = {'schema': STATE, 'anchor': anchor, 'history': history}; _safe(state)
    state['state_hash'] = digest(state)
    if len(json.dumps(state, ensure_ascii=False).encode()) > MAX_STATE_BYTES:
        fail('binding_capacity', '完整核对稿与回执超过保存上限；本次未写入，旧稿和历史保留', 413)
    row.data = state; row.version = revision; db.add(row)
    audit(db, p, 'setup.quote_binding_draft.saved', row.id, {'request_id': command['request_id'], 'revision': revision,
        'handoff_id': str(handoff_id), 'value_hash': value_hash, 'draft_hash': draft_hash})
    db.flush()
    reply = _reply(db, p, row, entry)
    db.commit()
    return reply


@integrity
def formal_bindings(db, p, handoff, configuration, candidate_index, draft_ref, bindings, lock=False):
    if set(draft_ref) != {'id', 'revision', 'value_hash', 'draft_hash'}: raise ValueError('draft ref')
    row = checked_draft(db, p, handoff['id'], draft_ref['id'], lock)
    entry = row.data['history'][-1]
    if (row.candidate_index != candidate_index or type(draft_ref['revision']) is not int
            or row.version != draft_ref['revision'] or entry['value_hash'] != draft_ref['value_hash']
            or entry['draft_hash'] != draft_ref['draft_hash'] or _json(bindings) != entry['value']['bindings']):
        fail('binding_draft_conflict', '正式复核必须使用本人明确保存的当前核对稿版本')
    return _validate_value(db, p, handoff, configuration, row.data['anchor'], entry['value'], formal=True, lock=lock)


@router.get('/report-handoffs/{handoff_id}/source-region-options')
def get_region_options(handoff_id: UUID, p: P, db: ReadDB, candidate_index: int = Query(ge=0, lt=20)):
    return source_region_options(db, p, _h().owned_record(db, p, handoff_id).data['record'], candidate_index)


@router.get('/report-handoffs/{handoff_id}/quote-binding-drafts')
def list_binding_drafts(handoff_id: UUID, p: P, db: ReadDB, candidate_index: int = Query(ge=0, lt=20)):
    rows = list(db.scalars(select(QuoteBindingDraft).where(QuoteBindingDraft.workspace_id == p.workspace_id,
        QuoteBindingDraft.actor_id == p.user_id, QuoteBindingDraft.handoff_id == str(handoff_id),
        QuoteBindingDraft.candidate_index == candidate_index).order_by(QuoteBindingDraft.created_at, QuoteBindingDraft.id).limit(MAX_OWNED_DRAFTS+1)))
    if len(rows) > MAX_OWNED_DRAFTS: fail('binding_capacity', '私人核对稿数量无法完整读取，旧稿保留', 413)
    return {'schema': 'report-quote-binding-list-v1', 'workspace_id': p.workspace_id, 'actor_id': p.user_id,
        'handoff_id': str(handoff_id), 'candidate_index': candidate_index, 'items': [draft_view(db, p, checked_draft(db, p, handoff_id, r.id)) for r in rows]}


@router.get('/report-handoffs/{handoff_id}/quote-binding-drafts/{draft_id}')
def get_binding_draft(handoff_id: UUID, draft_id: UUID, p: P, db: ReadDB):
    return draft_view(db, p, checked_draft(db, p, handoff_id, draft_id))


@router.put('/report-handoffs/{handoff_id}/quote-binding-drafts/{draft_id}')
def put_binding_draft(handoff_id: UUID, draft_id: UUID, body: SaveBindingDraft, p: P, db: DB):
    return save_binding_draft(db, p, handoff_id, draft_id, body)


@router.get('/report-handoffs/{handoff_id}/quote-binding-drafts/{draft_id}/receipts/{request_id}')
def get_binding_receipt(handoff_id: UUID, draft_id: UUID, request_id: UUID, p: P, db: ReadDB):
    try: row = checked_draft(db, p, handoff_id, draft_id)
    except HTTPException as error:
        if error.status_code != 404: raise
        fail('binding_receipt_not_found', '尚未找到本人此原编号的保存回执', 404)
    entry = next((e for e in row.data['history'] if e['command']['request_id'] == str(request_id)), None)
    if entry is None: fail('binding_receipt_not_found', '尚未找到本人此原编号的保存回执', 404)
    return _reply(db, p, row, entry)
