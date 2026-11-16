"""Pure request/schema/privacy tests, not source authorization or a paid query."""
import copy
import json
from uuid import uuid4, uuid5, NAMESPACE_DNS

import pytest
from fastapi import HTTPException
from jsonschema import Draft202012Validator, ValidationError as SchemaInvalid
from pydantic import ValidationError

from generic_agents import models as m
from generic_agents.contracts import digest
from generic_agents.services import as_dict
from generic_agents import multimodal_question_contracts_v262 as c
from generic_agents import multimodal_question_service_v262 as service
from generic_agents import multimodal_question_tool_v262 as tool
from tests.test_multimodal_query_contracts_v262 import sound_intent
from tests.test_multimodal_query_compute_v262 import source, sound_record, sound_query, plan


def body():
    return {'schema': 'multimodal-question-submit-v262', 'release_id': str(uuid4()), 'task_id': None,
        'entity_id': None, 'question': '声音成果还有多少等待独立接收？', 'model_profile_id': str(uuid4()),
        'options_hash': 'a' * 64, 'registry_hash': 'b' * 64, 'request_id': '问题原号/' + '甲' * 12,
        'preview_hash': 'c' * 64, 'confirm_cloud': True}


@pytest.mark.parametrize('damage', ['consent_int', 'consent_string', 'nul', 'surrogate', 'upper_uuid',
    'short_nonce', 'extra_records', 'blank_question', 'bad_hash', 'version_bool'])
def test_closed_new_request_does_not_coerce_or_accept_model_supplied_records(damage):
    value = body(); model = c.Submit
    if damage == 'consent_int': value['confirm_cloud'] = 1
    elif damage == 'consent_string': value['confirm_cloud'] = 'true'
    elif damage == 'nul': value['request_id'] += '\0'
    elif damage == 'surrogate': value['question'] += '\ud800'
    elif damage == 'upper_uuid': value['release_id'] = 'ABCDEF01-1111-4111-8111-111111111111'
    elif damage == 'short_nonce': value['request_id'] = 'short'
    elif damage == 'extra_records': value['records'] = [{'private': 99}]
    elif damage == 'blank_question': value['question'] = ' \n '
    elif damage == 'bad_hash': value['registry_hash'] = 'F' * 64
    else:
        model = c.Command
        value = {'schema': 'multimodal-question-command-v262', 'request_id': 'command-original-123',
            'version': True, 'reason': '保留原数据后明确恢复', 'expected_request_hash': 'd' * 64, 'operation': 'retry'}
    with pytest.raises((ValidationError, ValueError)): model.model_validate(value)


def test_unicode_raw_and_preview_are_exact_and_bounded_without_rewriting():
    value = body(); raw = json.dumps(value, ensure_ascii=False, indent=1).encode()
    assert service._wire(raw, c.Submit) == value
    expected = {key: value[key] for key in c.PreviewInput.model_fields if key != 'schema'}
    expected['schema'] = 'multimodal-question-preview-input-v262'
    assert c.preview_input(c.Submit.model_validate(value)) == expected
    with pytest.raises(HTTPException): service._wire(b' ' * (c.MAX_RAW + 1), c.Submit)
    with pytest.raises(HTTPException): service._wire(b'{"schema":"a","schema":"b"}', c.Submit)


def test_planner_schema_accepts_the_real_new_envelope_and_never_scope_or_results():
    intent = {'schema': 'multimodal-query-intent-v262', 'subqueries': [sound_intent()], 'unmet_requirements': []}
    value = {'status': 'candidate', 'intent': intent, 'explanation': '仅规划行政收件查询，不是模型生成数字'}
    check = Draft202012Validator(c.model_result_schema()); check.validate(value)
    for key, payload in [('scope', {'workspace_id': str(uuid4())}), ('answer', 3), ('sql', 'select 1')]:
        bad = copy.deepcopy(value); bad[key] = payload
        with pytest.raises(SchemaInvalid): check.validate(bad)
    with pytest.raises(SchemaInvalid): check.validate(intent)


def test_fixed_sdk_manifest_and_complete_actual_computation_schema_match():
    snapshot = source([sound_record()]); bound = plan(snapshot, sound_query())
    row = m.DataRun(id=service.query_identity(snapshot['workspace_id'], 'pure-schema-only-123'),
        workspace_id=snapshot['workspace_id'], owner_id=snapshot['captured_by'], question='pure metadata only', status='queued')
    state = {'request_hash': 'a' * 64, 'plan_hash': bound['plan_hash'], 'source_snapshot': snapshot,
        'source_audit_id': str(uuid4()), 'plan': bound}
    answer = service._answer(row, state)  # Schema fixture only: no DB proof/authority is claimed.
    Draft202012Validator(c.answer_schema()).validate(answer)
    assert answer['computation']['verification']['source_authority_checked'] is False
    fixed = tool.manifest().model_dump(mode='json'); tool.validate_manifest(fixed)
    assert fixed['allowed_agent_roles'] == ['data'] and fixed['data_policy']['cloud_allowed'] is False
    bad = copy.deepcopy(fixed); bad['allowed_agent_roles'].append('business')
    with pytest.raises(HTTPException): tool.validate_manifest(bad)
    bad_answer = copy.deepcopy(answer); bad_answer['computation']['facets'][0]['computation']['unexpected'] = 1
    with pytest.raises(SchemaInvalid): Draft202012Validator(c.answer_schema()).validate(bad_answer)


@pytest.mark.parametrize('kind', ['run', 'stripped_run', 'job', 'invocation', 'call', 'call_family', 'audit', 'parent'])
def test_generic_serializer_never_exposes_private_question_answer_source_or_command_text(kind):
    workspace = str(uuid4()); query_id = service.query_identity(workspace, 'original-request-123')
    secret = '私人原问与人工说明不可跨账号泄露'
    common = {'id': str(uuid4()), 'workspace_id': workspace, 'version': 1}
    if kind in {'run', 'stripped_run', 'parent'}:
        row = m.DataRun(**{**common, 'id': query_id if kind != 'parent' else str(uuid4())},
            owner_id=str(uuid4()), question=secret, status='succeeded', data={
                **({'family': service.KEY} if kind == 'run' else {}),
                **({'parent_query_id': query_id} if kind == 'parent' else {}), 'raw': secret})
    elif kind == 'job': row = m.Job(**common, kind='changed', idempotency_key='stripped-key', status='running', data={'query_id': query_id, 'raw': secret})
    elif kind == 'invocation': row = m.Invocation(**common, capability_id=str(uuid4()), idempotency_key='stripped-key',
        status='succeeded', data={'inputs': {'query_id': query_id}, 'output': {'secret': secret}})
    elif kind == 'call': row = m.CloudCall(**common, capability_id=str(uuid4()), status='succeeded',
        data={'query_id': query_id, 'raw': secret})
    elif kind == 'call_family': row = m.CloudCall(**common, capability_id=str(uuid4()), status='succeeded',
        data={'query_family': service.KEY, 'gateway_dispatch': {'identity': {'suggestion_id': query_id}}, 'raw': secret})
    else: row = m.Audit(**common, actor_id=str(uuid4()), action='stripped', object_id=query_id,
        data={'query_id': query_id, 'raw': secret})
    view = as_dict(row)
    assert view['private_record'] is True and view['reference_only'] is True
    assert secret not in json.dumps(view, ensure_ascii=False) and 'question' not in view and 'data' not in view
    if isinstance(row, m.Job):
        assert view['kind'] == row.kind  # Existing overview filters use this nonprivate real column.


def test_old_nonquery_uuid5_task_entity_and_namespace_are_not_private_query_markers():
    identity = str(uuid5(NAMESPACE_DNS, 'ordinary-task-not-a-query'))
    row = m.Job(id=str(uuid4()), workspace_id=str(uuid4()), version=1, kind='execute_workflow_step',
        status='queued', idempotency_key='ordinary-workflow', data={'task_id': identity, 'entity_id': identity, 'namespace': identity})
    assert service.private_projection(row) is None and as_dict(row)['data'] == row.data
    call = m.CloudCall(id=str(uuid4()), workspace_id=str(uuid4()), version=1, capability_id=str(uuid4()),
        status='succeeded', data={'gateway_dispatch': {'identity': {
            'suggestion_id': identity, 'task_id': 'ordinary-workflow'}}})
    assert service.private_projection(call) is None and as_dict(call)['data'] == call.data


@pytest.mark.parametrize('receipt,expected,unavailable', [
    ({'usage': {}, 'usage_unavailable': True}, None, True),
    ({'usage': {'prompt_tokens': None, 'completion_tokens': None, 'total_tokens': None}, 'usage_unavailable': True}, None, True),
    ({'usage': {'prompt_tokens': 10, 'completion_tokens': 20, 'total_tokens': 30}, 'usage_unavailable': True}, None, True),
    ({}, None, True),
    ({'usage': {'prompt_tokens': 1, 'completion_tokens': 2, 'total_tokens': 3}}, None, True),
    ({'usage': {'prompt_tokens': 1, 'completion_tokens': 2, 'total_tokens': 3}, 'usage_unavailable': False},
        {'prompt_tokens': 1, 'completion_tokens': 2, 'total_tokens': 3}, False),
    ({'usage': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}, 'usage_unavailable': False},
        {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}, False),
    ({'usage': {'prompt_tokens': True, 'completion_tokens': 2, 'total_tokens': 3}, 'usage_unavailable': False}, None, True),
    ({'usage': {'prompt_tokens': 1, 'completion_tokens': 2, 'total_tokens': 99}, 'usage_unavailable': False}, None, True),
])
def test_planning_usage_projection_uses_actual_receipt_and_preserves_unknown_reserved_charge(receipt, expected, unavailable):
    call = m.CloudCall(id=str(uuid4()), workspace_id=str(uuid4()), capability_id=str(uuid4()),
        version=1, status='succeeded', reserved_tokens=8192,
        charged_tokens=8192 if unavailable else expected['total_tokens'], data={'receipt': copy.deepcopy(receipt)})
    original = (copy.deepcopy(call.data), call.reserved_tokens, call.charged_tokens, call.status)
    assert service._usage_projection(call) == (expected, unavailable)
    assert (call.data, call.reserved_tokens, call.charged_tokens, call.status) == original
    assert service._usage_projection(None) == (None, True)
