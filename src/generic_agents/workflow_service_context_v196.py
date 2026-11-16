"""Explicit context-object resensing beside the unchanged two-UUID service.

The registered V2 contract retains the entire real reader output as wire input.
Only an independently checked producer context is lowered to the internal two
UUIDs used by the shared V195 parent/monitor/acquisition/ACK implementation.
No marker, self hash, reader declaration or this module is an execution grant.
"""
import copy
import sys

from sqlalchemy import select, func

from . import workflow_service_v195 as shared
from . import workflow_task_context_v196 as reader
from .business_flow_review_store import _bytes, _same
from .contracts import digest
from .models import Audit
from .services import audit, validate


OP = 'resense_from_context'
RELEASE_VERSION = '2.0.0'
RECEIPT = 'workflow-service-context-receipt-v196'
PROOF = 'workflow-service-context-source-v196'
PROOF_KEY = 'workflow_service_context_source_v196'
PROOF_AUDIT = 'workflow_service.context_source_v196'
MAX_INPUT_BYTES = 1024 * 1024
PROOF_KEYS = {'schema', 'operation', 'workspace_id', 'task_id', 'job_id', 'activation_id',
    'invocation_id', 'request_key_hash', 'input_hash', 'approval_hash', 'source_binding_hash',
    'context_hash', 'context_value_hash', 'producer_invocation_id', 'producer_job_id', 'proof_hash'}


def input_schema(operation=OP):
    shared.need(operation == OP, status=422)
    return shared._object({'context': copy.deepcopy(reader.context_schema())})


def output_schema(operation=OP):
    shared.need(operation == OP, status=422)
    result = copy.deepcopy(shared.output_schema('resense_with_receipt'))
    receipt = result['properties']['service_receipt']
    receipt['properties']['schema'] = {'const': RECEIPT}
    receipt['properties']['operation'] = {'const': OP}
    receipt['properties']['context_hash'] = {'type': 'string', 'pattern': shared.HASH_PATTERN}
    receipt['properties']['producer_invocation_id'] = {'type': 'string', 'pattern': shared.UUID_PATTERN}
    receipt['required'] += ['context_hash', 'producer_invocation_id']
    return result


def validate_manifest(manifest):
    shared.need(type(manifest) is dict and manifest.get('kind') == 'tool'
        and manifest.get('version') == RELEASE_VERSION
        and manifest.get('allowed_agent_roles') == ['business'], status=422)
    _bytes(manifest, MAX_INPUT_BYTES)
    execution = manifest.get('execution')
    shared.need(type(execution) is dict
        and set(execution) == {'adapter', 'service_actor', 'operation', 'side_effect'}
        and execution['adapter'] == 'workflow_service'
        and execution['service_actor'] == 'execution_service'
        and execution['operation'] == OP and execution['side_effect'] is True,
        '上下文补采必须保留真实执行服务职责和有副作用的独立操作', 422)
    shared.need(_same(manifest.get('input_schema'), input_schema())
        and _same(manifest.get('output_schema'), output_schema()),
        '上下文补采须登记完整对象及实际回执合同；未修改旧两编号服务', 422)
    return copy.deepcopy(manifest)


def _inputs(inputs):
    shared.need(type(inputs) is dict and set(inputs) == {'context'}
        and type(inputs['context']) is dict, status=422)
    _bytes(inputs, MAX_INPUT_BYTES)
    validate(input_schema(), inputs)
    return copy.deepcopy(inputs)


def prelock_gap_id(inputs):
    """Choose only the lock identity; shape validation grants no authority."""
    value = _inputs(inputs)['context']['gap']['gap_id']
    shared._id(value)
    return value


def _current(db, p, cap, role, inputs, key, approval):
    return shared.validate_call_approval(db, p, cap, role, inputs, key, approval,
        _variant=sys.modules[__name__])


def _checked_reader(db, p, current, inputs, *, dispatch):
    original = _inputs(inputs)['context']
    checked = reader.checked_context(db, p, original, task=current['task'], dispatch=dispatch)
    shared.need(_same(checked, original), '原生产者上下文核对不得修写输入或替换编号')
    return checked


def validate_call_approval(db, p, cap, role, inputs, key, approval):
    current = _current(db, p, cap, role, inputs, key, approval)
    # ProducerPending propagates unchanged. The caller must wait BEFORE any
    # consumer Invocation/reservation/dispatch intent, never mark it successful.
    # A previously fulfilled original request is still a valid receipt source.
    # This preflight grants no fresh collection; execute checks that separately.
    _checked_reader(db, p, current, inputs, dispatch=False)
    return current


def _request_inputs(value):
    return {'policy_id': value['policy']['policy_id'], 'gap_id': value['gap']['gap_id']}


def _proof_value(p, current, call, inputs, key, approval):
    value = inputs['context']
    result = {'schema': PROOF, 'operation': OP, 'workspace_id': p.workspace_id,
        'task_id': current['task'].id, 'job_id': current['job'].id,
        'activation_id': approval['activation_id'], 'invocation_id': call.id,
        'request_key_hash': digest(key), 'input_hash': digest(inputs), 'approval_hash': digest(approval),
        'source_binding_hash': current['binding']['binding_hash'], 'context_hash': value['context_hash'],
        'context_value_hash': digest(value), 'producer_invocation_id': value['producer']['invocation_id'],
        'producer_job_id': value['producer']['job_id']}
    return {**result, 'proof_hash': digest(result)}


def _proof_anchor(value):
    return {'schema': PROOF, 'operation': OP, 'invocation_id': value['invocation_id'],
        'task_id': value['task_id'], 'job_id': value['job_id'], 'proof_hash': value['proof_hash'],
        'context_hash': value['context_hash'], 'context_value_hash': value['context_value_hash'],
        'producer_invocation_id': value['producer_invocation_id'], 'producer_job_id': value['producer_job_id']}


def _checked_proof(db, p, current, call, inputs, key, approval):
    value = call.data.get(PROOF_KEY)
    events = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == PROOF_AUDIT, Audit.object_id == call.id)))
    if value is None:
        shared.need(not events, '原上下文服务来源审计仍在；未重建或替换原请求')
        return None
    expected = _proof_value(p, current, call, inputs, key, approval)
    shared.need(type(value) is dict and set(value) == PROOF_KEYS and _same(value, expected)
        and len(events) == 1 and events[0].workspace_id == p.workspace_id
        and events[0].actor_id == p.user_id and events[0].object_id == call.id
        and _same(events[0].data, _proof_anchor(expected)),
        '原上下文、实际生产者或服务来源独立审计无法核对；原号保留')
    return copy.deepcopy(value)


def _save_proof(db, p, current, call, inputs, key, approval):
    value = _proof_value(p, current, call, inputs, key, approval)
    call.data = {**call.data, PROOF_KEY: value}
    audit(db, p, PROOF_AUDIT, call.id, _proof_anchor(value))
    db.flush()
    return value


def _receipt(db, p, current, call, inputs, state, value, proof):
    shared.need(proof is not None)
    result = shared.resense_receipt(db, p, current, _request_inputs(value), call, state,
        receipt_operation=OP)
    # This is the new operation's actual shared-plan result, not a re-decoding
    # of an old saved response. Persisted V195 receipts are never rewritten.
    checked = copy.deepcopy(result)
    receipt = checked['service_receipt']
    shared.need(receipt['schema'] == shared.RECEIPT and receipt['operation'] == OP)
    receipt.update(schema=RECEIPT, context_hash=proof['context_hash'],
        producer_invocation_id=proof['producer_invocation_id'])
    receipt['receipt_hash'] = digest({field: item for field, item in receipt.items() if field != 'receipt_hash'})
    validate(output_schema(), checked)
    return checked


def execute(db, p, cap, inputs, key, approval):
    current = _current(db, p, cap, 'business', inputs, key, approval)
    call = shared._call(db, p, current, key, inputs, approval)
    state = shared._state(db, p, current, call, inputs, key, approval)
    proof = _checked_proof(db, p, current, call, inputs, key, approval)
    value = _checked_reader(db, p, current, inputs, dispatch=False)
    if state is not None:
        return _receipt(db, p, current, call, inputs, state, value, proof)
    shared.need(proof is None, '原来源证明已保存但补采状态缺失；未再次提议或重建请求')
    gap = shared.scoped(db, shared.DataGap, value['gap']['gap_id'], p, True)
    shared.need(gap.status not in {'cancelled', 'needs_review', 'pending_delivery'},
        '原资料缺口已停止、尚待核查或尚未接收；未安排新设备动作')
    if gap.status != 'resolved':
        _checked_reader(db, p, current, inputs, dispatch=True)
    _save_proof(db, p, current, call, inputs, key, approval)
    created_at = db.scalar(select(func.clock_timestamp())).isoformat()
    if gap.status == 'resolved':
        state = shared.start_existing_resense(db, p, current, call, inputs, key, approval,
            request_inputs=_request_inputs(value), observation_request=value['gap']['observation_request'],
            created_at=created_at)
        return _receipt(db, p, current, call, inputs, state, value, proof=call.data[PROOF_KEY])
    return shared.start_resense(db, p, current, call, inputs, key, approval,
        request_inputs=_request_inputs(value), created_at=created_at)


def reconcile(db, p, cap, inputs, key, approval, call):
    current = _current(db, p, cap, 'business', inputs, key, approval)
    actual = shared._call(db, p, current, key, inputs, approval, supplied=call, allow_unknown=True)
    state = shared._state(db, p, current, actual, inputs, key, approval)
    proof = _checked_proof(db, p, current, actual, inputs, key, approval)
    shared.need(state is not None and proof is not None,
        '原补采或上下文证明未完整保存；仅核对原号，不创建替代请求')
    value = _checked_reader(db, p, current, inputs, dispatch=False)
    return _receipt(db, p, current, actual, inputs, state, value, proof)
