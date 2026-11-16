"""Owner-private typed questions: one planning call, audited source, local SDK.

Only family-neutral V252 primitives and the existing gateway/budget transport
are reused. No old lifecycle globals, IR, catalogs or historical bytes change.
"""
import base64
import copy
import hashlib
import json
from datetime import timezone, timedelta
from uuid import UUID, uuid5

import httpx
from fastapi import HTTPException
from sqlalchemy import select, text, or_, and_
from sqlalchemy.orm import Session

from . import models as m
from .contracts import digest
from .services import scoped, published, require, validate
from .leases import ownership
from .media_daily_task_adapter import current_actor
from . import pixel_question_service_v252 as primitives
from . import multimodal_question_contracts_v262 as c
from . import multimodal_question_tool_v262 as catalog
from . import multimodal_query_contracts_v262 as contract
from . import multimodal_query_facets_v262 as facets
from . import multimodal_query_compute_v262 as arithmetic

KEY = c.FAMILY
TASK_ID = 'multimodal-question-v262'
JOB_PREFIX = TASK_ID + ':'
TOOL_PREFIX = 'multimodal-question-tool-v262:'
JOB_SCHEMA = 'multimodal-question-job-v262'
NONCE = TASK_ID + '-request'
ID_NAMESPACE = UUID('5eb5cba1-54c8-5f87-8688-1427d48845d7')
CREATED = 'multimodal_question.created_v262'
QUEUED = 'multimodal_question.queued_v262'
RESERVED = 'multimodal_question.reserved_v262'
CAPTURED = 'multimodal_question.response_captured_v262'
PLANNED = 'multimodal_question.planned_v262'
SOURCED = facets.SOURCE_ACTION
OUTCOME = 'multimodal_question.answered_v262'
COMMAND = 'multimodal_question.command_v262'
TOOL_ISSUED = 'multimodal_question.tool_issued_v262'
ACTIONS = [CREATED, QUEUED, RESERVED, CAPTURED, PLANNED, SOURCED, OUTCOME, COMMAND, TOOL_ISSUED]
ERROR = '原问题、回答或独立数据依据无法核对；已保存内容保留，未重新发送模型请求'
need = primitives.need
same = primitives.same
_audit = primitives._audit
_ref = primitives._ref
_proof = primitives._proof
_model = primitives._model


def _wire(raw, model):
    need(type(raw) is bytes and len(raw) <= c.MAX_RAW, '请求正文超过128KiB或不是原始字节', 422)
    return primitives._wire(raw, model)


def query_identity(workspace_id, request_id):
    return str(uuid5(ID_NAMESPACE, TASK_ID + ':' + workspace_id + ':' + request_id))


def intrinsic(identity):
    return contract.identity(identity) and UUID(identity).version == 5


def _nonce(db, p, request_id, *, read=False):
    try:
        need(type(request_id) is str and 12 <= len(request_id) <= 100, '原请求编号长度不符', 422)
        c.unicode_text(request_id)
    except (ValueError, UnicodeError): need(False, '原请求编号含不可保存字符', 422)
    if not read: return primitives._nonce(db, p, NONCE, request_id)
    key = int(hashlib.sha256((p.workspace_id + ':' + NONCE + ':' + request_id).encode()).hexdigest()[:15], 16)
    db.execute(text('SELECT pg_advisory_xact_lock_shared(:key)'), {'key': key})


def _reader(db, p):
    current_actor(db, p)


def _load(db, p, identity, lock=False):
    row = scoped(db, m.DataRun, identity, p, lock)
    need(row.owner_id == p.user_id, code=404)
    return row


def has_trace(db, p, identity):
    row = db.scalar(select(m.DataRun).where(m.DataRun.workspace_id == p.workspace_id, m.DataRun.id == identity))
    if row is not None:
        data = row.data if type(row.data) is dict else {}
        request = data.get('request') if type(data.get('request')) is dict else {}
        if intrinsic(row.id) or KEY in data or data.get('family') == KEY or request.get('schema') == 'multimodal-question-submit-v262': return True
    if db.scalar(select(m.Audit.id).where(m.Audit.workspace_id == p.workspace_id,
            m.Audit.object_id == identity, m.Audit.action.in_(ACTIONS)).limit(1)): return True
    if db.scalar(select(m.Job.id).where(m.Job.workspace_id == p.workspace_id,
            or_(m.Job.idempotency_key.startswith(JOB_PREFIX + str(identity) + ':'),
                and_(m.Job.data['query_id'].as_string() == identity,
                    or_(m.Job.data['family'].as_string() == KEY, m.Job.data['schema'].as_string() == JOB_SCHEMA)))).limit(1)): return True
    if db.scalar(select(m.Invocation.id).where(m.Invocation.workspace_id == p.workspace_id,
            m.Invocation.idempotency_key.startswith(TOOL_PREFIX + str(identity) + ':')).limit(1)): return True
    return db.scalar(select(m.CloudCall.id).where(m.CloudCall.workspace_id == p.workspace_id,
        or_(and_(m.CloudCall.data['gateway_dispatch']['identity']['suggestion_id'].as_string() == identity,
            m.CloudCall.data['gateway_dispatch']['identity']['task_id'].as_string() == TASK_ID),
            m.CloudCall.data['query_id'].as_string() == identity)).limit(1)) is not None


def private_projection(row):
    """No caller/auth context exists in generic as_dict; reveal no private text."""
    data = row.data if type(row.data) is dict else {}
    request = data.get('request') if type(data.get('request')) is dict else {}
    execution = data.get('capability_snapshot', {}).get('execution', {}) if type(data.get('capability_snapshot')) is dict else {}
    inputs = data.get('inputs') if type(data.get('inputs')) is dict else {}
    output = data.get('output') if type(data.get('output')) is dict else {}
    dispatch = data.get('gateway_dispatch', {}).get('identity', {}) if type(data.get('gateway_dispatch')) is dict else {}
    if type(execution) is not dict: execution = {}
    if type(dispatch) is not dict: dispatch = {}
    key = getattr(row, 'idempotency_key', None)
    marker = (KEY in data or data.get('family') == KEY or data.get('schema') == JOB_SCHEMA
        or request.get('schema') == 'multimodal-question-submit-v262')
    hidden = (isinstance(row, m.DataRun) and (intrinsic(row.id) or marker or intrinsic(data.get('parent_query_id')))
        or isinstance(row, m.Job) and (marker or type(key) is str and key.startswith(JOB_PREFIX) or intrinsic(data.get('query_id')))
        or isinstance(row, m.Invocation) and (type(key) is str and key.startswith(TOOL_PREFIX)
            or intrinsic(inputs.get('query_id')) or output.get('schema') == 'multimodal-question-answer-v262'
            or execution.get('handler') == catalog.HANDLER)
        or isinstance(row, m.CloudCall) and (dispatch.get('task_id') == TASK_ID
            or data.get('query_family') == KEY or intrinsic(data.get('query_id')))
        or isinstance(row, m.Audit) and (row.action in ACTIONS
            or intrinsic(data.get('query_id')) or intrinsic(row.object_id) and 'source_snapshot_hash' in data and 'registry_hash' in data))
    if not hidden: return None
    return {'schema': 'multimodal-question-private-reference-v262', 'id': row.id, 'version': row.version,
        **({'kind': row.kind} if isinstance(row, m.Job) else {}),
        **({'status': row.status} if hasattr(row, 'status') else {}), 'reference_only': True, 'private_record': True, 'family': KEY,
        'notice': '此为私人统一成果问题，请由原提问人在原实例的专属入口查看。'}


def _set(row, state):
    row.data = {'family': KEY, KEY: copy.deepcopy(state)}
    row.version += 1


def _context(row, s):
    return {'schema': 'multimodal-question-context-v262', 'query_id': row.id, 'workspace_id': row.workspace_id,
        'actor_id': row.owner_id, 'scope': copy.deepcopy(s['scope']), 'request_hash': s['request_hash'],
        'preview_hash': s['request']['preview_hash'], 'release_pin': s['release_pin'], 'tool_pin': s['tool_pin'],
        'model_profile_id': s['request']['model_profile_id'], 'model_profile_hash': digest(s['model_snapshot']),
        'registry_hash': s['registry']['registry_hash']}


def _created(row, s):
    return {'schema': 'multimodal-question-created-v262', 'query_id': row.id, 'workspace_id': row.workspace_id,
        'actor_id': row.owner_id, 'instance_id': s['scope']['instance_id'], 'request_id': s['request']['request_id'],
        'request_hash': s['request_hash'], 'raw_sha256': s['raw_sha256'], 'context_hash': s['context_hash'],
        'registry_hash': s['registry']['registry_hash'], 'initial_job_id': s['execution_job_ids'][0]}


def _cloud_expected(row, s):
    v = s['preview']
    return {'workspace_id': row.workspace_id, 'capability_id': s['request']['model_profile_id'], 'actor_id': row.owner_id,
        'request_hash': digest(v['messages']), 'dispatch_options_hash': digest(v['dispatch_options']),
        'provider': v['model']['provider'], 'model': v['model']['model']}


def _reserved(row, s):
    return {'schema': 'multimodal-question-reserved-v262', 'query_id': row.id, 'request_hash': s['request_hash'],
        'context_hash': s['context_hash'], 'call_id': s['planning']['call_id'], 'job_id': s['planning']['job_id'],
        'messages_hash': digest(s['preview']['messages']), 'dispatch_options_hash': digest(s['preview']['dispatch_options'])}


def _calls(db, p, row):
    return list(db.scalars(select(m.CloudCall).where(m.CloudCall.workspace_id == p.workspace_id,
        m.CloudCall.data['gateway_dispatch']['identity']['suggestion_id'].as_string() == row.id).limit(2)))


def _checked_cloud(db, p, row, s):
    calls = _calls(db, p, row); need(len(calls) <= 1)
    planning = s['planning']; identity = planning['call_id']
    if identity is None:
        need(db.scalar(select(m.Audit.id).where(m.Audit.workspace_id == p.workspace_id,
            m.Audit.object_id == row.id, m.Audit.action.in_([RESERVED, CAPTURED])).limit(1)) is None
            and planning['capture'] is None)
        if not calls: return None
    else: need(len(calls) == 1 and calls[0].id == identity)
    call = calls[0]; expected = _cloud_expected(row, s)
    need(call.capability_id == expected['capability_id'] and call.reserved_tokens == s['preview']['token_bound']
        and all(call.data.get(k) == expected[k] for k in ('actor_id', 'request_hash', 'dispatch_options_hash')))
    actual = {'suggestion_id': row.id, 'job_id': planning['job_id'], 'task_id': TASK_ID, 'request_hash': s['request_hash'],
        'workspace_id': p.workspace_id, 'actor_id': row.owner_id, 'capability_id': call.capability_id}
    need(planning['job_id'] in s['execution_job_ids'] and same(call.data.get('gateway_dispatch', {}).get('identity'), actual))
    if identity is not None:
        need(call.data.get('query_id') == row.id and call.data.get('query_family') == KEY)
        _proof(db, p, RESERVED, row.id, _reserved(row, s))
    capture = planning['capture']
    if capture is not None:
        need(set(capture) == {'raw_b64', 'raw_sha256', 'receipt'})
        raw = base64.b64decode(capture['raw_b64'], validate=True)
        need(hashlib.sha256(raw).hexdigest() == capture['raw_sha256'] and capture['receipt'].get('call_id') == call.id)
        _proof(db, p, CAPTURED, row.id, {'schema': 'multimodal-question-response-captured-v262', 'query_id': row.id,
            'call_id': call.id, 'context_hash': s['context_hash'], 'raw_sha256': capture['raw_sha256'],
            'receipt_hash': digest(capture['receipt'])})
        if planning['state'] == 'parsed':
            from .cloud_business_reasoning_v197 import parse_response
            _, receipt = parse_response(raw.decode('utf-8', errors='surrogateescape'), c.model_result_schema(), capture['receipt'])
            ledger = call.data.get('receipt'); need(type(ledger) is dict)
            keys = ('provider', 'model', 'call_id', 'request_id', 'usage', 'finish_reason', 'response_choice_count')
            need(call.status in {'succeeded', 'failed'} and same({k: receipt.get(k) for k in keys}, {k: ledger.get(k) for k in keys}))
            total = receipt['usage'].get('total_tokens')
            need(call.charged_tokens == (total if type(total) is int and total >= 0 else call.reserved_tokens))
    else:
        need(db.scalar(select(m.Audit.id).where(m.Audit.workspace_id == p.workspace_id,
            m.Audit.action == CAPTURED, m.Audit.object_id == row.id)) is None)
    return call


def _plan_proof(row, s):
    return {'schema': 'multimodal-question-plan-v262', 'query_id': row.id, 'context_hash': s['context_hash'],
        'request_hash': s['request_hash'], 'plan_hash': s['plan_hash'], 'model_call_id': s['planning']['call_id'],
        'response_sha256': s['planning']['capture']['raw_sha256'], 'explanation_hash': digest(s['plan_explanation'])}


def _outcome(row, s, invocation):
    return {'schema': 'multimodal-question-answered-v262', 'query_id': row.id, 'request_hash': s['request_hash'],
        'plan_hash': s['plan_hash'], 'source_audit_id': s['source_audit_id'],
        'source_snapshot_hash': s['source_snapshot']['snapshot_hash'], 'invocation_id': invocation.id,
        'output_hash': digest(s['answer']), 'answer_hash': s['answer']['answer_hash']}


def checked(db, p, row, *, summary_only=False):
    need(row.workspace_id == p.workspace_id and row.owner_id == p.user_id, code=404)
    try:
        need(row.data.get('family') == KEY)
        s = copy.deepcopy(row.data[KEY]); need(s['schema'] == c.SCHEMA)
        body = c.Submit.model_validate(s['request']).model_dump(mode='json')
        raw = base64.b64decode(s['raw_b64'], validate=True)
        need(same(body, s['request']) and same(_wire(raw, c.Submit), body) and digest(body) == s['request_hash']
            and hashlib.sha256(raw).hexdigest() == s['raw_sha256'] and row.question == body['question']
            and row.id == query_identity(p.workspace_id, body['request_id']) and row.status in c.STATUS)
        expected_scope = {'instance_id': s['scope']['instance_id'], 'authorization_release_id': body['release_id'],
            'task_id': body['task_id'], 'entity_id': body['entity_id']}
        need(same(s['scope'], expected_scope) and same(_context(row, s), s['context']) and digest(s['context']) == s['context_hash'])
        registry = contract.checked_registry(s['registry']); need(registry['registry_hash'] == body['registry_hash'])
        need(same(s['preview']['input'], c.preview_input(body)) and s['preview']['workspace_id'] == p.workspace_id
            and s['preview']['actor_id'] == p.user_id and same(s['preview']['registry'], registry)
            and s['preview']['registry_hash'] == registry['registry_hash']
            and digest({k: v for k, v in s['preview'].items() if k != 'preview_hash'}) == body['preview_hash'])
        need(same(s['preview']['result_schema'], c.model_result_schema()) and same(s['preview']['tool'], s['tool_pin']))
        instance, release = facets._context(db, p, s['scope'], live=False)
        need(s['release_pin'] == {'configuration_hash': release.configuration_hash, 'package_id': release.package_id,
            'package_hash': _ref(db, p, m.Capability, release.package_id).content_hash})
        facets._historical_registry(db, p, s['scope'], registry)
        tool = catalog.catalog(db, p, live=False, pin=s['tool_pin']); need(tool is not None)
        proof = _proof(db, p, CREATED, row.id, _created(row, s), s['creation_audit_id'])
        need(digest(proof.data) == s['creation_hash'] and 1 <= len(s['execution_job_ids']) <= 256
            and len(set(s['execution_job_ids'])) == len(s['execution_job_ids']))
        jobs = list(db.scalars(select(m.Job).where(m.Job.workspace_id == p.workspace_id,
            or_(m.Job.data['query_id'].as_string() == row.id, m.Job.idempotency_key.startswith(JOB_PREFIX + row.id + ':')))))
        queued = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
            m.Audit.action == QUEUED, m.Audit.object_id == row.id)))
        need(len(jobs) == len(queued) == len(s['execution_job_ids']) and {j.id for j in jobs} == set(s['execution_job_ids']))
        for job in jobs:
            entries = [entry for entry in queued if entry.data.get('job_id') == job.id]
            need(job.kind == 'data_query' and job.idempotency_key.startswith(JOB_PREFIX + row.id + ':')
                and job.data.get('schema') == JOB_SCHEMA and job.data.get('family') == KEY
                and job.data.get('query_id') == row.id and job.data.get('actor_id') == row.owner_id
                and job.data.get('context_hash') == s['context_hash'] and type(job.data.get('cached_only')) is bool)
            need(len(entries) == 1 and entries[0].actor_id == row.owner_id and same(entries[0].data,
                {'query_id': row.id, 'job_id': job.id, 'context_hash': s['context_hash'], 'recovery_only': job.data['cached_only']}))
        commands = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
            m.Audit.action == COMMAND, m.Audit.object_id == row.id)))
        need(len(commands) == len(s['commands']) <= 256 and {a.id for a in commands} == {v['audit_id'] for v in s['commands']})
        for command in commands: _check_command(db, p, row, s, command)
        retries = [entry for entry in s['commands'] if entry['body']['operation'] == 'retry']
        need(len([job for job in jobs if job.data['cached_only']]) == len(retries)
            and jobs and s['execution_job_ids'][0] in {job.id for job in jobs if not job.data['cached_only']
                and job.idempotency_key == JOB_PREFIX + row.id + ':initial'})
        for entry in retries:
            need(len([job for job in jobs if job.data['cached_only'] and
                job.idempotency_key == JOB_PREFIX + row.id + ':' + entry['body']['request_id']]) == 1)
        if row.status == 'cancelled': need(s['commands'] and s['commands'][-1]['body']['operation'] == 'cancel')
        if s['plan'] is not None:
            need(s['plan_hash'] == s['plan']['plan_hash'] and s['plan']['scope'] == s['scope']
                and s['plan']['registry_hash'] == registry['registry_hash'] and s['plan']['question_hash'] == digest(row.question)
                and s['plan_hash'] == digest({k: v for k, v in s['plan'].items() if k != 'plan_hash'}))
            _proof(db, p, PLANNED, row.id, _plan_proof(row, s), s['plan_audit_id'])
        else:
            need(s['plan_hash'] is None and s['plan_audit_id'] is None and s['source_snapshot'] is None
                and db.scalar(select(m.Audit.id).where(m.Audit.workspace_id == p.workspace_id,
                    m.Audit.object_id == row.id, m.Audit.action == PLANNED)) is None)
        if s['source_snapshot'] is not None:
            source = contract.checked_snapshot(s['source_snapshot'])
            need(source['scope'] == s['scope'] and source['registry']['registry_hash'] == registry['registry_hash'])
            _proof(db, p, SOURCED, row.id, facets.source_audit_expected(row, source), s['source_audit_id'])
        else:
            need(s['source_audit_id'] is None and db.scalar(select(m.Audit.id).where(m.Audit.workspace_id == p.workspace_id,
                m.Audit.object_id == row.id, m.Audit.action == SOURCED)) is None)
        _checked_cloud(db, p, row, s)
        invocations = list(db.scalars(select(m.Invocation).where(m.Invocation.workspace_id == p.workspace_id,
            m.Invocation.idempotency_key.startswith(TOOL_PREFIX + row.id + ':'))))
        issued = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
            m.Audit.action == TOOL_ISSUED, m.Audit.object_id == row.id)))
        need(len(invocations) == len(issued) and {call.id for call in invocations} == {proof.data.get('invocation_id') for proof in issued})
        for invocation in invocations: _verify_tool(db, p, row, s, invocation)
        if s['answer'] is not None:
            need(row.status == 'succeeded' and s['tool'] is not None)
            invocation = _ref(db, p, m.Invocation, s['tool']['invocation_id'])
            need(same(s['tool'], _verify_tool(db, p, row, s, invocation)) and invocation.status == 'succeeded'
                and same(s['answer'], invocation.data['output']))
            _proof(db, p, OUTCOME, row.id, _outcome(row, s, invocation), s['outcome_audit_id'])
        else:
            need(row.status != 'succeeded' and s['outcome_audit_id'] is None
                and db.scalar(select(m.Audit.id).where(m.Audit.workspace_id == p.workspace_id,
                    m.Audit.object_id == row.id, m.Audit.action == OUTCOME)) is None)
        return s
    except HTTPException as error:
        if error.status_code == 422: need(False, ERROR)
        raise
    except (KeyError, ValueError, TypeError, AttributeError, UnicodeError, IndexError, OverflowError):
        need(False, ERROR)


def _models(db, p):
    from .cloud_provider_policy import provider_enabled
    values = []
    for row in db.scalars(select(m.Capability).where(m.Capability.workspace_id == p.workspace_id,
            m.Capability.kind == 'foundation_model', m.Capability.status == 'published').order_by(m.Capability.id)):
        execution = row.data.get('execution', {})
        if not provider_enabled(execution.get('provider')): continue
        if execution.get('provider') not in {'qwen', 'deepseek'} or execution.get('task') == 'embedding' or row.data.get('data_policy', {}).get('cloud_allowed') is not True:
            continue
        if 'report_evidence_output' in execution or 'native_schema_variant' in execution: continue
        _model(db, p, row.id)
        values.append({'id': row.id, 'hash': row.content_hash, 'version': row.version,
            'provider': execution['provider'], 'model': execution['model'], 'max_tokens': execution.get('max_tokens', 2048)})
    return values


def options(db, p, instance_id, task_id=None, entity_id=None):
    _reader(db, p); require(p, 'admin', 'operator'); instance = scoped(db, m.DomainInstance, instance_id, p)
    reasons = []; registry = None
    tool = catalog.catalog(db, p)
    if tool is None: reasons.append('尚未由管理员正常登记并发布固定统一成果问答工具')
    release = scoped(db, m.InstanceRelease, instance.active_release_id, p) if instance.active_release_id else None
    if release is None or instance.status != 'partially_active': reasons.append('实例未启用或已暂停；已保存私人问题仍可查看')
    else:
        registry = facets.registry(db, p, instance.id, release.id, task_id=task_id, entity_id=entity_id)
    models = _models(db, p)
    if not models: reasons.append('尚无已发布且允许问题规划外发的通用模型配置')
    result = {'schema': 'multimodal-question-options-v262', 'workspace_id': p.workspace_id, 'actor_id': p.user_id,
        'instance_id': instance.id, 'release_id': release.id if release else None,
        'configuration_hash': release.configuration_hash if release else None, 'task_id': task_id, 'entity_id': entity_id,
        'registry': registry, 'registry_hash': registry['registry_hash'] if registry else None,
        'tool': {'id': tool.id, 'hash': tool.content_hash, 'version': tool.release_version} if tool else None,
        'models': models, 'can_ask': not reasons, 'reasons': reasons,
        'limits': {'max_source_records': 1000, 'max_combined_source_bytes': 16 * 1024 * 1024,
            'max_question_characters': 4000, 'max_facets': 2, 'max_administrative_days': 31}}
    return {**result, 'options_hash': digest(result)}


def _messages(question, registry, reference_day, task_limited, entity_limited):
    # No UUIDs, hashes, source records, images, audio, scores, values or notes.
    public = {'facets': sorted(registry['facets']), 'timezone': 'Asia/Shanghai', 'reference_day': reference_day,
        'pixel': {'operations': ['read', 'count', 'sum', 'mean', 'min', 'max'],
            'metrics': ['candidate_pixels', 'common_valid_pixels', 'unknown_pixels'], 'time_basis': ['machine_result_registered'],
            'count_semantics': {'operation': 'count', 'metric': None, 'unit': '已登记影像成果记录',
                'meaning': '影像成果有多少条或份是成果记录计数，不是像素数量'},
            'received_semantics': {'reception': 'received', 'meaning': '已接收表示真实独立数据ACK，无须用户额外说独立'},
            'no_date_semantics': {'basis': 'machine_result_registered', 'registration_days': None, 'time_quote': None}},
        'task_sound': {'operations': ['read', 'count', 'sum'], 'metrics': ['candidate_window_count', 'candidate_class_count'],
            'time_basis': ['machine_result_registered', 'independent_data_received', 'model_completed'],
            'count_semantics': {'operation': 'count', 'metric': None, 'unit': '已登记声音处理成果记录',
                'meaning': '声音处理或声音成果有多少份或条是登记Run记录计数，不依赖候选窗口或声类数值'},
            'received_semantics': {'reception': 'received', 'meaning': '已接收表示真实独立数据ACK，无须用户额外说独立'},
            'no_date_semantics': {'basis': 'machine_result_registered', 'days': None, 'time_quote': None}}}
    rules = ['仅生成闭合typed intent，不生成数值、答案、SQL、工具调用或平台标识。',
        'selected_scope由服务器完成真实鉴权并注入，当前实例已确定；不得索要内部实例、任务、对象UUID或其映射，不得把未向模型公开内部标识列为unmet_requirements。',
        '原问题未要求日期时查询授权范围内全部已登记成果，不加日期过滤；days与time_quote都null，不得把没有行政日期列为缺项或unmet_requirements。',
        'task_limited/entity_limited为false表示不按任务/对象额外筛选，不是缺少定位；为true表示服务器已选定该任务/对象，也不需要用户提供标识或映射。',
        '每facet最多一子计划，question_quote必须逐字连续摘自原问题，grounding在该片段内；原要求未覆盖必须完整列unmet_requirements。',
        '仅使用可用facets；像素指标按原像素合同，数值计算须原句明确已接收；不能把成果份数冒像素数量。',
        '两个成果facet中的“已接收”就是实际独立数据ACK，filters.reception=received；用户不必额外写“独立接收”或内部时间字段。',
        '声音处理或声音成果有多少份、多少条使用count、metric=null，计数已登记Run成果记录；count不依赖候选数值，只有sum候选窗口或声类数量才要求received。',
        '声音处理成果Run记录不等于原音文件数、独立声音事件数、声源或动物数量；这些要求不能改为Run计数，应保留unmet_requirements或澄清。',
        '接收状态筛选与日期筛选相互独立；未要求日期时默认machine_result_registered，像素registration_days与声音days均null，grounding.time_quote=null，不因接收筛选而要求日期。',
        '原问题明确要求特定任务或对象但selected_scope尚未选定时，须澄清人用名称或选择，不能把实例范围冒充该特定范围；明确要求的日期无法确定时仍须澄清，不索要内部UUID。',
        '窗口可重叠不是时长、事件或声源数；声类不是ASR、声纹、物种或风险概率。',
        '未知拍摄/录音/采集时间、面积、现场事实、质量判定均不能推断；不可通过改写原分句躲避限制。',
        '声音日期必须明确行政时钟，time_quote仅逐字YYYY-MM-DD列表或今日/昨日；具体days按reference_day的CST解释；无明确日期时days与time_quote都null。',
        '当前实例/本任务/本对象不是日期；不默认今天，不以下载/查询时刻冒充采集时刻。',
        '无法明确表示的条件或所需来源缺失须needs_clarification或保留unmet_requirements，绝不静默忽略。']
    return [{'role': 'system', 'content': json.dumps({'task': '规划私人只读多模态登记成果问题', 'catalog': public, 'rules': rules}, ensure_ascii=False)},
        {'role': 'user', 'content': json.dumps({'question': question, 'selected_scope': {
            'instance': '当前真实实例', 'task_limited': task_limited, 'entity_limited': entity_limited}}, ensure_ascii=False)}]


def preview(db, p, instance_id, body):
    value = c.PreviewInput.model_validate(body.model_dump(mode='json') if hasattr(body, 'model_dump') else body).model_dump(mode='json')
    opts = options(db, p, instance_id, value['task_id'], value['entity_id'])
    need(opts['can_ask'], '；'.join(opts['reasons']))
    need(opts['options_hash'] == value['options_hash'] and opts['release_id'] == value['release_id']
        and opts['registry_hash'] == value['registry_hash'], '真实业务目录或版本已变化，请刷新后重新核对本次问题')
    model = _model(db, p, value['model_profile_id']); need(any(x['id'] == model.id for x in opts['models']))
    day = m.now().astimezone(timezone(timedelta(hours=8))).date().isoformat()
    from .cloud_business_reasoning_v197 import prepare
    prepared = prepare(model, _messages(value['question'], opts['registry'], day,
        value['task_id'] is not None, value['entity_id'] is not None), c.model_result_schema())
    result = {'schema': 'multimodal-question-preview-v262', 'workspace_id': p.workspace_id, 'instance_id': instance_id,
        'actor_id': p.user_id, 'input': value, 'registry': opts['registry'], 'registry_hash': opts['registry_hash'], 'tool': opts['tool'],
        'model': {'id': model.id, 'hash': model.content_hash, 'version': model.version,
            'provider': model.data['execution']['provider'], 'model': model.data['execution']['model']},
        'messages': prepared['messages'], 'result_schema': c.model_result_schema(), 'dispatch_options': prepared['options'],
        'input_bytes': prepared['text_bytes'], 'token_bound': prepared['token_bound'], 'reference_day': day,
        'notice': '明确提问将按已发布策略付费发送这一次问题规划；只发送原问题与公开目录，不发送图像、原音、分数、数据记录或人工说明。'}
    return {**result, 'preview_hash': digest(result)}


def _find_request(db, p, instance_id, request_id):
    scoped(db, m.DomainInstance, instance_id, p)
    identity = query_identity(p.workspace_id, request_id)
    proofs = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id, m.Audit.action == CREATED,
        m.Audit.data['request_id'].as_string() == request_id).limit(2)))
    need(len(proofs) <= 1)
    row = db.scalar(select(m.DataRun).where(m.DataRun.workspace_id == p.workspace_id, m.DataRun.id == identity))
    if row is None and not proofs:
        need(not has_trace(db, p, identity), ERROR)
        return None
    need(row is not None and len(proofs) == 1 and proofs[0].object_id == row.id)
    row = _load(db, p, row.id); s = checked(db, p, row)
    need(s['scope']['instance_id'] == instance_id, code=404)
    return row


def _job(db, p, row, s, request_id, *, cached_only):
    identity = m.uid(); s['execution_job_ids'].append(identity)
    db.add(m.Job(id=identity, workspace_id=p.workspace_id, kind='data_query',
        idempotency_key=JOB_PREFIX + row.id + ':' + request_id,
        data={'schema': JOB_SCHEMA, 'query_id': row.id, 'actor_id': p.user_id, 'family': KEY,
            'context_hash': s['context_hash'], 'cached_only': cached_only, 'recovery_only': cached_only}))
    _audit(db, p, QUEUED, row.id, {'query_id': row.id, 'job_id': identity,
        'context_hash': s['context_hash'], 'recovery_only': cached_only})
    return identity


def create(db, p, instance_id, body, raw):
    from .relations import lock_current_actor
    lock_current_actor(db, p); _nonce(db, p, body.request_id)
    prior = _find_request(db, p, instance_id, body.request_id)
    if prior is not None:
        need(checked(db, p, prior)['request_hash'] == digest(body.model_dump(mode='json')), '原申请编号不能更换正文')
        return view(db, p, prior)
    need(_command_proof(db, p, body.request_id) is None, '原编号已用于另一个操作')
    require(p, 'admin', 'operator'); instance = scoped(db, m.DomainInstance, instance_id, p, True)
    shown = preview(db, p, instance.id, c.preview_input(body)); need(shown['preview_hash'] == body.preview_hash, '本次发送内容已变化，请重新核对')
    value = body.model_dump(mode='json'); need(same(_wire(raw, c.Submit), value), '原始正文与已核输入不一致', 422)
    release = scoped(db, m.InstanceRelease, body.release_id, p); package = published(db, p, release.package_id, 'domain_package')
    tool = catalog.catalog(db, p); model = _model(db, p, body.model_profile_id)
    row = m.DataRun(id=query_identity(p.workspace_id, body.request_id), workspace_id=p.workspace_id,
        owner_id=p.user_id, question=body.question, status='queued', data={})
    s = {'schema': c.SCHEMA, 'request': value, 'request_hash': digest(value), 'raw_b64': base64.b64encode(raw).decode('ascii'),
        'raw_sha256': hashlib.sha256(raw).hexdigest(), 'scope': {'instance_id': instance.id, 'authorization_release_id': release.id,
            'task_id': body.task_id, 'entity_id': body.entity_id}, 'registry': copy.deepcopy(shown['registry']),
        'release_pin': {'configuration_hash': release.configuration_hash, 'package_id': package.id, 'package_hash': package.content_hash},
        'tool_pin': shown['tool'], 'model_snapshot': copy.deepcopy(model.data), 'preview': shown, 'execution_job_ids': [],
        'creation_audit_id': None, 'creation_hash': None, 'plan': None, 'plan_hash': None, 'plan_explanation': None, 'plan_audit_id': None,
        'source_snapshot': None, 'source_audit_id': None, 'source_job_id': None, 'tool': None, 'answer': None, 'outcome_audit_id': None,
        'clarifications': [], 'limitations': [], 'error': None, 'commands': [],
        'planning': {'state': 'not_started', 'call_id': None, 'capture': None, 'job_id': None}}
    s['context'] = _context(row, s); s['context_hash'] = digest(s['context'])
    db.add(row); db.flush(); _job(db, p, row, s, 'initial', cached_only=False)
    proof = _audit(db, p, CREATED, row.id, _created(row, s)); s['creation_audit_id'] = proof.id; s['creation_hash'] = digest(proof.data)
    row.data = {'family': KEY, KEY: copy.deepcopy(s)}
    db.commit()
    return view(db, p, row)


def _live(db, p, row, job):
    from .relations import lock_current_actor
    lock_current_actor(db, p); require(p, 'admin', 'operator')
    state = checked(db, p, row)
    scoped(db, m.DomainInstance, state['scope']['instance_id'], p, True)
    row = _load(db, p, row.id, True); state = checked(db, p, row)
    registry = facets.registry(db, p, state['scope']['instance_id'], state['scope']['authorization_release_id'],
        task_id=state['scope']['task_id'], entity_id=state['scope']['entity_id'])
    need(same(registry, state['registry']), '当前真实来源目录与本次已核对范围不一致；原问题保留')
    need(catalog.catalog(db, p, pin=state['tool_pin']) is not None)
    owned = ownership(db)
    need(job.workspace_id == p.workspace_id and job.id in state['execution_job_ids'] and job.kind == 'data_query'
        and job.status == 'running' and job.data.get('actor_id') == p.user_id and row.status != 'cancelled'
        and owned.get('job_id') == job.id and owned.get('lease_token') == job.lease_token,
        '本次问题没有真实有效的原后台租约，未派发新模型或工具调用')
    return state


def _save_first_response(p, query_id, raw, receipt):
    """History-only transport persistence survives cancel/actor revocation.

    Verify original request/creation/reservation anchors, not current execution
    authority. This callback never plans, reads sources, invokes tools or retries.
    """
    need(type(raw) is str)
    wire = raw.encode('utf-8', errors='surrogateescape')
    capture = {'raw_b64': base64.b64encode(wire).decode('ascii'),
        'raw_sha256': hashlib.sha256(wire).hexdigest(), 'receipt': copy.deepcopy(receipt)}
    from .db import engine
    with Session(engine()) as saved:
        row = _load(saved, p, query_id, True); s = copy.deepcopy(row.data[KEY])
        original = base64.b64decode(s['raw_b64'], validate=True)
        need(row.data.get('family') == KEY and row.id == query_identity(p.workspace_id, s['request']['request_id'])
            and same(_wire(original, c.Submit), s['request']) and digest(s['request']) == s['request_hash']
            and hashlib.sha256(original).hexdigest() == s['raw_sha256']
            and same(s['context'], _context(row, s)) and digest(s['context']) == s['context_hash'])
        _proof(saved, p, CREATED, row.id, _created(row, s), s['creation_audit_id'])
        _checked_cloud(saved, p, row, s)
        need(s['planning']['call_id'] == receipt.get('call_id'))
        if s['planning']['capture'] is not None:
            need(same(s['planning']['capture'], capture)); return
        s['planning'].update(capture=capture, state='response_saved'); _set(row, s)
        _audit(saved, p, CAPTURED, row.id, {'schema': 'multimodal-question-response-captured-v262', 'query_id': row.id,
            'call_id': receipt['call_id'], 'context_hash': s['context_hash'], 'raw_sha256': capture['raw_sha256'],
            'receipt_hash': digest(receipt)})
        saved.commit()


def _plan(db, p, row, job, cached_only):
    s = _live(db, p, row, job)
    if s['plan'] is not None: return s
    if s['planning']['capture'] is None:
        need(s['planning']['call_id'] is None and not _calls(db, p, row),
            '原模型调用可能已经发送，未自动重发；请保留原编号并核对回答')
        need(not cached_only, '恢复只允许使用原回答；没有可确认的原回答时不能再次发送')
        model = _model(db, p, s['request']['model_profile_id'])
        need(digest(model.data) == s['context']['model_profile_hash'])
        original = _messages(row.question, s['registry'], s['preview']['reference_day'],
            s['scope']['task_id'] is not None, s['scope']['entity_id'] is not None)
        from .cloud_business_reasoning_v197 import prepare
        prepared = prepare(model, original, c.model_result_schema())
        need(same(prepared['messages'], s['preview']['messages']) and same(prepared['options'], s['preview']['dispatch_options'])
            and prepared['token_bound'] == s['preview']['token_bound'])
        s['planning']['job_id'] = job.id; _set(row, s); db.commit()

        def reserved(call_id):
            current = _load(db, p, row.id); state = _live(db, p, current, job)
            current_model = _model(db, p, state['request']['model_profile_id'])
            need(current_model.content_hash == state['context']['model_profile_hash'] and state['planning']['call_id'] is None)
            call = _ref(db, p, m.CloudCall, call_id); expected = _cloud_expected(current, state)
            need(call.status == 'reserved' and call.capability_id == current_model.id
                and call.reserved_tokens == state['preview']['token_bound']
                and all(call.data.get(k) == expected[k] for k in ('actor_id', 'request_hash', 'dispatch_options_hash')))
            actual = {'suggestion_id': current.id, 'job_id': job.id, 'task_id': TASK_ID, 'request_hash': state['request_hash'],
                'workspace_id': p.workspace_id, 'actor_id': p.user_id, 'capability_id': current_model.id}
            need(same(call.data.get('gateway_dispatch', {}).get('identity'), actual))
            call.data = {**call.data, 'query_id': current.id, 'query_family': KEY}
            state['planning'].update(call_id=call_id, state='reserved'); _set(current, state)
            _audit(db, p, RESERVED, current.id, _reserved(current, state)); db.commit()

        from .gateway import cloud_json
        try:
            cloud_json(model, original, c.model_result_schema(), p, on_reserved=reserved,
                on_response=lambda raw, receipt: _save_first_response(p, row.id, raw, receipt),
                full_report=False, evidence_stage='business-reasoning-v197', dispatch_identity={
                    'suggestion_id': row.id, 'job_id': job.id, 'task_id': TASK_ID, 'request_hash': s['request_hash']})
        finally: db.expire_all()
        row = _load(db, p, row.id); s = _live(db, p, row, job)
    from .cloud_business_reasoning_v197 import parse_response
    capture = s['planning']['capture']; need(capture is not None)
    value, receipt = parse_response(base64.b64decode(capture['raw_b64'], validate=True).decode('utf-8', errors='surrogateescape'),
        c.model_result_schema(), capture['receipt'])
    from .cloud_budget import settle_paid_response
    settle_paid_response(db, s['planning']['call_id'], receipt, _cloud_expected(row, s), 'succeeded')
    if value['status'] == 'needs_clarification':
        row.status = 'needs_clarification'; s['clarifications'] = value['questions']; s['limitations'] = value['limitations']
        s['planning']['state'] = 'parsed'; _set(row, s); db.commit(); return None
    try:
        bound = contract.bind_plan(value['intent'], row.question, s['scope'], s['registry'], reference_day=s['preview']['reference_day'])
    except ValueError as error:
        row.status = 'needs_clarification'; s['clarifications'] = [str(error)]; s['planning']['state'] = 'parsed'
        _set(row, s); db.commit(); return None
    s.update(plan=bound, plan_hash=bound['plan_hash'], plan_explanation=value['explanation'])
    s['planning']['state'] = 'parsed'
    proof = _audit(db, p, PLANNED, row.id, _plan_proof(row, s)); s['plan_audit_id'] = proof.id
    _set(row, s); db.commit(); return s


def _capture_source(db, p, row, job):
    s = _live(db, p, row, job)
    if s['source_snapshot'] is not None: return s
    from .db import engine
    db.commit()
    with Session(engine()) as read_db:
        read_db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
        source = facets.capture(read_db, p, s['scope']['instance_id'], s['scope']['authorization_release_id'],
            task_id=s['scope']['task_id'], entity_id=s['scope']['entity_id'])
    row = _load(db, p, row.id); s = _live(db, p, row, job)
    need(s['source_snapshot'] is None and same(source['registry'], s['registry']))
    s['source_snapshot'] = source; s['source_job_id'] = job.id
    proof = _audit(db, p, SOURCED, row.id, facets.source_audit_expected(row, source)); s['source_audit_id'] = proof.id
    _set(row, s); db.commit(); return s


def _answer(row, s):
    local = arithmetic.compute(s['source_snapshot'], s['plan'])
    value = {'schema': 'multimodal-question-answer-v262', 'query_id': row.id, 'request_hash': s['request_hash'],
        'plan_hash': s['plan_hash'], 'source_snapshot_hash': s['source_snapshot']['snapshot_hash'],
        'source_audit_id': s['source_audit_id'], 'source_proof_hash': digest(facets.source_audit_expected(row, s['source_snapshot'])),
        'computation': local}
    value['answer_hash'] = digest(value); validate(c.answer_schema(), value)
    return value


def authorize_tool(db, p, cap, inputs, key):
    catalog.validate_manifest(cap.data); validate(c.tool_input_schema(), inputs)
    row = _load(db, p, inputs['query_id']); job = _ref(db, p, m.Job, ownership(db).get('job_id'))
    s = _live(db, p, row, job)
    need(s['plan'] is not None and s['source_snapshot'] is not None and inputs['plan_hash'] == s['plan_hash']
        and cap.id == s['tool_pin']['id'] and cap.content_hash == s['tool_pin']['hash']
        and key == TOOL_PREFIX + row.id + ':' + job.id, '工具只能读取本原问题已冻结且独立审计的来源')
    facets.verify_saved_snapshot(db, p, row, s['source_snapshot'])
    return row, s, job


def tool_execute(db, p, cap, inputs, key):
    row, s, job = authorize_tool(db, p, cap, inputs, key)
    invocation = db.scalar(select(m.Invocation).where(m.Invocation.workspace_id == p.workspace_id, m.Invocation.idempotency_key == key))
    need(invocation is not None and invocation.status == 'running' and invocation.capability_id == cap.id
        and invocation.data.get('role') == 'data' and invocation.data.get('input_hash') == digest(inputs)
        and invocation.data.get('job_id') == job.id and invocation.data.get('lease_token') == job.lease_token
        and invocation.data.get('capability_hash') == cap.content_hash)
    result = _answer(row, s)
    authorize_tool(db, p, cap, inputs, key)
    return result


def _verify_tool(db, p, row, s, invocation):
    need(s['plan'] is not None and s['source_snapshot'] is not None
        and invocation.capability_id == s['tool_pin']['id'] and invocation.data.get('role') == 'data'
        and invocation.data.get('capability_hash') == s['tool_pin']['hash']
        and same(invocation.data.get('inputs'), {'query_id': row.id, 'plan_hash': s['plan_hash']})
        and invocation.data.get('input_hash') == digest(invocation.data['inputs'])
        and digest(invocation.data.get('capability_snapshot')) == s['tool_pin']['hash']
        and invocation.data.get('job_id') in s['execution_job_ids']
        and contract.identity(invocation.data.get('lease_token'))
        and invocation.idempotency_key == TOOL_PREFIX + row.id + ':' + invocation.data['job_id'])
    expected = _tool_issued_expected(row, s, invocation)
    proofs = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == TOOL_ISSUED, m.Audit.object_id == row.id,
        m.Audit.data['invocation_id'].as_string() == invocation.id)))
    need(len(proofs) == 1 and proofs[0].actor_id == row.owner_id and same(proofs[0].data, expected))
    if invocation.status == 'succeeded':
        need(same(invocation.data.get('output'), _answer(row, s)))
        _proof(db, p, 'capability.invoke', invocation.id, {'status': 'succeeded'})
    return {'invocation_id': invocation.id, 'job_id': invocation.data['job_id'], 'capability_id': invocation.capability_id,
        'capability_hash': invocation.data['capability_hash'], 'input_hash': invocation.data['input_hash'],
        'output_hash': digest(invocation.data['output']) if invocation.status == 'succeeded' else None, 'status': invocation.status}


def _tool_issued_expected(row, s, invocation):
    return {'schema': 'multimodal-question-tool-issued-v262', 'query_id': row.id, 'invocation_id': invocation.id,
        'job_id': invocation.data['job_id'], 'capability_id': invocation.capability_id,
        'capability_hash': invocation.data['capability_hash'], 'input_hash': invocation.data['input_hash'],
        'lease_token_hash': digest(invocation.data['lease_token']), 'plan_hash': s['plan_hash'],
        'source_audit_id': s['source_audit_id'], 'context_hash': s['context_hash']}


def record_tool_issued(db, p, invocation):
    """Called by gateway before its Invocation commit; no parent-lock acquisition.

    The gateway already holds the actual authorize_tool parent locks. Only the
    new Invocation and independent linkage are added to that same transaction.
    """
    row = _load(db, p, invocation.data['inputs']['query_id']); s = copy.deepcopy(row.data[KEY])
    owned = ownership(db)
    need(row.data.get('family') == KEY and owned.get('job_id') == invocation.data['job_id']
        and owned.get('lease_token') == invocation.data['lease_token']
        and invocation.idempotency_key == TOOL_PREFIX + row.id + ':' + owned['job_id']
        and invocation.capability_id == s['tool_pin']['id'] and invocation.data['capability_hash'] == s['tool_pin']['hash']
        and invocation.data['inputs'] == {'query_id': row.id, 'plan_hash': s['plan_hash']})
    _proof(db, p, SOURCED, row.id, facets.source_audit_expected(row, s['source_snapshot']), s['source_audit_id'])
    _audit(db, p, TOOL_ISSUED, row.id, _tool_issued_expected(row, s, invocation))


def process(db, p, job):
    row = _load(db, p, job.data['query_id']); s = checked(db, p, row)
    if row.status in {'cancelled', 'succeeded'}: return row.status
    s = _live(db, p, row, job); row.status = 'running'; s['error'] = None; _set(row, s); db.commit()
    try:
        if _plan(db, p, row, job, bool(job.data.get('recovery_only')) or job.data['cached_only']) is None:
            return 'needs_clarification'
        row = _load(db, p, row.id); s = _capture_source(db, p, row, job)
        facets.verify_saved_snapshot(db, p, row, s['source_snapshot'])
        invocations = list(db.scalars(select(m.Invocation).where(m.Invocation.workspace_id == p.workspace_id,
            m.Invocation.idempotency_key.startswith(TOOL_PREFIX + row.id + ':'))))
        for invocation in invocations: _verify_tool(db, p, row, s, invocation)
        successes = [invocation for invocation in invocations if invocation.status == 'succeeded']; need(len(successes) <= 1)
        if successes: invocation = successes[0]
        else:
            need(not any(invocation.status in {'created', 'running', 'unknown'} for invocation in invocations),
                '原本机SDK调用尚不能确认，未另建计算或伪收件')
            from .gateway import invoke
            invocation = invoke(db, p, s['tool_pin']['id'], 'data', {'query_id': row.id, 'plan_hash': s['plan_hash']},
                TOOL_PREFIX + row.id + ':' + job.id)
        db.expire_all(); row = _load(db, p, row.id); s = _live(db, p, row, job)
        s['tool'] = _verify_tool(db, p, row, s, invocation)
        need(invocation.status == 'succeeded', '原本机查询尚未成功，原调用保留，请核对后显式恢复')
        facets.verify_saved_snapshot(db, p, row, s['source_snapshot'])
        s['answer'] = copy.deepcopy(invocation.data['output']); s['error'] = None; row.status = 'succeeded'
        proof = _audit(db, p, OUTCOME, row.id, _outcome(row, s, invocation)); s['outcome_audit_id'] = proof.id
        _set(row, s); db.commit(); return row.status
    except Exception as error:
        from .gateway import ModelBlocked
        from .leases import LeaseLost, ExecutionUnauthorized
        if isinstance(error, (LeaseLost, ExecutionUnauthorized)): raise
        if not isinstance(error, (HTTPException, ModelBlocked, httpx.HTTPError, ValueError, KeyError, TypeError)): raise
        db.rollback(); row = _load(db, p, row.id)
        if row.status == 'cancelled': return 'cancelled'
        s = _live(db, p, row, job)
        if s['source_snapshot'] is not None:
            saved = list(db.scalars(select(m.Invocation).where(m.Invocation.workspace_id == p.workspace_id,
                m.Invocation.idempotency_key.startswith(TOOL_PREFIX + row.id + ':')).order_by(m.Invocation.created_at)))
            for invocation in saved: _verify_tool(db, p, row, s, invocation)
            succeeded = [invocation for invocation in saved if invocation.status == 'succeeded']; need(len(succeeded) <= 1)
            if saved: s['tool'] = _verify_tool(db, p, row, s, succeeded[0] if succeeded else saved[-1])
        unknown = s['planning']['capture'] is None and (s['planning']['call_id'] is not None or bool(_calls(db, p, row)))
        row.status = 'unknown_model' if unknown else 'blocked_model' if isinstance(error, ModelBlocked) else (
            'blocked_evidence' if s['plan'] is not None and s['source_snapshot'] is None else 'blocked_integrity')
        if unknown: s['planning']['state'] = 'unknown'
        s['error'] = ('原模型请求是否已有回答尚不能确认，未重新发送；请保留原编号并核对记录' if unknown else
            error.detail if isinstance(error, HTTPException) and type(error.detail) is str else ERROR)
        _set(row, s); db.commit(); return row.status


def _actions(db, p, row, s):
    active = db.scalar(select(m.Job.id).where(m.Job.workspace_id == p.workspace_id,
        m.Job.id.in_(s['execution_job_ids']), m.Job.status.in_(['queued', 'running'])).limit(1)) is not None
    cancel = row.status in {'queued', 'running', 'unknown_model', 'blocked_model', 'blocked_evidence', 'blocked_integrity', 'failed'}
    retry = row.status not in {'succeeded', 'needs_clarification'} and not active and (
        s['planning']['capture'] is not None or s['plan'] is not None) and p.role in {'admin', 'operator'}
    return {'can_cancel': cancel, 'cancel_reason': None if cancel else '该原问题无需取消',
        'can_retry': retry, 'retry_reason': None if retry else '当前无可恢复且已停止的原回答或无执行权限；不会重新发送未知模型调用'}


def view(db, p, row):
    return _view_state(db, p, row, checked(db, p, row))


def _usage_projection(call):
    """Display only proven ledger totals; never settle or alter conservative charges."""
    receipt = call.data.get('receipt') if call is not None and type(call.data) is dict else None
    usage = receipt.get('usage') if type(receipt) is dict else None
    keys = ('prompt_tokens', 'completion_tokens', 'total_tokens')
    if (type(receipt) is not dict or receipt.get('usage_unavailable') is not False or type(usage) is not dict
            or any(type(usage.get(key)) is not int or usage[key] < 0 for key in keys)
            or usage['prompt_tokens'] + usage['completion_tokens'] != usage['total_tokens']):
        return None, True
    return {key: usage[key] for key in keys}, False


def _view_state(db, p, row, s):
    # Transaction-private projection also used while a command's immutable
    # receipt is being assembled under Actor/nonce/Query locks. It grants no
    # execution; public callers enter only through checked()/owner auth.
    integrity = None
    if s['source_snapshot'] is not None:
        try: facets.verify_saved_snapshot(db, p, row, s['source_snapshot'])
        except HTTPException as error:
            if error.status_code != 409: raise
            integrity = '原问题与已保存回答仍保留，当前原件或独立收件依据受阻；原回答不能作为已核实的当前结果使用'
    if s['tool'] is not None:
        need(same(s['tool'], _verify_tool(db, p, row, s, _ref(db, p, m.Invocation, s['tool']['invocation_id']))))
    call = _checked_cloud(db, p, row, s); capture = s['planning']['capture']; source = s['source_snapshot']
    actions = _actions(db, p, row, s)
    if integrity: actions.update(can_retry=False, retry_reason='原依据恢复并核对前不能重新处理；原回答不删除')
    usage, usage_unavailable = _usage_projection(call)
    return {'schema': 'multimodal-question-view-v262', 'id': row.id, 'version': row.version,
        'workspace_id': p.workspace_id, 'instance_id': s['scope']['instance_id'], 'actor_id': p.user_id,
        'status': 'blocked_integrity' if integrity else row.status, 'persisted_status': row.status,
        'request': s['request'], 'request_hash': s['request_hash'], 'raw_sha256': s['raw_sha256'],
        'created_at': row.created_at.astimezone(timezone.utc).isoformat(),
        'plan': s['plan'], 'plan_hash': s['plan_hash'], 'plan_explanation': s['plan_explanation'],
        'clarifications': s['clarifications'], 'limitations': s['limitations'],
        'planning': {'state': s['planning']['state'], 'model_profile_id': s['request']['model_profile_id'],
            'model_profile_hash': s['context']['model_profile_hash'], 'cloud_call_id': call.id if call else None,
            'response_sha256': capture['raw_sha256'] if capture else None, 'receipt_hash': digest(capture['receipt']) if capture else None,
            'call_status': call.status if call else None, 'reserved_tokens': call.reserved_tokens if call else None,
            'charged_tokens': call.charged_tokens if call else None, 'usage': usage, 'usage_unavailable': usage_unavailable},
        'source': None if source is None else {'audit_id': s['source_audit_id'], 'snapshot_hash': source['snapshot_hash'],
            'registry_hash': source['registry']['registry_hash'], 'as_of': source['as_of'], 'record_count': source['record_count'],
            'expected_sources_verified': False}, 'tool': s['tool'], 'saved_answer': s['answer'],
        'answer_usable': s['answer'] is not None and integrity is None, 'integrity_issue': integrity,
        'error': integrity or s['error'], 'actions': actions}


def get(db, p, instance_id, identity):
    _reader(db, p); scoped(db, m.DomainInstance, instance_id, p)
    row = _load(db, p, identity); need(checked(db, p, row)['scope']['instance_id'] == instance_id, code=404)
    return view(db, p, row)


def lookup(db, p, instance_id, request_id, expected_body_hash):
    _reader(db, p); _nonce(db, p, request_id, read=True)
    need(contract.hashed(expected_body_hash), '原请求摘要不是标准SHA256', 422)
    row = _find_request(db, p, instance_id, request_id)
    if row is not None: need(checked(db, p, row)['request_hash'] == expected_body_hash, '原编号与正文摘要不一致')
    return {'schema': 'multimodal-question-request-recovery-v262', 'found': row is not None,
        'state': 'found' if row else 'unknown', 'request_id': request_id, 'expected_body_hash': expected_body_hash,
        'query': view(db, p, row) if row else None}


def summary(db, p, row):
    s = checked(db, p, row, summary_only=True)
    return {'id': row.id, 'version': row.version, 'status': row.status,
        'created_at': row.created_at.astimezone(timezone.utc).isoformat(), 'question': row.question,
        'scope': s['scope'], 'model_profile_id': s['request']['model_profile_id'],
        'has_saved_answer': s['answer'] is not None, 'has_saved_plan': s['plan'] is not None}


def history(db, p, instance_id, limit=20, before_id=None):
    _reader(db, p); scoped(db, m.DomainInstance, instance_id, p)
    need(type(limit) is int and 1 <= limit <= 20, '单页须为1至20条', 422)
    missing = db.scalar(select(m.Audit.id).outerjoin(m.DataRun, and_(m.DataRun.id == m.Audit.object_id,
        m.DataRun.workspace_id == m.Audit.workspace_id)).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.actor_id == p.user_id, m.Audit.action == CREATED, m.Audit.data['instance_id'].as_string() == instance_id,
        m.DataRun.id.is_(None)).limit(1)); need(missing is None)
    unsigned = db.scalar(select(m.DataRun.id).outerjoin(m.Audit, and_(m.Audit.object_id == m.DataRun.id,
        m.Audit.workspace_id == m.DataRun.workspace_id, m.Audit.action == CREATED)).where(m.DataRun.workspace_id == p.workspace_id,
        m.DataRun.owner_id == p.user_id, or_(m.DataRun.data[KEY]['scope']['instance_id'].as_string() == instance_id,
            m.DataRun.id.like('________-____-5___-%')), m.Audit.id.is_(None)).limit(1)); need(unsigned is None)
    statement = select(m.DataRun).join(m.Audit, and_(m.Audit.object_id == m.DataRun.id, m.Audit.workspace_id == m.DataRun.workspace_id)).where(
        m.DataRun.workspace_id == p.workspace_id, m.DataRun.owner_id == p.user_id, m.Audit.actor_id == p.user_id,
        m.Audit.action == CREATED, m.Audit.data['instance_id'].as_string() == instance_id)
    if before_id is not None:
        prior = _load(db, p, before_id); need(checked(db, p, prior)['scope']['instance_id'] == instance_id, code=404)
        statement = statement.where(or_(m.DataRun.created_at < prior.created_at,
            and_(m.DataRun.created_at == prior.created_at, m.DataRun.id < prior.id)))
    rows = list(db.scalars(statement.order_by(m.DataRun.created_at.desc(), m.DataRun.id.desc()).limit(limit + 1)))
    return {'schema': 'multimodal-question-list-v262', 'workspace_id': p.workspace_id, 'instance_id': instance_id,
        'records': [summary(db, p, row) for row in rows[:limit]], 'has_more': len(rows) > limit,
        'next_before_id': rows[limit - 1].id if len(rows) > limit else None}


def _command_proof(db, p, request_id):
    entries = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id, m.Audit.action == COMMAND,
        m.Audit.data['request_id'].as_string() == request_id).limit(2)))
    need(len(entries) <= 1)
    return entries[0] if entries else None


def _check_command(db, p, row, s, proof):
    need(proof.actor_id == p.user_id, code=404)
    matches = [entry for entry in s['commands'] if entry['audit_id'] == proof.id]; need(len(matches) == 1)
    value = matches[0]; body = c.Command.model_validate(value['body']).model_dump(mode='json')
    need(set(value) == {'audit_id', 'body', 'body_hash', 'raw_b64', 'raw_sha256', 'result'}
        and digest(body) == value['body_hash'] and body['expected_request_hash'] == s['request_hash'])
    raw = base64.b64decode(value['raw_b64'], validate=True)
    need(hashlib.sha256(raw).hexdigest() == value['raw_sha256'] and same(_wire(raw, c.Command), body))
    result = value['result']
    expected = {'schema': 'multimodal-question-command-audit-v262', 'query_id': row.id,
        'instance_id': s['scope']['instance_id'], 'actor_id': p.user_id, 'request_id': body['request_id'],
        'body_hash': value['body_hash'], 'raw_sha256': value['raw_sha256'], 'request_hash': s['request_hash'], 'result_hash': digest(result)}
    need(proof.object_id == row.id and same(proof.data, expected)
        and set(result) == {'schema', 'operation', 'request_id', 'body_hash', 'command_audit_id', 'query'}
        and result['schema'] == 'multimodal-question-command-result-v262' and result['command_audit_id'] == proof.id
        and result['request_id'] == body['request_id'] and result['operation'] == body['operation']
        and result['body_hash'] == value['body_hash'] and result['query']['id'] == row.id
        and result['query']['request_hash'] == s['request_hash'] and same(result['query']['request'], s['request']))
    return copy.deepcopy(result)


def command(db, p, instance_id, identity, operation, body, raw):
    from .relations import lock_current_actor
    lock_current_actor(db, p); _nonce(db, p, body.request_id)
    need(operation == body.operation, '操作路径与正文不一致', 422)
    row = _load(db, p, identity); s = checked(db, p, row)
    need(s['scope']['instance_id'] == instance_id, code=404)
    prior = _command_proof(db, p, body.request_id)
    if prior is not None:
        need(prior.object_id == row.id); result = _check_command(db, p, row, s, prior)
        need(result['body_hash'] == digest(body.model_dump(mode='json')), '原操作编号不能更换正文')
        return result
    need(not has_trace(db, p, query_identity(p.workspace_id, body.request_id)), '原编号已用于另一问题')
    row = _load(db, p, row.id, True); s = checked(db, p, row)
    need(row.version == body.version and s['request_hash'] == body.expected_request_hash, '问题版本已变化，请刷新后操作')
    need(same(_wire(raw, c.Command), body.model_dump(mode='json')), '原操作正文不一致', 422)
    actions = _actions(db, p, row, s); need(actions['can_' + operation], actions[operation + '_reason'])
    need(len(s['commands']) < 256, '原问题操作记录已达有界上限，历史仍保留')
    if operation == 'cancel': row.status = 'cancelled'
    else:
        opts = options(db, p, instance_id, s['scope']['task_id'], s['scope']['entity_id'])
        need(opts['can_ask'] and opts['release_id'] == s['request']['release_id'] and same(opts['registry'], s['registry']),
            '当前来源或授权已变化；可以查看原问题，不能继承旧授权重新处理')
        if s['source_snapshot'] is not None: facets.verify_saved_snapshot(db, p, row, s['source_snapshot'])
        row.status = 'queued'; s['error'] = None; _job(db, p, row, s, body.request_id, cached_only=True)
    _set(row, s); db.flush()
    proof = m.Audit(id=m.uid(), workspace_id=p.workspace_id, actor_id=p.user_id, action=COMMAND, object_id=row.id, data={})
    result = {'schema': 'multimodal-question-command-result-v262', 'operation': operation, 'request_id': body.request_id,
        'body_hash': digest(body.model_dump(mode='json')), 'command_audit_id': proof.id, 'query': _view_state(db, p, row, s)}
    proof.data = {'schema': 'multimodal-question-command-audit-v262', 'query_id': row.id, 'instance_id': instance_id,
        'actor_id': p.user_id, 'request_id': body.request_id, 'body_hash': result['body_hash'],
        'raw_sha256': hashlib.sha256(raw).hexdigest(), 'request_hash': s['request_hash'], 'result_hash': digest(result)}
    s['commands'].append({'audit_id': proof.id, 'body': body.model_dump(mode='json'), 'body_hash': result['body_hash'],
        'raw_b64': base64.b64encode(raw).decode('ascii'), 'raw_sha256': hashlib.sha256(raw).hexdigest(), 'result': copy.deepcopy(result)})
    row.data = {'family': KEY, KEY: copy.deepcopy(s)}; db.add(proof); db.commit(); return result


def command_lookup(db, p, instance_id, identity, request_id, expected_body_hash):
    _reader(db, p); _nonce(db, p, request_id, read=True)
    need(contract.hashed(expected_body_hash), '原操作摘要不是标准SHA256', 422)
    row = _load(db, p, identity); s = checked(db, p, row); need(s['scope']['instance_id'] == instance_id, code=404)
    prior = _command_proof(db, p, request_id); result = None
    if prior is not None:
        need(prior.object_id == row.id); result = _check_command(db, p, row, s, prior)
        need(result['body_hash'] == expected_body_hash, '原操作摘要不符')
    elif any(entry['body']['request_id'] == request_id for entry in s['commands']): need(False)
    return {'schema': 'multimodal-question-command-recovery-v262', 'found': prior is not None,
        'state': 'found' if prior else 'unknown', 'request_id': request_id, 'expected_body_hash': expected_body_hash, 'result': result}


def safe_reference(db, p, row):
    _reader(db, p); row = _load(db, p, row.id); s = checked(db, p, row, summary_only=True)
    return {'schema': 'data-query-reference-v144', 'id': row.id, 'version': row.version, 'status': row.status,
        'question': row.question, 'created_at': row.created_at.astimezone(timezone.utc).isoformat(), 'reference_only': True, 'family': KEY,
        'context': {'instance_id': s['scope']['instance_id'], 'task_id': s['scope']['task_id'],
            'detail_path': '/api/workspace/instances/' + s['scope']['instance_id'] + '/multimodal-questions/' + row.id},
        'notice': '这是本人统一成果问题；通用字段查询不能改写、派生或采用其候选结果。'}


def fail(db, p, job, error):
    """Trusted worker-only negative status write; no source or paid-call repair."""
    row = _load(db, p, job.data['query_id'], True)
    owned = ownership(db)
    need(owned.get('job_id') == job.id and owned.get('lease_token') == job.lease_token
        and job.status == 'running' and job.workspace_id == p.workspace_id)
    s = copy.deepcopy(row.data[KEY]); need(row.data.get('family') == KEY and job.id in s['execution_job_ids'])
    _proof(db, p, CREATED, row.id, _created(row, s), s['creation_audit_id'])
    if row.status not in {'succeeded', 'cancelled', 'needs_clarification'}:
        row.status = 'failed'; s['error'] = ERROR; _set(row, s)
