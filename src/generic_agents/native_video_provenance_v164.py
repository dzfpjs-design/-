"""Scoped native video evidence; never invent a workflow call or capture clock.

Only a genuinely queued automatic Supplement Session can acquire an input
anchor. Readers reconstruct that original authority without renewing a Watch,
requiring today's model installation, or treating a scene as physical identity.
"""
import copy
import math
from datetime import datetime
from functools import wraps
from weakref import WeakKeyDictionary

from fastapi import HTTPException
from sqlalchemy import select, or_

from . import models as m
from .contracts import digest

INPUT_ACTION = 'native_video.capture_frozen_v164'
RESULT_ACTION = 'native_video.result_frozen_v164'
ISSUED = 'native_video_receipt_v164'
ISSUED_ACTION = 'native_video.receipt_issued_v164'
RECEPTION_SCHEMA = 'instance-native-video-data-reception-v1'
INPUT_SCHEMA = 'native-video-capture-input-v164'
RESULT_SCHEMA = 'native-video-result-v164'
ISSUED_SCHEMA = 'native-video-issued-v164'
EVENT_KEYS = frozenset({'observation_id', 'entity_id', 'source_id', 'evidence_ids',
    'native_receipt', 'observed_at', 'candidate_only', 'extracted', 'invocation_id'})
SCOPE_KEYS = frozenset({'instance_id', 'release_id', 'business_id', 'business_name',
    'entity_id', 'entity_version', 'source_id', 'source_hash', 'package_id', 'package_hash',
    'configuration_hash', 'requirements_schema', 'requirements_hash', 'channel_id',
    'gateway_id', 'binding_id', 'binding_generation', 'session_id', 'reservation_id',
    'plan_id', 'watch_id', 'processor_id', 'approved_by', 'watch_owner_id'})
SESSION_KEYS = ('channel_id', 'seconds', 'configuration', 'installation_id',
    'installation_generation', 'backend', 'capture_hash', 'generation',
    'read_reservation_id', 'read_reservation_hash', 'domain_binding',
    'supplement_plan_id', 'job_id')


def need(value):
    if not value:
        raise HTTPException(409, '原生视频的原读取授权、范围、原件或处理回执无法核对；原记录保留')


def same(left, right):
    return digest(left) == digest(right)


def _checked(function):
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except HTTPException as error:
            if error.status_code != 422:
                raise
            need(False)
        except (KeyError, ValueError, TypeError, AttributeError, OSError, OverflowError, RecursionError):
            need(False)
    return call


def row(db, cls, identity, p, *, lock=False):
    query = select(cls).where(cls.workspace_id == p.workspace_id, cls.id == identity)
    if lock:
        query = query.with_for_update(read=True)
    value = db.scalar(query.execution_options(populate_existing=True))
    need(value is not None)
    return value


def _audits(db, p, action, identity):
    return list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == action, m.Audit.object_id == identity).limit(2)))


def _one(db, p, action, identity):
    values = _audits(db, p, action, identity)
    need(len(values) == 1)
    return values[0]


def input_audits(db, p, session_id):
    return _audits(db, p, INPUT_ACTION, session_id)


def has_input(db, p, session_or_id):
    identity = session_or_id if type(session_or_id) is str else session_or_id.id
    return bool(input_audits(db, p, identity))


def _actor(db, p, identity, *, admin=False):
    query = select(m.User.id).join(m.Membership, m.Membership.user_id == m.User.id).where(
        m.User.id == identity, m.User.active.is_(True), m.Membership.workspace_id == p.workspace_id,
        m.Membership.role.in_(['admin'] if admin else ['admin', 'operator']))
    need(db.scalar(query.with_for_update(read=True)) is not None)


def _positive(value):
    return type(value) is int and value >= 1


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _session_request(session):
    return {key: copy.deepcopy(session.channel_id if key == 'channel_id' else session.data[key])
        for key in SESSION_KEYS}


def _installation(db, p, identity, generation, *, live=False, frozen=None):
    from .local_specialists import RECIPE, RECIPE_HASH
    actual = row(db, m.SpecialistInstallation, identity, p)
    need(actual.pack_id == RECIPE['pack_id'] and _positive(generation))
    if frozen is None:
        need(actual.data['generation'] == generation and actual.data['recipe_hash'] == RECIPE_HASH)
        candidates = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
            m.Audit.action == 'specialist.install.ready', m.Audit.object_id == actual.id)
            .order_by(m.Audit.created_at.desc(), m.Audit.id).limit(1)))
        need(len(candidates) == 1)
        ready = candidates[0]
        pin = {'installation_id': actual.id, 'pack_id': actual.pack_id,
            'generation': generation, 'recipe_hash': RECIPE_HASH, 'backend': actual.data['backend'],
            'artifact_hashes': copy.deepcopy(actual.data['artifact_hashes']),
            'ready_audit_id': ready.id, 'ready_audit_hash': digest(ready.data)}
    else:
        pin = copy.deepcopy(frozen)
        need(type(pin) is dict and set(pin) == {'installation_id', 'pack_id', 'generation',
            'recipe_hash', 'backend', 'artifact_hashes', 'ready_audit_id', 'ready_audit_hash'}
            and pin['installation_id'] == actual.id and pin['pack_id'] == actual.pack_id
            and pin['generation'] == generation and pin['recipe_hash'] == RECIPE_HASH)
        ready = row(db, m.Audit, pin['ready_audit_id'], p)
    need(ready.action == 'specialist.install.ready' and ready.object_id == actual.id
        and same(ready.data, {'recipe_hash': pin['recipe_hash'], 'backend': pin['backend']})
        and digest(ready.data) == pin['ready_audit_hash']
        and same(pin['artifact_hashes'], {item['name']: item['sha256'] for item in RECIPE['files']})
        and pin['backend'] in {'cpu', 'mps'})
    if live:
        from .local_specialists import ready_detection_install
        current = ready_detection_install(db, p, actual.id)
        need(current.data['generation'] == pin['generation'] and current.data['backend'] == pin['backend']
            and current.data['recipe_hash'] == pin['recipe_hash']
            and same(current.data['artifact_hashes'], pin['artifact_hashes']))
    return pin


def _authority(db, p, session, capture_job, *, live=False, frozen=None):
    """Validate the original immutable request, not mutable Watch progress."""
    from .video_read_boundary import checked as checked_reservation
    from .supplement import checked as checked_plan
    from .supplement_watch import checked as checked_watch
    from .evidence_assessment import checked as checked_assessment
    from .instance_data_receipts import verify_release
    from .entity_release_compat import require_entity, object_contract, _definition_structure
    from .services import validate
    data = session.data
    need(type(data) is dict and capture_job.kind == 'capture_media'
        and capture_job.data['session_id'] == session.id and data['job_id'] == capture_job.id
        and type(capture_job.data['actor_id']) is str
        and type(data['generation']) is int and data['generation'] == 1
        and _positive(data['installation_generation']) and _positive(data['channel_generation'])
        and _positive(data['gateway_generation']))
    association = data['domain_binding']
    need(type(association) is dict and association['association'] == 'observed_scene'
        and association['not_physical_identity'] is True and _positive(association['entity_version'])
        and _positive(association['binding_generation']))
    plan = checked_plan(row(db, m.SupplementPlan, data['supplement_plan_id'], p))
    watch = checked_watch(row(db, m.SupplementWatch, plan.data['automatic_watch_id'], p))
    assessment = checked_assessment(row(db, m.EvidenceAssessment, plan.assessment_id, p))
    approved, authorization = plan.data['approved'], watch.data['authorization']
    need(type(authorization) is dict and authorization['control_authorized'] is False
        and authorization['raw_media_cloud_allowed'] is False
        and plan.instance_id == watch.instance_id == association['instance_id']
        and plan.data['automatic_authorization_hash'] == watch.data['authorization_hash']
        and approved['automatic_watch_id'] == watch.id
        and approved['automatic_authorization_hash'] == watch.data['authorization_hash']
        and _positive(authorization['max_reads']) and type(authorization['seconds']) is int
        and authorization['seconds'] == approved['seconds'] == data['seconds']
        and same(approved['read_configuration'], data['configuration'])
        and data['configuration'].get('presence_rule') is None)
    need(assessment.status == 'completed' and assessment.instance_id == plan.instance_id
        and assessment.data['result_hash'] == plan.data['assessment_result_hash']
        and same(assessment.data['request']['entity_ids'], [association['entity_id']])
        and assessment.data['request']['release_id'] == association['instance_release_id']
        and same(assessment.data['request']['required_modalities'], ['video'])
        and all(same(assessment.data['request'][key], value)
            for key, value in authorization['criteria'].items()))
    sessions = [item for item in plan.data['sessions'] if item['id'] == session.id]
    history = [item for item in watch.data['history'] if item.get('plan_id') == plan.id]
    need(len(sessions) == len(history) == 1 and sessions[0]['job_id'] == capture_job.id
        and sessions[0]['channel_id'] == session.channel_id
        and history[0]['assessment_id'] == plan.assessment_id)
    queued = [item for item in db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == 'business.automatic_read_queued', m.Audit.object_id == watch.id,
        m.Audit.data['plan_id'].as_string() == plan.id).limit(2))]
    need(len(queued) == 1 and queued[0].actor_id == plan.data['approved_by']
        and same(queued[0].data, {'assessment_id': plan.assessment_id, 'plan_id': plan.id,
            'seconds': authorization['seconds']}))
    reservation = checked_reservation(row(db, m.VideoReadReservation, data['read_reservation_id'], p))
    receipt = reservation.data['receipt']; request, authority = receipt['request'], receipt['authority']
    need(reservation.data['receipt_hash'] == data['read_reservation_hash']
        and reservation.channel_id == session.channel_id and reservation.actor_id == capture_job.data['actor_id']
        and request['mode'] == authority['mode'] == 'automatic' and request['kind'] == 'clip'
        and request['seconds'] == data['seconds'] and request['request_key'] == 'clip:' + session.id
        and authority['scope'] == 'published_instance' and authority['control_authorized'] is False
        and authority['raw_media_cloud_allowed'] is False)
    instance = row(db, m.DomainInstance, association['instance_id'], p)
    release = row(db, m.InstanceRelease, association['instance_release_id'], p)
    requirements = verify_release(db, p, release)
    need(requirements is not None and release.instance_id == instance.id == plan.instance_id
        and authority['instance_id'] == instance.id and authority['instance_release_id'] == release.id)
    entity = row(db, m.Entity, association['entity_id'], p)
    source = row(db, m.Capability, association['source_id'], p)
    package = row(db, m.Capability, release.package_id, p)
    binding = row(db, m.VideoDomainBinding, association['binding_id'], p)
    channel = row(db, m.MediaChannel, session.channel_id, p)
    gateway = row(db, m.MediaGateway, channel.gateway_id, p)
    need(source.kind == 'source' and source.content_hash == digest(source.data)
        and source.validated_hash == source.content_hash and package.content_hash == association['package_hash']
        and entity.namespace == instance.namespace
        and binding.channel_id == session.channel_id and authority['binding_id'] == binding.id
        and authority['entity_id'] == entity.id and authority['entity_version'] == association['entity_version']
        and authority['binding_generation'] == association['binding_generation']
        and authority['channel_generation'] == data['channel_generation'] == association['channel_generation']
        and authority['gateway_generation'] == data['gateway_generation'])
    resource = authorization['action']['resource']
    need(sum(same(item, authorization['action']) for item in approved['actions']) == 1
        and resource['binding_id'] == binding.id and resource['source_id'] == source.id
        and resource['source_hash'] == source.content_hash and resource['channel_id'] == session.channel_id
        and resource['gateway_id'] == gateway.id and resource['entity_id'] == entity.id
        and resource['instance_release_id'] == release.id
        and resource['installation_id'] == data['installation_id']
        and resource['installation_generation'] == data['installation_generation']
        and resource['backend'] == data['backend'] and resource['capture_hash'] == data['capture_hash'])
    business_id = association['business_id']
    businesses = [item for item in release.data['snapshot']['businesses'] if item['id'] == business_id]
    need(len(businesses) == 1 and businesses[0]['available'] is True
        and authority['business_id'] == business_id == authorization['permissions']['business_id'])
    scope = {'instance_id': instance.id, 'release_id': release.id, 'business_id': business_id,
        'business_name': businesses[0]['name'], 'entity_id': entity.id,
        'entity_version': association['entity_version'], 'source_id': source.id,
        'source_hash': source.content_hash, 'package_id': release.package_id,
        'package_hash': package.content_hash, 'configuration_hash': release.configuration_hash,
        'requirements_schema': requirements['schema'], 'requirements_hash': requirements['requirements_hash'],
        'channel_id': session.channel_id, 'gateway_id': gateway.id, 'binding_id': binding.id,
        'binding_generation': association['binding_generation'], 'session_id': session.id,
        'reservation_id': reservation.id, 'plan_id': plan.id, 'watch_id': watch.id,
        'processor_id': capture_job.data['actor_id'], 'approved_by': plan.data['approved_by'],
        'watch_owner_id': watch.owner_id}
    if frozen is None:
        definition = require_entity(db, p, instance, release, entity, lock_sources=False)
        entity_snapshot = {'id': entity.id, 'version': entity.version,
            'schema_capability_id': entity.schema_capability_id, 'namespace': entity.namespace,
            'entity_type': entity.entity_type, 'external_id': entity.external_id, 'attributes': copy.deepcopy(entity.data)}
    else:
        definition, entity_snapshot = frozen['definition'], frozen['entity_snapshot']
        _definition_structure(definition)
        need(definition['entity_id'] == entity.id and definition['instance_id'] == instance.id
            and definition['target_release_id'] == release.id
            and definition['target_configuration_hash'] == release.configuration_hash
            and entity_snapshot['id'] == entity.id and entity_snapshot['namespace'] == instance.namespace
            and entity_snapshot['version'] == association['entity_version'])
        original_release = row(db, m.InstanceRelease, definition['source_release_id'], p)
        original_package = row(db, m.Capability, definition['source_package_id'], p)
        need(original_release.instance_id == instance.id
            and original_release.package_id == original_package.id == entity_snapshot['schema_capability_id']
            and digest(original_release.data['snapshot']) == original_release.configuration_hash == definition['source_configuration_hash']
            and original_package.content_hash == digest(original_package.data) == definition['source_package_hash']
            and original_package.validated_hash == original_package.content_hash)
        source_contract = object_contract(original_release.data['snapshot'], entity_snapshot['entity_type'])
        target_contract = object_contract(release.data['snapshot'], entity_snapshot['entity_type'])
        need(same(source_contract, target_contract) and digest(target_contract) == definition['contract_hash'])
        validate(source_contract['schema'], entity_snapshot['attributes'])
    need(entity_snapshot['version'] == association['entity_version'])
    installation = _installation(db, p, data['installation_id'], data['installation_generation'],
        live=live, frozen=None if frozen is None else frozen['installation'])
    need(installation['backend'] == data['backend'])
    if live:
        _actor(db, p, p.user_id); _actor(db, p, capture_job.data['actor_id'])
        _actor(db, p, plan.data['approved_by'], admin=True); _actor(db, p, watch.owner_id, admin=True)
        from .supplement_watch import permissions
        from .supplement import validate_action
        template = checked_plan(row(db, m.SupplementPlan, watch.template_plan_id, p))
        need(same(permissions(db, p, template, authorization['action']), authorization['permissions']))
        validate_action(db, p, authorization['action'])
        need(entity.version == association['entity_version'] and binding.entity_id == entity.id
            and binding.data['generation'] == association['binding_generation'])
        from .supplement_watch import clock
        deadline = datetime.fromisoformat(authorization['deadline'])
        need(watch.status == 'active' and deadline.tzinfo is not None and clock(db) < deadline)
    return {'session': session, 'capture_job': capture_job, 'supplement_plan': plan, 'watch': watch,
        'reservation': reservation, 'queued_audit': queued[0], 'instance': instance, 'release': release,
        'plan': requirements, 'requirements': requirements, 'entity': entity, 'definition': copy.deepcopy(definition),
        'entity_snapshot': copy.deepcopy(entity_snapshot), 'source': source,
        'installation': installation, 'frozen_scope': scope,
        'authority_pin': {'reservation_receipt': copy.deepcopy(receipt),
            'approved': copy.deepcopy(approved), 'authorization': copy.deepcopy(authorization),
            'plan_proposal_hash': plan.data['proposal_hash'], 'assessment_id': plan.assessment_id,
            'assessment_result_hash': plan.data['assessment_result_hash'],
            'assessment_request_hash': assessment.data['request_hash'],
            'history_entry': copy.deepcopy(history[0]), 'queued_audit_id': queued[0].id,
            'queued_audit_hash': digest(queued[0].data)}}


def _input_value(context, lease_token):
    return {'schema': INPUT_SCHEMA, 'session_id': context['session'].id,
        'capture_job_id': context['capture_job'].id, 'actor_id': context['capture_job'].data['actor_id'],
        'lease_token': lease_token, 'session_request': _session_request(context['session']),
        **{key: copy.deepcopy(context[key]) for key in ('frozen_scope', 'definition',
            'entity_snapshot', 'installation', 'authority_pin')}}


@_checked
def freeze_capture(db, p, job):
    """Worker hook before process_capture; no old/captured Session is signed."""
    need(job.kind == 'capture_media' and job.workspace_id == p.workspace_id)
    session = row(db, m.StreamSession, job.data['session_id'], p, lock=True)
    from . import manual_video_provenance_v348 as manual
    if manual.has_declaration(db, p, session):
        return manual.freeze(db, p, session, job)
    existing = input_audits(db, p, session.id)
    if existing:
        _input_context(db, p, session, live=True)
        return True
    if session.status != 'queued' or session.evidence_id is not None or session.video_run_id is not None:
        return False
    association = session.data.get('domain_binding')
    if not session.data.get('supplement_plan_id') or type(association) is not dict or not association.get('instance_id'):
        return False
    plan = row(db, m.SupplementPlan, session.data['supplement_plan_id'], p)
    if not plan.data.get('automatic_watch_id'):
        return False
    from .leases import ownership
    lease = ownership(db)
    need(job.status == 'running' and job.data['actor_id'] == p.user_id
        and lease.get('job_id') == job.id and type(lease.get('lease_token')) is str
        and bool(lease['lease_token']) and lease['lease_token'] == job.lease_token)
    context = _authority(db, p, session, job, live=True)
    from .supplement_watch import clock
    watch = context['watch']; deadline = datetime.fromisoformat(watch.data['authorization']['deadline'])
    need(watch.status == 'active' and deadline.tzinfo is not None and clock(db) < deadline
        and plan.status == 'reading')
    from .services import audit
    audit(db, p, INPUT_ACTION, session.id, _input_value(context, lease['lease_token']))
    db.flush()
    return True


def _input_context(db, p, session, *, live=False):
    frozen = _one(db, p, INPUT_ACTION, session.id)
    data = frozen.data
    from . import manual_video_provenance_v348 as manual
    if type(data) is dict and data.get('schema') == manual.INPUT_SCHEMA:
        return manual.input_context(db, p, session, frozen, live=live)
    need(type(data) is dict and data.get('schema') == INPUT_SCHEMA and data.get('session_id') == session.id
        and type(data.get('lease_token')) is str and bool(data['lease_token'])
        and type(data.get('frozen_scope')) is dict and set(data['frozen_scope']) == SCOPE_KEYS)
    job = row(db, m.Job, data['capture_job_id'], p)
    context = _authority(db, p, session, job, live=live, frozen=data)
    need(frozen.actor_id == data['actor_id'] == job.data['actor_id']
        and same(data, _input_value(context, data['lease_token'])))
    context['input_audit'] = frozen
    return context


def _machine(run):
    value = {column.name: getattr(run, column.name) for column in run.__table__.columns
        if column.name not in {'data', 'version', 'created_at'} and not column.info.get('typed_payload_internal')}
    value['data'] = {key: item for key, item in run.data.items()
        if key not in {'review', 'review_draft', 'review_history', 'reviewed_at'}}
    return value


def _frames(db, p, run):
    from .evidence import evidence_bytes
    from .local_video import validate_frame
    frames = list(db.scalars(select(m.VideoFrame).where(m.VideoFrame.workspace_id == p.workspace_id,
        m.VideoFrame.run_id == run.id).order_by(m.VideoFrame.sample_index)))
    result = run.data['result']
    need(1 <= len(frames) <= 120 and type(result['sample_count']) is int
        and result['sample_count'] == len(frames) and type(run.data['saved_samples']) is int
        and run.data['saved_samples'] == len(frames))
    pins, previous = [], -1
    for index, frame in enumerate(frames):
        need(type(frame.sample_index) is int and frame.sample_index == index)
        value = frame.data
        stable = {key: value[key] for key in ('frame_sha256', 'pts', 'time_base', 'seconds',
            'width', 'height', 'segment', 'detections', 'tracks', 'events', 'gap_before')}
        evidence, content = evidence_bytes(db, p, frame.evidence_id)
        need(evidence.sha256 == value['frame_sha256'] and digest(stable) == value['receipt_hash']
            and value['pipeline_hash'] == run.data['pipeline_hash']
            and same(value['source_context'], run.data['source_context'])
            and value['not_authoritative'] is True and _finite(value['seconds']) and value['seconds'] > previous)
        validate_frame({**stable, 'file': 'frame-' + format(index, '04d') + '.jpg', 'sample_index': index}, content, run.data['backend'])
        previous = value['seconds']
        events = list(db.scalars(select(m.Event).where(m.Event.workspace_id == p.workspace_id,
            m.Event.kind == 'video.data', m.Event.idempotency_key == 'video-data:' + run.id + ':' + str(index)).limit(2)))
        need(len(events) == 1 and events[0].status == 'received'
            and same(events[0].data, {'run_id': run.id, 'frame_id': frame.id,
                'actor_id': row(db, m.Job, run.data['job_id'], p).data['actor_id']}))
        memories = list(db.scalars(select(m.Memory).where(m.Memory.workspace_id == p.workspace_id,
            m.Memory.data['event_id'].as_string() == events[0].id,
            m.Memory.data['type'].as_string() == 'video_observation').limit(2)))
        need(len(memories) == 1 and memories[0].status == 'candidate'
            and memories[0].data['run_id'] == run.id and memories[0].data['frame_id'] == frame.id
            and memories[0].data['not_authoritative'] is True)
        pins.append({'id': frame.id, 'sample_index': index, 'evidence_id': evidence.id,
            'sha256': evidence.sha256, 'receipt_hash': value['receipt_hash'],
            'event_id': events[0].id, 'event_hash': digest(events[0].data),
            'memory_id': memories[0].id, 'memory_hash': digest(memories[0].data)})
    return frames, pins


def _run_context(db, p, run, *, live=False):
    from .evidence import evidence_bytes
    from .media import validate_completion
    session = row(db, m.StreamSession, run.data['source_context']['session_id'], p)
    context = _input_context(db, p, session, live=live)
    evidence, _ = evidence_bytes(db, p, run.evidence_id)
    job = row(db, m.Job, run.data['job_id'], p)
    source_context = run.data['source_context']
    sessions = list(db.scalars(select(m.StreamSession.id).where(m.StreamSession.workspace_id == p.workspace_id,
        m.StreamSession.video_run_id == run.id).limit(2)))
    jobs = list(db.scalars(select(m.Job.id).where(m.Job.workspace_id == p.workspace_id,
        m.Job.kind == 'process_video', m.Job.data['run_id'].as_string() == run.id).limit(2)))
    need(run.status == 'succeeded' and type(run.data['generation']) is int and run.data['generation'] == 1
        and sessions == [session.id] and jobs == [job.id]
        and job.kind == 'process_video' and job.idempotency_key == 'video:' + run.id
        and job.data['run_id'] == run.id and type(job.data['generation']) is int and job.data['generation'] == 1
        and session.status == 'captured' and session.video_run_id == run.id
        and session.evidence_id == run.evidence_id and session.data['input_sha256'] == evidence.sha256 == run.data['input_sha256']
        and context['capture_job'].status == 'succeeded'
        and run.installation_id == context['installation']['installation_id']
        and run.data['installation_generation'] == context['installation']['generation']
        and run.data['backend'] == context['installation']['backend']
        and run.data['pipeline_hash'] == (context['frozen_scope']['pipeline_hash']
            if context['frozen_scope'].get('capture_mode') == 'manual'
            else context['watch'].data['authorization']['action']['resource']['pipeline_hash'])
        and same(run.data['configuration'], session.data['configuration'])
        and source_context['channel_id'] == session.channel_id
        and source_context['gateway_generation'] == session.data['gateway_generation']
        and source_context['channel_generation'] == session.data['channel_generation']
        and source_context['evidence_id'] == evidence.id and source_context['partial'] is False
        and source_context['source_clock_utc'] == 'unknown'
        and same(source_context['domain_binding'], session.data['domain_binding'])
        and source_context['capture_hash'] == session.data['capture_hash'])
    expected_key = digest({'input': evidence.sha256, 'configuration': run.data['configuration'],
        'pipeline': run.data['pipeline_hash'], 'installation': run.installation_id,
        'generation': run.data['installation_generation'], 'backend': run.data['backend'],
        'retry_of': run.data['retry_of'], 'actor': job.data['actor_id'], 'source_context': source_context})
    need(run.request_key == expected_key and run.data['retry_of'] is None)
    completion = session.data['receipt']
    validate_completion(completion, session.data['seconds'])
    need(type(completion['cloud_calls']) is int and completion['cloud_calls'] == 0
        and completion['partial'] is False and completion['source_clock_utc'] == 'unknown'
        and source_context['received_start_unix'] == completion['received_start_unix']
        and source_context['received_end_unix'] == completion['received_end_unix']
        and source_context['captured_duration_seconds'] == completion['duration_seconds'])
    result = run.data['result']
    need(type(result) is dict and result['schema'] == 'local-video-v1' and result['cloud_calls'] == 0
        and type(result['cloud_calls']) is int and result['backend'] == run.data['backend']
        and result['audio_processed'] is False and result['tracking_identity'] == 'session_and_segment_only'
        and result['sample_hz'] == run.data['configuration']['sample_hz']
        and digest(result) == run.data['result_hash'] and _finite(result['duration_seconds'])
        and abs(result['duration_seconds'] - completion['duration_seconds']) <= .5)
    frames, frame_pins = _frames(db, p, run)
    completed = _one(db, p, 'video.completed', run.id)
    need(completed.actor_id == job.data['actor_id']
        and same(completed.data, {'samples': len(frames), 'cloud_calls': 0}))
    if live:
        _actor(db, p, job.data['actor_id'])
    context.update(run=run, job=job, evidence=evidence, frames=frames, frame_pins=frame_pins,
        completion_audit=completed, capture_receipt=copy.deepcopy(completion))
    return context


def _result_value(context, lease_token):
    run, job, frozen = context['run'], context['job'], context['input_audit']
    return {'schema': RESULT_SCHEMA, 'run_id': run.id, 'session_id': context['session'].id,
        'job_id': job.id, 'actor_id': job.data['actor_id'], 'lease_token': lease_token,
        'input_audit_id': frozen.id, 'input_audit_hash': digest(frozen.data),
        'machine_hash': digest(_machine(run)), 'result_hash': run.data['result_hash'],
        'frame_pins': copy.deepcopy(context['frame_pins']),
        'capture_receipt_hash': digest(context['capture_receipt']),
        'completion_audit_id': context['completion_audit'].id,
        'completion_audit_hash': digest(context['completion_audit'].data)}


class _Started:
    __slots__ = ('__weakref__',)


_starts = WeakKeyDictionary()


def _run_start_pin(run):
    return {'id': run.id, 'request_key': run.request_key, 'installation_id': run.installation_id,
        'evidence_id': run.evidence_id, **{key: copy.deepcopy(run.data[key]) for key in
            ('configuration', 'input_sha256', 'pipeline_hash', 'installation_generation',
             'backend', 'generation', 'retry_of', 'source_context', 'job_id')}}


@_checked
def before_result(db, p, job):
    """An in-process witness minted only before this new Run starts inference.

    It deliberately does not survive a process exit. A succeeded Run without
    its result Audit cannot be re-inferred or retrospectively signed.
    """
    need(job.kind == 'process_video' and job.workspace_id == p.workspace_id)
    run = row(db, m.VideoRun, job.data['run_id'], p)
    source_context = run.data.get('source_context')
    if type(source_context) is not dict or not has_input(db, p, source_context.get('session_id')):
        from .manual_video_provenance_v348 import has_declaration
        need(type(source_context) is not dict or not has_declaration(db, p, source_context.get('session_id')))
        need(not _audits(db, p, RESULT_ACTION, run.id))
        return None
    if _audits(db, p, RESULT_ACTION, run.id):
        return None  # freeze_result still reconstructs and verifies that Audit.
    need(run.status == 'queued' and 'result' not in run.data and 'result_hash' not in run.data
        and type(run.data['saved_samples']) is int and run.data['saved_samples'] == 0
        and db.scalar(select(m.VideoFrame.id).where(m.VideoFrame.workspace_id == p.workspace_id,
            m.VideoFrame.run_id == run.id).limit(1)) is None)
    from .leases import ownership
    lease = ownership(db)
    need(job.status == 'running' and job.data['actor_id'] == p.user_id
        and type(job.data['generation']) is int and job.data['generation'] == 1
        and run.data['job_id'] == job.id and job.idempotency_key == 'video:' + run.id
        and lease.get('job_id') == job.id and type(lease.get('lease_token')) is str
        and bool(lease['lease_token']) and lease['lease_token'] == job.lease_token)
    session = row(db, m.StreamSession, source_context['session_id'], p)
    context = _input_context(db, p, session, live=True)
    need(session.status == 'captured' and session.video_run_id == run.id
        and session.evidence_id == run.evidence_id and context['capture_job'].status == 'succeeded')
    token = _Started()
    _starts[token] = {'db': db, 'workspace_id': p.workspace_id, 'actor_id': p.user_id,
        'job_id': job.id, 'lease_token': lease['lease_token'],
        'input_audit_id': context['input_audit'].id, 'input_audit_hash': digest(context['input_audit'].data),
        'run_pin': _run_start_pin(run)}
    return token


@_checked
def freeze_result(db, p, job, *, started=None):
    """Worker hook after the actual process_video returns, before enqueue_video."""
    need(job.kind == 'process_video' and job.workspace_id == p.workspace_id)
    run = row(db, m.VideoRun, job.data['run_id'], p)
    source_context = run.data.get('source_context')
    if type(source_context) is not dict or not has_input(db, p, source_context.get('session_id')):
        from .manual_video_provenance_v348 import has_declaration
        need(type(source_context) is not dict or not has_declaration(db, p, source_context.get('session_id')))
        need(not _audits(db, p, RESULT_ACTION, run.id))
        return False
    from .leases import ownership
    lease = ownership(db)
    need(job.status == 'running' and job.data['actor_id'] == p.user_id
        and lease.get('job_id') == job.id and type(lease.get('lease_token')) is str
        and bool(lease['lease_token']) and lease['lease_token'] == job.lease_token)
    context = _run_context(db, p, run)
    existing = _audits(db, p, RESULT_ACTION, run.id)
    if existing:
        need(len(existing) == 1 and existing[0].actor_id == job.data['actor_id']
            and same(existing[0].data, _result_value(context, existing[0].data['lease_token'])))
        return True
    start = _starts.pop(started, None) if isinstance(started, _Started) else None
    need(start is not None and start['db'] is db and start['workspace_id'] == p.workspace_id
        and start['actor_id'] == p.user_id and start['job_id'] == job.id
        and start['lease_token'] == lease['lease_token']
        and start['input_audit_id'] == context['input_audit'].id
        and start['input_audit_hash'] == digest(context['input_audit'].data)
        and same(start['run_pin'], _run_start_pin(run)))
    from .services import audit
    audit(db, p, RESULT_ACTION, run.id, _result_value(context, lease['lease_token']))
    db.flush()
    return True


def record_for_run(context):
    run, scope = context['run'], context['frozen_scope']
    return {'key': 'VideoRun:' + run.id, 'modality': 'video', 'kind': 'video',
        'name': run.data['name'], 'task_id': None, 'task_title': None,
        'entity_ids': [scope['entity_id']], 'status': run.status, 'integrity_verified': True,
        'integrity_issue': None, 'metrics': {'saved_frames': len(context['frames'])},
        'source_times': [{'value': run.data['configuration'].get('recorded_at'),
            'basis': 'user_declared_unverified' if run.data['configuration'].get('recorded_at') else 'unknown'}],
        'source_sha256': [context['evidence'].sha256, *[item['sha256'] for item in context['frame_pins']]],
        'review_state': 'unreviewed', 'source_time_verified': False, 'domain_quality_verified': False}


@_checked
def checked_run(db, p, run_id, *, live=False):
    need(type(live) is bool)
    run = row(db, m.VideoRun, run_id, p)
    context = _run_context(db, p, run, live=live)
    need(context['job'].status == 'succeeded')
    saved = _one(db, p, RESULT_ACTION, run.id)
    need(type(saved.data.get('lease_token')) is str and bool(saved.data['lease_token'])
        and saved.actor_id == context['job'].data['actor_id']
        and same(saved.data, _result_value(context, saved.data['lease_token'])))
    context['result_audit'] = saved
    from .native_receipts import packet
    context['packet'] = packet(db, p, record_for_run(context), context['entity'].id)
    context.update(native_source=None, event=None, base=None, issued_audit=None)
    return context


def issued_anchor(context, observation, event):
    return {'schema': ISSUED_SCHEMA, 'run_id': context['run'].id, 'session_id': context['session'].id,
        'observation_id': observation.id, 'event_id': event.id, 'entity_id': observation.entity_id,
        'instance_id': context['instance'].id, 'release_id': context['release'].id,
        'source_id': observation.source_id, 'receipt_actor_id': observation.data['submitted_by'],
        'native_packet_hash': digest(context['packet']),
        'input_audit_id': context['input_audit'].id, 'input_audit_hash': digest(context['input_audit'].data),
        'result_audit_id': context['result_audit'].id, 'result_audit_hash': digest(context['result_audit'].data)}


def has_run_trace(db, p, run_id):
    """Select the family on any surviving independent new-family trace."""
    if _audits(db, p, RESULT_ACTION, run_id):
        return True
    if db.scalar(select(m.Audit.id).where(m.Audit.workspace_id == p.workspace_id,
            m.Audit.action == ISSUED_ACTION, m.Audit.data['run_id'].as_string() == run_id).limit(1)) is not None:
        return True
    if db.scalar(select(m.Observation.id).where(m.Observation.workspace_id == p.workspace_id,
            or_(m.Observation.data[ISSUED]['run_id'].as_string() == run_id,
                m.Observation.data['data_reception']['run_id'].as_string() == run_id)).limit(1)) is not None:
        return True
    sessions = list(db.scalars(select(m.StreamSession).where(m.StreamSession.workspace_id == p.workspace_id,
        m.StreamSession.video_run_id == run_id)))
    run = db.scalar(select(m.VideoRun).where(m.VideoRun.workspace_id == p.workspace_id, m.VideoRun.id == run_id))
    if run and type(run.data) is dict and type(run.data.get('source_context')) is dict:
        identity = run.data['source_context'].get('session_id')
        from .manual_video_provenance_v348 import has_declaration
        if has_input(db, p, identity) or has_declaration(db, p, identity):
            return True
    from .manual_video_provenance_v348 import has_declaration
    return any(has_input(db, p, session.id) or has_declaration(db, p, session) for session in sessions)


def has_issued(db, p, observation):
    if ISSUED in observation.data or _audits(db, p, ISSUED_ACTION, observation.id):
        return True
    receipt = observation.data.get('data_reception')
    if type(receipt) is dict and receipt.get('schema') == RECEPTION_SCHEMA:
        return True
    packet = observation.data.get('native_packet')
    keys = [packet.get('source_key')] if type(packet) is dict else []
    keys += [item.data.get('source_key') for item in _audits(db, p, 'observation.native_receipt_saved', observation.id)]
    for key in keys:
        if type(key) is str and key.startswith('VideoRun:'):
            run_id = key.split(':', 1)[1]
            if has_run_trace(db, p, run_id):
                return True
    return False


@_checked
def checked_observation(db, p, observation, event=None, *, live=False):
    need(observation.workspace_id == p.workspace_id and type(observation.data) is dict)
    packet = observation.data['native_packet']
    need(type(packet) is dict and type(packet.get('source_key')) is str and packet['source_key'].startswith('VideoRun:'))
    context = checked_run(db, p, packet['source_key'].split(':', 1)[1], live=live)
    need(same(packet, context['packet']) and observation.entity_id == context['entity'].id
        and observation.source_record_id == packet['source_key'] + ':' + observation.entity_id
        and observation.processing_version == digest(packet)[:40] and observation.observed_at is None
        and observation.business_day == packet['business_day'] and observation.status == 'processed'
        and observation.data['native_packet_hash'] == digest(packet)
        and observation.data['native_instance_id'] == context['instance'].id
        and observation.data['candidate_only'] is True
        and same(observation.data['extracted'], {'attributes': packet['attributes']})
        and same(observation.data['evidence_ids'], packet['evidence_ids'])
        and 'invocation_id' not in observation.data and 'extractor_id' not in observation.data)
    native_source = row(db, m.Capability, observation.source_id, p)
    need(native_source.kind == 'source' and native_source.content_hash == digest(native_source.data)
        and native_source.validated_hash == native_source.content_hash
        and observation.data['source_hash'] == native_source.content_hash
        and native_source.data['specification']['managed_instance'] == context['instance'].id)
    events = list(db.scalars(select(m.Event).where(m.Event.workspace_id == p.workspace_id,
        m.Event.kind == 'observation.data', m.Event.idempotency_key == 'data:' + observation.id).limit(2)))
    need(len(events) == 1 and (event is None or event.id == events[0].id))
    event = events[0]
    need(type(event.data) is dict and EVENT_KEYS <= set(event.data))
    base = {key: copy.deepcopy(event.data[key]) for key in EVENT_KEYS}
    expected = {'observation_id': observation.id, 'entity_id': observation.entity_id,
        'source_id': observation.source_id, 'evidence_ids': packet['evidence_ids'],
        'native_receipt': packet, 'observed_at': None, 'candidate_only': True,
        'extracted': {'attributes': packet['attributes']}, 'invocation_id': None}
    need(same(base, expected))
    issued = _one(db, p, ISSUED_ACTION, observation.id)
    need(ISSUED in observation.data and issued.actor_id == observation.data['submitted_by']
        and same(issued.data, issued_anchor(context, observation, event))
        and same(observation.data[ISSUED], issued.data))
    handoff = _one(db, p, 'observation.native_receipt_saved', observation.id)
    need(handoff.actor_id == observation.data['submitted_by']
        and same(handoff.data, {'source_key': packet['source_key'], 'instance_id': context['instance'].id}))
    reverse = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == ISSUED_ACTION, m.Audit.data['run_id'].as_string() == context['run'].id).limit(2)))
    originals = list(db.scalars(select(m.Observation).where(m.Observation.workspace_id == p.workspace_id,
        or_(m.Observation.source_record_id == observation.source_record_id,
            m.Observation.data[ISSUED]['run_id'].as_string() == context['run'].id)).limit(2)))
    need(len(reverse) == len(originals) == 1 and reverse[0].id == issued.id and originals[0].id == observation.id)
    if live:
        _actor(db, p, observation.data['submitted_by'])
    context.update(native_source=native_source, event=event, base=base, issued_audit=issued, reception_audit=handoff)
    return context
