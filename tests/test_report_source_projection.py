"""Projection chain tests: real provenance checks, explicit synthetic OCR adapter."""
import copy
import json
from pathlib import Path
import runpy
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from generic_agents import report_configuration_handoff as h
from generic_agents import report_source_projection as projection
from generic_agents import document_copy, material_ocr
from generic_agents.contracts import digest
from generic_agents.models import Job, SetupMaterial

Q = runpy.run_path(str(Path(__file__).with_name('test_report_configuration_handoff_pure.py')))
P = Q['P']


Command = h.ProjectedSourceReviewCommand


def state(monkeypatch):
    report, config, _, handoff, proposed, _ = Q['accepted']()
    config.data = proposed; config.version += 1
    source = handoff['materials'][0]
    origin = NS(id=source['id'], name=source['name'], workspace_id=P.workspace_id, version=source['version'],
        sha256=source['hash'], evidence_id=source['evidence_id'], archived=False, status='needs_input', data={'blocks': []})
    copy_id, ev_id, confirmation_id = str(uuid4()), str(uuid4()), str(uuid4())
    quote = handoff['selection'][0]['source_refs'][0]['quote']; request_id = str(uuid4()); job_id = str(uuid4())
    receipt = {'source_sha256': origin.sha256, 'pdf_sha256': 'b'*64, 'engine_id': 'libreoffice-docx-final-v1',
        'manifest_hash': 'c'*64, 'revision_policy': 'accepted_final_v1', 'page_count': 1}
    provenance = {'contract_version': 1, 'copy_material_id': copy_id, 'workspace_id': P.workspace_id,
        'copy_evidence_id': ev_id, 'copy_name': '最终修订.pdf', 'source_material_id': origin.id,
        'source_sha256': origin.sha256, 'source_material_version': origin.version, 'source_name': origin.name,
        'source_evidence_id': origin.evidence_id, 'request_id': request_id, 'job_id': job_id,
        'request_body_hash': '', 'revision_policy': 'accepted_final_v1', 'engine_id': 'libreoffice-docx-final-v1',
        'engine_manifest_hash': 'c'*64, 'pdf_sha256': 'b'*64, 'receipt': receipt}
    body = {'request_id': request_id, 'expected_material_version': origin.version, 'source_sha256': origin.sha256,
        'engine_manifest_hash': 'c'*64, 'revision_policy': 'accepted_final_v1'}
    provenance['request_body_hash'] = digest(body); provenance['integrity_hash'] = digest(provenance)
    cov = {'contract_version': 2, 'source_sha256': 'b'*64, 'status': 'needs_ocr', 'unit_count': 1,
        'units': [{'kind': 'pdf_page', 'index': 1, 'page': 1}], 'gaps': [], 'reason': None,
        'profile': {'kind': 'document_copy_full_pages_v1', 'provenance_hash': provenance['integrity_hash']}, 'force_all_pages': True}
    cov['hash'] = digest(cov)
    block = {'id': 'ocr-confirmed-1', 'text': quote, 'kind': 'ocr_confirmed',
        'location': {'page': 1, 'ocr': {'confirmation_id': confirmation_id}}}
    copy_material = NS(id=copy_id, name='最终修订.pdf', workspace_id=P.workspace_id, version=7,
        sha256='b'*64, evidence_id=ev_id, archived=False, status='parsed', data={'document_copy': provenance,
        'ocr_coverage': cov, 'ocr_confirmation_id': confirmation_id, 'blocks': [block], 'ocr_run_id': str(uuid4())})
    snapshot = {'scope': {'workspace_id': P.workspace_id, 'material_id': copy_id, 'evidence_id': ev_id,
        'source_sha256': 'b'*64, 'name': copy_material.name}, 'coverage': cov,
        'units': [{'index': 1, 'unit_id': str(uuid4()), 'revision_id': str(uuid4()), 'revision_hash': 'd'*64, 'image_sha256': 'e'*64}]}
    confirmation = NS(id=confirmation_id, material_id=copy_id, material_version=7, content_hash=digest(snapshot),
        data={'snapshot': snapshot, 'blocks': [block], 'blocks_hash': digest([block])})
    jd = {'actor_id': P.user_id, 'actor_role': P.role, 'source_material_id': origin.id,
        'source_evidence_id': origin.evidence_id, 'source_name': origin.name, 'source_sha256': origin.sha256,
        'source_material_version': origin.version, 'engine_id': 'libreoffice-docx-final-v1',
        'engine_manifest_hash': 'c'*64, 'request_body': body, 'request_body_hash': digest(body),
        'copy_status': 'succeeded', 'request_version': 3, 'requested_at': 'synthetic',
        'output_material_id': copy_id, 'receipt': receipt, 'provenance': provenance}
    jd['request_scope_hash'] = document_copy.scope_hash(jd)
    job = NS(id=job_id, workspace_id=P.workspace_id, kind=document_copy.KIND,
        idempotency_key=document_copy.key(origin.id, request_id), data=jd)
    class DB:
        def scalars(self, query):
            model = query.column_descriptions[0].get('entity')
            return [job] if model is Job else [origin, copy_material]
    db = DB(); materials = {origin.id: origin, copy_id: copy_material}; seen = []
    def current(db, p, refs, *, ready=False, lock=False):
        result = {}
        for ref in refs:
            m = materials.get(ref['id'])
            if m is None or m.workspace_id != p.workspace_id or m.sha256 != ref['hash'] or m.version != ref['version'] or m.archived:
                h.fail('source_changed', '材料变化')
            if ready and m.status != 'parsed': h.fail('source_coverage_pending', '待核页')
            seen.append((m.id, ready)); result[m.id] = m
        return result
    monkeypatch.setattr(h, 'current_materials', current)
    monkeypatch.setattr(material_ocr, 'checked_confirmation', lambda *a: confirmation)
    monkeypatch.setattr(material_ocr, 'material_summary', lambda *a: {'total': 1, 'confirmed': 1 if copy_material.status == 'parsed' else 0})
    monkeypatch.setattr(h, 'scoped', lambda db, model, identity, p, *a: materials[identity])
    name = config.data['businesses'][0]
    command = Command(request_id=uuid4(), configuration_revision=config.version, configuration_hash=digest(config.data),
        candidate_index=0, fact_hash=h.fact_hash(name, config.data['flows'][name]),
        material_refs=[{'id': origin.id, 'version': origin.version, 'hash': origin.sha256}], consent=True,
        review_mode='confirmed_final_view_v1', final_view_confirmed=True,
        projection_refs=[{'source_material_id': origin.id, 'copy_ref': {'id': copy_id, 'version': 7, 'hash': 'b'*64},
                          'provenance_hash': provenance['integrity_hash']}])
    return NS(db=db, report=report, config=config, handoff=handoff, origin=origin, copy=copy_material,
        confirmation=confirmation, job=job, body=command, name=name, seen=seen, materials=materials)


def test_confirmed_copy_projection_keeps_word_unchanged_and_rebinds_every_formal_source(monkeypatch):
    s = state(monkeypatch); before = copy.deepcopy((s.report.__dict__, s.origin.__dict__, s.config.__dict__))
    proof, proposed, receipt = projection.projected_review_value(s.db, P, s.handoff, s.config, s.body, str(uuid4()))
    flow = proposed['flows'][s.name]
    assert before == (s.report.__dict__, s.origin.__dict__, s.config.__dict__)
    assert flow['steps'][0]['sourceId'] == s.copy.id and flow['steps'][0]['citations'][0]['material_id'] == s.copy.id
    assert flow['citations'][0]['block_id'] == 'ocr-confirmed-1'
    assert not flow['confirmedSignature'] and not flow['confirmedAt']
    assert {m['role'] for m in proof['materials']} == {'origin', 'confirmed_view'}
    assert proof['material_refs'] == s.body.model_dump(mode='json')['material_refs']
    assert proof['projection']['projection_refs'] == s.body.model_dump(mode='json')['projection_refs']
    assert receipt['kind'] == 'source_review'
    assert (s.origin.id, True) not in s.seen and (s.copy.id, True) in s.seen
    assert projection.verify_projection(s.db, P, proof, s.handoff, s.name, flow)
    assert projection.verify_projection(s.db, P, proof, s.handoff)


@pytest.mark.parametrize('field', ['source_material_id', 'source_sha256', 'source_material_version',
    'source_evidence_id', 'source_name', 'revision_policy'])
def test_wrong_origin_in_both_provenance_copies_still_rejected(monkeypatch, field):
    s = state(monkeypatch); prov = s.copy.data['document_copy']
    prov[field] = 999 if field == 'source_material_version' else 'wrong'
    prov['integrity_hash'] = digest({k: v for k, v in prov.items() if k != 'integrity_hash'})
    s.body.projection_refs[0].provenance_hash = prov['integrity_hash']
    # Both server records are changed together to exercise the actual parent binding.
    s.job.data['provenance'] = prov
    with pytest.raises(HTTPException): projection.projected_review_value(s.db, P, s.handoff, s.config, s.body, str(uuid4()))


def test_provenance_missing_or_job_disagreement_cannot_be_treated_as_plain_uploaded_pdf(monkeypatch):
    s = state(monkeypatch); s.copy.data.pop('document_copy')
    with pytest.raises(HTTPException): projection.projected_review_value(s.db, P, s.handoff, s.config, s.body, str(uuid4()))
    s = state(monkeypatch); s.job.data['receipt'] = {'fake': True}
    with pytest.raises(HTTPException): projection.projected_review_value(s.db, P, s.handoff, s.config, s.body, str(uuid4()))


@pytest.mark.parametrize('change', ['not_confirmed', 'no_full_pages', 'wrong_confirmation', 'wrong_pages', 'archived'])
def test_incomplete_or_unrelated_copy_never_authorizes_word(monkeypatch, change):
    s = state(monkeypatch)
    if change == 'not_confirmed': s.copy.status = 'needs_input'; s.copy.data.pop('ocr_confirmation_id')
    elif change == 'no_full_pages': s.copy.data['ocr_coverage']['force_all_pages'] = False
    elif change == 'wrong_confirmation': s.confirmation.material_id = str(uuid4())
    elif change == 'wrong_pages': s.confirmation.data['snapshot']['units'] = []
    else: s.copy.archived = True
    with pytest.raises(HTTPException): projection.projected_review_value(s.db, P, s.handoff, s.config, s.body, str(uuid4()))


def test_quote_ambiguity_or_cross_region_text_is_not_matched_by_first_or_fuzzy(monkeypatch):
    s = state(monkeypatch); duplicate = copy.deepcopy(s.copy.data['blocks'][0]); duplicate['id'] = 'second'
    s.copy.data['blocks'].append(duplicate); s.confirmation.data['blocks'] = s.copy.data['blocks']
    s.confirmation.data['blocks_hash'] = digest(s.copy.data['blocks'])
    with pytest.raises(HTTPException) as error: projection.projected_review_value(s.db, P, s.handoff, s.config, s.body, str(uuid4()))
    assert error.value.detail['code'] == 'projection_quote_ambiguous'
    s = state(monkeypatch); text = s.copy.data['blocks'][0]['text']; s.copy.data['blocks'][0]['text'] = text[:len(text)//2]
    s.copy.data['blocks'].append({'id': 'rest', 'text': text[len(text)//2:], 'location': {'page': 1}})
    s.confirmation.data['blocks'] = s.copy.data['blocks']; s.confirmation.data['blocks_hash'] = digest(s.copy.data['blocks'])
    with pytest.raises(HTTPException) as error: projection.projected_review_value(s.db, P, s.handoff, s.config, s.body, str(uuid4()))
    assert error.value.detail['code'] == 'projection_quote_missing'


def test_cold_verification_rechecks_both_sides_and_mapping_not_only_self_hash(monkeypatch):
    s = state(monkeypatch)
    proof, proposed, _ = projection.projected_review_value(s.db, P, s.handoff, s.config, s.body, str(uuid4()))
    assert projection.verify_projection(s.db, P, proof, s.handoff)
    s.origin.version += 1
    assert not projection.verify_projection(s.db, P, proof, s.handoff)
    s.origin.version -= 1
    tampered = copy.deepcopy(proof); tampered['mappings'][0]['target_block_id'] = 'different'
    tampered['record_hash'] = digest({k: v for k, v in tampered.items() if k != 'record_hash'})
    assert not projection.verify_projection(s.db, P, tampered, s.handoff)


def test_current_changed_fact_or_basis_does_not_reuse_projection_confirmation(monkeypatch):
    s = state(monkeypatch)
    proof, proposed, _ = projection.projected_review_value(s.db, P, s.handoff, s.config, s.body, str(uuid4()))
    flow = proposed['flows'][s.name]; flow['steps'][0]['role'] += '后来修改'
    assert not projection.verify_projection(s.db, P, proof, s.handoff, s.name, flow)


def test_options_show_unconfirmed_copy_with_navigation_id_and_reason_without_writes(monkeypatch):
    s = state(monkeypatch); s.copy.status = 'needs_input'; s.copy.data.pop('ocr_confirmation_id')
    before = copy.deepcopy((s.copy.__dict__, s.origin.__dict__, s.job.__dict__))
    options = projection.projection_options(s.db, P, s.handoff, 0)
    assert options['options_hash'] == digest({k: v for k, v in options.items() if k != 'options_hash'})
    item = options['options'][0]
    assert item['copy_ref']['id'] == s.copy.id and not item['ready'] and item['reason']
    assert item['source_material_id'] == s.origin.id and item['page_count'] == 1
    assert before == (s.copy.__dict__, s.origin.__dict__, s.job.__dict__)


def test_options_ready_requires_real_provenance_and_complete_confirmation(monkeypatch):
    s = state(monkeypatch)
    assert projection.projection_options(s.db, P, s.handoff, 0)['options'][0]['ready']
    s.copy.data['document_copy']['receipt']['page_count'] = 2
    assert not projection.projection_options(s.db, P, s.handoff, 0)['options'][0]['ready']


def test_unprojected_ordinary_pdf_keeps_existing_full_gate_and_formal_snapshot(monkeypatch):
    s = state(monkeypatch)
    extra = NS(id=str(uuid4()), workspace_id=P.workspace_id, name='补充资料.pdf', version=2, sha256='f'*64,
        evidence_id=str(uuid4()), status='parsed', archived=False, data={'blocks': [{'id': 'pdf1', 'text': '补充资料'}]})
    s.materials[extra.id] = extra
    s.handoff['materials'].append({'id': extra.id, 'name': extra.name, 'version': extra.version,
        'hash': extra.sha256, 'evidence_id': extra.evidence_id})
    s.handoff['record_hash'] = digest({k: v for k, v in s.handoff.items() if k != 'record_hash'})
    refs = [*s.body.material_refs, h.rd.MaterialRef(id=extra.id, version=extra.version, hash=extra.sha256)]
    s.body = s.body.model_copy(update={'material_refs': refs})
    proof, proposed, _ = projection.projected_review_value(s.db, P, s.handoff, s.config, s.body, str(uuid4()))
    assert {m['role'] for m in proof['materials']} == {'origin', 'confirmed_view', 'unprojected'}
    assert (extra.id, True) in s.seen
    assert projection.verify_projection(s.db, P, proof, s.handoff)
    extra.status = 'needs_input'
    assert not projection.verify_projection(s.db, P, proof, s.handoff)


def test_actual_projected_command_is_strict_and_old_source_contract_unchanged(monkeypatch):
    s = state(monkeypatch); body = s.body.model_dump(mode='json')
    for change in ({'final_view_confirmed': 1}, {'final_view_confirmed': False},
                   {'projection_refs': body['projection_refs'] * 2}, {'review_mode': 'other'}, {'extra': 'unknown'}):
        with pytest.raises(ValidationError): Command.model_validate({**body, **change})
    old = h.SourceReviewCommand.model_validate({k: v for k, v in body.items() if k in h.SourceReviewCommand.model_fields})
    assert set(old.model_dump(mode='json')) == set(h.SourceReviewCommand.model_fields)
    assert 'projection_refs' not in old.model_dump(mode='json')


def test_actual_typed_ledger_replays_original_projection_body_not_old_source_body(monkeypatch):
    s = state(monkeypatch)
    proof, proposed, receipt = projection.projected_review_value(s.db, P, s.handoff, s.config, s.body, str(uuid4()))
    row = NS(id=proof['id'], workspace_id=P.workspace_id, actor_id=P.user_id, report_id=proof['report_id'],
        configuration_id=s.config.id, request_id=str(s.body.request_id), kind='source_review', parent_id=s.handoff['id'],
        data=h.ledger_data(proof, s.body, receipt))
    assert h.checked_ledger(row, P)['record'] == proof
    assert h.replay_command(row, P, 'source_review', s.handoff['id'], s.body) == receipt
    old = h.SourceReviewCommand.model_validate({k: v for k, v in s.body.model_dump(mode='json').items()
        if k in h.SourceReviewCommand.model_fields})
    with pytest.raises(HTTPException): h.replay_command(row, P, 'source_review', s.handoff['id'], old)
    bad = copy.deepcopy(row); bad.data['record']['projection'] = None
    bad.data['record']['record_hash'] = digest({k: v for k, v in bad.data['record'].items() if k != 'record_hash'})
    bad.data['receipt']['record_hash'] = bad.data['record']['record_hash']
    bad.data['receipt']['receipt_hash'] = digest({k: v for k, v in bad.data['receipt'].items() if k != 'receipt_hash'})
    bad.data['data_hash'] = digest({k: v for k, v in bad.data.items() if k != 'data_hash'})
    with pytest.raises(HTTPException): h.checked_ledger(bad, P)


def test_bad_provenance_hash_is_explained_without_breaking_options_dto(monkeypatch):
    s = state(monkeypatch); s.copy.data['document_copy']['integrity_hash'] = 'invalid'
    value = projection.projection_options(s.db, P, s.handoff, 0)
    assert value['options'][0]['provenance_hash'] is None
    assert not value['options'][0]['ready'] and value['options'][0]['reason_code']
