from functools import lru_cache
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
from sqlalchemy.exc import StatementError
from fastapi import HTTPException
from .typed_payload_codec_v131 import TypedPayloadError, ERROR
from .config import settings
from .models import Base


@lru_cache
def engine():
    configured = settings()
    options = {'pool_pre_ping': True, 'hide_parameters': True}
    mode = getattr(configured, 'database_pool_mode', 'pooled')
    if type(mode) is not str or mode not in {'pooled', 'no_idle'}:
        raise ValueError("Database pool mode must be exactly 'pooled' or 'no_idle'")
    if mode == 'no_idle':
        options['poolclass'] = NullPool
    elif make_url(configured.database_url).get_backend_name() == 'postgresql':
        options.update(pool_size=configured.database_pool_size,
            max_overflow=configured.database_max_overflow,
            pool_timeout=configured.database_pool_timeout)
    return create_engine(configured.database_url, **options)


def session():
    with sessionmaker(engine(), expire_on_commit=False)() as db:
        try:
            yield db
        except (TypedPayloadError, StatementError) as exc:
            if isinstance(exc, TypedPayloadError) or isinstance(exc.orig, TypedPayloadError):
                db.rollback()
                raise HTTPException(409, ERROR) from None
            raise


def begin_private_read_snapshot(db):
    """Start or reuse this transaction's private, consistent read snapshot.

    A global read guard may need to authenticate before the endpoint-specific
    dependency runs. Both must use the same snapshot, and neither may attempt
    to change transaction mode after its first SELECT.
    """
    transaction = db.get_transaction()
    if transaction is not None and db.info.get('private_read_snapshot') is transaction:
        return
    db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
    db.info['private_read_snapshot'] = db.get_transaction()


def migrate():
    """Initial non-destructive schema install; future changes require explicit migration."""
    # Register the additive 104th table without importing API/worker modules.
    from .report_configuration_models import ReportConfigurationRecord
    from .signal_dual_models import SignalDualHandoff
    from .signal_graph_routing_models_v134 import SignalGraphDelivery
    from .signal_approval_models_v247 import SignalApprovalRouteV247,SignalApprovalOccurrenceV247,SignalApprovalCommandV247
    from .report_quote_binding_models import QuoteBindingDraft
    from .report_source_requirement_models import SetupRequirementCheck
    from .report_full_semantic_source_review_models import FullSemanticSourceReview
    from .report_full_semantic_region_models import FullSemanticRegionSession, FullSemanticRegionEntry, FullSemanticRegionProof
    from .report_full_semantic_schema_models import FullSemanticSchemaAdoption
    from .report_full_semantic_runtime_models import FullSemanticRuntimeAdoption
    from .report_full_semantic_graph_models import FullSemanticGraphAdoption
    from .report_full_semantic_graph_revision_models import FullSemanticGraphRevision
    from .report_full_semantic_runtime_revision_models import FullSemanticRuntimeRevision
    from .graph_event_models import GraphEventProducer, GraphEventStream, GraphEventIntent, GraphEventAdmission
    from .task_creation_recovery_v120 import TaskCreationRetirement
    from .graph_event_revocation_v122 import GraphEventProducerRevocation
    from .graph_event_request_recovery_v123 import GraphEventSourceRetirement
    from .graph_event_delivery_recovery_v124 import GraphEventIntentRetryReceipt
    from .graph_event_delivery_retirement_v125 import GraphEventIntentRetryRetirement
    from .graph_event_native_producer_requests_v136 import GraphEventNativeProducerCommand
    from .threshold_evolution_models_v147 import FeedbackRevision, ThresholdCommand
    from .threshold_request_recovery_models_v148 import ThresholdRequestRetirement
    from .threshold_study_models_v149 import ThresholdStudyCandidate, ThresholdStudyEvaluation, ThresholdStudyCommand
    from .threshold_shadow_models_v151 import ThresholdShadowGrant, ThresholdShadowCommand, ThresholdShadowConsumption
    from .quality_review_models_v152 import ThresholdQualityReview, ThresholdQualityCommand
    from .threshold_policy_models_v152 import ThresholdPolicyGrant, ThresholdPolicyCommand, ThresholdPolicySelection
    from .saved_imagery_models_v163 import SavedImageryReassessment, SavedImageryCommand, SavedImageryAudit
    from .native_video_review_models_v165 import NativeVideoReview, NativeVideoReviewCommand
    from .report_object_revision_models_v166 import ReportObjectRevision, ReportObjectRevisionCommand
    from . import layered_approval_models_v175
    from . import simulation_models_v176
    from . import simulation_evolution_models_v176
    from . import report_saved_map_design_models_v176
    from . import http_observation_exchange_models_v177
    from . import report_saved_map_review_models_v178
    from . import report_scoped_design_models_v181
    from . import business_flow_review_models
    from . import business_flow_qualification_models
    from . import business_flow_execution_models
    from . import business_flow_execution_draft_models
    from . import business_flow_nested_configuration_models
    from . import workflow_task_dispatch_models_v198
    from . import workflow_task_archive_models_v198
    from . import workflow_task_packet_models_v199
    from . import workflow_goal_evaluation_models_v288
    from . import auto_evaluation_policy_v356
    from . import auto_evaluation_trigger_v356
    from . import auto_evaluation_ack_witness_v356
    from . import auto_evaluation_scanner_v356
    from . import native_goal_fair_state_v358
    from . import mqtt_unit_revision_v357
    from . import mqtt_unit_quality_retry_v357
    from . import goal_evaluation_append_models_v297
    from . import workflow_monitor_schedule_models_v200
    from . import workflow_registered_tool_plan_models_v201
    from . import business_data_models
    from . import runtime_document_models
    from . import runtime_document_ocr_models
    from . import runtime_task_document_models_v207
    from . import authored_workflow_models_v208
    from . import layered_case_models_v209
    from . import layered_case_use_models_v210
    from . import report_business_catalogue_models_v211
    from . import media_daily_task_models
    from . import business_automation_models
    from . import monitor_scheduling_models_v253
    from . import sound_models
    from . import audio_stream_models
    from . import pixel_annotation_models_v256
    from . import pixel_diagnostic_models_v257
    from . import goal_conflict_observation_models_v259
    from . import catalogue_current_context_models_v260
    from . import local_proposal_review_models_v261
    from . import local_proposal_review_submission_models_v261
    from . import device_gateway_models_v268
    from . import device_gateway_tool_models_v269
    # The recovery Control table is a separately controlled future migration.
    Base.metadata.create_all(engine(), tables=[table for table in Base.metadata.tables.values()
        if not (table.schema is None and table.name == 'auto_evaluation_recovery_control_v360')])
    from .mqtt_unit_index_v342 import ensure as ensure_mqtt_unit_index
    ensure_mqtt_unit_index(engine())
    from .typed_payload_migration_v131 import migrate_typed_payloads
    migrate_typed_payloads(engine(), Base.metadata)
    # Cloud API 2 stores independent authorization epochs; this is additive
    # and intentionally does not alter other gateways' permission semantics.
    from .dji_cloud2_auth_epoch import migrate as migrate_dji_cloud2_auth_epoch
    migrate_dji_cloud2_auth_epoch(engine())
    # v0.6 unknown source clocks: keep NULL, not a manufactured reception timestamp.
    # Existing ingestion endpoints still require a real timestamp/business day.
    # Idempotent, lossless expansion; rollback requires resolving new unclocked rows.
    with engine().begin() as connection:
        connection.execute(text('ALTER TABLE observations ALTER COLUMN observed_at DROP NOT NULL'))
        connection.execute(text('ALTER TABLE observations ALTER COLUMN business_day DROP NOT NULL'))
