"""Pure contracts and stubbed bridge interfaces, not stream/native/ACK evidence."""
import copy
import hashlib
import sys
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException
from jsonschema import Draft202012Validator, ValidationError
import pytest

from generic_agents import audio_observation_model as subject
from generic_agents import local_sound
from generic_agents.contracts import digest


def model_trace():
    return {'installation_id': str(uuid4()), 'pack_id': local_sound.PACK, 'generation': 1,
        'recipe_hash': local_sound.RECIPE_HASH, 'backend': 'cpu',
        'model_sha256': local_sound.RECIPE['files'][0]['sha256'],
        'labels_sha256': local_sound.RECIPE['files'][1]['sha256']}


@pytest.fixture
def interface(monkeypatch):
    """No database, model, device or receiver exists in this fixture."""
    import generic_agents
    principal = SimpleNamespace(workspace_id=str(uuid4()), user_id=str(uuid4()), role='operator')
    value = subject.manifest(model_trace()).model_dump(mode='json')
    cap = SimpleNamespace(id=str(uuid4()), workspace_id=principal.workspace_id,
        namespace=value['namespace'], name=value['name'], release_version=value['version'],
        kind=value['kind'], status='published', data=value, content_hash=digest(value),
        validated_hash=digest(value))
    content = b'explicit interface fixture, not real audio or inference'
    evidence = SimpleNamespace(id=str(uuid4()), workspace_id=principal.workspace_id,
        size_bytes=len(content), sha256=hashlib.sha256(content).hexdigest(), media_type='audio/wav')
    selected = {'channel': 'mix', 'threshold': .75}
    proof = {'schema': 'test-only-stream-proof', 'evidence_id': evidence.id, 'source_hash': 'a' * 64}
    scene = SimpleNamespace(p=principal, cap=cap, content=content, evidence=evidence,
        configuration=selected, proof=proof, bridge_calls=[], trace_calls=[], model_calls=[], validations=[])

    def capture(db, p, identity, cap_id, cap_hash, *, key, execution):
        assert db is None and p is principal and identity == evidence.id
        assert cap_id == cap.id and cap_hash == cap.content_hash and execution is True
        scene.bridge_calls.append((identity, cap_id, cap_hash, key, execution))
        return {'evidence': evidence, 'content': content, 'configuration': copy.deepcopy(selected),
            'source_proof': copy.deepcopy(proof)}

    def checked_trace(db, p, trace, *, live=False):
        assert db is None and p is principal
        scene.trace_calls.append((copy.deepcopy(trace), live))
        return {'proof': {'test_only': True}}

    def call(raw, configuration, guard):
        scene.model_calls.append((raw, copy.deepcopy(configuration)))
        guard()
        # This synthetic payload deliberately is not advertised as a native
        # model result. The validator stub only exercises call plumbing.
        return {'test_only_model_interface_payload': True}

    def validate(result, raw, configuration):
        assert result == {'test_only_model_interface_payload': True}
        assert raw == content and configuration == selected
        scene.validations.append(copy.deepcopy(result))
        return result

    bridge = SimpleNamespace(check_capture_input=capture)
    monkeypatch.setitem(sys.modules, 'generic_agents.audio_streams', bridge)
    monkeypatch.setattr(generic_agents, 'audio_streams', bridge, raising=False)
    monkeypatch.setattr(local_sound, 'checked_trace', checked_trace)
    monkeypatch.setattr(local_sound, 'call', call)
    monkeypatch.setattr(local_sound, 'validate', validate)
    scene.bridge = bridge
    scene.inputs = {'attributes': {}, 'evidence_ids': [evidence.id]}
    return scene


def test_fixed_manifest_is_a_separate_local_candidate_capability():
    trace = model_trace(); value = subject.manifest(trace).model_dump(mode='json')
    assert value['kind'] == 'specialist_model'
    assert value['allowed_agent_roles'] == ['perception', 'data']
    assert value['execution']['adapter'] == subject.ADAPTER != 'local_environment_sound'
    assert value['execution']['side_effect'] is False
    assert value['data_policy'] == {'deployment': 'local', 'cloud_allowed': False, 'external_allowed': False}
    assert value['specification']['candidate_only'] is True
    assert set(value['input_schema']['properties']) == {'attributes', 'evidence_ids'}
    assert set(value['output_schema']['properties']) == {'attributes', 'anomalies', 'environment_sound'}
    assert subject.manifest({**trace, 'generation': 2}).version != value['version']
    trace['generation'] = 10
    assert value['execution']['model_trace']['generation'] == 1


@pytest.mark.parametrize('patch', [
    {'generation': True}, {'generation': 0}, {'installation_id': 'other'},
    {'model_sha256': '0' * 64}, {'labels_sha256': '0' * 64},
    {'pack_id': 'other'}, {'backend': 'cuda'}, {'extra': True},
])
def test_manifest_rejects_different_or_incomplete_fixed_installation(patch):
    with pytest.raises(HTTPException) as caught:
        subject.manifest({**model_trace(), **patch})
    assert caught.value.status_code == 422


def test_validation_uses_real_installation_contract_and_explicit_history_mode(interface):
    s = interface
    assert subject.validate_manifest(None, s.p, s.cap.data).model_dump(mode='json') == s.cap.data
    assert s.trace_calls[-1][1] is True
    subject.validate_manifest(None, s.p, s.cap.data, live=False)
    assert s.trace_calls[-1][1] is False


@pytest.mark.parametrize('change', ['adapter', 'cloud', 'attributes', 'input', 'roles', 'configuration'])
def test_cannot_widen_a_registered_observation_manifest(interface, change):
    s = interface; value = copy.deepcopy(s.cap.data)
    if change == 'adapter': value['execution']['adapter'] = 'local_environment_sound'
    if change == 'cloud': value['data_policy']['cloud_allowed'] = True
    if change == 'attributes': value['output_schema']['properties']['attributes'].pop('maxProperties')
    if change == 'input': value['input_schema']['additionalProperties'] = True
    if change == 'roles': value['allowed_agent_roles'].append('business')
    if change == 'configuration': value['execution']['threshold'] = .1
    with pytest.raises(HTTPException) as caught:
        subject.validate_manifest(None, s.p, value)
    assert caught.value.status_code == 422 and not s.trace_calls


@pytest.mark.parametrize('value', [
    {}, {'attributes': {'bird_count': 1}, 'evidence_ids': ['id']},
    {'attributes': {}, 'evidence_ids': []}, {'attributes': {}, 'evidence_ids': ['other']},
    {'attributes': {}, 'evidence_ids': [str(uuid4()), str(uuid4())]},
    {'attributes': {}, 'evidence_ids': [str(uuid4())], 'channel': '1'},
    {'attributes': {}, 'evidence_ids': [str(uuid4())], 'threshold': .1},
    {'attributes': [], 'evidence_ids': [str(uuid4())]},
])
def test_runtime_rejects_client_facts_configuration_or_nonunique_original_before_bridge(interface, value):
    s = interface
    with pytest.raises(HTTPException) as caught:
        subject.execute(None, s.p, s.cap, value, 'test-interface-key')
    assert caught.value.status_code == 422
    assert not s.bridge_calls and not s.model_calls


def test_manifest_schemas_exclude_fact_values_extra_inputs_and_outputs():
    value = subject.manifest(model_trace())
    inputs = Draft202012Validator(value.input_schema)
    inputs.validate({'attributes': {}, 'evidence_ids': [str(uuid4())]})
    with pytest.raises(ValidationError):
        inputs.validate({'attributes': {'fire': True}, 'evidence_ids': [str(uuid4())]})
    outputs = Draft202012Validator(value.output_schema)
    with pytest.raises(ValidationError):
        outputs.validate({'attributes': {'risk': 1}, 'anomalies': [], 'environment_sound': {}})
    with pytest.raises(ValidationError):
        outputs.validate({'attributes': {}, 'anomalies': [{'label': 'fire'}], 'environment_sound': {}})


def test_interface_stub_uses_only_trusted_bridge_original_and_configuration(interface):
    s = interface
    result = subject.execute(None, s.p, s.cap, s.inputs, 'test-interface-key')
    assert result == {'attributes': {}, 'anomalies': [],
        'environment_sound': {'test_only_model_interface_payload': True}}
    assert s.model_calls == [(s.content, s.configuration)] and len(s.validations) == 1
    assert len(s.bridge_calls) >= 4 and all(item[-1] is True for item in s.bridge_calls)
    assert all(item[1:3] == (s.cap.id, s.cap.content_hash) for item in s.bridge_calls)
    assert all(live is True for _, live in s.trace_calls)


@pytest.mark.parametrize('change', ['workspace', 'hash', 'unpublished', 'version'])
def test_direct_caller_cannot_substitute_capability_row_or_scope(interface, change):
    s = interface
    if change == 'workspace': s.cap.workspace_id = str(uuid4())
    if change == 'hash': s.cap.content_hash = '0' * 64
    if change == 'unpublished': s.cap.status = 'draft'
    if change == 'version': s.cap.release_version = '9.9.9'
    with pytest.raises(HTTPException):
        subject.execute(None, s.p, s.cap, s.inputs, 'test-interface-key')
    assert not s.model_calls and not s.bridge_calls


def test_different_but_valid_evidence_uuid_is_not_authorized_by_the_manifest(interface):
    s = interface
    def denied(*args, **kwargs): raise HTTPException(409, 'different captured evidence')
    s.bridge.check_capture_input = denied
    with pytest.raises(HTTPException):
        subject.execute(None, s.p, s.cap, {'attributes': {}, 'evidence_ids': [str(uuid4())]}, 'test-key')
    assert not s.model_calls


@pytest.mark.parametrize('change', ['bytes', 'id', 'workspace', 'configuration', 'proof'])
def test_bridge_result_must_preserve_immutable_bytes_identity_and_configuration(interface, change):
    s = interface; original = s.bridge.check_capture_input
    def changed(*args, **kwargs):
        value = original(*args, **kwargs)
        if change == 'bytes': value['content'] += b'changed'
        if change == 'id': value['evidence'] = SimpleNamespace(**{**vars(s.evidence), 'id': str(uuid4())})
        if change == 'workspace': value['evidence'] = SimpleNamespace(**{**vars(s.evidence), 'workspace_id': str(uuid4())})
        if change == 'configuration': value['configuration'] = {'channel': 'mix', 'threshold': True}
        if change == 'proof': value['source_proof'] = {}
        return value
    s.bridge.check_capture_input = changed
    with pytest.raises(HTTPException):
        subject.execute(None, s.p, s.cap, s.inputs, 'test-key')
    assert not s.model_calls


def test_guard_change_during_runner_does_not_return_or_hold_unvalidated_output(interface, monkeypatch):
    s = interface
    def runner(content, configuration, guard):
        s.proof['source_hash'] = 'b' * 64
        guard()
        raise AssertionError('guard must reject before a result exists')
    monkeypatch.setattr(local_sound, 'call', runner)
    with pytest.raises(HTTPException):
        subject.execute(None, s.p, s.cap, s.inputs, 'test-key')
    assert not s.validations


def test_only_validated_output_is_held_when_final_authority_changes(interface, monkeypatch):
    s = interface; expected_proof_hash = digest(s.proof)
    real_validate = local_sound.validate
    def validate_then_revoke(result, content, configuration):
        real_validate(result, content, configuration)
        s.proof['source_hash'] = 'b' * 64
    monkeypatch.setattr(local_sound, 'validate', validate_then_revoke)
    with pytest.raises(subject.SoundObservationOutputHeld) as caught:
        subject.execute(None, s.p, s.cap, s.inputs, 'test-key')
    held = caught.value
    assert held.output == {'attributes': {}, 'anomalies': [],
        'environment_sound': {'test_only_model_interface_payload': True}}
    assert held.source_proof_hash == expected_proof_hash
    assert 'source_hash' not in str(held) and s.content.decode() not in str(held)


def test_invalid_model_output_is_not_converted_to_a_held_calculation(interface, monkeypatch):
    s = interface
    def invalid(*args): raise ValueError('invalid raw model result')
    monkeypatch.setattr(local_sound, 'validate', invalid)
    with pytest.raises(ValueError, match='invalid raw model'):
        subject.execute(None, s.p, s.cap, s.inputs, 'test-key')


def test_family_and_verification_delegate_to_origin_bridge_not_candidate_flag(interface):
    s = interface; row = SimpleNamespace(data={'candidate_only': True}); calls = []
    def marker(db, p, actual):
        assert actual is row
        calls.append('marker'); return False
    def verify(db, p, actual, extractor_id, *, execution):
        assert actual is row and extractor_id == s.cap.id
        calls.append(execution); return s.evidence
    s.bridge.observation_marker = marker; s.bridge.check_observation = verify
    assert subject.observation_marker(None, s.p, row) is False
    assert subject.handles_observation(None, s.p, row) is False
    assert subject.verify_observation(None, s.p, row, s.cap.id, execution=False) is s.evidence
    assert calls == ['marker', 'marker', False]


def test_readonly_actor_cannot_directly_execute_the_model(interface):
    s = interface; s.p.role = 'viewer'
    with pytest.raises(HTTPException) as caught:
        subject.execute(None, s.p, s.cap, s.inputs, 'test-key')
    assert caught.value.status_code == 403 and not s.bridge_calls
