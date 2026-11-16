"""Pure HTTP contract and explicit reader/provenance IO seams, not PG proof."""
from contextlib import nullcontext
import hashlib
from types import SimpleNamespace as NS
from uuid import uuid4

from fastapi import HTTPException
import pytest
from starlette.requests import Request

from generic_agents import workflow_quality_artifact_api_v220 as api
from generic_agents import workflow_quality_archive_v220 as archive
from generic_agents import workspace_api
from generic_agents import report_full_source_read_scope


def request(query):
    return Request({'type':'http','method':'GET','path':'/','headers':[],'query_string':query.encode()})


@pytest.mark.parametrize('query,expected',[('',False),('download=false',False),('download=true',True)])
def test_only_explicit_download_boolean_strings_select_the_disposition(query,expected):
    assert api.download_mode(request(query)) is expected


@pytest.mark.parametrize('query',['download=1','download=yes','download=','download=True',
    'path=/secret','download=false&download=true','download=true&other=x'])
def test_unsupported_or_duplicate_query_never_selects_a_path_or_loose_boolean(query):
    with pytest.raises(HTTPException) as error:api.download_mode(request(query))
    assert error.value.status_code==422


@pytest.mark.parametrize('kind,media',[('markdown','text/markdown'),('machine_json','application/json')])
def test_headers_bind_exact_bytes_hash_length_fixed_filename_and_no_cache(kind,media):
    raw='内部技术汇总'.encode()
    descriptor={'media_type':media,'size_bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
    for download in (False,True):
        headers=api.headers(kind,descriptor,raw,download)
        assert headers['Content-Length']==str(len(raw)) and headers['X-Content-SHA256']==descriptor['sha256']
        assert headers['Content-Type'].split(';')[0]==media and headers['X-Technical-Artifact-Kind']==kind
        assert headers['Content-Disposition'].startswith('attachment;' if download else 'inline;')
        assert 'filename="task-technical-summary.' in headers['Content-Disposition']
        assert "filename*=UTF-8''" in headers['Content-Disposition']
        assert headers['Cache-Control']=='private, no-store' and headers['X-Content-Type-Options']=='nosniff'
        assert '/private/' not in str(headers)
    with pytest.raises(HTTPException):api.headers(kind,{**descriptor,'sha256':'0'*64},raw,False)


def test_task_read_permission_is_checked_before_any_evidence_or_filesystem_lookup(monkeypatch):
    db=NS(no_autoflush=nullcontext());p=NS(workspace_id=str(uuid4()))
    monkeypatch.setattr(report_full_source_read_scope,'task_full_source_read_scope',lambda *_:nullcontext())
    def denied(*_):raise HTTPException(403,'normal Task reader rejected')
    monkeypatch.setattr(workspace_api,'saved_execution',denied)
    monkeypatch.setattr(api,'scoped',lambda *_:pytest.fail('No Evidence lookup without Task reader permission'))
    with pytest.raises(HTTPException) as error:api.read_artifact(db,p,str(uuid4()),str(uuid4()))
    assert error.value.status_code==403


def test_unrelated_evidence_is_not_a_generic_download_fallback(monkeypatch):
    task_id=str(uuid4());db=NS(no_autoflush=nullcontext());p=NS(workspace_id=str(uuid4()))
    monkeypatch.setattr(report_full_source_read_scope,'task_full_source_read_scope',lambda *_:nullcontext())
    monkeypatch.setattr(workspace_api,'saved_execution',lambda *_:(NS(id=task_id),None,None))
    monkeypatch.setattr(api,'scoped',lambda *_:NS(data={'purpose':'ordinary_upload'}))
    monkeypatch.setattr(archive,'checked_saved_result',lambda *_:pytest.fail('Not an archive receipt'))
    with pytest.raises(HTTPException) as error:api.read_artifact(db,p,task_id,str(uuid4()))
    assert error.value.status_code==404
