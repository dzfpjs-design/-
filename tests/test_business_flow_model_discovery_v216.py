"""Pure discovery projection; model authorization is an explicit mocked edge.

Real V182 input-policy validation and the available_models function run. This
does not prove database permissions, real HTTP discovery, provider quality,
source admission, spending or execution readiness.
"""
import copy
import json
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import HTTPException

from generic_agents import report_business_flow_native_v182 as native
from generic_agents import report_business_flow_native_v183 as compatible_native
from generic_agents import report_business_flow_native_v184 as planning_native


@pytest.fixture
def discovery(monkeypatch):
    # Initialize the real composite in its established order, avoiding the
    # historical setup_api/understanding import cycle used by older tests too.
    from generic_agents import api as application
    from generic_agents import setup_understanding as subject
    principal = NS(workspace_id=str(uuid4()))
    rows, checked = [], []

    class ReadOnlyCapabilities:
        def scalars(self, statement):
            parameters = statement.compile().params
            assert parameters['workspace_id_1'] == principal.workspace_id
            assert parameters['kind_1'] == 'foundation_model'
            assert parameters['status_1'] == 'published'
            return NS(all=lambda: list(rows))

    denied = set()

    def current_model(db, actor, identifier):
        assert actor is principal
        checked.append(identifier)
        if identifier in denied:
            raise HTTPException(403, 'public mocked discovery permission denial')
        return next(row for row in rows if row.id == identifier)

    monkeypatch.setattr(subject, 'model', current_model)
    return NS(subject=subject, principal=principal, db=ReadOnlyCapabilities(), rows=rows,
              checked=checked, denied=denied)


def capability(workspace, module=native):
    return NS(id=str(uuid4()), workspace_id=workspace, kind='foundation_model', status='published',
        name='公开流程设计模型', release_version='1.0.0', data={'execution': {
            'provider': 'qwen', 'model': module.MODEL, 'max_tokens': 8192,
            'secret_ref': 'DASHSCOPE_API_KEY', 'private_for_test': 'must-not-leak',
            'report_evidence_output': {'schema': module.OUTPUT_DECLARATION, 'max_tokens': 8192,
                'reduce_transport': module.DELIVERY, 'endpoint': module.ENDPOINT}},
            'data_policy': {'cloud_allowed': True, 'private_policy': 'must-not-leak'}})


def test_published_v182_discovery_exposes_only_true_public_family_metadata(discovery):
    cap = capability(discovery.principal.workspace_id)
    before = copy.deepcopy(cap.data)
    discovery.rows.append(cap)
    [item] = discovery.subject.available_models(discovery.principal, discovery.db)
    assert discovery.checked == [cap.id]
    assert type(item['generation_blocked_reason']) is str
    assert 'V182' in item['generation_blocked_reason'] and '中文' in item['generation_blocked_reason']
    assert {key: value for key, value in item.items() if key != 'generation_blocked_reason'} == {
        'id': cap.id, 'name': cap.name, 'version': '1.0.0', 'provider': 'qwen',
        'model': native.MODEL, 'generation_profile': 'flow-v182', 'status': 'published',
        'execution': {'provider': 'qwen', 'model': native.MODEL,
            'report_evidence_output': before['execution']['report_evidence_output']}}
    assert 'secret_ref' not in json.dumps(item) and 'must-not-leak' not in json.dumps(item)
    item['execution']['report_evidence_output']['max_tokens'] = 128
    assert cap.data == before, 'UI edits must not mutate the registered capability'


def test_v183_is_a_distinct_explicit_compatible_family_not_an_automatic_rewrite(discovery):
    old = capability(discovery.principal.workspace_id)
    compatible = capability(discovery.principal.workspace_id, compatible_native)
    before = copy.deepcopy([old.data, compatible.data])
    discovery.rows.extend([old, compatible])
    legacy, item = discovery.subject.available_models(discovery.principal, discovery.db)
    assert legacy['generation_profile'] == 'flow-v182' and legacy['generation_blocked_reason']
    assert item == {'id': compatible.id, 'name': compatible.name, 'version': compatible.release_version,
        'provider': 'qwen', 'model': compatible_native.MODEL, 'generation_profile': 'flow-v183',
        'status': 'published', 'execution': {'provider': 'qwen', 'model': compatible_native.MODEL,
            'report_evidence_output': before[1]['execution']['report_evidence_output']}}
    assert legacy['id'] != item['id'] and 'generation_blocked_reason' not in item
    assert [old.data, compatible.data] == before


def test_a_mismatched_v183_declaration_is_not_discovered(discovery):
    cap = capability(discovery.principal.workspace_id, compatible_native)
    cap.data['execution']['report_evidence_output']['reduce_transport'] = native.DELIVERY
    discovery.rows.append(cap)
    assert discovery.subject.available_models(discovery.principal, discovery.db) == []


@pytest.mark.parametrize('fault', ['endpoint', 'transport', 'model', 'provider', 'boolean_budget', 'extra_declaration'])
def test_invalid_v182_declaration_is_not_advertised_as_a_usable_family(discovery, fault):
    cap = capability(discovery.principal.workspace_id)
    execution = cap.data['execution']
    declaration = execution['report_evidence_output']
    if fault == 'endpoint':
        declaration['endpoint'] = 'https://wrong.invalid/'
    elif fault == 'transport':
        declaration['reduce_transport'] = 'different-family'
    elif fault == 'model':
        execution['model'] = 'different-model'
    elif fault == 'provider':
        execution['provider'] = 'deepseek'
    elif fault == 'boolean_budget':
        declaration['max_tokens'] = True
    else:
        declaration['private_schema_override'] = {'type': 'object'}
    discovery.rows.append(cap)
    assert discovery.subject.available_models(discovery.principal, discovery.db) == []
    assert discovery.checked == [cap.id]


def test_current_model_permission_denial_still_precedes_v182_projection(discovery):
    cap = capability(discovery.principal.workspace_id)
    discovery.rows.append(cap)
    discovery.denied.add(cap.id)
    assert discovery.subject.available_models(discovery.principal, discovery.db) == []
    assert discovery.checked == [cap.id]


def test_existing_generic_and_v184_discovery_metadata_are_unchanged(discovery):
    generic = capability(discovery.principal.workspace_id)
    generic.data['execution'].pop('report_evidence_output')
    planned = capability(discovery.principal.workspace_id, planning_native)
    discovery.rows.extend([generic, planned])
    basic, item = discovery.subject.available_models(discovery.principal, discovery.db)
    assert basic == {'id': generic.id, 'name': generic.name, 'version': generic.release_version,
                     'provider': 'qwen', 'model': native.MODEL}
    assert item == {'id': planned.id, 'name': planned.name, 'version': planned.release_version,
        'provider': 'qwen', 'model': planning_native.MODEL, 'generation_profile': 'flow-v184',
        'status': 'published', 'execution': {'provider': 'qwen', 'model': planning_native.MODEL,
            'report_evidence_output': planned.data['execution']['report_evidence_output']}}
    assert discovery.checked == [generic.id, planned.id]
