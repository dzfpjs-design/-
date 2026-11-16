"""Pure data-facet contracts and stubbed registry reads, not capture or grants."""
import copy
import sys
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException
import pytest

from generic_agents import audio_stream_data_plan as subject
from generic_agents import audio_observation_model, local_sound
from generic_agents.contracts import Manifest, digest


def signed(value, key):
    body = {k: v for k, v in value.items() if k != key}
    return {**body, key: digest(body)}


@pytest.fixture
def resource_scene(monkeypatch):
    """Only public contracts and in-memory rows; no gateway/model/network IO."""
    import generic_agents
    p = SimpleNamespace(workspace_id=str(uuid4()))
    gateway = SimpleNamespace(id=str(uuid4()), workspace_id=p.workspace_id, status='ready',
        data={'generation': 1, 'api_origin': 'http://127.0.0.1:19997', 'rtsp_origin': 'rtsp://127.0.0.1:18554'})
    # A pure-audio confirmed channel has no H264/video-validation/read consent.
    channel = SimpleNamespace(id=str(uuid4()), workspace_id=p.workspace_id, gateway_id=gateway.id,
        path='audio/example', status='confirmed', data={'generation': 1})
    def connection_hash(ch, gw):
        return digest({'channel': {'id': ch.id, 'path': ch.path, 'gateway_id': ch.gateway_id,
            'generation': ch.data['generation']}, 'gateway': {key: gw.data[key]
            for key in ('api_origin', 'rtsp_origin', 'generation')}})
    def source_manifest(ch, gw):
        return Manifest(kind='source', namespace='platform_gateway_audio', name='audio_test_source', version='1.0.0',
            owner='test-only registry', description='test metadata contract; not recording permission',
            allowed_agent_roles=['perception', 'data'], execution={'adapter': 'audio_stream_source',
                'channel_id': ch.id, 'gateway_id': gw.id, 'channel_generation': ch.data['generation'],
                'gateway_generation': gw.data['generation'], 'source_identity_hash': connection_hash(ch, gw),
                'side_effect': False})
    def capability(value):
        data = value.model_dump(mode='json')
        return SimpleNamespace(id=str(uuid4()), workspace_id=p.workspace_id, kind=data['kind'],
            namespace=data['namespace'], name=data['name'], release_version=data['version'],
            status='published', data=data, content_hash=digest(data), validated_hash=digest(data))
    trace = {'installation_id': str(uuid4()), 'pack_id': local_sound.PACK, 'generation': 1,
        'recipe_hash': local_sound.RECIPE_HASH, 'backend': 'cpu', 'model_sha256': local_sound.RECIPE['files'][0]['sha256'],
        'labels_sha256': local_sound.RECIPE['files'][1]['sha256']}
    model = capability(audio_observation_model.manifest(trace))
    source = capability(source_manifest(channel, gateway))
    calls = []
    def validate_source(db, principal, value):
        assert principal is p and db is None
        assert digest(value.model_dump(mode='json')) == digest(source_manifest(channel, gateway).model_dump(mode='json'))
        calls.append('source')
    def checked_trace(db, principal, value, *, live=False):
        assert principal is p and value == trace and live is True
        calls.append('model')
    bridge = SimpleNamespace(source_manifest=source_manifest, validate_source_manifest=validate_source,
        connection_hash=connection_hash)
    monkeypatch.setitem(sys.modules, 'generic_agents.audio_streams', bridge)
    monkeypatch.setattr(generic_agents, 'audio_streams', bridge, raising=False)
    monkeypatch.setattr(local_sound, 'checked_trace', checked_trace)
    rows = {row.id: row for row in (gateway, channel, model, source)}
    def row(db, principal, cls, identity):
        assert db is None and principal is p
        value = rows.get(identity)
        if value is None or value.workspace_id != p.workspace_id:
            raise HTTPException(404, 'missing or foreign test row')
        return value
    monkeypatch.setattr(subject, '_row', row)
    resource = {'id': 'audio-resource-opaque-id', 'category': 'video', 'businesses': ['动态安全巡检'],
        'details': {'channel_id': channel.id, 'capability_id': str(uuid4()),
            'audio': {'enabled': True, 'source_capability_id': source.id, 'model_capability_id': model.id}}}
    return SimpleNamespace(p=p, gateway=gateway, channel=channel, model=model, source=source,
        resource=resource, rows=rows, calls=calls)


def snapshot(s):
    resource = copy.deepcopy(s.resource)
    resource['backend_audio_proof'] = subject.compile_resource(None, s.p, resource)
    return {'resources': [resource], 'businesses': [{'id': 'business-opaque', 'name': '动态安全巡检',
        'available': True, 'issues': [], 'flow': {'steps': []}}], 'general_issues': [],
        'automation': {'policies': {'动态安全巡检': {'rules': {'observe': {'mode': '自动运行',
            'resources': [resource['id']], 'scope': '', 'limitsEnabled': True, 'limit': 12, 'cooldown': 2}}}}},
        'equipment': {'types': {'camera': {'read': True, 'limitEnabled': True, 'limit': 8,
            'cooldownEnabled': True, 'cooldown': 3, 'durationEnabled': True, 'duration': 1}},
            'devices': {resource['id']: {'type': 'camera', 'custom': False}}}}


def requirements(schema='instance-data-requirements-v3'):
    # An explicitly synthetic, correctly hashed builder output, not a release.
    return signed({'schema': schema, 'businesses': [], 'dependency_hash': 'a' * 64}, 'requirements_hash')


def test_compile_pure_audio_source_is_independent_of_video_registration_or_read_permission(resource_scene):
    s = resource_scene; proof = subject.compile_resource(None, s.p, s.resource)
    assert proof['source_capability_id'] == s.source.id != s.resource['details']['capability_id']
    assert proof['channel_id'] == s.channel.id and proof['model_capability_id'] == s.model.id
    assert proof['candidate_only'] is True and proof['capture_authorized'] is False
    assert s.calls == ['source', 'model']
    assert proof['proof_hash'] == digest({k: v for k, v in proof.items() if k != 'proof_hash'})
    assert 'api_origin' not in proof and 'rtsp_origin' not in proof


@pytest.mark.parametrize('change', ['gateway', 'channel', 'generation', 'path', 'source_hash', 'model_hash', 'model_role'])
def test_compilation_rejects_stale_or_broadened_source_and_model(resource_scene, change):
    s = resource_scene
    if change == 'gateway': s.gateway.status = 'disabled'
    if change == 'channel': s.channel.status = 'disabled'
    if change == 'generation': s.channel.data['generation'] += 1
    if change == 'path': s.channel.path = 'different/audio'
    if change == 'source_hash': s.source.content_hash = '0' * 64
    if change == 'model_hash': s.model.content_hash = '0' * 64
    if change == 'model_role':
        s.model.data['allowed_agent_roles'].append('business')
        s.model.content_hash = s.model.validated_hash = digest(s.model.data)
    with pytest.raises((HTTPException, ValueError)):
        subject.compile_resource(None, s.p, s.resource)


def test_no_audio_keeps_original_requirements_byte_order_and_values():
    old = {'resources': []}; original = requirements('instance-data-requirements-v1')
    clone = subject.augment_requirements(old, original)
    assert clone == original and list(clone) == list(original) and clone is not original
    assert subject.compile_resource(None, None, {'category': 'documents'}) is None
    assert subject.verify(old) is None


def test_resource_model_binding_is_not_a_workflow_step_and_uses_real_limits(resource_scene):
    value = snapshot(resource_scene); before = copy.deepcopy(value); plan = subject.build(value)
    binding, = plan['bindings']
    assert binding['business_id'] == 'business-opaque' and binding['business_name'] == '动态安全巡检'
    assert binding['declaration_kind'] == 'resource_model' and binding['step_id'] is None
    assert binding['step_name'] == '资源自动音轨' and binding['agent_role'] == 'perception'
    assert binding['read_bounds'] == {'daily_limit': 8, 'cooldown_minutes': 3, 'max_read_seconds': 60}
    assert binding['storage']['machine_result'] == 'observation_extracted_environment_sound'
    assert binding['requires_independent_ack'] is True and binding['capture_authorized'] is False
    assert plan['source_clock_utc'] == 'unknown' and plan['recording_permission'] is False
    assert plan['date_basis'] == binding['date_basis'] == 'audio_original_registered'
    assert value == before and value['businesses'][0]['flow']['steps'] == []
    value[subject.KEY] = plan
    assert subject.verify(value) == plan


@pytest.mark.parametrize('change', ['advice', 'not_selected', 'blocked_business'])
def test_model_selection_without_automatic_scope_cannot_produce_start_binding(resource_scene, change):
    value = snapshot(resource_scene); rule = value['automation']['policies']['动态安全巡检']['rules']['observe']
    if change == 'advice': rule['mode'] = '仅给出建议'
    if change == 'not_selected': rule['resources'] = []
    if change == 'blocked_business': value['businesses'][0]['available'] = False
    assert subject.build(value) is None
    assert value['resources'][0]['backend_audio_proof']


def test_uncompiled_resource_remains_a_blocked_preview_without_synthesizing_a_binding(resource_scene):
    value = snapshot(resource_scene); resource = value['resources'][0]
    resource.pop('backend_audio_proof'); resource['details']['audio']['source_capability_id'] = ''
    business = value['businesses'][0]; business['available'] = False
    business['issues'] = ['此资源的音轨来源尚未通过服务器登记核对']
    before = copy.deepcopy(value)
    assert subject.build(value) is None and subject.verify(value) is None
    assert subject.augment_requirements(value, requirements()) == requirements()
    assert value == before and business['issues']


@pytest.mark.parametrize('change', ['available', 'no_issue', 'mixed', 'unknown_business'])
def test_missing_resource_proof_cannot_be_hidden_by_an_incomplete_blocked_flag(resource_scene, change):
    value = snapshot(resource_scene); value['resources'][0].pop('backend_audio_proof')
    business = value['businesses'][0]; business['available'] = False; business['issues'] = ['资源待核对']
    if change == 'available': business['available'] = True
    if change == 'no_issue': business['issues'] = []
    if change == 'mixed':
        value['businesses'].append({'id': 'other-business', 'name': '另一业务', 'available': True, 'issues': []})
        value['resources'][0]['businesses'].append('另一业务')
    if change == 'unknown_business': value['resources'][0]['businesses'].append('不存在的业务')
    with pytest.raises((ValueError, HTTPException)):
        subject.build(value)


@pytest.mark.parametrize('change', ['proof_missing', 'proof_tamper', 'schema_rehashed', 'wrong_source', 'wrong_model',
    'missing_facet', 'facet_rehashed', 'unknown_business', 'no_equipment', 'unbounded_text'])
def test_saved_facet_rebuild_detects_stripping_tampering_and_unverifiable_scope(resource_scene, change):
    value = snapshot(resource_scene); value[subject.KEY] = subject.build(value)
    resource = value['resources'][0]; proof = resource['backend_audio_proof']
    if change == 'proof_missing': resource.pop('backend_audio_proof')
    if change == 'proof_tamper': proof['connection_hash'] = '0' * 64
    if change == 'schema_rehashed':
        proof['output_schema']['properties']['attributes'].pop('maxProperties')
        proof['output_schema_hash'] = digest(proof['output_schema'])
        resource['backend_audio_proof'] = signed(proof, 'proof_hash')
    if change == 'wrong_source': resource['details']['audio']['source_capability_id'] = str(uuid4())
    if change == 'wrong_model': resource['details']['audio']['model_capability_id'] = str(uuid4())
    if change == 'missing_facet': value.pop(subject.KEY)
    if change == 'facet_rehashed':
        value[subject.KEY]['bindings'][0]['capture_authorized'] = True
        value[subject.KEY] = signed(value[subject.KEY], 'plan_hash')
    if change == 'unknown_business': resource['businesses'] = ['未配置业务']
    if change == 'no_equipment': value.pop('equipment')
    if change == 'unbounded_text': value['automation']['policies']['动态安全巡检']['rules']['observe']['scope'] = '随便录一片区域'
    with pytest.raises((ValueError, HTTPException)):
        subject.verify(value)


@pytest.mark.parametrize('schema', ['instance-data-requirements-v1', 'instance-data-requirements-v2', 'instance-data-requirements-v3'])
def test_additive_requirements_preserve_old_fields_and_hash_every_audio_binding(resource_scene, schema):
    value = snapshot(resource_scene); original = requirements(schema); before = copy.deepcopy(original)
    augmented = subject.augment_requirements(value, original)
    assert original == before and augmented['schema'] == schema
    assert augmented['audio_sources'] == subject.build(value)['bindings']
    assert augmented['businesses'] == before['businesses']
    assert augmented['requirements_hash'] == digest({k: v for k, v in augmented.items() if k != 'requirements_hash'})
    assert subject.augment_requirements(value, augmented) == augmented


def test_live_binding_requires_the_published_original_scope_and_current_source(resource_scene, monkeypatch):
    from generic_agents import instance_data_contract_dispatch
    s = resource_scene; value = snapshot(s); value[subject.KEY] = subject.build(value)
    value['data_requirements'] = subject.augment_requirements(value, requirements())
    release = SimpleNamespace(id=str(uuid4()), workspace_id=s.p.workspace_id, instance_id=str(uuid4()),
        status='prepared', data={'snapshot': value}, configuration_hash=digest(value), package_id=str(uuid4()))
    instance = SimpleNamespace(id=release.instance_id, workspace_id=s.p.workspace_id, namespace='test_instance',
        status='partially_active', active_release_id=release.id, data={'enabled_business_ids': ['business-opaque']})
    package_data = {'kind': 'domain_package', 'namespace': instance.namespace, 'name': 'test_package',
        'version': '1.0.0', 'specification': {'configuration_hash': release.configuration_hash,
        'data_requirements_hash': value['data_requirements']['requirements_hash']}}
    package = SimpleNamespace(id=release.package_id, workspace_id=s.p.workspace_id, kind='domain_package',
        namespace=instance.namespace, name='test_package', status='published', release_version='1.0.0', data=package_data,
        content_hash=digest(package_data), validated_hash=digest(package_data))
    s.rows.update({r.id: r for r in (release, instance, package)})
    checks = []
    monkeypatch.setattr(instance_data_contract_dispatch, 'verify_requirements', lambda snap: checks.append(snap))
    business = value['businesses'][0]
    binding = subject.require_binding(None, s.p, instance, release, business, s.channel, s.model)
    assert binding == value[subject.KEY]['bindings'][0] and checks == [value]
    for other_business, other_channel, other_model in (
        ({**business, 'id': 'other-business'}, s.channel, s.model),
        (business, SimpleNamespace(id=str(uuid4())), s.model),
        (business, s.channel, SimpleNamespace(id=str(uuid4()))),
    ):
        with pytest.raises(HTTPException):
            subject.require_binding(None, s.p, instance, release, other_business, other_channel, other_model)
    instance.status = 'paused'
    with pytest.raises(HTTPException):
        subject.require_binding(None, s.p, instance, release, business, s.channel, s.model)
    # History remains pure, does not ask for a new recording or current model.
    assert subject.verify(value)['bindings'] == [binding]
    # Even this deliberately permissive base-verifier stub cannot turn an
    # old undeclared snapshot into a resource-model recording binding.
    instance.status = 'partially_active'; old = release.data['snapshot']
    undeclared = copy.deepcopy(old); undeclared.pop(subject.KEY)
    release.data = {'snapshot': undeclared}; release.configuration_hash = digest(undeclared)
    package.data['specification']['configuration_hash'] = release.configuration_hash
    package.content_hash = package.validated_hash = digest(package.data)
    with pytest.raises(HTTPException):
        subject.require_binding(None, s.p, instance, release, undeclared['businesses'][0], s.channel, s.model)
    release.data = {'snapshot': old}; release.configuration_hash = digest(old)
    package.data['specification']['configuration_hash'] = release.configuration_hash
    package.content_hash = package.validated_hash = digest(package.data)
    instance.status = 'partially_active'; s.channel.data['generation'] += 1
    with pytest.raises((HTTPException, ValueError)):
        subject.require_binding(None, s.p, instance, release, business, s.channel, s.model)
