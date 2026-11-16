"""Pure protocol/business decisions; no camera or authority success is implied."""
import copy
import json
from uuid import uuid4
import pytest
from fastapi import HTTPException
from generic_agents import frame_supplement_v236 as service


def wire():
    return {'schema': 'frame-supplement-propose-v236', 'request_id': str(uuid4()),
        'assessment_id': str(uuid4()), 'expected_result_hash': 'a'*64,
        'expected_source_hash': 'b'*64, 'expected_frame_sha256': 'c'*64}


@pytest.mark.parametrize('change', [lambda v: v.update(extra=True),
    lambda v: v.pop('expected_result_hash'), lambda v: v.update(expected_source_hash=True),
    lambda v: v.update(request_id='not-an-id'), lambda v: v.update(assessment_id=False),
    lambda v: v.update(expected_frame_sha256='F'*64)])
def test_closed_wire_rejects_ambiguous_or_incomplete_intents(change):
    body = wire(); change(body)
    with pytest.raises(HTTPException) as error: service.parse(json.dumps(body).encode(), 'propose')
    assert error.value.status_code == 422


def test_utf8_exact_bytes_and_duplicate_keys_have_distinct_semantics():
    body = wire(); raw = json.dumps(body).encode()
    assert service.parse(raw, 'propose') == body
    with pytest.raises(HTTPException):
        service.parse(raw[:-1]+b',"request_id":"'+body['request_id'].encode()+b'"}', 'propose')


@pytest.mark.parametrize('seconds', [True, 4.0, 3, 13, '6'])
def test_admin_read_scope_requires_real_bounded_integer_seconds(seconds):
    body = {'schema': 'frame-supplement-approve-v236', 'request_id': str(uuid4()),
        'version': 1, 'expected_decision_hash': 'b'*64, 'seconds': seconds, 'confirm_one_time_read': True}
    with pytest.raises(HTTPException): service.parse(json.dumps(body).encode(), 'approve')


def test_saved_qa_and_real_stock_are_explicit_rule_inputs_not_implicit_planner_claims():
    qa = {'source_hash': 'b'*64, 'frame_sha256': 'c'*64, 'current_state_sufficient': False,
        'state_conclusion': None, 'checks':[{'kind':'source_time','status':'unverified'}],
        'visual':None, 'gaps': [{'kind': 'coverage'}, {'kind': 'source_time'}]}
    stock = {'run_id': 'run', 'total_frames': 10, 'checked_frames': 8, 'limit': 8,
        'truncated': True, 'frames': [{'frame_id': str(i), 'sha256': 'c'*64, 'visual': None} for i in range(8)]}
    source = {'camera_id': 'camera', 'label': '原来源', 'configuration_changed': False,
        'can_execute': False, 'blocked_reasons': ['当前来源离线，不能读取']}
    before = copy.deepcopy((qa, stock, source))
    result = service.business_decision(qa, assessment_id='qa', result_hash='a'*64,
        source_hash='b'*64, frame_sha256='c'*64, stock=stock, source=source)
    assert result['target_gaps'] == ['coverage', 'source_time']
    assert result['stock']['truncated'] is True and result['read']['max_reads'] == 1
    assert result['source']['can_execute'] is False and result['read']['physical_control_authorized'] is False
    assert '原核查' in result['explanation'][0] and len(result['cannot_certify']) == 4
    result['stock']['frames'][0]['frame_id'] = 'changed'
    assert (qa, stock, source) == before
    with pytest.raises(HTTPException):
        service.business_decision(qa, assessment_id='qa', result_hash='a'*64,
            source_hash='d'*64, frame_sha256='c'*64, stock=stock, source=source)


@pytest.mark.parametrize('same', [True, False])
def test_comparison_keeps_unknown_scene_information_even_when_sha_changes(same):
    from types import SimpleNamespace
    old = {'source_hash': 'b'*64, 'frame_sha256': 'c'*64, 'current_state_sufficient': False,
        'state_conclusion': None, 'gaps': [{'kind': 'source_time'}]}
    after = {**copy.deepcopy(old), 'frame_sha256': 'c'*64 if same else 'd'*64}
    row = SimpleNamespace(assessment_id='before', data={service.FAMILY: {'source_hash': 'b'*64,
        'result_hash': 'a'*64}, 'original_result': old, 'followup_assessment_id': 'after'})
    result = service.make_comparison(row, after, 'f'*64)
    assert result['same_original'] is same and result['new_scene_information_verified'] is False
    assert result['current_state_sufficient'] is False and result['state_conclusion'] is None
    assert result['remaining_gaps'] == ['source_time']
    result['before']['gaps'].clear()
    assert old['gaps'] == [{'kind': 'source_time'}]


@pytest.mark.parametrize('capture_status,run_status,processing_status,expected,word', [
    ('running', None, None, 'reading', '采集任务结算'),
    ('succeeded', 'queued', 'queued', 'reading', '本地处理'),
    ('succeeded', 'running', 'running', 'reading', '本地处理'),
    ('succeeded', 'succeeded', 'running', 'reading', '仍在结算'),
    ('succeeded', 'succeeded', 'succeeded', 'blocked', '未完成完整补采'),
    ('succeeded', 'failed', 'failed', 'blocked', '未成功'),
    ('cancelled', 'queued', 'queued', 'blocked', '未成功'),
])
def test_partial_read_state_selection_waits_for_real_tail_and_never_builds_qa(
        monkeypatch, capture_status, run_status, processing_status, expected, word):
    """Mechanical lifecycle selection only; SourcePG owns real signed IO proof.

    Stand-ins mark the explicit authority/storage boundaries, not successful
    camera/Job fixtures. No new assessment, receipt or capture may be invoked.
    """
    from types import SimpleNamespace as NS
    from generic_agents import frame_supplement_source_v236 as source
    from generic_agents import media_daily_task_adapter as daily
    from generic_agents.models import Job, VideoRun
    row = NS(id='plan', status='reading', data={'approved_by':'admin',
        'approved':{'seconds':8}, 'progress':'原保存进度'})
    actor = NS(user_id='admin', role='admin')
    session = NS(status='partial', video_run_id='run' if run_status is not None else None,
        data={'job_id':'capture','receipt':{'duration_seconds':2.4,'requested_seconds':8}})
    capture = NS(status=capture_status)
    run = NS(id='run',status=run_status,data={'job_id':'processing'}) if run_status else None
    processing = NS(status=processing_status,kind='process_video',data={'run_id':'run'}) if run else None
    saved = []; stopped = []
    monkeypatch.setattr(service, 'check_row', lambda *args, **kwargs: row)
    monkeypatch.setattr(daily, 'current_actor', lambda *args, **kwargs: 'admin')
    monkeypatch.setattr(source, '_fixed_session', lambda *args: session)
    def lookup(db, cls, identity, p):
        if cls is Job and identity == 'capture': return capture
        if cls is Job and identity == 'processing': return processing
        if cls is VideoRun and identity == 'run': return run
        pytest.fail('partial path attempted unrelated storage or a new source')
    monkeypatch.setattr(service, 'scoped', lookup)
    monkeypatch.setattr(service, 'save_state', lambda *args, **kwargs: saved.append(row.data['progress']))
    def block(db, p, value, reason):
        stopped.append(reason); value.status='blocked'; value.data={**value.data,'error':reason}
    monkeypatch.setattr(service, 'block', block)
    monkeypatch.setattr(source, 'read_followup', lambda *args, **kwargs: pytest.fail('partial became a full QA source'))
    service.advance(None, actor, row)
    assert row.status == expected
    text = row.data.get('error') if expected == 'blocked' else row.data['progress']
    assert word in text
    if run_status != 'failed' and capture_status != 'cancelled':
        assert '2.4秒' in text and '8秒' in text
    if expected == 'reading':
        assert not stopped and len(saved) == 1
        # Polling an unchanged phase does not inflate versions/audit history.
        service.advance(None, actor, row)
        assert len(saved) == 1
    else: assert len(stopped) == 1 and not saved


@pytest.mark.parametrize('status,received,expected', [
    ('blocked', False, 'blocked'), ('cancelled', False, 'blocked'),
    ('blocked', True, 'received'), ('reading', False, 'waiting')])
def test_plan_view_terminal_without_fixed_receipt_does_not_keep_waiting(
        monkeypatch, status, received, expected):
    """Mechanical projection only; genuine partial source/ACK is in SourcePG."""
    from datetime import datetime, timezone
    from types import SimpleNamespace as NS
    from generic_agents import frame_supplement_source_v236 as source
    from generic_agents.models import Job, StreamSession, VideoRun
    actor = NS(role='admin',user_id='admin')
    row = NS(id='plan',version=3,status=status,owner_id='admin',instance_id='instance',
        assessment_id='qa',created_at=datetime.now(timezone.utc),data={
            service.FAMILY:{'task_id':'task'},'progress':'部分片段已保存，实际2.4秒 / 请求8秒',
            'error':'部分补采未完成' if status=='blocked' else None,
            'session_id':'session','decision_hash':'a'*64,'decision':{'source':{'can_execute':True}},
            'scope_lines':[],'original_frame':{},'received_source':{'event_id':'fixed'} if received else None})
    session = NS(id='session',video_run_id='run',status='partial',data={'job_id':'capture'})
    run = NS(status='succeeded',data={'job_id':'process'})
    jobs = {identity:NS(id=identity,status='succeeded') for identity in ('original','capture','process')}
    monkeypatch.setattr(service,'check_row',lambda *args,**kwargs:row)
    monkeypatch.setattr(source,'parent',lambda *args,**kwargs:NS(data={'job_id':'original'}))
    monkeypatch.setattr(service,'assessment_view',lambda *args:None)
    def lookup(db,cls,identity,p):
        if cls is Job:return jobs[identity]
        if cls is StreamSession:return session
        if cls is VideoRun:return run
        pytest.fail('unrelated source/authority lookup in projection boundary')
    monkeypatch.setattr(service,'scoped',lookup)
    view=service.plan_view(None,actor,row)
    assert view['session']['receipt_status']==expected
    assert view['status']==status
    if received:assert view['session']['receipt_status']=='received'
