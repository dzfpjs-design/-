"""Explicit durable source selection for the existing original-report V166 flow.

This selects an independently completed design as a private draft BASIS. It
does not save/confirm/adopt a draft, alter the failed producer or consumer, grant
cloud execution, or create a parallel editing family. Original wire commands
and independently anchored history remain queryable after source changes.
"""
import copy
import hashlib
import json
import re
from functools import wraps
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import inspect, or_, select
from sqlalchemy.exc import IntegrityError

from .contracts import digest
from .db import session
from .models import Audit, AuthSession, CloudCall, Membership, SetupReportDraft, User, now, uid
from .relations import lock_current_actor
from .security import Principal, authenticated, require
from .services import audit
from .typed_payload_codec_v131 import canonical_json
from . import report_object_revision_store_v166 as revisions
from .report_object_revision_models_v166 import ReportObjectRevision as Revision
from .report_object_revision_models_v166 import ReportObjectRevisionCommand as RevisionCommand
from .report_saved_map_review_models_v178 import ReportSavedMapReviewSourceV178 as Head

PREFIX = '/api/setup/report-saved-map-designs'
VIEW = 'report-saved-map-review-source-v178'
RESULT = 'report-saved-map-review-source-result-v178'
REQUEST = 'report-saved-map-review-source-request-v178'
STATE = 'report-saved-map-review-source-state-v178'
COMMAND = 'report-saved-map-review-source-command-v178'
PROOF = 'report-saved-map-review-source-proof-v178'
AUDIT = 'report-saved-map-review-source-audit-v178'
ACTION = 'report.saved_map_review_source.selected_v178'
SOURCE_KIND = 'saved-map-design-v178'
MAX_HISTORY = 1000
MAX_WIRE_BYTES = 8192
ERROR = '人工稿选源、原请求或独立审计库存无法核对；原稿、原候选与原报告保留'
_SHA = re.compile(r'^[a-f0-9]{64}$')
router = APIRouter(prefix=PREFIX)
P = Annotated[Principal, Depends(authenticated)]
DB = Annotated[object, Depends(session)]


def need(ok, detail=ERROR, code=409):
    if not ok: raise HTTPException(code, detail)


def _integrity(function):
    @wraps(function)
    def checked(*args, **kwargs):
        try: return function(*args, **kwargs)
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
            raise HTTPException(409, ERROR) from None
    return checked


def same(left, right): return canonical_json(left) == canonical_json(right)


def identity(value):
    if type(value) is not str or str(UUID(value)) != value: raise ValueError(ERROR)
    return value


def path(consumer_id): return PREFIX + '/' + identity(consumer_id) + '/review-source'


def _pairs(items):
    value = {}
    for key, item in items:
        if key in value: raise ValueError(ERROR)
        value[key] = item
    return value


def parse_wire(raw):
    """Exact three fields, UUID nonce and typed integer CAS; never fill defaults."""
    try:
        if type(raw) is not bytes or not 0 < len(raw) <= MAX_WIRE_BYTES: raise ValueError(ERROR)
        def constant(_): raise ValueError(ERROR)
        body = json.loads(raw.decode('utf-8', errors='strict'), object_pairs_hook=_pairs,
            parse_constant=constant)
        canonical_json(body)
        if (type(body) is not dict or set(body) != {'request_id', 'expected_version', 'consumer_version'}
                or type(body['expected_version']) is not int or body['expected_version'] < 0
                or type(body['consumer_version']) is not int or body['consumer_version'] < 1):
            raise ValueError(ERROR)
        identity(body['request_id'])
        return body
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
        raise ValueError(ERROR) from None


def _consumer(db, p, consumer_id):
    # Lazy to avoid introducing a consumer -> source -> consumer import cycle.
    from .report_saved_map_design_consumer_v176 import checked_consumer
    return checked_consumer(db, p, identity(consumer_id), lock=False)


def _storage_available(db):
    """Explicit deployment capability check; DB errors are never absence.

    A lazy import in an older live process must not SELECT a not-yet-migrated
    table. Only the connection's real schema inspector decides availability.
    """
    key = 'report_saved_map_review_storage_v178'
    if key not in db.info:
        db.info[key] = inspect(db.connection()).has_table(Head.__tablename__)
    need(type(db.info[key]) is bool)
    return db.info[key]


def _require_storage(db):
    need(_storage_available(db), '人工稿选源存储尚未升级；请由管理员完成数据库升级后再选择', 503)


def _audit_trace_without_storage(db, p):
    # A new-family Audit without its deployed table is not a legacy empty
    # inventory. Even another own report's trace must not be silently hidden.
    return db.scalar(select(Audit.id).where(Audit.workspace_id == p.workspace_id,
        Audit.action == ACTION, or_(Audit.actor_id == p.user_id,
            Audit.data['actor_id'].as_string() == p.user_id)).limit(1)) is not None


def _iam(db, p, *, write=False):
    if write:
        require(p, 'admin', 'operator')
    query = select(User, Membership).join(Membership, Membership.user_id == User.id).where(
        User.id == p.user_id, User.active.is_(True), Membership.workspace_id == p.workspace_id,
        Membership.role == p.role)
    session_id = getattr(p, 'session_id', None)
    if session_id is not None:
        try: identity(session_id)
        except ValueError: raise HTTPException(403, '当前登录会话无法核对') from None
        query = query.add_columns(AuthSession).join(AuthSession, AuthSession.user_id == User.id).where(
            AuthSession.id == session_id, AuthSession.revoked.is_(False), AuthSession.expires_at > now())
    need(db.execute(query).first() is not None, '当前账号、工作区权限或登录会话已变化', 403)
    if write: lock_current_actor(db, p)


def _owner(db, p, report_id, *, lock=False):
    query = select(SetupReportDraft).where(SetupReportDraft.id == identity(report_id),
        SetupReportDraft.workspace_id == p.workspace_id, SetupReportDraft.actor_id == p.user_id)
    if lock: query = query.with_for_update(read=True)
    row = db.scalar(query.execution_options(populate_existing=True))
    need(row is not None, '原报告不存在或不属于当前账号', 404)
    return row


def _snapshot(head, history=None):
    history = copy.deepcopy(head.data['history'] if history is None else history)
    return {'id': head.id, 'workspace_id': head.workspace_id, 'report_id': head.report_id,
        'actor_id': head.actor_id, 'consumer_id': history[-1]['selected_proof']['consumer_id'],
        'version': len(history), 'data': {'schema': STATE, 'history': history}}


def _command_hash(command): return digest({key: value for key, value in command.items() if key != 'command_hash'})


def _audit_value(head, command, state_hash):
    return {'schema': AUDIT, 'selection_id': head.id, 'report_id': head.report_id,
        'actor_id': head.actor_id, 'consumer_id': command['selected_proof']['consumer_id'],
        'version': command['expected_version'] + 1, 'request_id': command['request_id'],
        'body_sha256': command['body_sha256'], 'command_hash': command['command_hash'],
        'selection_hash': command['selection_hash'], 'state_hash': state_hash,
        'previous_state_hash': command['previous_state_hash'], 'path': command['path']}


def _proof_shape(proof, head):
    keys = set(('schema selection_id workspace_id actor_id report_id consumer_id generation_profile '
        'call_pin response_body_sha256 projection_hash manual_hash source_snapshot_hash '
        'model_result_hash source_hash catalog_hash context_hash').split())
    need(type(proof) is dict and set(proof) == keys and proof['schema'] == PROOF
        and proof['selection_id'] == head.id and proof['workspace_id'] == head.workspace_id
        and proof['actor_id'] == head.actor_id and proof['report_id'] == head.report_id
        and proof['generation_profile'] in {'saved-v176', 'indexed-v178', 'indexed-v179'}
        and type(proof['call_pin']) is dict)
    identity(proof['consumer_id'])
    for key in ('response_body_sha256', 'projection_hash', 'manual_hash', 'source_snapshot_hash',
                'model_result_hash', 'source_hash', 'catalog_hash', 'context_hash'):
        need(type(proof[key]) is str and _SHA.fullmatch(proof[key]) is not None)
    canonical_json(proof['call_pin'])


@_integrity
def _check_head(head, events):
    need(type(head.version) is int and 1 <= head.version <= MAX_HISTORY
        and type(head.data) is dict and set(head.data) == {'schema', 'history'}
        and head.data['schema'] == STATE and type(head.data['history']) is list
        and len(head.data['history']) == head.version and len(events) == head.version)
    for value in (head.id, head.workspace_id, head.report_id, head.actor_id, head.consumer_id): identity(value)
    history, previous, nonces = [], None, set()
    by_version = {}
    for event in events:
        need(event.workspace_id == head.workspace_id and event.actor_id == head.actor_id
            and event.action == ACTION and event.object_id == head.id and type(event.data) is dict
            and type(event.data.get('version')) is int and event.data['version'] not in by_version)
        by_version[event.data['version']] = event
    need(set(by_version) == set(range(1, head.version + 1)))
    for version, command in enumerate(head.data['history'], 1):
        need(type(command) is dict and set(command) == set(('schema request_id expected_version '
            'consumer_version method path exact_body body_sha256 body_hash selected_proof '
            'selection_hash result previous_state_hash command_hash').split())
            and command['schema'] == COMMAND and command['method'] == 'POST'
            and type(command['expected_version']) is int and command['expected_version'] == version - 1
            and type(command['consumer_version']) is int and command['consumer_version'] >= 1)
        _proof_shape(command['selected_proof'], head)
        proof = command['selected_proof']; identity(command['request_id'])
        need(command['request_id'] not in nonces and command['path'] == path(proof['consumer_id'])
            and command['previous_state_hash'] == previous and command['selection_hash'] == digest(proof)
            and type(command['exact_body']) is str)
        nonces.add(command['request_id'])
        raw = command['exact_body'].encode('utf-8', errors='strict'); body = parse_wire(raw)
        need(same(body, {key: command[key] for key in ('request_id', 'expected_version', 'consumer_version')})
            and command['body_sha256'] == hashlib.sha256(raw).hexdigest()
            and command['body_hash'] == digest(body) and command['command_hash'] == _command_hash(command))
        expected = {'schema': RESULT, 'request_id': command['request_id'],
            'consumer_id': proof['consumer_id'], 'report_id': head.report_id, 'selection_id': head.id,
            'version': version, 'selection_hash': digest(proof), 'selected': True}
        need(same(command['result'], expected))
        history.append(copy.deepcopy(command)); current = digest(_snapshot(head, history))
        need(same(by_version[version].data, _audit_value(head, command, current)))
        previous = current
    need(head.consumer_id == history[-1]['selected_proof']['consumer_id']
        and head.version == len(history) and head.record_hash == previous)
    return head


@_integrity
def _inventory(db, p, target_report_id, *, write=False):
    """Actor mutex precedes sorted report locks; both head/Audit directions.

    Shared advisory locks serialize with source selection and V166 writers.
    Historical commands are checked as saved, not regenerated from current head.
    """
    revisions._actor_lock(db, p, shared=not write)
    heads = list(db.scalars(select(Head).where(Head.workspace_id == p.workspace_id,
        Head.actor_id == p.user_id).execution_options(populate_existing=True)))
    events = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == ACTION, or_(Audit.actor_id == p.user_id,
            Audit.data['actor_id'].as_string() == p.user_id))))
    need(len(heads) <= MAX_HISTORY and len(events) <= MAX_HISTORY * MAX_HISTORY)
    reports = {identity(target_report_id)}
    reports.update(identity(head.report_id) for head in heads)
    reports.update(identity(event.data['report_id']) for event in events)
    for report_id in sorted(reports): revisions._report_lock(db, p, report_id, shared=not write)
    need(len({head.id for head in heads}) == len(heads)
        and len({head.report_id for head in heads}) == len(heads))
    known = {head.id: head for head in heads}
    need(all(event.object_id in known for event in events))
    nonces = set()
    for head in heads:
        need(head.actor_id == p.user_id and head.workspace_id == p.workspace_id)
        _check_head(head, [event for event in events if event.object_id == head.id])
        for command in head.data['history']:
            need(command['request_id'] not in nonces); nonces.add(command['request_id'])
    return {head.report_id: head for head in heads}


def _revision(db, p, report_id):
    # This is the real three-sided V166 inventory, including orphan commands
    # and Audit. Its basis union is integrated separately by Root.
    row, _ = revisions._inventory(db, p, report_id)
    return row


@_integrity
def _new_basis_trace(db, p, report_id):
    rows = list(db.scalars(select(Revision).where(Revision.workspace_id == p.workspace_id,
        or_(Revision.report_id == report_id, Revision.data['basis']['report_id'].as_string() == report_id),
        Revision.actor_id == p.user_id)))
    commands = list(db.scalars(select(RevisionCommand).where(RevisionCommand.workspace_id == p.workspace_id,
        or_(RevisionCommand.report_id == report_id,
            RevisionCommand.data['record_snapshot']['report_id'].as_string() == report_id,
            RevisionCommand.data['record_snapshot']['data']['basis']['report_id'].as_string() == report_id),
        RevisionCommand.actor_id == p.user_id)))
    for row in rows:
        if row.data.get('basis', {}).get('source_kind') == SOURCE_KIND: return True
    for row in commands:
        if row.data.get('record_snapshot', {}).get('data', {}).get('basis', {}).get('source_kind') == SOURCE_KIND:
            return True
    return False


@_integrity
def _fresh_source(db, p, consumer_id, selection_id, *, lock=False):
    """Recheck actual raw answer, complete candidate, material and original report."""
    from . import report_saved_map_design_consumer_v176 as consumer
    from . import report_design_profiles_v178 as profiles
    from . import report_evidence_pipeline as pipeline
    from .report_full_semantic_handoff import material_refs
    from .report_object_semantics_v166 import validate_value
    row = _consumer(db, p, consumer_id)
    need(row.status in {'review', 'needs_clarification'} and row.reserved_call_id is not None
        and type(row.data.get('projection')) is dict and type(row.data.get('manual_value')) is dict,
        '本候选未完整通过核对，不能初始化人工稿；原回答保留')
    report = _owner(db, p, row.producer_id, lock=lock)
    pipeline.check_row(report, p)
    source = row.data['preview']['source_context']; frozen = source['snapshot']
    need(frozen['producer']['id'] == report.id and frozen['actor_id'] == p.user_id
        and frozen['workspace_id'] == p.workspace_id)
    materials = material_refs(db, p, report, lock=lock)
    catalog = pipeline._catalog(report.data['preview'])
    context = copy.deepcopy(report.data['preview']['map_preparation']['context'])
    need(same(materials, frozen['materials']) and same(catalog, source['catalog'])
        and same(catalog['descriptor'], frozen['catalog_descriptor'])
        and same(context, source['context']) and same(context, frozen['context'])
        and report.data['preview']['source_hash'] == frozen['source_hash'])
    family = profiles.modules(profiles.profile_id(row.data['preview']['preparation']))
    saved = consumer.runtime().read_response(db, p, row, recover_saved=False)
    need(type(saved) is dict and type(saved.get('body')) is bytes)
    inspected = family.native.inspect_response(saved['body'], saved['capture']['http_status'],
        family.native.STAGE, response_complete=saved['capture']['response_complete'],
        http_content_encoding=saved['capture']['http_content_encoding'])
    need(inspected['metadata']['envelope_valid'] is True)
    arguments = inspected['arguments'].encode('utf-8', errors='strict')
    expected_response = {key: copy.deepcopy(saved[key]) for key in ('receipt', 'capture', 'descriptor', 'binding')}
    expected_response.update(body_sha256=hashlib.sha256(saved['body']).hexdigest(),
        arguments_sha256=hashlib.sha256(arguments).hexdigest())
    need(same(expected_response, row.data['response']))
    extra = ({'indexed': row.data['preview']['preparation']['indexed_inputs'],
        'source_snapshot_hash': row.data['preview']['preparation']['source_snapshot_hash']}
        if family.id in {profiles.INDEXED, profiles.COMPATIBLE} else {})
    projection = family.wire.check_reduction_projection(row.data['projection'], arguments,
        catalog=catalog, allowed_span_refs=source['allowed_span_refs'], context=context,
        input_order=frozen['input_order'], input_items=frozen['input_items'], **extra)
    manual = validate_value(copy.deepcopy(row.data['manual_value']), catalog, source['allowed_span_refs'], context)
    need(same(manual, row.data['manual_value']) and same(manual['result'], projection['result']))
    call = db.scalar(select(CloudCall).where(CloudCall.workspace_id == p.workspace_id,
        CloudCall.id == row.reserved_call_id).execution_options(populate_existing=True))
    need(call is not None and call.status == 'succeeded')
    consumer._check_call(row, call)
    proof = {'schema': PROOF, 'selection_id': identity(selection_id), 'workspace_id': p.workspace_id,
        'actor_id': p.user_id, 'report_id': report.id, 'consumer_id': row.id, 'generation_profile': family.id,
        'call_pin': copy.deepcopy(row.data['call_pin']),
        'response_body_sha256': expected_response['body_sha256'],
        'projection_hash': projection['projection_hash'], 'manual_hash': digest(manual),
        'source_snapshot_hash': frozen['snapshot_hash'], 'model_result_hash': digest(projection['result']),
        'source_hash': frozen['source_hash'], 'catalog_hash': catalog['descriptor']['catalog_hash'],
        'context_hash': digest(context)}
    basis = {'report_id': report.id, 'report_revision': report.version,
        'model_result_hash': proof['model_result_hash'], 'source_hash': proof['source_hash'],
        'catalog_hash': proof['catalog_hash'], 'context_hash': proof['context_hash'],
        'base_value_hash': digest(manual), 'source_kind': SOURCE_KIND, 'consumer_id': row.id,
        'selection_id': selection_id, 'selection_hash': digest(proof)}
    return row, proof, {'basis': basis, 'basis_hash': digest(basis), 'value': copy.deepcopy(manual),
        'catalog': catalog, 'allowed_span_refs': copy.deepcopy(source['allowed_span_refs']), 'context': context}


def _lookup(heads, request_id):
    matches = [(head, command) for head in heads.values() for command in head.data['history']
        if command['request_id'] == request_id]
    need(len(matches) <= 1)
    return matches[0] if matches else (None, None)


@_integrity
def detail(db, p, consumer_id):
    _iam(db, p)
    _require_storage(db)
    row = _consumer(db, p, consumer_id); _owner(db, p, row.producer_id)
    heads = _inventory(db, p, row.producer_id); head = heads.get(row.producer_id)
    if head is None: need(not _new_basis_trace(db, p, row.producer_id))
    current = head.data['history'][-1] if head is not None else None
    can_select = False
    if p.role in {'admin', 'operator'}:
        try:
            _, proof, _ = _fresh_source(db, p, row.id, head.id if head is not None else uid())
            revision = _revision(db, p, row.producer_id)
            can_select = revision is None or current is not None and head.consumer_id == row.id and same(
                current['selected_proof'], proof)
        except HTTPException as error:
            if error.status_code not in {404, 409, 422}: raise
    return {'schema': VIEW, 'selected': head is not None and head.consumer_id == row.id,
        'consumer_id': head.consumer_id if head is not None else None, 'report_id': row.producer_id,
        'version': head.version if head is not None else 0,
        'selection_hash': current['selection_hash'] if current is not None else None, 'can_select': bool(can_select)}


@_integrity
def select_source(db, p, consumer_id, raw, *, actual_path):
    """Caller commits one head+Audit transaction; no source/consumer is mutated."""
    body = parse_wire(raw); need(actual_path == path(consumer_id))
    _iam(db, p, write=True)
    _require_storage(db)
    revisions._lock(db, {'saved_map_review_source_nonce_v178': body['request_id'],
        'workspace_id': p.workspace_id, 'actor_id': p.user_id})
    row = _consumer(db, p, consumer_id)
    heads = _inventory(db, p, row.producer_id, write=True)
    _owner(db, p, row.producer_id)
    previous_head, previous_command = _lookup(heads, body['request_id'])
    sha = hashlib.sha256(raw).hexdigest(); exact = raw.decode('utf-8', errors='strict')
    if previous_command is not None:
        need(previous_command['path'] == actual_path and previous_command['exact_body'] == exact
            and previous_command['body_sha256'] == sha)
        return copy.deepcopy(previous_command['result'])
    head = heads.get(row.producer_id)
    if head is None: need(not _new_basis_trace(db, p, row.producer_id))
    need(body['expected_version'] == (head.version if head is not None else 0),
        '选源版本已变化；保留原输入，请查询原请求或刷新后明确选择')
    selection_id = head.id if head is not None else uid()
    current, proof, _ = _fresh_source(db, p, row.id, selection_id, lock=True)
    need(current.version == body['consumer_version'], '候选版本已变化，请刷新后核对本次选择')
    revision = _revision(db, p, row.producer_id)
    if revision is not None:
        need(head is not None and head.consumer_id == current.id
            and same(head.data['history'][-1]['selected_proof'], proof),
            '本报告已有保存的人工稿，不能更换候选来源；原稿和历史保持不变')
    if head is None:
        head = Head(id=selection_id, workspace_id=p.workspace_id, report_id=row.producer_id,
            actor_id=p.user_id, consumer_id=current.id, version=1, record_hash='',
            data={'schema': STATE, 'history': []})
        previous, history = None, []
        db.add(head)
    else:
        previous, history = head.record_hash, copy.deepcopy(head.data['history'])
    need(len(history) < MAX_HISTORY, '选源历史已达完整保存上限；未截断历史或覆盖原稿')
    result = {'schema': RESULT, 'request_id': body['request_id'], 'consumer_id': current.id,
        'report_id': row.producer_id, 'selection_id': head.id, 'version': len(history) + 1,
        'selection_hash': digest(proof), 'selected': True}
    command = {'schema': COMMAND, **copy.deepcopy(body), 'method': 'POST', 'path': actual_path,
        'exact_body': exact, 'body_sha256': sha, 'body_hash': digest(body), 'selected_proof': proof,
        'selection_hash': digest(proof), 'result': copy.deepcopy(result), 'previous_state_hash': previous}
    command['command_hash'] = _command_hash(command)
    history.append(command); head.version = len(history); head.consumer_id = current.id
    head.data = {'schema': STATE, 'history': history}; head.record_hash = digest(_snapshot(head))
    audit(db, p, ACTION, head.id, _audit_value(head, command, head.record_hash))
    db.flush()
    return result


@_integrity
def request_result(db, p, consumer_id, request_id, expected_sha256):
    _iam(db, p); identity(request_id)
    _require_storage(db)
    need(type(expected_sha256) is str and _SHA.fullmatch(expected_sha256) is not None)
    row = _consumer(db, p, consumer_id); _owner(db, p, row.producer_id)
    revisions._lock(db, {'saved_map_review_source_nonce_v178': request_id,
        'workspace_id': p.workspace_id, 'actor_id': p.user_id}, shared=True)
    heads = _inventory(db, p, row.producer_id)
    if row.producer_id not in heads: need(not _new_basis_trace(db, p, row.producer_id))
    _, command = _lookup(heads, request_id)
    if command is not None:
        need(command['path'] == path(consumer_id) and command['body_sha256'] == expected_sha256)
    return {'schema': REQUEST, 'found': command is not None, 'request_id': request_id,
        'body_sha256': command['body_sha256'] if command is not None else expected_sha256,
        'result': copy.deepcopy(command['result']) if command is not None else None}


@_integrity
def load_selected_source(db, p, report_id, lock=False):
    """Six-key V166 loader union; no trace means legacy, never damaged fallback."""
    need(type(lock) is bool)
    _iam(db, p); _owner(db, p, report_id)
    if not _storage_available(db):
        revisions._actor_lock(db, p, shared=True)
        revisions._report_lock(db, p, report_id, shared=True)
        need(not _audit_trace_without_storage(db, p) and not _new_basis_trace(db, p, report_id))
        return None
    # This loader never mutates a selection. Adoption already holds V166
    # actor/report SHARE locks; upgrading them here would deadlock concurrent
    # adopters. lock=True freezes source/material rows with SHARE only below.
    heads = _inventory(db, p, report_id, write=False); head = heads.get(report_id)
    if head is None:
        need(not _new_basis_trace(db, p, report_id))
        return None
    _, proof, source = _fresh_source(db, p, head.consumer_id, head.id, lock=lock)
    need(same(proof, head.data['history'][-1]['selected_proof'])
        and source['basis']['selection_hash'] == head.data['history'][-1]['selection_hash'])
    return source


@router.get('/{consumer_id}/review-source')
def view_route(consumer_id: str, p: P, db: DB):
    try: identity(consumer_id)
    except (ValueError, TypeError, AttributeError): raise HTTPException(422, '请保留原候选编号') from None
    return detail(db, p, consumer_id)


@router.post('/{consumer_id}/review-source')
async def select_route(consumer_id: str, request: Request, p: P, db: DB):
    need(request.method == 'POST' and request.url.path == path(consumer_id), code=422)
    raw = await request.body()
    try: parse_wire(raw)
    except ValueError: raise HTTPException(422, '请保留原请求编号、选源版本和候选版本') from None
    try:
        result = select_source(db, p, consumer_id, raw, actual_path=request.url.path)
        db.commit(); return result
    except IntegrityError:
        db.rollback(); raise HTTPException(409, '选源并发冲突；请查询原请求，未覆盖原稿') from None


@router.get('/{consumer_id}/review-source/requests/{request_id}')
def request_route(consumer_id: str, request_id: str, p: P, db: DB,
                  expected_sha256: str = Query(pattern=r'^[a-f0-9]{64}$')):
    try: identity(consumer_id); identity(request_id)
    except (ValueError, TypeError, AttributeError): raise HTTPException(422, '请保留原候选与请求编号') from None
    return request_result(db, p, consumer_id, request_id, expected_sha256)
