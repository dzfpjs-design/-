"""Pure V185 revision checks over public synthetic V184/source fixtures.

Actual parent/pointer/schema/compilation/source validators execute. No provider,
database, capture authenticity, IAM, semantic quality or runtime is proved.
"""
import copy
import hashlib
import json
import socket
import subprocess
from uuid import NAMESPACE_URL, uuid5

import pytest
from jsonschema import Draft202012Validator

from generic_agents import report_business_flow_revision_policy_v185 as policy
from generic_agents import report_business_flow_wire_v185 as subject
from generic_agents import report_business_flow_wire_v184 as plans
from generic_agents import report_business_flow_wire_v182 as base
from generic_agents.contracts import digest
from generic_agents.typed_payload_codec_v131 import canonical_json
from tests.test_report_business_flow_wire_v184 import scene as plan_scene


@pytest.fixture(autouse=True)
def no_external(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail('pure revision must not contact a service, database or process')
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session
    monkeypatch.setattr(Engine, 'connect', denied)
    monkeypatch.setattr(Session, 'execute', denied)
    monkeypatch.setattr(Session, 'commit', denied)
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket.socket, 'connect_ex', denied)
    monkeypatch.setattr(socket, 'getaddrinfo', denied)
    monkeypatch.setattr(subprocess, 'Popen', denied)


def uid(label):
    return str(uuid5(NAMESPACE_URL, 'public-revision-v185:' + label))


def raw(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')


def seal_parent(parent):
    parent['parent_hash'] = digest({key: value for key, value in parent.items() if key != 'parent_hash'})
    return parent


def scene():
    # The public source has four positions (including a deliberate duplicate).
    # Preserve all four so its allowed-ref union still matches the real gate.
    plan, source, _, _, _ = plan_scene(4)
    binding = source['scope_binding']
    origin = {'schema': subject.PARENT_BINDING, 'attempt_id': uid('attempt'),
        'producer_id': uid('producer'), 'revision': 3, 'model_id': uid('model'),
        'request_hash': digest('synthetic-request'), 'reserved_call_id': uid('call'),
        'capture_hash': digest('synthetic-capture'), 'response_body_sha256': digest('synthetic-envelope'),
        'arguments_sha256': hashlib.sha256(raw(plan)).hexdigest(),
        'source_snapshot_hash': binding['source_snapshot_hash'], 'generation_profile': 'flow-v184',
        'preparation_hash': digest('synthetic-preparation'), 'scope_id': binding['scope_id'],
        'plan_id': uid('plan'), 'plan_hash': binding['plan_hash'], 'raw_plan_hash': digest(plan),
        'scope_binding_hash': digest(binding)}
    parent = seal_parent({'schema': subject.PARENT, 'binding': origin, 'raw_plan': copy.deepcopy(plan)})
    revised = copy.deepcopy(plan)
    revised['business']['flows'][0]['phases'][0]['steps'][0]['role'] = '资料核对负责人'
    path = '/business/flows/0/phases/0/steps/0/role'
    finding = {'kind': 'roles', 'severity': 'major', 'basis': 'both', 'base_paths': [path],
        'revised_paths': [path], 'resolution': 'changed', 'citations': [source['allowed_span_refs'][0]],
        'explanation': '合成示例中的岗位表达需要独立核对，不代表原报告的语义事实。',
        'resolution_explanation': '保留该步骤，另存新的岗位表达，仍需人核对实际权限。'}
    review = {'schema': subject.WIRE, 'binding_hash': digest(binding), 'parent_source_hash': digest(parent),
        'base_plan_hash': digest(plan), 'platform_policy_hash': digest(policy.platform_policy()),
        'findings': [finding], 'revised_plan': revised}
    return review, parent, source


def project(review, parent, source):
    return subject.build_projection(raw(review), parent_source=parent, **source)


def reseal_projection(value):
    value['projection_hash'] = digest({key: item for key, item in value.items() if key != 'projection_hash'})
    return value


def test_policy_is_closed_detached_generic_and_does_not_claim_report_or_execution_authority():
    value = policy.platform_policy(); before = canonical_json(value)
    assert set(value) == {'schema', 'version', 'authority', 'evidence_policy', 'roles', 'planning_rules', 'revision_rules'}
    assert value['schema'] == policy.SCHEMA == policy.PLATFORM_POLICY_SCHEMA
    assert type(value['version']) is int and value['version'] == policy.VERSION == 1
    assert value['authority'] == 'user_defined_platform_architecture'
    assert set(value['roles']) == {'business_agent', 'perception_agent', 'data_agent', 'task_center', 'execution_service', 'human'}
    assert all(word not in before for word in ('林业', '森林', '火情', 'w:p3430', '920', '208'))
    value['roles']['business_agent'] = 'local mutation'; value['planning_rules'].clear()
    assert canonical_json(policy.platform_policy()) == before


def test_exact_nested_schema_hashes_and_local_refs_use_the_real_v184_plan():
    review, parent, source = scene(); b = source['scope_binding']
    schema = subject.output_schema(b, parent_source=parent)
    Draft202012Validator.check_schema(schema)
    assert list(Draft202012Validator(schema).iter_errors(review)) == []
    assert schema == subject._schema_for_shape(b['name'], digest(b), digest(parent),
        digest(parent['raw_plan']), digest(policy.platform_policy()))
    for key in ('binding_hash', 'parent_source_hash', 'base_plan_hash', 'platform_policy_hash'):
        assert schema['properties'][key]['const'] == review[key]
    assert schema['$defs']['BusinessFlowPlanV184']['properties']['binding_hash']['const'] == digest(b)
    assert schema['$defs']['BusinessDefinitionV184']['properties']['name']['const'] == b['name']
    def refs(value):
        if type(value) is dict:
            for key, item in value.items():
                if key == '$ref':
                    assert item.startswith('#/$defs/') and item.split('/')[-1] in schema['$defs']
                refs(item)
        elif type(value) is list:
            for item in value: refs(item)
    refs(schema)
    assert subject.parse_review(raw(review), scope_binding=b, parent_source=parent) == review


@pytest.mark.parametrize('fault', ['schema', 'extra_parent', 'extra_binding', 'profile', 'bool_version',
    'float_version', 'zero_version', 'bad_uuid', 'uppercase_uuid', 'bad_hash', 'scope', 'plan_hash',
    'snapshot_hash', 'binding_hash', 'raw_hash', 'raw_binding', 'raw_schema', 'raw_extra', 'parent_hash'])
def test_parent_binding_cannot_be_relabelled_or_self_resealed_to_another_scope(fault):
    _, parent, source = scene(); origin = parent['binding']
    if fault == 'schema': parent['schema'] = 'business-flow-revision-parent-v184'
    elif fault == 'extra_parent': parent['current_permission'] = True
    elif fault == 'extra_binding': origin['actor_approved'] = True
    elif fault == 'profile': origin['generation_profile'] = 'flow-v183'
    elif fault == 'bool_version': origin['revision'] = True
    elif fault == 'float_version': origin['revision'] = 3.0
    elif fault == 'zero_version': origin['revision'] = 0
    elif fault == 'bad_uuid': origin['reserved_call_id'] = 'not-a-call-uuid'
    elif fault == 'uppercase_uuid': origin['attempt_id'] = origin['attempt_id'].upper()
    elif fault == 'bad_hash': origin['arguments_sha256'] = 'G' * 64
    elif fault == 'scope': origin['scope_id'] = 'another-opaque-scope'
    elif fault == 'plan_hash': origin['plan_hash'] = digest('another-plan')
    elif fault == 'snapshot_hash': origin['source_snapshot_hash'] = digest('another-source')
    elif fault == 'binding_hash': origin['scope_binding_hash'] = digest('another-binding')
    elif fault == 'raw_hash': origin['raw_plan_hash'] = digest('another-value')
    elif fault == 'raw_binding': parent['raw_plan']['binding_hash'] = digest('another-binding')
    elif fault == 'raw_schema': parent['raw_plan']['schema'] = base.WIRE
    elif fault == 'raw_extra': parent['raw_plan']['model_approved'] = True
    else: parent['parent_hash'] = digest('another-parent')
    if fault.startswith('raw_') and fault != 'raw_hash': origin['raw_plan_hash'] = digest(parent['raw_plan'])
    if fault != 'parent_hash': seal_parent(parent)
    with pytest.raises(ValueError): subject.check_parent_source(parent, source['scope_binding'])


def test_scope_identifier_preserves_the_existing_opaque_v182_contract_and_input_is_not_changed():
    review, parent, source = scene(); before = canonical_json([review, parent, source])
    assert ':' in parent['binding']['scope_id']
    checked = subject.check_parent_source(parent, source['scope_binding'])
    checked['raw_plan']['business']['questions'].append({'question': 'local', 'reason': 'local', 'citations': []})
    assert canonical_json([review, parent, source]) == before


@pytest.mark.parametrize('pointer', ['business', '/business/flows/00/name', '/business/flows/-1/name',
    '/business/flows/+0/name', '/business/flows/0.0/name', '/business/flows/٠/name', '/business/flows/1/name',
    '/business/flows/0/missing', '/business/flows/0/name/x', '/business/flows/~2',
    '/business/flows/~', '/business/flows/0/phases/', '/' + 'x' * 2048])
@pytest.mark.parametrize('target', ['base_paths', 'revised_paths'])
def test_findings_require_resolvable_rfc6901_paths_without_guessing_or_array_coercion(pointer, target):
    review, parent, source = scene(); review['findings'][0][target] = [pointer]
    with pytest.raises(ValueError): subject.parse_review(raw(review), scope_binding=source['scope_binding'], parent_source=parent)


def test_root_pointer_and_single_pass_rfc6901_escaping_resolve_real_values():
    review, parent, source = scene()
    review['findings'][0]['base_paths'] = ['']; review['findings'][0]['revised_paths'] = ['']
    assert subject.parse_review(raw(review), scope_binding=source['scope_binding'], parent_source=parent) == review
    value = {'a/b': {'~literal': [7]}, '~1': 'one escape pass', '01': 'dictionary key'}
    assert subject._resolve_pointer(value, '') is value
    assert subject._resolve_pointer(value, '/a~1b/~0literal/0') == 7
    assert subject._resolve_pointer(value, '/~01') == 'one escape pass'
    assert subject._resolve_pointer(value, '/01') == 'dictionary key'


@pytest.mark.parametrize('fault', ['empty_base', 'changed_without_target', 'removed_with_target',
    'kind', 'severity', 'basis', 'resolution', 'blank_explanation', 'blank_resolution', 'extra', 'too_many'])
def test_findings_are_closed_and_do_not_lose_changed_or_removed_target_meaning(fault):
    review, parent, source = scene(); item = review['findings'][0]
    if fault == 'empty_base': item['base_paths'] = []
    elif fault == 'changed_without_target': item['revised_paths'] = []
    elif fault == 'removed_with_target': item['resolution'] = 'removed'
    elif fault in {'kind', 'severity', 'basis', 'resolution'}: item[fault] = 'not-supported'
    elif fault == 'blank_explanation': item['explanation'] = ' \n '
    elif fault == 'blank_resolution': item['resolution_explanation'] = ' '
    elif fault == 'extra': item['semantic_quality_verified'] = True
    else: review['findings'] = [copy.deepcopy(item) for _ in range(65)]
    with pytest.raises(ValueError): subject.parse_review(raw(review), scope_binding=source['scope_binding'], parent_source=parent)


@pytest.mark.parametrize('resolution', ['removed', 'unresolved'])
def test_removed_and_unresolved_findings_remain_candidates_without_claiming_resolution_quality(resolution):
    review, parent, source = scene(); item = review['findings'][0]
    item['resolution'] = resolution; item['revised_paths'] = []
    item['resolution_explanation'] = '这是待核的修订说明，不由纯校验器判断业务意义。'
    if resolution == 'removed':
        review['revised_plan']['business']['flows'][0]['phases'] = []
    result = project(review, parent, source)
    assert result['raw_review']['findings'][0]['resolution'] == resolution
    assert all(result[key] is False for key in subject._FALSE_FLAGS)


@pytest.mark.parametrize('key', ['binding_hash', 'parent_source_hash', 'base_plan_hash', 'platform_policy_hash'])
def test_top_hashes_must_bind_the_exact_parent_policy_and_scope(key):
    review, parent, source = scene(); review[key] = digest('unrelated:' + key)
    with pytest.raises(ValueError): subject.parse_review(raw(review), scope_binding=source['scope_binding'], parent_source=parent)


@pytest.mark.parametrize('fault', ['old_plan', 'old_wire', 'duplicate_json_key', 'extra', 'wrong_name', 'wrong_binding', 'nonfinite'])
def test_old_families_duplicate_keys_and_revised_binding_changes_cannot_enter(fault):
    review, parent, source = scene()
    if fault == 'old_plan': payload = raw(parent['raw_plan'])
    elif fault == 'old_wire': payload = raw(plans.build_projection(raw(parent['raw_plan']), **source)['wire'])
    elif fault == 'duplicate_json_key':
        payload = ('{"schema":"' + subject.WIRE + '",' + raw(review).decode('utf-8')[1:]).encode('utf-8')
    else:
        if fault == 'extra': review['approval'] = True
        elif fault == 'wrong_name': review['revised_plan']['business']['name'] = '另一个业务'
        elif fault == 'wrong_binding': review['revised_plan']['binding_hash'] = digest('other-binding')
        else: review['findings'][0]['explanation'] = float('nan')
        payload = json.dumps(review, ensure_ascii=False, allow_nan=True).encode('utf-8')
    with pytest.raises(ValueError): subject.parse_review(payload, scope_binding=source['scope_binding'], parent_source=parent)


def test_all_finding_citations_are_checked_even_if_not_used_by_the_revised_graph():
    review, parent, source = scene(); review['findings'][0]['citations'] = ['w:p999999/s1']
    original = raw(review)
    # Source-free parsing cannot prove a citation belongs to an actual catalog.
    assert subject.parse_review(original, scope_binding=source['scope_binding'], parent_source=parent) == review
    with pytest.raises(ValueError): project(review, parent, source)
    assert raw(review) == original


def test_projection_keeps_real_raw_review_and_compiled_layers_independent_and_does_not_modify_inputs():
    review, parent, source = scene(); before = canonical_json([review, parent, source]); actual = raw(review)
    result = subject.build_projection(actual, parent_source=parent, **source)
    assert result['schema'] == subject.PROJECTION and result['wire_schema'] == base.WIRE
    assert result['raw_review'] == review and result['raw_review_value_hash'] == digest(review)
    assert result['raw_review_bytes_sha256'] == hashlib.sha256(actual).hexdigest()
    assert result['raw_review_bytes'] == len(actual)
    assert result['parent_source'] == parent and result['parent_source_hash'] == digest(parent)
    assert result['revised_plan'] == review['revised_plan']
    assert result['revised_projection']['schema'] == plans.PROJECTION
    assert result['revised_projection']['raw_plan'] == review['revised_plan']
    assert result['compiled_wire_bytes_sha256'] == hashlib.sha256(
        canonical_json(result['wire']).encode('utf-8')).hexdigest()
    assert result['raw_review_bytes_sha256'] != result['compiled_wire_bytes_sha256']
    assert result['compilation_manifest'] == result['revised_projection']['compilation_manifest']
    assert result['selected_input_claims'] == source['input_claims']
    assert result['partial_scope'] is True and all(result[key] is False for key in subject._FALSE_FLAGS)
    assert canonical_json([review, parent, source]) == before
    assert subject.check_projection(result, actual, parent_source=parent, **source) == result
    result['parent_source']['raw_plan']['business']['flows'].clear()
    result['wire']['business']['flows'][0]['nodes'][0]['role'] = 'local mutation'
    assert canonical_json([review, parent, source]) == before


def test_actual_v184_and_unchanged_v182_gates_are_not_replaced_by_revision_self_hashes(monkeypatch):
    review, parent, source = scene(); called = []
    actual = plans.build_projection
    def tracked(*args, **kwargs):
        called.append(1)
        return actual(*args, **kwargs)
    monkeypatch.setattr(plans, 'build_projection', tracked)
    assert project(review, parent, source)['wire']['schema'] == base.WIRE
    assert called == [1]
    def blocked(*args, **kwargs):
        raise ValueError('actual unchanged V182 gate refused')
    monkeypatch.setattr(base, 'build_projection', blocked)
    with pytest.raises(ValueError, match='actual unchanged V182'): project(review, parent, source)


@pytest.mark.parametrize('fault', ['raw_review', 'parent', 'policy', 'revised_plan', 'compiled_wire', 'manifest', 'flag', 'extra'])
def test_cold_rebuild_rejects_resealed_projection_and_original_plan_mutation(fault):
    review, parent, source = scene(); value = project(review, parent, source)
    if fault == 'raw_review': value['raw_review']['findings'][0]['explanation'] += '改写'
    elif fault == 'parent': value['parent_source']['binding']['request_hash'] = digest('other-request')
    elif fault == 'policy': value['platform_policy']['roles']['business_agent'] = 'changed'
    elif fault == 'revised_plan': value['revised_plan']['business']['flows'][0]['completion']['role'] = '改写岗位'
    elif fault == 'compiled_wire': value['wire']['business']['flows'][0]['nodes'][0]['role'] = '改写岗位'
    elif fault == 'manifest': value['compilation_manifest']['algorithm'] = 'different-compiler'
    elif fault == 'flag': value['can_execute'] = True
    else: value['current_permission'] = True
    reseal_projection(value)
    with pytest.raises(ValueError): subject.check_projection(value, raw(review), parent_source=parent, **source)


def test_manual_is_the_compiled_v182_draft_with_both_acknowledgements_false_and_separate_private_note():
    review, parent, source = scene(); result = project(review, parent, source)
    manual = subject.new_manual(result)
    assert manual == {'schema': base.MANUAL, 'result': result['wire'], 'note': '',
        'acknowledge_partial_scope': False, 'acknowledge_inferences': False}
    manual['note'] = '仅本人可见的核对说明，不进入原模型回答。'
    assert subject.validate_value(manual, **source) == manual
    assert subject.manual_value_schema(source['scope_binding']) == plans.manual_value_schema(source['scope_binding'])
    assert manual['note'] not in canonical_json([review, parent, result])


def test_complete_projection_capacity_fails_instead_of_truncating_findings_or_source(monkeypatch):
    review, parent, source = scene(); before = canonical_json([review, parent, source])
    monkeypatch.setattr(subject, 'MAX_PROJECTION_BYTES', 1)
    with pytest.raises(subject.BusinessFlowRevisionCapacityError): project(review, parent, source)
    assert canonical_json([review, parent, source]) == before
