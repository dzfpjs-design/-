"""Disposable-PG coverage for the saved full-report response viewer.

The provider is a synthetic checkpoint double; no provider HTTP is sent.
This tests the read-only module, not a browser or a separately wired route.
"""
import base64
import copy
import json
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import CloudCall, SetupReportDraft
from generic_agents.report_full_raw_view import raw_view
from generic_agents.security import Principal
from generic_agents.worker import process_once
from tests.test_material_ocr import local_runtime
from tests.test_report_full_reading_source import full_source_scene, read_db
from tests.test_report_full_reading_pipeline import reading_scene, SyntheticProvider, start, get


@pytest.fixture(autouse=True)
def require_owned_disposable_database(monkeypatch):
    """Existing historical fixture fence names a different dedicated database.

    This suite uses conftest's freshly created ga_test database, after conftest
    has verified the independent host, port, data directory and system ID.
    """
    import tests.test_report_full_reading_source as source_tests
    import tests.test_report_full_reading_pipeline as pipeline_tests
    def fenced():
        url = engine().url
        assert url.host == '127.0.0.1' and url.database.startswith('ga_test_')
    fenced()
    monkeypatch.setattr(source_tests, 'fence', fenced)
    monkeypatch.setattr(pipeline_tests, 'fence', fenced)


def stopped(scene, monkeypatch, mode):
    provider = SyntheticProvider(scene, monkeypatch, mode=mode)
    _, _, accepted = start(scene)
    assert process_once(accepted['draft']['job_id'])
    row = get(scene, accepted['draft']['id'])
    return provider, row


def read(scene, row, stage='extract-1', principal=None, revision=None):
    with read_db() as db:
        return raw_view(db, principal or scene['p'], row['id'], stage,
                        row['revision'] if revision is None else revision)


def test_raw_view_route_is_private_and_rejects_stale_revision(reading_scene, monkeypatch):
    scene = reading_scene
    provider, row = stopped(scene, monkeypatch, 'truncated')
    url = '/api/setup/report-full-readings/' + row['id'] + '/stages/extract-1/raw'
    result = scene['client'].get(url, params={'revision': row['revision']})
    assert result.status_code == 200
    assert result.headers['cache-control'] == 'private, no-store'
    assert result.headers['x-content-type-options'] == 'nosniff'
    assert result.json()['status'] == 'available'
    assert result.json()['finish_reason'] == 'length'
    assert scene['client'].get(url, params={'revision': row['revision'] + 1}).status_code == 409
    assert len(provider.calls) == 1


def test_incomplete_answer_is_visible_without_mutation_or_new_call(reading_scene, monkeypatch):
    scene=reading_scene; provider,row=stopped(scene,monkeypatch,'truncated')
    with Session(engine()) as db:
        draft=db.get(SetupReportDraft,row['id']);before=digest(draft.data)
        call=db.get(CloudCall,provider.calls[0]);receipt_before=copy.deepcopy(call.data)
    value=read(scene,row)
    assert value['status']=='available' and value['finish_reason']=='length'
    assert value['content_encoding']=='plain' and isinstance(value['content'],str)
    assert value['scope']['workspace_id']==scene['p'].workspace_id
    assert value['scope']['actor_id']==scene['p'].user_id and value['scope']['stage']=='extract-1'
    assert value['can_apply'] is value['can_publish'] is False
    assert 'provider_wire_base64' not in json.dumps(value)
    with Session(engine()) as db:
        assert digest(db.get(SetupReportDraft,row['id']).data)==before
        assert db.get(CloudCall,provider.calls[0]).data==receipt_before
        assert len(db.scalars(select(CloudCall)).all())==1
    assert len(provider.calls)==1


@pytest.mark.parametrize('mode',['oversize','unsupported_encoding','legacy_missing','multiple_choices'])
def test_unsupported_or_unverifiable_original_is_never_fabricated(reading_scene, monkeypatch, mode):
    scene=reading_scene;provider,row=stopped(scene,monkeypatch,mode)
    value=read(scene,row)
    assert value['status']=='unavailable' and value['content'] is None and value['content_encoding'] is None
    assert len(value['scope']['raw_hash'])==64 and len(provider.calls)==1


def test_unsaved_checkpoint_and_wrong_scope_or_version_fail_safely(reading_scene,monkeypatch):
    scene=reading_scene;provider,row=stopped(scene,monkeypatch,'unknown')
    value=read(scene,row)
    assert value['status']=='not_saved' and value['content'] is None and value['scope']['raw_hash'] is None
    for principal in (Principal(str(uuid4()),scene['p'].workspace_id,'admin',''),
                      Principal(scene['p'].user_id,str(uuid4()),'admin','')):
        with pytest.raises(HTTPException) as failure:read(scene,row,principal=principal)
        assert failure.value.status_code==404
    with pytest.raises(HTTPException) as stale:read(scene,row,revision=row['revision']+1)
    assert stale.value.status_code==409
    for stage in ('extract-9','merge','../extract-1'):
        with pytest.raises(HTTPException) as missing:read(scene,row,stage=stage)
        assert missing.value.status_code==404
    assert len(provider.calls)==1


@pytest.mark.parametrize('tamper',['raw_content','call_receipt','call_actor'])
def test_tampered_checkpoint_or_ledger_is_not_disclosed(reading_scene,monkeypatch,tamper):
    scene=reading_scene;provider,row=stopped(scene,monkeypatch,'truncated')
    with Session(engine()) as db:
        draft=db.get(SetupReportDraft,row['id']);call=db.get(CloudCall,provider.calls[0])
        if tamper=='raw_content':
            data=copy.deepcopy(draft.data);raw=data['extractions'][0]['raw_response']
            raw['content']='合成篡改内容';raw['hash']=digest({k:v for k,v in raw.items() if k!='hash'})
            draft.data=data
        elif tamper=='call_receipt':
            data=copy.deepcopy(call.data);data['receipt']['finish_reason']='stop';call.data=data
        else:
            call.data={**call.data,'actor_id':str(uuid4())}
        db.commit()
    with pytest.raises(HTTPException) as failure:read(scene,row)
    assert failure.value.status_code==409
    assert len(provider.calls)==1


def test_exact_escaped_nul_text_uses_safe_reversible_representation(reading_scene,monkeypatch):
    scene=reading_scene;provider,row=stopped(scene,monkeypatch,'truncated')
    with Session(engine()) as db:
        draft=db.get(SetupReportDraft,row['id']);data=copy.deepcopy(draft.data)
        raw=data['extractions'][0]['raw_response'];wire=json.loads(base64.b64decode(raw['provider_wire_base64']))
        original='合成\x00原答';wire['choices'][0]['message']['content']=original
        raw.pop('content',None);raw.pop('value',None)
        raw['content_encoding']='json-ascii-string-v1';raw['content_json_ascii']=json.dumps(original,ensure_ascii=True)
        raw['provider_wire_base64']=base64.b64encode(json.dumps(wire,ensure_ascii=True).encode()).decode()
        raw['hash']=digest({k:v for k,v in raw.items() if k!='hash'});draft.data=data;db.commit()
    value=read(scene,row)
    assert value['status']=='available' and value['content_encoding']=='json-ascii-string-v1'
    assert json.loads(value['content'])=='合成\x00原答' and '\x00' not in value['content']
    assert len(provider.calls)==1
