"""Synthetic HTTP capture checks; no DB, external network or model calls."""
import copy
import hashlib
import json
from types import SimpleNamespace
from uuid import UUID

import pytest

from generic_agents import report_evidence_delegated_wire_v1 as wire
from generic_agents import report_evidence_http_capture_v1 as capture


def uid(n):
    return str(UUID('10000000-0000-4000-8000-' + f'{n:012x}'))


def scene(stage='map-1', request_id='fl_' + uid(1)):
    binding = {'schema': 'report-evidence-journal-binding-v1', 'actor_id': uid(2),
        'workspace_id': uid(3), 'draft_id': uid(4), 'stage': stage, 'reserved_call_id': uid(5),
        'source_hash': 'a'*64, 'catalog_hash': 'b'*64, 'generation_hash': 'c'*64,
        'dispatch_hash': 'd'*64, 'messages_hash': 'e'*64}
    spec = {'evidence_stage': 'reduce-v2' if stage == 'reduce' else 'map-v3',
        'input_policy': {'single_batch': True, 'stage': 'synthetic'},
        'dispatch_options': {'model':'deepseek-v4-pro','max_tokens':8192},
        'output_schema': {'type':'object'}}
    row = SimpleNamespace(request_id=request_id, data={'preview': {'provider':'deepseek','model':'deepseek-v4-pro'}})
    pipeline = SimpleNamespace(v3=SimpleNamespace(applies=lambda p: True),
        _stage=lambda r,k: {'reserved_call_id':uid(5)}, _spec=lambda r,k:spec,
        _authorized=lambda r,k: {'input_summary': {'token_bound':10000}},
        _request=lambda r,k: {'messages':[{'role':'user','content':'synthetic'}]},
        journal_binding=lambda r,k,c:binding)
    return pipeline,row,spec


def save(tmp_path, monkeypatch, stage='map-1', *, finish='stop', count=1, raw_override=None):
    pipeline,row,_ = scene(stage)
    monkeypatch.setattr(wire, 'root', lambda: tmp_path/'capture')
    ctx=wire.context(pipeline,row,stage);sink=capture.prepare(wire.root(),ctx)
    sink.mark_request_started();sink.begin_response(200,None,'synthetic-request')
    content='{"claims":[]}'
    data={'choices':[{'message':{'content':content},'finish_reason':finish} for _ in range(count)],
          'usage':{'prompt_tokens':11,'completion_tokens':3,'total_tokens':14}}
    raw=raw_override if raw_override is not None else json.dumps(data).encode()
    sink.append(raw);sink.finish(eof=True);sink.close()
    return pipeline,row,ctx,data,content


@pytest.mark.parametrize('stage',['map-1','reduce'])
def test_exact_provider_wire_reconstructs_and_binds_saved_content(tmp_path,monkeypatch,stage):
    pipeline,row,ctx,data,content=save(tmp_path,monkeypatch,stage)
    actual,receipt,state=wire.response(pipeline,row,stage)
    assert actual==content and receipt['usage']==data['usage'] and receipt['finish_reason']=='stop'
    assert state['ready'] is True and ctx['binding']['stage']==stage
    descriptor={'wire_sha256':hashlib.sha256(content.encode()).hexdigest()}
    wire.verify_saved_response(pipeline,row,stage,{'receipt':receipt,'value':json.loads(content)},descriptor)
    changed=copy.deepcopy(receipt);changed['usage']['total_tokens']=15
    with pytest.raises(ValueError):
        wire.verify_saved_response(pipeline,row,stage,{'receipt':changed,'value':json.loads(content)},descriptor)


@pytest.mark.parametrize('finish,count',[('length',1),('stop',2)])
def test_incomplete_or_multiple_choices_are_preserved_but_remain_distinguishable(tmp_path,monkeypatch,finish,count):
    pipeline,row,_,_,_=save(tmp_path,monkeypatch,finish=finish,count=count)
    _,receipt,_=wire.response(pipeline,row,'map-1')
    assert (receipt['finish_reason'],receipt['response_choice_count'])==(finish,count)


@pytest.mark.parametrize('raw',[b'{broken',b'{"choices":[],"choices":[]}',b'\xff'])
def test_malformed_outer_body_is_preserved_but_never_reconstructed(tmp_path,monkeypatch,raw):
    pipeline,row,ctx,_,_=save(tmp_path,monkeypatch,raw_override=raw)
    state=capture.load(wire.root(),ctx)
    assert state['response_complete'] is True
    assert capture.read_body(wire.root(),ctx,state['capture_hash'])==raw
    with pytest.raises((ValueError,UnicodeError)):
        wire.response(pipeline,row,'map-1')


def test_incomplete_capture_and_non_delegated_task_fail_closed(tmp_path,monkeypatch):
    pipeline,row,_=scene();monkeypatch.setattr(wire,'root',lambda:tmp_path/'capture')
    ctx=wire.context(pipeline,row,'map-1');sink=capture.prepare(wire.root(),ctx)
    sink.mark_request_started();sink.begin_response(200,None,'synthetic-request');sink.append(b'{');sink.abort('interrupted');sink.close()
    with pytest.raises(ValueError):wire.response(pipeline,row,'map-1')
    row.request_id='fe_'+uid(1)
    with pytest.raises(ValueError):wire.context(pipeline,row,'map-1')
