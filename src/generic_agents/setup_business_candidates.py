"""Unconfirmed, workspace-bound business candidates and exact source preview.

No document or model request is made here. Candidate provenance is supplied
by the caller; a text match proves neither report completeness nor approval.
"""
import copy
import hashlib
import unicodedata
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, ValidationError, model_validator

from .contracts import Contract, digest
from .db import session
from .evidence import evidence_bytes
from .models import SetupMaterial, uid
from .security import Principal, authenticated, require
from .services import scoped, audit, check_version
from .setup_citation_matcher import resolve_material_citation


_BAD_KEYS = {'__proto__', 'prototype', 'constructor'}
_REASONS = Literal['not_found', 'ambiguous', 'invalid_quote', 'material_changed', 'invalid_material']


def _safe_name(value):
    return bool(value.strip()) and value == value.strip() and _name_key(value) not in _BAD_KEYS


def _name_key(value):
    return unicodedata.normalize('NFKC', value).casefold()


class CandidateMaterial(Contract):
    id: str = Field(min_length=1, max_length=256)
    version: int = Field(strict=True, ge=1)
    hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    engine: str = Field(min_length=1, max_length=256)
    status: str = Field(default='', max_length=80)


class CandidateSource(Contract):
    source_text: str = Field(min_length=1, max_length=20000)
    quote: str = Field(min_length=1, max_length=10000)


class CandidateStep(Contract):
    key: str = Field(pattern=r'^[a-z][a-z0-9_]{0,39}$')
    name: str = Field(min_length=1, max_length=120)
    purpose: str = Field(default='', max_length=1000)
    agent: Literal['业务智能体', '多模感知智能体', '数据管理智能体', '人工确认']
    role: str = Field(default='', max_length=100)
    output: str = Field(default='', max_length=1000)
    condition: str = Field(default='', max_length=500)
    next_key: str = Field(default='auto', max_length=40)
    else_key: str = Field(default='end', max_length=40)
    inference_reason: str = Field(default='', max_length=1000)
    sources: list[CandidateSource] = Field(default_factory=list, max_length=4)


class CandidateBusiness(Contract):
    key: str = Field(pattern=r'^[a-z][a-z0-9_]{0,39}$')
    name: str = Field(min_length=1, max_length=100)
    trigger: str = Field(default='', max_length=1000)
    output: str = Field(default='', max_length=1000)
    inference_reason: str = Field(default='', max_length=1000)
    sources: list[CandidateSource] = Field(default_factory=list, max_length=4)
    steps: list[CandidateStep] = Field(min_length=1, max_length=16)

    @model_validator(mode='after')
    def check_items(self):
        keys = [item.key for item in self.steps]
        if not _safe_name(self.name) or len(keys) != len(set(keys)):
            raise ValueError('invalid candidate')
        for item in self.steps:
            if (not _safe_name(item.name) or item.next_key not in {'auto', 'end', *keys}
                    or item.next_key == item.key or item.else_key not in {'end', *keys}
                    or item.else_key == item.key):
                raise ValueError('invalid candidate step')
        return self


class CandidateBundle(Contract):
    schema: Literal['setup-business-candidates-v1'] = 'setup-business-candidates-v1'
    origin: Literal['local_summary', 'model'] = 'local_summary'
    material: CandidateMaterial
    candidates: list[CandidateBusiness] = Field(min_length=1, max_length=20)

    @model_validator(mode='after')
    def unique_items(self):
        names = [_name_key(item.name) for item in self.candidates]
        keys = [item.key for item in self.candidates]
        count = sum(len(item.sources) + sum(len(step.sources) for step in item.steps)
                    for item in self.candidates)
        if len(names) != len(set(names)) or len(keys) != len(set(keys)) or count > 200:
            raise ValueError('duplicate or excessive candidates')
        return self


class UnresolvedSource(Contract):
    material_id: str = Field(min_length=1, max_length=256)
    source_text: str = Field(min_length=1, max_length=20000)
    quote: str = Field(min_length=1, max_length=10000)
    reason: _REASONS


def validate_bundle(value):
    try:
        return CandidateBundle.model_validate(value)
    except (ValidationError, TypeError, ValueError):
        raise HTTPException(422, '业务候选格式无法核对，原草稿保留') from None


def validate_unresolved(value):
    if not isinstance(value, list) or len(value) > 4:
        raise HTTPException(422, '待核依据格式不完整，原草稿保留')
    try:
        return [UnresolvedSource.model_validate(item).model_dump() for item in value]
    except (ValidationError, TypeError, ValueError):
        raise HTTPException(422, '待核依据格式不完整，原草稿保留') from None


def material_snapshot(db, p, expected, *, check_pin=True):
    """Reload workspace material, verify actual original and optional pin."""
    row = scoped(db, SetupMaterial, expected.id, p, True)
    if row.archived:
        raise HTTPException(409, '资料已归档，请重新选择')
    ev, content = evidence_bytes(db, p, row.evidence_id)
    if ev.sha256 != row.sha256 or hashlib.sha256(content).hexdigest() != row.sha256:
        raise HTTPException(409, '资料原件无法核对，原草稿保留')
    engine = row.data.get('engine')
    if check_pin and (expected.version != row.version or expected.hash != row.sha256
                      or expected.engine != engine):
        raise HTTPException(409, '资料已更新，请重新核验候选')
    return {'id': row.id, 'version': row.version, 'hash': row.sha256,
            'engine': engine, 'status': row.status,
            'blocks': copy.deepcopy(row.data.get('blocks', []))}


def resolve_sources(material, sources):
    matched, unresolved = [], []
    for source in sources:
        result = resolve_material_citation(material=material,
            expected_file_hash=material['hash'], expected_material_version=material['version'],
            expected_engine=material['engine'], source_text=source.source_text, quote=source.quote)
        if result['status'] == 'matched':
            matched.append(result['citation'])
        else:
            unresolved.append({'material_id': material['id'], 'source_text': source.source_text,
                               'quote': source.quote, 'reason': result['reason']})
    return matched, unresolved


def preview_bundle(db, p, bundle):
    material = material_snapshot(db, p, bundle.material)
    candidates = []
    for item in bundle.candidates:
        value = item.model_dump()
        value['citations'], value['unresolved_sources'] = resolve_sources(material, item.sources)
        for source, step in zip(item.steps, value['steps']):
            step['citations'], step['unresolved_sources'] = resolve_sources(material, source.sources)
        candidates.append(value)
    return {'material': {key: material[key] for key in ('id', 'version', 'hash', 'engine', 'status')},
            'candidates': candidates}


def _items(flow):
    """Compare the business as a whole and its stable step identities."""
    yield ('business', None), flow
    for step in flow.get('steps', []):
        yield ('step', step.get('id')), step


def _candidate_basis(item, scope, name=''):
    fields = (('trigger', 'output', 'inferenceReason') if scope == 'business' else
              ('id', 'name', 'purpose', 'agent', 'role', 'output', 'sourceId',
               'citation', 'condition', 'nextId', 'elseId', 'inferenceReason'))
    return digest({'scope': scope, 'name': name if scope == 'business' else '',
                   'candidateKey': item.get('candidateKey'),
                   'values': {key: item.get(key, '') for key in fields},
                   'citations': item.get('citations', []),
                   'pending': item.get('candidateUnresolvedSources', [])})


def _verified_resolution(db, p, source, citations, snapshots):
    identity = source['material_id']
    if identity not in snapshots:
        # On a later recheck the parser version may legitimately have changed;
        # load the current authorized material, never the old displayed status.
        from .setup_api import material_view
        row = scoped(db, SetupMaterial, identity, p, True)
        if row.archived:
            raise HTTPException(409, '资料已归档，无法核验原文')
        ev, content = evidence_bytes(db, p, row.evidence_id)
        if ev.sha256 != row.sha256 or hashlib.sha256(content).hexdigest() != row.sha256:
            raise HTTPException(409, '资料原件无法核对，原草稿保留')
        snapshots[identity] = material_view(row, db, True)
    material = snapshots[identity]
    for ref in citations:
        if (not isinstance(ref, dict) or ref.get('material_id') != identity
                or not isinstance(ref.get('quote'), str) or not ref['quote'].strip()
                or ref['quote'] != source['quote']):
            continue
        result = resolve_material_citation(material=material, expected_file_hash=material['hash'],
            expected_material_version=material['version'], expected_engine=material['engine'],
            source_text=source['source_text'], quote=ref['quote'])
        if result.get('citation') == ref:
            return True
    return False


def protect_transitions(db, p, previous, proposed):
    """Keep unresolved sources until an exact, current-workspace recheck.

    A user may deliberately remove a whole business or step. For an item that
    remains, changing/removing a pending source requires a verified citation.
    """
    snapshots = {}
    old_flows = previous.get('flows', {})
    new_flows = proposed.get('flows', {})
    new_names = set(new_flows) - set(old_flows)
    aliases = proposed.get('businessAliases', {})
    candidate_keys = [flow['candidateKey'] for flow in new_flows.values()
                      if isinstance(flow, dict) and flow.get('candidateKey')]
    if len(candidate_keys) != len(set(candidate_keys)):
        raise HTTPException(409, '同一候选不能重复加入业务清单')
    for name, old_flow in old_flows.items():
        new_flow = new_flows.get(name)
        if new_flow is None and isinstance(old_flow, dict) and old_flow.get('candidateKey'):
            possible = [flow for flow in new_flows.values() if isinstance(flow, dict)
                        and flow.get('candidateKey') == old_flow['candidateKey']]
            if len(possible) == 1:
                new_flow = possible[0]
        if new_flow is None:
            possible = [new_flows[key] for key in new_names
                        if aliases.get(key) == name and isinstance(new_flows[key], dict)]
            if len(possible) == 1:
                new_flow = possible[0]
        old_protected = (isinstance(old_flow, dict) and any(
            item.get('candidateBasisHash') or item.get('candidateUnresolvedSources')
            for _, item in _items(old_flow)))
        if new_flow is None and old_protected and new_names:
            raise HTTPException(409, '候选业务请先单独删除，再新建其他业务')
        if not isinstance(old_flow, dict) or not isinstance(new_flow, dict):
            continue
        # IDs are needed to associate each protected source with exactly one
        # current step.  A duplicate must never let a dict silently choose one.
        if old_protected:
            steps = new_flow.get('steps', [])
            ids = [step.get('id') if isinstance(step, dict) else None for step in steps]
            if (any(not isinstance(identity, str) or not identity for identity in ids)
                    or len(ids) != len(set(ids))):
                raise HTTPException(409, '候选步骤无法唯一核对，原草稿保留')
        new_items = dict(_items(new_flow))
        old_items = dict(_items(old_flow))
        new_steps = {key for key in new_items if key[0] == 'step' and key not in old_items}
        for identity, old_item in _items(old_flow):
            if identity not in new_items:
                if (identity[0] == 'step' and (old_item.get('candidateBasisHash')
                        or old_item.get('candidateUnresolvedSources')) and new_steps):
                    raise HTTPException(409, '候选步骤请先单独删除，再新增其他步骤')
                continue
            new_item = new_items[identity]
            if 'candidateBasisHash' in old_item:
                if new_item.get('candidateKey') != old_item.get('candidateKey'):
                    raise HTTPException(409, '业务候选来源标记不能直接修改，原草稿保留')
                if 'candidateBasisHash' not in new_item:
                    # A just-saved browser draft can predate the server's
                    # protected marker, including an edit made in flight.
                    # Restore only the locked, authoritative marker; never
                    # overwrite the user's actual content or pending sources.
                    new_item['candidateBasisHash'] = old_item['candidateBasisHash']
                elif new_item['candidateBasisHash'] != old_item['candidateBasisHash']:
                    raise HTTPException(409, '业务候选来源标记不能直接修改，原草稿保留')
            old_sources = validate_unresolved(old_item.get('candidateUnresolvedSources', []))
            new_sources = validate_unresolved(new_item.get('candidateUnresolvedSources', []))
            for source in old_sources:
                if source not in new_sources and not _verified_resolution(
                        db, p, source, new_item.get('citations', []), snapshots):
                    raise HTTPException(409, '待核依据尚未重新核验，原草稿保留')

    # A newly imported candidate must preserve every source returned by a
    # fresh server-side preview, not arbitrary client-provided verification.
    bundle_value = proposed.get('businessCandidates')
    if bundle_value is not None:
        bundle = validate_bundle(bundle_value)
        candidate_names = {item.name: item.key for item in bundle.candidates}
        for name, flow in new_flows.items():
            if name not in old_flows and name in candidate_names and (
                    not isinstance(flow, dict) or flow.get('candidateKey') != candidate_names[name]):
                raise HTTPException(409, '请重新核验并加入候选业务，原草稿保留')
    old_candidate_keys = {flow.get('candidateKey') for flow in old_flows.values()
                          if isinstance(flow, dict) and flow.get('candidateKey')}
    imported = [(name, flow) for name, flow in new_flows.items()
                if isinstance(flow, dict) and flow.get('candidateKey')
                and flow.get('candidateKey') not in old_candidate_keys]
    if not imported:
        return
    bundle = validate_bundle(bundle_value)
    preview = preview_bundle(db, p, bundle)
    choices = {item['key']: item for item in preview['candidates']}
    old_names = set()
    for item in previous.get('businesses', []):
        name = item if isinstance(item, str) else item.get('name') if isinstance(item, dict) else None
        if not isinstance(name, str):
            raise HTTPException(409, '现有业务清单无法核对，原草稿保留')
        old_names.add(_name_key(name))
    old_names.update(_name_key(name) for name in old_flows)
    for name, flow in imported:
        candidate = choices.get(flow.get('candidateKey'))
        if (not candidate or name != candidate['name'] or _name_key(name) in old_names
                or flow.get('confirmedAt') or flow.get('confirmedSignature')
                or flow.get('trigger') != candidate['trigger'] or flow.get('output') != candidate['output']):
            raise HTTPException(409, '候选业务已变化或与现有业务重名，原草稿保留')
        if (flow.get('inferenceReason', '') != candidate['inference_reason']
                or flow.get('citations', []) != candidate['citations']
                or flow.get('candidateUnresolvedSources', []) != candidate['unresolved_sources']):
            raise HTTPException(409, '候选业务依据不完整，请重新核验')
        steps = flow.get('steps', [])
        if len(steps) != len(candidate['steps']):
            raise HTTPException(409, '候选步骤已变化，请重新核验')
        by_key = {step.get('candidateKey'): step for step in steps}
        ids = {key: step.get('id') for key, step in by_key.items()}
        if (len(by_key) != len(steps) or set(by_key) != {step['key'] for step in candidate['steps']}
                or any(not isinstance(value, str) or not value for value in ids.values())
                or len(set(ids.values())) != len(ids)):
            raise HTTPException(409, '候选步骤无法核对，请重新核验')
        for step in candidate['steps']:
            saved = by_key[step['key']]
            fields = ('name', 'purpose', 'agent', 'role', 'output', 'condition')
            if (any(saved.get(field, '') != step[field] for field in fields)
                    or saved.get('inferenceReason', '') != step['inference_reason']
                    or saved.get('nextId', 'auto') != ids.get(step['next_key'], step['next_key'])
                    or saved.get('elseId', 'end') != ids.get(step['else_key'], step['else_key'])
                    or saved.get('citations', []) != step['citations']
                    or saved.get('candidateUnresolvedSources', []) != step['unresolved_sources']):
                raise HTTPException(409, '候选步骤或依据已变化，请重新核验')
        # The client never supplies the trusted baseline. Keep it when users
        # later edit: a differing basis prevents automatic source replacement.
        flow['candidateBasisHash'] = _candidate_basis(flow, 'business', name)
        for saved in steps:
            saved['candidateBasisHash'] = _candidate_basis(saved, 'step')


def protect_recovery(previous, proposed):
    """Do not silently discard a candidate or its pending basis in history restore."""
    if previous.get('businessCandidates') != proposed.get('businessCandidates'):
        raise HTTPException(409, '历史版本的业务候选不同，请先处理当前候选，原草稿保留')
    new_flows = proposed.get('flows', {})
    for name, old_flow in previous.get('flows', {}).items():
        if not isinstance(old_flow, dict):
            continue
        current = dict(_items(old_flow))
        protected = {identity: item for identity, item in current.items()
                     if item.get('candidateBasisHash') or item.get('candidateUnresolvedSources')}
        if not protected:
            continue
        new_flow = new_flows.get(name)
        if not isinstance(new_flow, dict):
            raise HTTPException(409, '历史版本会移除候选来源，请先处理该业务，原草稿保留')
        new_items = dict(_items(new_flow))
        for identity, old_item in protected.items():
            item = new_items.get(identity)
            if (not isinstance(item, dict)
                    or item.get('candidateBasisHash') != old_item.get('candidateBasisHash')
                    or item.get('candidateUnresolvedSources', []) != old_item.get('candidateUnresolvedSources', [])):
                raise HTTPException(409, '历史版本会改变候选待核依据，请先处理该业务，原草稿保留')


router = APIRouter()
P = Annotated[Principal, Depends(authenticated)]
DB = Annotated[object, Depends(session)]


@router.post('/candidates/preview')
def candidate_preview(body: CandidateBundle, p: P, db: DB):
    return preview_bundle(db, p, body)


class AppendCandidates(Contract):
    revision: int = Field(strict=True, ge=1)
    bundle: CandidateBundle


def _save_candidates(db, p, row, value, action):
    from sqlalchemy.dialects.postgresql import insert
    from .models import SetupRevision
    from .setup_api import draft_view, validate_configuration

    validate_configuration(value)
    db.execute(insert(SetupRevision).values(id=uid(),
        workspace_id=p.workspace_id, draft_id=row.id, version=row.version, data=row.data)
        .on_conflict_do_nothing(index_elements=['draft_id', 'version']))
    row.data = value
    row.version += 1
    db.add(SetupRevision(workspace_id=p.workspace_id, draft_id=row.id, version=row.version, data=row.data))
    audit(db, p, action, row.id, {'revision': row.version})
    db.commit()
    return draft_view(row)


@router.post('/candidates')
def append_candidates(body: AppendCandidates, p: P, db: DB):
    """Append only to the existing versioned draft, without editing materials."""
    from .setup_api import draft, initial_configuration

    require(p, 'admin', 'operator')
    row = draft(db, p, True)
    check_version(row, body.revision)
    if row.data.get('businessCandidates') is not None:
        raise HTTPException(409, '已有业务候选，请先处理当前候选')
    material = material_snapshot(db, p, body.bundle.material)
    bundle = body.bundle.model_dump()
    bundle['material']['status'] = material['status']
    value = copy.deepcopy(row.data) if row.data else initial_configuration()
    value['businessCandidates'] = bundle
    return _save_candidates(db, p, row, value, 'setup.candidates.add')


class RevalidateCandidates(Contract):
    revision: int = Field(strict=True, ge=1)


def _refresh_candidate_basis(item, candidate, scope, name=''):
    if not item.get('candidateBasisHash') or item['candidateBasisHash'] != _candidate_basis(item, scope, name):
        return False, bool(candidate.get('sources'))
    citations, pending = candidate['citations'], candidate['unresolved_sources']
    changed = item.get('citations', []) != citations or item.get('candidateUnresolvedSources', []) != pending
    if changed:
        item['citations'] = copy.deepcopy(citations)
        item['candidateUnresolvedSources'] = copy.deepcopy(pending)
        item['candidateBasisHash'] = _candidate_basis(item, scope, name)
    return changed, False


def _resolve_unchanged_flows(value, preview, material):
    choices = {candidate['key']: candidate for candidate in preview['candidates']}
    skipped = 0
    for name, flow in value.get('flows', {}).items():
        candidate = choices.get(flow.get('candidateKey')) if isinstance(flow, dict) else None
        if not candidate:
            continue
        if name != candidate['name'] or 'reportSource' in flow or 'fullSemanticSource' in flow:
            skipped += 1
            continue
        changed, missed = _refresh_candidate_basis(flow, candidate, 'business', name)
        skipped += int(missed)
        steps = flow.get('steps', [])
        by_key = {step.get('candidateKey'): step for step in steps}
        if len(by_key) != len(steps):
            skipped += 1
            if changed:
                flow['confirmedAt'] = None
                flow['confirmedSignature'] = None
                flow.pop('candidateContentApproval', None)
                flow.pop('candidateContentReview', None)
            continue
        for source in candidate['steps']:
            saved = by_key.get(source['key'])
            if not saved:
                continue
            item_changed, missed = _refresh_candidate_basis(saved, source, 'step')
            changed = item_changed or changed
            skipped += int(missed)
        if changed:
            flow['confirmedAt'] = None
            flow['confirmedSignature'] = None
            flow.pop('candidateContentApproval', None)
            flow.pop('candidateContentReview', None)
    return {'skipped': skipped}


def _clear_stale_material_confirmations(previous, value, material, bundle):
    """Invalidate only saved approvals that still point at the older parse."""
    if material['version'] == bundle.material.version and material['engine'] == bundle.material.engine:
        return
    from .flow_sources import flow_source_ids
    prior = next((item for item in previous.get('materials', [])
                  if isinstance(item, dict) and item.get('id') == material['id']), None)
    if (prior and prior.get('version') == material['version']
            and material['engine'] == bundle.material.engine):
        return
    for flow in value.get('flows', {}).values():
        if isinstance(flow, dict) and material['id'] in flow_source_ids(flow):
            flow['confirmedAt'] = None
            flow['confirmedSignature'] = None
            flow.pop('candidateContentApproval', None)
            flow.pop('candidateContentReview', None)


@router.post('/candidates/revalidate')
def revalidate_candidates(body: RevalidateCandidates, p: P, db: DB):
    """Rebind only to a new parse of the same original, leaving flows alone."""
    from .setup_api import draft, draft_view

    require(p, 'admin', 'operator')
    row = draft(db, p, True)
    check_version(row, body.revision)
    bundle = validate_bundle(row.data.get('businessCandidates'))
    material = material_snapshot(db, p, bundle.material, check_pin=False)
    if material['hash'] != bundle.material.hash:
        raise HTTPException(409, '资料原件已变化，请重新整理候选')
    value = copy.deepcopy(row.data)
    value['businessCandidates']['material'] = {key: material[key] for key in
                                                ('id', 'version', 'hash', 'engine', 'status')}
    updated = validate_bundle(value['businessCandidates'])
    preview = preview_bundle(db, p, updated)
    recheck = _resolve_unchanged_flows(value, preview, material)
    # A changed material version affects approvals only when the flow actually
    # references it and that flow's saved material snapshot is still stale.
    # Do not disturb unrelated flows or approvals based on the current version.
    _clear_stale_material_confirmations(row.data, value, material, bundle)
    if value.get('flows', {}) != row.data.get('flows', {}):
        from .report_configuration_handoff import protect_configuration_sources
        try:
            guarded = protect_configuration_sources(db, p, row.data, value)
        except HTTPException:
            raise HTTPException(409, '已有业务来源需要先核对，原草稿保留') from None
        if guarded != value:
            raise HTTPException(409, '已有业务来源需要先核对，原草稿保留')
    if value == row.data:
        db.commit()
        return {'draft': draft_view(row), 'preview': preview, 'recheck': recheck}
    result = _save_candidates(db, p, row, value, 'setup.candidates.revalidate')
    return {'draft': result, 'preview': preview, 'recheck': recheck}
