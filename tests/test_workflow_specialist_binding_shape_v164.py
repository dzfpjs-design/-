"""Actual pin validation with one explicit Capability SELECT seam.

This is pure protocol/IO coverage, not a real publication, installation, model
call, Task, or PostgreSQL authorization proof. The real published/eligible/pin
functions run unchanged; only the scoped Capability read is supplied here.
"""
from copy import deepcopy
from operator import eq
from types import SimpleNamespace

from fastapi import HTTPException
import pytest
from sqlalchemy.sql.elements import BinaryExpression, BindParameter

from generic_agents.contracts import Manifest, digest
from generic_agents.models import Capability
from generic_agents import workflow_abilities as abilities


WORKSPACE = '164bea00-0000-4000-8000-000000000001'
CAPABILITY = '164bea00-0000-4000-8000-000000000002'
INSTALLATION = '164bea00-0000-4000-8000-000000000003'


class CapabilityRead:
    """Exactly one real scoped/published SELECT, with no write fallback."""
    def __init__(self, row):
        self.row = row
        self.reads = 0

    def scalar(self, statement):
        assert self.reads == 0
        assert statement.column_descriptions[0]['entity'] is Capability
        assert statement._for_update_arg is None
        predicates = tuple(statement._where_criteria)
        assert len(predicates) == 2
        for predicate, (field, value) in zip(predicates,
                [('id', CAPABILITY), ('workspace_id', WORKSPACE)]):
            assert isinstance(predicate, BinaryExpression) and predicate.operator is eq
            assert predicate.left.compare(Capability.__table__.c[field])
            assert isinstance(predicate.right, BindParameter)
            assert type(predicate.right.value) is str and predicate.right.value == value
        self.reads += 1
        return self.row


def context():
    value = Manifest(kind='specialist_model', namespace='binding_shape_v164',
        name='local_detection', version='1.0.1', owner='纯协议测试',
        description='本地候选检测输入绑定；不代表真实安装或推理',
        allowed_agent_roles=['perception', 'data'],
        input_schema={'type': 'object', 'required': ['evidence_ids'],
            'additionalProperties': False, 'properties': {
                'evidence_ids': {'type': 'array', 'minItems': 1, 'maxItems': 1,
                    'items': {'type': 'string'}},
                'attributes': {'type': 'object'}}},
        output_schema={'type': 'object', 'required': ['attributes', 'anomalies', 'detection'],
            'additionalProperties': False, 'properties': {
                'attributes': {'type': 'object'}, 'anomalies': {'type': 'array', 'maxItems': 0},
                'detection': {'type': 'object'}}},
        execution={'adapter': 'local_specialist', 'installation_id': INSTALLATION,
            'generation': 1, 'recipe_hash': 'a' * 64, 'backend': 'cpu', 'side_effect': False},
        data_policy={'cloud_allowed': False, 'external_allowed': False, 'deployment': 'local'},
        specification={'automatic_domain_use': False}).model_dump(mode='json')
    fingerprint = digest(value)
    row = Capability(id=CAPABILITY, workspace_id=WORKSPACE, namespace=value['namespace'],
        name=value['name'], release_version=value['version'], kind=value['kind'],
        status='published', version=1, data=value, content_hash=fingerprint,
        validated_hash=fingerprint)
    principal = SimpleNamespace(workspace_id=WORKSPACE)
    return CapabilityRead(row), principal, row


def step(binding):
    return {'id': 'detect', 'agent': '多模感知智能体', 'condition': '',
        'execution': {'capability_id': CAPABILITY, 'automation_action': 'observe',
            'arguments': {}, 'bindings': {'evidence_ids': deepcopy(binding)}}}


def rejected(value):
    db, principal, cap = context()
    before = deepcopy(value)
    original_cap = deepcopy(cap.data)
    with pytest.raises(HTTPException) as error:
        abilities.pin(db, principal, value, {'rules': {'observe': {'mode': '自动运行'}}})
    assert error.value.status_code == 422
    assert db.reads == 1 and value == before
    assert cap.data == original_cap and digest(cap.data) == cap.content_hash


@pytest.mark.parametrize('binding', [
    None, 'perception_frame_ids', 'task_image_ids', [], ['perception_frame_ids'],
    True, False, 0, 1, 1.0,
])
def test_actual_pin_rejects_non_object_specialist_evidence_binding_as_422(binding):
    rejected(step(binding))


@pytest.mark.parametrize('binding', [
    {}, {'source': None}, {'source': ''}, {'source': 'invented'},
    {'source': True}, {'source': 1}, {'source': 1.0}, {'source': []}, {'source': {}},
])
def test_actual_pin_rejects_invalid_source_shape_before_specialist_check(binding):
    rejected(step(binding))


@pytest.mark.parametrize('source', ['task_image_ids', 'perception_frame_ids'])
@pytest.mark.parametrize('mode', ['自动运行', '执行前确认'])
def test_actual_pin_preserves_legal_task_or_associated_frame_source_and_original_pin(source, mode):
    db, principal, cap = context()
    value = step({'source': source})
    before = deepcopy(value)
    result = abilities.pin(db, principal, value, {'rules': {'observe': {'mode': mode}}})
    assert result == {**before['execution'], 'capability_hash': cap.content_hash,
        'capability_name': cap.data['description'], 'output_schema': cap.data['output_schema'],
        'release_version': '1.0.1', 'agent_role': 'perception', 'mode': mode,
        'adapter': 'local_specialist'}
    assert db.reads == 1 and value == before
    assert digest(cap.data) == cap.content_hash == cap.validated_hash
    result['bindings']['evidence_ids']['source'] = 'changed-result-only'
    assert value == before


@pytest.mark.parametrize('binding', [
    {'source': 'original_evidence_ids'}, {'source': 'object_records'},
    {'source': 'previous_output', 'step_id': 'prior', 'field': 'evidence_ids'},
])
def test_generic_valid_sources_cannot_replace_selected_images_or_associated_frames(binding):
    rejected(step(binding))


@pytest.mark.parametrize('arguments', [{}, {'evidence_ids': ['constant-image-id']}])
def test_missing_specialist_binding_or_constant_argument_is_still_rejected(arguments):
    value = step({'source': 'task_image_ids'})
    value['execution'].update(bindings={}, arguments=arguments)
    rejected(value)


@pytest.mark.parametrize('binding', [None, 'object_attributes'])
def test_all_bindings_receive_generic_shape_validation_before_specialist_check(binding):
    value = step({'source': 'task_image_ids'})
    value['execution']['bindings']['attributes'] = binding
    rejected(value)
