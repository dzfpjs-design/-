"""Resource-model audio storage declarations, never a recording permission.

Only a newly compiled resource can acquire a server-checked proof.  The pure
facet is derived from that proof and the actual business observe scope; it does
not manufacture a workflow node or upgrade an old release during reception.
"""
import copy
import re
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select

from . import models as m
from .contracts import Manifest, digest

KEY = 'gateway_audio_results'
SCHEMA = 'gateway-audio-data-plan-v1'
PROOF_SCHEMA = 'gateway-audio-resource-proof-v1'
BINDING_SCHEMA = 'gateway-audio-data-binding-v1'
FAMILY = 'gateway_audio_observation_candidate_v1'
ERROR = '原音轨资源、模型或自动观测数据声明无法核对；声明不授予录音权限'
_HASH = re.compile(r'^[0-9a-f]{64}$')
_VERSION = re.compile(r'^\d+\.\d+\.\d+$')
_PROOF_KEYS = {'schema', 'workspace_id', 'resource_id', 'channel_id', 'channel_generation',
    'gateway_id', 'gateway_generation', 'connection_hash', 'source_capability_id',
    'source_capability_hash', 'source_release_version', 'model_capability_id',
    'model_capability_hash', 'model_release_version', 'model_trace', 'input_schema',
    'input_schema_hash', 'output_schema', 'output_schema_hash', 'candidate_only',
    'capture_authorized', 'proof_hash'}
_PIN_KEYS = ('workspace_id', 'resource_id', 'channel_id', 'channel_generation', 'gateway_id',
    'gateway_generation', 'connection_hash', 'source_capability_id', 'source_capability_hash',
    'source_release_version', 'model_capability_id', 'model_capability_hash', 'model_release_version',
    'model_trace', 'input_schema', 'input_schema_hash', 'output_schema', 'output_schema_hash')


def _need(value, code='gateway_audio_data_plan_invalid'):
    if not value:
        raise ValueError(code)


def _uuid(value):
    try:
        return type(value) is str and str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def _text(value):
    return type(value) is str and bool(value.strip()) and '\0' not in value


def _hash(value):
    return type(value) is str and _HASH.fullmatch(value) is not None


def _row(db, p, cls, identity):
    row = db.scalar(select(cls).where(cls.workspace_id == p.workspace_id, cls.id == identity)
        .execution_options(populate_existing=True))
    if row is None:
        raise HTTPException(404, '记录不存在或不在当前工作区')
    return row


def _published(row, kind):
    _need(row.status == 'published' and row.kind == kind and row.content_hash == digest(row.data)
        and row.validated_hash == row.content_hash and row.release_version == row.data.get('version')
        and row.kind == row.data.get('kind') and row.namespace == row.data.get('namespace')
        and row.name == row.data.get('name'))


def _audio(resource):
    _need(type(resource) is dict)
    details = resource.get('details', {})
    if resource.get('category') != 'video':
        _need('backend_audio_proof' not in resource)
        return None
    _need(type(details) is dict)
    audio = details.get('audio')
    if audio is None:
        _need('backend_audio_proof' not in resource)
        return None
    _need(type(audio) is dict and type(audio.get('enabled')) is bool)
    if not audio['enabled']:
        _need('backend_audio_proof' not in resource)
        return None
    _need(_text(resource.get('id')) and _uuid(details.get('channel_id'))
        and _uuid(audio.get('source_capability_id')) and _uuid(audio.get('model_capability_id')))
    return audio


def compile_resource(db, p, resource):
    """Read current source/model definitions, without opening or recording audio."""
    audio = _audio(resource)
    if audio is None:
        return None
    from .audio_streams import source_manifest, validate_source_manifest, connection_hash
    from .audio_observation_model import validate_manifest
    channel = _row(db, p, m.MediaChannel, resource['details']['channel_id'])
    gateway = _row(db, p, m.MediaGateway, channel.gateway_id)
    source = _row(db, p, m.Capability, audio['source_capability_id'])
    model = _row(db, p, m.Capability, audio['model_capability_id'])
    _need(channel.status in {'confirmed', 'validated'} and gateway.status == 'ready'
        and type(channel.data.get('generation')) is int and channel.data['generation'] >= 1
        and type(gateway.data.get('generation')) is int and gateway.data['generation'] >= 1)
    _published(source, 'source'); _published(model, 'specialist_model')
    expected = source_manifest(channel, gateway)
    expected = expected.model_dump(mode='json') if isinstance(expected, Manifest) else expected
    _need(digest(source.data) == digest(expected)
        and source.data['execution']['adapter'] == 'audio_stream_source')
    validate_source_manifest(db, p, Manifest.model_validate(source.data))
    typed = validate_manifest(db, p, model.data, live=True)
    identity_hash = connection_hash(channel, gateway)
    _need(_hash(identity_hash) and source.data['execution']['source_identity_hash'] == identity_hash)
    value = {'schema': PROOF_SCHEMA, 'workspace_id': p.workspace_id, 'resource_id': resource['id'],
        'channel_id': channel.id, 'channel_generation': channel.data['generation'],
        'gateway_id': gateway.id, 'gateway_generation': gateway.data['generation'],
        'connection_hash': identity_hash, 'source_capability_id': source.id,
        'source_capability_hash': source.content_hash, 'source_release_version': source.release_version,
        'model_capability_id': model.id, 'model_capability_hash': model.content_hash,
        'model_release_version': model.release_version, 'model_trace': copy.deepcopy(typed.execution['model_trace']),
        'input_schema': copy.deepcopy(typed.input_schema), 'input_schema_hash': digest(typed.input_schema),
        'output_schema': copy.deepcopy(typed.output_schema), 'output_schema_hash': digest(typed.output_schema),
        'candidate_only': True, 'capture_authorized': False}
    return _proof({**value, 'proof_hash': digest(value)})


def _proof(value):
    from .audio_observation_model import manifest
    _need(type(value) is dict and set(value) == _PROOF_KEYS and value['schema'] == PROOF_SCHEMA
        and _text(value['resource_id']) and value['candidate_only'] is True
        and value['capture_authorized'] is False)
    _need(all(_uuid(value[k]) for k in ('workspace_id', 'channel_id', 'gateway_id',
        'source_capability_id', 'model_capability_id')))
    _need(all(type(value[k]) is int and value[k] >= 1 for k in ('channel_generation', 'gateway_generation')))
    _need(all(_hash(value[k]) for k in ('connection_hash', 'source_capability_hash',
        'model_capability_hash', 'input_schema_hash', 'output_schema_hash', 'proof_hash')))
    _need(all(type(value[k]) is str and _VERSION.fullmatch(value[k]) is not None
        for k in ('source_release_version', 'model_release_version')))
    expected = manifest(value['model_trace']).model_dump(mode='json')
    _need(value['model_capability_hash'] == digest(expected) and value['model_release_version'] == expected['version']
        and digest(value['input_schema']) == value['input_schema_hash'] == digest(expected['input_schema'])
        and digest(value['output_schema']) == value['output_schema_hash'] == digest(expected['output_schema'])
        and value['proof_hash'] == digest({k: v for k, v in value.items() if k != 'proof_hash'}))
    return copy.deepcopy(value)


def _blocked_uncompiled(snapshot, resource):
    """Keep an explicitly blocked preview visible without inventing a proof."""
    if (type(resource) is not dict or resource.get('category') != 'video'
            or 'backend_audio_proof' in resource or type(resource.get('details')) is not dict):
        return False
    audio = resource['details'].get('audio')
    if type(audio) is not dict or audio.get('enabled') is not True:
        return False
    names = resource.get('businesses'); businesses = snapshot.get('businesses')
    if (type(names) is not list or not names or not all(_text(name) for name in names)
            or len(set(names)) != len(names) or type(businesses) is not list):
        return False
    for name in names:
        found = [b for b in businesses if type(b) is dict and b.get('name') == name]
        if len(found) != 1:
            return False
        business = found[0]; issues = business.get('issues')
        if (business.get('available') is not False or type(issues) is not list
                or not issues or not all(_text(issue) for issue in issues)):
            return False
    return True


def build(snapshot):
    """Derive actual resource-model outputs; never invent a Task step."""
    _need(type(snapshot) is dict)
    resources = snapshot.get('resources', [])
    _need(type(resources) is list)
    enabled = []
    resource_ids = set()
    for resource in resources:
        if _blocked_uncompiled(snapshot, resource):
            continue
        audio = _audio(resource)
        if audio is None:
            continue
        _need(resource['id'] not in resource_ids, 'gateway_audio_duplicate_resource')
        resource_ids.add(resource['id'])
        proof = _proof(resource.get('backend_audio_proof'))
        _need(proof['resource_id'] == resource['id'] and proof['channel_id'] == resource['details']['channel_id']
            and proof['source_capability_id'] == audio['source_capability_id']
            and proof['model_capability_id'] == audio['model_capability_id'])
        enabled.append((resource, proof))
    if not enabled:
        return None
    businesses = snapshot.get('businesses')
    _need(type(businesses) is list)
    by_name = {}
    for business in businesses:
        _need(type(business) is dict and _text(business.get('id')) and _text(business.get('name'))
            and business['name'] not in by_name)
        by_name[business['name']] = business
    _need(len({b['id'] for b in businesses}) == len(businesses))
    automation = snapshot.get('automation')
    _need(type(automation) is dict)
    policies = automation.get('policies', {})
    _need(type(policies) is dict)
    bindings = []
    for resource, proof in enabled:
        assigned = resource.get('businesses')
        _need(type(assigned) is list and assigned and all(_text(name) and name in by_name for name in assigned)
            and len(set(assigned)) == len(assigned), 'gateway_audio_business_scope')
        for name in assigned:
            business = by_name[name]
            policy = policies.get(name, {})
            _need(type(policy) is dict and type(policy.get('rules', {})) is dict)
            rule = policy.get('rules', {}).get('observe', {})
            _need(type(rule) is dict)
            if rule.get('mode') != '自动运行':
                continue
            scope = rule.get('resources')
            _need(type(scope) is list and all(_text(item) for item in scope) and len(set(scope)) == len(scope))
            if resource['id'] not in scope or business.get('available') is not True or business.get('issues') or snapshot.get('general_issues'):
                continue
            from .video_read_boundary import limits
            try:
                bounds = limits(policy, snapshot.get('equipment'), resource, 'automatic')
            except HTTPException:
                raise ValueError('gateway_audio_read_bounds') from None
            _need(bounds.get('legacy_manual_channel_consent') is False)
            value = {'schema': BINDING_SCHEMA, **{key: copy.deepcopy(proof[key]) for key in _PIN_KEYS},
                'business_id': business['id'], 'business_name': name, 'resource_proof_hash': proof['proof_hash'],
                'observe_policy_hash': digest(rule), 'source_family': FAMILY, 'declaration_kind': 'resource_model',
                'read_bounds': {key: bounds[key] for key in ('daily_limit', 'cooldown_minutes', 'max_read_seconds')},
                'step_id': None, 'step_name': '资源自动音轨', 'agent_role': 'perception',
                'storage': {'original': 'immutable_evidence', 'machine_result': 'observation_extracted_environment_sound',
                    'invocation': 'sdk_invocations', 'handoff': 'observation_data_event',
                    'reception': 'independent_observation_memory'},
                'candidate_only': True, 'source_clock_utc': 'unknown', 'date_basis': 'audio_original_registered',
                'requires_independent_ack': True, 'field_truth_verified': False, 'capture_authorized': False}
            bindings.append({**value, 'binding_hash': digest(value)})
    if not bindings:
        return None
    bindings.sort(key=lambda row: (row['business_id'], row['resource_id']))
    _need(len({(b['business_id'], b['channel_id'], b['model_capability_id']) for b in bindings}) == len(bindings),
        'gateway_audio_ambiguous_resource_scope')
    _need(len({b['workspace_id'] for b in bindings}) == 1, 'gateway_audio_workspace_scope')
    value = {'schema': SCHEMA, 'source_family': FAMILY, 'bindings': bindings, 'time_zone': 'Asia/Shanghai',
        'date_basis': 'audio_original_registered', 'source_clock_utc': 'unknown',
        'requires_independent_ack': True, 'candidate_only': True, 'field_truth_verified': False,
        'recording_permission': False, 'deletion_authorized': False}
    return {**value, 'plan_hash': digest(value)}


def verify(snapshot):
    expected = build(snapshot)
    _need((KEY not in snapshot) if expected is None else
        type(snapshot.get(KEY)) is dict and digest(snapshot[KEY]) == digest(expected),
        'gateway_audio_data_plan_changed')
    return copy.deepcopy(expected)


def augment_requirements(snapshot, requirements):
    """Add resource outputs to the authoritative v1/v2/v3 base, without mutation."""
    plan = build(snapshot)
    out = copy.deepcopy(requirements)
    if plan is None:
        return out
    _need(type(out) is dict and out.get('schema') in {'instance-data-requirements-v1',
        'instance-data-requirements-v2', 'instance-data-requirements-v3'}
        and out.get('requirements_hash') == digest({k: v for k, v in out.items() if k != 'requirements_hash'}))
    out.pop('requirements_hash')
    out['audio_sources'] = copy.deepcopy(plan['bindings'])
    return {**out, 'requirements_hash': digest(out)}


def require_binding(db, p, instance, release, business, channel, cap):
    """Live selection gate only. Historical reception uses verify(), not this."""
    try:
        current = _row(db, p, m.DomainInstance, instance.id)
        original = _row(db, p, m.InstanceRelease, release.id)
        _need(current.status == 'partially_active' and current.active_release_id == original.id
            and original.instance_id == current.id and original.status == 'prepared')
        snapshot = original.data['snapshot']
        _need(digest(snapshot) == original.configuration_hash)
        package = _row(db, p, m.Capability, original.package_id)
        _published(package, 'domain_package')
        _need(package.namespace == current.namespace and package.data['specification']['configuration_hash'] == original.configuration_hash)
        from .instance_data_contract_dispatch import verify_requirements
        verify_requirements(snapshot)
        plan = verify(snapshot)
        _need(plan is not None, 'gateway_audio_not_declared')
        actual = next((item for item in snapshot['businesses'] if item['id'] == business['id']), None)
        _need(actual is not None and digest(actual) == digest(business) and actual['available'] is True
            and not actual['issues'] and not snapshot.get('general_issues')
            and actual['id'] in current.data.get('enabled_business_ids', []))
        found = [item for item in plan['bindings'] if item['business_id'] == actual['id']
            and item['channel_id'] == channel.id and item['model_capability_id'] == cap.id
            and item['workspace_id'] == p.workspace_id]
        _need(len(found) == 1, 'gateway_audio_binding_scope')
        binding = found[0]
        resource = next(item for item in snapshot['resources'] if item.get('id') == binding['resource_id'])
        proof = compile_resource(db, p, resource)
        _need(proof is not None and proof['proof_hash'] == binding['resource_proof_hash']
            and proof['model_capability_hash'] == cap.content_hash
            and snapshot['data_requirements'].get('audio_sources') is not None
            and any(digest(row) == digest(binding) for row in snapshot['data_requirements']['audio_sources']))
        _need(package.data['specification']['data_requirements_hash'] == snapshot['data_requirements']['requirements_hash'])
        return copy.deepcopy(binding)
    except HTTPException:
        raise
    except (ValueError, KeyError, TypeError, AttributeError, StopIteration):
        raise HTTPException(409, ERROR) from None
