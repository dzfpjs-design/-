"""Immutable ACK-based machine-day snapshots, with later human work separate."""
import copy
from datetime import date

from fastapi import HTTPException
from sqlalchemy import select, text, or_, and_

from . import models as m
from .contracts import digest
from .services import audit
from . import native_pixel_provenance_v250 as origin
from .native_pixel_receipts_v250 import receipt_projection, handles

SCHEMA = 'native-pixel-daily-summary-v250'
ACTION = 'native_pixel.daily_saved_v250'
BLOCKED_ACTION = 'native_pixel.daily_blocked_v250'
LIMIT = 1000
HUMAN_BASIS = 'current_completed_task_receipts_not_daily_snapshot'


def day(value):
    try:
        origin.need(type(value) is str and date.fromisoformat(value).isoformat() == value)
    except (ValueError, TypeError):
        origin.need(False, '请按北京时间登记日填写有效日期（YYYY-MM-DD）')
    return value


def inventory(db, p, instance_id, registration_day):
    day(registration_day)
    issued = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == origin.ISSUED_ACTION,
        m.Audit.data['instance_id'].as_string() == instance_id,
        m.Audit.data['registration_day'].as_string() == registration_day)
        .order_by(m.Audit.id).limit(LIMIT + 1)))
    origin.need(len(issued) <= LIMIT, '该登记日超过本次完整归集上限；未输出不完整总数，请缩小业务范围后核查。')
    prior = summaries(db, p, instance_id, registration_day)
    snapshot_ids = [item['observation_id'] for item in prior[0].data['records']] if prior else []
    # Audit-only discovery would turn a deleted/moved issued Audit into an
    # empty day. Original rows, fixed source identity and the prior immutable
    # snapshot independently retain the same registration inventory.
    observations = list(db.scalars(select(m.Observation).outerjoin(m.Capability,
        m.Capability.id == m.Observation.source_id).where(m.Observation.workspace_id == p.workspace_id,
        or_(and_(m.Observation.data[origin.PACKET]['instance_id'].as_string() == instance_id,
                 m.Observation.data[origin.PACKET]['registration_day'].as_string() == registration_day),
            and_(m.Observation.business_day == registration_day,
                 m.Capability.workspace_id == p.workspace_id,
                 m.Capability.namespace == 'platform_native_pixel_results',
                 m.Capability.data['specification']['managed_instance'].as_string() == instance_id),
            m.Observation.id.in_([anchor.object_id for anchor in issued] + snapshot_ids)))
        .order_by(m.Observation.id).limit(LIMIT + 1)))
    origin.need(len(observations) <= LIMIT and len(observations) == len(issued)
        and {r.id for r in observations} == {a.object_id for a in issued}
        and set(snapshot_ids) <= {r.id for r in observations})
    values, seen = [], set()
    for observation in observations:
        anchor = next(item for item in issued if item.object_id == observation.id)
        context = origin.checked_observation(db, p, observation)
        packet = context['packet']; run = context['run']
        origin.need(run.id not in seen and context['issued_audit'].id == anchor.id
                    and packet['registration_day'] == registration_day and packet['instance_id'] == instance_id)
        seen.add(run.id)
        reception = receipt_projection(db, p, run)
        origin.need(reception['state'] in {'waiting_data', 'received'})
        values.append((context, reception))
    return values


def content(db, p, instance_id, registration_day):
    values = inventory(db, p, instance_id, registration_day)
    records, totals = [], {'candidate_pixels': 0, 'common_valid_pixels': 0, 'unknown_pixels': 0}
    for context, reception in values:
        if reception['state'] != 'received':
            continue
        run = context['run']; packet = context['packet']
        records.append({'run_id': run.id, 'task_id': run.task_id,
            'entity_id': context['observation'].entity_id, 'observation_id': context['observation'].id,
            'event_id': reception['event_id'], 'memory_id': reception['memory_id'],
            'packet_hash': digest(packet), 'receipt_hash': reception['receipt']['receipt_hash'],
            'result_hash': run.data['result_hash'], 'sources_hash': run.data['sources_hash']})
        for key in totals:
            totals[key] += run.data['result'][key]
    records.sort(key=lambda item: item['run_id'])
    body = {'schema': SCHEMA, 'workspace_id': p.workspace_id, 'instance_id': instance_id,
        'registration_day': registration_day, 'time_zone': 'Asia/Shanghai',
        'date_basis': 'machine_result_registered', 'records': records,
        'counts': {'registered_results': len(values), 'received_results': len(records),
                   'waiting_results': len(values) - len(records)},
        'metrics': totals if records else {key: None for key in totals},
        'metrics_basis': 'independently_received_results_only',
        'totals_unit': 'model_result_pixels_not_object_count_or_area',
        'candidate_only': True, 'not_authoritative': True, 'object_state_updated': False,
        'human_receipts_basis': HUMAN_BASIS}
    return {**body, 'summary_hash': digest(body)}, values


def checked_summary(db, p, memory):
    origin.need(memory.workspace_id == p.workspace_id and memory.status == 'candidate'
                and memory.data.get('schema') == SCHEMA
                and memory.data.get('summary_hash') == digest({k: v for k, v in memory.data.items() if k != 'summary_hash'}))
    issued = origin.audits(db, p, ACTION, memory.id)
    expected = {'schema': 'native-pixel-daily-saved-v250', 'instance_id': memory.data['instance_id'],
        'registration_day': memory.data['registration_day'], 'summary_hash': memory.data['summary_hash'],
        'version': memory.version}
    origin.need(len(issued) == 1 and origin.same({k: issued[0].data.get(k) for k in expected}, expected)
        and set(issued[0].data) == set(expected) | {'previous_summary_id', 'rebuild_command_id', 'rebuild_request_id'})
    return memory


def summaries(db, p, instance_id, registration_day):
    issued = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == ACTION, m.Audit.data['instance_id'].as_string() == instance_id,
        m.Audit.data['registration_day'].as_string() == registration_day)))
    rows = list(db.scalars(select(m.Memory).where(m.Memory.workspace_id == p.workspace_id,
        or_(and_(m.Memory.data['schema'].as_string() == SCHEMA,
                m.Memory.data['instance_id'].as_string() == instance_id,
                m.Memory.data['registration_day'].as_string() == registration_day),
            m.Memory.id.in_([a.object_id for a in issued])))
        .order_by(m.Memory.version.desc(), m.Memory.id.desc())))
    origin.need(len(issued) == len(rows) and {a.object_id for a in issued} == {r.id for r in rows}
                and sorted(r.version for r in rows) == list(range(1, len(rows) + 1)))
    by_version = {r.version: r for r in rows}
    for memory in rows:
        checked_summary(db, p, memory)
        origin.need(memory.data['instance_id'] == instance_id and memory.data['registration_day'] == registration_day)
        anchor = next(a for a in issued if a.object_id == memory.id)
        previous = by_version.get(memory.version - 1)
        origin.need(anchor.data['previous_summary_id'] == (previous.id if previous else None))
    return rows


def lock_day(db, p, instance_id, registration_day):
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'),
        {'key': int(digest(['native-pixel-day-v250', p.workspace_id, instance_id, registration_day])[:15], 16)})


def blocked(db, p, instance_id, registration_day):
    values = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action == BLOCKED_ACTION, m.Audit.object_id == instance_id,
        m.Audit.data['registration_day'].as_string() == registration_day)
        .order_by(m.Audit.created_at.desc(), m.Audit.id.desc())))
    fingerprints = set()
    for value in values:
        origin.need(value.data.get('schema') == 'native-pixel-daily-blocked-v250'
            and value.data.get('instance_id') == instance_id
            and value.data.get('fingerprint') == digest(value.data.get('basis'))
            and value.data['fingerprint'] not in fingerprints)
        fingerprints.add(value.data['fingerprint'])
    return values


def _record_blocked(db, p, instance_id, registration_day):
    lock_day(db, p, instance_id, registration_day)
    issued = list(db.scalars(select(m.Audit).where(m.Audit.workspace_id == p.workspace_id,
        m.Audit.action.in_([origin.ISSUED_ACTION, ACTION]),
        m.Audit.data['instance_id'].as_string() == instance_id,
        m.Audit.data['registration_day'].as_string() == registration_day)
        .order_by(m.Audit.id).limit(2 * LIMIT + 1)))
    basis = {'instance_id': instance_id, 'registration_day': registration_day,
        'anchors': [{'id': a.id, 'action': a.action, 'data_hash': digest(a.data)} for a in issued],
        'reason': 'daily_source_or_saved_summary_integrity'}
    fingerprint = digest(basis)
    previous = blocked(db, p, instance_id, registration_day)
    if any(a.data['fingerprint'] == fingerprint for a in previous):
        return None
    audit(db, p, BLOCKED_ACTION, instance_id, {'schema': 'native-pixel-daily-blocked-v250',
        'instance_id': instance_id, 'registration_day': registration_day,
        'basis': basis, 'fingerprint': fingerprint})
    return None


def refresh_received(db, p, observation):
    """Receiver calls after its real Memory/ACK, in that same transaction."""
    if not handles(db, p, observation):
        return None
    context = origin.checked_observation(db, p, observation)
    from .instance_data_receipts import observation_receipt
    reception = observation_receipt(db, p, observation)['data_reception']
    instance_id, registration_day = reception['instance_id'], reception['registration_day']
    try:
        with db.begin_nested():
            return _refresh_day(db, p, instance_id, registration_day)
    except HTTPException as error:
        if error.status_code != 409:
            raise
        # An unrelated damaged historical result cannot erase this valid ACK.
        # The failed aggregate is explicit and never saved as a successful day.
        _record_blocked(db, p, instance_id, registration_day)
        return None


def _refresh_day(db, p, instance_id, registration_day, *, rebuild=None):
    lock_day(db, p, instance_id, registration_day)
    value, _ = content(db, p, instance_id, registration_day)
    previous = summaries(db, p, instance_id, registration_day)
    for memory in previous:
        checked_summary(db, p, memory)
        if rebuild is None and memory.data['summary_hash'] == value['summary_hash']:
            origin.need(memory.data == value)
            return memory
    memory = m.Memory(workspace_id=p.workspace_id, status='candidate', version=len(previous) + 1,
        text='本地像素成果按登记日归集，仅统计独立收件的机器候选；非现场变化结论', data=copy.deepcopy(value))
    db.add(memory); db.flush()
    audit(db, p, ACTION, memory.id, {'schema': 'native-pixel-daily-saved-v250',
        'instance_id': instance_id, 'registration_day': registration_day, 'summary_hash': value['summary_hash'],
        'version': memory.version, 'previous_summary_id': previous[0].id if previous else None,
        'rebuild_command_id': rebuild['command_id'] if rebuild else None,
        'rebuild_request_id': rebuild['request_id'] if rebuild else None})
    db.flush()
    return memory


def projection(db, p, instance_id, registration_day):
    origin.row(db, p, m.DomainInstance, instance_id)
    day(registration_day)
    issue = None; value = None; rows = []; previous = []; trustworthy_head = True
    try:
        previous = summaries(db, p, instance_id, registration_day)
    except HTTPException as error:
        if error.status_code != 409: raise
        trustworthy_head = False; issue = origin.ERROR
    errors = blocked(db, p, instance_id, registration_day)
    try:
        if trustworthy_head: value, rows = content(db, p, instance_id, registration_day)
    except HTTPException as error:
        if error.status_code != 409: raise
        issue = origin.ERROR
    latest = previous[0] if previous else None
    saved = None if latest is None else {'id': latest.id, 'summary_hash': latest.data['summary_hash'],
        'version': latest.version, 'created_at': origin.iso(latest.created_at)}
    from .native_pixel_provider_v250 import result
    return {'schema': 'native-pixel-machine-daily-v250', 'workspace_id': p.workspace_id,
        'instance_id': instance_id, 'registration_day': registration_day,
        'time_zone': 'Asia/Shanghai', 'date_basis': 'machine_result_registered',
        'records': [result(db, p, context['run'], data_only=True) for context, _ in rows] if issue is None else [],
        'counts': value['counts'] if value else None, 'metrics': value['metrics'] if value else None,
        'metrics_basis': 'independently_received_results_only',
        'totals_unit': 'model_result_pixels_not_object_count_or_area', 'summary': saved,
        'summary_state': 'blocked_integrity' if issue else 'not_saved' if latest is None else 'saved_current'
            if latest.data == value else 'stale', 'integrity_issue': issue,
        'blocked_record': None if not errors else {'id': errors[0].id,
            'fingerprint': errors[0].data['fingerprint'], 'created_at': origin.iso(errors[0].created_at)},
        'rebuild': {'can_rebuild': trustworthy_head and p.role in {'admin', 'operator'},
            'reason': '仅重建原登记日机器汇总，不重新分析或改写收件回执' if trustworthy_head
                      and p.role in {'admin', 'operator'} else '需要当前有权账号及完整原日版本依据',
            'version': latest.version if latest else 0,
            'expected_summary_hash': latest.data['summary_hash'] if latest else None,
            'expected_blocked_fingerprint': errors[0].data['fingerprint'] if errors else None},
        'human_receipts_basis': HUMAN_BASIS, 'not_authoritative': True,
        'coordinate_space': 'pixel', 'association_basis': 'task_entity_association_not_verified_coverage'}
