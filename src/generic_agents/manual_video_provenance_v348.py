"""Read-before-I/O provenance for newly queued, explicitly manual video clips.

This family never upgrades an existing Session and never grants an automatic
reading permission. The general native-video receiver validates the result.
"""
import copy

from sqlalchemy import select
from . import models as m
from .contracts import digest
from . import native_video_provenance_v164 as common

DECL_ACTION = 'native_video.manual_declared_v348'
DECL_SCHEMA = 'manual-video-capture-declaration-v348'
INPUT_SCHEMA = 'manual-video-capture-input-v348'
MARKER = 'manual_video_declaration_v348'
REQUEST_KEYS = ('seconds', 'configuration', 'installation_id', 'installation_generation',
    'backend', 'capture_hash', 'generation', 'read_reservation_id',
    'read_reservation_hash', 'domain_binding', 'job_id', 'channel_generation',
    'gateway_generation', 'monitor_id', 'monitor_sequence')


def request(session):
    return {'channel_id': session.channel_id, **{k: copy.deepcopy(session.data[k]) for k in REQUEST_KEYS}}


def declarations(db, p, identity):
    return common._audits(db, p, DECL_ACTION, identity)


def has_declaration(db, p, session):
    if session is None:
        return False
    identity = session if type(session) is str else session.id
    if declarations(db, p, identity):
        return True
    if type(session) is str:
        session = db.scalar(select(m.StreamSession).where(m.StreamSession.workspace_id == p.workspace_id,
            m.StreamSession.id == identity))
    if session is not None and MARKER in session.data:
        return True
    return db.scalar(select(m.Job.id).where(m.Job.workspace_id == p.workspace_id,
        m.Job.kind == 'capture_media', m.Job.data['session_id'].as_string() == identity,
        m.Job.data[MARKER].is_not(None)).limit(1)) is not None


def context(db, p, session, job, *, live=False, frozen=None):
    from .video_read_boundary import checked, authority
    from .video_domain import snapshot
    from .instance_data_receipts import verify_release
    from .entity_release_compat import require_entity, object_contract, _definition_structure
    from .services import validate
    from .local_video import PIPELINE_HASH
    need = common.need
    d = session.data
    need(job.kind == 'capture_media' and job.workspace_id == p.workspace_id
        and job.idempotency_key == 'capture:' + session.id and job.data['session_id'] == session.id
        and d['job_id'] == job.id and d['generation'] == 1
        and d['monitor_id'] is None and d['monitor_sequence'] is None
        and not d.get('supplement_plan_id') and not d.get('continuous_id')
        and type(job.data['actor_id']) is str)
    association = d['domain_binding']
    need(type(association) is dict and association['association'] == 'observed_scene'
        and association['not_physical_identity'] is True
        and common._positive(association['entity_version'])
        and common._positive(association['binding_generation']))
    reservation = checked(common.row(db, m.VideoReadReservation, d['read_reservation_id'], p))
    receipt = reservation.data['receipt']; req, auth = receipt['request'], receipt['authority']
    need(reservation.data['receipt_hash'] == d['read_reservation_hash']
        and reservation.channel_id == session.channel_id and reservation.actor_id == job.data['actor_id']
        and req['mode'] == auth['mode'] == 'manual' and req['kind'] == 'clip'
        and req['seconds'] == d['seconds'] and req['request_key'] == 'clip:' + session.id
        and auth['scope'] == 'published_instance' and auth['control_authorized'] is False
        and auth['raw_media_cloud_allowed'] is False)
    instance = common.row(db, m.DomainInstance, association['instance_id'], p)
    release = common.row(db, m.InstanceRelease, association['instance_release_id'], p)
    requirements = verify_release(db, p, release)
    entity = common.row(db, m.Entity, association['entity_id'], p)
    source = common.row(db, m.Capability, association['source_id'], p)
    package = common.row(db, m.Capability, release.package_id, p)
    binding = common.row(db, m.VideoDomainBinding, association['binding_id'], p)
    channel = common.row(db, m.MediaChannel, session.channel_id, p)
    gateway = common.row(db, m.MediaGateway, channel.gateway_id, p)
    need(type(requirements) is dict and release.instance_id == instance.id == auth['instance_id']
        and auth['instance_release_id'] == release.id and entity.namespace == instance.namespace
        and source.kind == 'source' and source.content_hash == digest(source.data) == source.validated_hash
        and package.content_hash == digest(package.data) == package.validated_hash == association['package_hash']
        and release.package_id == association['package_id'] and binding.channel_id == session.channel_id
        and auth['binding_id'] == binding.id and auth['entity_id'] == entity.id
        and auth['entity_version'] == association['entity_version']
        and auth['binding_generation'] == association['binding_generation']
        and auth['channel_generation'] == d['channel_generation'] == association['channel_generation']
        and auth['gateway_generation'] == d['gateway_generation'])
    business_id = association['business_id']
    businesses = [item for item in release.data['snapshot']['businesses'] if item['id'] == business_id]
    need(len(businesses) == 1 and businesses[0]['available'] is True and auth['business_id'] == business_id)
    scope = {'instance_id': instance.id, 'release_id': release.id, 'business_id': business_id,
        'business_name': businesses[0]['name'], 'entity_id': entity.id,
        'entity_version': association['entity_version'], 'source_id': source.id,
        'source_hash': source.content_hash, 'package_id': release.package_id,
        'package_hash': package.content_hash, 'configuration_hash': release.configuration_hash,
        'requirements_schema': requirements['schema'], 'requirements_hash': requirements['requirements_hash'],
        'channel_id': session.channel_id, 'gateway_id': gateway.id, 'binding_id': binding.id,
        'binding_generation': association['binding_generation'], 'session_id': session.id,
        'reservation_id': reservation.id, 'plan_id': None, 'watch_id': None,
        'processor_id': job.data['actor_id'], 'approved_by': None, 'watch_owner_id': None,
        'capture_mode': 'manual', 'pipeline_hash': PIPELINE_HASH if frozen is None else frozen['frozen_scope']['pipeline_hash']}
    # The registered capability must describe the capture and processing
    # implementation actually used. A changed API changes CAPTURE_HASH; an
    # older published source must be revalidated and registered separately.
    need(common.same(source.data['execution'], {'adapter': 'media_source',
        'channel_id': session.channel_id, 'channel_generation': d['channel_generation'],
        'gateway_generation': d['gateway_generation'], 'capture_hash': d['capture_hash'],
        'pipeline_hash': scope['pipeline_hash'], 'side_effect': False}))
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
        original_release = common.row(db, m.InstanceRelease, definition['source_release_id'], p)
        original_package = common.row(db, m.Capability, definition['source_package_id'], p)
        need(original_release.instance_id == instance.id
            and original_release.package_id == original_package.id == entity_snapshot['schema_capability_id']
            and digest(original_release.data['snapshot']) == original_release.configuration_hash == definition['source_configuration_hash']
            and original_package.content_hash == digest(original_package.data) == definition['source_package_hash']
            and original_package.validated_hash == original_package.content_hash)
        source_contract = object_contract(original_release.data['snapshot'], entity_snapshot['entity_type'])
        target_contract = object_contract(release.data['snapshot'], entity_snapshot['entity_type'])
        need(common.same(source_contract, target_contract) and digest(target_contract) == definition['contract_hash'])
        validate(source_contract['schema'], entity_snapshot['attributes'])
    installation = common._installation(db, p, d['installation_id'], d['installation_generation'],
        live=live, frozen=None if frozen is None else frozen['installation'])
    need(installation['backend'] == d['backend'])
    if live:
        common._actor(db, p, p.user_id); common._actor(db, p, job.data['actor_id'])
        need(common.same(snapshot(db, p, channel), association)
            and common.same(authority(db, p, channel, gateway, 'manual'), auth)
            and entity.version == association['entity_version']
            and scope['pipeline_hash'] == PIPELINE_HASH)
    return {'session': session, 'capture_job': job, 'reservation': reservation,
        'instance': instance, 'release': release, 'plan': requirements, 'requirements': requirements,
        'entity': entity, 'source': source, 'definition': copy.deepcopy(definition),
        'entity_snapshot': copy.deepcopy(entity_snapshot), 'installation': installation,
        'frozen_scope': scope, 'authority_pin': {'reservation_receipt': copy.deepcopy(receipt)}}


def value(ctx):
    return {'schema': DECL_SCHEMA, 'session_id': ctx['session'].id,
        'capture_job_id': ctx['capture_job'].id, 'actor_id': ctx['capture_job'].data['actor_id'],
        'session_request': request(ctx['session']), **{key: copy.deepcopy(ctx[key]) for key in
        ('frozen_scope', 'definition', 'entity_snapshot', 'installation', 'authority_pin')}}


@common._checked
def declare(db, p, session):
    """Call only in the manual API's original queue transaction."""
    association = session.data.get('domain_binding')
    if type(association) is not dict or not association.get('instance_id'):
        return False
    from .instance_data_receipts import verify_release
    release = common.row(db, m.InstanceRelease, association['instance_release_id'], p)
    if verify_release(db, p, release) is None:
        return False
    job = common.row(db, m.Job, session.data['job_id'], p)
    common.need(session.status == 'queued' and session.evidence_id is None and session.video_run_id is None
        and job.status == 'queued' and not declarations(db, p, session.id)
        and MARKER not in session.data and MARKER not in job.data)
    ctx = context(db, p, session, job, live=True)
    from .services import audit
    declaration = value(ctx)
    audit(db, p, DECL_ACTION, session.id, declaration)
    db.flush()
    anchor = common._one(db, p, DECL_ACTION, session.id)
    marker = {'schema': DECL_SCHEMA, 'audit_id': anchor.id, 'audit_hash': digest(declaration)}
    session.data = {**session.data, MARKER: marker}
    job.data = {**job.data, MARKER: copy.deepcopy(marker)}
    db.flush()
    return True


@common._checked
def checked_declaration(db, p, session, job, *, live=False):
    anchor = common._one(db, p, DECL_ACTION, session.id)
    data = anchor.data
    common.need(type(data) is dict and data.get('schema') == DECL_SCHEMA
        and anchor.actor_id == data['actor_id'] == job.data['actor_id'])
    marker = {'schema': DECL_SCHEMA, 'audit_id': anchor.id, 'audit_hash': digest(data)}
    common.need(common.same(session.data.get(MARKER), marker) and common.same(job.data.get(MARKER), marker))
    ctx = context(db, p, session, job, live=live, frozen=data)
    common.need(common.same(data, value(ctx)))
    ctx['declaration_audit'] = anchor
    return ctx


def input_value(ctx, lease_token):
    return {'schema': INPUT_SCHEMA, 'session_id': ctx['session'].id,
        'capture_job_id': ctx['capture_job'].id, 'actor_id': ctx['capture_job'].data['actor_id'],
        'lease_token': lease_token, 'declaration_audit_id': ctx['declaration_audit'].id,
        'declaration_audit_hash': digest(ctx['declaration_audit'].data)}


@common._checked
def freeze(db, p, session, job):
    from .leases import ownership
    from .video_read_boundary import guard
    lease = ownership(db)
    common.need(job.status == 'running' and job.data['actor_id'] == p.user_id
        and lease.get('job_id') == job.id and type(lease.get('lease_token')) is str
        and bool(lease['lease_token']) and lease['lease_token'] == job.lease_token)
    ctx = checked_declaration(db, p, session, job, live=True)
    common.need(session.status == 'queued' and session.evidence_id is None and session.video_run_id is None
        and not common.input_audits(db, p, session.id))
    guard(db, p, session.data['read_reservation_id'])
    from .services import audit
    audit(db, p, common.INPUT_ACTION, session.id, input_value(ctx, lease['lease_token']))
    db.flush()
    return True


@common._checked
def input_context(db, p, session, anchor, *, live=False):
    data = anchor.data
    common.need(type(data) is dict and data.get('schema') == INPUT_SCHEMA
        and data.get('session_id') == session.id and type(data.get('lease_token')) is str
        and bool(data['lease_token']))
    job = common.row(db, m.Job, data['capture_job_id'], p)
    ctx = checked_declaration(db, p, session, job, live=live)
    common.need(anchor.actor_id == data['actor_id'] == job.data['actor_id']
        and common.same(data, input_value(ctx, data['lease_token'])))
    ctx['input_audit'] = anchor
    return ctx
