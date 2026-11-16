"""Profile resume is a distinct strict command; no database or runtime IO."""
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from generic_agents import material_ocr_api as api


def command():
    return dict(request_id=str(uuid4()),expected_material_version=4,run_id=str(uuid4()),
                expected_run_version=8,reason='保留已保存文字，继续未完成页',
                engine_id='rapidocr-3.10.0-ppocrv6-small-cpu-m49-low-memory-v1',
                engine_manifest_hash='3a11f677a07ca36d3a767379b00546aa41c1c47f2dc659b80344872e7c10d13f')


@pytest.mark.parametrize('key,value',[
    ('request_id','not-a-uuid'),('expected_run_version',True),('expected_run_version',0),
    ('expected_material_version','4'),('engine_manifest_hash','A'*64),
    ('engine_manifest_hash','x'),('engine_id',False),('engine_id',''),('reason',''),
])
def test_profile_resume_rejects_invalid_frozen_request_fields(key,value):
    value_map=command();value_map[key]=value
    with pytest.raises(ValidationError):api.ResumeWithProfile.model_validate(value_map)


def test_new_command_does_not_extend_or_reinterpret_original_retry():
    value=command()
    with pytest.raises(ValidationError):api.RunCommand.model_validate(value)
    original={k:v for k,v in value.items() if k not in {'engine_id','engine_manifest_hash'}}
    assert api.RunCommand.model_validate(original).model_dump(mode='json')==original
    assert api.COMMANDS['retry'] is api.RunCommand
    assert api.COMMANDS['resume_with_profile'] is api.ResumeWithProfile
    assert api.ResumeWithProfile.model_validate(value).model_dump(mode='json')==value
    with pytest.raises(ValidationError):api.ResumeWithProfile.model_validate({**value,'model_path':'/tmp/override'})


def test_profile_resume_forwarding_is_distinct_and_commits_once(monkeypatch):
    calls=[];db=SimpleNamespace(commit=lambda:calls.append('commit'));principal=object()
    body=api.ResumeWithProfile.model_validate(command());material_id=str(uuid4())
    def execute(actual_db,actual_principal,mid,action,actual_body,target):
        assert actual_db is db and actual_principal is principal and mid==material_id
        assert action=='resume_with_profile' and actual_body is body and target is None
        calls.append(action);return {'request':{'action':action,'body':body.model_dump(mode='json')}}
    monkeypatch.setattr(api.ocr,'execute',execute)
    result=api.resume_with_profile(material_id,body,principal,db)
    assert calls==['resume_with_profile','commit']
    assert result['request']['body']==body.model_dump(mode='json')
    assert any(r.path=='/materials/{material_id}/ocr/resume_with_profile' and r.methods=={'POST'} for r in api.router.routes)


def test_profile_request_can_be_abandoned_without_losing_frozen_engine():
    value=command()
    abandoned=api.Abandon.model_validate(dict(action='resume_with_profile',target=None,
        original_request=value,expected_material_version=4,reason='不再提交这次恢复'))
    parsed=api.COMMANDS[abandoned.action].model_validate(abandoned.original_request)
    assert parsed.model_dump(mode='json')==value and abandoned.target is None


def test_engine_catalog_retains_original_first_and_reports_explicit_profile(monkeypatch):
    old={'id':'legacy','ready':True};new={'id':'low-memory','ready':False,'reason':'未安装固定本地包'}
    monkeypatch.setattr(api.ocr,'engine_status',lambda:old)
    monkeypatch.setattr(api.ocr,'low_memory_engine_status',lambda:new,raising=False)
    assert api.engines(object())=={'items':[old,new]}
