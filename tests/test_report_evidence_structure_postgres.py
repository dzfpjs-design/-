"""Root-run owned-PG human structure additions; SOURCE authored, not executed.

All source/provider fixtures are synthetic. The ordinary review API, full local
semantic validation, PG history and receipts are real. Fresh sessions/clients
are cold reads, not a claim of process restart or model/meaning approval.
"""
import copy
import json
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from generic_agents.api import app
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents import report_evidence_pipeline as pipeline
from generic_agents.report_evidence_manual_v6_adapter import manual_from_projection_v6
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_source import full_source_scene, fence
from tests.test_report_full_reading_pipeline import reading_scene
from tests.test_report_evidence_pipeline import reply
from tests.test_report_evidence_pipeline_v3 import all_rows, fees
from tests.test_report_evidence_pipeline_v5 import call_ids
from tests.test_report_evidence_pipeline_v7_postgres import (
    BASE, NativeHTTP, v7_scene, mapped, reduced, storage_files, call_job_rows,
)


def test_manual5_appended_structures_save_exact_history_cold_read_and_keep_paid_originals(
        v7_scene, monkeypatch, account):
    fence()  # v7_scene also checks the exact owned disposable PG name/comment.
    scene = v7_scene
    http = NativeHTTP(monkeypatch)
    _, _, _, middle = mapped(scene, http)
    _, _, _, current = reduced(scene, http, middle)
    assert current['status'] == 'review' and current['review_history'] == []
    identity = current['id']
    original = {key: copy.deepcopy(current[key]) for key in (
        'preview', 'maps', 'reduction', 'model_result', 'model_result_hash')}
    paid = call_job_rows(), fees(call_ids(current)), storage_files(), len(http.requests)
    history, commands, saved = [], [], []

    def unchanged_paid(*, fresh_login=False):
        actual = call_job_rows()
        for table, expected_rows in paid[0].items():
            assert set(actual[table]) == set(expected_rows), ('paid-row-ids', table)
            for key, expected in expected_rows.items():
                left, right = json.loads(actual[table][key]), json.loads(expected)
                # A fresh successful login legitimately advances its own rate
                # limit window. No report call/job may change with that login.
                if fresh_login and table == 'jobs' and right['kind'] == 'login_limit':
                    assert left['data']['count'] == right['data']['count'] == 0
                    assert set(left['data']) == set(right['data']) == {'count', 'since'}
                    left = copy.deepcopy(left)
                    left['data']['since'] = right['data']['since']
                assert left == right, ('paid-row-fields', table, key,
                    [field for field in left if left[field] != right.get(field)])
        assert (fees(call_ids(current)), storage_files(), len(http.requests)) == paid[1:]

    def save(value):
        nonlocal current
        previous_revision = current['revision']
        command = {'revision': previous_revision, 'request_id': str(uuid4()), 'value': copy.deepcopy(value)}
        response = reply(scene['client'].put(BASE + '/' + identity + '/review', json=command))
        current = response['draft']
        assert response['current_matches'] is True
        assert current['revision'] == response['receipt']['accepted_revision'] == previous_revision + 1
        assert response['receipt']['value_hash'] == digest(value)
        assert current['status'] == 'review' and current['review_value'] == value
        history.append(copy.deepcopy(value))
        commands.append(copy.deepcopy(command))
        saved.append(copy.deepcopy(response))
        assert [entry['value'] for entry in current['review_history']] == history
        assert [entry['receipt'] for entry in current['review_history']] == [item['receipt'] for item in saved]
        assert all(current[key] == expected for key, expected in original.items())
        assert current['can_apply'] is current['can_publish'] is False
        unchanged_paid()

    def reason(value, label):
        text = ' 人工补充' + label + '；含义及来源仍待核，不代表模型结论或执行授权。末字🙂\n'
        value['result']['questions'].append(text)
        return text

    def evidence(text):
        return {'status': 'unknown', 'citations': [], 'explanation': text}

    value = manual_from_projection_v6(original['model_result'], note=' 人工追加私人结构，原说明末字🙂\n')
    baseline = copy.deepcopy(value)
    business_index = len(value['result']['businesses'])
    assert business_index < 20 and len(value['result']['questions']) <= 22
    why = reason(value, '业务')
    value['result']['businesses'].append({'operation': 'upsert', 'original_name': '',
        'name': '人工补充待核业务', 'trigger': '', 'output': '', 'steps': [],
        'citations': [], 'inference_reason': why, 'participants': [], 'interactions': [],
        'conditions': [], 'coordination': []})
    save(value)
    assert current['review_value']['result']['businesses'][business_index]['steps'] == []
    assert current['review_value']['result']['businesses'][business_index]['trigger'] == ''

    # The human explicitly supplies the trigger/output before a complete step;
    # neither the source nor a provider is claimed to have supplied these facts.
    for name, target in [('人工补充步骤甲', None), ('人工补充步骤乙', 0)]:
        value = copy.deepcopy(current['review_value'])
        business = value['result']['businesses'][business_index]
        business['trigger'] = '人工明确填写的待核启动条件'
        business['output'] = '人工明确填写的待核业务产出'
        why = reason(value, name)
        business['steps'].append({'name': name, 'purpose': ' 人工填写的待核目的 ',
            'agent': '人工确认', 'role': '人工填写的核对职责', 'output': '人工填写的待核步骤产出',
            'branch_condition': '', 'citations': [], 'inference_reason': why,
            'next_index': target, 'else_index': None})
        save(value)
    assert [step['next_index'] for step in current['review_value']['result']['businesses'][business_index]['steps']] == [None, 0]

    for name in ['人工补充参与方甲', '人工补充参与方乙']:
        value = copy.deepcopy(current['review_value'])
        why = reason(value, name)
        value['result']['businesses'][business_index]['participants'].append({
            'agent': name, 'responsibility': ' 人工填写的独立待核职责 ', 'evidence': evidence(why)})
        save(value)

    for collection in ['interactions', 'conditions', 'coordination']:
        value = copy.deepcopy(current['review_value'])
        why = reason(value, collection)
        if collection == 'interactions':
            item = {'from_participant_index': 1, 'to_participant_index': 0,
                'purpose': '人工填写的待核交接方向', 'condition': '人工填写的待核交接条件',
                'step_indices': [1, 0], 'evidence': evidence(why)}
        elif collection == 'conditions':
            item = {'kind': 'stop', 'statement': '人工填写的待核停止条件',
                'step_indices': [1], 'evidence': evidence(why)}
        else:
            item = {'kind': 'parallel', 'step_indices': [1, 0],
                'start_rule': '人工填写的待核开始规则', 'wait_rule': '人工填写的待核等待规则',
                'join_rule': '人工填写的待核汇合规则', 'evidence': evidence(why)}
        value['result']['businesses'][business_index][collection].append(item)
        save(value)

    assert len(saved) == 8
    last = copy.deepcopy(current)
    assert last['review_value']['result']['businesses'][:business_index] == baseline['result']['businesses']
    for key in ['domain', 'source_hash', 'catalog_hash', 'objects', 'relationships', 'global_claims']:
        assert last['review_value']['result'][key] == baseline['result'][key]
    assert last['review_value']['result']['questions'][:len(baseline['result']['questions'])] == baseline['result']['questions']
    assert last['review_value']['note'] == baseline['note']

    # Same nonce always retrieves the original receipt before the current CAS.
    replay = reply(scene['client'].put(BASE + '/' + identity + '/review', json=commands[0]))
    assert replay['receipt'] == saved[0]['receipt'] and replay['current_matches'] is False
    assert replay['draft'] == last
    assert reply(scene['client'].get(BASE + '/' + identity + '/review-receipts/' + commands[0]['request_id'])) == replay
    assert reply(scene['client'].put(BASE + '/' + identity + '/review', json=commands[-1])) == saved[-1]

    rows = all_rows()
    stale = {**copy.deepcopy(commands[0]), 'request_id': str(uuid4())}
    response = scene['client'].put(BASE + '/' + identity + '/review', json=stale)
    assert response.status_code == 409 and response.json()['detail']['code'] == 'evidence_review_conflict'
    changed = copy.deepcopy(commands[0]); changed['value']['note'] += '同号改体'
    response = scene['client'].put(BASE + '/' + identity + '/review', json=changed)
    assert response.status_code == 409 and response.json()['detail']['code'] == 'nonce_conflict'

    for mode in ['self-step', 'same-person', 'foreign-step', 'unknown-without-question', 'wrong-source']:
        invalid = copy.deepcopy(last['review_value'])
        business = invalid['result']['businesses'][business_index]
        if mode == 'self-step': business['steps'][0]['next_index'] = 0
        elif mode == 'same-person': business['interactions'][0]['to_participant_index'] = 1
        elif mode == 'foreign-step': business['conditions'][0]['step_indices'] = [len(business['steps'])]
        elif mode == 'unknown-without-question': invalid['result']['questions'] = []
        else: invalid['result']['source_hash'] = 'f' * 64
        response = scene['client'].put(BASE + '/' + identity + '/review', json={
            'revision': last['revision'], 'request_id': str(uuid4()), 'value': invalid})
        assert response.status_code == 422 and response.json()['detail']['code'] == 'evidence_review_invalid'
        assert all_rows() == rows and reply(scene['client'].get(BASE + '/' + identity)) == last
        unchanged_paid()
    assert all_rows() == rows

    with Session(engine()) as db:
        assert pipeline.draft_view(pipeline.owned(db, scene['p'], identity), scene['p']) == last
    with TestClient(app) as fresh:
        login = reply(fresh.post('/api/login', json={'username': account[0], 'password': account[1]}))
        fresh.headers.update({'X-CSRF-Token': login['csrf_token'], 'X-Workspace-ID': scene['p'].workspace_id})
        assert reply(fresh.get(BASE + '/' + identity)) == last
        receipt = reply(fresh.get(BASE + '/' + identity + '/review-receipts/' + commands[-1]['request_id']))
        assert receipt == saved[-1]
    unchanged_paid(fresh_login=True)
