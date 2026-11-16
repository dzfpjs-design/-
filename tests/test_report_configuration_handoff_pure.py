"""No services: handoff/source proof must not mutate a paid private candidate."""
import copy
import json
import runpy
from pathlib import Path
from types import SimpleNamespace as NS
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from generic_agents import report_configuration_handoff as h
from generic_agents.contracts import digest

Q = runpy.run_path(str(Path(__file__).with_name('test_report_draft_partial_review_pure.py')))
P, pr = Q['P'], Q['pr']


def state():
    report, call = Q['state']()
    pr.record_local_review(report, P, Q['command'](report), lambda: call)
    config = NS(id=str(uuid4()), workspace_id=P.workspace_id, version=2,
                data={'version': 1, 'resources': [], 'connections': [], 'businesses': [], 'flows': {},
                      'instanceProfile': {}, 'materials': []})
    receipt = report.data['local_review_receipts'][-1]['receipt']
    body = h.PreviewCommand(report_revision=report.version, review_receipt_hash=receipt['receipt_hash'],
        candidate_hash=digest(pr.current_value(report)['result']), source_hash=report.data['preview']['source_hash'],
        validation_report_hash=report.data['validation_report']['report_hash'], configuration_revision=config.version,
        configuration_hash=digest(config.data), business_indexes=[0])
    return report, config, body


def accepted():
    report, config, body = state()
    preview = h.preview_value(report, P, config, body)
    command = h.HandoffCommand(**body.model_dump(), request_id=uuid4(), preview_hash=preview['preview_hash'], consent=True)
    record, proposed, receipt = h.handoff_value(report, P, config, command, str(uuid4()))
    return report, config, command, record, proposed, receipt


def test_preview_is_private_read_only_and_only_selected_business_is_shared():
    report, config, body = state(); before = copy.deepcopy(report.__dict__)
    preview = h.preview_value(report, P, config, body)
    assert report.__dict__ == before
    assert preview['preview_hash'] == digest({k: v for k, v in preview.items() if k != 'preview_hash'})
    assert len(preview['selection']) == 1
    assert not any(key in preview['selection'][0] for key in ('note', 'objects', 'relationships', 'questions'))
    assert 'raw_response' not in preview and 'source_snapshot' not in preview


def test_handoff_preserves_private_row_version_raw_fees_and_retains_non_adopted_objects():
    report, config, body = state(); before = copy.deepcopy(report.__dict__)
    preview = h.preview_value(report, P, config, body)
    command = h.HandoffCommand(**body.model_dump(), request_id=uuid4(), preview_hash=preview['preview_hash'], consent=True)
    record, proposed, receipt = h.handoff_value(report, P, config, command, str(uuid4()))
    assert report.__dict__ == before and config.data['businesses'] == []
    assert report.data['raw_response']['value']['objects']
    assert not proposed.get('instanceObjects') and not proposed.get('instanceRelationTypes')
    flow = next(iter(proposed['flows'].values()))
    assert flow['confirmedSignature'] is None and flow['confirmedAt'] is None
    assert flow['reportSource'] == {'handoff_id': record['id'], 'candidate_index': 0}
    assert receipt['accepted_revision'] == config.version + 1


@pytest.mark.parametrize('field,value', [('report_revision', 999), ('configuration_revision', 999),
    ('candidate_hash', '0'*64), ('source_hash', '0'*64), ('review_receipt_hash', '0'*64),
    ('validation_report_hash', '0'*64), ('configuration_hash', '0'*64)])
def test_preview_cas_and_saved_receipt_are_not_optional(field, value):
    report, config, body = state()
    with pytest.raises(HTTPException) as error:
        h.preview_value(report, P, config, body.model_copy(update={field: value}))
    assert error.value.status_code == 409


def test_strict_extra_boolean_and_duplicate_selection_rejected():
    _, _, body = state()
    for change in ({'business_indexes': [0, 0]}, {'business_indexes': [True]}, {'report_revision': True}, {'extra': 1}):
        with pytest.raises(ValidationError): h.PreviewCommand(**{**body.model_dump(), **change})
    with pytest.raises(ValidationError):
        h.HandoffCommand(**body.model_dump(), request_id=uuid4(), preview_hash='a'*64, consent=1)


def test_existing_business_or_second_import_cannot_overwrite_manual_flow():
    report, config, body = state(); name = pr.current_value(report)['result']['businesses'][0]['name']
    config.data['businesses'] = [name]; body = body.model_copy(update={'configuration_hash': digest(config.data)})
    preview = h.preview_value(report, P, config, body)
    assert preview['conflicts'] == [name]
    with pytest.raises(HTTPException):
        h.handoff_value(report, P, config, h.HandoffCommand(**body.model_dump(), request_id=uuid4(),
            preview_hash=preview['preview_hash'], consent=True), str(uuid4()))


def test_report_source_cannot_be_stripped_replaced_or_recreated_after_deletion():
    _, config, _, record, proposed, _ = accepted()
    name = proposed['businesses'][0]
    for change in ('strip', 'replace', 'recreate'):
        after = copy.deepcopy(proposed)
        if change == 'replace': after['flows'][name]['reportSource']['handoff_id'] = str(uuid4())
        else: after['flows'][name].pop('reportSource')
        before = config.data if change == 'recreate' else proposed
        with pytest.raises(HTTPException): h.protect_value(before, after, [record], {}, lambda *_: False)
    removed = copy.deepcopy(proposed); removed['businesses'] = []; removed['flows'] = {}
    assert h.protect_value(proposed, removed, [record], {}, lambda *_: False) == removed


def test_alias_rename_keeps_provenance_and_fact_change_invalidates_source_review():
    _, _, _, record, proposed, _ = accepted(); name = proposed['businesses'][0]
    after = copy.deepcopy(proposed); after['businesses'] = ['改名']; after['businessAliases'] = {'改名': name}
    after['flows'][name]['reportSource']['source_review_id'] = str(uuid4())
    protected = h.protect_value(proposed, after, [record], {}, lambda *_: False)
    flow = h.resolve_flow(protected, '改名')
    assert flow['reportSource'] == {'handoff_id': record['id'], 'candidate_index': 0}
    assert flow['confirmedSignature'] is None
    assert h.fact_hash('改名', flow) != h.fact_hash(name, flow)


def test_forged_fresh_confirmation_is_rejected_but_stale_confirmed_edit_is_saved_unconfirmed():
    _, _, _, record, proposed, _ = accepted(); name = proposed['businesses'][0]
    changed = copy.deepcopy(proposed); changed['flows'][name].update(confirmedSignature='forged', confirmedAt='now')
    with pytest.raises(HTTPException) as error: h.protect_value(proposed, changed, [record], {}, lambda *_: False)
    assert error.value.detail['code'] == 'source_review_required'
    prior = copy.deepcopy(changed); changed['flows'][name]['steps'][0]['purpose'] += '人工修改'
    saved = h.protect_value(prior, changed, [record], {}, lambda *_: False)
    assert saved['flows'][name]['confirmedSignature'] is None
    assert saved['flows'][name]['steps'][0]['purpose'].endswith('人工修改')


def test_exact_quote_mapping_rejects_missing_ambiguous_and_changed_sha():
    _, _, _, record, proposed, _ = accepted()
    material = record['materials'][0]
    quote = record['selection'][0]['source_refs'][0]['quote']
    current = NS(id=material['id'], version=material['version'] + 1, sha256=material['hash'],
        evidence_id=material['evidence_id'], archived=False, status='parsed', data={'blocks': [{'id': 'official1', 'text': quote}]})
    mappings = h.exact_mappings(record, {current.id: current})
    assert mappings and {m['target_block_id'] for m in mappings} == {'official1'}
    current.data['blocks'].append({'id': 'official2', 'text': quote})
    with pytest.raises(HTTPException) as error: h.exact_mappings(record, {current.id: current})
    assert error.value.detail['code'] == 'source_quote_ambiguous'
    current.data['blocks'] = []
    with pytest.raises(HTTPException): h.exact_mappings(record, {current.id: current})


def test_fact_hash_excludes_confirmation_and_current_basis_but_binds_routes_and_roles():
    _, _, _, _, proposed, _ = accepted(); name = proposed['businesses'][0]; flow = proposed['flows'][name]
    before = h.fact_hash(name, flow)
    other = copy.deepcopy(flow); other['confirmedSignature'] = 'x'; other['citations'] = []
    assert h.fact_hash(name, other) == before
    other['steps'][0]['role'] += '修改'
    assert h.fact_hash(name, other) != before


def test_ledger_receipt_scope_and_original_command_are_integrity_checked():
    report, config, command, record, proposed, receipt = accepted()
    data = h.ledger_data(record, command, receipt)
    row = NS(id=record['id'], workspace_id=P.workspace_id, actor_id=P.user_id, report_id=report.id,
        configuration_id=config.id, request_id=str(command.request_id), kind='handoff', parent_id=None, data=data)
    assert h.checked_ledger(row, P)['record'] == record
    bad = copy.deepcopy(row); bad.data['command']['consent'] = False
    with pytest.raises(HTTPException): h.checked_ledger(bad, P)
    bad = copy.deepcopy(row); bad.actor_id = str(uuid4())
    with pytest.raises(HTTPException): h.checked_ledger(bad, P)


def test_original_nonce_replay_precedes_fresh_cas_and_returns_no_adoptable_old_config():
    report, config, command, record, proposed, receipt = accepted()
    row = NS(id=record['id'], workspace_id=P.workspace_id, actor_id=P.user_id, report_id=report.id,
        configuration_id=config.id, request_id=str(command.request_id), kind='handoff', parent_id=None,
        data=h.ledger_data(record, command, receipt))
    assert h.replay_command(row, P, 'handoff', report.id, command) == receipt
    config.version += 10
    reply = h.receipt_reply(row, P, config)
    assert reply['current_matches'] is False and reply['configuration']['revision'] == config.version
    with pytest.raises(HTTPException):
        h.replay_command(row, P, 'handoff', report.id, command.model_copy(update={'report_revision': 999}))


def source_fixture(monkeypatch):
    _, config, _, handoff, proposed, _ = accepted(); config.data = proposed; config.version += 1
    ref = handoff['materials'][0]; quote = handoff['selection'][0]['source_refs'][0]['quote']
    material = NS(id=ref['id'], version=ref['version'] + 1, sha256=ref['hash'], evidence_id=ref['evidence_id'],
        archived=False, status='parsed', data={'blocks': [{'id': 'formal_block', 'text': quote}],
        'ocr_coverage': {'status': 'complete'}})
    calls = []
    def current(db, p, wanted, *, ready=False, lock=False):
        calls.append((copy.deepcopy(wanted), ready, lock))
        if wanted[0]['version'] != material.version: h.fail('source_changed', '版本变化')
        return {material.id: material}
    monkeypatch.setattr(h, 'current_materials', current)
    name = config.data['businesses'][0]; flow = config.data['flows'][name]
    body = h.SourceReviewCommand(request_id=uuid4(), configuration_revision=config.version,
        configuration_hash=digest(config.data), candidate_index=0, fact_hash=h.fact_hash(name, flow),
        material_refs=[{'id': material.id, 'version': material.version, 'hash': material.sha256}], consent=True)
    return config, handoff, material, name, body, calls


def test_real_source_plan_rebinds_native_id_without_granting_confirmation_and_binds_facts(monkeypatch):
    config, handoff, material, name, body, calls = source_fixture(monkeypatch)
    before = copy.deepcopy(config.data)
    proof, proposed, receipt = h.source_review_value(None, P, handoff, config, body, str(uuid4()))
    assert config.data == before and calls[0][1:] == (True, True)
    flow = proposed['flows'][name]
    assert flow['reportSource']['source_review_id'] == proof['id']
    assert flow['confirmedSignature'] is None and flow['confirmedAt'] is None
    assert flow['steps'][0]['citations'][0]['block_id'] == 'formal_block'
    assert handoff['selection'][0]['business']['steps'][0]['citations'][0]['block_id'] != 'formal_block'
    assert h.proof_valid(None, P, proof, handoff, name, flow)
    flow['steps'][0]['role'] += '更改'
    assert not h.proof_valid(None, P, proof, handoff, name, flow)
    assert receipt['source_review_id'] == proof['id'] and receipt['preview_hash'] is None


def test_source_snapshot_version_and_current_basis_invalidate_existing_proof(monkeypatch):
    config, handoff, material, name, body, _ = source_fixture(monkeypatch)
    proof, proposed, _ = h.source_review_value(None, P, handoff, config, body, str(uuid4()))
    changed = copy.deepcopy(proposed['flows'][name]); changed['inferenceReason'] = '后来依据'
    assert not h.proof_valid(None, P, proof, handoff, name, changed)
    material.version += 1
    assert not h.proof_valid(None, P, proof, handoff, name, proposed['flows'][name])


def test_source_plan_coverage_failure_is_zero_mutation_and_not_an_ocr_confirmation(monkeypatch):
    config, handoff, _, _, body, _ = source_fixture(monkeypatch); before = copy.deepcopy(config.__dict__)
    def blocked(*args, **kwargs): h.fail('source_coverage_pending', '需要完整核对')
    monkeypatch.setattr(h, 'current_materials', blocked)
    with pytest.raises(HTTPException) as error: h.source_review_value(None, P, handoff, config, body, str(uuid4()))
    assert error.value.detail['code'] == 'source_coverage_pending' and config.__dict__ == before


def test_current_materials_uses_real_full_gate_not_only_parsed_status(monkeypatch):
    from generic_agents import material_ocr, evidence
    content = b'original report'; sha = h.hashlib.sha256(content).hexdigest()
    m = NS(id=str(uuid4()), version=4, sha256=sha, evidence_id=str(uuid4()), archived=False, status='parsed')
    monkeypatch.setattr(h, 'scoped', lambda *a: m)
    monkeypatch.setattr(evidence, 'evidence_bytes', lambda *a: (NS(sha256=sha), content))
    called = []
    def full_gate(db, p, material):
        called.append(material.id); raise HTTPException(409, '完整图像未确认')
    monkeypatch.setattr(material_ocr, 'ensure_publishable', full_gate)
    with pytest.raises(HTTPException) as error:
        h.current_materials(None, P, [{'id': m.id, 'hash': sha, 'version': 4}], ready=True)
    assert called == [m.id] and error.value.detail['code'] == 'source_coverage_pending'


def test_source_review_nonce_replay_retains_original_proof_after_later_config_edit(monkeypatch):
    config, handoff, _, _, command, _ = source_fixture(monkeypatch)
    proof, proposed, receipt = h.source_review_value(None, P, handoff, config, command, str(uuid4()))
    row = NS(id=proof['id'], workspace_id=P.workspace_id, actor_id=P.user_id, report_id=proof['report_id'],
        configuration_id=config.id, request_id=str(command.request_id), kind='source_review', parent_id=handoff['id'],
        data=h.ledger_data(proof, command, receipt))
    assert h.replay_command(row, P, 'source_review', handoff['id'], command) == receipt
    config.version += 4
    assert not h.receipt_reply(row, P, config)['current_matches']
    with pytest.raises(HTTPException): h.replay_command(row, P, 'handoff', proof['report_id'], command)


def test_edited_facts_cannot_inherit_original_model_citations(monkeypatch):
    report, config, body = state()
    value = pr.current_value(report); value['result']['businesses'][0]['steps'][0]['purpose'] += '人工改写'
    ledger = Q['state']()[1]
    # Actual private save uses the original ledger binding, not a new fake call.
    expected = report.data['local_review']['call_binding']
    ledger.id = expected['call_id']; ledger.data = {**expected['expected'],
        'input_policy_hash': digest(report.data['preview']['generation_contract']['input_policy']),
        'receipt': report.data['raw_response']['receipt']}
    ledger.reserved_tokens = report.data['preview']['input_summary']['token_bound']
    command = h.rd.ReviewCommand(revision=report.version, request_id=uuid4(), value=value)
    receipt = pr.record_partial_review(report, P, command, lambda: ledger)
    body = body.model_copy(update={'report_revision': report.version, 'review_receipt_hash': receipt['receipt_hash'],
        'candidate_hash': digest(value['result']), 'validation_report_hash': report.data['validation_report']['report_hash']})
    preview = h.preview_value(report, P, config, body)
    record, proposed, _ = h.handoff_value(report, P, config, h.HandoffCommand(**body.model_dump(), request_id=uuid4(),
        preview_hash=preview['preview_hash'], consent=True), str(uuid4()))
    step = next(iter(proposed['flows'].values()))['steps'][0]
    assert step['citations'] == [] and step['inferenceReason'] == ''
    assert step['basisHistory'] and record['selection'][0]['source_refs']


def test_restore_and_compiler_check_effective_alias_and_reject_stripped_historical_marker(monkeypatch):
    _, _, _, record, proposed, _ = accepted(); name = proposed['businesses'][0]
    config = copy.deepcopy(proposed); config['businesses'] = ['别名']; config['businessAliases'] = {'别名': name}
    monkeypatch.setattr(h, 'all_records', lambda *_: [record])
    assert h.configuration_source_issues(None, P, config)['别名']
    historical = copy.deepcopy(config); historical['flows'][name].pop('reportSource')
    with pytest.raises(HTTPException): h.protect_configuration_sources(None, P, config, historical, restore=True)
    assert h.configuration_source_issues(None, P, historical)['别名']


def test_source_material_rechecked_at_activation_even_if_release_was_compiled(monkeypatch):
    config, handoff, material, name, body, _ = source_fixture(monkeypatch)
    proof, proposed, receipt = h.source_review_value(None, P, handoff, config, body, str(uuid4()))
    monkeypatch.setattr(h, 'all_records', lambda *_: [handoff, proof])
    pins = h.configuration_source_snapshots(None, P, proposed)
    row = NS(data={'record': proof})
    monkeypatch.setattr(h, 'owned_record', lambda *a, **k: row)
    source = {'id': material.id, 'report_sources': pins[material.id]}
    h.verify_report_material_snapshot(None, P, source)
    material.data['ocr_coverage'] = {'status': 'blocked'}
    with pytest.raises(HTTPException): h.verify_report_material_snapshot(None, P, source)


def test_actual_handoff_handler_commits_one_ledger_revision_audit_then_replays_without_fresh_source(monkeypatch):
    report, config, preview_body = state(); private_before = copy.deepcopy(report.__dict__)
    preview = h.preview_value(report, P, config, preview_body)
    body = h.HandoffCommand(**preview_body.model_dump(), request_id=uuid4(), preview_hash=preview['preview_hash'], consent=True)
    binding = report.data['local_review']['call_binding']
    call = NS(id=binding['call_id'], workspace_id=P.workspace_id, capability_id=binding['expected']['capability_id'],
        status=binding['status'], reserved_tokens=binding['reserved_tokens'], charged_tokens=binding['charged_tokens'],
        data={**binding['expected'], 'input_policy_hash': digest(report.data['preview']['generation_contract']['input_policy']),
              'receipt': report.data['raw_response']['receipt']})
    class DB:
        def __init__(self): self.added = []; self.executed = []; self.commits = 0
        def execute(self, *args): self.executed.append(args)
        def add(self, row): self.added.append(row)
        def commit(self): self.commits += 1
    db = DB(); audits = []; auth = []
    monkeypatch.setattr(h, 'configuration_row', lambda *a, **k: config)
    monkeypatch.setattr(h.rd, 'owned', lambda *a: report)
    monkeypatch.setattr(h, 'request_row', lambda *_: next((r for r in db.added if isinstance(r, h.Record)), None))
    monkeypatch.setattr(h, 'all_records', lambda *_: [])
    monkeypatch.setattr(h, 'scoped', lambda *a: call)
    monkeypatch.setattr(h, 'current_materials', lambda *a, **k: {})
    monkeypatch.setattr(h.setup(), 'automation_review_actor', lambda *a: auth.append(P.user_id))
    monkeypatch.setattr(h, 'audit', lambda *args: audits.append(args[2:]))
    result = h.submit_handoff(UUID(report.id), body, P, db)
    assert result['current_matches'] and db.commits == 1 and len(auth) == len(audits) == 1
    assert len([r for r in db.added if isinstance(r, h.Record)]) == 1
    assert len([r for r in db.added if isinstance(r, h.SetupRevision)]) == 1
    assert report.__dict__ == private_before and 'note' not in json.dumps(audits, ensure_ascii=False)
    monkeypatch.setattr(h.rd, 'owned', lambda *a: pytest.fail('old nonce must not fresh-load report'))
    monkeypatch.setattr(h, 'current_materials', lambda *a, **k: pytest.fail('old nonce must not recheck live source'))
    config.version += 1
    replay = h.submit_handoff(UUID(report.id), body, P, db)
    assert replay['receipt'] == result['receipt'] and not replay['current_matches'] and db.commits == 1
    with pytest.raises(HTTPException):
        h.submit_handoff(UUID(report.id), body.model_copy(update={'report_revision': 999}), P, db)
    foreign = type(P)(str(uuid4()), P.workspace_id, 'operator', 'other-session')
    with pytest.raises(HTTPException): h.submit_handoff(UUID(report.id), body, foreign, db)
    assert db.commits == 1 and report.__dict__ == private_before


def test_actual_handlers_viewer_cannot_mutate_and_model_declaration_is_additive():
    from sqlalchemy import DateTime, String
    from sqlalchemy.dialects.postgresql import JSONB
    from generic_agents import signal_dual_models, report_quote_binding_models
    from generic_agents.models import Base
    from generic_agents.typed_payload_orm_v131 import CanonicalJSONB
    viewer = type(P)(P.user_id, P.workspace_id, 'viewer', P.session_id)
    report, config, body = state()
    with pytest.raises(HTTPException) as error: h.preview_handoff(UUID(report.id), body, viewer, None)
    assert error.value.status_code == 403
    assert 'setup_requirement_checks' in Base.metadata.tables
    assert {'report_configuration_records', 'signal_dual_handoffs', 'report_quote_binding_drafts'} <= set(Base.metadata.tables)
    assert h.Record.__table__.name == 'report_configuration_records'
    # Other feature modules register tables lazily. The ledger's replay and
    # tenant-boundary contract must hold regardless of their import order.
    columns = h.Record.__table__.c
    identifiers = {'id', 'workspace_id', 'actor_id', 'report_id', 'configuration_id', 'request_id', 'parent_id'}
    assert identifiers | {'created_at', 'kind', 'data'} <= set(columns.keys())
    for name in identifiers:
        assert isinstance(columns[name].type, String) and columns[name].type.length == 36
        assert columns[name].nullable is (name == 'parent_id')
    assert columns.id.primary_key and tuple(h.Record.__table__.primary_key.columns.keys()) == ('id',)
    assert isinstance(columns.kind.type, String) and columns.kind.type.length == 20 and not columns.kind.nullable
    assert isinstance(columns.created_at.type, DateTime) and columns.created_at.type.timezone is True
    assert columns.created_at.nullable is False
    assert isinstance(columns.data.type, CanonicalJSONB) and isinstance(columns.data.type.impl, JSONB)
    assert columns.data.nullable is False
    assert {name: {key.target_fullname for key in columns[name].foreign_keys} for name in
            ('workspace_id', 'actor_id', 'report_id', 'configuration_id', 'parent_id')} == {
        'workspace_id': {'workspaces.id'}, 'actor_id': {'users.id'},
        'report_id': {'setup_report_drafts.id'}, 'configuration_id': {'setup_drafts.id'},
        'parent_id': {'report_configuration_records.id'}}
    assert {tuple(c.name for c in constraint.columns) for constraint in h.Record.__table__.constraints
            if constraint.__class__.__name__ == 'UniqueConstraint'} == {('workspace_id', 'request_id')}


def test_rename_rebuild_every_step_cannot_remove_origin_in_same_save():
    _, _, _, record, proposed, _ = accepted(); name = proposed['businesses'][0]
    replacement = copy.deepcopy(proposed['flows'][name]); replacement.pop('reportSource')
    for step in replacement['steps']: step['id'] = str(uuid4())
    after = {**proposed, 'businesses': ['换名业务'], 'flows': {'换名业务': replacement}}
    with pytest.raises(HTTPException) as error: h.protect_value(proposed, after, [record], {}, lambda *_: False)
    assert error.value.detail['code'] == 'source_origin_replacement'


def test_historical_restore_does_not_revive_even_currently_matching_source_confirmation(monkeypatch):
    from generic_agents import report_full_semantic_handoff as full
    monkeypatch.setattr(full, 'shared_records', lambda *_: [])
    config, handoff, _, name, body, _ = source_fixture(monkeypatch)
    proof, proposed, _ = h.source_review_value(None, P, handoff, config, body, str(uuid4()))
    proposed['flows'][name].update(confirmedSignature='previous-confirmation', confirmedAt='then')
    monkeypatch.setattr(h, 'all_records', lambda *_: [handoff, proof])
    restored = h.protect_configuration_sources(None, P, proposed, proposed, restore=True)
    assert 'source_review_id' not in restored['flows'][name]['reportSource']
    assert restored['flows'][name]['confirmedSignature'] is None
