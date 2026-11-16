"""Pure rendering/safe generated-file primitives, not source/approval proof."""
import copy
from datetime import UTC, datetime, timedelta, timezone
import hashlib
import json
import os
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import HTTPException

from generic_agents import workflow_quality_archive_v220 as archive
from generic_agents import workflow_data_quality_v219 as quality
from generic_agents import evidence
from generic_agents.contracts import digest
from tests.test_workflow_data_quality_v219 import outcome


def inputs(tmp_path):
    first=outcome(tmp_path)['report'];second=copy.deepcopy(first)
    second['operation']='deduplicate';second['producer'].update(job_id=str(uuid4()),invocation_id=str(uuid4()),
        node_id='dedup',activation_id='dedup#0')
    second['report_hash']=digest({k:v for k,v in second.items() if k!='report_hash'})
    return {'quality_report':first,'dedup_report':second}


def render(value):
    return archive.render_artifacts(value,task_id=value['quality_report']['producer']['task_id'],
        invocation_id='00000000-0000-4000-8000-000000000001',created_at='2026-10-27T12:00:00+00:00')


def test_saved_orm_aware_datetime_is_normalized_without_guessing_a_naive_timezone():
    utc=datetime(2026,10,27,12,0,0,123456,tzinfo=UTC)
    china=utc.astimezone(timezone(timedelta(hours=8)))
    assert archive._time(NS(created_at=utc))=='2026-10-27T12:00:00.123456+00:00'
    assert archive._time(NS(created_at=china))==archive._time(NS(created_at=utc))
    for missing_offset in (utc.replace(tzinfo=None),'2026-10-27T12:00:00+00:00',None):
        with pytest.raises(HTTPException) as error:archive._time(NS(created_at=missing_offset))
        assert error.value.status_code==422


def test_registration_admits_real_internal_artifact_writes_without_external_or_formal_claims():
    value=archive.manifest().model_dump(mode='json')
    assert archive.validate_manifest(value)==value
    assert '生成并保存内部技术汇总' in value['description'] and '只读' not in value['description']
    assert value['specification']['creates_local_evidence'] is True
    for key in ('description','execution','input_schema','data_policy'):
        broken=copy.deepcopy(value)
        if key=='description':broken[key]='只读正式归档'
        elif key=='execution':broken[key]['side_effect']=0
        elif key=='input_schema':broken[key]={'type':'object'}
        else:broken[key]['external_allowed']=True
        with pytest.raises(HTTPException):archive.validate_manifest(broken)


def test_human_chinese_and_machine_artifacts_are_deterministic_and_do_not_mutate_reports(tmp_path):
    value=inputs(tmp_path);before=copy.deepcopy(value)
    artifacts,status=render(value)
    assert status=='pass' and value==before and artifacts==render(value)[0]
    markdown=artifacts['markdown'].decode();machine=json.loads(artifacts['machine_json'])
    assert markdown.startswith('# 任务内部技术汇总')
    assert '同SHA只表示文件字节一致' in markdown and '正式对外成果' in markdown
    assert machine['source_reports']['quality_report']['report_hash']==value['quality_report']['report_hash']
    assert machine['formal_deliverable'] is False and machine['independent_data_ack'] is False
    assert machine['originals_deleted'] is False and machine['semantic_completeness_verified'] is False


def test_source_fields_paths_and_sensitive_diagnostic_text_are_not_echoed_in_artifacts(tmp_path):
    value=inputs(tmp_path)
    for report in value.values():
        report['issues']=[{'code':'private person /secret/report.docx','severity':'review',
            'evidence_id':'/private/original/person','field':'private medical field',
            'message':'sensitive source quotation private-person-name'}]
        report['quality_status']='needs_review';report['attributes']['technical_review_required']=1
        report['report_hash']=digest({k:v for k,v in report.items() if k!='report_hash'})
    artifacts,status=render(value)
    assert status=='needs_review'
    for content in artifacts.values():
        assert b'/private' not in content and b'private-person' not in content and b'/secret' not in content
        assert b'private medical' not in content and b'sensitive source quotation' not in content
    machine=json.loads(artifacts['machine_json'])
    assert machine['source_reports']['quality_report']['issues'][0]['code']=='unrecognized_technical_issue'


def test_unknown_or_blocked_sources_are_reported_not_promoted_to_ecology_success(tmp_path):
    value=inputs(tmp_path)
    for report in value.values():
        report['quality_status']='blocked';report['files']=[]
        report['duplicate_groups']=[];report['digest_groups']=[]
        report['issues']=[quality.issue('empty_source',blocked=True)]
        report['attributes']=quality.attributes([],report['issues'],[])
        report['report_hash']=digest({k:v for k,v in report.items() if k!='report_hash'})
    files,status=render(value)
    assert status=='blocked' and '存在技术阻断' in files['markdown'].decode()
    assert json.loads(files['machine_json'])['physical_state_verified'] is False


def test_complete_artifact_size_limit_rejects_instead_of_truncating(tmp_path,monkeypatch):
    value=inputs(tmp_path);monkeypatch.setattr(archive,'MAX_ARTIFACT_BYTES',10)
    with pytest.raises(HTTPException) as error:render(value)
    assert error.value.status_code==413


def test_safe_content_addressed_writer_is_exact_idempotent_and_metadata_primitive_never_writes(tmp_path,monkeypatch):
    root=tmp_path.resolve()/'store';workspace=str(uuid4());raw='固定内部技术汇总'.encode()
    monkeypatch.setattr(evidence.settings(),'storage_dir',root)
    with archive.workspace_directory(root,workspace) as fd:
        sha=archive._put(fd,raw)
        assert sha==hashlib.sha256(raw).hexdigest()==archive._put(fd,raw)
        archive._directory_unchanged(root,workspace,fd)
        evidence._existing_generated_blob(NS(workspace_id=workspace),raw,sha,fd)
    assert (root/workspace/(sha+'.bin')).read_bytes()==raw
    assert not list((root/workspace).glob('*.tmp'))


@pytest.mark.parametrize('fault',['workspace_symlink','target_symlink','wrong_existing_bytes','outside_fd'])
def test_safe_generated_artifacts_refuse_external_locations_or_a_corrupt_same_named_blob(tmp_path,monkeypatch,fault):
    root=tmp_path.resolve()/'root';outside=tmp_path.resolve()/'outside';outside.mkdir()
    workspace=str(uuid4());raw=b'internal only';sha=hashlib.sha256(raw).hexdigest()
    monkeypatch.setattr(evidence.settings(),'storage_dir',root)
    if fault=='workspace_symlink':
        root.mkdir();(root/workspace).symlink_to(outside,target_is_directory=True)
        with pytest.raises((OSError,HTTPException)):
            with archive.workspace_directory(root,workspace):pytest.fail('symlink directory accepted')
        assert list(outside.iterdir())==[];return
    with archive.workspace_directory(root,workspace) as fd:
        target=root/workspace/(sha+'.bin')
        if fault=='target_symlink':
            secret=outside/'unrelated';secret.write_bytes(b'do not modify');target.symlink_to(secret)
        elif fault=='wrong_existing_bytes':target.write_bytes(b'wrong existing original')
        else:
            archive._put(fd,raw)
            other=os.open(outside,os.O_RDONLY|os.O_DIRECTORY)
            try:
                with pytest.raises(HTTPException):evidence._existing_generated_blob(NS(workspace_id=workspace),raw,sha,other)
            finally:os.close(other)
            return
        archive._put(fd,raw)
        with pytest.raises((OSError,HTTPException)):evidence._existing_generated_blob(NS(workspace_id=workspace),raw,sha,fd)
        if fault=='target_symlink':assert secret.read_bytes()==b'do not modify'
        else:assert target.read_bytes()==b'wrong existing original'


def test_directory_swap_is_detected_and_writes_stay_on_held_original_fd(tmp_path):
    root=tmp_path.resolve()/'root';outside=tmp_path.resolve()/'outside';outside.mkdir()
    workspace=str(uuid4());raw=b'not for external directory'
    with archive.workspace_directory(root,workspace) as fd:
        moved=root.with_name('original-root');root.rename(moved);root.symlink_to(outside,target_is_directory=True)
        try:
            archive._put(fd,raw)
            with pytest.raises((OSError,HTTPException)):archive._directory_unchanged(root,workspace,fd)
            assert list(outside.iterdir())==[]
        finally:root.unlink();moved.rename(root)


def test_orphan_file_is_not_a_successful_original_receipt(tmp_path):
    with archive.workspace_directory(tmp_path.resolve(),str(uuid4())) as fd:archive._put(fd,b'content alone is not success')
    with pytest.raises(HTTPException):archive.checked_saved_result(None,None,NS(status='running'))
