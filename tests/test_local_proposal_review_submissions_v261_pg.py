"""Real private-draft/submission/review/apply persistence on disposable PG.

review_scene's only provider HTTP is an explicit synthetic transport producing
one saved invalid answer; it is not cloud-quality evidence. This file does not
mock review persistence, permission, receipt, configuration, or successful
derivation. Deliberate missing-anchor cases modify only the disposable test DB.
"""
import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents import models as m
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.local_proposal_review_models_v261 import LocalProposalReviewDraftV261 as Draft
from generic_agents.local_proposal_review_submission_models_v261 import LocalReviewSubmissionV261 as Submission
from generic_agents.local_proposal_review_submission_contracts_v261 import SubmitInput, ACTION
from generic_agents.local_proposal_review_compile_v261 import compile_saved_review
from generic_agents import local_proposal_review_submissions_v261 as service
from tests.test_local_proposal_review_drafts_v261_pg import review_scene, open_body, another_principal


BASE = '/api/setup/local-review-drafts/'
ANALYSES = '/api/setup/understanding/'


def good(response, status=200):
    assert response.status_code == status, response.text[:1200]
    return response.json()


def indexed_edits(source, *, partial=False):
    block = source['source_blocks'][0]
    assert block['text'].strip()
    paths = sorted(source['citation_hashes'])
    # The upstream transport serializes the same wrong citation in the business
    # and its step. Fixing only one must fail the ORIGINAL complete validator.
    assert len(paths) >= 2
    if partial: paths = paths[:1]
    return [{'op': 'replace_citation', 'path': path,
        'reason': '值班人员核对原文后明确更正引用', 'material_id': block['material_id'],
        'block_id': block['block_id'], 'start_text': '1',
        'end_text': str(min(250, len(block['text'])))} for path in paths]


def prepare_review(s, *, partial=False):
    opening = open_body(s)
    draft = good(s.client.post(ANALYSES + s.source_id + '/local-review-drafts', json=opening), 201)
    path = BASE + draft['id']
    source = good(s.client.get(path + '/source'))
    value = copy.deepcopy(draft['value']); value['edits'] = indexed_edits(source, partial=partial)
    saving = {'version': draft['version'], 'request_id': str(uuid4()), 'value': value}
    saved = good(s.client.put(path, json=saving))
    draft = saved['current_draft']
    source = good(s.client.get(path + '/source'))
    body = submission_body(s, draft, source)
    return draft, source, body, opening, saving


def submission_body(s, draft, source, **changes):
    return {'version': draft['version'], 'request_id': str(uuid4()),
        'value_hash': draft['value_hash'],
        'revision': good(s.client.get('/api/setup/draft'))['revision'],
        'operations': compile_saved_review(draft['value'], source),
        'confirm_local_changes': True, **changes}


def post(s, draft, body, status=200):
    return good(s.client.post(BASE + draft['id'] + '/submissions', json=body), status)


def lookup(s, draft, body):
    return good(s.client.get(BASE + draft['id'] + '/submissions/by-request/' + body['request_id'],
        params={'expected_body_hash': digest(body)}))


def nonderived_state(s, *, include_configuration=True):
    with Session(engine()) as db:
        original = db.get(m.SetupAnalysis, s.source_id)
        value = {'original': (original.version, original.status, digest(original.data))}
        for cls in (m.CloudCall, m.Job, *( [m.SetupDraft] if include_configuration else [])):
            value[cls.__name__] = sorted((row.id, getattr(row, 'version', None),
                getattr(row, 'status', None), digest(row.data),
                getattr(row, 'reserved_tokens', None), getattr(row, 'charged_tokens', None))
                for row in db.scalars(select(cls).where(cls.workspace_id == s.p.workspace_id)))
        return value


def inventory(s):
    with Session(engine()) as db:
        return {cls.__name__: sorted((row.id, getattr(row, 'version', None),
            getattr(row, 'status', None), digest(row.data))
            for row in db.scalars(select(cls).where(cls.workspace_id == s.p.workspace_id)))
            for cls in (Draft, Submission, m.SetupAnalysis, m.SetupDraft, m.SetupRevision,
                m.Audit, m.CloudCall, m.Job)}


def apply_body(s, analysis):
    configuration = good(s.client.get('/api/setup/draft'))
    return {'version': analysis['version'], 'revision': configuration['revision'],
        'names': [item['name'] for item in analysis['result']['businesses']],
        'object_names': [], 'accept_domain': False, 'request_id': str(uuid4()),
        'result_hash': analysis['result_hash'],
        'configuration_hash': digest(configuration['configuration'])}


def change_private_draft(s, draft, source):
    value = copy.deepcopy(draft['value']); value['edits'] = indexed_edits(source)
    value['edits'][0]['reason'] = '随后另存的私人输入不能改变已提交快照'
    return good(s.client.put(BASE + draft['id'], json={'version': draft['version'],
        'request_id': str(uuid4()), 'value': value}))['current_draft']


def test_real_submission_freezes_snapshot_then_normal_review_apply_and_apply_nonce_recover(review_scene):
    s = review_scene; draft, source, body, _, _ = prepare_review(s)
    before = nonderived_state(s)
    saved = post(s, draft, body); child = saved['analysis']; receipt = saved['receipt']
    assert child['status'] == 'review' and child['id'] != s.source_id
    assert receipt['derived_analysis_id'] == child['id'] and child['original_cloud_source'] == s.source_id
    assert child['receipts'] == [] and UUID(child['id']).version == 5
    assert receipt['operations_hash'] == digest(body['operations'])
    assert nonderived_state(s) == before and len(s.sent) == 1
    with Session(engine()) as db:
        row = db.get(Submission, receipt['submission_id'])
        snapshot = copy.deepcopy(row.data['snapshot']); derivation = copy.deepcopy(row.data['derivation'])
        assert snapshot['value'] == draft['value']
        assert db.get(m.SetupAnalysis, s.source_id).data['raw_response'] == s.raw
    changed = change_private_draft(s, draft, source)
    assert changed['version'] > draft['version']
    with Session(engine()) as db:
        row = db.get(Submission, receipt['submission_id'])
        assert row.data['snapshot'] == snapshot and row.data['derivation'] == derivation
    names = [item['name'] for item in child['result']['businesses']]
    reviewed = good(s.client.post(ANALYSES + child['id'] + '/review', json={
        'version': child['version'], 'names': names, 'object_names': [], 'accept_domain': False}))
    assert good(s.client.get(ANALYSES + child['id']))['review_selection']['names'] == names
    applying = apply_body(s, reviewed)
    applied = good(s.client.post(ANALYSES + child['id'] + '/apply', json=applying))
    assert applied['analysis']['status'] == 'applied' and applied['state'] == 'saved'
    assert all(name in applied['configuration']['businesses'] for name in names)
    recovered = good(s.client.get(ANALYSES + child['id'] + '/apply-receipts/' + applying['request_id']))
    assert recovered['receipt'] == applied['receipt'] and recovered['current_matches'] is True
    replay = good(s.client.post(ANALYSES + child['id'] + '/apply', json=applying))
    assert replay['receipt'] == applied['receipt']
    assert nonderived_state(s, include_configuration=False) == {
        key: value for key, value in before.items() if key != 'SetupDraft'}
    assert len(s.sent) == 1


def test_original_submit_nonce_recovers_after_private_and_configuration_progress_without_new_derivation(review_scene):
    s = review_scene; draft, source, body, _, _ = prepare_review(s)
    first = post(s, draft, body)
    change_private_draft(s, draft, source)
    config = good(s.client.get('/api/setup/draft'))
    good(s.client.put('/api/setup/draft', json={'revision': config['revision'],
        'configuration': config['configuration']}))
    before = inventory(s)
    replay = post(s, draft, body)
    assert replay['receipt'] == first['receipt'] and replay['analysis']['id'] == first['analysis']['id']
    found = lookup(s, draft, body)
    assert found['found'] is True and found['submission']['receipt'] == first['receipt']
    assert found['absence_is_non_execution'] is False
    assert inventory(s) == before and len(s.sent) == 1
    changed = copy.deepcopy(body); changed['operations'][0]['reason'] = '同一原号不能改成另一正文'
    response = s.client.post(BASE + draft['id'] + '/submissions', json=changed)
    assert response.status_code == 409 and inventory(s) == before


@pytest.mark.parametrize('kind,code', [('operations', 'invalid_operations'),
    ('validator', 'invalid_proposal'), ('revision', 'stale_configuration')])
def test_terminal_rejection_is_durable_keeps_draft_and_does_not_occupy_saved_slot(review_scene, kind, code):
    s = review_scene
    draft, source, original_body, _, _ = prepare_review(s, partial=kind == 'validator')
    body = copy.deepcopy(original_body)
    if kind == 'operations': body['operations'][0]['reason'] = '并非已保存人稿中的修改理由'
    if kind == 'revision': body['revision'] += 1
    before = nonderived_state(s)
    rejected = post(s, draft, body, 409)['detail']['rejection']
    assert rejected['code'] == code
    found = lookup(s, draft, body)
    assert found['found'] is True and found['rejection'] == rejected and found['submission'] is None
    assert good(s.client.get(BASE + draft['id']))['value'] == draft['value']
    with Session(engine()) as db:
        record = db.get(Submission, rejected['submission_id'])
        assert record.status == 'rejected' and record.saved_key is None and record.derived_analysis_id is None
        assert list(db.scalars(select(Submission).where(Submission.status == 'saved'))) == []
    if kind == 'validator':
        value = copy.deepcopy(draft['value']); value['edits'] = indexed_edits(source)
        draft = good(s.client.put(BASE + draft['id'], json={'version': draft['version'],
            'request_id': str(uuid4()), 'value': value}))['current_draft']
        source = good(s.client.get(BASE + draft['id'] + '/source'))
    fixed = submission_body(s, draft, source)
    saved = post(s, draft, fixed)
    assert saved['analysis']['status'] == 'review'
    # A repaired/new request never rewrites the previous terminal rejection.
    assert post(s, draft, body, 409)['detail']['rejection'] == rejected
    assert lookup(s, draft, body)['rejection'] == rejected
    assert nonderived_state(s) == before and len(s.sent) == 1


def test_second_nonce_same_saved_draft_version_has_durable_already_saved_and_original_entry(review_scene):
    s = review_scene; draft, _, body, _, _ = prepare_review(s)
    first = post(s, draft, body)
    second_body = {**body, 'request_id': str(uuid4())}
    second = post(s, draft, second_body, 409)['detail']
    assert second['rejection']['code'] == 'already_saved'
    assert second['receipt'] == first['receipt'] and second['analysis']['id'] == first['analysis']['id']
    found = lookup(s, draft, second_body)
    assert found['found'] and found['submission']['receipt'] == first['receipt']
    assert found['rejection'] == second['rejection']
    with Session(engine()) as db:
        rows = list(db.scalars(select(Submission).where(Submission.draft_id == draft['id'])))
        assert sorted(row.status for row in rows) == ['rejected', 'saved']
        assert len(list(db.scalars(select(m.SetupAnalysis).where(
            m.SetupAnalysis.data['original_cloud_source'].as_string() == s.source_id)))) == 1
    assert len(s.sent) == 1


@pytest.mark.parametrize('foreign', [False, True])
def test_another_actual_admin_cannot_submit_or_lookup_owner_private_work(review_scene, foreign):
    s = review_scene; draft, _, body, _, _ = prepare_review(s)
    post(s, draft, body)
    other = another_principal(s, foreign=foreign)
    for action in ('submit', 'lookup'):
        with Session(engine()) as db:
            with pytest.raises(HTTPException) as failure:
                if action == 'submit':
                    service.submit(db, other, draft['id'], SubmitInput.model_validate(body))
                else:
                    service.lookup(db, other, draft['id'], body['request_id'], digest(body))
            assert failure.value.status_code in {403, 404}


@pytest.mark.parametrize('damage', ['marker', 'submission', 'audit', 'all_three'])
def test_missing_independent_lineage_never_downgrades_derived_uuid5_to_an_ordinary_apply(review_scene, damage):
    s = review_scene; draft, _, body, _, _ = prepare_review(s)
    saved = post(s, draft, body); child = saved['analysis']; sid = saved['receipt']['submission_id']
    applying = apply_body(s, child); before = good(s.client.get('/api/setup/draft'))
    with Session(engine()) as db:
        if damage in {'marker', 'all_three'}:
            row = db.get(m.SetupAnalysis, child['id'])
            row.data = {key: value for key, value in row.data.items() if key != 'local_review_provenance'}
        if damage in {'audit', 'all_three'}:
            for record in db.scalars(select(m.Audit).where(m.Audit.object_id == sid, m.Audit.action == ACTION)):
                db.delete(record)
        if damage in {'submission', 'all_three'}:
            db.delete(db.get(Submission, sid))
        db.commit()  # Explicit disposable integrity fault; never a repair/adoption shortcut.
    response = s.client.post(ANALYSES + child['id'] + '/apply', json=applying)
    assert response.status_code == 409
    assert good(s.client.get('/api/setup/draft')) == before and len(s.sent) == 1


def test_normal_reuse_retains_submission_provenance_and_applies_without_a_model_call(review_scene):
    s = review_scene; draft, _, body, _, _ = prepare_review(s)
    child = post(s, draft, body)['analysis']
    revision = good(s.client.get('/api/setup/draft'))['revision']
    reused = good(s.client.post(ANALYSES + child['id'] + '/reuse', json={
        'version': child['version'], 'revision': revision}), 201)
    assert reused['reused_from'] == child['id'] and reused['original_cloud_source'] == s.source_id
    assert UUID(reused['id']).version == 5
    applied = good(s.client.post(ANALYSES + reused['id'] + '/apply', json=apply_body(s, reused)))
    assert applied['analysis']['status'] == 'applied' and len(s.sent) == 1


def test_reuse_marker_loss_does_not_fall_through_to_none_provenance(review_scene):
    s = review_scene; draft, _, body, _, _ = prepare_review(s)
    child = post(s, draft, body)['analysis']
    revision = good(s.client.get('/api/setup/draft'))['revision']
    reused = good(s.client.post(ANALYSES + child['id'] + '/reuse', json={
        'version': child['version'], 'revision': revision}), 201)
    with Session(engine()) as db:
        row = db.get(m.SetupAnalysis, reused['id'])
        row.data = {key: value for key, value in row.data.items() if key != 'local_review_provenance'}
        db.commit()
    config = good(s.client.get('/api/setup/draft'))
    assert s.client.post(ANALYSES + reused['id'] + '/apply', json=apply_body(s, reused)).status_code == 409
    assert good(s.client.get('/api/setup/draft')) == config and len(s.sent) == 1


def test_known_and_unknown_lookup_projections_never_write_or_claim_non_execution(review_scene):
    s = review_scene; draft, _, body, opening, saving = prepare_review(s)
    post(s, draft, body)
    missing_open = {**opening, 'request_id': str(uuid4())}
    missing_save = {**saving, 'request_id': str(uuid4())}
    missing_submit = {**body, 'request_id': str(uuid4())}
    cases = [
        (ANALYSES + s.source_id + '/local-review-drafts/by-request/' + opening['request_id'],
            {'expected_body_hash': digest(opening)}, True),
        (ANALYSES + s.source_id + '/local-review-drafts/by-request/' + missing_open['request_id'],
            {'expected_body_hash': digest(missing_open)}, False),
        (BASE + draft['id'] + '/saves/by-request/' + saving['request_id'],
            {'expected_body_hash': digest(saving), 'expected_version': saving['version']}, True),
        (BASE + draft['id'] + '/saves/by-request/' + missing_save['request_id'],
            {'expected_body_hash': digest(missing_save), 'expected_version': missing_save['version']}, False),
        (BASE + draft['id'] + '/submissions/by-request/' + body['request_id'],
            {'expected_body_hash': digest(body)}, True),
        (BASE + draft['id'] + '/submissions/by-request/' + missing_submit['request_id'],
            {'expected_body_hash': digest(missing_submit)}, False)]
    for path, params, expected in cases:
        before = inventory(s); value = good(s.client.get(path, params=params))
        assert value['found'] is expected and value['absence_is_non_execution'] is False
        assert inventory(s) == before


@pytest.mark.parametrize('same_nonce', [True, False])
def test_two_real_sessions_serialize_one_derivation_for_same_nonce_or_same_saved_version(review_scene, same_nonce):
    s = review_scene; draft, _, first_body, _, _ = prepare_review(s)
    second_body = copy.deepcopy(first_body)
    if not same_nonce: second_body['request_id'] = str(uuid4())
    gate = Barrier(2)

    def run(body):
        # Both threads start real DB work together. No third observer connection
        # competes with the application's bounded pool, and no success is stubbed.
        gate.wait(timeout=15)
        with Session(engine()) as db:
            outcome = service.submit(db, s.p, draft['id'], SubmitInput.model_validate(body))
            db.commit()
            return outcome

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run, first_body); second = pool.submit(run, second_body)
        outcomes = [first.result(timeout=60), second.result(timeout=60)]
    if same_nonce:
        assert all(value['rejection'] is None for value in outcomes)
    else:
        assert sorted(value['rejection']['code'] if value['rejection'] else 'saved'
            for value in outcomes) == ['already_saved', 'saved']
    identities = {value['saved']['analysis']['id'] for value in outcomes}
    assert len(identities) == 1
    with Session(engine()) as db:
        rows = list(db.scalars(select(Submission).where(Submission.draft_id == draft['id'])))
        assert sum(row.status == 'saved' for row in rows) == 1
        assert len(rows) == (1 if same_nonce else 2)
    assert len(s.sent) == 1
