"""Actual V195 parser/compiler/adoption checks over public synthetic inputs.

The qualification/source, current IAM and persistence adapters below are explicit
IO-boundary spies. They are not a real qualification, registry, PG, HTTP, device,
or human-acceptance proof; Root runs the independent normal-API journey.
"""
import copy
import hashlib
import json
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from uuid import NAMESPACE_URL, uuid5

import pytest
from fastapi import HTTPException

from generic_agents import business_flow_execution_adoption as shared
from generic_agents import business_flow_nested_adoption_v195 as nested
from generic_agents.contracts import digest
from tests.test_business_flow_execution_adoption import fixture as legacy_fixture
from tests.test_business_flow_nested_execution_v195 import scene as compiler_scene
from tests.test_report_business_flow_wire_v184 import no_external


def identity(label):
    return str(uuid5(NAMESPACE_URL, 'public-v195-adoption-test:' + label))


def wire(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()


def fixture(*, service=False):
    plan, source, supplied, sidecar, _ = compiler_scene()
    if service:
        path = '/business/flows/0/phases/0/branches/0/phases/0/branches/0/phases/0/steps/0'
        action = plan['business']['flows'][0]['phases'][0]['branches'][0]['phases'][0]['branches'][0]['phases'][0]['steps'][0]
        action.update(actor='task_center', role='任务中心值守角色')
        supplied['nodes'][path].update(service_actor='task_center', operation='in_app_notice')
        supplied['nodes'][path]['execution'].update(adapter='workflow_service', agent_role='business')
        sidecar['source_plan_hash'] = digest(plan)
    walked = nested.plans.walk_action_ports(plan, scope_binding=source['scope_binding'])
    roles = {action['role']: {'user_id': identity(action['role']), 'username': '公开合成人员',
        'membership_role': 'operator'} for action in walked['actions']}
    for action in walked['actions']:
        supplied['nodes'][action['path']]['actor_id'] = roles[action['role']]['user_id']
    translated = nested.compiler.build_execution_graph(plan, 0, supplied,
        scope_binding=source['scope_binding'], causal_bindings=sidecar)
    # This minimal metadata is only the pure package adapter boundary. The real
    # shared qualification/source loader and its independent audits are not faked
    # into a claimed success here.
    q = {'schema': nested.review.RESULT, 'id': identity('qualification'),
        'qualification_hash': 'a' * 64, 'value': copy.deepcopy(plan),
        'status': 'engineering_reviewed', **nested.review.mode_fields('engineering_simulation'),
        'source_binding': {'schema': 'business-flow-nested-review-source-v193',
            'generation_profile': 'flow-v193', 'value_schema': nested.plans.PLAN},
        **{key: False for key in nested.reviews.FLAGS}}
    package = {'schema': nested.SOURCE, 'qualification': q,
        'scope_binding': copy.deepcopy(source['scope_binding']), 'material_snapshot': {}}
    pins = {node['id']: copy.deepcopy(node['execution']) for node in translated['graph']['nodes']
        if node['kind'] == 'ability'}
    return shared.seal({'schema': nested.BINDING, 'business_name': plan['business']['name'],
        'qualification_id': q['id'], 'qualification_hash': q['qualification_hash'], 'flow_index': 0,
        'flow': nested.derive_flow(package, 0, translated), 'graph': translated['graph'],
        'role_bindings': roles, 'capability_pins': pins, 'event_source_bindings': {},
        'event_admission_policy': None, 'source_materials': [], 'source': package,
        'execution_bindings': supplied, 'causal_bindings': sidecar, 'translation': translated,
        'node_actions': {node_id: 'state' for node_id in pins}, 'external_node_ids': [],
        'review_mode': q['review_mode'], 'simulation_only': True,
        'entry_policy': nested.entry_policy(package, 0, 'engineering_manual')}, 'binding_hash')


def command(binding, *, before=None):
    before = {} if before is None else before
    value = {'schema': 'business-flow-nested-execution-adopt-command-v195',
        'qualification_id': binding['qualification_id'], 'qualification_hash': binding['qualification_hash'],
        'flow_index': 0, 'business_name': binding['business_name'], 'configuration_revision': 1,
        'configuration_hash': digest(before), 'execution_bindings': binding['execution_bindings'],
        'causal_bindings': binding['causal_bindings'],
        'role_bindings': {key: row['user_id'] for key, row in binding['role_bindings'].items()},
        'node_actions': binding['node_actions'], 'external_node_ids': [],
        'entry_mode': 'engineering_manual', 'request_id': identity('request'),
        'preview_hash': 'd' * 64, 'consent': True, 'share_source': True}
    return copy.deepcopy(value)


def principal():
    return NS(role='operator', user_id=identity('actor'), workspace_id=identity('workspace'))


def reseal(value):
    return shared.seal({key: item for key, item in value.items() if key != 'binding_hash'}, 'binding_hash')


def test_actual_recursive_compiler_binding_preserves_original_paths_mode_and_no_legacy_proof():
    binding = fixture(); original = copy.deepcopy(binding)
    assert nested.check_binding(binding) == binding == shared.check_binding(binding)
    assert set(binding) == nested.KEYS and len(binding) == 22
    assert binding == original
    assert binding['flow']['source_flow'] == binding['source']['qualification']['value']['business']['flows'][0]
    assert binding['flow']['source_mapping'] == binding['translation']['source_mapping']
    assert '/branches/0/phases/' in json.dumps(binding['flow']['source_mapping'])
    assert len(binding['flow']['steps']) == len(binding['translation']['source_mapping']['actions'])
    assert binding['graph']['references']['branch_result']['source'] == 'exclusive_output'
    assert binding['entry_policy']['source_trigger_kind'] == binding['flow']['source_flow']['trigger']['kind']
    assert binding['simulation_only'] is True and binding['review_mode'] == 'engineering_simulation'
    assert not {'source_design', 'source_manifest', 'source_proof'} & set(binding)
    assert binding['event_source_bindings'] == {} and binding['event_admission_policy'] is None


@pytest.mark.parametrize('fault', ['flow', 'mapping', 'graph', 'role', 'missing_role', 'pins',
    'causal', 'qualification', 'mode', 'simulation', 'entry', 'trigger', 'extra', 'events'])
def test_resealed_changes_cannot_launder_recursive_source_or_engineering_into_another_contract(fault):
    binding = fixture()
    if fault == 'flow': binding['flow']['steps'].pop()
    elif fault == 'mapping': binding['flow']['source_mapping']['actions'].pop()
    elif fault == 'graph': binding['graph']['nodes'].pop()
    elif fault == 'role': next(iter(binding['role_bindings'].values()))['user_id'] = identity('different')
    elif fault == 'missing_role': binding['role_bindings'].pop(next(iter(binding['role_bindings'])))
    elif fault == 'pins': binding['capability_pins'].pop(next(iter(binding['capability_pins'])))
    elif fault == 'causal': binding['causal_bindings']['input_bindings'][0]['sources'].pop()
    elif fault == 'qualification': binding['qualification_hash'] = 'b' * 64
    elif fault == 'mode': binding['review_mode'] = 'author_self_review'
    elif fault == 'simulation': binding['simulation_only'] = False
    elif fault == 'entry': binding['entry_policy']['mode'] = 'declared_manual'
    elif fault == 'trigger': binding['entry_policy']['source_trigger_kind'] = 'invented'
    elif fault == 'extra': binding['execution_grant'] = True
    else: binding['event_source_bindings'] = {'made_up': {}}
    with pytest.raises((HTTPException, ValueError)):
        shared.check_binding(reseal(binding))


def test_old_and_new_bindings_do_not_accept_each_others_marker_or_closed_shape():
    old = legacy_fixture(2); new = fixture()
    assert shared.check_binding(old) == old
    with pytest.raises(HTTPException): nested.check_binding(old)
    for value, marker in ((old, nested.BINDING), (new, shared.BINDING), (new, 'unknown-binding')):
        changed = copy.deepcopy(value); changed['schema'] = marker
        with pytest.raises(HTTPException): shared.check_binding(reseal(changed))


@pytest.mark.parametrize('fault', ['missing_mode', 'old_marker', 'old_flow_id', 'bool_index', 'float_index',
    'negative_index', 'too_large_index', 'numeric_ack', 'missing_causality', 'duplicate', 'nonfinite'])
def test_exact_nested_command_rejects_mixed_families_coercion_and_implicit_entry(fault):
    body = command(fixture())
    if fault == 'missing_mode': body.pop('entry_mode')
    elif fault == 'old_marker': body['schema'] = 'business-flow-execution-adopt-command-v1'
    elif fault == 'old_flow_id': body['flow_id'] = 'old_flow'
    elif fault == 'bool_index': body['flow_index'] = True
    elif fault == 'float_index': body['flow_index'] = 0.0
    elif fault == 'negative_index': body['flow_index'] = -1
    elif fault == 'too_large_index': body['flow_index'] = 16
    elif fault == 'numeric_ack': body['consent'] = 1
    elif fault == 'missing_causality': body.pop('causal_bindings')
    raw = wire(body)
    if fault == 'duplicate': raw = raw[:-1] + b',"entry_mode":"engineering_manual"}'
    if fault == 'nonfinite': raw = raw.replace(b'"flow_index":0', b'"flow_index":NaN')
    with pytest.raises(HTTPException) as error: shared.parse(raw, nested.Adopt)
    assert error.value.status_code == 422


def test_entry_mode_is_explicit_and_derived_from_original_trigger_and_review_mode_only():
    package = fixture()['source']; flow = package['qualification']['value']['business']['flows'][0]
    flow['trigger']['kind'] = 'event'
    assert nested.allowed_entry_modes(package, 0) == ['engineering_manual']
    assert nested.entry_policy(package, 0, 'engineering_manual') == {
        'mode': 'engineering_manual', 'source_trigger_kind': 'event'}
    with pytest.raises(HTTPException): nested.entry_policy(package, 0, 'declared_manual')
    q = package['qualification']; q.update(**nested.review.mode_fields('author_self_review'), status='qualified')
    assert nested.allowed_entry_modes(package, 0) == ['authorized_anomaly_handoff']
    with pytest.raises(HTTPException): nested.entry_policy(package, 0, 'engineering_manual')
    flow['trigger']['kind'] = 'manual'
    assert nested.allowed_entry_modes(package, 0) == ['declared_manual']
    assert flow['trigger']['kind'] == 'manual'


def test_preview_freezes_mode_causal_bindings_and_original_recursive_display_without_execute_claim():
    binding = fixture(); p = principal(); body = shared.parse(wire(command(binding)), nested.Adopt)
    preview = shared.make_preview(p, body, binding)
    assert preview['schema'] == nested.PREVIEW and preview['value_schema'] == nested.plans.PLAN
    assert preview['command']['entry_mode'] == 'engineering_manual'
    assert preview['command']['causal_bindings'] == binding['causal_bindings']
    assert preview['command']['schema'] == 'business-flow-nested-execution-preview-command-v195'
    assert 'request_id' not in preview['command'] and 'preview_hash' not in preview['command']
    assert preview['simulation_only'] is True and preview['can_execute'] is False
    assert preview['semantic_quality_verified'] is False
    assert preview['preview_hash'] == digest({key: value for key, value in preview.items() if key != 'preview_hash'})
    issued = shared.origin(identity('adoption'), binding, 'b' * 64)
    assert issued['schema'] == nested.ORIGIN
    before = {'businesses': ['已有普通业务'], 'flows': {'已有普通业务': {'steps': []}},
        'automation': {'policies': {'已有普通业务': {'enabled': False}}}}
    original = copy.deepcopy(before)
    proposal = shared.proposed(before, binding['business_name'], binding, issued)
    assert before == original and proposal['flows']['已有普通业务'] == before['flows']['已有普通业务']
    assert proposal['automation'] == before['automation']
    assert proposal['flows'][binding['business_name']]['source_flow'] == binding['flow']['source_flow']
    assert proposal[shared.CONFIG_KEY][binding['business_name']] == issued


def historical_record():
    binding = fixture(); p = principal(); before = {}
    body = command(binding, before=before)
    first = shared.parse(wire(body), nested.Adopt)
    body['preview_hash'] = shared.make_preview(p, first, binding)['preview_hash']
    parsed = shared.parse(wire(body), nested.Adopt)
    row = NS(id=identity('adoption'), workspace_id=p.workspace_id, actor_id=p.user_id,
        qualification_id=binding['qualification_id'], configuration_id=identity('configuration'),
        request_id=body['request_id'], business_name=binding['business_name'], accepted_revision=2,
        version=1, created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), exact_body=wire(body).decode(),
        body_sha256=hashlib.sha256(wire(body)).hexdigest(), command_hash=digest(parsed.model_dump(mode='json')))
    issued = shared.origin(row.id, binding, row.command_hash)
    after = shared.proposed(before, binding['business_name'], binding, issued)
    result = shared._result(row.id, row.request_id, row.qualification_id, issued, 2, digest(after), binding)
    row.data = {'schema': nested.LEDGER, 'binding': binding, 'origin': issued,
        'preview': shared.make_preview(p, parsed, binding), 'result': result}
    row.record_hash = digest(shared._row_pin(row))
    event = NS(actor_id=p.user_id, object_id=row.id, data=shared._audit(row))
    return p, row, before, after, event


class RecordIO:
    """Only revision/Audit read boundaries; not SQL or persistence evidence."""
    def __init__(self, before, after, event):
        self.snapshots = [before, after]; self.events = [event]; self.reads = []

    def scalar(self, statement):
        assert statement.column_descriptions[0]['entity'] is shared.SetupRevision
        self.reads.append('revision')
        return self.snapshots[len(self.reads) - 1]

    def scalars(self, statement):
        assert statement.column_descriptions[0]['entity'] is shared.Audit
        return self.events


def test_real_historical_checker_preserves_exact_original_receipt_and_engineering_label(monkeypatch):
    p, row, before, after, event = historical_record(); calls = []
    monkeypatch.setattr(shared.sources, 'check_shared_qualification',
        lambda db, current, q: calls.append((current.user_id, q['id'])))
    original = copy.deepcopy(row.data)
    checked = shared.checked_row(RecordIO(before, after, event), p, row)
    assert checked == original and row.data == original
    assert calls == [(p.user_id, row.qualification_id)]
    assert checked['result']['schema'] == nested.RESULT and checked['result']['simulation_only'] is True
    assert checked['result']['review_mode'] == 'engineering_simulation'
    assert checked['result']['can_execute'] is False


@pytest.mark.parametrize('fault', ['audit_missing', 'audit_mode', 'revision_missing', 'revision_changed',
    'raw_changed', 'result_changed', 'ledger_mixed', 'entry_changed'])
def test_historical_checker_rejects_missing_independent_anchors_or_resealed_family_mix(monkeypatch, fault):
    p, row, before, after, event = historical_record()
    monkeypatch.setattr(shared.sources, 'check_shared_qualification', lambda *args: None)
    db = RecordIO(before, after, event)
    if fault == 'audit_missing': db.events = []
    elif fault == 'audit_mode': event.data['simulation_only'] = False
    elif fault == 'revision_missing': db.snapshots[1] = None
    elif fault == 'revision_changed': db.snapshots[1] = {**after, 'unrelated_change': True}
    elif fault == 'raw_changed': row.exact_body += ' '
    elif fault == 'result_changed': row.data['result']['simulation_only'] = False
    elif fault == 'ledger_mixed': row.data['schema'] = shared.LEDGER
    else: row.data['binding']['entry_policy']['mode'] = 'declared_manual'
    # A hash reseal alone never substitutes for the raw command, dual history,
    # independently anchored Audit, compiler or source checks.
    row.record_hash = digest(shared._row_pin(row))
    with pytest.raises(HTTPException) as error: shared.checked_row(db, p, row)
    assert error.value.status_code in {409, 422}


def test_nested_original_nonce_is_recovered_before_fresh_source_or_configuration(monkeypatch):
    raw = wire(command(fixture())); expected = {'original_reply': True}
    monkeypatch.setattr(shared, 'recover_nested', lambda *args: {'found': True, 'result': expected})
    def forbidden(*args, **kwargs): raise AssertionError('no current source/preview on original recovery')
    monkeypatch.setattr(shared, 'prepare', forbidden)
    assert shared.adopt_nested(object(), principal(), raw) == expected


@pytest.mark.parametrize('fault', ['other_family', 'other_actor', 'other_raw'])
def test_recovery_does_not_guess_a_different_family_actor_or_same_nonce_body(monkeypatch, fault):
    p, row, _, _, _ = historical_record()
    monkeypatch.setattr(shared.reviews, '_lock', lambda *args: None)
    monkeypatch.setattr(shared, '_inventory', lambda *args: [row])
    if fault == 'other_family': row.data['schema'] = shared.LEDGER
    elif fault == 'other_actor': row.actor_id = identity('other_actor')
    else: row.body_sha256 = 'f' * 64
    def forbidden(*args): raise AssertionError('mismatch must fail before original result')
    monkeypatch.setattr(shared, 'checked_row', forbidden)
    with pytest.raises(HTTPException): shared.recover_nested(object(), p, row.request_id, hashlib.sha256(row.exact_body.encode()).hexdigest())


@pytest.mark.parametrize('status', [409, 422, 403])
def test_only_proven_preinsert_prepare_rejections_have_not_applied_and_exact_nonce(monkeypatch, status):
    raw = wire(command(fixture())); parsed = shared.parse(raw, nested.Adopt)
    monkeypatch.setattr(shared, 'recover_nested', lambda *args: {'found': False, 'result': None})
    def rejected(*args): raise HTTPException(status, 'explicit fresh gate rejection')
    monkeypatch.setattr(shared, 'prepare', rejected)
    with pytest.raises(HTTPException) as error: shared.adopt_nested(object(), principal(), raw)
    if status in {409, 422}:
        assert error.value.detail['code'] == 'business_flow_nested_execution_adoption_not_applied'
        assert error.value.detail['not_applied'] is True
        assert error.value.detail['request_id'] == str(parsed.request_id)
        assert error.value.detail['body_sha256'] == hashlib.sha256(raw).hexdigest()
    else:
        assert error.value.status_code == 403 and error.value.detail == 'explicit fresh gate rejection'


def test_ordinary_configuration_cannot_insert_nested_origin_or_detach_its_marker():
    binding = fixture(); issued = shared.origin(identity('adoption'), binding, 'a' * 64)
    p = principal(); db = NS(scalars=lambda _: [])
    with pytest.raises(HTTPException): shared.protect_configuration(db, p, {}, {shared.CONFIG_KEY: {'new': issued}})
    with pytest.raises(HTTPException): shared.protect_configuration(db, p, {}, {'flows': {'new': binding['flow']}})


def test_actual_nested_context_exposes_recursive_choices_and_whitelists_capability_metadata(monkeypatch):
    from generic_agents import business_flow_execution_api as api
    from generic_agents.models import Capability, Membership, User
    binding = fixture(); p = principal(); package = binding['source']; calls = []
    cap = NS(id=identity('capability'), workspace_id=p.workspace_id, kind='tool', status='published',
        name='公开合成只读工具', release_version='1.0.0', content_hash='b' * 64,
        data={'allowed_agent_roles': ['business'], 'execution': {'adapter': 'http', 'side_effect': False,
            'endpoint': 'https://secret-endpoint.invalid', 'secret_ref': 'do-not-expose-secret',
            'headers': {'Authorization': 'do-not-expose-token'}, 'input_bindings': {}},
            'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
            'output_schema': {'type': 'object', 'properties': {'result': {'type': 'string'}},
                'required': ['result'], 'additionalProperties': False},
            'budget': {'max_attempts': 1}, 'data_policy': {'external_allowed': False, 'cloud_allowed': False}})
    class CatalogIO:
        def execute(self, statement):
            assert {item['entity'] for item in statement.column_descriptions} == {User, Membership}
            return NS(all=lambda: [(NS(id=p.user_id, username='合成成员'), NS(role='operator'))])
        def scalars(self, statement):
            assert statement.column_descriptions[0]['entity'] is Capability
            return [cap]
    def source_boundary(db, current, qualification_id):
        calls.append((current.user_id, qualification_id)); return copy.deepcopy(package)
    monkeypatch.setattr(shared.sources, 'fresh_nested', source_boundary)
    monkeypatch.setattr(shared, 'current_configuration', lambda *args: NS(id=identity('configuration'), version=1, data={}))
    monkeypatch.setattr(shared, '_inventory', lambda *args: [])
    value = api.nested_context(CatalogIO(), p, binding['qualification_id'], 0)
    assert calls == [(p.user_id, binding['qualification_id'])]
    assert value['schema'] == nested.CONTEXT and value['allowed_entry_modes'] == ['engineering_manual']
    assert value['source_flow'] == package['qualification']['value']['business']['flows'][0]
    all_actions = nested.plans.walk_action_ports(package['qualification']['value'],
        scope_binding=package['scope_binding'])['actions']
    graph_ids = nested.compiler.graph_action_ids(package['qualification']['value'], 0,
        scope_binding=package['scope_binding'])
    assert value['all_action_ports'] == all_actions
    assert value['action_ports'] == [{**row, 'graph_node_id': graph_ids[row['path']]} for row in all_actions]
    assert value['source_plan_hash'] == digest(package['qualification']['value'])
    assert value['scope_binding_hash'] == digest(package['scope_binding'])
    assert {item['kind'] for item in value['controls']} == {'decision', 'parallel'}
    assert value['roles'] == sorted(binding['role_bindings'])
    assert value['members'] == [{'user_id': p.user_id, 'username': '合成成员', 'membership_role': 'operator'}]
    assert value['capabilities'][0]['eligible_agent_roles'] == ['business']
    assert value['capabilities'][0]['input_schema'] == cap.data['input_schema']
    assert value['current_source_checked'] is True and value['can_execute'] is False and value['can_apply'] is False
    serialized = json.dumps(value, ensure_ascii=False)
    assert 'do-not-expose' not in serialized and 'secret-endpoint' not in serialized
    assert 'secret_ref' not in serialized and 'headers' not in value['capabilities'][0]


def test_new_routes_are_registered_and_old_context_does_not_interpret_recursive_value_as_flow_id(monkeypatch):
    from generic_agents import business_flow_execution_api as api
    routes = {(route.path, method) for route in api.router.routes for method in getattr(route, 'methods', set())}
    assert {(shared.PREFIX + '/nested-context', 'GET'), (shared.PREFIX + '/nested-preview', 'POST'),
        (shared.PREFIX + '/nested-adopt', 'POST'), (shared.PREFIX + '/nested-requests/{request_id}', 'GET')} <= routes
    q = fixture()['source']['qualification']
    monkeypatch.setattr(api.qualifications, 'get', lambda *args: q)
    def forbidden(*args): raise AssertionError('old context must reject before interpreting new flow IDs')
    monkeypatch.setattr(shared, 'current_configuration', forbidden)
    with pytest.raises(HTTPException) as error: api.context(object(), principal(), q['id'])
    assert error.value.status_code == 422


def test_actual_service_translation_keeps_source_actor_and_closed_delegation_separate_from_permission():
    binding = fixture(service=True)
    assert nested.check_binding(binding) == binding
    entries = [row for row in binding['translation']['source_mapping']['actions'] if 'service_delegation' in row]
    assert len(entries) == 1 and entries[0]['source_action']['actor'] == 'task_center'
    declared = {'service_actor': 'task_center', 'operation': 'in_app_notice',
        'agent_role': 'business', 'side_effect': True}
    assert nested.service_delegations(binding['translation']) == {entries[0]['graph_node_id']: declared}
    assert next(node for node in binding['graph']['nodes'] if node['id'] == entries[0]['graph_node_id'])['kind'] == 'ability'
    assert binding['flow']['source_flow'] == binding['source']['qualification']['value']['business']['flows'][0]
    assert nested.service_delegations(fixture()['translation']) == {}
    changed = copy.deepcopy(binding['translation']); changed['source_mapping']['actions'][0]['service_delegation'] = {
        **declared, 'implicit_grant': True}
    with pytest.raises((ValueError, HTTPException)): nested.service_delegations(changed)


def test_new_prepare_passes_actual_recompiled_service_delegation_to_current_cap_gate_before_cas(monkeypatch):
    from generic_agents import report_full_semantic_graph_adoption as common
    binding = fixture(service=True); body = shared.parse(wire(command(binding)), nested.Adopt); calls = []
    monkeypatch.setattr(shared.sources, 'fresh_nested', lambda *args: copy.deepcopy(binding['source']))
    monkeypatch.setattr(common, 'members', lambda *args, **kwargs: copy.deepcopy(binding['role_bindings']))
    def capability_boundary(db, p, graph, external_ids, **options):
        calls.append((copy.deepcopy(graph), copy.deepcopy(options)))
        return copy.deepcopy(binding['capability_pins'])
    monkeypatch.setattr(common, 'ability_capabilities', capability_boundary)
    monkeypatch.setattr(shared, 'current_configuration', lambda *args, **kwargs: NS(version=2, data={}))
    with pytest.raises(HTTPException) as error: nested.prepare(object(), principal(), body)
    assert error.value.status_code == 409
    assert calls == [(binding['graph'], {'service_delegations': nested.service_delegations(binding['translation'])})]


@pytest.mark.parametrize('family', ['legacy', 'nested'])
def test_live_origin_uses_service_keyword_only_for_explicit_new_family(monkeypatch, family):
    from generic_agents import report_full_semantic_graph_adoption as common
    binding = legacy_fixture(2) if family == 'legacy' else fixture(service=True)
    issued = shared.origin(identity('adoption'), binding, 'a' * 64); calls = []
    monkeypatch.setattr(shared, 'checked_origin', lambda *args: copy.deepcopy(binding))
    monkeypatch.setattr(shared, 'current_configuration', lambda *args: NS(data={shared.CONFIG_KEY: {binding['business_name']: issued}}))
    monkeypatch.setattr(shared.sources, 'current_materials', lambda *args: calls.append('materials'))
    monkeypatch.setattr(common, 'members', lambda *args: copy.deepcopy(binding['role_bindings']))
    def capability_boundary(db, p, graph, external_ids, **options):
        calls.append(copy.deepcopy(options)); return copy.deepcopy(binding['capability_pins'])
    monkeypatch.setattr(common, 'ability_capabilities', capability_boundary)
    monkeypatch.setattr(common, 'automation', lambda *args: calls.append('automation'))
    assert shared.verify_origin(object(), principal(), issued) == binding
    expected = {} if family == 'legacy' else {'service_delegations': nested.service_delegations(binding['translation'])}
    assert calls == ['materials', expected, 'automation']
