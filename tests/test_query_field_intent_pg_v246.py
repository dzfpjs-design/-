"""Normal published quantity Schema, local extraction and independent ACK.

No model or successful reception is substituted. A named old-heuristic seam
creates an authentic saved clarification through the normal modern API solely
to verify old-record display/copy; it does not fabricate a successful query.
"""
import copy
import json
import re
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func,select
from sqlalchemy.orm import Session

from generic_agents import models as m,worker,workspace_observation_intake as intake,query_needs
from generic_agents.api import app
from generic_agents.contracts import DataRequest,digest
from generic_agents.instance_compiler import signature_value
from generic_agents.workspace_needs import NeedCreate
from tests.test_scoped_data_gap_intake_pg_v245 import scene,ingest,event,good
from tests.test_instance_workspace import object_row,prepared,enable,save,configuration,OBJECTS
from tests.test_vertical_slice import manifest,publish

ORIGINAL='只核对本对象已登记数量，不调用云或设备'


def legacy_body(s,**changes):
    return {'question':ORIGINAL,'entity_ids':[s.entity['id']],'fields':[s.field],'mode':'structured',
        'business_timezone':'Asia/Shanghai','evidence_requirement':{'source':'observations','minimum_verification':'unverified_extraction',
            'time':{'mode':'latest','timezone':'Asia/Shanghai','max_age_seconds':3600}}}|changes


def modern_base(s):return '/api/workspace/instances/'+s.release['instance_id']+'/data-needs'


def modern_body(s,**changes):
    return {'request_id':str(uuid4()),'release_id':s.release['id'],'business_id':s.release['available_business_ids'][0],
        'entity_id':s.entity['id'],'purpose':ORIGINAL,'fields':[s.field],'source':'observations',
        'confirmation':'unverified_extraction','max_age_hours':1}|changes


def create(s,body):return good(s.client.post(modern_base(s),json=body),201)


def modern_query_status(s,view,expected):
    assert view['query_status']==expected and view['result_status']==expected
    with Session(s.bound) as db:
        row=db.get(m.DataRun,view['id'])
        assert row.status==expected and row.data['request']['question']==view['purpose']


def extract(s):
    observation=ingest(s)
    with Session(s.bound) as db:identity=db.scalar(select(m.Job.id).where(m.Job.idempotency_key=='extract:'+observation['id']))
    assert worker.process_once(identity,kinds=['process_observation'],workspace_id=s.workspace_id,deliver=False)
    return observation


def value_rows(row):return row['data']['result'].get('observation_evidence',[])


def only_local(db,s):
    assert not list(db.scalars(select(m.CloudCall).where(m.CloudCall.workspace_id==s.workspace_id)))
    assert not list(db.scalars(select(m.Acquisition).where(m.Acquisition.workspace_id==s.workspace_id)))
    assert not list(db.scalars(select(m.Job).where(m.Job.workspace_id==s.workspace_id,
        m.Job.kind.in_(['dispatch_acquisition','data_query']))))


def test_original_sentence_modern_and_plain_query_read_27_only_after_actual_independent_ack(scene):
    s=scene;body=modern_body(s);initial=create(s,body)
    modern_query_status(s,initial,'insufficient')
    assert initial['status']=='pending_delivery' and initial['purpose']==ORIGINAL and not initial['clarifications']
    assert initial['existing_records_only'] and not initial['parent_goal_completed']
    assert initial['copy_descriptor']['source']['request_id']==body['request_id']
    assert initial['copy_descriptor']['source']['body_hash']==digest(NeedCreate.model_validate(body).model_dump())
    plain=good(s.client.post('/api/data/queries',json=legacy_body(s)));assert plain['status']=='insufficient'
    observation=extract(s)
    with Session(s.bound) as db:assert event(db,s,observation['id']).status=='pending_delivery'
    pending=create(s,modern_body(s));modern_query_status(s,pending,'insufficient')
    assert pending['status']=='pending_delivery'
    plain_pending=good(s.client.post('/api/data/queries',json=legacy_body(s)))
    assert plain_pending['status']=='insufficient' and not value_rows(plain_pending)
    intake.deliver_pending(s.workspace_id)
    full=create(s,modern_body(s));modern_query_status(s,full,'sufficient')
    assert full['status']=='sufficient' and full['purpose']==ORIGINAL
    result=good(s.client.post('/api/data/queries',json=legacy_body(s)))
    assert result['status']=='sufficient' and result['question']==ORIGINAL and result['data']['request']['question']==ORIGINAL
    records=value_rows(result);assert len(records)==1 and records[0]['observation_id']==observation['id']
    assert records[0]['attributes'][s.field]==27 and not result['data']['request']['tool_steps']
    detail=good(s.client.get(modern_base(s)+'/'+full['id']))
    assert detail['records'][0]['extracted_values'][s.field]==27 and detail['copy_descriptor']['can_copy']
    with Session(s.bound) as db:
        row=event(db,s,observation['id']);assert row.status=='received'
        assert db.scalar(select(func.count()).select_from(m.Memory).where(m.Memory.workspace_id==s.workspace_id,
            m.Memory.data['event_id'].as_string()==row.id,m.Memory.data['type'].as_string()=='observation_evidence'))==1
        assert db.get(m.DataRun,initial['id']).question==ORIGINAL
        assert db.scalar(select(func.count()).select_from(m.Invocation).where(m.Invocation.workspace_id==s.workspace_id))==1
        only_local(db,s)
    with TestClient(app) as reopened:
        reopened.headers.update(s.client.headers);reopened.cookies.update(s.client.cookies)
        restored=good(reopened.get(modern_base(s)+'/'+full['id']))
        assert restored['copy_descriptor']==detail['copy_descriptor'] and restored['purpose']==ORIGINAL


def test_real_registered_quantity_never_answers_statistics_or_compound_negations_without_tools(scene):
    s=scene
    for question in ['统计本对象已登记数量的总数','只核对本对象已登记数量的平均值','核对已登记数量并汇总',
        '读取已登记数量的趋势','核对数量最高的对象','只读取数量，不平均但给总数',
        '逐条读取数量字段，禁止平均、统计、计数，也不查询关联关系。']:
        modern=create(s,modern_body(s,purpose=question))
        modern_query_status(s,modern,'needs_clarification')
        assert modern['status']=='needs_clarification' and modern['clarifications'] and modern['reason']
        assert modern['purpose']==question and modern['copy_descriptor']['conditions']['purpose']==question
        plain=good(s.client.post('/api/data/queries',json=legacy_body(s,question=question)))
        assert plain['status']=='needs_clarification' and not plain['data'].get('result')
    with Session(s.bound) as db:
        only_local(db,s)
        assert not list(db.scalars(select(m.Invocation).where(m.Invocation.workspace_id==s.workspace_id)))


def test_known_package_quantity_in_another_object_type_or_unspecified_fields_never_supplies_context(scene):
    s=scene;other=object_row(s.client,s.release,0)
    for changes in [{'fields':[]},{'fields':['not_registered']},{'entity_ids':[other['id']]}]:
        row=good(s.client.post('/api/data/queries',json=legacy_body(s,**changes)))
        assert row['status']=='needs_clarification' and not row['data'].get('result')
    for extra in ['_confirmed_plan','field_context','query_intent']:
        assert s.client.post('/api/data/queries',json=legacy_body(s)|{extra:True}).status_code==422


def test_same_real_package_and_type_multi_selection_requires_per_row_language(scene):
    s=scene;second=object_row(s.client,s.release,1);ids=[s.entity['id'],second['id']]
    for text in ['分别读取所选对象已登记数量','逐条核对所选对象已登记数量']:
        row=good(s.client.post('/api/data/queries',json=legacy_body(s,question=text,entity_ids=ids)))
        assert row['status']=='insufficient' and not row['data']['request']['clarifications']
    ambiguous=good(s.client.post('/api/data/queries',json=legacy_body(s,question='读取所选对象已登记数量',entity_ids=ids)))
    assert ambiguous['status']=='needs_clarification'


def test_real_distinct_types_with_identical_quantity_properties_cannot_obtain_the_exception(scene):
    s=scene;schema={'type':'object','required':[],'additionalProperties':False,
        'properties':{s.field:{'type':'integer','title':'数量','unit':'件'}}}
    package=publish(s.client,manifest(namespace='quantity_v246',name='two_types_'+uuid4().hex[:8],
        specification={'entity_schemas':{'first_type':copy.deepcopy(schema),'second_type':copy.deepcopy(schema)}}))
    objects=[]
    for kind in ['first_type','second_type']:
        objects.append(good(s.client.post('/api/entities',json={'package_id':package['id'],'entity_type':kind,
            'external_id':str(uuid4()),'attributes':{s.field:13}}),201))
    row=good(s.client.post('/api/data/queries',json=legacy_body(s,question='分别读取所选对象已登记数量',
        entity_ids=[obj['id'] for obj in objects])))
    assert row['status']=='needs_clarification' and not row['data'].get('result')


@pytest.mark.parametrize('damage',['withdrawn','changed_schema_hash','foreign_schema'])
def test_actual_package_publication_integrity_and_workspace_are_required_for_the_new_exception(scene,damage):
    s=scene
    if damage=='foreign_schema':
        from generic_agents.bootstrap import create_administrator
        from generic_agents.security import password_hash
        username='quantity_foreign_'+uuid4().hex;password='Synthetic-quantity-test-only-47!'
        with Session(s.bound) as db:
            account=create_administrator(db,username=username,workspace_name='quantity foreign source scope',
                encoded_password=password_hash(password))
            db.commit()
        with TestClient(app) as other:
            login=good(other.post('/api/login',json={'username':username,'password':password}))
            other.headers.update({'X-CSRF-Token':login['csrf_token'],'X-Workspace-ID':account['workspace_id']})
            foreign=publish(other,manifest(namespace='quantity_foreign_v246',name='foreign_'+uuid4().hex[:8],
                specification={'entity_schemas':{'entry':{'type':'object','additionalProperties':False,
                    'properties':{s.field:{'type':'integer','title':'数量'}}}}}))
            obj=good(other.post('/api/entities',json={'package_id':foreign['id'],'entity_type':'entry',
                'external_id':str(uuid4()),'attributes':{s.field:27}}),201)
        # Real foreign published content has the same explicit quantity field;
        # the original actor must not obtain any context or value from it.
        response=s.client.post('/api/data/queries',json=legacy_body(s,entity_ids=[obj['id']]))
        assert response.status_code==404,response.text[:700]
        with Session(s.bound) as db:
            assert db.get(m.Capability,foreign['id']).workspace_id==account['workspace_id']
            assert db.get(m.Capability,s.release['package_id']).workspace_id==s.workspace_id
            only_local(db,s)
        return
    with Session(s.bound) as db:
        package=db.get(m.Capability,s.release['package_id'])
        if damage=='withdrawn':package.status='revoked'
        else:
            value=copy.deepcopy(package.data);value['specification']['entity_schemas'][s.entity['entity_type']]['properties'][s.field]['title']='改坏的名称'
            package.data=value  # explicit corruption: retain old hashes, never re-sign
        db.commit()
    response=s.client.post('/api/data/queries',json=legacy_body(s))
    assert response.status_code==409,response.text[:700]


def test_model_need_uses_actual_original_selected_fields_and_cannot_self_declare_direct(scene):
    s=scene
    with Session(s.bound) as db:
        original=DataRequest.model_validate(legacy_body(s)).model_dump()
        description=query_needs.catalog(db,s.p,original['entity_ids'])
        candidate={'entity_ids':original['entity_ids'],'fields':[s.field],'purpose':ORIGINAL,'query_intent':'direct','clarifications':[]}
        result,status=query_needs.validate_need(db,s.p,original,candidate,description)
        assert status=='querying' and not result['clarifications']
        empty={**original,'fields':[]}
        result,status=query_needs.validate_need(db,s.p,empty,candidate,description)
        assert status=='needs_clarification' and result['clarifications']
        aggregate={**original,'question':'核对本对象已登记数量的平均值'}
        result,status=query_needs.validate_need(db,s.p,aggregate,candidate,description)
        assert status=='needs_clarification' and result['clarifications']
        only_local(db,s)


def old_clarification(s,monkeypatch):
    original=modern_body(s)
    # Only the old text heuristic is substituted, not IAM, Scope, publication,
    # data query, save, hashing or any successful reception path.
    with monkeypatch.context() as patch:
        patch.setattr(query_needs,'unsupported_intent',lambda question,**_kwargs:bool(re.search(
            r'平均|汇总|总计|趋势|最大值|最小值|数量|aggregate|average|trend|count\b',question,re.I)))
        result=create(s,original)
    modern_query_status(s,result,'needs_clarification')
    assert result['status']=='needs_clarification'
    return original,result


def test_old_clarification_reason_and_value_free_copy_plan_survive_and_create_only_a_new_need(scene,monkeypatch):
    s=scene;old_body,old=old_clarification(s,monkeypatch)
    before=good(s.client.get(modern_base(s)+'/'+old['id']));copy_plan=before['copy_descriptor']
    assert before['clarifications'] and before['error'] and before['reason']
    assert any('聚合' in reason or '统计' in reason for reason in before['clarifications'])
    assert copy_plan['can_copy'] and copy_plan['conditions']['purpose']==ORIGINAL
    assert copy_plan['source']['request_id']==old_body['request_id'] and copy_plan['source']['query_id']==old['id']
    assert copy_plan['source']['context_hash'] and copy_plan['source']['request_hash']
    assert copy_plan['descriptor_hash']==digest({key:value for key,value in copy_plan.items() if key!='descriptor_hash'})
    assert copy_plan==good(s.client.get(modern_base(s)+'/'+old['id']))['copy_descriptor']
    assert copy_plan['source_fields']==copy_plan['target']['fields']
    assert copy_plan['source_fields'][0]['kind']=='整数' and copy_plan['source_fields'][0]['unit'] is None
    def no_values(value):
        if isinstance(value,dict):
            assert not {'attributes','original_values','extracted_values','records','evidence','observation_evidence'}&set(value)
            for child in value.values():no_values(child)
        elif isinstance(value,list):
            for child in value:no_values(child)
    no_values(copy_plan)
    with Session(s.bound) as db:stored=copy.deepcopy(db.get(m.DataRun,old['id']).data)
    # A descriptor is not accepted as a command or extra permission field.
    assert s.client.post(modern_base(s),json=copy_plan).status_code==422
    assert s.client.post(modern_base(s),json=old_body|{'copy_descriptor':copy_plan}).status_code==422
    assert s.client.post(modern_base(s),json=old_body|{'purpose':'替换原条件'}).status_code==409
    fresh_body={**copy_plan['conditions'],'release_id':copy_plan['target']['release_id'],
        'business_id':copy_plan['target']['business_id'],'entity_id':copy_plan['target']['entity_id'],'request_id':str(uuid4())}
    fresh=create(s,fresh_body)
    modern_query_status(s,fresh,'insufficient')
    assert fresh['id']!=old['id'] and fresh['status']=='pending_delivery' and fresh['purpose']==ORIGINAL
    with Session(s.bound) as db:
        assert db.get(m.DataRun,old['id']).data==stored and db.get(m.DataRun,old['id']).status=='needs_clarification'
        only_local(db,s)
    after=good(s.client.get(modern_base(s)+'/'+old['id']))
    assert after['purpose']==before['purpose'] and after['clarifications']==before['clarifications']
    assert after['copy_descriptor']==copy_plan


def test_old_copy_descriptor_never_overrides_current_disabled_scope_or_extra_create_fields(scene,monkeypatch):
    s=scene;_,old=old_clarification(s,monkeypatch)
    initial=good(s.client.get(modern_base(s)+'/'+old['id']))
    instance=next(row for row in good(s.client.get('/api/instances')) if row['id']==s.release['instance_id'])
    good(s.client.post('/api/instances/'+instance['id']+'/pause',json={'version':instance['version']}))
    blocked=good(s.client.get(modern_base(s)+'/'+old['id']))
    assert blocked['copy_descriptor']['source']==initial['copy_descriptor']['source']
    assert not blocked['scope_valid'] and not blocked['copy_descriptor']['can_copy'] and blocked['copy_descriptor']['reasons']
    assert blocked['clarifications']==initial['clarifications'] and blocked['records']==[]
    assert s.client.get(modern_base(s)+'/options').status_code==409
    condition=initial['copy_descriptor']['conditions']
    response=s.client.post(modern_base(s),json={**condition,'release_id':s.release['id'],
        'business_id':s.release['available_business_ids'][0],'entity_id':s.entity['id'],'request_id':str(uuid4())})
    assert response.status_code==409
    with Session(s.bound) as db:only_local(db,s)


def test_copy_to_a_compatible_latest_release_is_only_a_plan_until_explicit_new_request(scene,monkeypatch):
    s=scene;old_body,old=old_clarification(s,monkeypatch)
    with Session(s.bound) as db:original=copy.deepcopy(db.get(m.DataRun,old['id']).data)
    configuration_v2=configuration()
    flow=configuration_v2['flows']['资料核验']
    flow['steps'][0]['purpose']='新版仍对照原对象登记核对，并保留完整原资料依据'
    flow['confirmedSignature']=json.dumps(signature_value(configuration_v2,flow),ensure_ascii=False)
    latest,_,_=prepared(s.client,configuration_v2,instance_id=s.release['instance_id'])
    assert latest['id']!=s.release['id'] and latest['release_number']==s.release['release_number']+1
    enable(s.client,latest)
    options=good(s.client.get(modern_base(s)+'/options'))
    assert options['release_id']==latest['id']
    prior=good(s.client.get(modern_base(s)+'/'+old['id']));plan=prior['copy_descriptor']
    assert not prior['scope_valid'] and prior['records']==[] and plan['can_copy']
    assert plan['source']['release_id']==s.release['id'] and plan['target']['release_id']==latest['id']
    assert plan['source_fields']==plan['target']['fields'] and plan['conditions']['purpose']==ORIGINAL
    assert plan['source']['object_definition_hash']!=plan['target']['object_definition_hash']
    # The original nonce still recovers its old saved scope, without promotion.
    replay=create(s,old_body)
    assert replay['id']==old['id'] and not replay['scope_valid']
    assert replay['copy_descriptor']['source']['release_id']==s.release['id']
    assert s.client.post(modern_base(s),json={**old_body,'request_id':str(uuid4())}).status_code==409
    fresh=create(s,{**old_body,'request_id':str(uuid4()),'release_id':plan['target']['release_id']})
    modern_query_status(s,fresh,'insufficient')
    assert fresh['id']!=old['id'] and fresh['copy_descriptor']['source']['release_id']==latest['id']
    with Session(s.bound) as db:
        assert db.get(m.DataRun,old['id']).data==original
        only_local(db,s)


@pytest.mark.parametrize('change',['kind','unit'])
def test_copy_never_treats_current_same_field_name_with_changed_type_or_unit_as_compatible(scene,monkeypatch,change):
    s=scene;_,old=old_clarification(s,monkeypatch)
    with Session(s.bound) as db:original=copy.deepcopy(db.get(m.DataRun,old['id']).data)
    objects=copy.deepcopy(OBJECTS)
    quantity=next(field for field in objects[1]['fields'] if field['label']=='数量')
    quantity.update({'kind':'数值'} if change=='kind' else {'unit':'件'})
    revision=save(s.client,configuration(),review_automation=True)
    member=good(s.client.get('/api/instances/members'))[0]
    body={'revision':revision,'objects':objects,'role_bindings':{'核验负责人':member['id']}}
    checked=good(s.client.post('/api/instances/check',json=body))
    latest=good(s.client.post('/api/instances',json={**body,'confirmed_configuration_hash':checked['configuration_hash'],
        'confirm_configuration':True,'instance_id':s.release['instance_id']}),201)
    assert latest['id']!=s.release['id'] and latest['release_number']==s.release['release_number']+1
    enable(s.client,latest)
    prior=good(s.client.get(modern_base(s)+'/'+old['id']));plan=prior['copy_descriptor']
    assert not prior['scope_valid'] and not plan['can_copy'] and plan['reasons']
    assert plan['source_fields'][0]['kind']=='整数' and plan['source_fields'][0]['unit'] is None
    assert plan['target']['release_id']==latest['id']
    options=good(s.client.get(modern_base(s)+'/options'))
    assert options['release_id']==latest['id'] and s.entity['id'] not in {obj['id'] for obj in options['objects']}
    assert s.client.post(modern_base(s),json=modern_body(s,release_id=latest['id'])).status_code==409
    with Session(s.bound) as db:
        assert db.get(m.DataRun,old['id']).data==original
        only_local(db,s)


def test_archive_copy_marks_observation_confirmation_and_age_as_not_applicable(scene):
    s=scene;body=modern_body(s,source='archive',confirmation='unverified_extraction',max_age_hours=1)
    prior=create(s,body);plan=prior['copy_descriptor']
    assert plan['can_copy'] and plan['conditions']=={'purpose':ORIGINAL,'fields':[s.field],
        'source':'archive','confirmation':None,'max_age_hours':None}
    assert prior['requirement']['time']['mode']=='archive' and prior['requirement']['minimum_verification']=='recorded'
    assert plan==good(s.client.get(modern_base(s)+'/'+prior['id']))['copy_descriptor']
    with Session(s.bound) as db:only_local(db,s)
