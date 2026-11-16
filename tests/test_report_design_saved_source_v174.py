"""Synthetic source-adapter tests, NOT authenticated API/PG/report evidence.

The producer row and DB ledger below are deliberately public fake rows. The
owned/check_row and material authority boundaries are explicit spies: these
tests do not prove those independent authorities. Current-role/pending queries
and frozen reservation checks run against a read-only fake DB. Actual V172
Map/capture/journal reconstruction and the complete-input reducer run for real
on temporary files, always retaining the original producer bindings. Root owns
execution. No successful provider, persisted Job or business record is claimed.
"""
import copy
from contextlib import nullcontext
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from generic_agents import report_design_saved_source_v174 as subject
from generic_agents import report_evidence_pipeline as pipeline
from generic_agents import report_evidence_enum_pipeline_v172 as maps
from generic_agents import report_evidence_enum_reduction_v172 as reducer
from generic_agents import report_evidence_native_bridge_v1 as bridge
from generic_agents import report_evidence_http_capture_v1 as capture
from generic_agents import report_claim_enum_native_v172 as native
from generic_agents.contracts import digest
from generic_agents.models import CloudCall, Job, SetupAnalysis, SetupDraft, SetupReportDraft, User
from tests.test_report_evidence_enum_pipeline_v172 import kernel, no_external_effects, files, raw
from tests.test_report_evidence_enum_reduction_v172 import identity


class ReadOnlyDB:
    """Only selected public query results; no ORM/session/SQL integration claim."""
    def __init__(self, case):
        self.case = case; self.calls = {}; self.jobs = {}; self.role = True
        self.pending_report = None; self.pending_analysis = []; self.log = []
        self.no_autoflush = nullcontext()

    def scalar(self, statement):
        entity = statement.column_descriptions[0]['entity']
        params = list(statement.compile().params.values())
        self.log.append(('scalar', entity.__name__))
        if entity is User:
            return self.case.principal.user_id if self.role else None
        if entity is SetupDraft:
            return NS(id=identity(500), workspace_id=self.case.row.workspace_id, data={}, version=1)
        if entity is SetupReportDraft:
            return self.pending_report
        if entity is CloudCall:
            selected = [row for key, row in self.calls.items() if key in params]
            if selected:
                assert len(selected) == 1
                return selected[0]
            # The only other CloudCall query asks for any unresolved call id.
            return next((row.id for row in self.calls.values()
                if row.status not in {'succeeded', 'failed'}), None)
        if entity is Job:
            selected = [row for row in self.jobs.values()
                if row.id in params or row.idempotency_key in params]
            assert len(selected) <= 1
            return selected[0] if selected else None
        raise AssertionError('Unexpected read boundary: ' + entity.__name__)

    def scalars(self, statement):
        assert statement.column_descriptions[0]['entity'] is SetupAnalysis
        self.log.append(('scalars', 'SetupAnalysis'))
        return NS(all=lambda: self.pending_analysis)

    def refresh(self, row):
        assert row is self.case.row
        self.log.append(('refresh', row.id))

    def add(self, *args, **kwargs): raise AssertionError('Source must not add a row')
    def flush(self, *args, **kwargs): raise AssertionError('Source must not flush')
    def commit(self, *args, **kwargs): raise AssertionError('Source must not commit')
    def execute(self, *args, **kwargs): raise AssertionError('Source must not run write SQL')


@pytest.fixture
def source_case(kernel, monkeypatch):
    case = kernel(); case.consume_maps()
    report_nonce = 'fe_' + identity(601)
    reduce_nonce = 'fer_' + identity(602)
    case.row.request_id = report_nonce
    case.row.data['preview']['source_snapshot'] = {'public_synthetic_boundary': 'not-original-report-authority'}
    case.row.data.update(start_command_hash='c' * 64,
        start_receipt={'public_synthetic_start_receipt': True}, job_id=identity(604))
    complete = maps.map_inputs(pipeline, case.row, case.catalog)
    prepared = reducer.build_reduction_preparation(case.cap, case.preparation, case.catalog, complete)
    case.row.data['reduction'] = pipeline._empty_stage('failed',
        preview={'reduction_preparation': prepared}, start_receipt={'request_id': reduce_nonce})
    case.row.data['reduction'].update(reserved_call_id=identity(605), inflight=True,
        error_code='evidence_candidate_invalid')
    case.row.status = 'failed'; case.row.data['phase'] = 'failed'
    body = raw({'choices': [{'finish_reason': 'length', 'message': {
        'role': 'assistant', 'content': '{"result":'}}],
        'usage': {'prompt_tokens': 11, 'completion_tokens': 32768, 'total_tokens': 32779}})
    context = bridge.context(pipeline, case.row, 'reduce')
    with capture.prepare(case.captures, context) as sink:
        sink.mark_request_started(); sink.begin_response(200, None, 'public-length-original')
        sink.append(body); sink.finish(eof=True)
    policy = copy.deepcopy(pipeline._spec(case.row, 'reduce')['input_policy'])
    assert policy.pop('single_batch') is True
    content, receipt, state = bridge.response_context(context, 'qwen', native.MODEL, policy, 'reduce-v172')
    adapter = pipeline._journal_for(case.row, 'reduce')
    case.row.data['reduction']['journal'] = adapter.write_response(case.journal2,
        context['binding'], content, receipt)
    assert state['ready'] is True and receipt['finish_reason'] == 'length'
    with pytest.raises(ValueError):
        native.parse_arguments(body, 200, 'reduce-v172', response_complete=True, http_content_encoding=None)
    db = ReadOnlyDB(case)
    for key in [*case.wires, 'reduce']:
        stage = pipeline._stage(case.row, key)
        loaded = pipeline._journal_for(case.row, key).read_response(
            pipeline._stage_journal_root(case.row, key),
            pipeline.journal_binding(case.row, key, stage['reserved_call_id']), stage['journal'])
        spec, authorized = pipeline._spec(case.row, key), pipeline._authorized(case.row, key)
        expected = pipeline._expected(case.row, key)
        db.calls[stage['reserved_call_id']] = NS(id=stage['reserved_call_id'],
            workspace_id=case.row.workspace_id, capability_id=expected['capability_id'],
            status='failed' if key == 'reduce' else 'succeeded',
            reserved_tokens=authorized['input_summary']['token_bound'], charged_tokens=12,
            data={**{field: expected[field] for field in ('actor_id', 'request_hash', 'dispatch_options_hash')},
                'input_policy_hash': digest(spec['input_policy']), 'delivery': spec['delivery'],
                'request_endpoint': spec['endpoint'], 'schema_proof_hash': digest(spec['schema_proof']),
                'request_body_bytes': authorized['input_summary']['prepared_request_bytes'],
                'finished_at': '2026-10-22T10:00:00+00:00',
                'receipt': pipeline.legacy.safe_paid_receipt(loaded['receipt'])})
    for key, status, job_id in [('report-evidence:' + report_nonce, 'succeeded', identity(603)),
            ('evidence-reduce:' + reduce_nonce, 'failed', identity(604))]:
        db.jobs[job_id] = NS(id=job_id, workspace_id=case.row.workspace_id,
            kind=pipeline.legacy.KIND, status=status, idempotency_key=key, lease_until=None,
            data={'actor_id': case.row.actor_id, 'report_draft_id': case.row.id,
                'finished_at': '2026-10-22T10:00:01+00:00'})
    authority = []
    def owned(actual_db, actual_p, report_id, lock=False):
        assert actual_db is db and type(lock) is bool
        authority.append(('owned', report_id, lock))
        if report_id != case.row.id or actual_p.user_id != case.row.actor_id or actual_p.workspace_id != case.row.workspace_id:
            raise HTTPException(404, 'Synthetic ownership boundary denied')
        return case.row
    def checked(row, p):
        assert row is case.row and p is case.principal
        authority.append(('check_row', row.id)); return row
    def materials(actual_db, p, row, *, lock):
        assert actual_db is db and p is case.principal and row is case.row
        authority.append(('material_refs', row.id, lock))
        return [{'id': identity(610), 'version': 2, 'hash': 'e' * 64,
            'name': 'PUBLIC-SYNTHETIC', 'evidence_id': identity(611)}]
    monkeypatch.setattr(pipeline, 'owned', owned)
    monkeypatch.setattr(pipeline, 'check_row', checked)
    monkeypatch.setattr(subject, 'material_refs', materials)
    return NS(case=case, db=db, p=case.principal, authority=authority, failed_body=body)


def preserved(scene):
    return (copy.deepcopy(scene.case.row.__dict__), files(scene.case.root),
        copy.deepcopy({key: value.__dict__ for key, value in scene.db.calls.items()}),
        copy.deepcopy({key: value.__dict__ for key, value in scene.db.jobs.items()}),
        copy.deepcopy(scene.case.events), copy.deepcopy(scene.case.settlements),
        copy.deepcopy(scene.case.checkpoints))


@pytest.mark.parametrize('original_flag', [False, True])
def test_known_failed_length_only_reads_complete_maps_under_original_bindings(source_case, original_flag):
    scene = source_case; scene.case.row.data['reduction']['inflight'] = original_flag
    before = preserved(scene)
    context = subject.load_source_context(scene.db, scene.p, scene.case.row.id, lock=True)
    value = context['snapshot']
    assert value['schema'] == subject.SCHEMA and value['scope'] == subject.SCOPE
    assert value['producer']['id'] == scene.case.row.id and value['producer']['status'] == 'failed'
    assert value['known_producer_failure']['original_inflight'] is original_flag
    assert value['known_producer_failure']['arguments_adopted'] is False
    assert value['known_producer_failure']['finish_reason'] == 'length'
    assert all(call['binding']['draft_id'] == scene.case.row.id for call in value['map_calls'])
    assert [proof['chunk_id'] for proof in value['map_proofs']] == list(scene.case.wires)
    assert value['producer_model']['published_model_hash'] == scene.case.row.data['preview']['published_model_hash']
    inputs = reducer.reduction_inputs_v3(scene.case.preparation, scene.case.catalog, scene.case.cap,
        maps.map_inputs(pipeline, scene.case.row, scene.case.catalog))
    assert value['input_order'] == inputs['input_order']
    assert value['input_items'] == inputs['input_items'] and len(value['input_order']) == 4
    assert value['input_items'][value['input_order'][0]] == value['input_items'][value['input_order'][1]]
    assert value['input_items_hash'] == digest(inputs['input_items'])
    assert value['context']['counter'] == 1.0 and str(value['context']['zero']) == '-0.0'
    assert context['catalog'] == scene.case.catalog and context['allowed_span_refs'] == inputs['allowed_span_refs']
    assert all(value[key] is False for key in ('original_reduction_adopted', 'semantic_quality_verified',
        'consumer_authorized', 'can_apply', 'can_publish'))
    assert subject.check_source(value, scene.db, scene.p, lock=True) == value
    assert any(entry[0] == 'material_refs' for entry in scene.authority)
    assert preserved(scene) == before


@pytest.mark.parametrize('fault', ['producer-revision', 'producer-id', 'published-model', 'map-binding',
    'map-proof', 'first-claim', 'drop-tail', 'claim-count', 'unknown-field', 'typed-context'])
def test_resealed_snapshot_does_not_replace_real_producer_or_lose_any_claim(source_case, fault):
    scene = source_case; value = subject.load_source(scene.db, scene.p, scene.case.row.id)
    tampered = copy.deepcopy(value)
    if fault == 'producer-revision': tampered['producer']['revision'] += 1
    elif fault == 'producer-id': tampered['producer']['id'] = identity(999)
    elif fault == 'published-model': tampered['producer_model']['published_model_hash'] = 'f' * 64
    elif fault == 'map-binding': tampered['map_calls'][0]['binding']['draft_id'] = identity(999)
    elif fault == 'map-proof': tampered['map_proofs'][0]['projection_hash'] = 'f' * 64
    elif fault == 'first-claim': tampered['input_items'][tampered['input_order'][0]]['statement'] += ' fabricated'
    elif fault == 'drop-tail': del tampered['input_items'][tampered['input_order'].pop()]
    elif fault == 'claim-count': tampered['claim_count'] -= 1
    elif fault == 'unknown-field': tampered['private_note'] = 'must never become a source field'
    else: tampered['context']['counter'] = 1
    tampered['snapshot_hash'] = digest({key: val for key, val in tampered.items() if key != 'snapshot_hash'})
    before = preserved(scene)
    with pytest.raises(HTTPException): subject.check_source(tampered, scene.db, scene.p)
    assert preserved(scene) == before


@pytest.mark.parametrize('fault', ['producer-running', 'producer-unknown', 'old-family', 'map-inflight',
    'map-failed', 'map-missing', 'reduce-not-failed', 'has-machine-candidate', 'reduce-job-running',
    'map-job-running', 'reduce-job-missing', 'map-call-failed', 'reduce-call-reserved',
    'wrong-call-capability', 'wrong-request-hash', 'wrong-ledger-receipt', 'bad-charge'])
def test_incomplete_unknown_mixed_or_unsettled_sources_fail_closed(source_case, fault):
    scene = source_case; row = scene.case.row; stage = row.data['maps'][0]
    first_call = scene.db.calls[stage['reserved_call_id']]
    reduce_call = scene.db.calls[row.data['reduction']['reserved_call_id']]
    if fault == 'producer-running': row.status = 'running'
    elif fault == 'producer-unknown': row.status = 'response_unknown'
    elif fault == 'old-family': row.data['preview']['map_preparation']['generation_contract']['schema'] = 'report-claim-array-map-generation-v168'
    elif fault == 'map-inflight': stage['inflight'] = True
    elif fault == 'map-failed': stage['status'] = 'failed'
    elif fault == 'map-missing': row.data['maps'].pop()
    elif fault == 'reduce-not-failed': row.data['reduction']['status'] = 'succeeded'
    elif fault == 'has-machine-candidate': row.data['model_result_hash'] = 'f' * 64
    elif fault == 'reduce-job-running': scene.db.jobs[row.data['job_id']].status = 'running'
    elif fault == 'map-job-running': scene.db.jobs[identity(603)].status = 'running'
    elif fault == 'reduce-job-missing': del scene.db.jobs[row.data['job_id']]
    elif fault == 'map-call-failed': first_call.status = 'failed'
    elif fault == 'reduce-call-reserved': reduce_call.status = 'reserved'
    elif fault == 'wrong-call-capability': first_call.capability_id = identity(888)
    elif fault == 'wrong-request-hash': first_call.data['request_hash'] = 'f' * 64
    elif fault == 'wrong-ledger-receipt': reduce_call.data['receipt']['finish_reason'] = 'stop'
    else: first_call.charged_tokens = -1
    before = preserved(scene)
    with pytest.raises(HTTPException): subject.load_source(scene.db, scene.p, row.id)
    assert preserved(scene) == before


@pytest.mark.parametrize('fault', ['role-revoked', 'other-active-report', 'legacy-unknown', 'source-changed'])
def test_current_authority_and_existing_unknown_gates_are_required(source_case, monkeypatch, fault):
    scene = source_case
    if fault == 'role-revoked': scene.db.role = False
    elif fault == 'other-active-report': scene.db.pending_report = NS(id=identity(777), status='response_unknown')
    elif fault == 'legacy-unknown': scene.db.pending_analysis = [NS(status='failed', data={'inflight': 'original', 'raw_response': None})]
    else:
        def changed(*args, **kwargs): raise HTTPException(409, 'Public source authority boundary changed')
        monkeypatch.setattr(subject, 'material_refs', changed)
    before = preserved(scene)
    with pytest.raises(HTTPException): subject.load_source(scene.db, scene.p, scene.case.row.id)
    assert preserved(scene) == before


def test_source_never_calls_recovery_settlement_provider_or_rewrites_original_files(source_case, monkeypatch):
    scene = source_case
    def forbidden(*args, **kwargs): raise AssertionError('Source attempted a side effect')
    for name in ('_settle', '_consume', '_recover_native', '_run_stage', 'cloud_json', 'audit', 'checkpoint'):
        monkeypatch.setattr(pipeline, name, forbidden)
    monkeypatch.setattr(bridge, 'write_missing_journal', forbidden)
    before = preserved(scene)
    snapshot = subject.load_source(scene.db, scene.p, scene.case.row.id)
    assert snapshot['known_producer_failure']['http_body_sha256'] == digest_bytes(scene.failed_body)
    assert snapshot['claim_count'] == 4 and preserved(scene) == before


def digest_bytes(value):
    import hashlib
    return hashlib.sha256(value).hexdigest()


def test_context_return_and_snapshot_are_independent_copies_and_capacity_never_truncates(source_case, monkeypatch):
    scene = source_case; before = preserved(scene)
    result = subject.load_source_context(scene.db, scene.p, scene.case.row.id)
    result['context']['counter'] = 99
    result['catalog']['index'].clear()
    assert result['snapshot']['context']['counter'] == 1.0
    assert preserved(scene) == before
    monkeypatch.setattr(subject, 'MAX_BYTES', 1)
    with pytest.raises(HTTPException): subject.load_source(scene.db, scene.p, scene.case.row.id)
    assert preserved(scene) == before
