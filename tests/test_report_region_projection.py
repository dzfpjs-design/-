"""Typed region v2: synthetic PG lifecycle and legacy pure compatibility.

No real model/converter/OCR quality is claimed.  The shared quote_scene uses one
new local fake-provider callback in an empty owned database, followed by genuine
review, handoff, source confirmation, compilation and Task APIs.
"""
import copy
import json
import runpy
from pathlib import Path
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session

from generic_agents import report_configuration_handoff as h
from generic_agents import report_region_projection as region
from generic_agents import report_source_projection as legacy
from generic_agents import report_source_region_binding as binding
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.instance_compiler import signature_value
from generic_agents.models import Membership, SetupMaterial, Task
from generic_agents.security import Principal
from tests.test_instance_workspace import OBJECTS, enable, object_row, save
from tests.test_report_quote_binding import (
    QUOTE, ROLE, confirm_copy, draft_body, local_runtime, opaque_state,
    quote_scene, refresh_options, reply, save_selection,
)


def formal_body(scene, options, saved):
    draft = saved['draft']
    return {'request_id': str(uuid4()), 'configuration_revision': options['configuration']['revision'],
        'configuration_hash': options['configuration']['hash'], 'candidate_index': 0,
        'fact_hash': options['configuration']['fact_hash'],
        'material_refs': [item['anchor']['source_ref'] for item in options['copies']], 'consent': True,
        'review_mode': 'confirmed_final_view_regions_v2', 'final_view_confirmed': True,
        'projection_refs': [{'source_material_id': item['anchor']['source_ref']['id'],
            'copy_ref': item['copy_ref'], 'provenance_hash': item['anchor']['provenance_hash']} for item in options['copies']],
        'quote_binding_draft_ref': {key: draft[key] for key in ('id', 'revision', 'value_hash', 'draft_hash')},
        'region_bindings': copy.deepcopy(draft['value']['bindings'])}


def approved_source(scene):
    confirm_copy(scene)
    options = refresh_options(scene)
    assert options['copies'][0]['whole_document_confirmed'] is True
    path, body, saved = save_selection(scene, options=options)
    command = formal_body(scene, options, saved)
    approved = reply(scene['client'].post(scene['base'] + '/region-source-review', json=command))
    return path, body, saved, command, approved


def principal(scene):
    # Pure service readers use the same actor as the genuinely logged-in client.
    return Principal(scene['owner']['id'], scene['workspace_id'], 'operator', 'synthetic-reader-only')


def test_confirmed_multiregion_source_compiles_activates_and_real_role_account_completes_task(quote_scene):
    scene = quote_scene; client = scene['client']
    path, _, saved, command, approved = approved_source(scene)
    proof = approved['source_review']
    assert proof['projection']['schema'] == region.SCHEMA
    assert proof['projection']['region_bindings'] == command['region_bindings']
    assert set(proof['binding_context']) == {'anchor', 'configuration_revision', 'configuration_hash',
        'fact_hash', 'mapping_basis_hash', 'copy_refs'}
    assert 'history' not in proof['binding_context'] and 'note' not in proof['binding_context']
    configuration = approved['configuration']['configuration']; name = '合成巡护记录核验'
    flow = configuration['flows'][name]
    assert flow['confirmedAt'] is None and flow['confirmedSignature'] is None
    assert all(c['quote'] == QUOTE and len(c['span_binding_id']) == 64
               for c in [*flow['citations'], *flow['steps'][0]['citations']])
    assert all(c['material_id'] == scene['copy']['id'] for c in flow['citations'])
    with Session(engine()) as db:
        origin = db.get(SetupMaterial, scene['origin']['id']); pdf = db.get(SetupMaterial, scene['copy']['id'])
        assert origin.status == 'needs_input'
        assert not any(QUOTE in block['text'] for block in pdf.data['blocks'])
    # No fake concatenated formal block exists. The complete quote needs v2.
    before = opaque_state()
    replay = reply(client.post(scene['base'] + '/region-source-review', json=command))
    assert replay['receipt'] == approved['receipt']
    assert opaque_state() == before
    formal_id = proof['id']; first_binding = command['region_bindings'][0]
    endpoint = scene['base'] + '/source-reviews/' + formal_id + '/region-bindings/' + digest(first_binding)
    public = reply(scene['other'].get(endpoint))
    assert public['binding'] == first_binding and len(public['regions']) == 2
    assert public['unit']['image_sha256'] == first_binding['spans'][0]['image_sha256']
    reply(scene['other'].get(path), 404)
    # A later private draft revision cannot revoke/mutate the issued shared
    # proof. This is a real new private save, not a monkeypatch of the verifier.
    newer = draft_body(refresh_options(scene), revision=saved['draft']['revision'], bindings=[])
    newer_reply = reply(client.put(path, json=newer))
    assert newer_reply['draft']['revision'] == 2
    assert reply(scene['other'].get(endpoint)) == public
    configuration = reply(client.get('/api/setup/draft'))['configuration']
    configuration['instanceProfile']['goal'] = '人工核对合成巡护登记并保存处理记录'
    configuration['automation'] = {'version': 1, 'policies': {name: {'rules': {'write': {'mode': '仅给出建议'}}}}}
    flow = configuration['flows'][name]
    flow['confirmedAt'] = '2026-10-16T00:00:00+00:00'
    flow['confirmedSignature'] = json.dumps(signature_value(configuration, flow), ensure_ascii=False)
    revision = save(client, configuration, review_automation=True)
    compilation = {'revision': revision, 'objects': OBJECTS, 'role_bindings': {ROLE: scene['other_user']['id']}}
    checked = reply(client.post('/api/instances/check', json=compilation))
    release = reply(scene['admin'].post('/api/instances', json={**compilation,
        'confirmed_configuration_hash': checked['configuration_hash'], 'confirm_configuration': True,
        'generation_intent': 'new_instance', 'request_id': str(uuid4())}), 201)
    assert len(release['available_business_ids']) == 1
    assert release['snapshot']['roles'][ROLE]['user_id'] == scene['other_user']['id']
    enable(scene['admin'], release)
    person = scene['other']; obj = object_row(person, release)
    task = reply(person.post('/api/workspace/instances/' + release['instance_id'] + '/tasks', json={
        'release_id': release['id'], 'business_id': release['available_business_ids'][0],
        'entity_ids': [obj['id']], 'title': '合成多区域来源核对', 'request_id': str(uuid4())}), 201)
    with Session(engine()) as db:
        snapshots = db.get(Task, task['id']).data['material_snapshots']
        assert [m['id'] for m in snapshots] == [scene['copy']['id']]
        assert snapshots[0]['report_sources']
    task_path = '/api/workspace/tasks/' + task['id']
    current = reply(person.get(task_path)); step_id = current['workflow_runtime']['current_step_id']
    note = reply(person.put(task_path + '/note', json={'version': current['version'], 'step_id': step_id,
        'note': '实际责任账号对照正式跨区域引文核对，合成 fixture。'}))
    assert reply(person.get(task_path))['workflow_runtime']['drafts'][step_id]['note'].endswith('fixture。')
    completed = reply(person.post(task_path + '/complete-step', json={'version': note['version'],
        'step_id': step_id, 'note': '合成业务人工核对完成', 'request_id': str(uuid4())}))
    assert completed['status'] == 'workflow_completed'
    assert reply(person.get(task_path))['status'] == 'workflow_completed'


@pytest.mark.parametrize('mutation', ['reverse', 'page', 'revision', 'image', 'box', 'offset', 'text_hash'])
def test_private_span_rejects_mixed_page_chain_offsets_or_resealed_content(quote_scene, mutation):
    scene = quote_scene; options = refresh_options(scene); body = draft_body(options)
    span = body['value']['bindings'][0]['spans'][0]
    if mutation == 'reverse': body['value']['bindings'][0]['spans'].reverse()
    elif mutation == 'page': span['page'] += 1
    elif mutation == 'revision': span['revision_id'] = str(uuid4())
    elif mutation == 'image': span['image_sha256'] = '0' * 64
    elif mutation == 'box': span['box'][0] = '1.1'
    elif mutation == 'offset': span['end'] -= 1
    else: span['region_text_hash'] = '0' * 64
    before = opaque_state()
    reply(scene['client'].put(scene['base'] + '/quote-binding-drafts/' + str(uuid4()), json=body), 409)
    assert opaque_state() == before


def test_formal_review_requires_exact_saved_complete_selection_and_does_not_mutate_private_draft(quote_scene):
    scene = quote_scene; confirm_copy(scene); options = refresh_options(scene)
    _, _, saved = save_selection(scene, options=options, bindings=[])
    full = draft_body(options)['value']['bindings']
    command = formal_body(scene, options, saved); command['region_bindings'] = full
    before = opaque_state()
    reply(scene['client'].post(scene['base'] + '/region-source-review', json=command), 409)
    assert opaque_state() == before
    _, _, complete = save_selection(scene, options=options)
    command = formal_body(scene, options, complete); command['region_bindings'] = command['region_bindings'][:-1]
    before = opaque_state()
    reply(scene['client'].post(scene['base'] + '/region-source-review', json=command), 409)
    assert opaque_state() == before


def test_v2_source_original_nonce_survives_role_loss_but_changed_or_fresh_command_cannot_write(quote_scene):
    scene = quote_scene; _, _, _, command, accepted = approved_source(scene)
    with Session(engine()) as db:
        from sqlalchemy import select
        member = db.scalar(select(Membership).where(Membership.user_id == scene['owner']['id'],
            Membership.workspace_id == scene['workspace_id']))
        member.role = 'viewer'; db.commit()
    before = opaque_state(); path = scene['base'] + '/region-source-review'
    recovered = reply(scene['client'].post(path, json=command))
    assert recovered['receipt'] == accepted['receipt'] and recovered['source_review'] == accepted['source_review']
    changed = {**command, 'fact_hash': '0' * 64}
    reply(scene['client'].post(path, json=changed), 409)
    reply(scene['client'].post(path, json={**command, 'request_id': str(uuid4())}), 403)
    assert opaque_state() == before


def test_cold_proof_rejects_resealed_context_or_draft_reference_without_matching_selected_hashes(quote_scene):
    scene = quote_scene; _, _, _, _, accepted = approved_source(scene)
    proof = accepted['source_review']; before = opaque_state()
    with Session(engine()) as db:
        assert region.verify_projection(db, principal(scene), proof, scene['handoff']) is True
        for damage in ('context_revision', 'context_copy_version', 'draft_revision', 'draft_value_hash', 'draft_hash'):
            altered = copy.deepcopy(proof)
            if damage == 'context_revision': altered['binding_context']['configuration_revision'] += 1
            elif damage == 'context_copy_version': altered['binding_context']['copy_refs'][0]['version'] += 1
            elif damage == 'draft_revision': altered['projection']['quote_binding_draft_ref']['revision'] += 1
            elif damage == 'draft_value_hash': altered['projection']['quote_binding_draft_ref']['value_hash'] = '0' * 64
            else: altered['projection']['quote_binding_draft_ref']['draft_hash'] = '0' * 64
            # A fresh outer digest is not authority to alter the selected inner
            # value/scope. No current/later private draft is read to repair it.
            altered = h.public_record({k: v for k, v in altered.items() if k != 'record_hash'})
            assert region.verify_projection(db, principal(scene), altered, scene['handoff']) is False
    assert opaque_state() == before


def test_formal_bad_span_never_falls_back_to_a_valid_single_block_quote(monkeypatch):
    from generic_agents import instance_compiler
    seen = []
    monkeypatch.setattr(region, 'citation_valid', lambda *args, **kwargs: seen.append(1) or False)
    monkeypatch.setattr(instance_compiler, 'scoped', lambda *args, **kwargs: pytest.fail('bad typed span fell back to v1'))
    cite = {'material_id': str(uuid4()), 'block_id': 'ocr-confirmed-1', 'quote': '此处即使单块能匹配',
            'span_binding_id': '0' * 64}
    assert instance_compiler._citation_issues(None, None, [cite], '合成步骤', flow={}, path='/steps/0', name='合成业务')
    assert seen == [1]


def test_legacy_source_body_ledger_hash_and_receipt_replay_remain_exact(monkeypatch):
    # Existing public pure fixture; explicitly synthetic, not a PG proof.
    fixtures = runpy.run_path(str(Path(__file__).with_name('test_report_source_projection.py')))
    scene = fixtures['state'](monkeypatch); p = fixtures['P']
    body = scene.body; old = body.model_dump(mode='json')
    assert set(old) == {'request_id', 'configuration_revision', 'configuration_hash', 'candidate_index',
        'fact_hash', 'material_refs', 'consent', 'review_mode', 'projection_refs', 'final_view_confirmed'}
    plain = {k: v for k, v in old.items() if k not in {'review_mode', 'projection_refs', 'final_view_confirmed'}}
    assert h.SourceReviewCommand.model_validate(plain).model_dump(mode='json') == plain
    assert h.ProjectedSourceReviewCommand.model_validate(old).model_dump(mode='json') == old
    with pytest.raises(ValidationError): h.SourceReviewCommand.model_validate(old)
    with pytest.raises(ValidationError): h.MultiRegionSourceReviewCommand.model_validate(old)
    proof, _, receipt = legacy.projected_review_value(scene.db, p, scene.handoff, scene.config, body, str(uuid4()))
    ledger = h.ledger_data(proof, body, receipt); before = copy.deepcopy(ledger)
    row = NS(id=proof['id'], workspace_id=p.workspace_id, actor_id=p.user_id, kind='source_review',
        parent_id=scene.handoff['id'], report_id=scene.handoff['report_id'], configuration_id=scene.config.id,
        request_id=str(body.request_id), data=ledger)
    assert proof['command_hash'] == digest({'kind': 'source_review', 'actor_id': p.user_id,
        'workspace_id': p.workspace_id, 'handoff_id': scene.handoff['id'], 'body': old})
    monkeypatch.setattr(region, 'verify_projection', lambda *args, **kwargs: pytest.fail('v1 dispatched to v2'))
    assert h.checked_ledger(row, p)['record'] == proof
    assert h.replay_command(row, p, 'source_review', scene.handoff['id'], body) == receipt
    assert row.data == before and body.model_dump(mode='json') == old


@pytest.mark.parametrize('bad', [None, {}, {'schema': region.SCHEMA, 'review_mode': legacy.MODE},
    {'schema': legacy.SCHEMA, 'review_mode': region.MODE}, {'schema': region.SCHEMA, 'review_mode': 'future_v3'}])
def test_unknown_or_cross_version_projection_has_no_v1_fallback(bad):
    with pytest.raises(HTTPException) as failure: h.projection_verifier(bad)
    assert failure.value.status_code == 409


@pytest.mark.parametrize('field,value', [('start', True), ('end', True), ('page', True), ('box', [1, 2, 3, 4])])
def test_region_span_contract_rejects_boolean_offsets_and_float_coordinate_coercion(field, value):
    span = {'page': 1, 'unit_id': str(uuid4()), 'revision_id': str(uuid4()), 'revision_hash': 'a' * 64,
        'revision_kind': 'raw', 'image_sha256': 'b' * 64, 'region_id': 'r1', 'region_text_hash': 'c' * 64,
        'box': ['1', '2', '90', '20'], 'start': 0, 'end': 2}
    span[field] = value
    with pytest.raises(ValidationError): binding.RegionSpan.model_validate(span)
