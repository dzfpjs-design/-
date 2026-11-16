"""A bounded, owner-only view of one already saved Current allocation answer.

This verifies the selected database record and its independent saved anchors.
It does not qualify a report, inspect files, rebuild a Plan, or authorize use of
the answer. The ordinary full read and all fresh write gates remain separate.
"""
import base64
import copy
import hashlib
import json
import math
import re
from typing import Annotated

from fastapi import Depends, HTTPException
from sqlalchemy import or_, select

from .contracts import digest
from .models import Audit, CloudCall, Job
from .security import Principal
from .signal_dual_read_session import read_session
from .snapshot_authentication_v141 import snapshot_authenticated

SCHEMA = 'catalogue-current-allocation-saved-record-v260'
ERROR = '本份已保存机答、原命令或独立回执无法核对；原记录保留，未重发或修写'
MAX_COMMANDS = 256
MAX_STATES = 512
MAX_RECORD_BYTES = 64 * 1024 * 1024
MAX_PROJECTION_BYTES = 16 * 1024 * 1024
MAX_ARGUMENT_BYTES = 1024 * 1024
SHA = re.compile(r'^[a-f0-9]{64}$')
ReadP = Annotated[Principal, Depends(snapshot_authenticated)]
ReadDB = Annotated[object, Depends(read_session)]
VERIFICATION = {'saved_record_checked': True, 'source_revalidated': False,
    'files_revalidated': False, 'current_configuration_revalidated': False,
    'semantic_quality_verified': False, 'can_apply': False, 'can_publish': False}


def _need(value):
    if not value:
        raise HTTPException(409, ERROR)


def _hash(value):
    return type(value) is str and SHA.fullmatch(value) is not None


def _json_bytes(value, maximum):
    # Bound both structure and serialized size. No coercion, surrogate strings,
    # non-finite values, or duplicate-key reparsing is accepted as saved JSON.
    stack = [(value, 0)]; nodes = 0
    while stack:
        item, depth = stack.pop(); nodes += 1
        _need(depth <= 64 and nodes <= 2_000_000)
        kind = type(item)
        if kind is dict:
            _need(all(type(k) is str and '\x00' not in k for k in item))
            stack.extend((v, depth + 1) for v in item.values())
        elif kind is list:
            stack.extend((v, depth + 1) for v in item)
        elif kind is str:
            _need('\x00' not in item); item.encode('utf-8', 'strict')
        elif kind is float:
            _need(math.isfinite(item))
        else:
            _need(item is None or kind in {bool, int})
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode('utf-8', 'strict')
    _need(len(raw) <= maximum)
    return raw


def _same(left, right):
    return _json_bytes(left, MAX_RECORD_BYTES) == _json_bytes(right, MAX_RECORD_BYTES)


def _loads(raw):
    _need(type(raw) is bytes and 0 < len(raw) <= MAX_ARGUMENT_BYTES)
    def pairs(items):
        result = {}
        for key, value in items:
            _need(key not in result); result[key] = value
        return result
    def constant(_value):
        raise ValueError('nonfinite')
    value = json.loads(raw.decode('utf-8', 'strict'), object_pairs_hook=pairs,
        parse_constant=constant)
    _json_bytes(value, MAX_ARGUMENT_BYTES)
    return value


def _rows(db, statement, maximum):
    values = list(db.scalars(statement.limit(maximum + 1)
        .execution_options(populate_existing=True)))
    _need(len(values) <= maximum)
    return values


def _selected(db, p, consumer_id, c):
    row = db.scalar(select(c.Consumer).where(c.Consumer.workspace_id == p.workspace_id,
        c.Consumer.id == consumer_id).execution_options(populate_existing=True))
    # Discovery is not actor-filtered: wrong-owner anchors cannot disappear and
    # make a damaged selected record appear absent. No foreign body is returned.
    commands = _rows(db, select(c.Command).where(c.Command.workspace_id == p.workspace_id,
        c.Command.consumer_id == consumer_id), MAX_COMMANDS)
    refs = row.data.get('command_refs') if row is not None and type(row.data) is dict else []
    _need(type(refs) is list and len(refs) <= MAX_COMMANDS)
    ref_ids = []
    for ref in refs:
        _need(type(ref) is dict and set(ref) == {'id', 'request_id', 'operation', 'wire_sha256'})
        c.identity(ref['id']); ref_ids.append(ref['id'])
    ids = set(ref_ids) | {command.id for command in commands}
    audits = _rows(db, select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action.in_([c.STATE_AUDIT, c.COMMAND_AUDIT]),
        or_(Audit.object_id == consumer_id, Audit.object_id.in_(ids),
            Audit.data['consumer_id'].as_string() == consumer_id)), MAX_STATES + MAX_COMMANDS)
    jobs = _rows(db, select(Job).where(Job.workspace_id == p.workspace_id,
        or_(Job.data['consumer_id'].as_string() == consumer_id,
            Job.id == (row.job_id if row is not None else consumer_id))), 2)
    calls = _rows(db, select(CloudCall).where(CloudCall.workspace_id == p.workspace_id,
        or_(CloudCall.id == (row.reserved_call_id if row is not None else consumer_id),
            CloudCall.data['gateway_dispatch']['identity']['suggestion_id'].as_string() == consumer_id,
            CloudCall.data['gateway_dispatch']['identity']['job_id'].as_string().in_([j.id for j in jobs]))), 2)
    if row is None:
        _need(not commands and not audits and not jobs and not calls)
        raise HTTPException(404, '原机答不存在或不属于当前工作区')
    if row.actor_id != p.user_id:
        raise HTTPException(403, '不能读取其他账号的已保存机答')
    return row, commands, audits, jobs, calls


def _commands(row, commands, audits, c, current):
    refs = row.data['command_refs']
    _need(refs and len({ref['id'] for ref in refs}) == len(refs)
        and {v.id for v in commands} == {v['id'] for v in refs})
    by_id = {v.id: v for v in commands}; start = None; checked = set()
    for ref in refs:
        command = by_id[ref['id']]
        _need(command.actor_id == row.actor_id and command.workspace_id == row.workspace_id
            and command.consumer_id == row.id and command.method == 'POST'
            and command.command_hash == c.h(c._command_pin(command))
            and type(command.data) is dict and set(command.data) == {'schema', 'result'}
            and command.data['schema'] == c.COMMAND)
        if command.operation == 'start':
            model = current.StartBody; suffix = '/start-allocation'
        elif command.operation == 'recover-saved':
            model = c.RecoverBody; suffix = '/' + row.id + '/recover-saved'
        elif command.operation == 'apply-current-allocation':
            from .catalogue_current_allocation_apply_v260 import ApplyBody
            model = ApplyBody; suffix = '/' + row.id + '/apply-current-allocation'
        elif command.operation == 'end-current-interrupted':
            from .catalogue_current_allocation_end_v260 import EndInterruptedBody
            model = EndInterruptedBody; suffix = '/' + row.id + '/end-current-interrupted'
        else:
            _need(False)
        body, exact, sha = c.read_wire(command.exact_body.encode('utf-8', 'strict'), model)
        _need(exact == command.exact_body and sha == command.body_sha256
            and command.path == c.PREFIX + suffix and body.request_id == command.request_id
            and _same(ref, {'id': command.id, 'request_id': command.request_id,
                'operation': command.operation, 'wire_sha256': sha}))
        anchors = [a for a in audits if a.action == c.COMMAND_AUDIT and a.object_id == command.id]
        _need(len(anchors) == 1 and anchors[0].actor_id == row.actor_id
            and _same(anchors[0].data, c._command_anchor(command)))
        checked.add(anchors[0].id)
        result = command.data['result']
        extra = ({'application'} if command.operation == 'apply-current-allocation' else
            {'interrupted_end'} if command.operation == 'end-current-interrupted' else set())
        _need(type(result) is dict and set(result) == {'schema', 'id', 'version', 'operation', 'status', 'job_id'} | extra
            and result['schema'] == c.RESULT and result['id'] == row.id and result['job_id'] == row.job_id
            and result['operation'] == command.operation and type(result['version']) is int
            and 1 <= result['version'] <= row.version
            and result['status'] in {'queued', 'running', 'response_unknown', 'review', 'needs_clarification', 'failed'})
        if command.operation == 'start':
            _need(start is None and command.request_id == row.request_id)
            start = command, body
    _need(start is not None)
    return start, by_id, checked


def _states(row, audits, commands, checked, c):
    history = sorted((a for a in audits if a.action == c.STATE_AUDIT),
        key=lambda a: a.data.get('version', 0) if type(a.data) is dict else 0)
    _need(type(row.version) is int and 1 <= row.version <= MAX_STATES and len(history) == row.version)
    refs = row.data['command_refs']; previous = None; previous_refs = []
    for version, event in enumerate(history, 1):
        data = event.data
        _need(event.actor_id == row.actor_id and event.object_id == row.id
            and type(data) is dict and set(data) == {'schema', 'consumer_id', 'version',
                'previous_state_hash', 'state_hash', 'command_refs'}
            and data['schema'] == c.STATE and data['consumer_id'] == row.id
            and type(data['version']) is int and data['version'] == version
            and data['previous_state_hash'] == previous and _hash(data['state_hash']))
        saved = data['command_refs']
        _need(type(saved) is list and saved and len(previous_refs) <= len(saved) <= len(refs)
            and _same(saved, refs[:len(saved)]) and _same(previous_refs, saved[:len(previous_refs)]))
        for ref in saved[len(previous_refs):]:
            _need(commands[ref['id']].data['result']['version'] == version)
        previous_refs = saved; previous = data['state_hash']; checked.add(event.id)
    _need(_same(previous_refs, refs) and checked == {a.id for a in audits})
    pin = {key: getattr(row, key) for key in ('id', 'workspace_id', 'actor_id', 'request_id',
        'producer_id', 'model_id', 'grant_hash', 'revision', 'version', 'status', 'job_id', 'reserved_call_id', 'data')}
    _need(previous == hashlib.sha256(_json_bytes(pin, MAX_RECORD_BYTES)).hexdigest())
    return previous


def _metadata(row, start, p, c, current):
    preview = row.data['preview']; outer = preview['preparation']
    _need(type(outer) is dict and set(outer) == current._FIELDS and outer['schema'] == current.PREPARATION
        and outer['preparation_hash'] == digest({k: v for k, v in outer.items() if k != 'preparation_hash'}))
    prep = outer['native_preparation']; spec = prep['generation_contract']
    _need(prep['schema'] == 'report-business-catalogue-allocation-preparation-v213'
        and prep['preparation_hash'] == digest({k: v for k, v in prep.items() if k != 'preparation_hash'})
        and prep['generation_contract_hash'] == digest(spec))
    admission = current.check_admission(outer['admission_basis'])
    scope = current.check_scope(outer['current_scope'], admission, prep)
    _need(preview['schema'] == current.PREVIEW and preview['generation_profile'] == current.PROFILE
        and preview['workspace_id'] == row.workspace_id and preview['actor_id'] == row.actor_id
        and preview['preview_hash'] == digest({k: v for k, v in preview.items() if k != 'preview_hash'})
        and all(preview[k] is False for k in c.FALSE_FLAGS)
        and _same(preview['admission_basis'], admission) and _same(preview['current_scope'], scope))
    command, body = start; saved = current.PreviewBody.model_validate(preview['body']).model_dump()
    _need(_same(saved, {k: body.model_dump()[k] for k in current.PreviewBody.model_fields})
        and body.preview_hash == preview['preview_hash'] and body.request_hash == prep['request_hash']
        and body.plan_hash == prep['plan_hash'] and body.batch_hash == prep['batch_hash']
        and body.current_scope_hash == scope['current_scope_hash'] and body.model_id == row.model_id
        and body.binding_id == admission['binding_id'] and body.binding_record_hash == admission['binding_record_hash']
        and body.expected_configuration_hash == admission['target_configuration']['configuration_hash']
        and row.revision == preview['revision'] == admission['target_configuration']['revision']
        and row.producer_id == prep['producer_id'] == admission['source_pins']['producer_id']
        and row.grant_hash == preview['budget']['grant_hash'])
    cap = preview['capability']
    _need(cap['id'] == row.model_id and cap['workspace_id'] == p.workspace_id
        and cap['content_hash'] == digest(cap['data']) and spec['model_hash'] == cap['content_hash'])
    for key in ('request_hash', 'messages_hash', 'source_hash', 'catalog_hash', 'plan_hash', 'batch_hash'):
        _need(_hash(prep[key]))
    return prep, spec, scope


def _call(row, calls, prep, spec, scope, c, current):
    _need(len(calls) == 1 and row.reserved_call_id is not None)
    call = calls[0]; data = call.data
    identity = {'suggestion_id': row.id, 'job_id': row.job_id,
        'task_id': current.TASK_PREFIX + scope['current_scope_hash'], 'request_hash': prep['request_hash'],
        'workspace_id': row.workspace_id, 'actor_id': row.actor_id, 'capability_id': row.model_id}
    dispatch = data.get('gateway_dispatch'); summary = prep['input_summary']
    _need(type(dispatch) is dict and set(dispatch) == {'schema', 'identity', 'attempt_id', 'reservation_request_hash'}
        and dispatch['schema'] == 'cloud-dispatch-v130' and _same(dispatch['identity'], identity)
        and dispatch['reservation_request_hash'] == prep['request_hash'])
    c.identity(dispatch['attempt_id'])
    _need(call.id == row.reserved_call_id and call.workspace_id == row.workspace_id
        and call.capability_id == row.model_id and data.get('actor_id') == row.actor_id
        and call.status == 'succeeded' and type(call.reserved_tokens) is int
        and call.reserved_tokens == summary['token_bound'] and 1 <= call.reserved_tokens <= 1_000_000
        and type(call.charged_tokens) is int and call.charged_tokens >= 0
        and data.get('request_hash') == prep['request_hash']
        and data.get('dispatch_options_hash') == digest(spec['dispatch_options'])
        and data.get('request_endpoint') == spec['endpoint']
        and data.get('delivery') == spec['delivery']
        and type(data.get('request_body_bytes')) is int
        and data['request_body_bytes'] == summary['prepared_request_bytes']
        and data.get('input_policy_hash') == digest(spec['input_policy'])
        and data.get('schema_proof_hash') == digest(spec['schema_proof'])
        and _same(row.data.get('call_pin'), c._call_pin(call))
        and row.data['dispatch_started'] is True and _same(row.data['dispatch_identity'], identity))
    from . import report_business_catalogue_allocation_native_v213 as native
    _need(spec['delivery'] == native.DELIVERY and spec['endpoint'] == native.ENDPOINT
        and spec['evidence_stage'] == native.STAGE and spec['input_policy']['single_batch'] is True)
    binding = {'schema': 'report-evidence-journal-binding-v2', 'actor_id': row.actor_id,
        'workspace_id': row.workspace_id, 'draft_id': row.id, 'stage': 'reduce', 'reserved_call_id': call.id,
        'source_hash': prep['source_hash'], 'catalog_hash': prep['catalog_hash'],
        'generation_hash': digest({'published_model_hash': row.data['preview']['capability']['content_hash'], 'contract': spec}),
        'dispatch_hash': digest(identity), 'messages_hash': prep['messages_hash']}
    return call, binding


def _answer(row, call, binding, prep, spec):
    response = row.data['response']; projection = row.data['projection']
    encoded = row.data['allocation_raw_arguments_b64']
    _need(type(encoded) is str and len(encoded) <= 4 * ((MAX_ARGUMENT_BYTES + 2) // 3))
    raw = base64.b64decode(encoded, validate=True)
    _need(base64.b64encode(raw).decode('ascii') == encoded)
    parsed = _loads(raw); raw_sha = hashlib.sha256(raw).hexdigest()
    _need(type(response) is dict and set(response) == {'receipt', 'capture', 'descriptor', 'binding', 'body_sha256', 'arguments_sha256'}
        and response['arguments_sha256'] == raw_sha and _hash(response['body_sha256'])
        and _same(response['binding'], binding))
    receipt = response['receipt']; capture = response['capture']; descriptor = response['descriptor']
    _need(type(receipt) is dict and _same(receipt, call.data.get('receipt'))
        and receipt.get('call_id') == call.id and receipt.get('provider') == 'qwen'
        and receipt.get('model') == 'qwen3.8-max-0902' and receipt.get('arguments_sha256') == raw_sha
        and receipt.get('response_complete') is True and receipt.get('envelope_valid') is True
        and receipt.get('finish_reason') == 'stop')
    policy = dict(spec['input_policy']); _need(policy.pop('single_batch') is True)
    _need(_same(receipt.get('evidence_profile'), policy))
    ctx = {'binding': binding, 'request_hash': prep['request_hash'], 'endpoint': spec['endpoint'],
        'delivery': spec['delivery'], 'schema_proof_hash': digest(spec['schema_proof']),
        'profile_hash': digest(policy), 'token_bound': prep['input_summary']['token_bound'], 'max_bytes': spec['max_response_bytes']}
    _need(type(capture) is dict and capture.get('schema') == 'report-evidence-http-capture-state-v1'
        and capture.get('context_hash') == digest(ctx) and capture.get('state') == 'complete'
        and capture.get('capture_hash') == digest({k: v for k, v in capture.items() if k != 'capture_hash'})
        and capture.get('ready') is True and capture.get('response_complete') is True
        and capture.get('content_encoding_safe') is True and capture.get('can_adopt') is False
        and capture.get('body_sha256') == response['body_sha256']
        and receipt.get('capture_hash') == capture['capture_hash']
        and receipt.get('request_id') == capture.get('request_id'))
    _need(type(descriptor) is dict and descriptor.get('schema') == 'report-evidence-journal-descriptor-v2'
        and descriptor.get('descriptor_hash') == digest({k: v for k, v in descriptor.items() if k != 'descriptor_hash'})
        and _same(descriptor.get('binding'), binding) and descriptor.get('binding_hash') == digest(binding)
        and descriptor.get('consumable') is True and descriptor.get('quarantine_reasons') == []
        and descriptor.get('wire_sha256') == response['body_sha256']
        and descriptor.get('wire_bytes') == capture.get('body_bytes')
        and descriptor.get('receipt_status') == 'saved')
    receipt_raw = json.dumps(receipt, sort_keys=True, ensure_ascii=True,
        separators=(',', ':'), allow_nan=False).encode('ascii')
    _need(descriptor.get('receipt_sha256') == hashlib.sha256(receipt_raw).hexdigest()
        and descriptor.get('receipt_bytes') == len(receipt_raw))
    fields = {'schema', 'source_pins', 'plan_hash', 'batch_hash', 'batch_ordinal', 'base_draft_ref',
        'catalogue_review_hash', 'raw_arguments_sha256', 'raw_wire', 'output_schema_hash', 'decisions',
        'batch_decisions_complete', 'review_required', 'flags', 'projection_hash'}
    _need(type(projection) is dict and set(projection) == fields
        and projection['schema'] == 'report-business-catalogue-allocation-projection-v213'
        and projection['projection_hash'] == hashlib.sha256(_json_bytes(
            {k: v for k, v in projection.items() if k != 'projection_hash'}, MAX_PROJECTION_BYTES)).hexdigest()
        and projection['raw_arguments_sha256'] == raw_sha and _same(projection['raw_wire'], parsed)
        and projection['plan_hash'] == prep['plan_hash'] and projection['batch_hash'] == prep['batch_hash']
        and projection['batch_ordinal'] == prep['batch_ordinal']
        and projection['output_schema_hash'] == digest(spec['output_schema'])
        and projection['batch_decisions_complete'] is True and projection['review_required'] is True)
    from .report_business_catalogue_allocation_plan_v213 import FLAGS
    _need(_same(projection['flags'], FLAGS))
    _need(type(parsed) is dict and set(parsed) == {'schema', 'decisions'}
        and parsed['schema'] == 'report-business-catalogue-allocation-output-v213'
        and type(parsed['decisions']) is dict and 1 <= len(parsed['decisions']) <= 80)
    rows = projection['decisions']; keys = list(parsed['decisions'])
    _need(type(rows) is list and len(rows) == len(keys)
        and len({v['decision_key'] for v in rows}) == len(rows)
        and {v['decision_key'] for v in rows} == set(keys))
    available = {v['key'] for v in row.data['preview']['allocation_evidence']['plan']['businesses']}
    for decision in rows:
        key = decision['decision_key']; value = parsed['decisions'][key]
        _need(type(key) is str and re.fullmatch(r'p[0-9]{6}', key) is not None
            and type(decision['input_position']) is int and decision['input_position'] == int(key[1:])
            and 0 <= decision['input_position'] < 1920 and decision['platform_derived'] is True
            and decision['review_required'] is True and _same(decision['raw_decision'], value)
            and type(value) is dict and set(value) == {'disposition', 'business_keys', 'reason', 'questions'}
            and value['disposition'] in {'assign', 'background', 'pending'}
            and type(value['business_keys']) is list and len(value['business_keys']) <= 64
            and all(type(k) is str and k in available for k in value['business_keys'])
            and len(set(value['business_keys'])) == len(value['business_keys'])
            and (value['disposition'] != 'assign' or bool(value['business_keys']))
            and (value['disposition'] != 'background' or not value['business_keys'])
            and type(value['reason']) is str and bool(value['reason'].strip()) and len(value['reason']) <= 2000
            and type(value['questions']) is list and len(value['questions']) <= 4)
        for question in value['questions']:
            _need(type(question) is dict and set(question) == {'kind', 'question'}
                and question['kind'] in {'ownership', 'missing_info'}
                and (value['disposition'] == 'pending' or question['kind'] == 'missing_info')
                and type(question['question']) is str and bool(question['question'].strip())
                and len(question['question']) <= 4000)
        _need(value['disposition'] != 'pending' or any(q['kind'] == 'ownership' for q in value['questions']))
    unavailable = receipt.get('usage_unavailable')
    # The real native gateway's finish_tx predates the optional ledger flag.
    # Its original receipt is authoritative here; an existing flag must agree,
    # but absence must not manufacture either known usage or unknown usage.
    _need(type(unavailable) is bool and receipt.get('usage_available') is (not unavailable)
        and ('usage_unavailable' not in call.data or call.data['usage_unavailable'] is unavailable))
    usage = receipt.get('usage')
    if unavailable:
        _need(usage == {} and call.charged_tokens == call.reserved_tokens); public_usage = None
    else:
        _need(type(usage) is dict and all(type(usage.get(k)) is int and usage[k] >= 0
            for k in ('prompt_tokens', 'completion_tokens', 'total_tokens'))
            and usage['prompt_tokens'] + usage['completion_tokens'] == usage['total_tokens']
            and call.charged_tokens == usage['total_tokens'])
        public_usage = {k: usage[k] for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')}
    return {'raw_arguments_sha256': raw_sha, 'projection_hash': projection['projection_hash'],
        'projection': copy.deepcopy(projection)}, public_usage, unavailable


def saved_record(db, p, consumer_id):
    """Check exactly one saved record. No global completeness or source claim."""
    from . import report_saved_map_design_consumer_v176 as c
    from . import catalogue_current_allocation_v260 as current
    c.route_identity(consumer_id); c._current_reader(db, p)
    try:
        row, commands, audits, jobs, calls = _selected(db, p, consumer_id, c)
        _need(type(row.data) is dict and row.data.get('schema') == c.STATE
            and row.status in {'review', 'needs_clarification'} and row.data.get('manual_value') is None)
        _json_bytes(row.data, MAX_RECORD_BYTES)
        start, by_id, checked = _commands(row, commands, audits, c, current)
        state_hash = _states(row, audits, by_id, checked, c)
        _need(len(jobs) == 1)
        job = jobs[0]
        _need(job.id == row.job_id and job.workspace_id == row.workspace_id and job.kind == c.JOB
            and job.status == 'succeeded' and job.idempotency_key == 'saved-map-design:' + row.request_id
            and job.data.get('actor_id') == row.actor_id and job.data.get('consumer_id') == row.id
            and job.data.get('generation_profile') == current.PROFILE)
        prep, spec, scope = _metadata(row, start, p, c, current)
        call, binding = _call(row, calls, prep, spec, scope, c, current)
        answer, usage, unavailable = _answer(row, call, binding, prep, spec)
        return {'schema': SCHEMA, 'workspace_id': row.workspace_id, 'actor_id': row.actor_id,
            'consumer_id': row.id, 'version': row.version, 'status': row.status, 'state_hash': state_hash,
            'start_receipt': copy.deepcopy(start[0].data['result']), 'request_sha256': start[0].body_sha256,
            'call': {'id': call.id, 'status': call.status, 'charged_tokens': call.charged_tokens,
                'usage': usage, 'usage_unavailable': unavailable}, 'answer': answer,
            'verification': dict(VERIFICATION), 'full_read_path': c.PREFIX + '/' + row.id}
    except HTTPException as error:
        if error.status_code in {401, 403, 404}:
            raise
        raise HTTPException(409, ERROR) from None
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, UnicodeError,
            RecursionError, OverflowError):
        raise HTTPException(409, ERROR) from None


def register_routes(router):
    @router.get('/{consumer_id}/saved-record')
    def saved_record_route(consumer_id: str, p: ReadP, db: ReadDB):
        return saved_record(db, p, consumer_id)
