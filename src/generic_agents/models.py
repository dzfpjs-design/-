from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import String, Integer, Boolean, DateTime, ForeignKey, UniqueConstraint, Text, Index, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now():
    return datetime.now(timezone.utc)


def uid():
    return str(uuid4())


class Base(DeclarativeBase):
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        from .typed_payload_orm_v131 import instrument_model
        instrument_model(cls)


class Row:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Workspace(Row, Base):
    __tablename__ = "workspaces"
    name: Mapped[str] = mapped_column(String(200))


class User(Row, Base):
    __tablename__ = "users"
    username: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Membership(Row, Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "workspace_id"),)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    role: Mapped[str] = mapped_column(String(30))


class AuthSession(Row, Base):
    __tablename__ = "auth_sessions"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    kind: Mapped[str] = mapped_column(String(20), default="cookie")


class Scoped:
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)


class Payload:
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)


class Capability(Row, Scoped, Payload, Base):
    __tablename__ = "capabilities"
    __table_args__ = (UniqueConstraint("workspace_id", "namespace", "name", "release_version"),)
    namespace: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(100))
    release_version: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30), default="draft")
    content_hash: Mapped[str] = mapped_column(String(64))
    validated_hash: Mapped[str | None] = mapped_column(String(64))


class MqttGateway(Row, Scoped, Payload, Base):
    __tablename__ = 'mqtt_gateways'
    __table_args__ = (UniqueConstraint('workspace_id','creation_request_id'),)
    name: Mapped[str] = mapped_column(String(200))
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    status: Mapped[str] = mapped_column(String(30), default='paused')
    creation_request_id: Mapped[str | None] = mapped_column(String(36))
    creation_hash: Mapped[str | None] = mapped_column(String(64))


class MqttReceiverState(Row, Scoped, Payload, Base):
    __tablename__ = 'mqtt_receiver_states'
    __table_args__ = (UniqueConstraint('gateway_id'),)
    gateway_id: Mapped[str] = mapped_column(ForeignKey('mqtt_gateways.id'), index=True)
    gateway_version: Mapped[int] = mapped_column(Integer)
    config_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30))
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MqttReceipt(Row, Scoped, Payload, Base):
    __tablename__ = 'mqtt_receipts'
    __table_args__ = (UniqueConstraint('gateway_id', 'packet_key'),)
    gateway_id: Mapped[str] = mapped_column(ForeignKey('mqtt_gateways.id'), index=True)
    evidence_id: Mapped[str] = mapped_column(ForeignKey('evidence_objects.id'))
    packet_key: Mapped[str] = mapped_column(String(64))
    source_record_id: Mapped[str | None] = mapped_column(String(80), index=True)
    input_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30))


class MqttBinding(Row, Scoped, Payload, Base):
    __tablename__ = 'mqtt_bindings'
    __table_args__ = (UniqueConstraint('gateway_id', 'topic', 'config_hash'),)
    gateway_id: Mapped[str] = mapped_column(ForeignKey('mqtt_gateways.id'), index=True)
    topic: Mapped[str] = mapped_column(String(256))
    config_hash: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str] = mapped_column(ForeignKey('resource_entities.id'))
    source_id: Mapped[str | None] = mapped_column(ForeignKey('capabilities.id'))
    extractor_id: Mapped[str] = mapped_column(ForeignKey('capabilities.id'))


class MqttBindingRoute(Row, Scoped, Payload, Base):
    __tablename__ = 'mqtt_binding_routes'
    __table_args__ = (UniqueConstraint('workspace_id', 'creation_request_id'),
                     UniqueConstraint('legacy_binding_id'),)
    gateway_id: Mapped[str] = mapped_column(ForeignKey('mqtt_gateways.id'), index=True)
    status: Mapped[str] = mapped_column(String(30), default='active')
    current_revision: Mapped[int] = mapped_column(Integer, default=1)
    creation_request_id: Mapped[str | None] = mapped_column(String(36))
    legacy_binding_id: Mapped[str | None] = mapped_column(ForeignKey('mqtt_bindings.id'))


class MqttBindingRevision(Row, Scoped, Payload, Base):
    __tablename__ = 'mqtt_binding_revisions'
    __table_args__ = (UniqueConstraint('route_id', 'revision'),
                     UniqueConstraint('workspace_id', 'request_id'),)
    route_id: Mapped[str] = mapped_column(ForeignKey('mqtt_binding_routes.id'), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    request_id: Mapped[str | None] = mapped_column(String(36))
    request_hash: Mapped[str | None] = mapped_column(String(64))
    gateway_id: Mapped[str] = mapped_column(ForeignKey('mqtt_gateways.id'), index=True)
    topic: Mapped[str] = mapped_column(String(256))
    config_hash: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str] = mapped_column(ForeignKey('resource_entities.id'))
    source_id: Mapped[str | None] = mapped_column(ForeignKey('capabilities.id'))
    extractor_id: Mapped[str] = mapped_column(ForeignKey('capabilities.id'))
    content_hash: Mapped[str | None] = mapped_column(String(64))


class MqttReceiptDelivery(Row, Scoped, Payload, Base):
    __tablename__ = 'mqtt_receipt_deliveries'
    __table_args__ = (UniqueConstraint('receipt_id', 'binding_id'),)
    receipt_id: Mapped[str] = mapped_column(ForeignKey('mqtt_receipts.id'), index=True)
    # Legacy binding IDs and new revision IDs deliberately share this proof key.
    binding_id: Mapped[str] = mapped_column(String(36), index=True)
    route_id: Mapped[str] = mapped_column(String(36), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default='pending')
    observation_id: Mapped[str | None] = mapped_column(ForeignKey('observations.id'), index=True)


class MqttDailyLedger(Row, Scoped, Payload, Base):
    __tablename__ = 'mqtt_daily_ledgers'
    __table_args__ = (UniqueConstraint('workspace_id', 'gateway_id', 'day_key'),
                     Index('ix_mqtt_daily_ledger_days', 'workspace_id', 'gateway_id', 'created_at', 'id'),)
    gateway_id: Mapped[str] = mapped_column(ForeignKey('mqtt_gateways.id'), index=True)
    day_key: Mapped[str] = mapped_column(String(20))
    revision: Mapped[int] = mapped_column(Integer, default=0)
    published_revision: Mapped[int] = mapped_column(Integer, default=0)


class MqttDailyEntry(Row, Scoped, Payload, Base):
    __tablename__ = 'mqtt_daily_entries'
    __table_args__ = (UniqueConstraint('ledger_id', 'item_key'),)
    ledger_id: Mapped[str] = mapped_column(ForeignKey('mqtt_daily_ledgers.id'), index=True)
    item_key: Mapped[str] = mapped_column(String(100))
    current_revision: Mapped[int] = mapped_column(Integer)
    current_hash: Mapped[str] = mapped_column(String(64))


class MqttDailyChange(Row, Scoped, Base):
    __tablename__ = 'mqtt_daily_changes'
    __table_args__ = (UniqueConstraint('ledger_id', 'revision'),
        Index('ix_mqtt_daily_change_latest', 'ledger_id', 'item_key', 'revision'),
        Index('ix_mqtt_daily_change_pages', 'ledger_id', 'item_created_at', 'item_key', 'revision'),
        Index('ix_mqtt_daily_change_instance', 'workspace_id', 'instance_id', 'ledger_id', 'revision'),)
    ledger_id: Mapped[str] = mapped_column(ForeignKey('mqtt_daily_ledgers.id'), index=True)
    entry_id: Mapped[str] = mapped_column(ForeignKey('mqtt_daily_entries.id'))
    revision: Mapped[int] = mapped_column(Integer)
    item_key: Mapped[str] = mapped_column(String(100))
    item_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(30))
    instance_id: Mapped[str | None] = mapped_column(String(36))
    receipt_id: Mapped[str] = mapped_column(ForeignKey('mqtt_receipts.id'), index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict] = mapped_column(JSONB)


class MqttDailyCheckpoint(Row, Scoped, Payload, Base):
    __tablename__ = 'mqtt_daily_checkpoints'
    __table_args__ = (UniqueConstraint('workspace_id', 'request_id'),
                     UniqueConstraint('workspace_id', 'system_key'),)
    gateway_id: Mapped[str] = mapped_column(ForeignKey('mqtt_gateways.id'), index=True)
    ledger_id: Mapped[str | None] = mapped_column(ForeignKey('mqtt_daily_ledgers.id'), index=True)
    purpose: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(30), default='queued')
    request_id: Mapped[str | None] = mapped_column(String(36))
    system_key: Mapped[str | None] = mapped_column(String(100))


class Entity(Row, Scoped, Payload, Base):
    __tablename__ = "resource_entities"
    __table_args__ = (UniqueConstraint("workspace_id", "namespace", "entity_type", "external_id"),)
    namespace: Mapped[str] = mapped_column(String(100))
    entity_type: Mapped[str] = mapped_column(String(100))
    external_id: Mapped[str] = mapped_column(String(200))
    schema_capability_id: Mapped[str] = mapped_column(ForeignKey("capabilities.id"))
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EntityEvent(Row, Scoped, Payload, Base):
    __tablename__ = "resource_events"
    entity_id: Mapped[str] = mapped_column(ForeignKey("resource_entities.id"))
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))


class VectorCache(Row, Scoped, Payload, Base):
    __tablename__ = "retrieval_vectors"
    __table_args__ = (UniqueConstraint("workspace_id", "cache_key"),)
    cache_key: Mapped[str] = mapped_column(String(64))
    model_profile_id: Mapped[str] = mapped_column(ForeignKey("capabilities.id"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="ready")


class Relation(Row, Scoped, Payload, Base):
    __tablename__ = "entity_relations"
    package_id: Mapped[str] = mapped_column(ForeignKey("capabilities.id"), index=True)
    relation_type: Mapped[str] = mapped_column(String(100))
    source_entity_id: Mapped[str] = mapped_column(ForeignKey("resource_entities.id"), index=True)
    target_entity_id: Mapped[str] = mapped_column(ForeignKey("resource_entities.id"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="proposed")
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RelationEvent(Row, Scoped, Payload, Base):
    __tablename__ = "relation_events"
    relation_id: Mapped[str] = mapped_column(ForeignKey("entity_relations.id"), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))


class Task(Row, Scoped, Payload, Base):
    __tablename__ = "tasks"
    title: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(40), default="draft")
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    package_id: Mapped[str] = mapped_column(ForeignKey("capabilities.id"))


class Plan(Row, Scoped, Payload, Base):
    __tablename__ = "plan_versions"
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"))
    status: Mapped[str] = mapped_column(String(30), default="review")
    approved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    approved_hash: Mapped[str | None] = mapped_column(String(64))


class Observation(Row, Scoped, Payload, Base):
    __tablename__ = "observations"
    __table_args__ = (UniqueConstraint("workspace_id", "source_id", "source_record_id", "processing_version"),)
    source_id: Mapped[str] = mapped_column(ForeignKey("capabilities.id"))
    source_record_id: Mapped[str] = mapped_column(String(200))
    processing_version: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(ForeignKey("resource_entities.id"))
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(30), default="received")
    business_day: Mapped[str | None] = mapped_column(String(10))


class CandidateWatch(Row, Scoped, Payload, Base):
    __tablename__ = 'candidate_watches'
    instance_id: Mapped[str] = mapped_column(ForeignKey('domain_instances.id'), index=True)
    status: Mapped[str] = mapped_column(String(30), default='enabled')


class PerceptionSignal(Row, Scoped, Payload, Base):
    __tablename__ = 'perception_signals'
    __table_args__ = (UniqueConstraint('workspace_id', 'event_id'),)
    event_id: Mapped[str] = mapped_column(ForeignKey('outbox_events.id'))
    instance_id: Mapped[str] = mapped_column(ForeignKey('domain_instances.id'), index=True)
    entity_id: Mapped[str] = mapped_column(ForeignKey('resource_entities.id'))
    status: Mapped[str] = mapped_column(String(30), default='unassigned')


class Memory(Row, Scoped, Payload, Base):
    __tablename__ = "memory_items"
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"))
    status: Mapped[str] = mapped_column(String(30), default="candidate")
    text: Mapped[str] = mapped_column(Text)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Evidence(Row, Scoped, Payload, Base):
    __tablename__ = "evidence_objects"
    sha256: Mapped[str] = mapped_column(String(64))
    media_type: Mapped[str] = mapped_column(String(150))
    size_bytes: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(String(200))


class SetupDraft(Row, Scoped, Payload, Base):
    __tablename__ = "setup_drafts"
    __table_args__ = (UniqueConstraint("workspace_id"),)


class DomainInstance(Row, Scoped, Payload, Base):
    __tablename__ = 'domain_instances'
    namespace: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(40), default='draft')
    active_release_id: Mapped[str | None] = mapped_column(ForeignKey('domain_instance_releases.id'))


class InstanceRelease(Row, Scoped, Payload, Base):
    __tablename__ = 'domain_instance_releases'
    __table_args__ = (UniqueConstraint('instance_id','release_number'),UniqueConstraint('instance_id','configuration_hash'))
    instance_id: Mapped[str] = mapped_column(ForeignKey('domain_instances.id'))
    release_number: Mapped[int] = mapped_column(Integer)
    configuration_hash: Mapped[str] = mapped_column(String(64))
    package_id: Mapped[str | None] = mapped_column(ForeignKey('capabilities.id'))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    status: Mapped[str] = mapped_column(String(40), default='draft')


class SetupRevision(Row, Scoped, Payload, Base):
    __tablename__ = "setup_revisions"
    __table_args__ = (UniqueConstraint("draft_id", "version"),)
    draft_id: Mapped[str] = mapped_column(ForeignKey("setup_drafts.id"))


class SetupMaterial(Row, Scoped, Payload, Base):
    __tablename__ = "setup_materials"
    __table_args__ = (UniqueConstraint("workspace_id", "sha256"),)
    name: Mapped[str] = mapped_column(String(250))
    sha256: Mapped[str] = mapped_column(String(64))
    evidence_id: Mapped[str] = mapped_column(ForeignKey("evidence_objects.id"))
    status: Mapped[str] = mapped_column(String(30), default="uploaded")
    archived: Mapped[bool] = mapped_column(Boolean, default=False)


class SetupAnalysis(Row, Scoped, Payload, Base):
    __tablename__ = "setup_analyses"
    status: Mapped[str] = mapped_column(String(30), default="queued")


class MaterialOcrRun(Row, Scoped, Payload, Base):
    __tablename__ = 'material_ocr_runs'
    __table_args__ = (UniqueConstraint('material_id', 'generation'),)
    material_id: Mapped[str] = mapped_column(ForeignKey('setup_materials.id'), index=True)
    generation: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default='queued')


class MaterialOcrUnit(Row, Scoped, Payload, Base):
    __tablename__ = 'material_ocr_units'
    __table_args__ = (UniqueConstraint('run_id', 'unit_index'),)
    material_id: Mapped[str] = mapped_column(ForeignKey('setup_materials.id'))
    run_id: Mapped[str] = mapped_column(ForeignKey('material_ocr_runs.id'), index=True)
    unit_index: Mapped[int] = mapped_column(Integer)
    evidence_id: Mapped[str] = mapped_column(ForeignKey('evidence_objects.id'))
    status: Mapped[str] = mapped_column(String(30), default='queued')


class MaterialOcrVersion(Row, Scoped, Base):
    __tablename__ = 'material_ocr_versions'
    __table_args__ = (UniqueConstraint('unit_id', 'number'),)
    run_id: Mapped[str] = mapped_column(ForeignKey('material_ocr_runs.id'))
    unit_id: Mapped[str] = mapped_column(ForeignKey('material_ocr_units.id'), index=True)
    number: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20))
    content_hash: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict] = mapped_column(JSONB)


class MaterialOcrConfirmation(Row, Scoped, Base):
    __tablename__ = 'material_ocr_confirmations'
    material_id: Mapped[str] = mapped_column(ForeignKey('setup_materials.id'), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey('material_ocr_runs.id'))
    material_version: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict] = mapped_column(JSONB)


class MaterialOcrRequest(Row, Scoped, Base):
    __tablename__ = 'material_ocr_requests'
    __table_args__ = (UniqueConstraint('material_id', 'actor_id', 'request_id'),)
    material_id: Mapped[str] = mapped_column(ForeignKey('setup_materials.id'), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    request_id: Mapped[str] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(30))
    state: Mapped[str] = mapped_column(String(20))
    body_hash: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict] = mapped_column(JSONB)


class SpecialistInstallation(Row, Scoped, Payload, Base):
    __tablename__ = 'specialist_installations'
    __table_args__ = (UniqueConstraint('workspace_id', 'pack_id'),)
    pack_id: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(30), default='queued')


class SpecialistRun(Row, Scoped, Payload, Base):
    __tablename__ = 'specialist_runs'
    installation_id: Mapped[str] = mapped_column(ForeignKey('specialist_installations.id'))
    evidence_id: Mapped[str] = mapped_column(ForeignKey('evidence_objects.id'))
    status: Mapped[str] = mapped_column(String(30), default='queued')


class WorkflowAudio(Row, Scoped, Payload, Base):
    __tablename__ = 'workflow_audio_inputs'
    __table_args__ = (UniqueConstraint('instance_id','request_id'),)
    instance_id: Mapped[str] = mapped_column(ForeignKey('domain_instances.id'))
    source_run_id: Mapped[str] = mapped_column(ForeignKey('audio_runs.id'))
    request_id: Mapped[str] = mapped_column(String(100))


class AudioRun(Row, Scoped, Payload, Base):
    __tablename__ = 'audio_runs'
    __table_args__ = (UniqueConstraint('workspace_id', 'request_id'),)
    request_id: Mapped[str] = mapped_column(String(64))
    evidence_id: Mapped[str] = mapped_column(ForeignKey('evidence_objects.id'))
    status: Mapped[str] = mapped_column(String(30), default='queued')


class SpeechActivityRun(Row, Scoped, Payload, Base):
    __tablename__ = 'speech_activity_runs'
    __table_args__ = (UniqueConstraint('workspace_id', 'request_id'),)
    request_id: Mapped[str] = mapped_column(String(64))
    evidence_id: Mapped[str] = mapped_column(ForeignKey('evidence_objects.id'))
    status: Mapped[str] = mapped_column(String(30), default='queued')


class SegmentedAudioRun(Row, Scoped, Payload, Base):
    __tablename__ = 'segmented_audio_runs'
    __table_args__ = (UniqueConstraint('workspace_id', 'request_id'),)
    request_id: Mapped[str] = mapped_column(String(64))
    source_run_id: Mapped[str] = mapped_column(ForeignKey('speech_activity_runs.id'))
    status: Mapped[str] = mapped_column(String(30), default='queued')


class AudioDaily(Row, Scoped, Payload, Base):
    __tablename__ = 'audio_daily_collections'
    __table_args__ = (UniqueConstraint('workspace_id','received_day'),)
    received_day: Mapped[str] = mapped_column(String(10))


class VideoRun(Row, Scoped, Payload, Base):
    __tablename__ = 'video_runs'
    __table_args__ = (UniqueConstraint('workspace_id', 'request_key'),)
    request_key: Mapped[str] = mapped_column(String(64))
    installation_id: Mapped[str] = mapped_column(ForeignKey('specialist_installations.id'))
    evidence_id: Mapped[str] = mapped_column(ForeignKey('evidence_objects.id'))
    status: Mapped[str] = mapped_column(String(30), default='queued')


class VideoFrame(Row, Scoped, Payload, Base):
    __tablename__ = 'video_frames'
    __table_args__ = (UniqueConstraint('run_id', 'sample_index'),)
    run_id: Mapped[str] = mapped_column(ForeignKey('video_runs.id'))
    sample_index: Mapped[int] = mapped_column(Integer)
    evidence_id: Mapped[str] = mapped_column(ForeignKey('evidence_objects.id'))


class VideoAlert(Row, Scoped, Payload, Base):
    __tablename__ = 'video_alerts'
    __table_args__ = (UniqueConstraint('run_id', 'event_key'),)
    run_id: Mapped[str] = mapped_column(ForeignKey('video_runs.id'))
    event_key: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30), default='pending')


class MediaGateway(Row, Scoped, Payload, Base):
    __tablename__ = 'media_gateways'
    status: Mapped[str] = mapped_column(String(30), default='draft')


class MediaChannel(Row, Scoped, Payload, Base):
    __tablename__ = 'media_channels'
    __table_args__ = (UniqueConstraint('workspace_id', 'gateway_id', 'path'),)
    gateway_id: Mapped[str] = mapped_column(ForeignKey('media_gateways.id'))
    path: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(30), default='confirmed')


class MediaMonitor(Row, Scoped, Payload, Base):
    __tablename__ = 'media_monitors'
    channel_id: Mapped[str] = mapped_column(ForeignKey('media_channels.id'), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    status: Mapped[str] = mapped_column(String(30), default='active')


class VideoDomainBinding(Row, Scoped, Payload, Base):
    __tablename__ = 'video_domain_bindings'
    __table_args__ = (UniqueConstraint('workspace_id', 'channel_id'),)
    channel_id: Mapped[str] = mapped_column(ForeignKey('media_channels.id'))
    entity_id: Mapped[str] = mapped_column(ForeignKey('resource_entities.id'))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    status: Mapped[str] = mapped_column(String(30), default='active')


class VideoDaily(Row, Scoped, Payload, Base):
    __tablename__ = 'video_daily_collections'
    __table_args__ = (UniqueConstraint('workspace_id', 'source_key', 'business_day'),)
    source_key: Mapped[str] = mapped_column(String(100))
    business_day: Mapped[str] = mapped_column(String(10))


class ContinuousStream(Row, Scoped, Payload, Base):
    __tablename__ = 'continuous_video_streams'
    channel_id: Mapped[str] = mapped_column(ForeignKey('media_channels.id'), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    status: Mapped[str] = mapped_column(String(30), default='queued')


class StreamSession(Row, Scoped, Payload, Base):
    __tablename__ = 'stream_sessions'
    channel_id: Mapped[str] = mapped_column(ForeignKey('media_channels.id'))
    evidence_id: Mapped[str | None] = mapped_column(ForeignKey('evidence_objects.id'))
    video_run_id: Mapped[str | None] = mapped_column(ForeignKey('video_runs.id'))
    status: Mapped[str] = mapped_column(String(30), default='queued')


class VideoReadReservation(Row, Scoped, Payload, Base):
    __tablename__ = 'video_read_reservations'
    __table_args__ = (UniqueConstraint('workspace_id', 'request_key'),)
    channel_id: Mapped[str] = mapped_column(ForeignKey('media_channels.id'), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    source_key: Mapped[str] = mapped_column(String(64), index=True)
    request_key: Mapped[str] = mapped_column(String(160))


class Job(Row, Scoped, Payload, Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("workspace_id", "idempotency_key"),)
    kind: Mapped[str] = mapped_column(String(40))
    idempotency_key: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[str | None] = mapped_column(String(36))


class Event(Row, Scoped, Payload, Base):
    __tablename__ = "outbox_events"
    __table_args__ = (UniqueConstraint("workspace_id", "idempotency_key"),)
    kind: Mapped[str] = mapped_column(String(50))
    idempotency_key: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), default="pending_delivery")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Issue(Row, Scoped, Payload, Base):
    __tablename__ = "data_quality_issues"
    entity_id: Mapped[str | None] = mapped_column(ForeignKey("resource_entities.id"))
    kind: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(30), default="open")


class Governance(Row, Scoped, Payload, Base):
    __tablename__ = "data_governance_plans"
    issue_id: Mapped[str] = mapped_column(ForeignKey("data_quality_issues.id"))
    status: Mapped[str] = mapped_column(String(30), default="review")
    approved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))


class QualityPolicy(Row, Scoped, Payload, Base):
    __tablename__ = 'data_quality_policies'
    name: Mapped[str] = mapped_column(String(200))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    status: Mapped[str] = mapped_column(String(30),default='active')
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=now)


class IssueCheck(Row, Scoped, Payload, Base):
    __tablename__ = 'data_quality_issue_checks'
    issue_id: Mapped[str] = mapped_column(ForeignKey('data_quality_issues.id'))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))


class Acquisition(Row, Scoped, Payload, Base):
    __tablename__ = "acquisition_tasks"
    __table_args__ = (UniqueConstraint("workspace_id", "idempotency_key"),)
    entity_id: Mapped[str] = mapped_column(ForeignKey("resource_entities.id"))
    capability_id: Mapped[str] = mapped_column(ForeignKey("capabilities.id"))
    idempotency_key: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), default="proposed")


class SensorAsset(Row, Scoped, Payload, Base):
    __tablename__ = "sensor_assets"
    __table_args__ = (UniqueConstraint("workspace_id", "source_id", "external_id"),)
    source_id: Mapped[str] = mapped_column(ForeignKey("capabilities.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), default="enabled")


class SensorAssetEvent(Row, Scoped, Payload, Base):
    __tablename__ = "sensor_asset_events"
    asset_id: Mapped[str] = mapped_column(ForeignKey("sensor_assets.id"), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))


class ObservationPlan(Row, Scoped, Payload, Base):
    __tablename__ = "observation_plans"
    policy_id: Mapped[str] = mapped_column(ForeignKey("monitor_policies.id"), index=True)
    entity_id: Mapped[str] = mapped_column(ForeignKey("resource_entities.id"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="selecting")


class SensorReservation(Row, Scoped, Base):
    __tablename__ = "sensor_reservations"
    __table_args__ = (UniqueConstraint("workspace_id", "asset_id"),)
    asset_id: Mapped[str] = mapped_column(ForeignKey("sensor_assets.id"))
    acquisition_id: Mapped[str | None] = mapped_column(ForeignKey("acquisition_tasks.id"))


class SensorDevice(Row, Scoped, Payload, Base):
    __tablename__ = "sensor_devices"
    __table_args__ = (UniqueConstraint("workspace_id", "identity_scheme", "identity_issuer", "identity_reference"),)
    title: Mapped[str] = mapped_column(String(200))
    identity_scheme: Mapped[str] = mapped_column(String(30))
    identity_issuer: Mapped[str] = mapped_column(String(100))
    identity_reference: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), default="review")
    approved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))


class SensorDeviceEvent(Row, Scoped, Payload, Base):
    __tablename__ = "sensor_device_events"
    device_id: Mapped[str] = mapped_column(ForeignKey("sensor_devices.id"), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))


class AssetDeviceBinding(Row, Scoped, Payload, Base):
    __tablename__ = "sensor_asset_device_bindings"
    __table_args__ = (UniqueConstraint("workspace_id", "asset_id"),)
    asset_id: Mapped[str] = mapped_column(ForeignKey("sensor_assets.id"), index=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("sensor_devices.id"), index=True)
    approved_by: Mapped[str] = mapped_column(ForeignKey("users.id"))


class DeviceReservation(Row, Scoped, Base):
    __tablename__ = "sensor_device_reservations"
    __table_args__ = (UniqueConstraint("workspace_id", "device_id"),)
    device_id: Mapped[str] = mapped_column(ForeignKey("sensor_devices.id"))
    acquisition_id: Mapped[str | None] = mapped_column(ForeignKey("acquisition_tasks.id"))


class Invocation(Row, Scoped, Payload, Base):
    __tablename__ = "invocations"
    __table_args__ = (UniqueConstraint("workspace_id", "idempotency_key"),)
    capability_id: Mapped[str] = mapped_column(ForeignKey("capabilities.id"))
    idempotency_key: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), default="created")


class Audit(Row, Scoped, Payload, Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index('uq_audit_mqtt_units_v342', 'workspace_id', 'object_id', unique=True,
                            postgresql_where=text("action = 'mqtt.observation.units_captured_v342'")),)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(100))
    object_id: Mapped[str] = mapped_column(String(100))


class DataRun(Row, Scoped, Payload, Base):
    __tablename__ = "data_query_runs"
    question: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))


class DataGap(Row, Scoped, Payload, Base):
    __tablename__ = 'data_evidence_gaps'
    __table_args__ = (UniqueConstraint('workspace_id', 'query_id', 'entity_id'),)
    query_id: Mapped[str] = mapped_column(ForeignKey('data_query_runs.id'))
    entity_id: Mapped[str] = mapped_column(ForeignKey('resource_entities.id'))
    status: Mapped[str] = mapped_column(String(40), default='pending_delivery')


class GapCheck(Row, Scoped, Payload, Base):
    __tablename__ = 'data_gap_checks'
    gap_id: Mapped[str] = mapped_column(ForeignKey('data_evidence_gaps.id'))
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))


class MonitorPolicy(Row, Scoped, Payload, Base):
    __tablename__ = "monitor_policies"
    status: Mapped[str] = mapped_column(String(30), default="review")
    approved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))


class SourceReservation(Row, Scoped, Base):
    __tablename__ = "source_reservations"
    __table_args__ = (UniqueConstraint("workspace_id", "source_id"),)
    source_id: Mapped[str] = mapped_column(ForeignKey("capabilities.id"))
    acquisition_id: Mapped[str | None] = mapped_column(ForeignKey("acquisition_tasks.id"))


class ObjectState(Row, Scoped, Payload, Base):
    __tablename__ = "object_state_snapshots"
    __table_args__ = (UniqueConstraint("workspace_id", "entity_id"),)
    entity_id: Mapped[str] = mapped_column(ForeignKey("resource_entities.id"))


class CloudCall(Row, Scoped, Payload, Base):
    __tablename__ = "cloud_model_calls"
    capability_id: Mapped[str] = mapped_column(ForeignKey("capabilities.id"))
    status: Mapped[str] = mapped_column(String(30), default="reserved")
    reserved_tokens: Mapped[int] = mapped_column(Integer)
    charged_tokens: Mapped[int] = mapped_column(Integer)


class LearningCandidate(Row, Scoped, Payload, Base):
    __tablename__ = "learning_candidates"
    __table_args__ = (UniqueConstraint("workspace_id", "content_hash"),)
    package_id: Mapped[str] = mapped_column(ForeignKey("capabilities.id"))
    title: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(40), default="draft")
    content_hash: Mapped[str] = mapped_column(String(64))


class EvaluationSet(Row, Scoped, Payload, Base):
    __tablename__ = "learning_evaluation_sets"
    package_id: Mapped[str] = mapped_column(ForeignKey("capabilities.id"))
    title: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), default="draft")
    content_hash: Mapped[str | None] = mapped_column(String(64))


class EvaluationRun(Row, Scoped, Payload, Base):
    __tablename__ = "learning_evaluation_runs"
    candidate_id: Mapped[str] = mapped_column(ForeignKey("learning_candidates.id"))
    set_id: Mapped[str] = mapped_column(ForeignKey("learning_evaluation_sets.id"))
    status: Mapped[str] = mapped_column(String(30), default="queued")


class LearningDeployment(Row, Scoped, Payload, Base):
    __tablename__ = "learning_deployments"
    package_id: Mapped[str] = mapped_column(ForeignKey("capabilities.id"))
    candidate_id: Mapped[str] = mapped_column(ForeignKey("learning_candidates.id"))
    status: Mapped[str] = mapped_column(String(40), default="shadow")
    approved_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    approved_hash: Mapped[str] = mapped_column(String(64))


class LearningChannel(Row, Scoped, Payload, Base):
    __tablename__ = "learning_channels"
    __table_args__ = (UniqueConstraint("workspace_id", "package_id"),)
    package_id: Mapped[str] = mapped_column(ForeignKey("capabilities.id"))
    active_deployment_id: Mapped[str | None] = mapped_column(ForeignKey("learning_deployments.id"))
    shadow_deployment_id: Mapped[str | None] = mapped_column(ForeignKey("learning_deployments.id"))


class LearningTrial(Row, Scoped, Payload, Base):
    __tablename__ = "learning_trials"
    __table_args__ = (UniqueConstraint("workspace_id", "deployment_id", "request_key"),)
    deployment_id: Mapped[str] = mapped_column(ForeignKey("learning_deployments.id"))
    request_key: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30), default="pending_review")


class GeoScene(Row, Scoped, Payload, Base):
    __tablename__ = 'geo_scenes'
    evidence_id: Mapped[str] = mapped_column(ForeignKey('evidence_objects.id'))
    status: Mapped[str] = mapped_column(String(30), default='queued')


class GeoRun(Row, Scoped, Payload, Base):
    __tablename__ = 'geo_runs'
    scene_id: Mapped[str] = mapped_column(ForeignKey('geo_scenes.id'))
    installation_id: Mapped[str] = mapped_column(ForeignKey('specialist_installations.id'))
    status: Mapped[str] = mapped_column(String(30), default='queued')


class WorkflowScene(Row, Scoped, Payload, Base):
    __tablename__ = 'workflow_scenes'
    scene_id: Mapped[str] = mapped_column(ForeignKey('geo_scenes.id'))
    instance_id: Mapped[str] = mapped_column(ForeignKey('domain_instances.id'))


class GeoPair(Row, Scoped, Payload, Base):
    __tablename__ = 'geo_pairs'
    before_scene_id: Mapped[str] = mapped_column(ForeignKey('geo_scenes.id'))
    after_scene_id: Mapped[str] = mapped_column(ForeignKey('geo_scenes.id'))
    status: Mapped[str] = mapped_column(String(30), default='queued')


class PixelChangeRun(Row, Scoped, Payload, Base):
    __tablename__ = 'pixel_change_runs_v249'
    __table_args__ = (UniqueConstraint('workspace_id', 'creation_request_id'),)
    instance_id: Mapped[str] = mapped_column(ForeignKey('domain_instances.id'), index=True)
    release_id: Mapped[str] = mapped_column(ForeignKey('domain_instance_releases.id'))
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    creation_request_id: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30), default='queued')


class WorkflowPixelRun(Row, Scoped, Payload, Base):
    __tablename__ = 'workflow_pixel_runs_v250'
    __table_args__ = (UniqueConstraint('workspace_id', 'invocation_id'),)
    task_id: Mapped[str] = mapped_column(ForeignKey('tasks.id'), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey('jobs.id'), index=True)
    invocation_id: Mapped[str] = mapped_column(ForeignKey('invocations.id'))
    capability_id: Mapped[str] = mapped_column(ForeignKey('capabilities.id'))
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    status: Mapped[str] = mapped_column(String(30), default='running')


class GeoDaily(Row, Scoped, Payload, Base):
    __tablename__ = 'geo_daily_collections'
    __table_args__ = (UniqueConstraint('workspace_id', 'received_day'),)
    received_day: Mapped[str] = mapped_column(String(10))


class EvidenceAssessment(Row, Scoped, Payload, Base):
    __tablename__ = 'evidence_assessments'
    __table_args__ = (UniqueConstraint('workspace_id', 'request_id'),)
    instance_id: Mapped[str] = mapped_column(ForeignKey('domain_instances.id'))
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    request_id: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(40), default='queued')


class SignalAdvice(Row, Scoped, Payload, Base):
    __tablename__ = 'signal_advice'
    __table_args__ = (UniqueConstraint('workspace_id', 'request_id'),)
    instance_id: Mapped[str] = mapped_column(ForeignKey('domain_instances.id'))
    signal_id: Mapped[str] = mapped_column(ForeignKey('perception_signals.id'))
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    request_id: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(40), default='queued')


class EvidenceQuestion(Row, Scoped, Payload, Base):
    __tablename__ = 'evidence_questions'
    __table_args__ = (UniqueConstraint('workspace_id', 'request_id'),)
    instance_id: Mapped[str] = mapped_column(ForeignKey('domain_instances.id'))
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    request_id: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(40), default='queued')


class SupplementPlan(Row, Scoped, Payload, Base):
    __tablename__ = 'supplement_plans'
    __table_args__ = (UniqueConstraint('workspace_id', 'request_id'),)
    instance_id: Mapped[str] = mapped_column(ForeignKey('domain_instances.id'))
    assessment_id: Mapped[str] = mapped_column(ForeignKey('evidence_assessments.id'))
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    request_id: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30), default='proposed')


class SupplementWatch(Row, Scoped, Payload, Base):
    __tablename__ = 'supplement_watches'
    __table_args__ = (UniqueConstraint('workspace_id', 'request_id'),)
    instance_id: Mapped[str] = mapped_column(ForeignKey('domain_instances.id'))
    template_plan_id: Mapped[str] = mapped_column(ForeignKey('supplement_plans.id'))
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    request_id: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30), default='active')


class WMSConnection(Row, Scoped, Payload, Base):
    __tablename__ = 'wms_connections'
    __table_args__ = (UniqueConstraint('workspace_id', 'request_id'),)
    request_id: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30), default='queued')


class WMSRead(Row, Scoped, Payload, Base):
    __tablename__ = 'wms_reads'
    __table_args__ = (UniqueConstraint('workspace_id', 'request_id'),)
    connection_id: Mapped[str] = mapped_column(ForeignKey('wms_connections.id'))
    request_id: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30), default='queued')


class SetupUnderstandingPreparation(Row, Scoped, Payload, Base):
    __tablename__ = 'setup_understanding_preparations'
    __table_args__ = (UniqueConstraint('workspace_id', 'actor_id'),)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))


class SetupClarificationDraft(Row, Scoped, Payload, Base):
    __tablename__ = 'setup_clarification_drafts'
    __table_args__ = (UniqueConstraint('workspace_id', 'actor_id', 'analysis_id', 'result_hash'),)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    analysis_id: Mapped[str] = mapped_column(ForeignKey('setup_analyses.id'))
    result_hash: Mapped[str] = mapped_column(String(64))


class SetupClarificationFollowup(Row, Scoped, Payload, Base):
    __tablename__ = 'setup_clarification_followups'
    __table_args__ = (UniqueConstraint('workspace_id', 'request_id'),)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    analysis_id: Mapped[str] = mapped_column(ForeignKey('setup_analyses.id'))
    result_hash: Mapped[str] = mapped_column(String(64))
    draft_id: Mapped[str] = mapped_column(ForeignKey('setup_clarification_drafts.id'))
    request_id: Mapped[str] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(30), default='prepared')
    child_id: Mapped[str | None] = mapped_column(ForeignKey('setup_analyses.id'))


class SetupReportDraft(Row, Scoped, Payload, Base):
    __tablename__ = 'setup_report_drafts'
    __table_args__ = (UniqueConstraint('workspace_id', 'request_id'),)
    actor_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    request_id: Mapped[str] = mapped_column(String(39))
    status: Mapped[str] = mapped_column(String(30), default='queued')
