"""Internal confirmed-plan preparation, no database, service, model or network.

These tests use actual preparation/time/tool/HTTP-body contracts. Adapter tests
stop at preparation, with fake authorization failures, before any persistence.
They are not evidence of actual API or worker execution.
"""
import ast
import copy
import hashlib
import json
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import generic_agents
from generic_agents import data_requirements as preparation
from generic_agents import data_tools, goal_queries, native_goals, native_task_queries
from generic_agents import native_goal_time, native_query_proposals, query_needs, relations
from generic_agents.contracts import DataRequest, EvidenceRequirement, digest
from generic_agents.native_query_catalog import manifests

SOURCE = Path(__file__).resolve().parents[1] / 'src/generic_agents'
CLOCK = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
EVIDENCE = EvidenceRequirement(source='archive').model_dump()
QUESTION = '逐条读取数量字段，禁止平均、统计、计数，也不查询关联关系。'


@pytest.fixture(autouse=True)
def no_runtime(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('DB/network/subprocess forbidden in pure confirmed-plan tests')
    from generic_agents import db
    monkeypatch.setattr(db, 'engine', forbidden)
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def request(question=QUESTION, **extra):
    return DataRequest(question=question, entity_ids=['object-a'], fields=['quantity'],
        mode='structured', business_timezone='UTC', evidence_requirement=EVIDENCE, **extra).model_dump()


def tool(operation='mean', **patch):
    return {'id': 'calculate', 'capability_id': 'aggregate', 'input_from': 'evidence',
        'parameters': {'field': 'quantity', 'operation': operation}, **patch}


def native_caps():
    return [SimpleNamespace(id=manifest.specification['method'], data=manifest.model_dump(mode='json'))
        for manifest in manifests()]


def prepared(value=None, **options):
    return preparation.prepare_request(None, SimpleNamespace(user_id='actor', workspace_id='workspace'),
        request() if value is None else value, reference=CLOCK, **options)


def test_source_is_candidate_and_original_http_and_versioned_heuristic_baselines_are_preserved():
    assert Path(generic_agents.__file__).resolve().parent == SOURCE
    # Preserve M66's original bytes and expected hashes. V246 deliberately
    # changes only grounded field intent; its current behavior is tested in
    # dedicated semantic/PG tests. contracts.py had evolved before V246, so
    # its original M66 source and current pre-V246 source are both retained.
    baseline = {
        'contracts.py': '114e5ad25a92c6b0c544e99e9a72774ef07b7191cc856b386872012a7c72c763',
        'query_needs.py': '309951dd2115d774c7b9b27a73f5a96ae04f6bf8b3728496c7fc5e21c4b321c1',
        'api.py': 'a49927f86f16605005889090cbdd671532b6c8160a3214afff365be793266b32',
        'native_query_proposal_contract.py': 'e665e858bc1ff343d8ca20bba12a9212daf1d8a02bb1022b3f8ef3310ee567f1',
        'native_query_proposal_v2.py': '02ebc2f0261ca9fb34788a91b0a039ef22ecc3e631aaca5c13f8f03b339495ab',
        'native_query_proposal_v3.py': '569a0c36339ddf44e2e028e1832f9ceeb94ce018d246aca59d648e78c60e0e33',
        'native_query_proposal_v4.py': 'bcbadaa7c4a59c1ae2212651fa66dfaf4767ba0d32d97676609d20278ca62f08'}
    history = SOURCE.parents[1] / '.agentic-engineering/history/v245-before-field-intent-v246'
    original_sources = {
        'query_needs.py': history / 'src/generic_agents/query_needs.py',
        'contracts.py': history / 'contracts-historical-m66.py'}
    for name, expected in baseline.items():
        assert hashlib.sha256(original_sources.get(name, SOURCE / name).read_bytes()).hexdigest() == expected
    manifest = json.loads((history / 'baseline-manifest.json').read_text())
    assert hashlib.sha256((history / 'tests/test_m66_confirmed_plan.py').read_bytes()).hexdigest() == manifest['old_m66_test_preserved_sha256']
    assert hashlib.sha256((history / 'contracts-current-pre-v246.py').read_bytes()).hexdigest() == manifest['current_pre_v246_contract_sha256']
    assert hashlib.sha256((SOURCE / 'contracts.py').read_bytes()).hexdigest() == manifest['current_pre_v246_contract_sha256']


def test_internal_confirmation_skips_only_raw_intent_guesses_without_rewriting_request(monkeypatch):
    original = request(); before = copy.deepcopy(original)
    def not_called(*args):
        pytest.fail('confirmed structural plan must not reinterpret raw question intent')
    monkeypatch.setattr(query_needs, 'relation_intent', not_called)
    monkeypatch.setattr(query_needs, 'unsupported_intent', not_called)
    value = prepared(original, _confirmed_plan=True)
    assert value['clarifications'] == [] and value['question'] == QUESTION
    assert original == before and '_confirmed_plan' not in value
    assert value['evidence_requirement']['source'] == 'archive'


@pytest.mark.parametrize('question', [QUESTION, '读取数量字段，不执行计算。', '计算平均值', '查询关联对象'])
def test_manual_or_unconfirmed_requests_keep_original_conservative_questions(question):
    assert prepared(request(question))['clarifications']
    assert prepared(request(question), _confirmed_plan=False)['clarifications']


@pytest.mark.parametrize('flag', [None, 1, 'true', {}, []])
def test_only_literal_internal_true_can_skip_raw_intent_guesses(flag):
    assert prepared(_confirmed_plan=flag)['clarifications']


def test_relation_scope_validator_still_runs_and_can_refuse_confirmed_plan(monkeypatch):
    seen = []
    value = request()
    value['relation_query'] = {'relation_types': ['route'], 'root_ids': ['other-workspace'],
        'direction': 'outgoing', 'max_hops': 1, 'include_roots': False, 'at': None}
    def refuse(db, p, selector, entity_ids):
        seen.append((selector, entity_ids))
        raise HTTPException(403, 'relation scope refused')
    monkeypatch.setattr(relations, 'validate_query', refuse)
    with pytest.raises(HTTPException, match='relation scope refused'):
        prepared(value, _confirmed_plan=True)
    assert seen == [(value['relation_query'], ['object-a'])]


def test_tool_validation_and_registry_binding_still_run_for_confirmed_plan(monkeypatch):
    monkeypatch.setattr(data_tools, 'choices', lambda *args, lock_sources=True: native_caps())
    value = request(tool_steps=[tool()])
    assert prepared(value, _confirmed_plan=True)['tool_steps'] == value['tool_steps']


@pytest.mark.parametrize('failure', ['not_registered', 'outside_fields', 'future_frame', 'sql_parameter', 'budget', 'bad_operation'])
def test_actual_tool_scope_parameter_and_budget_gates_cannot_be_bypassed(monkeypatch, failure):
    monkeypatch.setattr(data_tools, 'choices', lambda *args, lock_sources=True: native_caps())
    value = request(tool_steps=[tool()])
    step = value['tool_steps'][0]
    if failure == 'not_registered':
        step['capability_id'] = 'unregistered'
    elif failure == 'outside_fields':
        step['parameters']['field'] = 'outside'
    elif failure == 'future_frame':
        step['input_from'] = 'future'
    elif failure == 'sql_parameter':
        step['parameters']['sql'] = 'not executable'
    elif failure == 'budget':
        value['tool_steps'].append(tool(id='second'))
        value['max_tool_calls'] = 1
    elif failure == 'bad_operation':
        step['parameters']['operation'] = 'arbitrary'
    with pytest.raises(HTTPException):
        prepared(value, _confirmed_plan=True)


def test_object_scope_resolution_is_not_skipped_when_timezone_is_not_provided(monkeypatch):
    value = request(); value['business_timezone'] = None
    def refuse(*args):
        raise HTTPException(403, 'object ownership refused')
    monkeypatch.setattr(preparation, 'scoped', refuse)
    with pytest.raises(HTTPException, match='object ownership refused'):
        prepared(value, _confirmed_plan=True)


@pytest.mark.parametrize('failure', ['source', 'verification', 'range', 'date_conflict'])
def test_evidence_and_time_gates_remain_active_for_confirmed_plan(failure):
    value = request()
    if failure == 'source':
        value['evidence_requirement']['source'] = 'unapproved'
    elif failure == 'verification':
        value['evidence_requirement']['minimum_verification'] = 'invented'
    elif failure == 'range':
        value['evidence_requirement']['time'].update(mode='range', start='2026-10-16T00:00:00Z', end='2026-10-15T00:00:00Z')
    else:
        value['question'] = '读取昨天的数量'
    assert prepared(value, _confirmed_plan=True)['clarifications']


@pytest.mark.parametrize('field', ['_confirmed_plan', 'confirmed_plan'])
def test_http_json_cannot_supply_confirmation_flag(field):
    body = request(); body[field] = True
    with pytest.raises(ValidationError) as error:
        DataRequest.model_validate(body)
    assert any(item['type'] == 'extra_forbidden' and item['loc'] == (field,) for item in error.value.errors())
    assert field not in DataRequest.model_json_schema()['properties']


def test_http_request_budget_and_external_policy_model_are_unchanged():
    body = request(); body['max_tool_calls'] = 7
    with pytest.raises(ValidationError):
        DataRequest.model_validate(body)
    body = request(); body['max_tool_seconds'] = 121
    with pytest.raises(ValidationError):
        DataRequest.model_validate(body)
    assert request()['allow_external_tools'] is False


class PreparationReached(Exception):
    pass


def supplemental_adapter(monkeypatch, *, failure=None, request_patch=None):
    task = SimpleNamespace(id='task', version=1, data={'entity_ids': ['object-a']})
    body = SimpleNamespace(request_id='request', expected_version=1, proposal_id='proposal')
    value = request(); value.update(request_patch or {})
    events = []
    def gate(name):
        def check(*args):
            events.append(name)
            if failure == name:
                raise HTTPException(403 if name == 'permission' else 409, name + ' refused')
        return check
    monkeypatch.setattr(native_task_queries, '_manage', gate('permission'))
    monkeypatch.setattr(native_task_queries, '_journal', lambda *args: None)
    monkeypatch.setattr(native_task_queries, '_guard', gate('goal_scope'))
    monkeypatch.setattr(native_task_queries, 'check_version', gate('version'))
    def adoption(*args):
        gate('adoption')()
        return {'request': value}
    monkeypatch.setattr(native_query_proposals, 'adopted_reference', adoption)
    def capture(db, p, incoming, **options):
        events.append('prepare')
        assert options == {'_confirmed_plan': True}
        assert incoming == value and incoming is not value and incoming['question'] == QUESTION
        raise PreparationReached()
    monkeypatch.setattr(preparation, 'prepare_request', capture)
    return task, body, events


def test_supporting_adapter_checks_scope_and_adoption_before_internal_flag_without_rewriting_question(monkeypatch):
    task, body, events = supplemental_adapter(monkeypatch)
    with pytest.raises(PreparationReached):
        native_task_queries.execute(None, None, task, body)
    assert events == ['permission', 'goal_scope', 'version', 'adoption', 'prepare']


@pytest.mark.parametrize('failure', ['permission', 'goal_scope', 'version', 'adoption'])
def test_supporting_adapter_cannot_bypass_current_authority_or_original_adoption(monkeypatch, failure):
    task, body, events = supplemental_adapter(monkeypatch, failure=failure)
    with pytest.raises(HTTPException):
        native_task_queries.execute(None, None, task, body)
    assert 'prepare' not in events


@pytest.mark.parametrize('patch', [{'mode': 'cloud'}, {'allow_external_tools': True}, {'entity_ids': ['other-object']}])
def test_supporting_adapter_rejects_changed_execution_scope_before_internal_flag(monkeypatch, patch):
    task, body, events = supplemental_adapter(monkeypatch, request_patch=patch)
    with pytest.raises(HTTPException):
        native_task_queries.execute(None, None, task, body)
    assert events[-1] == 'adoption' and 'prepare' not in events


def test_formal_fixed_data_query_keeps_purpose_reference_and_window(monkeypatch):
    reference = '2026-10-14T12:00:00+00:00'
    window = {'mode': 'archive', 'reference_at': reference}
    task = SimpleNamespace(data={'goal_spec': {'purpose': QUESTION, 'entity_ids': ['object-a'],
        'required_fields': ['quantity'], 'evidence': EVIDENCE, 'max_tool_calls': 6,
        'data_query': {'tool_steps': [], 'max_tool_seconds': 60, 'allow_external_tools': False}}, 'goal_window': window})
    seen = []
    def capture(db, p, value, **options):
        seen.append((copy.deepcopy(value), options))
        return {**value, 'resolved_time': {'temporary': True}}
    monkeypatch.setattr(preparation, 'prepare_request', capture)
    result = goal_queries.request_for(None, None, task)
    assert seen[0][0]['question'] == QUESTION
    assert seen[0][1] == {'reference': datetime.fromisoformat(reference), '_confirmed_plan': True,
        'lock_sources': True}
    assert result['resolved_time'] == window and result['resolved_time'] is not window
    assert '_confirmed_plan' not in result


@pytest.mark.parametrize('has_data_query', [False, True])
def test_native_goal_process_keeps_existing_source_question_and_guard_before_flag(monkeypatch, has_data_query):
    spec = {'purpose': QUESTION, 'entity_ids': ['object-a'], 'required_fields': ['quantity'], 'evidence': EVIDENCE}
    if has_data_query:
        spec['data_query'] = {'tool_steps': []}
    task = SimpleNamespace(id='task', status='active', data={'goal_spec': spec})
    context = {'fixed': 'goal'}
    job = SimpleNamespace(data={'task_id': 'task', 'context_hash': digest(context)})
    events = []
    monkeypatch.setattr(native_goals, 'require', lambda *args: events.append('permission'))
    monkeypatch.setattr(native_goals, 'scoped', lambda *args: task)
    monkeypatch.setattr(native_goals, 'guard', lambda *args, **kwargs: events.append('guard') or context)
    monkeypatch.setattr(native_goal_time, 'timeout', lambda *args: False)
    monkeypatch.setattr(native_goal_time, 'admitted', lambda *args: True)
    monkeypatch.setattr(native_goal_time, 'expire', lambda *args: False)
    def capture(db, p, value, **options):
        events.append('prepare')
        assert options == {'_confirmed_plan': True}
        assert value['question'] == ('读取原任务已确认范围内的原资料' if has_data_query else QUESTION)
        raise PreparationReached()
    monkeypatch.setattr(preparation, 'prepare_request', capture)
    with pytest.raises(PreparationReached):
        native_goals.process(None, None, job)
    assert events == ['permission', 'guard', 'prepare']


def test_flag_is_only_wired_to_three_internal_adapters_and_not_http_handler():
    calls = []
    for path in SOURCE.glob('*.py'):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and any(key.arg == '_confirmed_plan' for key in node.keywords):
                calls.append((path.name, node))
    assert {name for name, node in calls} == {'native_task_queries.py', 'native_goals.py', 'goal_queries.py'}
    assert len(calls) == 3
    assert all(any(key.arg == '_confirmed_plan' and isinstance(key.value, ast.Constant) and key.value.value is True
        for key in node.keywords) for name, node in calls)
    tree = ast.parse((SOURCE / 'api.py').read_text())
    endpoint = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'data_query')
    preparing = [node for node in ast.walk(endpoint) if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name) and node.func.id == 'prepare_request']
    assert len(preparing) == 1 and not preparing[0].keywords
