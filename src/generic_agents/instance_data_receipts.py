"""Version-bound reception integrity; never source truth or execution authority.

Only successful observation.data events use this seam. Legacy releases with no
new markers retain their original path. Queries only read/rebuild; reception
sets one immutable receipt on Observation, in the caller's event transaction.
"""
import copy

from fastapi import HTTPException
from sqlalchemy import JSON, literal, select

from .contracts import digest
from .models import Capability, DomainInstance, Entity, Event, InstanceRelease, Invocation, Memory, Observation
from .services import scoped, validate
from .instance_data_contract_dispatch import accepted_schema, verify_requirements
# Preserve the public legacy identifier for existing integrations and fixtures.
# Runtime verification below always uses the rebuilt plan's actual version.
from .instance_data_requirements import SCHEMA as REQUIREMENTS
from .instance_domain import generated_release
from .entity_release_compat import require_entity
from .observation_integrity import checked_report
from .observation_time_anchor_v135 import same_observation_instant


SCHEMA = 'instance-data-reception-receipt-v1'
_MARKERS = ('data_requirements', 'data_requirements_schema', 'data_requirements_hash', 'native_pixel_results_v250', 'native_sound_results', 'gateway_audio_results')
_SIMULATION_UNSET = object()


def _need(value):
    if not value:
        raise HTTPException(409, '数据保存契约或接收依据无法核对；原记录保留，请核对版本')


def _same(left, right):
    return digest(left) == digest(right)


def _checked(function, *args):
    try:
        return function(*args)
    except HTTPException as error:
        if error.status_code != 422:
            raise
        _need(False)
    except (ValueError, KeyError, TypeError, OSError, RecursionError):
        _need(False)


def _verify_release(db, p, release):
    """Cold-check new contracts; removing one side cannot select legacy mode."""
    _need(release.workspace_id == p.workspace_id and type(release.data) is dict)
    snapshot = release.data.get('snapshot', {})
    package = scoped(db, Capability, release.package_id, p) if release.package_id else None
    specification = package.data.get('specification', {}) if package and type(package.data) is dict else {}
    marked = any(key in value for value in (release.data, snapshot, specification)
                 if type(value) is dict for key in _MARKERS)
    if not marked:
        return None
    _need(type(snapshot) is dict and type(specification) is dict and package is not None
          and release.status == 'prepared' and package.kind == 'domain_package')
    instance = scoped(db, DomainInstance, release.instance_id, p)
    _need(package.namespace == instance.namespace and package.content_hash == digest(package.data)
          and package.validated_hash == package.content_hash
          and digest(snapshot) == release.configuration_hash
          and specification.get('configuration_hash') == release.configuration_hash)
    verify_requirements(snapshot)
    from .native_pixel_data_plan_v250 import verify as verify_pixel_design
    verify_pixel_design(snapshot)
    from .native_sound_data_plan import verify as verify_sound_design
    verify_sound_design(snapshot)
    plan = snapshot.get('data_requirements')
    _need(accepted_schema(plan)
          and specification.get('data_requirements_schema') == plan['schema']
          and specification.get('data_requirements_hash') == plan.get('requirements_hash'))
    for container in (release.data, snapshot):
        if 'data_requirements_schema' in container:
            _need(container['data_requirements_schema'] == plan['schema'])
        if 'data_requirements_hash' in container:
            _need(container['data_requirements_hash'] == plan['requirements_hash'])
    return copy.deepcopy(plan)


def _source_release(db, p, entity):
    release = generated_release(db, p, entity)
    if release is None:
        package = scoped(db, Capability, entity.schema_capability_id, p)
        _need(not any(key in package.data.get('specification', {}) for key in _MARKERS))
    return release


def _query_context(db, p, entity_ids):
    """Check origin contracts without choosing a new active destination."""
    for identity in entity_ids:
        entity = scoped(db, Entity, identity, p)
        release = _source_release(db, p, entity)
        if release is not None:
            verify_release(db, p, release)
    return None


def _destination(db, p, observation, *, simulation=_SIMULATION_UNSET):
    if observation.entity_id is None:
        return None, None, []
    entity = scoped(db, Entity, observation.entity_id, p)
    if simulation is _SIMULATION_UNSET:
        # An independently issued video owns its original release even when
        # this namespace also has an unrelated simulated sensor environment.
        # Check the full producer chain, never merely a removable JSON tag.
        from . import native_video_provenance_v164 as video_origin
        if video_origin.has_issued(db, p, observation):
            context = video_origin.checked_observation(db, p, observation)
            _need(context['entity'].id == entity.id)
            return context['release'], entity, []
        from .simulation_observation_origin_v177 import guard
        simulation = guard(db,p,observation)
    if simulation is not None:
        release = scoped(db, InstanceRelease, simulation['release_id'], p)
        _need(simulation['entity_id'] == entity.id and release.instance_id == simulation['instance_id'])
        return release, entity, [copy.deepcopy(simulation)]
    from .audio_streams import observation_marker as audio_marker,observation_context as audio_context
    if audio_marker(db,p,observation):
        _need(not any(key in observation.data for key in ('local_instance','reported_rule_contract')))
        origin=audio_context(db,p,observation,execution=False)
        stream,binding,capture=(origin[key] for key in ('stream','binding','capture'))
        release=scoped(db,InstanceRelease,capture['release_id'],p)
        _need(release.instance_id==stream.instance_id==capture['instance_id']
              and observation.entity_id==stream.entity_id==capture['entity_id'])
        context={'schema':'gateway-audio-destination-v255','instance_id':stream.instance_id,'release_id':release.id,
            'stream_id':stream.id,'binding_hash':binding['binding_hash'],'capture_hash':digest(capture),
            'source_hash':binding['source_capability_hash'],'extractor_hash':binding['model_capability_hash'],
            'entity_source_package_id':entity.schema_capability_id,
            'entity_definition_hash':stream.data['definition']['entity_definition_hash']}
        return release,entity,[context]
    from .original_acquisition_reception_v127 import context as original_context
    original = original_context(db, p, observation)
    contexts = []
    for key, instance_key in (('local_instance', 'id'), ('reported_rule_contract', 'instance_id')):
        if key in observation.data:
            value = observation.data[key]
            _need(type(value) is dict and type(value.get(instance_key)) is str
                  and type(value.get('release_id')) is str)
            contexts.append((value[instance_key], value['release_id'], value))
    if original is not None:
        _need(entity.id == original.entity.id and all(item[:2] == (original.instance.id, original.release.id) for item in contexts))
        return original.release, entity, [value for _, _, value in contexts]
    if not contexts:
        return _source_release(db, p, entity), entity, []
    _need(all(item[:2] == contexts[0][:2] for item in contexts))
    release = scoped(db, InstanceRelease, contexts[0][1], p)
    _need(release.instance_id == contexts[0][0])
    return release, entity, [value for _, _, value in contexts]


def _declarations(plan, extractor, extracted, *, audio_binding=None):
    if extractor.data.get('execution',{}).get('adapter')=='local_sound_observation':
        _need(type(audio_binding) is dict)
        matches=[row for row in plan.get('audio_sources',[]) if digest(row)==digest(audio_binding)]
        _need(len(matches)==1)
        row=matches[0]
        _need(row['model_capability_id']==extractor.id
              and row['model_capability_hash']==extractor.content_hash
              and row['model_release_version']==extractor.release_version
              and _same(row['output_schema'],extractor.data.get('output_schema')))
        validate(row['output_schema'],extracted)
        return [{'business_id':row['business_id'],'step_id':None,'step_name':row['step_name'],
            'agent_role':'perception','capability_id':extractor.id,'capability_hash':extractor.content_hash,
            'release_version':extractor.release_version,'output_schema_hash':row['output_schema_hash'],
            'declaration_kind':'resource_model','resource_id':row['resource_id'],
            'source_family':row['source_family'],'binding_hash':row['binding_hash']}]
    matches = []
    for business in plan['businesses']:
        for output in business['outputs']:
            if plan.get('schema')=='instance-data-requirements-v3' and output.get('kind') in {'human','wait'}:
                continue
            if output['capability_id'] != extractor.id:
                continue
            _need(output['capability_hash'] == extractor.content_hash
                  and output['release_version'] == extractor.release_version
                  and _same(output['output_schema'], extractor.data.get('output_schema')))
            validate(output['output_schema'], extracted)
            matches.append({'business_id': business['id'], **{key: copy.deepcopy(output[key]) for key in
                ('step_id', 'step_name', 'agent_role', 'capability_id', 'capability_hash',
                 'release_version', 'output_schema_hash')},
                **({key: copy.deepcopy(output[key]) for key in ('declaration_kind', 'source_binding_hash')}
                    if output.get('kind') == 'asynchronous_perception' else {})})
    return matches


def _event_matches(event, observation, *, simulation=None):
    _need(event.workspace_id == observation.workspace_id and event.kind == 'observation.data'
          and event.idempotency_key == 'data:' + observation.id and type(event.data) is dict)
    expected = {'observation_id': observation.id, 'entity_id': observation.entity_id,
        'source_id': observation.source_id, 'invocation_id': observation.data.get('invocation_id'),
        'extracted': observation.data.get('extracted'), 'evidence_ids': observation.data.get('evidence_ids', [])}
    _need(all(key in event.data and _same(event.data[key], value) for key, value in expected.items()))
    if 'observed_at' in event.data:
        same_observation_instant(event.data['observed_at'], observation.observed_at)
    for key, value in (('candidate_only', observation.data.get('candidate_only', False)),
            ('model_receipt', observation.data.get('model_receipt', {}))):
        if key in event.data:
            _need(_same(event.data[key], value))
    markers = {'simulation','origin_kind','field_verified','simulation_origin'}
    if simulation is not None:
        _need(event.data.get('simulation') is True and event.data.get('origin_kind') == 'simulated_equipment'
              and event.data.get('field_verified') is False
              and _same(event.data.get('simulation_origin'), simulation))
    else:
        _need(not (markers & set(event.data)) and 'simulation_origin' not in observation.data)


def _receipt(db, p, event, observation):
    from . import native_sound_receipts as native_sound
    if native_sound.handles(db,p,observation):
        return native_sound.receipt(db,p,event,observation)
    from . import native_pixel_receipts_v250 as native_pixel
    if native_pixel.handles(db, p, observation):
        return native_pixel.receipt(db, p, event, observation)
    _need(observation.workspace_id == p.workspace_id)
    from . import native_video_reception_v164 as native_video
    if native_video.handles(db,p,observation):
        return native_video.receipt(db,p,event,observation)
    from .simulation_observation_origin_v177 import guard
    simulation = guard(db,p,observation)
    from . import native_learned_geo_pair_reception_v138 as native_learned
    if native_learned.handles(db,p,observation):
        return native_learned.receipt(db,p,event,observation)
    from . import native_geo_pair_reception_v137 as native_geo_pair
    if native_geo_pair.handles(db,p,observation):
        return native_geo_pair.receipt(db,p,event,observation)
    from . import native_audio_reception_v136 as native_audio
    if native_audio.handles(db,p,observation):
        return native_audio.receipt(db,p,event,observation)
    release, entity, contexts = _destination(db, p, observation, simulation=simulation)
    plan = verify_release(db, p, release) if release is not None else None
    if plan is None:
        _need('data_reception' not in observation.data and 'data_reception' not in event.data)
        return None
    _event_matches(event, observation, simulation=simulation)
    instance = scoped(db, DomainInstance, release.instance_id, p)
    from .original_acquisition_reception_v127 import context as original_context
    original = original_context(db, p, observation)
    proof = (original.definition if original is not None else
        require_entity(db, p, instance, release, entity, lock_sources=False))
    if original is not None:
        _need(original.release.id == release.id and original.instance.id == instance.id)
    for context in contexts:
        if entity.schema_capability_id != release.package_id or any(key in context for key in
                ('entity_source_package_id', 'entity_definition_hash')):
            _need(context.get('entity_source_package_id') == entity.schema_capability_id
                  and context.get('entity_definition_hash') == digest(proof))
    report = checked_report(db, p, observation, allow_candidates=True)
    call = scoped(db, Invocation, observation.data['invocation_id'], p)
    source = scoped(db, Capability, observation.source_id, p)
    extractor = scoped(db, Capability, observation.data['extractor_id'], p)
    for context in contexts:
        if 'source_hash' in context:
            _need(context['source_hash'] == source.content_hash)
        if 'extractor_hash' in context:
            _need(context['extractor_hash'] == extractor.content_hash)
    ids = observation.data.get('evidence_ids', [])
    _need(len(ids) == len(report['file_hashes']))
    audio_binding=None
    if extractor.data.get('execution',{}).get('adapter')=='local_sound_observation':
        from .audio_streams import observation_binding
        audio_binding=observation_binding(db,p,observation)
    declarations = _declarations(plan, extractor, observation.data['extracted'],audio_binding=audio_binding)
    inputs = {'attributes': observation.data.get('attributes', {})}
    if ids:
        inputs['evidence_ids'] = ids
    original = {key: getattr(observation, key) for key in
        ('source_id', 'source_record_id', 'processing_version', 'entity_id', 'business_day')}
    # The original v1 contract allowed a missing Event timestamp. Such history
    # keeps its original environment-dependent hash; never guess its old SQL
    # offset or silently invent a timestamp anchor. Present time stays raw.
    original_time = (event.data['observed_at'] if 'observed_at' in event.data else
        observation.observed_at.isoformat() if observation.observed_at else None)
    original.update(observed_at=original_time,
        attributes=observation.data.get('attributes', {}), evidence_ids=ids,
        extractor_id=observation.data['extractor_id'], input_hash=observation.data.get('input_hash'))
    value = {'schema': SCHEMA, 'workspace_id': p.workspace_id, 'event_id': event.id,
        'observation_id': observation.id, 'entity_id': entity.id,
        'source_id': source.id, 'source_hash': source.content_hash,
        'source_record_id': observation.source_record_id, 'processing_version': observation.processing_version,
        'instance_id': instance.id, 'release_id': release.id, 'release_number': release.release_number,
        'package_id': release.package_id, 'configuration_hash': release.configuration_hash,
        'requirements_schema': plan['schema'], 'requirements_hash': plan['requirements_hash'],
        'destination_hash': digest(contexts), 'original_input_hash': digest(original), 'input_hash': digest(inputs),
        'invocation_id': call.id, 'extractor_id': extractor.id, 'extractor_hash': extractor.content_hash,
        'extractor_release_version': extractor.release_version,
        'extracted_hash': digest(observation.data['extracted']),
        'invocation_hash': digest({'capability_id': call.capability_id,
            **{key: call.data.get(key) for key in ('capability_hash', 'input_hash', 'output')}}),
        'evidence': [{'id': identity, 'sha256': fingerprint} for identity, fingerprint in zip(ids, report['file_hashes'])],
        'declaration_status': 'declared' if declarations else 'not_declared', 'declared_outputs': declarations,
        'candidate_only': observation.data.get('candidate_only', False), 'not_authoritative': True,
        'source_time_verified': False, 'physical_state_verified': False, 'can_execute': False, 'can_delete': False}
    if simulation is not None:
        _need(instance.id == simulation['instance_id'] and release.id == simulation['release_id'])
        value.update(simulation=True,origin_kind='simulated_equipment',field_verified=False,
                     simulation_origin=copy.deepcopy(simulation))
    return {**value, 'receipt_hash': digest(value)}


def _receive_observation(db, p, event, observation):
    """Called before the caller inserts Memory/acks this successful event."""
    from .original_acquisition_reception_v127 import context as original_context
    from .native_audio_reception_v136 import handles as native_audio
    from .native_geo_pair_reception_v137 import handles as native_geo_pair
    from .native_learned_geo_pair_reception_v138 import handles as native_learned
    from .native_video_reception_v164 import handles as native_video
    from .native_pixel_receipts_v250 import handles as native_pixel
    from .native_sound_receipts import handles as native_sound
    if original_context(db, p, observation) is not None or native_audio(db,p,observation) or native_geo_pair(db,p,observation) or native_learned(db,p,observation) or native_video(db,p,observation) or native_pixel(db,p,observation) or native_sound(db,p,observation):
        # Human verification already locks Observation first. Match that order
        # before the first JSON-column write, then reconstruct the full chain
        # and reception from the refreshed row rather than overwriting review.
        observation = scoped(db, Observation, observation.id, p, True)
        original_context(db, p, observation)
    receipt = _receipt(db, p, event, observation)
    if receipt is not None:
        if 'data_reception' in observation.data:
            _need(_same(observation.data['data_reception'], receipt))
        else:
            observation.data = {**observation.data, 'data_reception': copy.deepcopy(receipt)}
    return copy.deepcopy(receipt)


def _observation_receipt(db, p, observation):
    """Read-only cold chain: release -> original invocation -> event -> Memory."""
    _need(observation.workspace_id == p.workspace_id)
    event = db.scalar(select(Event).where(Event.workspace_id == p.workspace_id,
        Event.kind == 'observation.data', Event.idempotency_key == 'data:' + observation.id))
    if event is None:
        from .native_audio_reception_v136 import handles as native_audio
        from .native_geo_pair_reception_v137 import handles as native_geo_pair
        from .native_learned_geo_pair_reception_v138 import handles as native_learned
        from .native_video_reception_v164 import handles as native_video
        from .native_pixel_receipts_v250 import handles as native_pixel
        from .native_sound_receipts import handles as native_sound
        _need(not native_audio(db,p,observation) and not native_geo_pair(db,p,observation) and not native_learned(db,p,observation) and not native_video(db,p,observation) and not native_pixel(db,p,observation) and not native_sound(db,p,observation))
        release, _, _ = _destination(db, p, observation)
        _need((release is None or verify_release(db, p, release) is None)
              and 'data_reception' not in observation.data)
        return {}
    receipt = _receipt(db, p, event, observation)
    if receipt is None:
        return {}
    _need(event.status == 'received' and _same(observation.data.get('data_reception'), receipt)
          and _same(event.data.get('data_reception'), receipt))
    rows = list(db.scalars(select(Memory).where(Memory.workspace_id == p.workspace_id,
        Memory.data[literal('event_id', type_=JSON.JSONStrIndexType(), literal_execute=True)].as_string() == event.id,
        Memory.data[literal('type', type_=JSON.JSONStrIndexType(), literal_execute=True)].as_string() == 'observation_evidence').limit(2)))
    _need(len(rows) == 1 and rows[0].status == 'candidate'
          and rows[0].data.get('event_id') == event.id
          and rows[0].data.get('type') == 'observation_evidence'
          and rows[0].data.get('not_authoritative') is True
          and _same(rows[0].data.get('data_reception'), receipt))
    _event_matches(type('SavedEvent', (), {'workspace_id': rows[0].workspace_id,
        'kind': event.kind, 'idempotency_key': event.idempotency_key, 'data': rows[0].data})(), observation,
        simulation=receipt.get('simulation_origin'))
    _need(('observed_at' in rows[0].data) == ('observed_at' in event.data)
          and _same(rows[0].data.get('observed_at'), event.data.get('observed_at')))
    if receipt.get('schema') == 'instance-native-audio-data-reception-v1':
        from .native_audio_reception_v136 import verify_memory
        verify_memory(observation,rows[0])
    if receipt.get('schema') == 'instance-native-geo-pair-data-reception-v1':
        from .native_geo_pair_reception_v137 import verify_memory
        verify_memory(observation,rows[0])
    if receipt.get('schema') == 'instance-native-learned-geo-pair-data-reception-v1':
        from .native_learned_geo_pair_reception_v138 import verify_memory
        verify_memory(observation,rows[0])
    if receipt.get('schema') == 'instance-native-video-data-reception-v1':
        from .native_video_reception_v164 import verify_memory
        verify_memory(observation,rows[0])
    if receipt.get('schema') == 'instance-native-sound-data-reception-v1':
        from .native_sound_receipts import verify_memory as verify_sound_memory
        _need(event.received_at is not None)
        verify_sound_memory(observation, rows[0])
    if receipt.get('schema') == 'instance-native-pixel-data-reception-v250':
        from .native_pixel_receipts_v250 import verify_memory
        _need(event.received_at is not None)
        verify_memory(observation, rows[0])
    return {'data_reception': copy.deepcopy(receipt)}


def verify_release(db, p, release):
    return _checked(_verify_release, db, p, release)


def receive_observation(db, p, event, observation):
    return _checked(_receive_observation, db, p, event, observation)


def query_context(db, p, entity_ids):
    return _checked(_query_context, db, p, entity_ids)


def observation_receipt(db, p, observation):
    return _checked(_observation_receipt, db, p, observation)
