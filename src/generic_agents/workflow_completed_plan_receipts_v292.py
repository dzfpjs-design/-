"""Cold proof of a V288 plan's completed collection, never current freshness.

Only the original successful ACK-settlement Job and independent audits can
establish a completion cut. Mutable plan/result hashes alone grant nothing.
No evaluator, clock override, device operation, row update or retry is used.
"""
import copy
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import or_, select

from .contracts import EvidenceRequirement, digest
from .models import Acquisition, Audit, Event, Job, Observation
from .services import scoped
from .temporal import instant, matches, resolve

SCHEMA = 'completed-observation-plan-proof-v292'
SETTLED = 'acquisition.data_ack_settled_v245'
MAX_AUDITS = 1000


def need(value):
    if not value:
        raise HTTPException(409, '原计划历史完成、独立结案或收件证明无法核对；未刷新目标时效或重新采集')


def _time(value):
    need(isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None)
    return value


def _audits(db, p, action, identity):
    rows = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
        Audit.action == action, Audit.object_id == identity).limit(MAX_AUDITS + 1)
        .execution_options(populate_existing=True)))
    need(len(rows) <= MAX_AUDITS and len({row.id for row in rows}) == len(rows)
        and all(row.workspace_id == p.workspace_id and row.action == action
            and row.object_id == identity for row in rows))
    return rows


def _family_inventory(db, p, children):
    from . import acquisition_ack_settlement_v245 as settlement
    inventory = {}
    for child in children:
        closing = child.data.get('closing_observation_id')
        job_filters = [Job.idempotency_key.startswith('acquisition-data-ack:' + child.id + ':', autoescape=True),
            (Job.kind == settlement.KIND) & (Job.data['settlement']['acquisition_id'].as_string() == child.id)]
        jobs = list(db.scalars(select(Job).where(Job.workspace_id == p.workspace_id,
            or_(*job_filters))
            .limit(MAX_AUDITS + 1).execution_options(populate_existing=True)))
        need(len(jobs) <= MAX_AUDITS)
        audit_filters = [Audit.data['settlement']['acquisition_id'].as_string() == child.id,
            Audit.object_id.in_([job.id for job in jobs])]
        if type(closing) is str:
            audit_filters.append(Audit.data['settlement']['observation_id'].as_string() == closing)
        queued_rows = list(db.scalars(select(Audit).where(Audit.workspace_id == p.workspace_id,
            Audit.action == settlement.ACTION, or_(*audit_filters))
            .limit(MAX_AUDITS + 1).execution_options(populate_existing=True)))
        settled_rows = _audits(db, p, SETTLED, child.id)
        need(len(queued_rows) <= MAX_AUDITS)
        if not jobs and not queued_rows and not settled_rows:
            inventory[child.id] = None
            continue
        # Read all three independent directions. Missing a settled audit must
        # never disguise an existing V245 Job/queued witness as legacy input.
        need(len(jobs) == len(queued_rows) == len(settled_rows) == 1
            and queued_rows[0].workspace_id == p.workspace_id
            and queued_rows[0].action == settlement.ACTION
            and queued_rows[0].object_id == jobs[0].id)
        inventory[child.id] = (jobs[0], queued_rows[0], settled_rows[0])
    return inventory


def _witness(db, p, context, children, observations, inventory):
    from . import acquisition_ack_settlement_v245 as settlement
    candidates = []
    for child in children:
        family = inventory[child.id]
        if family is None:
            continue
        original_job, original_queued, original_settled = family
        key = 'acquisition-data-ack:' + child.id + ':' + child.data['closing_observation_id']
        for event in [original_settled]:
            body = {key: value for key, value in event.data.items() if key != 'job_id'}
            need(set(body) == {'schema', 'acquisition_id', 'observation_id', 'event_id', 'status',
                'plan_status', 'cloud_calls', 'device_actions', 'new_acquisitions'}
                and body['schema'] == settlement.SCHEMA and body['status'] == 'succeeded'
                and body['plan_status'] in {'completed', 'awaiting_observation', 'awaiting_collection',
                    'authorized', 'dispatching', 'queued', 'escalated', 'cancelled'}
                and body['acquisition_id'] == child.id
                and body['observation_id'] == child.data['closing_observation_id']
                and all(type(body[name]) is int and body[name] == 0
                    for name in ('cloud_calls', 'device_actions', 'new_acquisitions')))
            job = scoped(db, Job, event.data['job_id'], p)
            pin = job.data['settlement']
            need(job.id == original_job.id and job.workspace_id == p.workspace_id
                and job.status == 'succeeded' and job.kind == settlement.KIND
                and job.idempotency_key == key
                and pin['schema'] == settlement.SCHEMA and pin['workspace_id'] == p.workspace_id
                and pin['instance_id'] == context['task'].data['instance_id']
                and pin['acquisition_id'] == child.id and pin['observation_id'] == body['observation_id']
                and pin['event_id'] == body['event_id'] and pin['actor_id'] == child.data['actor_id']
                == job.data['actor_id'] == event.actor_id
                and job.data['settlement_hash'] == digest(pin)
                and digest(job.data['settlement_result']) == job.data['settlement_result_hash'] == digest(body))
            queued = _audits(db, p, settlement.ACTION, job.id)
            need(len(queued) == 1 and queued[0].actor_id == pin['actor_id'] and queued[0].data == {
                'job_id': job.id, 'settlement': pin, 'settlement_hash': digest(pin)}
                and queued[0].id == original_queued.id
                and digest(queued[0].data) == digest({'job_id': job.id,
                    'settlement': pin, 'settlement_hash': digest(pin)})
                and _time(queued[0].created_at) <= _time(event.created_at))
            observation = observations[body['observation_id']]
            received = scoped(db, Event, body['event_id'], p)
            # Reuse the actual original mapped input/ACK/quality-constraint
            # proof; this SELECT-only reader never runs sensing.evidence.
            need(digest(settlement.checked_input(db, p, received, observation)) == digest(pin)
                and _time(received.received_at) <= event.created_at)
            if body['plan_status'] == 'completed':
                candidates.append((event, job, pin, queued[0]))
    if not candidates:
        return None
    # Multiple completed witnesses must be distinguishable actual original
    # Jobs, not duplicate audit rows. The first completion is authoritative.
    need(len({item[0].id for item in candidates}) == len(candidates)
        and len({item[1].id for item in candidates}) == len(candidates))
    candidates.sort(key=lambda item: (item[0].created_at, item[0].id))
    return candidates[0]


def _confirmed(db, p, observation, cut):
    """Narrow normal single-review proof, independent of Goal query clocks.

    A multi-review history needs its own historical reader; do not silently
    select an older confirmation or treat a late confirmation as prior fact.
    """
    from .reported_form import verified_history
    history = verified_history(observation.data)
    review = observation.data.get('human_verification')
    fingerprint = digest({'extracted': observation.data['extracted'],
        'evidence_ids': observation.data.get('evidence_ids', []),
        'invocation_id': observation.data['invocation_id']})
    need(type(review) is dict and history == [review]
        and observation.data.get('verification_history_hash') == digest(history)
        and review.get('decision') == 'confirmed' and review.get('input_hash') == fingerprint)
    if review.get('request_id'):
        need(review.get('body_hash') == digest({'expected_version': observation.version - 1,
            'reason': review['reason'], 'decision': 'confirmed', 'request_id': review['request_id'],
            'expected_input_hash': fingerprint}))
    audits = _audits(db, p, 'observation.verified', observation.id)
    need(len(audits) == 1 and audits[0].actor_id == review['reviewed_by']
        and audits[0].data == {'decision': 'confirmed', 'reason': review['reason']}
        and _time(observation.created_at) <= instant(review['reviewed_at'])
        <= _time(audits[0].created_at) <= cut)


def completed_plan(db, p, context, policy, request, state, plan, *, max_acquisitions):
    """Return a witnessed historical result, or None for the unchanged old path."""
    from . import workflow_service_v195 as service
    from . import mapped_acquisition_origin_v177 as mapped
    from .observation_integrity import checked_report
    from .instance_data_receipts import observation_receipt
    need(plan.status == 'completed' and plan.policy_id == policy.id
        and plan.entity_id == request['entity_id'] and plan.data['policy_hash'] == policy.data['approved_hash']
        and plan.data['observation_request'] == request)
    children = list(db.scalars(select(Acquisition).where(Acquisition.workspace_id == p.workspace_id,
        Acquisition.data['observation_plan_id'].as_string() == plan.id).order_by(Acquisition.id)
        .limit(max_acquisitions + 1).execution_options(populate_existing=True)))
    need(0 < len(children) <= max_acquisitions and len({child.id for child in children}) == len(children))
    inventory = _family_inventory(db, p, children)
    # Genuine absence is determined before mapped-origin, closure or quality
    # requirements of this new historical subset. Legacy/current behavior is
    # unchanged, while an orphaned V245 family already failed closed above.
    if not any(value is not None for value in inventory.values()):
        return None
    # This narrow completed subset does not reinterpret rejected/unknown or
    # still-active alternative actions as closed successful collection.
    if any(child.status != 'succeeded' for child in children):
        raise service.PendingService('原历史计划仍含未成功闭合的请求；保留原号，未重新采集')
    observations, close_proofs, raw = {}, [], {}
    for child in children:
        service._acquisition_pin(db, p, child, policy, request)
        observation = scoped(db, Observation, child.data['closing_observation_id'], p)
        need(observation.id not in observations and observation.status == 'processed'
            and observation.entity_id == child.entity_id == plan.entity_id
            and observation.source_id == child.capability_id and observation.data['acquisition_id'] == child.id)
        closes = _audits(db, p, 'acquisition.close', child.id)
        need(len(closes) == 1 and closes[0].actor_id == child.data['actor_id'] and closes[0].data == {
            'observation_id': observation.id, 'verified_collection': True})
        mapped_proof = mapped.guard(db, p, observation, execution=False, result=observation.data['extracted'])
        need(type(mapped_proof) is dict and mapped_proof['acquisition_id'] == child.id
            and mapped_proof['instance_id'] == context['task'].data['instance_id']
            and mapped_proof['release_id'] == context['release'].id)
        received_hash = service._observation_receipt_hash(db, p, context, observation, state)
        reception = observation_receipt(db, p, observation)['data_reception']
        event = scoped(db, Event, reception['event_id'], p)
        need(event.status == 'received' and _time(event.received_at) <= _time(closes[0].created_at)
            and _time(observation.created_at) <= closes[0].created_at
            and _time(observation.observed_at) <= closes[0].created_at)
        report = checked_report(db, p, observation)
        observations[observation.id] = observation; close_proofs.append(closes[0])
        raw[observation.id] = {'observation': observation, 'report': report,
            'receipt_hash': received_hash, 'receipt': reception, 'mapped': mapped_proof,
            'child': child, 'close': closes[0]}
    witnessed = _witness(db, p, context, children, observations, inventory)
    if witnessed is None:
        return None
    event, job, pin, queued = witnessed
    cut = _time(event.created_at)
    need(all(_time(close.created_at) <= cut for close in close_proofs))
    result = copy.deepcopy(plan.data['result'])
    requirement = EvidenceRequirement.model_validate(policy.data['observation_evidence_requirement']).model_dump()
    need(result['status'] == 'sufficient' and not result.get('gaps') and not result.get('conflicts')
        and result['evidence_requirement'] == requirement and requirement['source'] == 'observations'
        and instant(result['evaluated_at']) <= cut)
    # A historical collection proof is checked at its independent completion
    # witness, not by rewinding any Goal query or by accepting its own timestamp.
    window = resolve(requirement['time'], reference=cut)
    used = result['observation_evidence']; covered = {field: 0 for field in request['fields']}; values = {}
    need(type(used) is list and 0 < len(used) <= max_acquisitions
        and len({item['observation_id'] for item in used}) == len(used)
        and {item['observation_id'] for item in used} == set(observations))
    selected = []
    for item in used:
        original = raw[item['observation_id']]; observation = original['observation']
        attrs = {field: value for field, value in original['report']['attributes'].items()
            if field in request['fields']}
        need(item['entity_id'] == request['entity_id'] and item['source_id'] == observation.source_id
            and item['version'] == observation.version and item['attributes'] == attrs
            and item['observed_at'] == observation.observed_at.isoformat()
            and item['invocation_id'] == observation.data['invocation_id']
            and item['evidence_ids'] == observation.data.get('evidence_ids', [])
            and item['anomalies'] == observation.data['extracted'].get('anomalies', [])
            and item['source'] == 'processed_observation'
            and item['data_reception'] == original['receipt']
            and matches(observation.observed_at, window, evaluated_at=cut))
        need(original['report']['review'] != 'rejected'
            and item['verification'] == ('confirmed' if original['report']['review'] == 'confirmed'
                else 'unverified_extraction'))
        if requirement['minimum_verification'] == 'confirmed':
            need(original['report']['review'] == 'confirmed')
        if original['report']['review'] == 'confirmed':
            _confirmed(db, p, observation, cut)
        req = policy.data['selection_requirement']; sensor = observation.data['sensor_evidence']
        need(item['sensor_evidence'] == sensor and sensor['modality'] in req['allowed_modalities']
            and (req['max_resolution_m'] is None or sensor.get('resolution_m') is not None
                and sensor['resolution_m'] <= req['max_resolution_m']))
        for field, value in attrs.items():
            if value is not None:
                covered[field] += 1
                values.setdefault((field, observation.observed_at.isoformat()), set()).add(digest(value))
        selected.append({'observation_id': observation.id, 'input_hash': observation.data['input_hash'],
            'mapped_proof_hash': digest(original['mapped']), 'data_receipt_hash': original['receipt_hash'],
            'close_audit_id': original['close'].id, 'close_audit_hash': digest(original['close'].data)})
    need(all(count >= requirement['minimum_records_per_entity'] for count in covered.values())
        and all(len(items) == 1 for items in values.values()))
    proof = {'schema': SCHEMA, 'plan_id': plan.id, 'observation_request_hash': digest(request),
        'policy_hash': policy.data['approved_hash'], 'completion_cut': cut.isoformat(),
        'settled_audit_id': event.id, 'settled_audit_hash': digest(event.data), 'settlement_job_id': job.id,
        'settlement_result_hash': job.data['settlement_result_hash'], 'queued_audit_id': queued.id,
        'queued_audit_hash': digest(queued.data), 'settlement_pin_hash': digest(pin),
        'result_hash': digest(result), 'supporting_originals': selected}
    return {'evidence': result, 'evidence_hash': digest(proof)}
