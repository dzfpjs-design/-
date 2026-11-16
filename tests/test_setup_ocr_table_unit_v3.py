"""M47 隔离包：已确认表格结构补足单位证据；不改变模型原答或旧契约。"""
import copy
import json
import hashlib
import os
from pathlib import Path
from uuid import uuid4
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.contracts import digest
from generic_agents.db import engine
from generic_agents.models import SetupAnalysis,CloudCall,Capability,Job
from generic_agents.worker import process_once
import generic_agents.setup_understanding as understanding
import generic_agents.setup_object_proposals as objects
from tests.test_material_ocr import local_runtime,upload as upload_scan,start as start_ocr,scan_pdf,png
from tests.test_material_ocr import finish_jobs,detail,confirm_page,confirm_document
from tests.test_setup_understanding import prepared,start,process
from tests.test_setup_objects_runtime import fake_v2

ISOLATED=Path(understanding.__file__).resolve().parents[1]
if os.getenv('GA_TEST_FROZEN_SOURCE'):
    assert ISOLATED==Path(os.environ['GA_TEST_FROZEN_SOURCE']).resolve(), 'tests must import the requested frozen source'


def fixture_table():
    rows=[['字段名称','数据类型','是否必填','单位要求'],['设备码','文本','必填','不适用'],
          ['温度','数值','非必填','单位为℃'],['振动','数值','非必填','单位未说明']]
    blocks=[]
    for row,cells in enumerate(rows):
        for col,text in enumerate(cells):
            i=len(blocks)+1
            blocks.append({'id':f'b{i}','kind':'ocr_confirmed','text':text,'location':{'page':1,'ocr':{
                'confirmation_id':'confirmation','confirmation_hash':'a'*64,'run_id':'run','unit_id':'unit',
                'revision_id':'revision','revision_hash':'b'*64,'image_sha256':'c'*64,'region_id':f'r{i}',
                'box':[10+col*100,10+row*40,70,20]}}})
    material={'id':'material','blocks':blocks,'confirmation':{'id':'confirmation','hash':'a'*64,'run_id':'run'}}
    req={'contract_version':3,'source_snapshot':{'version':'setup-source-snapshot-v1','materials':[material]}}
    def cite(n):return {'material_id':'material','block_id':f'b{n}','quote':blocks[n-1]['text']}
    result={'domain':'资料管理','businesses':[],'questions':[],'objects':[{'name':'装置','citations':[cite(5)],'inference_reason':'',
        'fields':[{'label':'设备码','kind':'文本','required':True,'unit':None,'citations':[cite(5)],'inference_reason':''},
                  {'label':'温度','kind':'数值','required':False,'unit':'℃','citations':[cite(9),cite(12)],'inference_reason':''}]}]}
    compact=[{'material_id':'material','block_id':b['id'],'text':b['text'],'location':{'page':1}} for b in blocks]
    return req,result,compact


def check(req,result,blocks):
    assert Path(understanding.__file__).resolve().is_relative_to(ISOLATED)
    assert Path(objects.__file__).resolve().is_relative_to(ISOLATED)
    return understanding.validate_result(result,blocks,{},understanding.ResultV2,request=req)


def test_same_confirmed_row_field_and_explicit_unit_cells_pass_without_rewriting_quotes():
    req,result,blocks=fixture_table();before=copy.deepcopy(result)
    checked=check(req,result,blocks)
    assert checked==before and result==before


@pytest.mark.parametrize('damage',['next_row','cross_page','cross_image','cross_revision','cross_confirmation',
    'missing_header','duplicate_header','wrong_column','overlapping_column','unknown','negative','example','no_unit_quote','wrong_type'])
def test_unit_relationship_damage_stays_blocked(damage):
    req,result,blocks=fixture_table();full=req['source_snapshot']['materials'][0]['blocks'];unit=full[11]
    if damage=='next_row':unit['location']['ocr']['box'][1]+=40
    elif damage=='cross_page':unit['location']['page']=2
    elif damage=='cross_image':unit['location']['ocr']['image_sha256']='d'*64
    elif damage=='cross_revision':unit['location']['ocr']['revision_hash']='d'*64
    elif damage=='cross_confirmation':unit['location']['ocr']['confirmation_hash']='d'*64
    elif damage=='missing_header':full[3]['text']='其他说明'
    elif damage=='duplicate_header':
        duplicate=copy.deepcopy(full[:4])
        for i,b in enumerate(duplicate):b['id']=f'h{i}';b['location']['ocr']['region_id']=f'h{i}';b['location']['ocr']['box'][1]=35
        full.extend(duplicate)
    elif damage=='wrong_column':unit['location']['ocr']['box'][0]=110
    elif damage=='overlapping_column':unit['location']['ocr']['box']=[90,90,250,20]
    elif damage in {'unknown','negative','example'}:
        unit['text']={'unknown':'单位为℃，尚未确定','negative':'单位不是℃','example':'示例：单位为℃'}[damage]
        blocks[11]['text']=unit['text'];result['objects'][0]['fields'][1]['citations'][1]['quote']=unit['text']
    elif damage=='no_unit_quote':result['objects'][0]['fields'][1]['citations'].pop()
    elif damage=='wrong_type':full[9]['text']='文本'
    with pytest.raises((ValueError,understanding.ProposalInvalid)):
        check(req,result,blocks)


def test_legacy_v2_and_unconfirmed_native_blocks_keep_original_unit_rule():
    req,result,blocks=fixture_table()
    for version in (1,2):
        with pytest.raises((ValueError,understanding.ProposalInvalid)):
            check({**req,'contract_version':version},result,blocks)
    for b in req['source_snapshot']['materials'][0]['blocks']:b['kind']='paragraph'
    with pytest.raises((ValueError,understanding.ProposalInvalid)):
        check(req,result,blocks)


@pytest.mark.parametrize('placeholder',['未知','未说明','不适用','待核','尚未确定','无','N/A','unknown','—'])
def test_exact_unknown_unit_placeholder_is_not_a_positive_unit(placeholder):
    req,result,blocks=fixture_table()
    req['source_snapshot']['materials'][0]['blocks'][11]['text']=placeholder
    blocks[11]['text']=placeholder
    field=result['objects'][0]['fields'][1]
    field['unit']=placeholder;field['citations'][1]['quote']=placeholder
    with pytest.raises((ValueError,understanding.ProposalInvalid)):
        check(req,result,blocks)


@pytest.mark.parametrize('statement',['温度单位未明确','温度单位不为℃','温度不得用℃','温度单位并非℃'])
def test_same_confirmed_image_contradiction_cannot_be_overridden_by_table(statement):
    req,result,blocks=fixture_table();full=req['source_snapshot']['materials'][0]['blocks']
    narrative=copy.deepcopy(full[0]);narrative['id']='contradiction';narrative['text']=statement
    narrative['location']['ocr'].update(region_id='contradiction',box=[10,190,300,20]);full.append(narrative)
    with pytest.raises((ValueError,understanding.ProposalInvalid)):
        check(req,result,blocks)


@pytest.mark.parametrize('damage',['overlap','reversed_center'])
def test_ambiguous_header_geometry_does_not_establish_unit_column(damage):
    req,result,blocks=fixture_table();full=req['source_snapshot']['materials'][0]['blocks']
    if damage=='overlap':
        for b in full[:4]:b['location']['ocr']['box'][2]=250
        for b in full[4:]:b['location']['ocr']['box'][0]+=90
    else:
        full[0]['location']['ocr']['box'][2]=250;full[1]['location']['ocr']['box'][2]=10
    with pytest.raises((ValueError,understanding.ProposalInvalid)):
        check(req,result,blocks)


@pytest.mark.parametrize('overlap,allowed',[(0,True),(.7,True),(1.4,True),(1.5,False),(4,False)])
def test_header_edge_noise_has_fixed_scale_bound_not_seen_sample_exception(overlap,allowed):
    req,result,blocks=fixture_table();full=req['source_snapshot']['materials'][0]['blocks']
    full[2]['location']['ocr']['box'][2]=100+overlap
    if allowed:assert check(req,result,blocks)==result
    else:
        with pytest.raises((ValueError,understanding.ProposalInvalid)):
            check(req,result,blocks)


def test_nonrequired_and_no_conversion_statement_is_not_a_unit_negation():
    req,result,blocks=fixture_table();full=req['source_snapshot']['materials'][0]['blocks']
    note=copy.deepcopy(full[0]);note.update(id='note',text='温度非必填；单位为℃；不换算')
    note['location']['ocr'].update(region_id='note',box=[10,190,300,20]);full.append(note)
    assert check(req,result,blocks)==result


def test_seen_paid_b_raw_regression_uses_exact_original_quotes_not_gold():
    evidence=Path('docs/evidence/local-ocr-v21/browser/server-checkpoint-5.json')
    d=json.loads(evidence.read_text())['records']['setup_analyses'][0]['data'];req=d['request'];raw=d['raw_response']['value']
    original=copy.deepcopy(raw)
    checked=check(req,raw,req['chunks'][0])
    assert checked==original and raw==original
    actual=json.loads(Path('docs/evidence/local-ocr-v21/browser/cloud-http/request-1.json').read_text())
    assert json.loads(actual['response_final_content'])==raw


@pytest.mark.parametrize('damage',[None,'resigned_geometry'])
def test_server_current_source_local_retry_and_apply_keep_original_paid_response(client,monkeypatch,local_runtime,damage):
    assert Path(understanding.__file__).resolve().is_relative_to(ISOLATED)
    from generic_agents import material_ocr_runtime as runtime
    req0,output,_=fixture_table();full=req0['source_snapshot']['materials'][0]['blocks']
    image=png(500,250)
    def render(content,page=None,guard=None):
        return [{'page':1,'png':image,'width':500,'height':250,'rotation':0,'dpi':72,'transform':[1,0,0,1,0,0],
            'receipt':{'input_sha256':hashlib.sha256(content).hexdigest(),'output_sha256':hashlib.sha256(image).hexdigest(),
                       'manifest_hash':digest(runtime.engine_status()['manifest']),'summary_hash':digest(runtime.engine_status()['manifest']),
                       'manifest':runtime.engine_status()['manifest'],'test_only':True}}]
    def recognize(content,expected_manifest_hash,guard=None):
        regions=[{'id':f'r{i}','text':b['text'],'box':b['location']['ocr']['box'],'score':.7} for i,b in enumerate(full)]
        return {'regions':regions,'elapsed_ms':1,'receipt':{'input_sha256':hashlib.sha256(content).hexdigest(),
            'manifest_hash':expected_manifest_hash,'summary_hash':expected_manifest_hash,'manifest':runtime.engine_status()['manifest'],
            'result_sha256':digest(regions),'test_only':True}}
    monkeypatch.setattr(runtime,'render_pdf',render);monkeypatch.setattr(runtime,'recognize',recognize)
    material=upload_scan(client,scan_pdf(1));_,first=start_ocr(client,material);root,units=finish_jobs(client,material,first)
    confirm_page(client,material,detail(client,material,root['run']['id'],units[0]['id']));confirm_document(client,material)
    body,_=prepared(client);body.update(contract_version=3,request_id=str(uuid4()),material_ids=[material['id']])
    def model_result(value,payload):
        refs={b['text']:{'material_id':b['material_id'],'block_id':b['block_id'],'quote':b['text']} for b in payload['source_blocks']}
        value.update(copy.deepcopy(output))
        for obj in value['objects']:
            for item in [obj,*obj['fields']]:item['citations']=[refs[c['quote']] for c in item['citations']]
    calls=fake_v2(monkeypatch,model_result)
    original=understanding.validate_result
    # 明确模拟旧v3只允许同quote的本地规则，先获得失败但已持久的付费原答。
    monkeypatch.setattr(understanding,'validate_result',lambda *a,**kw:original(*a))
    row,_=start(client,body);process(row);failed=client.get('/api/setup/understanding/'+row['id']).json()
    assert failed['status']=='failed' and len(calls)==1
    monkeypatch.setattr(understanding,'validate_result',original)
    monkeypatch.setattr(understanding,'cloud_json',lambda *a,**kw:pytest.fail('local retry cannot call cloud'))
    with Session(engine()) as db:
        record=db.get(SetupAnalysis,row['id']);paid=copy.deepcopy(record.data['raw_response'])
        if damage:
            req=copy.deepcopy(record.data['request']);req['source_snapshot']['materials'][0]['blocks'][11]['location']['ocr']['box'][1]+=40
            req['source_snapshot_hash']=digest(req['source_snapshot'])
            req['input_summary'],req['first_dispatch_messages_hash']=understanding.input_summary(req,db.get(Capability,body['model_id']))
            req['preview_hash']=digest({k:v for k,v in req.items() if k!='preview_hash'})
            record.data={**record.data,'request':req,'consent':{**record.data['consent'],'preview_hash':req['preview_hash']}}
        db.commit()
    retry=client.post('/api/setup/understanding/'+row['id']+'/retry',json={'version':failed['version']})
    if damage:assert retry.status_code==409
    else:
        assert retry.status_code==200,retry.text
        with Session(engine()) as db:job=db.get(SetupAnalysis,row['id']).data['job_id']
        assert process_once(job,deliver=False)
        reviewed=client.get('/api/setup/understanding/'+row['id']).json();assert reviewed['status']=='review',reviewed['error']
        applied=client.post('/api/setup/understanding/'+row['id']+'/apply',json={'version':reviewed['version'],
            'revision':body['revision'],'names':[],'object_names':['装置']})
        assert applied.status_code==200,applied.text
        assert applied.json()['configuration']['instanceObjects'][0]['fields'][1]['unit']=='℃'
    with Session(engine()) as db:
        assert db.get(SetupAnalysis,row['id']).data['raw_response']==paid
        assert len(db.scalars(select(CloudCall)).all())==1
        assert len(calls)==1
