"""真实隔离服务保存/读回/恢复；供应商替身测试不声称云质量。"""
import copy
from uuid import uuid4
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.db import engine
from generic_agents.models import Task,Memory,Membership
from generic_agents.contracts import digest
from generic_agents import native_cases as cases
from tests.test_native_goals import setup,define
from tests.test_native_cases import accept,url,command,case_body


def new_draft(client,task,view,title):
    return command(view,acceptance_hash=view['source']['acceptance_hash'],title=title,
        summary='用户保存的完整说明 '+title,limits='原范围限制 '+title,
        steps=[{'id':'identity','kind':'source_identity','field':view['field_options'][0]['key'],'instruction':'旧稿完整核查要求 '+title}],
        **({'case_id':view['draft']['id'],'expected_case_version':view['draft']['version'],
            'content_hash':view['draft']['content_hash']} if view['draft'] else {}))


def saved(client,task,view,title):
    response=client.post(url(task)+'/draft',json=new_draft(client,task,view,title));assert response.status_code==200,response.text
    return response.json()


def edited(client):
    _,_,task,base,body=setup(client);define(client,base,body);assert accept(client,task).status_code==200
    view=client.get(url(task)).json();first=saved(client,task,view,'首个完整人工稿')
    assert client.get(url(task)+'/draft-revisions').json()['items']==[]
    current=saved(client,task,first,'第二个完整人工稿')
    return task,first,current


def detail(client,task,revision):
    response=client.get(url(task)+'/draft-revisions/'+str(revision));assert response.status_code==200,response.text
    return response.json()


def restore_body(view,item):
    return {**case_body(view),'revision_version':item['revision'],'revision_hash':item['revision_hash']}


def test_manual_save_full_old_draft_restores_as_new_revision_and_original_uuid_recovers(client):
    task,first,current=edited(client);old=detail(client,task,first['draft']['version'])
    item=old['item'];assert item['title']==first['draft']['title'] and item['summary']==first['draft']['summary']
    assert item['steps']==first['draft']['steps'] and item['limits']==first['draft']['limits']
    assert item['saved_at'] and item['archived_at'] and item['reason'] and item['can_restore']
    body=restore_body(current,item);response=client.post(url(task)+'/restore',json=body)
    assert response.status_code==200,response.text;restored=response.json()
    assert restored['draft']['title']==first['draft']['title'] and restored['draft']['version']==current['draft']['version']+1
    assert restored['permissions']['restore']['allowed']
    second=detail(client,task,current['draft']['version'])['item']
    assert second['title']==current['draft']['title'] and second['steps']==current['draft']['steps']
    replay=client.post(url(task)+'/restore',json=body);assert replay.status_code==200
    assert replay.json()['version']==restored['version']
    recovery=client.post(url(task)+'/requests/recover',json={'operation':'restore','original_request':body,
        'expected_version':restored['version'],'reason':'读取恢复原请求结果，不重复覆盖','action':'probe'})
    assert recovery.status_code==200 and recovery.json()['saved'] and recovery.json()['view']['draft']['title']==item['title']
    assert len(client.get(url(task)+'/draft-revisions').json()['items'])==2
    assert client.post(url(task)+'/restore',json={**body,'reason':'更换原UUID内容'}).status_code==409


def test_revision_pagination_uses_actual_old_case_versions_without_backfill(client):
    task,first,current=edited(client);current=saved(client,task,current,'第三稿')
    page=client.get(url(task)+'/draft-revisions?limit=1').json()
    assert page['items'][0]['revision']==2 and page['next_cursor']==2
    last=client.get(url(task)+'/draft-revisions?limit=1&after_version=2').json()
    assert last['items'][0]['revision']==1 and last['next_cursor'] is None
    assert client.get(url(task)+'/draft-revisions/99').status_code==404
    assert client.get(url(task)+'/draft-revisions?after_version=99').status_code==409
    with Session(engine()) as db:
        row=db.get(Memory,current['draft']['id']);assert len(row.data['draft_revisions'])==2
        assert all('observations' not in item and 'attributes' not in item and 'source_proof' not in item for item in row.data['draft_revisions'])


def test_signed_state_from_another_task_cannot_move_its_old_draft_to_current_path(client):
    from tests.test_native_cases import another
    release,_,a,base,body=setup(client);define(client,base,body);assert accept(client,a).status_code==200
    first=saved(client,a,client.get(url(a)).json(),'原任务人工稿');saved(client,a,first,'原任务第二稿')
    _,b=another(client,release,body['goal_spec'])
    with Session(engine()) as db:
        parent=db.get(Task,a['id']);other=db.get(Task,b['id'])
        other.data={**other.data,'native_experience':copy.deepcopy(parent.data['native_experience']),
            'native_experience_hash':parent.data['native_experience_hash']};db.commit()
    for suffix in ['/draft-revisions','/draft-revisions/1']:
        response=client.get(url(b)+suffix)
        assert response.status_code==409 and '当前任务' in response.json()['detail']


@pytest.mark.parametrize('conflict',['task','case','revision_hash'])
def test_restore_cas_conflict_does_not_mutate_current_or_old_work(client,conflict):
    task,first,current=edited(client);old=detail(client,task,1);body=restore_body(current,old['item'])
    key={'task':'expected_version','case':'expected_case_version','revision_hash':'revision_hash'}[conflict]
    body[key]=body[key]-1 if conflict!='revision_hash' else '0'*64
    response=client.post(url(task)+'/restore',json=body);assert response.status_code==409,response.text
    view=client.get(url(task)).json();assert view['version']==current['version'] and view['draft']==current['draft']
    assert len(client.get(url(task)+'/draft-revisions').json()['items'])==1


def test_restore_unknown_uuid_can_be_explicitly_abandoned_without_touching_draft(client):
    task,_,current=edited(client);body=restore_body(current,detail(client,task,1)['item'])
    recovery={'operation':'restore','original_request':body,'expected_version':current['version'],
        'reason':'仅作废明确未保存的原恢复请求','action':'probe'}
    response=client.post(url(task)+'/requests/recover',json=recovery);assert response.status_code==200 and not response.json()['saved']
    response=client.post(url(task)+'/requests/recover',json={**recovery,'action':'abandon'})
    assert response.status_code==200 and response.json()['abandoned']
    assert response.json()['view']['draft']==current['draft']
    assert client.post(url(task)+'/restore',json=body).status_code==409


def test_viewer_can_read_full_old_work_but_cannot_restore(client):
    task,_,current=edited(client);item=detail(client,task,1)['item']
    with Session(engine()) as db:member=db.scalar(select(Membership));member.role='viewer';db.commit()
    response=client.get(url(task)+'/draft-revisions/1');assert response.status_code==200,response.text
    assert response.json()['item']['summary']==item['summary'] and not response.json()['permissions']['restore']['allowed']
    assert '只读' in response.json()['permissions']['restore']['reason']
    assert client.post(url(task)+'/restore',json=restore_body(current,item)).status_code==403


@pytest.mark.parametrize('approved',[False,True])
def test_submitted_or_approved_case_preserves_old_draft_but_refuses_restore(client,approved):
    task,_,current=edited(client);item=detail(client,task,1)['item']
    response=client.post(url(task)+'/submit',json=case_body(current));assert response.status_code==200,response.text;current=response.json()
    if approved:
        response=client.post(url(task)+'/review',json={**case_body(current),'decision':'trust','valid_until':None})
        assert response.status_code==200,response.text;current=response.json()
    history=detail(client,task,1);assert not history['permissions']['restore']['allowed'] and history['item']['summary']==item['summary']
    assert client.post(url(task)+'/restore',json=restore_body(current,item)).status_code==409


def test_old_draft_read_is_static_when_formal_source_broken_but_restore_rechecks_original(client):
    task,_,current=edited(client)
    with Session(engine()) as db:
        row=db.get(Task,task['id']);data=copy.deepcopy(row.data);data['native_goal_acceptance']['at']='changed';row.data=data;db.commit()
    history=detail(client,task,1);assert history['item']['title']=='首个完整人工稿' and not history['permissions']['restore']['allowed']
    assert client.post(url(task)+'/restore',json=restore_body(current,history['item'])).status_code==409


@pytest.mark.parametrize('damage',['content','chain','scope','display','delete'])
def test_damaged_snapshot_or_chain_fails_closed_even_if_current_case_is_resealed(client,damage):
    task,_,current=edited(client);current=saved(client,task,current,'第三稿')
    with Session(engine()) as db:
        row=db.get(Memory,current['draft']['id']);data=copy.deepcopy(row.data)
        if damage=='content':data['draft_revisions'][0]['content']['title']='破坏旧稿'
        elif damage=='chain':data['draft_revisions'][1]['previous_revision_hash']='0'*64
        elif damage=='scope':data['draft_revisions'][0]['scope_hash']='0'*64
        elif damage=='display':data['draft_revisions'][0]['display_steps'][0]['instruction']='伪显示'
        else:data['draft_revisions']=data['draft_revisions'][1:]
        data['draft_revisions_hash']=digest(data['draft_revisions'])
        row.data=data;cases._seal_case(row);db.commit()
    assert client.get(url(task)+'/draft-revisions').status_code==409
    assert client.get(url(task)+'/draft-revisions/1').status_code==409
    assert client.post(url(task)+'/restore',json={**case_body(current),'revision_version':1,'revision_hash':'0'*64}).status_code==409


def test_hundred_old_versions_rejects_new_overwrite_and_restore_without_losing_work(client):
    """受控内核构建100版，不绕过线上Task命令100条预算。"""
    from generic_agents.security import Principal
    _,_,task,base,body=setup(client);define(client,base,body);assert accept(client,task).status_code==200
    current=saved(client,task,client.get(url(task)).json(),'最初稿')
    with Session(engine()) as db:
        member=db.scalar(select(Membership));p=Principal(member.user_id,member.workspace_id,member.role,'test')
        parent=db.get(Task,task['id']);row=db.get(Memory,current['draft']['id'])
        for i in range(100):
            cases._archive_draft(db,p,parent,row,cases.Command(request_id=str(uuid4()),expected_version=parent.version,reason='受控版本容量夹具'))
            row.version+=1;content={**row.data['content'],'title':'容量稿'+str(i)}
            row.text=content['title'];row.data={**row.data,'content':content,'content_hash':digest(content)};cases._seal_case(row)
        db.commit()
    current=client.get(url(task)).json();response=client.post(url(task)+'/draft',json=new_draft(client,task,current,'不允许丢弃旧稿'))
    assert not current['permissions']['restore']['allowed'] and '100个旧草稿' in current['permissions']['restore']['reason']
    assert response.status_code==409 and '100个旧草稿' in response.json()['detail']
    first=detail(client,task,1)['item'];response=client.post(url(task)+'/restore',json=restore_body(current,first))
    assert response.status_code==409 and '100个旧草稿' in response.json()['detail']
    after=client.get(url(task)).json();assert after['version']==current['version'] and after['draft']==current['draft']
    with Session(engine()) as db:assert len(db.get(Memory,current['draft']['id']).data['draft_revisions'])==100


def test_cloud_import_shared_archive_restores_exact_manual_and_cloud_origin_without_new_call(client,monkeypatch):
    from tests import test_native_method_suggestions as methods
    _,_,task,_,cap=methods.seed(client);manual=saved(client,task,client.get(url(task)).json(),'原始人工工作不得丢失')
    sent=[];methods.transport(monkeypatch,sent);candidate,_,_=methods.start(client,task,cap)
    methods.run(candidate);candidate,_=methods.save(client,task,methods.get(client,task,candidate))
    old=client.get(url(task)).json()
    body=methods.action(candidate,saved_edit_hash=candidate['saved_edit']['hash'],acceptance_hash=old['source']['acceptance_hash'],
        replace_existing=True,case_id=old['draft']['id'],expected_case_version=old['draft']['version'],content_hash=old['draft']['content_hash'])
    response=client.post(methods.url(task)+'/'+candidate['id']+'/import',json=body);assert response.status_code==200,response.text
    current=response.json()['experience'];prior=detail(client,task,manual['draft']['version'])['item']
    assert prior['summary']==manual['draft']['summary'] and prior['steps']==manual['draft']['steps']
    with Session(engine()) as db:cloud_origin=copy.deepcopy(db.get(Memory,current['draft']['id']).data['suggestion_origin'])
    response=client.post(url(task)+'/restore',json=restore_body(current,prior));assert response.status_code==200,response.text
    restored=response.json();assert restored['draft']['title']==manual['draft']['title']
    with Session(engine()) as db:assert 'suggestion_origin' not in db.get(Memory,current['draft']['id']).data
    cloud=detail(client,task,current['draft']['version'])['item']
    response=client.post(url(task)+'/restore',json=restore_body(restored,cloud));assert response.status_code==200,response.text
    with Session(engine()) as db:assert db.get(Memory,current['draft']['id']).data['suggestion_origin']==cloud_origin
    # 云稿再人工保存必须解除旧建议正文标记；恢复云旧稿才恢复其来源。
    cloud_restored=response.json();manual_again=saved(client,task,cloud_restored,'云稿以后重新人工编辑')
    with Session(engine()) as db:assert 'suggestion_origin' not in db.get(Memory,current['draft']['id']).data
    cloud_old=detail(client,task,cloud_restored['draft']['version'])['item']
    response=client.post(url(task)+'/restore',json=restore_body(manual_again,cloud_old));assert response.status_code==200,response.text
    with Session(engine()) as db:assert db.get(Memory,current['draft']['id']).data['suggestion_origin']==cloud_origin
    assert len(sent)==1
