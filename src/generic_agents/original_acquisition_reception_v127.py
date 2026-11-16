"""Cold original native-acquisition reception; never execution authorization.

The only historical publication exception is the generated *domain package*.
Current source/model/IAM checks remain with ingestion and the real invocation.
Read-only receivers may have a viewer Principal. No role is elevated here.
"""
import copy
import re
from dataclasses import dataclass
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import select

from .contracts import digest
from .models import (Acquisition, Audit, Capability, DataGap, DataRun, DomainInstance,
    Entity, Event, InstanceRelease, Memory, MonitorPolicy, Observation, ObservationPlan, SensorAsset, Task, now)
from .entity_release_compat import EntityReleaseContext, verify_task_binding_snapshot


ERROR = '原补采、业务版本、派发质量或迟到资料关联无法核对；原记录保留，不能继续派生'
MARKER = 'original_acquisition_reception_only'
PROOF_SCHEMA = 'original-acquisition-reception-proof-v127'
HOLD_SCHEMA = 'original-acquisition-reception-only-v127'
_UUID = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'


def _need(value):
    if not value:
        raise HTTPException(409, ERROR)


def _row(db, p, model, identity, lock=False):
    query = select(model).where(model.id == identity, model.workspace_id == p.workspace_id).execution_options(populate_existing=True)
    row = db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise HTTPException(404, '原补采记录不存在或不属于当前工作空间')
    return row


def _time(value):
    _need(type(value) is str)
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    _need(stamp.tzinfo is not None and stamp.utcoffset() is not None)
    return stamp


class _HistoricalDefinition(EntityReleaseContext):
    """Private to an already checked original chain; no public live switch."""
    def _release(self, release, *, live=True):
        # Both __init__ and require/source/parent loading dispatch through here.
        release = _row(self.db, self.p, InstanceRelease, release.id)
        _row(self.db, self.p, Capability, release.package_id)
        return super()._release(release, live=False)


@dataclass(frozen=True)
class OriginalReceptionContext:
    task: Task
    release: InstanceRelease
    instance: DomainInstance
    entity: Entity
    acquisition: Acquisition
    plan: ObservationPlan
    definition: dict
    proof: dict


def _links(db, p, observation):
    """Existing delivery projections/audits cannot lose one restriction side."""
    hashes = set()
    events = db.scalars(select(Event).where(Event.workspace_id == p.workspace_id,
        Event.data['observation_id'].as_string() == observation.id,
        Event.kind.in_(['observation.data', 'observation.failed', 'perception.anomaly', 'perception.candidate']))
        .execution_options(populate_existing=True))
    for event in events:
        audits = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
            Audit.object_id == event.id, Audit.action == 'observation.derivation_skipped',
            Audit.data['original_reception_restriction_hash'].as_string().is_not(None))
            .execution_options(populate_existing=True).limit(2)))
        _need(len(audits) <= 1)
        memories = list(db.scalars(select(Memory).where(Memory.workspace_id == p.workspace_id,
            Memory.data['event_id'].as_string() == event.id,
            Memory.data['type'].as_string().in_(['observation_evidence', 'observation_failure']))
            .execution_options(populate_existing=True).limit(2)))
        projections = [event.data.get('derivation_receipt'), *[row.data.get('derivation_receipt') for row in memories],
            *[row.data for row in audits]]
        linked = [value for value in projections if isinstance(value, dict) and 'original_reception_restriction_hash' in value]
        if not linked: continue
        for value in linked:
            _need(value.get('event_id') == event.id and value.get('observation_id') == observation.id
                and digest({key: item for key, item in value.items() if key != 'receipt_hash'}) == value.get('receipt_hash'))
            if MARKER in observation.data:
                _need(value.get('original_reception_proof') == observation.data[MARKER]['proof'])
            hashes.add(value['original_reception_restriction_hash'])
        if event.status == 'received':
            expected_memories = 1 if event.kind in {'observation.data', 'observation.failed'} else 0
            _need(len(memories) == expected_memories and len(audits) == 1
                and all(value == linked[0] for value in projections))
    _need(len(hashes) <= 1)
    return hashes


def _mapped_dispatch_request(db, p, acq, source, entity, payload):
    """Distinguish an audited domain-to-wire mapping from a legacy request.

    Ingestion calls this BEFORE attaching an Observation/child. Only the
    exchange's immutable request freeze is used here; neither a derived child,
    its completion nor a self-declared marker is substitute authority.
    """
    from . import http_observation_exchange_v177 as ledger
    from . import http_observation_mapping_v177 as protocol
    from .http_observation_exchange_models_v177 import HTTPObservationExchangeV177 as Exchange
    from .services import validate
    key = 'http_observation_exchange_id_v177'
    rows = list(db.scalars(select(Exchange).where(Exchange.workspace_id == p.workspace_id,
        Exchange.acquisition_id == acq.id).execution_options(populate_existing=True)))
    anchors = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == ledger.ACTION, Audit.data['acquisition_id'].as_string() == acq.id)
        .execution_options(populate_existing=True)))
    specification = source.data.get('specification', {})
    traced = (key in acq.data or bool(rows) or bool(anchors)
        or type(specification) is dict and 'http_observation_mapping_v177' in specification)
    if not traced:
        return None
    identity = acq.data.get(key)
    protocol._uuid(identity)
    _need(len(rows) == 1 and rows[0].id == identity and bool(anchors)
        and all(anchor.object_id == identity and anchor.actor_id == acq.data['actor_id'] for anchor in anchors))
    row = ledger.checked_store(db, p, identity, lock=False)
    frozen = row.data['freeze']; mapping = frozen['mapping']; scope = mapping['mapping']['scope']
    _need(row.workspace_id == p.workspace_id and row.acquisition_id == acq.id
        and row.source_id == source.id == acq.capability_id and row.actor_id == acq.data['actor_id']
        and row.environment_id == scope['environment_id'] and row.instance_id == scope['instance_id']
        and row.release_id == scope['release_id'] and scope['workspace_id'] == p.workspace_id
        and row.data['receipt_history'])
    _need(source.kind == 'source' and source.content_hash == source.validated_hash
        == mapping['source_manifest_hash'] == digest(source.data))
    package = _row(db, p, Capability, scope['package_id'])
    _need(package.kind == 'domain_package' and package.content_hash == package.validated_hash
        == scope['package_hash'] == digest(package.data)
        and entity.schema_capability_id == package.id and entity.entity_type == scope['entity_type']
        and entity.namespace == package.namespace and entity.id == frozen['parent_pins']['entity_id'])
    actual = protocol.freeze_mapping(source.data, {**scope, 'source_id': source.id},
        package.data['specification']['entity_schemas'], mapping['mapping']['links'])
    rebuilt = protocol.to_supplier_request(payload, actual)
    _need(protocol.canonical(actual) == protocol.canonical(mapping)
        and protocol.canonical(rebuilt['domain_frozen']) == protocol.canonical(frozen['domain_frozen'])
        and protocol.canonical(rebuilt['wire_frozen']) == protocol.canonical(frozen['wire_frozen'])
        and payload['target_external_id'] == entity.external_id
        and acq.data['request_hash'] == frozen['wire_frozen']['request_hash'])
    from . import device_gateway_sources_v270 as managed_sources
    if managed_sources.managed(db, p, source):
        # This server-issued family declares logical domain inputs. A marker
        # alone cannot select this branch: cold receipt authority rechecks the
        # exact manifest, independent issuance and original recipe scope.
        registered = managed_sources.validate_source(db, p, source, purpose='receipt')
        actual_source = registered['capability']
        _need(actual_source.id == source.id and actual_source.workspace_id == p.workspace_id
            and actual_source.namespace == managed_sources.NAMESPACE
            and actual_source.data['specification']['schema'] == managed_sources.CONTRACT
            and actual_source.content_hash == actual_source.validated_hash == source.content_hash
            and actual_source.data == source.data
            and registered['binding']['mapping'] == mapping['mapping']
            and registered['environment'].id == row.environment_id == scope['environment_id']
            and registered['instance'].id == row.instance_id == scope['instance_id']
            and registered['release'].id == row.release_id == scope['release_id']
            and registered['release'].instance_id == row.instance_id
            and registered['release'].package_id == registered['package'].id == package.id
            and registered['package'].content_hash == package.content_hash == scope['package_hash'])
        # to_supplier_request above independently validates the supplier DTO;
        # both its wire/body/hash and domain request must equal the old freeze.
        validate(actual_source.data['input_schema'], rebuilt['domain_frozen']['request'])
    else:
        # Unmanaged mapped Sources retain their existing raw supplier contract.
        validate(source.data['input_schema'], rebuilt['wire_frozen']['request'])
    for item in row.data['receipt_history']:
        validate(source.data['output_schema'], item['raw'])
    # This proof stays identical before/after attachment. Exchange version,
    # status and Observation pointers are mutable and deliberately excluded.
    return {'schema': 'http-mapped-request-proof-v177', 'exchange_id': row.id,
        'mapping_freeze_hash': mapping['freeze_hash'],
        'domain_request_hash': frozen['domain_frozen']['request_hash'],
        'wire_request_hash': frozen['wire_frozen']['request_hash'],
        'wire_body_sha256': frozen['wire_frozen']['body_sha256']}


def _dispatch(db, p, acq, plan, origin, entity):
    policy = _row(db, p, MonitorPolicy, plan.policy_id)
    raw = {key: value for key, value in policy.data.items() if key not in {'approved_hash', 'approval_reason'}}
    _need(policy.data['approved_hash'] == digest(raw) == plan.data['policy_hash'] == acq.data['policy_hash']
        and policy.approved_by == acq.data['actor_id'] == plan.data['actor_id']
        and entity.id in policy.data['entity_ids'] and acq.capability_id in policy.data['source_ids'])
    # Rebuild the original quality intersection without calling the live guard,
    # which intentionally refuses cancelled parents and new device dispatch.
    fields = origin['fields']; requirement = copy.deepcopy(origin['request']['evidence_requirement'])
    _need(fields and set(fields).issubset(policy.data['fields']) and requirement['source'] != 'archive'
        and requirement['time']['mode'] in {'archive', 'latest'})
    age = min(policy.data['max_age_seconds'], requirement['time'].get('max_age_seconds') or policy.data['max_age_seconds'])
    verification = ('confirmed' if requirement['minimum_verification'] != 'unverified_extraction'
        or policy.data['selection_requirement']['minimum_verification'] == 'confirmed' else 'unverified_extraction')
    requirement['source'] = 'observations'
    requirement['time'] = {'mode': 'latest', 'timezone': requirement['time'].get('timezone') or policy.data.get('timezone', 'UTC'), 'max_age_seconds': age}
    requirement['minimum_verification'] = verification
    expected = {'fields': fields, 'max_age_seconds': age,
        'selection_requirement': {**policy.data['selection_requirement'], 'minimum_verification': verification},
        'observation_evidence_requirement': requirement}
    from .observation_requests import frozen
    frozen_policy = frozen(policy, acq)
    _need(acq.data['dispatch_observation_constraints'] == expected
        and {key: frozen_policy.data[key] for key in expected} == expected
        and acq.data['selected_fields'] and set(acq.data['selected_fields']).issubset(fields))
    asset = _row(db, p, SensorAsset, acq.data['sensor_asset_id'])
    source = _row(db, p, Capability, acq.capability_id)
    _need(source.kind == 'source' and digest(source.data) == source.content_hash == acq.data['source_hash']
        and asset.source_id == source.id and entity.id in acq.data['dispatch_asset']['coverage_entity_ids']
        and set(acq.data['selected_fields']).issubset(acq.data['dispatch_asset']['fields'])
        and acq.data['selected_action'] in acq.data['dispatch_asset']['actions']
        and acq.data['dispatch_execution'] == source.data['execution'])
    choice = acq.data['selection']
    _need(all(choice[key] == acq.data[saved] for key, saved in (
        ('asset_id', 'sensor_asset_id'), ('asset_version', 'sensor_asset_version'),
        ('source_hash', 'source_hash'), ('device_id', 'sensor_device_id'),
        ('device_version', 'sensor_device_version'), ('binding_id', 'device_binding_id'),
        ('binding_version', 'device_binding_version')))
        and choice['source_id'] == source.id and choice['action'] == acq.data['selected_action']
        and choice['fields'] == acq.data['selected_fields'])
    payload = {'request_key': acq.idempotency_key, 'target_external_id': entity.external_id,
        'fields': [row['field'] for row in acq.data['gaps']], 'max_duration_seconds': policy.data['max_duration_seconds'],
        'asset_external_id': asset.external_id, 'action': acq.data['selected_action'],
        'max_cost_minor': acq.data['reserved_cost_minor'], 'currency': acq.data['currency']}
    _need(payload['fields'] == acq.data['selected_fields'])
    mapped = _mapped_dispatch_request(db, p, acq, source, entity, payload)
    if mapped is None:
        _need(digest(payload) == acq.data['request_hash'])
    dispatched_at = _time(acq.data['dispatch_started_at'])
    _need(db.scalar(select(Audit.id).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == acq.id, Audit.action == 'acquisition.dispatch', Audit.actor_id == policy.approved_by).limit(1)))
    return {'source_hash': source.content_hash, 'policy_hash': acq.data['policy_hash'],
        'request_hash': acq.data['request_hash'], 'dispatched_at': dispatched_at.isoformat(),
        'constraints': expected, 'asset': acq.data['dispatch_asset'], 'selection': choice,
        'execution': acq.data['dispatch_execution'], **({'http_mapping': mapped} if mapped is not None else {})}


def _context(db, p, observation, *, lock=False):
    observation = _row(db, p, Observation, observation.id, lock=lock)
    issued = list(db.scalars(select(Audit.id).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == observation.id, Audit.action == 'observation.original_reception_only').limit(2)))
    links = _links(db, p, observation)
    _need(len(issued) <= 1 and (MARKER in observation.data) == bool(issued)
        and (not links or MARKER in observation.data and links == {observation.data[MARKER]['restriction_hash']}))
    acquisition_id = observation.data.get('acquisition_id')
    original = observation.data.get('original_request')
    if not acquisition_id:
        _need(not observation.data.get(MARKER) and not (isinstance(original, dict) and original.get('acquisition_id')))
        return None
    acq = _row(db, p, Acquisition, acquisition_id)
    plan_id = acq.data.get('observation_plan_id')
    if not plan_id:
        _need(MARKER not in observation.data and not acq.data.get('data_gap_id'))
        return None
    plan = _row(db, p, ObservationPlan, plan_id)
    origin = plan.data.get('observation_request')
    _need(origin is None or type(origin) is dict)
    # The reverse actual gap/Task links remain evidence if a forward native
    # marker is removed. Neither side may silently select the legacy path.
    gaps = list(db.scalars(select(DataGap).where(DataGap.workspace_id == p.workspace_id,
        DataGap.data['acquisition_ids'].contains([acq.id])).execution_options(populate_existing=True).limit(2)))
    direct = acq.data.get('data_gap_id')
    for identifier in {direct, origin.get('gap_id') if origin else None} - {None}:
        gap = _row(db, p, DataGap, identifier)
        if gap.id not in {item.id for item in gaps}: gaps.append(gap)
    _need(len(gaps) <= 1)
    run = _row(db, p, DataRun, gaps[0].query_id) if gaps else None
    parents = list(db.scalars(select(Task).where(Task.workspace_id == p.workspace_id,
        Task.data['native_goal_query_id'].as_string() == run.id).execution_options(populate_existing=True).limit(2))) if run else []
    native = bool(parents or run and any(key in run.data for key in
        ('native_goal_task_id', 'native_goal_context_hash', 'native_goal_request_hash')))
    delegation = (origin or {}).get('goal_delegation') or (run.data.get('goal_delegation') if run else None)
    if delegation is not None:
        _need(type(delegation) is dict and type(delegation.get('task_id')) is str)
        declared = _row(db, p, Task, delegation['task_id'])
        native = native or any(key in declared.data for key in ('native_goal_context', 'native_goal_context_hash'))
    if not native:
        _need(MARKER not in observation.data and not (origin is None and 'dispatch_observation_constraints' in acq.data))
        if origin is not None:
            run = _row(db, p, DataRun, origin['query_id'])
            _need('goal_delegation' in origin)
        return None  # Generic queries lack this version-bound native Task chain.
    _need(type(origin) is dict and type(origin.get('goal_delegation')) is dict
        and len(gaps) == len(parents) == 1 and direct == gaps[0].id == origin['gap_id']
        and origin['query_id'] == run.id and parents[0].id == origin['goal_delegation']['task_id'])
    task = _row(db, p, Task, origin['goal_delegation']['task_id'])
    query = _row(db, p, DataRun, task.data['native_goal_query_id'])
    gap = _row(db, p, DataGap, origin['gap_id'])
    from .native_goal_acquisitions import acquisition
    actual_acq, actual_plan = acquisition(db, p, task, acq.id)
    _need(actual_acq is acq and actual_plan is plan and query.id == gap.query_id)
    from .native_goals import saved_context
    saved = saved_context(db, p, task)
    release = _row(db, p, InstanceRelease, saved['release_id'])
    instance = _row(db, p, DomainInstance, saved['instance_id'])
    package = _row(db, p, Capability, release.package_id)
    entity = _row(db, p, Entity, observation.entity_id)
    _need(isinstance(original, dict) and original.get('acquisition_id') == acq.id
        and original.get('entity_id') == entity.id == acq.entity_id
        and original.get('source_id') == observation.source_id == acq.capability_id
        and original.get('extractor_id') == observation.data['extractor_id']
        and original.get('attributes') == observation.data.get('attributes')
        and original.get('evidence_ids', []) == observation.data.get('evidence_ids', [])
        and all(original[key] == getattr(observation, key) for key in ('source_record_id', 'processing_version', 'business_day'))
        and digest(original) == observation.data['input_hash']
        and _time(original['observed_at']) == observation.observed_at)
    # Full actual mirrors, lineage, namespace, type and schema still apply.
    historical = _HistoricalDefinition(db, p, instance, release, lock_sources=False)
    definition = historical.require(entity)
    binding = verify_task_binding_snapshot(task, release)
    if binding is not None:
        fixed = next(row for row in binding['objects'] if row['entity_id'] == entity.id)
        _need(definition == fixed['definition'])
    else:
        _need(entity.schema_capability_id == task.package_id == release.package_id)
    dispatch = _dispatch(db, p, acq, plan, origin, entity)
    _need(observation.observed_at >= _time(acq.data['dispatch_started_at']))
    extractor = _row(db, p, Capability, observation.data['extractor_id'])
    _need(extractor.content_hash == digest(extractor.data))
    if observation.status == 'processed':
        from .observation_integrity import checked_report
        checked_report(db, p, observation)
    proof = {'schema': PROOF_SCHEMA, 'workspace_id': p.workspace_id, 'observation_id': observation.id,
        'original_input_hash': observation.data['input_hash'], 'task_id': task.id,
        'goal_context_hash': task.data['native_goal_context_hash'], 'goal_snapshot_hash': digest(saved['goal_snapshot']),
        'query_id': query.id, 'query_request_hash': query.data['native_goal_request_hash'], 'gap_id': gap.id,
        'plan_id': plan.id, 'acquisition_id': acq.id, 'dispatch_hash': digest(dispatch),
        'instance_id': instance.id, 'release_id': release.id, 'configuration_hash': release.configuration_hash,
        'package_id': package.id, 'package_hash': package.content_hash,
        'entity_id': entity.id, 'definition_hash': digest(definition),
        'source_id': observation.source_id, 'source_hash': dispatch['source_hash'],
        'extractor_id': extractor.id, 'extractor_hash': extractor.content_hash,
        'physical_state_verified': False, 'execution_granted': False}
    proof['proof_hash'] = digest(proof)
    return OriginalReceptionContext(task, release, instance, entity, acq, plan, definition, proof)


def context(db, p, observation, *, lock=False):
    """Cold rebuild or None for a non-native acquisition; malformed chains fail.

    Explicit writers may lock Observation; this proof never takes Gap/Task
    origin locks and never authorizes a new dispatch.
    """
    try:
        value = _context(db, p, observation, lock=lock)
        if value is not None and MARKER in observation.data:
            _restriction(db, p, observation, value)
        return value
    except HTTPException:
        raise
    except (ValueError, KeyError, TypeError, AttributeError, StopIteration, OSError, RecursionError):
        raise HTTPException(409, ERROR) from None


def _restriction(db, p, observation, ctx):
    value = observation.data[MARKER]
    _need(type(value) is dict and set(value) == {'schema', 'proof', 'code', 'reason', 'issued_by', 'issued_at', 'restriction_hash'}
        and value['schema'] == HOLD_SCHEMA and value['proof'] == ctx.proof
        and value['code'] in {'package_withdrawn', 'instance_not_active', 'release_replaced', 'business_disabled'}
        and type(value['reason']) is str and bool(value['reason'])
        and digest({key: item for key, item in value.items() if key != 'restriction_hash'}) == value['restriction_hash'])
    _time(value['issued_at'])
    _need(db.scalar(select(Audit.id).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == observation.id, Audit.action == 'observation.original_reception_only',
        Audit.actor_id == value['issued_by'], Audit.data['restriction_hash'].as_string() == value['restriction_hash']).limit(1)))
    return copy.deepcopy(value)


def _hold(db, p, observation, *, remember=False, lock=False):
    """Return a reducing-only restriction. remember persists it in the caller's transaction.

    Use remember=True at ingestion/processing/receiver writes. Historical reads
    never issue restrictions or require a source/model to remain published.
    Re-enabling a business never upgrades an already restricted observation.
    """
    if remember:
        # Receiver and human verification can meet after processing commits.
        # Serialize the reducing marker with the actual human record, refresh
        # it after the lock, then cold-check both ledger sides before issuing.
        observation = _row(db, p, Observation, observation.id, lock=True)
    ctx = context(db, p, observation, lock=lock)
    if ctx is None:
        return None
    if MARKER in observation.data:
        return _restriction(db, p, observation, ctx)
    package = _row(db, p, Capability, ctx.release.package_id)
    if package.status != 'published':
        code, reason = 'package_withdrawn', '原业务包已停止发布；只保存原补采资料与核查，不恢复业务、目标或设备动作'
    elif ctx.instance.status != 'partially_active' or not ctx.instance.active_release_id:
        code, reason = 'instance_not_active', '原业务实例未启用或已暂停；只接收原补采资料，不安排新的业务工作'
    elif ctx.instance.active_release_id != ctx.release.id:
        code, reason = 'release_replaced', '实例已采用其他版本；迟到资料仍固定在原补采版本，不迁移旧授权'
    elif ctx.task.data['business_id'] not in ctx.instance.data.get('enabled_business_ids', []):
        code, reason = 'business_disabled', '原业务已停用；只保存原补采资料与核查，不恢复原目标'
    else:
        return None  # Stopping one Task does not disable independent monitoring.
    value = {'schema': HOLD_SCHEMA, 'proof': copy.deepcopy(ctx.proof), 'code': code, 'reason': reason,
        'issued_by': p.user_id, 'issued_at': now().isoformat()}
    value['restriction_hash'] = digest(value)
    if remember:
        observation.data = {**observation.data, MARKER: copy.deepcopy(value)}
        from .services import audit
        audit(db, p, 'observation.original_reception_only', observation.id, {'restriction_hash': value['restriction_hash']})
    return value


def hold(db, p, observation, *, remember=False, lock=False):
    try:
        return _hold(db, p, observation, remember=remember, lock=lock)
    except HTTPException:
        raise
    except (ValueError, KeyError, TypeError, AttributeError, StopIteration, OSError, RecursionError):
        raise HTTPException(409, ERROR) from None


def _trigger_hold(db, p, trigger, *, entity_id=None, package_id=None):
    """Only actual observation/review callbacks can suppress their derivatives."""
    match = re.fullmatch(r'observation-review:(' + _UUID + r'):([1-9][0-9]*)', trigger)
    if not match:
        return None
    observation = _row(db, p, Observation, match[1])
    _need(observation.status == 'processed' and observation.version == int(match[2]))
    entity = _row(db, p, Entity, observation.entity_id)
    _need((entity_id is None or entity.id == entity_id)
        and (package_id is None or entity.schema_capability_id == package_id))
    original = observation.data.get('original_request', {})
    _need(original.get('acquisition_id') == observation.data.get('acquisition_id'))
    review, history = observation.data.get('human_verification'), observation.data.get('verification_history')
    fingerprint = digest({'extracted': observation.data['extracted'], 'evidence_ids': observation.data.get('evidence_ids', []),
        'invocation_id': observation.data['invocation_id']})
    _need(type(review) is dict and type(history) is list and history and history[-1] == review
        and digest(history) == observation.data.get('verification_history_hash')
        and review.get('input_hash') == fingerprint and review.get('decision') in {'confirmed', 'rejected'})
    if review.get('request_id'):
        _need(review.get('body_hash') == digest({'expected_version': observation.version - 1,
            'reason': review['reason'], 'decision': review['decision'], 'request_id': review['request_id'],
            'expected_input_hash': fingerprint}))
    _time(review['reviewed_at'])
    _need(db.scalar(select(Audit.id).where(Audit.workspace_id == p.workspace_id,
        Audit.object_id == observation.id, Audit.action == 'observation.verified', Audit.actor_id == review['reviewed_by'],
        Audit.data['decision'].as_string() == review['decision'], Audit.data['reason'].as_string() == review['reason']).limit(1)))
    return hold(db, p, observation, remember=True)


def trigger_hold(db, p, trigger, *, entity_id=None, package_id=None):
    try:
        return _trigger_hold(db, p, trigger, entity_id=entity_id, package_id=package_id)
    except HTTPException:
        raise
    except (ValueError, KeyError, TypeError, AttributeError, StopIteration, OSError, RecursionError):
        raise HTTPException(409, ERROR) from None
