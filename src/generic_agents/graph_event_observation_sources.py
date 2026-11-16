"""Closed projections of actual processed observations, never physical facts.

All database locks and original-file verification happen before the admission
cursor is taken.  A projection is evidence for admission, not a graph/task grant.
Historical receipts use ``check_projection``; ``live=False`` can recheck source
material but cannot override a withdrawn local-model installation's own guard.
"""
import copy
import json
import math
import re
from datetime import datetime
from types import MappingProxyType
from uuid import UUID

from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import select

from .contracts import digest
from .models import Capability, Entity, Event, Evidence, Invocation, Job, Observation, SpecialistInstallation
from .observation_time_anchor_v135 import same_observation_instant


CONTRACT_SCHEMA = 'graph-event-observation-source-contract-v1'
PROJECTION_SCHEMA = 'graph-event-observation-projection-v1'
OUTPUT_SCHEMA = 'graph-event-observation-output-v1'
PROOF_SCHEMA = 'graph-event-observation-proof-v1'
ADAPTERS = MappingProxyType({'processed-observation-v1': 'observation.data',
                            'anomaly-candidate-v1': 'perception.anomaly'})
_PROCESSING_KINDS = frozenset({'process_observation', 'process_local_observation',
                             'process_reported_observation', 'process_mqtt_observation'})
_HEX = re.compile(r'^[0-9a-f]{64}$')
_VERSION = re.compile(r'^\d+\.\d+\.\d+$')
_CONTRACT_KEYS = {'schema', 'adapter_id', 'event_kind', 'correlation_kind',
                  'source_id', 'source_hash', 'source_release_version', 'extractor_id',
                  'extractor_hash', 'extractor_release_version', 'result_schema',
                  'output_schema', 'output_schema_hash', 'contract_hash'}
_OUTPUT_KEYS = {'schema', 'adapter_id', 'observation_id', 'entity_id', 'source_id',
                'extractor_id', 'invocation_id', 'observed_at', 'candidate_only',
                'result', 'source_time_verified', 'physical_state_verified'}
_BASE_KEYS = {'observation_id', 'entity_id', 'source_id', 'evidence_ids',
              'invocation_id', 'model_receipt', 'observed_at', 'candidate_only', 'extracted'}
_PROOF_KEYS = {'schema', 'workspace_id', 'adapter_id', 'event_kind', 'event_key',
               'source_event_base_hash', 'observation_id', 'source_record_id',
               'processing_version', 'entity_id', 'source_id', 'source_hash',
               'source_release_version', 'extractor_id', 'extractor_hash',
               'extractor_release_version', 'invocation_id', 'invocation_key',
               'invocation_hash', 'invocation_input_hash', 'invocation_output_hash',
               'invocation_lease_token', 'processing_job_id', 'processing_actor_id',
               'processing_job_hash', 'original_request_hash', 'evidence',
               'model_receipt_hash', 'observation_machine_hash'}


def _fail(code):
    raise ValueError('graph_event_observation:' + code)


def _safe(value):
    """Bounded JSON, including keys; no NaN, NUL or lone UTF-16 surrogates."""
    remaining = [100000]

    def visit(item, depth):
        remaining[0] -= 1
        if remaining[0] < 0 or depth > 64:
            _fail('json_limit')
        if item is None or type(item) in (bool, int):
            return
        if type(item) is float:
            if not math.isfinite(item): _fail('nonfinite')
            return
        if type(item) is str:
            if len(item) > 2 * 1024 * 1024 or '\x00' in item or any(0xD800 <= ord(c) <= 0xDFFF for c in item):
                _fail('unsafe_string')
            return
        if type(item) is list:
            for child in item: visit(child, depth + 1)
            return
        if type(item) is dict:
            for key, child in item.items():
                if type(key) is not str: _fail('json_key')
                visit(key, depth + 1); visit(child, depth + 1)
            return
        _fail('not_json')
    visit(value, 0)
    try:
        size = len(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8'))
    except (ValueError, UnicodeError, OverflowError):
        _fail('not_json')
    if size > 4 * 1024 * 1024: _fail('json_limit')


def _closed(value, keys, code):
    if type(value) is not dict or set(value) != keys: _fail(code)


def _text(value, maximum=200):
    if type(value) is not str or not value or len(value) > maximum: _fail('text')
    return value


def _uuid(value):
    if type(value) is not str: _fail('uuid')
    try:
        if str(UUID(value)) != value: _fail('uuid')
    except (ValueError, AttributeError):
        _fail('uuid')
    return value


def _hash(value):
    if type(value) is not str or not _HEX.fullmatch(value): _fail('hash')


def _schema(value):
    if type(value) is not dict: _fail('result_schema')
    _safe(value)
    if len(json.dumps(value).encode()) > 512 * 1024: _fail('schema_limit')
    refs = []

    def visit(item):
        if type(item) is dict:
            # Resource identifiers/anchors change local resolution when embedded.
            if any(key in item for key in ('$id', '$anchor', '$dynamicAnchor', '$dynamicRef', '$recursiveRef')):
                _fail('schema_resource')
            if '$ref' in item:
                ref = item['$ref']
                if type(ref) is not str or (ref != '#' and not ref.startswith('#/')):
                    _fail('schema_remote_or_anchor_ref')
                refs.append(ref)
            for child in item.values(): visit(child)
        elif type(item) is list:
            for child in item: visit(child)
    visit(value)
    for ref in refs:
        target = value
        if ref != '#':
            try:
                for component in ref[2:].split('/'):
                    if re.search(r'~(?![01])', component): _fail('schema_ref')
                    key = component.replace('~1', '/').replace('~0', '~')
                    target = target[int(key)] if type(target) is list else target[key]
            except (KeyError, TypeError, IndexError, ValueError):
                _fail('schema_ref')
        if type(target) not in (dict, bool): _fail('schema_ref')
    try:
        Draft202012Validator.check_schema(value)
    except Exception as error:
        raise ValueError('graph_event_observation:invalid_schema') from error


def _rebased_schema(value):
    if type(value) is list: return [_rebased_schema(item) for item in value]
    if type(value) is not dict: return copy.deepcopy(value)
    return {key: ('#/$defs/extractor_result' + child[1:] if key == '$ref' else _rebased_schema(child))
            for key, child in value.items()}


def _output_schema(result_schema, adapter_id):
    identity = {'type': 'string', 'format': 'uuid'}
    return {'type': 'object', 'additionalProperties': False, 'required': sorted(_OUTPUT_KEYS),
            '$defs': {'extractor_result': _rebased_schema(result_schema)},
            'properties': {'schema': {'const': OUTPUT_SCHEMA}, 'adapter_id': {'const': adapter_id},
                **{key: copy.deepcopy(identity) for key in ('observation_id', 'entity_id', 'source_id', 'extractor_id', 'invocation_id')},
                'observed_at': {'type': ['string', 'null'], 'format': 'date-time'},
                'candidate_only': {'type': 'boolean'}, 'result': {'$ref': '#/$defs/extractor_result'},
                'source_time_verified': {'const': False}, 'physical_state_verified': {'const': False}}}


def check_contract(value):
    """Pure exact contract/hash check; returns a detached copy, never authority."""
    _safe(value); _closed(value, _CONTRACT_KEYS, 'contract_shape')
    if value['schema'] != CONTRACT_SCHEMA or type(value['adapter_id']) is not str or value['adapter_id'] not in ADAPTERS:
        _fail('contract_adapter')
    if value['event_kind'] != ADAPTERS[value['adapter_id']] or value['correlation_kind'] != 'entity_id':
        _fail('contract_event')
    for prefix in ('source', 'extractor'):
        _uuid(value[prefix + '_id']); _hash(value[prefix + '_hash'])
        version = value[prefix + '_release_version']
        if type(version) is not str or len(version) > 40 or not _VERSION.fullmatch(version): _fail('release_version')
    if value['source_id'] == value['extractor_id']: _fail('contract_identity')
    _schema(value['result_schema'])
    if digest(value['output_schema']) != digest(_output_schema(value['result_schema'], value['adapter_id'])): _fail('output_schema')
    _hash(value['output_schema_hash']); _hash(value['contract_hash'])
    if value['output_schema_hash'] != digest(value['output_schema']): _fail('output_schema_hash')
    if value['contract_hash'] != digest({key: item for key, item in value.items() if key != 'contract_hash'}):
        _fail('contract_hash')
    return copy.deepcopy(value)


def registration_fields(contract):
    value = check_contract(contract)
    return {key: value[key] for key in ('adapter_id', 'source_id', 'source_hash', 'extractor_id', 'extractor_hash')}


def _row(db, model, identity, p, lock=True):
    _uuid(identity)
    query = select(model).where(model.id == identity, model.workspace_id == p.workspace_id)
    if lock: query = query.with_for_update(read=True)
    row = db.scalar(query.execution_options(populate_existing=True))
    if row is None: _fail('workspace_or_row')
    return row


def _actor(db, p, lock=True):
    _uuid(p.user_id); _uuid(p.workspace_id)
    if not lock: _fail('live_lock_required')
    from .relations import lock_current_actor
    lock_current_actor(db, p)


def _cap(row, *, kind=None, live=True):
    if type(row.data) is not dict: _fail('capability_data')
    _safe(row.data)
    if row.content_hash != digest(row.data) or (live and (row.status != 'published' or row.validated_hash != row.content_hash)):
        _fail('capability_changed_or_unpublished')
    if kind is not None and row.kind != kind: _fail('capability_kind')
    if row.data.get('version') != row.release_version or row.data.get('kind') != row.kind: _fail('capability_identity')


def build_contract(db, p, source_id, extractor_id, adapter_id):
    """Freeze published actual versions under shared locks; performs no commits."""
    if type(adapter_id) is not str or adapter_id not in ADAPTERS: _fail('contract_adapter')
    _actor(db, p)
    caps = {identity: _row(db, Capability, identity, p) for identity in sorted({_uuid(source_id), _uuid(extractor_id)})}
    source, extractor = caps[source_id], caps[extractor_id]
    _cap(source, kind='source'); _cap(extractor)
    if extractor.kind not in {'tool', 'specialist_model', 'foundation_model'} or 'perception' not in extractor.data.get('allowed_agent_roles', []):
        _fail('extractor_role')
    result_schema = copy.deepcopy(extractor.data.get('output_schema'))
    _schema(result_schema)
    value = {'schema': CONTRACT_SCHEMA, 'adapter_id': adapter_id, 'event_kind': ADAPTERS[adapter_id],
             'correlation_kind': 'entity_id', 'source_id': source.id, 'source_hash': source.content_hash,
             'source_release_version': source.release_version, 'extractor_id': extractor.id,
             'extractor_hash': extractor.content_hash, 'extractor_release_version': extractor.release_version,
             'result_schema': result_schema, 'output_schema': _output_schema(result_schema, adapter_id)}
    value['output_schema_hash'] = digest(value['output_schema'])
    value['contract_hash'] = digest(value)
    return check_contract(value)


def _source_key(adapter_id, observation_id, processing_version):
    return 'observation:' + digest({'adapter_id': adapter_id, 'observation_id': observation_id,
                                    'processing_version': processing_version})


def _processed_observation(row):
    if type(row.data) is not dict: _fail('observation_data')
    if row.status != 'processed' or not row.data.get('invocation_id') or any(key in row.data for key in ('native_packet', 'native_packet_hash', 'native_instance_id', 'native_receipt')):
        _fail('no_processed_invocation')
    if type(row.data.get('original_request')) is not dict: _fail('original_request_required')
    _uuid(row.data['invocation_id'])
    _safe(row.data['original_request']); _safe(row.data.get('extracted'))


def _processing_identity(job, row, extractor, call, p, live):
    if type(job.data) is not dict: _fail('processing_job_data')
    if job.kind not in _PROCESSING_KINDS or job.data.get('observation_id') != row.id or job.data.get('extractor_id') != extractor.id:
        _fail('processing_job_mismatch')
    processing_actor_id = _uuid(job.data.get('actor_id'))
    if live and processing_actor_id != p.user_id: _fail('processing_actor_mismatch')
    _uuid(call.data.get('lease_token'))
    return {'id': job.id, 'kind': job.kind, 'idempotency_key': job.idempotency_key,
            'observation_id': row.id, 'extractor_id': extractor.id, 'actor_id': processing_actor_id}


def _base_event(event, observation, call):
    """Only the original processing fields count; legacy additions grant nothing."""
    if type(event.data) is not dict or not _BASE_KEYS <= set(event.data): _fail('event_base_missing')
    try:
        original_time = same_observation_instant(event.data['observed_at'], observation.observed_at)
    except (ValueError, TypeError):
        _fail('event_observed_at')
    original = {'observation_id': observation.id, 'entity_id': observation.entity_id,
                'source_id': observation.source_id, 'evidence_ids': observation.data.get('evidence_ids', []),
                'invocation_id': call.id, 'model_receipt': call.data.get('model_receipt', {}),
                'observed_at': original_time,
                'candidate_only': observation.data.get('candidate_only', False),
                'extracted': observation.data.get('extracted')}
    _safe(original)
    if type(original['candidate_only']) is not bool or type(event.data['candidate_only']) is not bool:
        _fail('candidate_only')
    actual = {key: event.data[key] for key in _BASE_KEYS}
    _safe(actual)
    if digest(actual) != digest(original): _fail('event_base_changed')
    if digest(observation.data.get('model_receipt', {})) != digest(original['model_receipt']): _fail('model_receipt_changed')
    return original


def check_projection(value, contract=None):
    """Pure historical shape/hash check; does not authorize new admission."""
    _safe(value)
    _closed(value, {'schema', 'source_event_id', 'observation_id', 'source_key', 'correlation', 'output', 'proof', 'projection_hash'}, 'projection_shape')
    if value['schema'] != PROJECTION_SCHEMA: _fail('projection_schema')
    for key in ('source_event_id', 'observation_id', 'correlation'): _uuid(value[key])
    _hash(value['projection_hash'])
    if value['projection_hash'] != digest({key: item for key, item in value.items() if key != 'projection_hash'}): _fail('projection_hash')
    proof, output = value['proof'], value['output']
    _closed(proof, _PROOF_KEYS, 'proof_shape'); _closed(output, _OUTPUT_KEYS, 'output_shape')
    if proof['schema'] != PROOF_SCHEMA or output['schema'] != OUTPUT_SCHEMA: _fail('proof_schema')
    if type(proof['adapter_id']) is not str or proof['adapter_id'] not in ADAPTERS or proof['event_kind'] != ADAPTERS[proof['adapter_id']] or output['adapter_id'] != proof['adapter_id']:
        _fail('projection_adapter')
    for key in ('workspace_id', 'observation_id', 'entity_id', 'source_id', 'extractor_id', 'invocation_id',
                'invocation_lease_token', 'processing_job_id', 'processing_actor_id'):
        _uuid(proof[key])
    for key in ('source_event_base_hash', 'source_hash', 'extractor_hash', 'invocation_hash',
                'invocation_input_hash', 'invocation_output_hash', 'processing_job_hash',
                'original_request_hash', 'model_receipt_hash', 'observation_machine_hash'):
        _hash(proof[key])
    for key in ('source_record_id', 'invocation_key'): _text(proof[key])
    _text(proof['processing_version'], 40)
    for key in ('source_release_version', 'extractor_release_version'):
        if type(proof[key]) is not str or len(proof[key]) > 40 or not _VERSION.fullmatch(proof[key]): _fail('release_version')
    if type(proof['evidence']) is not list or len(proof['evidence']) > 100: _fail('evidence_shape')
    seen = set()
    for evidence in proof['evidence']:
        _closed(evidence, {'id', 'sha256'}, 'evidence_shape'); _uuid(evidence['id']); _hash(evidence['sha256'])
        if evidence['id'] in seen: _fail('duplicate_evidence')
        seen.add(evidence['id'])
    if value['observation_id'] != proof['observation_id'] or value['correlation'] != proof['entity_id']:
        _fail('projection_identity')
    if value['source_key'] != _source_key(proof['adapter_id'], proof['observation_id'], proof['processing_version']): _fail('source_key')
    prefix = 'data:' if proof['event_kind'] == 'observation.data' else 'anomaly:'
    if proof['event_key'] != prefix + proof['observation_id']: _fail('event_key')
    for key in ('observation_id', 'entity_id', 'source_id', 'extractor_id', 'invocation_id'):
        if output[key] != proof[key]: _fail('output_identity')
    if type(output['candidate_only']) is not bool or output['source_time_verified'] is not False or output['physical_state_verified'] is not False:
        _fail('fact_boundary')
    if output['observed_at'] is not None:
        try:
            when = datetime.fromisoformat(output['observed_at'])
            if when.tzinfo is None: _fail('observed_at')
        except (TypeError, ValueError): _fail('observed_at')
    elif not output['candidate_only']:
        _fail('observed_at')
    if proof['invocation_output_hash'] != digest(output['result']): _fail('result_hash')
    if type(output['result']) is not dict or type(output['result'].get('attributes')) is not dict: _fail('result_attributes')
    if proof['adapter_id'] == 'anomaly-candidate-v1':
        anomalies = output['result'].get('anomalies')
        if type(anomalies) is not list or not anomalies: _fail('no_anomaly_candidate')
    if contract is not None:
        checked = check_contract(contract)
        for key in ('adapter_id', 'event_kind', 'source_id', 'source_hash', 'source_release_version',
                    'extractor_id', 'extractor_hash', 'extractor_release_version'):
            if proof[key] != checked[key]: _fail('projection_contract')
        try:
            Draft202012Validator(checked['output_schema'], format_checker=FormatChecker()).validate(output)
        except Exception as error:
            raise ValueError('graph_event_observation:output_validation') from error
    return copy.deepcopy(value)


def project_source(db, p, producer, event, observation, *, live=True, lock=True):
    """Cold source projection, before any stream cursor. No writes or commits.

    ``live=False`` permits no new execution and only relaxes this adapter's current
    publication/actor checks. Subordinate original/local/MQTT receipt guards stay
    intact. Accepted-history reads should use frozen ``check_projection`` instead.
    """
    if type(live) is not bool or type(lock) is not bool or (live and not lock): _fail('live_lock_required')
    from .graph_event_admission import check_producer
    from .graph_event_models import GraphEventProducer
    from .observation_integrity import checked_report
    if live: _actor(db, p, lock)
    producer = _row(db, GraphEventProducer, producer.id, p, lock)
    contract = check_contract(check_producer(producer))
    if producer.workspace_id != p.workspace_id or (live and producer.status != 'active'): _fail('producer_scope_or_status')
    row = _row(db, Observation, observation.id, p, lock)
    notice = _row(db, Event, event.id, p, lock)
    _processed_observation(row)
    if notice.kind != contract['event_kind']: _fail('event_kind')
    expected_key = ('data:' if notice.kind == 'observation.data' else 'anomaly:') + row.id
    if notice.idempotency_key != expected_key: _fail('event_key')
    if row.source_id != contract['source_id'] or row.data.get('extractor_id') != contract['extractor_id']:
        _fail('observation_contract')
    caps = {identity: _row(db, Capability, identity, p, lock)
            for identity in sorted({contract['source_id'], contract['extractor_id']})}
    source, extractor = caps[contract['source_id']], caps[contract['extractor_id']]
    _cap(source, kind='source', live=live); _cap(extractor, live=live)
    if extractor.kind not in {'tool', 'specialist_model', 'foundation_model'} or 'perception' not in extractor.data.get('allowed_agent_roles', []):
        _fail('extractor_role')
    for prefix, cap in (('source', source), ('extractor', extractor)):
        if cap.content_hash != contract[prefix + '_hash'] or cap.release_version != contract[prefix + '_release_version']:
            _fail('frozen_capability_changed')
    if digest(extractor.data.get('output_schema')) != digest(contract['result_schema']): _fail('result_schema_changed')
    if row.data.get('candidate_only') and extractor.data.get('execution', {}).get('adapter') == 'local_specialist':
        # checked_report rechecks actual installation files. Keep its DB status
        # and generation stable until admission commits, before any cursor lock.
        _row(db, SpecialistInstallation, extractor.data['execution'].get('installation_id'), p, lock)
    entity = _row(db, Entity, row.entity_id, p, lock)
    call = _row(db, Invocation, row.data['invocation_id'], p, lock)
    if type(call.data) is not dict: _fail('invocation_data')
    _safe(call.data)
    if call.status != 'succeeded' or call.capability_id != extractor.id or call.data.get('role') != 'perception':
        _fail('invocation_not_succeeded')
    job = _row(db, Job, call.data.get('job_id'), p, lock)
    job_identity = _processing_identity(job, row, extractor, call, p, live)
    processing_actor_id = job_identity['actor_id']
    evidence_ids = row.data.get('evidence_ids', [])
    if type(evidence_ids) is not list or len(evidence_ids) > 100: _fail('evidence_shape')
    for identity in evidence_ids: _uuid(identity)
    if len(set(evidence_ids)) != len(evidence_ids): _fail('evidence_shape')
    evidence = [_row(db, Evidence, identity, p, lock) for identity in evidence_ids]
    base = _base_event(notice, row, call)
    if contract['adapter_id'] == 'anomaly-candidate-v1':
        # This reports arrival of a candidate, never a registered-state assertion.
        # The local original/installation receipt guard still runs in event_report.
        from .perception_signals import event_report
        original_row, report = event_report(db, p, notice, allow_candidates=True)
        if original_row.id != row.id: _fail('anomaly_observation')
    else:
        report = checked_report(db, p, row, allow_candidates=True)
    if report.get('basis') != 'original_inputs_and_extractor_receipt' or report.get('invocation_id') != call.id or report.get('extracted') != base['extracted']:
        _fail('original_receipt_mismatch')
    if report.get('file_hashes') != [item.sha256 for item in evidence]: _fail('evidence_hashes')
    inputs = {'attributes': row.data.get('attributes', {})}
    if evidence_ids: inputs['evidence_ids'] = evidence_ids
    if call.data.get('input_hash') != digest(inputs) or call.data.get('capability_hash') != extractor.content_hash or digest(call.data.get('output')) != digest(base['extracted']):
        _fail('invocation_receipt_mismatch')
    if 'inputs' in call.data and digest(call.data['inputs']) != digest(inputs): _fail('invocation_inputs_changed')
    output = {'schema': OUTPUT_SCHEMA, 'adapter_id': contract['adapter_id'], 'observation_id': row.id,
              'entity_id': entity.id, 'source_id': source.id, 'extractor_id': extractor.id,
              'invocation_id': call.id, 'observed_at': base['observed_at'], 'candidate_only': base['candidate_only'],
              'result': copy.deepcopy(base['extracted']), 'source_time_verified': False, 'physical_state_verified': False}
    invocation_identity = {'id': call.id, 'capability_id': call.capability_id, 'idempotency_key': call.idempotency_key,
                           **{key: call.data.get(key) for key in ('input_hash', 'role', 'capability_hash', 'job_id', 'lease_token')},
                           'output': base['extracted'], 'model_receipt': base['model_receipt']}
    machine = {'source_id': row.source_id, 'record': row.source_record_id, 'processing_version': row.processing_version,
               'entity_id': row.entity_id, 'observed_at': base['observed_at'], 'business_day': row.business_day,
               'original_request': row.data['original_request'], 'input_hash': row.data.get('input_hash'),
               'attributes': row.data.get('attributes'), 'extracted': base['extracted'], 'evidence_ids': evidence_ids,
               'invocation_id': call.id, 'extractor_id': extractor.id, 'source_hash': source.content_hash,
               'extractor_hash': extractor.content_hash}
    proof = {'schema': PROOF_SCHEMA, 'workspace_id': p.workspace_id, 'adapter_id': contract['adapter_id'],
             'event_kind': notice.kind, 'event_key': notice.idempotency_key, 'source_event_base_hash': digest(base),
             'observation_id': row.id, 'source_record_id': row.source_record_id, 'processing_version': row.processing_version,
             'entity_id': entity.id, **{key: contract[key] for key in ('source_id', 'source_hash', 'source_release_version',
                 'extractor_id', 'extractor_hash', 'extractor_release_version')},
             'invocation_id': call.id, 'invocation_key': call.idempotency_key, 'invocation_hash': digest(invocation_identity),
             'invocation_input_hash': digest(inputs), 'invocation_output_hash': digest(base['extracted']),
             'invocation_lease_token': call.data['lease_token'], 'processing_job_id': job.id,
             'processing_actor_id': processing_actor_id, 'processing_job_hash': digest(job_identity),
             'original_request_hash': digest(row.data['original_request']),
             'evidence': [{'id': item.id, 'sha256': item.sha256} for item in evidence],
             'model_receipt_hash': digest(base['model_receipt']), 'observation_machine_hash': digest(machine)}
    value = {'schema': PROJECTION_SCHEMA, 'source_event_id': notice.id, 'observation_id': row.id,
             'source_key': _source_key(contract['adapter_id'], row.id, row.processing_version),
             'correlation': entity.id, 'output': output, 'proof': proof}
    value['projection_hash'] = digest(value)
    return check_projection(value, contract)
