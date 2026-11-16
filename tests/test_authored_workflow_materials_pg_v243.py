"""Normal uploaded/parsed MD citation -> authored adoption -> published source.

No report qualification, adoption, Task or ACK is inserted as authority.
Negative tests corrupt only this disposable PG fixture's real saved records.
"""
import copy
import json
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from generic_agents import models as m, authored_workflow_store_v208 as common
from generic_agents import authored_workflow_materials_v243 as material_sources
from generic_agents import workflow_data_quality_v219 as quality
from generic_agents.config import settings
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.instance_compiler import signature_value, verify_material_snapshots
from generic_agents.security import Principal
from generic_agents.worker import process_once
from tests.test_setup import upload
from tests.test_instance_workspace import configuration, save, OBJECTS, object_row
from tests.test_workflow_images import upload as upload_image
from tests.test_authored_image_workflow_pg_v243 import BASE, good, exact, prepare_tools, body as image_body
from tests.test_authored_workflow_pg_v208 import published_packet, preview_body as document_body


def adopted_source(client, family='image'):
    cap = None
    if family == 'image': prepare_tools(client)
    else: cap = published_packet(client)
    raw = '# 图像复核要求\n逐项核对保存原件，记录技术问题并保留复核结论。\n'.encode()
    queued = upload(client,raw,'公开图像技术要求.md')
    assert process_once(job_id=queued['job_id'],deliver=False)
    parsed = good(client.get('/api/setup/materials/'+queued['id']))
    assert parsed['status'] == 'parsed'
    block = next(item for item in parsed['blocks'] if '逐项核对保存原件' in item['text'])
    value = configuration(); value['materials'] = [parsed]
    flow = value['flows']['资料核验']
    flow['steps'] = [copy.deepcopy(flow['steps'][0]) for _ in range(3)]
    for index,step in enumerate(flow['steps']):
        step.update(id='review_'+str(index),name='原人工复核'+str(index+1),role='原人工岗位'+str(index+1),
            condition='',elseId='end',nextId='review_'+str(index+1) if index < 2 else 'end',
            sourceId=parsed['id'],citations=[{'material_id':parsed['id'],'block_id':block['id'],
                'quote':'逐项核对保存原件'}])
    policy = value['automation']['policies']['资料核验']
    policy.update(triggers=['人工发起','新观测到达','定时运行'] if family == 'image' else ['人工发起'],time='08:00')
    policy['rules']['data'] = {'mode':'自动运行'}
    flow['confirmedSignature'] = json.dumps(signature_value(value,flow),ensure_ascii=False)
    save(client,value,review_automation=True)
    selected = image_body(client) if family == 'image' else document_body(client,cap)
    prefix = BASE if family == 'image' else common.PREFIX
    preview = good(client.post(prefix+'/preview',content=exact(selected)))
    command = {**selected,'request_id':str(uuid4()),'preview_hash':preview['preview_hash'],'consent':True}
    if family == 'image': command['schema'] = 'authored-image-workflow-adopt-command-v243'
    accepted = good(client.post(prefix+'/adopt',content=exact(command)),201)
    current = good(client.get('/api/setup/draft'))
    compilation = {'revision':current['revision'],'objects':OBJECTS,
        'role_bindings':{role:actor['user_id'] for role,actor in preview['binding']['role_bindings'].items()}}
    return NS(client=client,family=family,raw=raw,parsed=parsed,accepted=accepted,current=current,
        compilation=compilation,preview=preview)


def release_source(s):
    checked = good(s.client.post('/api/instances/check',json=s.compilation))
    assert not checked['general_issues'],checked['general_issues']
    release = good(s.client.post('/api/instances',json={**s.compilation,
        'confirmed_configuration_hash':checked['configuration_hash'],'confirm_configuration':True}),201)
    assert release['available_business_ids']
    snapshot = next(item for item in release['snapshot']['materials'] if item['id'] == s.parsed['id'])
    assert snapshot[material_sources.KEY] == [s.accepted['origin']]
    assert snapshot['blocks_hash'] == digest(s.parsed['blocks'])
    assert snapshot['status'] == 'parsed' and 'business_flow_execution_sources' not in snapshot
    return release,snapshot


def activate_source(s,release):
    instance = next(item for item in good(s.client.get('/api/instances')) if item['id'] == release['instance_id'])
    return s.client.post('/api/instances/'+instance['id']+'/activate',json={'version':instance['version'],
        'release_id':release['id'],'business_ids':release['available_business_ids'],'confirm_workflow_scope':True})


@pytest.mark.parametrize('family',['image','document'])
def test_real_md_citation_compiles_and_activates_with_its_own_authored_source_family(client,family):
    s = adopted_source(client,family)
    assert not s.current['configuration'].get('businessFlowExecutions')
    release,snapshot = release_source(s)
    assert good(activate_source(s,release))['active_release_id'] == release['id']
    account = good(client.get('/api/me'))
    p = Principal(account['user_id'],account['workspace_id'],'admin','actual-source-fixture')
    with Session(engine()) as db:
        verify_material_snapshots(db,p,[snapshot])
        actual = common.checked_origin(db,p,s.accepted['origin'])
        assert [item['id'] for item in actual['source_materials']] == [s.parsed['id']]*3
        assert db.scalar(select(func.count()).select_from(m.CloudCall)) == 0


def test_authored_md_material_in_real_image_task_does_not_gain_native_full_report_parse_proof(client):
    s = adopted_source(client); release,snapshot = release_source(s)
    good(activate_source(s,release))
    entity = object_row(client,release)
    image = good(upload_image(client,release),201)
    task = good(client.post('/api/workspace/instances/'+release['instance_id']+'/tasks',json={
        'release_id':release['id'],'business_id':release['available_business_ids'][0],
        'entity_ids':[entity['id']],'image_ids':[image['id']],'title':'实际保存图像及原文技术核对','request_id':str(uuid4())}),201)
    account = good(client.get('/api/me')); p = Principal(account['user_id'],account['workspace_id'],'admin','actual-task-source')
    with Session(engine()) as db:
        saved = db.get(m.Task,task['id'])
        assert saved.data['material_snapshots'] == [snapshot]
        rows,issues,covered,_,_ = quality.inventory(db,p,saved)
        material_rows = [row for row in rows if any(ref['kind'] == 'material' for ref in row['refs'])]
        assert len(material_rows) == 1 and material_rows[0]['parse_proof'] is False
        assert 'material' in covered and 'image' in covered and not issues


@pytest.mark.parametrize('fault',['needs_input','blocks','original_bytes','missing_adoption_audit'])
def test_real_authored_material_damage_blocks_activation_and_cannot_borrow_needs_input_exception(client,fault):
    s = adopted_source(client); release,snapshot = release_source(s)
    account = good(client.get('/api/me')); p = Principal(account['user_id'],account['workspace_id'],'admin','damaged-source-fixture')
    original_path = None
    with Session(engine()) as db:
        row = db.get(m.SetupMaterial,s.parsed['id'])
        if fault == 'needs_input': row.status = 'needs_input'
        elif fault == 'blocks': row.data = {**row.data,'blocks':[{**row.data['blocks'][0],'text':'明确损坏的解析内容'}]}; row.version += 1
        elif fault == 'missing_adoption_audit':
            event = db.scalar(select(m.Audit).where(m.Audit.action == common.ADOPTED_AUDIT,m.Audit.object_id == s.accepted['id']))
            assert event is not None; db.delete(event)
        else:
            original_path = settings().storage_dir/db.get(m.Evidence,row.evidence_id).storage_key
            original_path.write_bytes(b'explicit-corruption-of-this-disposable-original')
        db.commit()
    try:
        with Session(engine()) as db:
            before = db.scalar(select(func.count()).select_from(m.Audit))
            with pytest.raises(HTTPException) as error: verify_material_snapshots(db,p,[snapshot],allow_archived=True)
            assert error.value.status_code == 409
            assert db.scalar(select(func.count()).select_from(m.Audit)) == before
        response = activate_source(s,release)
        assert response.status_code == 409,response.text[:300]
        instance = next(item for item in good(client.get('/api/instances')) if item['id'] == release['instance_id'])
        assert instance['active_release_id'] is None
    finally:
        if original_path is not None: original_path.write_bytes(s.raw)
