"""Multi-pack lifecycle and immutable masks; runtime inference is verified separately."""
import base64,copy,hashlib,io,json
import pytest
from PIL import Image
from sqlalchemy.orm import Session
from generic_agents import local_specialists as local
from generic_agents.segmentation import validate_result
from generic_agents.db import engine
from generic_agents.models import Evidence,SpecialistRun
from generic_agents.config import settings
from generic_agents.worker import process_once
from tests.test_local_specialists import ready as detection_ready
from tests.test_workflow_images import upload,create
from tests.test_instance_workspace import configuration,prepared,object_row,complete
from tests.test_workflow_abilities import enable,finish_job
from generic_agents.instance_compiler import signature_value


def result(backend='cpu',threshold=.5,content=None):
    b=io.BytesIO();Image.new('RGBA',(8,8),(39,104,240,160)).save(b,format='PNG')
    return {'schema':'local-segmentation-v1','backend':backend,'threshold':threshold,
        'image_sha256':hashlib.sha256(content).hexdigest() if content else None,'width':20,'height':20,
        'coordinates':'decoded_original_pixels_xyxy','exif_orientation':1,'semantic_class':'unknown','crs':None,
        'area_unit':'pixels','tile_manifest':[{'index':0,'bbox':[0,0,20,20]}],
        'masks':[{'bbox':[2,2,10,10],'score':.9,'candidate_index':1,'area_pixels':64,'source_tiles':[0],
            'merge_evidence':[],'semantic_class':'unknown','png_base64':base64.b64encode(b.getvalue()).decode()}],
        'candidate_limit_reached':False,'discarded_proposals':0,'model_load_ms':10,'inference_ms':5,'cloud_calls':0}


@pytest.fixture
def runtime(monkeypatch,tmp_path):
    from tests.test_local_specialists import output
    monkeypatch.setattr(settings(),'specialist_dir',tmp_path/'weights')
    monkeypatch.setattr(local,'verify_files',lambda path,recipe=local.RECIPE:None)
    monkeypatch.setattr(local,'download_pack',lambda source,progress,recipe=local.RECIPE:local.weights_dir(recipe))
    def run(path,backend,threshold=.5,content=None,guard=lambda:None,recipe=local.RECIPE):
        guard();r=output(backend,threshold,content) if recipe==local.RECIPE else result(backend,threshold,content)
        if recipe!=local.RECIPE:validate_result(r,backend,threshold,content)
        return r
    monkeypatch.setattr(local,'execute',run)


def ready(client):
    r=client.post('/api/specialists/installations',json={'pack_id':'sam2-1-tiny'});assert r.status_code==201,r.text
    row=r.json();assert process_once(row['job_id'],deliver=False)
    return next(x for x in client.get('/api/specialists/installations').json() if x['id']==row['id'])


def sample(client,row):
    b=io.BytesIO();Image.new('RGB',(20,20),'red').save(b,format='PNG')
    r=client.post('/api/specialists/installations/'+row['id']+'/samples',files={'file':('公开候选图.png',b.getvalue(),'image/png')})
    assert r.status_code==201,r.text
    assert process_once(r.json()['job_id'],deliver=False)
    return client.get('/api/specialists/runs').json()[0]


def test_two_packs_register_independently_and_store_mask_artifact(client,runtime):
    det=detection_ready(client);row=ready(client)
    assert row['recipe_hash']!=det['recipe_hash'] and 'masks' not in row['smoke']
    assert {p['pack_id'] for p in client.get('/api/specialists/catalog').json()['packs']}=={'rtdetr-v2-r18','sam2-1-tiny','sensevoice-small-v1','silero-vad-onnx-v1'}
    r=client.post('/api/specialists/installations/'+row['id']+'/register',json={'version':row['version']});assert r.status_code==200,r.text
    run=sample(client,row);assert run['status']=='succeeded' and run['pack_id']=='sam2-1-tiny'
    mask=run['result']['masks'][0];assert 'png_base64' not in mask
    path='/api/specialists/runs/'+run['id']+'/masks/'+mask['mask_evidence_id']
    response=client.get(path);assert response.status_code==200
    assert hashlib.sha256(response.content).hexdigest()==mask['mask_sha256']
    assert client.get(path.replace(mask['mask_evidence_id'],run['evidence_id'])).status_code==404
    with Session(engine()) as db:
        row=db.get(Evidence,mask['mask_evidence_id']);row.data={**row.data,'original_evidence_id':'changed'};db.commit()
    assert client.get(path).status_code==409


def test_mask_validation_checks_geometry_area_tiles_and_truthful_metadata():
    good=result(content=b'input');validate_result(good,'cpu',.5,b'input')
    bad=[]
    for changes in [{'width':-20,'height':-20},{'crs':'EPSG:4326'},{'cloud_calls':1},{'tile_manifest':[]},{'candidate_limit_reached':True}]:
        bad.append({**good,**changes})
    for changes in [{'area_pixels':63},{'bbox':[2,2,11,10]},{'source_tiles':[99]},{'semantic_class':'tree'},{'score':float('nan')},{'png_base64':'broken'},{'merge_evidence':[{'iou':.8}]}]:
        r=copy.deepcopy(good);r['masks'][0].update(changes);bad.append(r)
    for item in bad:
        with pytest.raises(ValueError):validate_result(item,'cpu',.5,b'input')


def test_video_path_rejects_segmentation_pack(client,runtime):
    row=ready(client)
    r=client.post('/api/videos',data={'installation_id':row['id']},files={'file':('x.mp4',b'xxxxftypisomxxxx','video/mp4')})
    assert r.status_code==422,r.text


def test_cancel_retry_retains_pack_binding(client,runtime):
    row=ready(client);run=sample(client,row)
    with Session(engine()) as db:
        old=db.get(SpecialistRun,run['id']);old.status='failed';old.data={**old.data,'error':'模拟损坏'};old.version+=1;db.commit();version=old.version
    r=client.post('/api/specialists/runs/'+run['id']+'/retry',json={'version':version});assert r.status_code==201,r.text
    retry=r.json();assert retry['pack_id']=='sam2-1-tiny';assert process_once(retry['job_id'],deliver=False)
    assert client.get('/api/specialists/runs').json()[0]['result']['schema']=='local-segmentation-v1'


def test_mask_in_task_receipt_is_readable_and_missing_mask_blocks_human_completion(client,runtime):
    row=ready(client);r=client.post('/api/specialists/installations/'+row['id']+'/register',json={'version':row['version']});assert r.status_code==200,r.text
    c=configuration();s=c['flows']['资料核验']['steps'][0]
    s.update(agent='多模感知智能体',role='',name='本地实例候选',execution={'capability_id':r.json()['capability_id'],
        'automation_action':'observe','arguments':{},'bindings':{'evidence_ids':{'source':'task_image_ids'}}})
    c['automation']['policies']['资料核验']['rules']['observe']={'mode':'自动运行'}
    c['flows']['资料核验']['confirmedSignature']=json.dumps(signature_value(c,c['flows']['资料核验']),ensure_ascii=False)
    release,_,check=prepared(client,c);assert release['available_business_ids'],check;enable(client,release)
    obj=object_row(client,release);image=upload(client,release).json();t=finish_job(client,create(client,release,obj,[image['id']]).json())
    assert t['status']=='workflow_waiting';receipt=t['workflow_runtime']['receipts'][0]
    mask=receipt['output']['segmentation']['masks'][0]
    path='/api/workspace/tasks/'+t['id']+'/masks/'+mask['mask_evidence_id']+'/content'
    assert client.get(path).status_code==200
    assert client.get(path.replace(mask['mask_evidence_id'],image['id'])).status_code==404
    with Session(engine()) as db:
        ev=db.get(Evidence,mask['mask_evidence_id']);content_path=settings().storage_dir/ev.storage_key;raw=content_path.read_bytes();content_path.write_bytes(b'corrupt')
    assert client.get(path).status_code==409
    assert complete(client,t).status_code==409
    content_path.write_bytes(raw)
    assert complete(client,t).status_code==200
    assert client.get('/api/instances/'+release['instance_id']+'/objects').json()[0]['version']==1


def test_download_verifies_selected_pack_instead_of_detection_recipe(tmp_path,monkeypatch):
    import httpx
    config=b'{"model_type":"sam2_video"}';files={'config.json':config,'model.safetensors':b'public-test-weights'}
    recipe={**local.SAM_RECIPE,'files':[{'name':name,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()} for name,raw in files.items()]}
    monkeypatch.setattr(settings(),'specialist_dir',tmp_path/'models')
    real_client=httpx.Client
    transport=httpx.MockTransport(lambda request:httpx.Response(200,content=files[request.url.path.rsplit('/',1)[1]]))
    monkeypatch.setattr(local.httpx,'Client',lambda **kwargs:real_client(transport=transport,**kwargs))
    path=local.download_pack('official',lambda stage,**kwargs:None,recipe)
    local.verify_files(path,recipe)
    with pytest.raises(ValueError):local.verify_files(path)


def test_corrupt_existing_mask_blob_cannot_be_reported_saved(client,runtime):
    row=ready(client);old=sample(client,row);mask=old['result']['masks'][0]
    with Session(engine()) as db:
        ev=db.get(Evidence,mask['mask_evidence_id']);path=settings().storage_dir/ev.storage_key;path.write_bytes(b'corrupt')
    new=sample(client,row)
    assert new['status']=='failed' and '校验失败' in new['error']
    assert client.get('/api/specialists/runs/'+new['id']+'/masks/'+mask['mask_evidence_id']).status_code==409
