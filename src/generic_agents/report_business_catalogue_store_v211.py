"""Durable author-owned catalogue work, with one genuine V181 confirmation.

Fresh complete origins use the real completed consumer, CloudCall and capture.
An explicit failed-partial mode uses its independent current source service;
its original answer and financial outcome remain failed and unchanged.
Historical drafts rebuild their immutable saved source/projection and every
Command/Audit without requiring that source to remain currently usable. Saving
human labour is independent; new confirmation rechecks the actual source.
"""
import copy
import hashlib
import json
import re
from datetime import timezone
from uuid import UUID, NAMESPACE_URL, uuid5

from fastapi import HTTPException
from sqlalchemy import or_, select

from . import models as m
from . import report_business_catalogue_edit_v211 as edit
from . import report_business_catalogue_native_v211 as native
from . import report_business_catalogue_wire_v211 as wire
from . import report_business_catalogue_partial_v212 as partial
from . import report_business_catalogue_source_v212 as partial_source
from . import report_design_saved_source_v174 as saved_source
from . import report_saved_map_design_consumer_v176 as consumer
from . import report_scoped_design_store_v181 as plans
from .contracts import digest
from .report_business_catalogue_models_v211 import ReportBusinessCatalogueDraftV211 as Draft
from .report_business_catalogue_models_v211 import ReportBusinessCatalogueCommandV211 as Command
from .report_scoped_design_models_v181 import ReportScopedDesignPlanCommandV181 as PlanCommand
from .report_saved_map_design_preparation_v176 import _source as checked_source
from .report_full_reading_semantics import _safe
from .services import audit
from .typed_payload_codec_v131 import canonical_json

PREFIX = '/api/setup/report-business-catalogues/v211'
VIEW = 'report-business-catalogue-draft-view-v211'
RESULT = 'report-business-catalogue-draft-result-v211'
REQUEST = 'report-business-catalogue-draft-request-v211'
STATE = 'report-business-catalogue-draft-state-v211'
ORIGIN = 'report-business-catalogue-draft-origin-v211'
INPUTS = 'report-business-catalogue-draft-inputs-v211'
CONTEXT = 'report-business-catalogue-context-v211'
COMPLETE = 'complete'
PARTIAL = partial_source.MODE
COMMAND_AUDIT = 'report.business_catalogue_command_v211'
STATE_AUDIT = 'report.business_catalogue_state_v211'
MAX_WIRE_BYTES = 2 * 1024 * 1024
MAX_RECORD_BYTES = 32 * 1024 * 1024
MAX_DRAFTS = 100
MAX_HISTORY = 300
SHA = re.compile(r'^[a-f0-9]{64}$')
ERROR = '业务目录、原来源或独立修改历史无法核对；原资料和人工稿保留'


def need(ok, message=ERROR, status=409):
    if not ok:
        raise HTTPException(status, message)


def identity(value):
    if type(value) is not str or str(UUID(value)) != value:
        raise ValueError(ERROR)
    return value


def same(left, right):
    return canonical_json(left) == canonical_json(right)


def bytes_value(value):
    _safe(value)
    return canonical_json(value).encode('utf-8', errors='strict')


def sha(value):
    return type(value) is str and SHA.fullmatch(value) is not None


def command_path(draft_id, operation):
    if operation == 'create':
        need(draft_id is None)
        return PREFIX + '/drafts'
    need(operation in {'save', 'confirm'})
    return PREFIX + '/drafts/' + identity(draft_id) + '/' + operation


def parse_wire(raw, operation):
    try:
        if type(raw) is not bytes or not 0 < len(raw) <= MAX_WIRE_BYTES:
            raise ValueError(ERROR)
        def pairs(items):
            value = {}
            for key, item in items:
                if key in value: raise ValueError(ERROR)
                value[key] = item
            return value
        def constant(_): raise ValueError(ERROR)
        value = json.loads(raw.decode('utf-8', errors='strict'), object_pairs_hook=pairs, parse_constant=constant)
        bytes_value(value)
        if operation == 'create': fields = {'request_id', 'consumer_id', 'expected_projection_hash'}
        elif operation == 'save': fields = {'request_id', 'expected_version', 'expected_record_hash', 'document', 'actions'}
        elif operation == 'confirm': fields = {'request_id', 'expected_version', 'expected_record_hash', 'document_hash', 'confirm'}
        else: raise ValueError(ERROR)
        if type(value) is not dict: raise ValueError(ERROR)
        if operation == 'create' and 'origin_mode' in value: fields = fields | {'origin_mode'}
        if set(value) != fields: raise ValueError(ERROR)
        identity(value['request_id'])
        if operation == 'create':
            identity(value['consumer_id'])
            if not sha(value['expected_projection_hash']): raise ValueError(ERROR)
            if value.get('origin_mode', COMPLETE) not in (COMPLETE, PARTIAL): raise ValueError(ERROR)
        else:
            if type(value['expected_version']) is not int or value['expected_version'] < 1: raise ValueError(ERROR)
            if not sha(value['expected_record_hash']): raise ValueError(ERROR)
            if operation == 'confirm' and (value['confirm'] is not True or not sha(value['document_hash'])):
                raise ValueError(ERROR)
            if operation == 'save' and (type(value['document']) is not dict or type(value['actions']) is not list):
                raise ValueError(ERROR)
        return value
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
        raise ValueError('目录请求的原始JSON、严格版本或完整字段无法核对') from None


def _utc(value):
    need(value is not None and value.tzinfo is not None and value.utcoffset() is not None)
    return value.astimezone(timezone.utc).isoformat()


def _lock(db, p, request_id=None, *, write=False):
    # History may inspect a bound V181 Plan. Keep the same cross-family order
    # for readers and writers; confirmation already holds this lock Exclusive.
    plans._lock(db, {'scoped_design_actor_v181': p.user_id, 'workspace_id': p.workspace_id}, shared=True)
    if request_id is not None:
        plans._lock(db, {'catalogue_nonce_v211': identity(request_id), 'workspace_id': p.workspace_id}, shared=not write)
    plans._lock(db, {'catalogue_actor_v211': p.user_id, 'workspace_id': p.workspace_id}, shared=not write)


def _mode(origin):
    need(type(origin) is dict and type(origin.get('metadata')) is dict)
    mode = origin['metadata'].get('origin_mode', COMPLETE)
    need(mode in (COMPLETE, PARTIAL))
    return mode


def _seed(origin):
    if _mode(origin) == PARTIAL:
        return edit.validate_document(origin['projection']['draft_seed'], origin['metadata']['input_count'])
    return edit.from_projection(origin['projection'])


def _validation(document, origin):
    if _mode(origin) == PARTIAL:
        return edit.partial_validation(document, origin['projection'])
    return edit.validation(document, origin['metadata']['input_count'])


def _load_partial_origin(db, p, consumer_id):
    """The independent failed-source service owns every current source gate."""
    loaded = partial_source.load_failed_partial_source(db, p, identity(consumer_id))
    row, call, job = loaded['row'], loaded['call'], loaded['job']
    actual, source, prep = loaded['actual'], loaded['source'], loaded['prep']
    raw_arguments, projection = loaded['raw_arguments'], loaded['projection']
    snapshot, claims, _ = checked_source(source)
    metadata = {'schema': ORIGIN, 'consumer_id': row.id, 'consumer_version': row.version,
        'producer_id': row.producer_id, 'revision': row.revision,
        'source_snapshot_hash': snapshot['snapshot_hash'], 'indexed_inputs_hash': prep['indexed_inputs']['inputs_hash'],
        'input_claims_hash': prep['input_claims_hash'], 'input_count': len(claims),
        'projection_hash': projection['projection_hash'], 'call_id': call.id,
        'capture_hash': actual['capture']['capture_hash'],
        'response_body_sha256': row.data['response']['body_sha256'],
        'arguments_sha256': hashlib.sha256(raw_arguments.encode('utf-8')).hexdigest(), 'origin_mode': PARTIAL}
    return {'metadata': metadata, 'source_context': copy.deepcopy(source),
        'indexed_inputs': copy.deepcopy(prep['indexed_inputs']), 'raw_arguments': raw_arguments,
        'projection': copy.deepcopy(projection), 'request_hash': prep['request_hash'],
        'job_identity': {'id': job.id, 'workspace_id': job.workspace_id, 'kind': job.kind,
            'idempotency_key': job.idempotency_key, 'actor_id': job.data['actor_id'],
            'consumer_id': row.id, 'request_id': row.request_id},
        'capture_proof': {key: copy.deepcopy(actual[key]) for key in ('receipt', 'capture', 'descriptor', 'binding')}}


def _load_origin(db, p, consumer_id, *, origin_mode=COMPLETE):
    """Read-only real source edge; never repair a journal, settle or run a Job."""
    if origin_mode == PARTIAL:
        return _load_partial_origin(db, p, consumer_id)
    need(origin_mode == COMPLETE)
    rows, _, calls = consumer._inventory(db, p)
    row = consumer._owned(rows, p, identity(consumer_id))
    need(consumer._family(row.data['preview']['preparation']).id == 'catalogue-v211'
        and row.status in {'review', 'needs_clarification'} and row.data['projection'] is not None
        and row.data['manual_value'] is None, '请先完成本次真实业务目录建议，未采用旧设计或未完成回答')
    call = calls.get(row.id)
    job = db.scalar(select(m.Job).where(m.Job.id == row.job_id, m.Job.workspace_id == p.workspace_id))
    need(call is not None and call.id == row.reserved_call_id and call.status == 'succeeded'
        and job is not None and job.id == row.job_id and job.kind == consumer.JOB
        and job.idempotency_key == 'saved-map-design:' + row.request_id
        and job.data.get('actor_id') == p.user_id and job.data.get('consumer_id') == row.id,
        '原目录调用或原后台任务身份无法完整核对，请按原号查看')
    actual = consumer.runtime().read_response(db, p, row, recover_saved=False)
    need(type(actual) is dict and type(actual.get('body')) is bytes, '原目录HTTP或完整接收记录尚无法核对')
    response = row.data['response']; prep = row.data['preview']['preparation']
    need(type(response) is dict and same(actual['binding'], consumer.journal_binding(row))
        and all(same(actual[key], response[key]) for key in ('receipt', 'capture', 'descriptor', 'binding'))
        and same(call.data.get('receipt'), actual['receipt'])
        and response['body_sha256'] == hashlib.sha256(actual['body']).hexdigest())
    inspected = native.inspect_response(actual['body'], actual['capture']['http_status'], native.STAGE,
        response_complete=actual['capture']['response_complete'],
        http_content_encoding=actual['capture']['http_content_encoding'])
    need(inspected['metadata']['envelope_valid'] is True)
    source = row.data['preview']['source_context']; snapshot, claims, _ = checked_source(source)
    need(same(saved_source.load_historical_source_context(db, p, row.producer_id), source),
        '原报告资料及提取结果的独立来源无法冷验，人工稿保留，未采用缓存替代')
    raw_arguments = inspected['arguments']
    projection = wire.check_catalogue_projection(row.data['projection'], raw_arguments.encode('utf-8'),
        indexed=prep['indexed_inputs'], input_claims=claims,
        source_snapshot_hash=snapshot['snapshot_hash'], catalog=source['catalog'])
    metadata = {'schema': ORIGIN, 'consumer_id': row.id, 'consumer_version': row.version,
        'producer_id': row.producer_id, 'revision': row.revision,
        'source_snapshot_hash': snapshot['snapshot_hash'], 'indexed_inputs_hash': prep['indexed_inputs']['inputs_hash'],
        'input_claims_hash': prep['input_claims_hash'], 'input_count': len(claims),
        'projection_hash': projection['projection_hash'], 'call_id': call.id,
        'capture_hash': actual['capture']['capture_hash'], 'response_body_sha256': response['body_sha256'],
        'arguments_sha256': hashlib.sha256(raw_arguments.encode('utf-8')).hexdigest()}
    need(metadata['arguments_sha256'] == response['arguments_sha256'])
    return {'metadata': metadata, 'source_context': copy.deepcopy(source),
        'indexed_inputs': copy.deepcopy(prep['indexed_inputs']), 'raw_arguments': raw_arguments,
        'projection': projection, 'request_hash': prep['request_hash'],
        'job_identity': {'id': job.id, 'workspace_id': job.workspace_id, 'kind': job.kind,
            'idempotency_key': job.idempotency_key, 'actor_id': job.data['actor_id'],
            'consumer_id': row.id, 'request_id': row.request_id},
        'capture_proof': {key: copy.deepcopy(actual[key]) for key in ('receipt', 'capture', 'descriptor', 'binding')}}


def _check_origin(origin, p):
    need(type(origin) is dict and set(origin) == {'metadata', 'source_context', 'indexed_inputs',
        'raw_arguments', 'projection', 'request_hash', 'capture_proof', 'job_identity'})
    metadata = origin['metadata']; source = origin['source_context']
    mode = _mode(origin)
    snapshot, claims, _ = checked_source(source)
    need(snapshot['actor_id'] == p.user_id and snapshot['workspace_id'] == p.workspace_id
        and type(metadata) is dict and set(metadata) == {'schema', 'consumer_id', 'consumer_version',
            'producer_id', 'revision', 'source_snapshot_hash', 'indexed_inputs_hash', 'input_claims_hash',
            'input_count', 'projection_hash', 'call_id', 'capture_hash', 'response_body_sha256', 'arguments_sha256'}
            | ({'origin_mode'} if mode == PARTIAL else set())
        and metadata['schema'] == ORIGIN and type(metadata['consumer_version']) is int
        and metadata['consumer_version'] >= 1 and type(metadata['revision']) is int and metadata['revision'] >= 1
        and metadata['producer_id'] == snapshot['producer']['id']
        and metadata['source_snapshot_hash'] == snapshot['snapshot_hash']
        and metadata['input_count'] == len(claims) and type(metadata['input_count']) is int)
    for key in ('consumer_id', 'producer_id', 'call_id'): identity(metadata[key])
    for key in ('source_snapshot_hash', 'indexed_inputs_hash', 'input_claims_hash', 'projection_hash',
            'capture_hash', 'response_body_sha256', 'arguments_sha256'):
        need(sha(metadata[key]))
    job = origin['job_identity']
    need(type(job) is dict and set(job) == {'id', 'workspace_id', 'kind', 'idempotency_key',
        'actor_id', 'consumer_id', 'request_id'} and job['workspace_id'] == p.workspace_id
        and job['kind'] == consumer.JOB and job['actor_id'] == p.user_id
        and job['consumer_id'] == metadata['consumer_id'])
    identity(job['id']); identity(job['request_id'])
    need(job['idempotency_key'] == 'saved-map-design:' + job['request_id'])
    need(sha(origin['request_hash']) and type(origin['raw_arguments']) is str)
    checker = partial.check_partial_projection if mode == PARTIAL else wire.check_catalogue_projection
    checked = checker(origin['projection'], origin['raw_arguments'].encode('utf-8'),
        indexed=origin['indexed_inputs'], input_claims=claims,
        source_snapshot_hash=snapshot['snapshot_hash'], catalog=source['catalog'])
    proof = origin['capture_proof']
    need(type(proof) is dict and set(proof) == {'receipt', 'capture', 'descriptor', 'binding'}
        and metadata['projection_hash'] == checked['projection_hash']
        and metadata['indexed_inputs_hash'] == origin['indexed_inputs']['inputs_hash']
        and metadata['input_claims_hash'] == origin['indexed_inputs']['input_claims_hash']
        and metadata['arguments_sha256'] == hashlib.sha256(origin['raw_arguments'].encode('utf-8')).hexdigest()
        and proof['binding']['draft_id'] == metadata['consumer_id']
        and proof['binding']['reserved_call_id'] == metadata['call_id']
        and proof['binding']['actor_id'] == p.user_id and proof['binding']['workspace_id'] == p.workspace_id
        and proof['capture']['capture_hash'] == metadata['capture_hash']
        and proof['capture']['body_sha256'] == metadata['response_body_sha256']
        and proof['receipt']['call_id'] == metadata['call_id']
        and proof['receipt']['arguments_sha256'] == metadata['arguments_sha256'])
    if mode == PARTIAL:
        need(proof['capture'].get('http_status') == 200
            and proof['capture'].get('ready') is True
            and proof['capture'].get('response_complete') is True
            and proof['capture'].get('content_encoding_safe') is True
            and proof['receipt'].get('provider') == 'qwen'
            and proof['receipt'].get('model') == native.MODEL
            and proof['receipt'].get('envelope_valid') is True
            and proof['receipt'].get('finish_reason') == 'stop'
            and proof['receipt'].get('capture_hash') == metadata['capture_hash'],
            '原部分建议的正常完整接收证明无法核对，未改用其他回答')
    return metadata, claims


def _snapshot(row, document=None, plan_ref=None, *, version=None, current=True):
    if current: document, plan_ref = row.data['document'], row.data['plan_ref']
    return {'schema': STATE, 'id': row.id, 'workspace_id': row.workspace_id, 'actor_id': row.actor_id,
        'consumer_id': row.consumer_id, 'request_id': row.request_id,
        'created_at': _utc(row.created_at),
        'version': row.version if version is None else version, 'origin_hash': row.origin_hash,
        'document_hash': digest(document), 'plan_ref': copy.deepcopy(plan_ref)}


def _command_pin(command):
    return {**{key: copy.deepcopy(getattr(command, key)) for key in ('id', 'workspace_id', 'actor_id',
        'version', 'draft_id', 'draft_version', 'request_id', 'operation', 'method', 'path', 'exact_body', 'wire_sha256', 'data')},
        'created_at': _utc(command.created_at)}


def _result(state, operation):
    return {'schema': RESULT, 'id': state['id'], 'version': state['version'], 'operation': operation,
        'record_hash': digest(state), 'document_hash': state['document_hash'], 'plan_ref': copy.deepcopy(state['plan_ref'])}


def _command_anchor(command):
    return {'draft_id': command.draft_id, 'actor_id': command.actor_id, 'request_id': command.request_id,
        'version': command.draft_version, 'command_id': command.id, 'command_hash': command.command_hash}


def _state_anchor(row, command, record_hash):
    return {**_command_anchor(command), 'origin_hash': row.origin_hash, 'record_hash': record_hash}


def _child_requests(request_id):
    return tuple(str(uuid5(NAMESPACE_URL, PREFIX + ':' + identity(request_id) + ':' + operation))
        for operation in ('create-plan', 'confirm-plan'))


def _check_plan_ref(db, p, reference, document, origin):
    need(type(reference) is dict and set(reference) == {'plan_id', 'plan_version', 'plan_hash', 'ledger_hash',
        'document_hash', 'create_request_id', 'confirm_request_id', 'create_command_id', 'confirm_command_id'})
    try:
        row = plans.checked_plan(db, p, identity(reference['plan_id']), confirmed=True)
    except HTTPException as error:
        if error.status_code != 404: raise
        raise HTTPException(409, '目录绑定的范围计划已缺失，原人稿保留，未另建或替换计划') from None
    metadata, claims = _check_origin(origin, p)
    need(not any(issue['blocks_confirmation'] for issue in _validation(document, origin)['issues']),
        '原确认目录的归属或用户命名决定无法核对，未借用范围计划')
    ledger = edit.scope_ledger(document, indexed=origin['indexed_inputs'], input_claims=claims,
        source_snapshot_hash=metadata['source_snapshot_hash'])
    need(reference['document_hash'] == digest(document) and type(reference['plan_version']) is int
        and reference['plan_version'] == row.version == 2
        and row.producer_id == metadata['producer_id'] and row.revision == metadata['revision']
        and same(row.data['source_context'], origin['source_context'])
        and same(row.data['ledger'], ledger) and reference['ledger_hash'] == digest(ledger)
        and reference['plan_hash'] == row.data['plan']['plan_hash'])
    commands = list(db.scalars(select(PlanCommand).where(PlanCommand.workspace_id == p.workspace_id,
        PlanCommand.actor_id == p.user_id, PlanCommand.plan_id == row.id)))
    need(len(commands) == 2)
    by_op = {command.operation: command for command in commands}
    need(set(by_op) == {'create', 'confirm'}
        and by_op['create'].id == reference['create_command_id']
        and by_op['confirm'].id == reference['confirm_command_id']
        and by_op['create'].request_id == reference['create_request_id']
        and by_op['confirm'].request_id == reference['confirm_request_id'])
    return row


def _history(db, p, row, commands, events):
    need(row.workspace_id == p.workspace_id and row.actor_id == p.user_id
        and type(row.version) is int and 1 <= row.version <= MAX_HISTORY
        and type(row.data) is dict and set(row.data) == {'schema', 'origin', 'document', 'plan_ref'}
        and row.data['schema'] == STATE and row.origin_hash == digest(row.data['origin']))
    metadata, _ = _check_origin(row.data['origin'], p)
    need(row.consumer_id == metadata['consumer_id'] and len(commands) == row.version and len(events) == row.version * 2)
    previous_hash, previous_document, bound, history, checked_events = None, None, None, [], set()
    for version, command in enumerate(sorted(commands, key=lambda value: value.draft_version), 1):
        need(type(command.draft_version) is int and command.draft_version == version
            and command.version == 1 and command.draft_id == row.id
            and command.workspace_id == p.workspace_id and command.actor_id == p.user_id and command.method == 'POST'
            and command.command_hash == digest(_command_pin(command)))
        raw = command.exact_body.encode('utf-8'); body = parse_wire(raw, command.operation)
        need(command.wire_sha256 == hashlib.sha256(raw).hexdigest() and command.request_id == body['request_id']
            and command.path == command_path(None if command.operation == 'create' else row.id, command.operation)
            and type(command.data) is dict and set(command.data) == {'previous_hash', 'document', 'actions', 'state', 'result'}
            and command.data['previous_hash'] == previous_hash and bound is None)
        document = edit.validate_document(command.data['document'], metadata['input_count'])
        if version == 1:
            need(command.operation == 'create' and command.request_id == row.request_id
                and body['consumer_id'] == row.consumer_id
                and body['expected_projection_hash'] == metadata['projection_hash']
                and body.get('origin_mode', COMPLETE) == _mode(row.data['origin'])
                and same(document, _seed(row.data['origin']))
                and command.data['actions'] == [])
        else:
            need(command.operation in {'save', 'confirm'} and body['expected_version'] == version - 1
                and body['expected_record_hash'] == previous_hash)
            if command.operation == 'save':
                need(same(document, body['document']) and same(command.data['actions'], body['actions']))
                edit.validate_actions(body['actions'], previous_document, document)
            else:
                need(same(document, previous_document) and command.data['actions'] == []
                    and body['document_hash'] == digest(document))
                bound = command.data['state']['plan_ref']
                need(bound is not None and (bound['create_request_id'], bound['confirm_request_id'])
                    == _child_requests(command.request_id))
                _check_plan_ref(db, p, bound, document, row.data['origin'])
        state = _snapshot(row, document, bound, version=version, current=False)
        need(same(command.data['state'], state) and same(command.data['result'], _result(state, command.operation)))
        record_hash = digest(state)
        found_command = [event for event in events if event.action == COMMAND_AUDIT and event.object_id == command.id]
        found_state = [event for event in events if event.action == STATE_AUDIT
            and event.object_id == row.id and event.data.get('version') == version]
        need(len(found_command) == len(found_state) == 1)
        for event in found_command + found_state:
            need(event.workspace_id == p.workspace_id and event.actor_id == p.user_id)
            checked_events.add(event.id)
        need(same(found_command[0].data, _command_anchor(command))
            and same(found_state[0].data, _state_anchor(row, command, record_hash)))
        history.append({'version': version, 'operation': command.operation, 'request_id': command.request_id,
            'document_hash': digest(document), 'saved_at': _utc(command.created_at),
            'actions': copy.deepcopy(command.data['actions'])})
        previous_hash, previous_document = record_hash, document
    need(checked_events == {event.id for event in events} and row.record_hash == previous_hash
        and row.document_hash == digest(previous_document) and same(row.data['document'], previous_document)
        and same(row.data['plan_ref'], bound) and row.record_hash == digest(_snapshot(row))
        and len(bytes_value(row.data)) <= MAX_RECORD_BYTES)
    return history


def _inventory(db, p):
    rows = list(db.scalars(select(Draft).where(Draft.workspace_id == p.workspace_id, Draft.actor_id == p.user_id)))
    commands = list(db.scalars(select(Command).where(Command.workspace_id == p.workspace_id, Command.actor_id == p.user_id)))
    events = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action.in_([COMMAND_AUDIT, STATE_AUDIT]),
        or_(m.Audit.actor_id == p.user_id, m.Audit.data['actor_id'].as_string() == p.user_id))))
    need(len(rows) <= MAX_DRAFTS and len(commands) <= MAX_DRAFTS * MAX_HISTORY
        and len(events) <= MAX_DRAFTS * MAX_HISTORY * 2)
    by_id = {row.id: row for row in rows}; by_command = {command.id: command for command in commands}
    need(len(by_id) == len(rows) and len(by_command) == len(commands)
        and len({command.request_id for command in commands}) == len(commands))
    need(all(command.draft_id in by_id for command in commands)
        and all(type(event.data) is dict and event.data.get('draft_id') in by_id for event in events))
    histories = {row.id: _history(db, p, row, [command for command in commands if command.draft_id == row.id],
        [event for event in events if event.data['draft_id'] == row.id]) for row in rows}
    # Independent reverse witnesses keep a batch adoption distinguishable from
    # an ordinary human save, including when a parent/link was accidentally lost.
    from .report_saved_map_design_models_v176 import ReportSavedMapDesignCommandV176 as ParentCommand
    link_events = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action.in_(['report.catalogue_allocation_apply_link_v213',
            'report.catalogue_allocation_child_link_v213']),
        or_(m.Audit.actor_id == p.user_id, m.Audit.data['actor_id'].as_string() == p.user_id))))
    parent = db.scalar(select(ParentCommand.id).where(ParentCommand.workspace_id == p.workspace_id,
        ParentCommand.actor_id == p.user_id, ParentCommand.operation == 'apply-allocation').limit(1))
    if link_events or parent is not None:
        from .report_business_catalogue_allocation_apply_v213 import check_catalogue_links
        check_catalogue_links(db, p, by_id, by_command, link_events)
    current_links = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action.in_(['report.catalogue_current_allocation_apply_link_v260',
            'report.catalogue_current_allocation_child_link_v260']),
        or_(m.Audit.actor_id == p.user_id, m.Audit.data['actor_id'].as_string() == p.user_id))))
    current_parent = db.scalar(select(ParentCommand.id).where(ParentCommand.workspace_id == p.workspace_id,
        ParentCommand.actor_id == p.user_id, ParentCommand.operation == 'apply-current-allocation').limit(1))
    if current_links or current_parent is not None:
        from .catalogue_current_allocation_apply_v260 import check_catalogue_links
        check_catalogue_links(db, p, by_id, by_command, current_links)
    from .catalogue_current_context_store_v260 import verify_catalogue_inventory
    verify_catalogue_inventory(db,p,by_id,by_command)
    return by_id, by_command, histories


def _owned(rows, draft_id):
    row = rows.get(identity(draft_id)); need(row is not None, '目录草稿不存在或不属于当前账号', 404)
    return row


def _view(row, history, p, *, db=None):
    validation = _validation(row.data['document'], row.data['origin'])
    can_save = p.role in {'admin', 'operator'} and row.data['plan_ref'] is None
    from .catalogue_current_context_contracts_v260 import managed
    if managed(row.id):
        from .catalogue_current_context_store_v260 import can_edit
        can_save=can_save and db is not None and can_edit(db,p,row)
    return {'schema': VIEW, 'id': row.id, 'version': row.version, 'workspace_id': row.workspace_id,
        'actor_id': row.actor_id, 'record_hash': row.record_hash, 'document_hash': row.document_hash,
        'origin': copy.deepcopy(row.data['origin']['metadata']), 'document': copy.deepcopy(row.data['document']),
        'validation': validation, 'permissions': {'can_save': can_save,
            'can_confirm': can_save and not managed(row.id) and not any(item['blocks_confirmation'] for item in validation['issues']),
            'requires_current_source_check': True}, 'plan_ref': copy.deepcopy(row.data['plan_ref']),
        'history': copy.deepcopy(history)}


def detail(db, p, draft_id):
    plans._iam(db, p); _lock(db, p)
    rows, _, histories = _inventory(db, p); row = _owned(rows, draft_id)
    return _view(row, histories[row.id], p, db=db)


def history_version(db, p, draft_id, version):
    """Read one verified original human document, never change the current CAS."""
    plans._iam(db, p)
    need(type(version) is int and 1 <= version <= MAX_HISTORY, '历史版本须为有效严格整数', 422)
    _lock(db, p); rows, commands, _ = _inventory(db, p); row = _owned(rows, draft_id)
    found = [command for command in commands.values()
        if command.draft_id == row.id and command.draft_version == version]
    need(len(found) == 1, '该目录的原保存版本不存在', 404)
    command = found[0]; state = command.data['state']
    return {'schema': 'report-business-catalogue-draft-revision-v211', 'draft_id': row.id,
        'version': version, 'record_hash': digest(state), 'document_hash': state['document_hash'],
        'document': copy.deepcopy(command.data['document']), 'actions': copy.deepcopy(command.data['actions']),
        'saved_at': _utc(command.created_at), 'request_id': command.request_id,
        'plan_ref': copy.deepcopy(state['plan_ref'])}


def list_drafts(db, p, consumer_id, *, offset=0, limit=50):
    plans._iam(db, p); identity(consumer_id)
    need(type(offset) is int and offset >= 0 and type(limit) is int and 1 <= limit <= 100,
        '草稿分页参数须为严格整数', 422)
    _lock(db, p); rows, _, _ = _inventory(db, p)
    selected = sorted((row for row in rows.values() if row.consumer_id == consumer_id),
        key=lambda row: (row.created_at, row.id), reverse=True)
    records = [{'id': row.id, 'version': row.version, 'record_hash': row.record_hash,
        'document_hash': row.document_hash, 'origin': copy.deepcopy(row.data['origin']['metadata']),
        'plan_ref': copy.deepcopy(row.data['plan_ref'])} for row in selected[offset:offset + limit]]
    return {'schema': 'report-business-catalogue-draft-list-v211', 'consumer_id': consumer_id,
        'offset': offset, 'limit': limit, 'has_more': offset + limit < len(selected), 'records': records}


def inputs(db, p, draft_id, *, offset=0, limit=50):
    need(type(offset) is int and offset >= 0 and type(limit) is int and 1 <= limit <= 100,
        '原声明分页参数须为严格整数', 422)
    plans._iam(db, p); _lock(db, p)
    rows, _, _ = _inventory(db, p); row = _owned(rows, draft_id)
    projection = row.data['origin']['projection']; records = projection['assignments']
    return {'schema': INPUTS, 'draft_id': row.id, 'projection_hash': projection['projection_hash'],
        'total': len(records), 'offset': offset, 'limit': limit, 'has_more': offset + limit < len(records),
        'records': copy.deepcopy(records[offset:offset + limit])}


def request_result(db, p, request_id, expected_sha256):
    plans._iam(db, p); identity(request_id); need(sha(expected_sha256), '原请求摘要不符合SHA256格式', 422)
    _lock(db, p, request_id)
    _any_nonce(db, p, request_id)
    _, commands, _ = _inventory(db, p)
    found = [command for command in commands.values() if command.request_id == request_id]
    need(len(found) <= 1)
    command = found[0] if found else None
    need(command is None or command.wire_sha256 == expected_sha256, '该原编号对应另一完整正文，原记录保留')
    return {'schema': REQUEST, 'request_id': request_id, 'wire_sha256': expected_sha256,
        'outcome': 'saved' if command is not None else 'unknown',
        'result': copy.deepcopy(command.data['result']) if command is not None else None}


def _origin_content(origin):
    """Comparison only, never a source verifier or an authority shortcut.

    A normal recover-saved command advances the consumer command inventory;
    all content, original request, Job identity and capture pins remain fixed.
    Keep the original version in stored history and omit ONLY it here.
    """
    value = copy.deepcopy(origin)
    value['metadata'].pop('consumer_version')
    return value


def _current_source(db, p, origin, *, lock=True):
    metadata, _ = _check_origin(origin, p)
    fresh = _load_origin(db, p, metadata['consumer_id'], origin_mode=_mode(origin))
    need(same(_origin_content(fresh), _origin_content(origin)),
        '原机器目录、调用或接收记录已变化，人工稿仍保留，未确认')
    source = plans.runtime().load_source(db, p, metadata['producer_id'], metadata['revision'], lock=lock)
    need(same(source, origin['source_context']), '当前报告权限、版本或原来源已变化，人工稿仍保留，未确认')


def _any_nonce(db, p, request_id):
    records = list(db.scalars(select(Command).where(Command.workspace_id == p.workspace_id,
        Command.request_id == request_id)))
    origins = list(db.scalars(select(Draft).where(Draft.workspace_id == p.workspace_id,
        Draft.request_id == request_id)))
    events = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action.in_([COMMAND_AUDIT, STATE_AUDIT]),
        m.Audit.data['request_id'].as_string() == request_id)))
    need(all(row.actor_id == p.user_id for row in records + origins + events),
        '该原编号属于其他账号，不能借用', 403)


def _confirm_plan(db, p, row, request_id):
    origin = row.data['origin']; metadata, claims = _check_origin(origin, p)
    need(not any(issue['blocks_confirmation'] for issue in _validation(row.data['document'], origin)['issues']),
        '原归属或用户命名决定仍未完整保存，人工稿保留，未确认范围')
    ledger = edit.scope_ledger(row.data['document'], indexed=origin['indexed_inputs'], input_claims=claims,
        source_snapshot_hash=metadata['source_snapshot_hash'])
    create_nonce, confirm_nonce = _child_requests(request_id)
    # These nonces and the V181 actor were locked before any historical Plan
    # read. Existing children without this original parent cannot be adopted.
    existing = list(db.scalars(select(PlanCommand).where(PlanCommand.workspace_id == p.workspace_id,
        PlanCommand.request_id.in_([create_nonce, confirm_nonce]))))
    need(not existing, '内部原范围请求已存在却未绑定本次目录，未借用或重建计划')
    body = {'request_id': create_nonce, 'producer_id': metadata['producer_id'],
        'revision': metadata['revision'], 'source_snapshot_hash': metadata['source_snapshot_hash'], 'ledger': ledger}
    created = plans.mutate(db, p, None, 'create', bytes_value(body), actual_method='POST',
        actual_path=plans.command_path(None, 'create'))
    plan_id = created['id']
    body = {'request_id': confirm_nonce, 'expected_version': created['version'], 'revision': metadata['revision'],
        'source_snapshot_hash': metadata['source_snapshot_hash'], 'confirm': True}
    confirmed = plans.mutate(db, p, plan_id, 'confirm', bytes_value(body), actual_method='POST',
        actual_path=plans.command_path(plan_id, 'confirm'))
    child_rows = list(db.scalars(select(PlanCommand).where(PlanCommand.workspace_id == p.workspace_id,
        PlanCommand.plan_id == plan_id)))
    by_op = {command.operation: command for command in child_rows}
    need(len(child_rows) == 2 and set(by_op) == {'create', 'confirm'})
    reference = {'plan_id': plan_id, 'plan_version': confirmed['version'], 'plan_hash': confirmed['plan_hash'],
        'ledger_hash': digest(ledger), 'document_hash': row.document_hash,
        'create_request_id': create_nonce, 'confirm_request_id': confirm_nonce,
        'create_command_id': by_op['create'].id, 'confirm_command_id': by_op['confirm'].id}
    _check_plan_ref(db, p, reference, row.data['document'], origin)
    return reference


def mutate(db, p, draft_id, operation, raw, *, actual_method, actual_path, _trusted_create_id=None):
    body = parse_wire(raw, operation)
    if _trusted_create_id is not None:need(operation=='create','可信服务端编号只用于本次新建')
    need(actual_method == 'POST' and actual_path == command_path(draft_id, operation), '目录操作路径不匹配原正文', 422)
    plans._iam(db, p, write=True)
    if operation == 'confirm':
        for nonce in sorted(_child_requests(body['request_id'])): plans._nonce(db, p, nonce, shared=False)
        plans._lock(db, {'scoped_design_actor_v181': p.user_id, 'workspace_id': p.workspace_id}, shared=False)
    _lock(db, p, body['request_id'], write=True)
    # Workspace-wide raw-nonce isolation also rejects another author's traces.
    _any_nonce(db, p, body['request_id'])
    rows, commands, _ = _inventory(db, p)
    prior = next((command for command in commands.values() if command.request_id == body['request_id']), None)
    exact = raw.decode('utf-8'); body_sha = hashlib.sha256(raw).hexdigest()
    if prior is not None:
        need(prior.operation == operation and prior.path == actual_path and prior.method == actual_method
            and prior.exact_body == exact and prior.wire_sha256 == body_sha,
            '原请求编号已用于不同正文或操作，原记录保留')
        return copy.deepcopy(prior.data['result'])
    if operation == 'create':
        need(len(rows) < MAX_DRAFTS, '目录完整历史库存已达当前容量，未覆盖旧稿')
        origin = _load_origin(db, p, body['consumer_id'], origin_mode=body.get('origin_mode', COMPLETE))
        need(origin['metadata']['projection_hash'] == body['expected_projection_hash'], '原目录建议已变化，未采用另一份建议')
        document = _seed(origin); previous = None; actions = []
        if _trusted_create_id is None:
            create_id=m.uid()
        else:
            from .catalogue_current_context_store_v260 import trusted_create_id
            create_id=trusted_create_id(db,p,_trusted_create_id,raw)
        row = Draft(id=create_id, workspace_id=p.workspace_id, actor_id=p.user_id, consumer_id=body['consumer_id'],
            request_id=body['request_id'], version=1, origin_hash=digest(origin), document_hash=digest(document),
            record_hash='', created_at=m.now(), data={'schema': STATE, 'origin': origin, 'document': document, 'plan_ref': None})
    else:
        row = _owned(rows, draft_id)
        from .catalogue_current_context_store_v260 import guard_mutation
        guard_mutation(db,p,row,operation,raw)
        need(row.data['plan_ref'] is None, '目录已绑定真实范围计划；原文档保留，不能原地覆盖已确认意图')
        need(body['expected_version'] == row.version and body['expected_record_hash'] == row.record_hash,
            '目录版本已变化，本页原输入保留，请核对原请求后读取新版本')
        need(row.version < MAX_HISTORY, '目录已达完整历史容量，未覆盖或裁剪旧稿')
        previous = row.record_hash; document = row.data['document']; actions = []
        if operation == 'save':
            document = edit.validate_document(body['document'], row.data['origin']['metadata']['input_count'])
            actions = edit.validate_actions(body['actions'], row.data['document'], document)
        else:
            need(body['document_hash'] == row.document_hash, '确认文档与服务器已保存完整稿不同，未确认')
            _current_source(db, p, row.data['origin'])
            reference = _confirm_plan(db, p, row, body['request_id'])
            row.data = {**row.data, 'plan_ref': reference}
        row.version += 1; row.document_hash = digest(document)
        row.data = {**row.data, 'document': copy.deepcopy(document)}
    state = _snapshot(row); row.record_hash = digest(state)
    command = Command(id=m.uid(), workspace_id=p.workspace_id, actor_id=p.user_id, draft_id=row.id,
        draft_version=row.version, version=1, request_id=body['request_id'], operation=operation,
        method=actual_method, path=actual_path, exact_body=exact, wire_sha256=body_sha,
        created_at=m.now(), command_hash='', data={'previous_hash': previous,
            'document': copy.deepcopy(document), 'actions': actions, 'state': state, 'result': _result(state, operation)})
    command.command_hash = digest(_command_pin(command))
    need(len(bytes_value(row.data)) <= MAX_RECORD_BYTES, '完整目录来源及人工稿超过当前持久化容量，未裁剪保存')
    db.add(row); db.flush(); db.add(command); db.flush()
    audit(db, p, COMMAND_AUDIT, command.id, _command_anchor(command))
    audit(db, p, STATE_AUDIT, row.id, _state_anchor(row, command, row.record_hash)); db.flush()
    return copy.deepcopy(command.data['result'])


def catalogue_context(db, p, plan_id, scope_id):
    """Fixed human intent for a separately authorized future flow activity."""
    plans._iam(db, p); identity(plan_id); _lock(db, p)
    rows, _, _ = _inventory(db, p)
    matches = [row for row in rows.values() if row.data['plan_ref'] is not None
        and row.data['plan_ref']['plan_id'] == plan_id]
    need(len(matches) == 1, '范围计划缺少唯一完整人工目录绑定，未回退到旧短说明')
    row = matches[0]; document = row.data['document']
    business = next((item for item in document['businesses'] if item['key'] == scope_id), None)
    need(business is not None, '该业务不属于原确认目录', 404)
    selected = {position for group in document['allocation_groups'] if scope_id in group['business_keys']
        for position in group['input_positions']}
    questions = [question for question in document['questions'] if scope_id in question['candidate_business_keys']
        or selected.intersection(question['input_positions'])]
    value = {'schema': CONTEXT, 'draft_id': row.id, 'draft_version': row.version,
        'document_hash': row.document_hash, 'origin': copy.deepcopy(row.data['origin']['metadata']),
        'plan_ref': copy.deepcopy(row.data['plan_ref']), 'business': copy.deepcopy(business),
        'questions': copy.deepcopy(questions), 'human_intent_only': True,
        'source_facts_verified': False, 'dispatch_allowed': False}
    return {**value, 'context_hash': digest(value)}


def business_flow_source(db, p, draft_id, scope_id):
    """Resolve a confirmed catalogue row to its original flow planning input."""
    plans._iam(db, p)
    _lock(db, p)
    rows, _, _ = _inventory(db, p)
    row = _owned(rows, draft_id)
    reference = row.data['plan_ref']
    need(reference is not None, '请先保存并确认业务清单，再按已确认条目规划流程')
    origin = row.data['origin']
    document = row.data['document']
    plan = _check_plan_ref(db, p, reference, document, origin)
    business = next((item for item in document['businesses'] if item['key'] == scope_id), None)
    need(business is not None, '该业务不属于原确认目录', 404)
    selected = [scope for scope in plan.data['ledger']['scopes'] if scope['scope_id'] == scope_id]
    need(len(selected) == 1 and selected[0]['name'] == business['name'])
    _current_source(db, p, origin, lock=False)
    return {'schema': 'report-catalogue-flow-source-v316', 'producer_id': plan.producer_id,
        'revision': plan.revision, 'draft_id': row.id, 'draft_version': row.version,
        'document_hash': row.document_hash, 'plan_id': plan.id, 'plan_version': plan.version,
        'plan_hash': reference['plan_hash'], 'scope_id': scope_id, 'business_name': business['name'],
        'source_snapshot_hash': origin['metadata']['source_snapshot_hash']}


def _flow_question_context(db, p, plan_id, scope_id):
    """Read actual saved human questions for a confirmed single-business plan."""
    from . import report_catalogue_flow_context_v1 as flow_context
    plans._iam(db, p); identity(plan_id)
    need(type(scope_id) is str and 0 < len(scope_id) <= 128, '业务范围无法核对', 422)
    _lock(db, p)
    plan = plans.checked_plan(db, p, plan_id, confirmed=True)
    scopes = [item for item in plan.data['ledger']['scopes'] if item['scope_id'] == scope_id]
    need(len(scopes) == 1, '该业务不属于所选范围计划', 404)
    rows, _, _ = _inventory(db, p)
    matches = [row for row in rows.values() if row.data['plan_ref'] is not None
               and row.data['plan_ref']['plan_id'] == plan_id]
    need(len(matches) <= 1, '业务范围对应的目录记录不唯一，原资料保留')
    base = {'schema':'report-catalogue-flow-questions-v1', 'actor_id':p.user_id,
        'workspace_id':p.workspace_id, 'producer_id':plan.producer_id,
        'revision':plan.revision, 'plan_id':plan.id, 'plan_version':plan.version,
        'plan_hash':plan.data['plan']['plan_hash'], 'scope_id':scope_id,
        'business_name':scopes[0]['name']}
    if not matches:
        return base, None, []
    row = matches[0]
    actual = _check_plan_ref(db, p, row.data['plan_ref'], row.data['document'], row.data['origin'])
    need(actual.id == plan.id and actual.version == plan.version,
         '业务目录与原范围计划不一致，原资料保留')
    business = next((item for item in row.data['document']['businesses'] if item['key'] == scope_id), None)
    need(business is not None and business['name'] == scopes[0]['name'], '业务目录与范围不一致')
    return base, row, flow_context.relevant_questions(row.data['document'], scope_id)


def flow_questions(db, p, plan_id, scope_id, *, offset=0, limit=20):
    need(type(offset) is int and offset >= 0 and type(limit) is int and 1 <= limit <= 20,
         '待核事项分页参数不正确', 422)
    base, row, questions = _flow_question_context(db, p, plan_id, scope_id)
    result = {**base, 'status':'available' if row else 'no_catalogue',
        'catalogue':None if row is None else {'draft_id':row.id,'version':row.version,
            'document_hash':row.document_hash}, 'offset':offset,'limit':limit,
        'total':len(questions) if row else None, 'has_more':offset + limit < len(questions),
        'questions':copy.deepcopy(questions[offset:offset + limit]),
        'sent_to_model':False, 'source_confirmed':False}
    need(len(bytes_value(result)) <= 1024 * 1024, '待核事项本页超出显示容量，原记录未修改', 413)
    return {**result, 'context_hash':digest(result)}


def flow_question_evidence(db, p, plan_id, scope_id, question_key, *, offset=0, limit=10):
    from . import report_catalogue_flow_context_v1 as flow_context
    base, row, questions = _flow_question_context(db, p, plan_id, scope_id)
    need(row is not None, '所选范围没有可核对的业务目录', 404)
    matching = [q for q in questions if q['key'] == question_key]
    need(len(matching) == 1, '该待核事项不属于当前业务', 404)
    page = flow_context.question_evidence(row.data['origin']['projection'], matching[0], offset=offset, limit=limit)
    result = {'schema':'report-catalogue-flow-question-evidence-v1',
        **{key:base[key] for key in ('actor_id','workspace_id','producer_id','revision','plan_id','plan_version','plan_hash','scope_id')},
        'catalogue':{'draft_id':row.id,'version':row.version,'document_hash':row.document_hash},
        'question_key':question_key, **page, 'sent_to_model':False, 'source_confirmed':False}
    need(len(bytes_value(result)) <= 1024 * 1024, '原文片段本页超出显示容量，原记录未修改', 413)
    return {**result, 'evidence_hash':digest(result)}
