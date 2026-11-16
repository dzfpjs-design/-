"""Actual localhost workers + public RGB/synthetic spatial tags. Never field accuracy."""
import os,sys,json,time,hashlib,subprocess
from pathlib import Path
from uuid import uuid4
from contextlib import contextmanager
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session
from generic_agents.models import User,Workspace,Membership,CloudCall,Job,GeoScene,GeoRun
from generic_agents.db import engine
from generic_agents.security import password_hash
ROOT=Path(__file__).resolve().parents[1];os.chdir(ROOT)
ACCESS=ROOT/'.runtime/geo-access.json';OUT=ROOT/'docs/evidence/geo-v06';OUT.mkdir(parents=True,exist_ok=True)
FIXTURE=ROOT/'.runtime/geo-fixture/public-photo-synthetic-map.tif'
def save(name,value):(OUT/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
def access():return json.loads(ACCESS.read_text())
@contextmanager
def connection():
    a=access()
    with httpx.Client(base_url='http://127.0.0.1:8765',trust_env=False,timeout=30) as c:
        for attempt in range(40):
            try:r=c.post('/api/login',json={k:a[k] for k in ['username','password']});r.raise_for_status();break
            except httpx.ConnectError:
                if attempt==39:raise
                time.sleep(.25)
        c.headers.update({'X-CSRF-Token':r.json()['csrf_token'],'X-Workspace-ID':a['workspace_id']});yield c

def call(c,method,path,body=None):
    r=c.request(method,'/api'+path,**({'json':body} if body is not None else {}));r.raise_for_status();return r.json()
def wait(read,field='status',done=('ready','succeeded','failed','cancelled','parsed')):
    deadline=time.monotonic()+300
    while True:
        row=read()
        if row[field] in done:return row
        if time.monotonic()>deadline:raise TimeoutError('实际后台仍未完成，记录保留')
        time.sleep(.4)
phase=sys.argv[1]
if phase=='prepare':
    if not ACCESS.exists():
        a={'username':'geo_'+uuid4().hex[:8],'password':uuid4().hex}
        with Session(engine()) as db:
            user=User(username=a['username'],password_hash=password_hash(a['password']));workspace=Workspace(name='公开照片合成坐标工程核查');db.add_all([user,workspace]);db.flush();db.add(Membership(user_id=user.id,workspace_id=workspace.id,role='admin'));db.commit();a['workspace_id']=workspace.id
            save('baseline',{'workspace_id':workspace.id,'cloud_ids':sorted(db.scalars(select(CloudCall.id)).all())})
        fd=os.open(ACCESS,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w') as f:json.dump(a,f)
    if not FIXTURE.exists():
        FIXTURE.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        script=FIXTURE.parent/'make.py';script.write_text('''import sys,numpy as np,rasterio
from PIL import Image
from affine import Affine
from rasterio.enums import ColorInterp
im=np.asarray(Image.open(sys.argv[1]).convert('RGB').resize((1280,960)));valid=np.full(im.shape[:2],255,dtype='uint8');valid[:40,:40]=0
with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=True):
 with rasterio.open(sys.argv[2],'w',driver='GTiff',width=1280,height=960,count=3,dtype='uint8',compress='deflate',tiled=True,blockxsize=256,blockysize=256,crs='EPSG:32650',transform=Affine(2,.5,500000,.25,-3,2600000)) as dst:
  dst.write(im.transpose(2,0,1));dst.colorinterp=(ColorInterp.red,ColorInterp.green,ColorInterp.blue);dst.write_mask(valid)
''')
        result=subprocess.run([str(ROOT/'.runtime/venvs/geo/bin/python'),str(script),str(ROOT/'.runtime/rtdetr-bootstrap/coco-cats.jpg'),str(FIXTURE)],capture_output=True,text=True,timeout=30);assert result.returncode==0,result.stderr[-500:]
    with connection() as c:
        installs=call(c,'GET','/specialists/installations');row=next((x for x in installs if x['pack_id']=='sam2-1-tiny'),None)
        if not row:row=call(c,'POST','/specialists/installations',{'pack_id':'sam2-1-tiny','source':'official','backend':'cpu'})
        row=wait(lambda:next(x for x in call(c,'GET','/specialists/installations') if x['id']==row['id']));assert row['status']=='ready',row.get('error')
        scenes=call(c,'GET','/geo/scenes')
        if not scenes:
            with FIXTURE.open('rb') as f:r=c.post('/api/geo/scenes',data={'request_id':str(uuid4())},files={'file':('公开照片合成坐标（不是遥感实测）.tif',f,'image/tiff')});r.raise_for_status();scene=r.json()
        else:scene=scenes[0]
        scene=wait(lambda:call(c,'GET','/geo/scenes/'+scene['id']));assert scene['status'] in {'parsed','ready'},scene.get('error')
        if scene['status']=='parsed':
            scene=call(c,'PUT','/geo/scenes/'+scene['id']+'/configuration',{'version':scene['version'],'rgb_bands':[1,2,3],'map_image_confirmed':True,'observed_at':None});scene=wait(lambda:call(c,'GET','/geo/scenes/'+scene['id']));assert scene['status']=='ready',scene.get('error')
        runs=call(c,'GET','/geo/scenes/'+scene['id']+'/runs')
        if not runs:run=call(c,'POST','/geo/scenes/'+scene['id']+'/runs',{'version':scene['version'],'request_id':str(uuid4()),'installation_id':row['id']})
        else:run=runs[0]
        run=wait(lambda:call(c,'GET','/geo/runs/'+run['id']));assert run['status']=='succeeded',run.get('error')
        assert run['result']['masks'] and len(run['result']['tile_manifest'])==2 and run['result']['geometry_failures']==0
        if not run['review']:run=call(c,'PUT','/geo/runs/'+run['id']+'/review',{'version':run['version'],'note':'公开照片合成坐标工程核查：仅验证本地分割、nodata与原坐标面积机制；不是遥感样本，不证明实地精度。','decisions':{'1':'uncertain'}})
        save('selection',{'scene_id':scene['id'],'run_id':run['id'],'installation_id':row['id'],'input_sha256':hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),'fixture':'public COCO photograph resized locally and assigned synthetic map transform, NOT real remote sensing','domain_quality_verified':False,'ground_accuracy_verified':False})
    print('实际配置worker已保存GeoTIFF原件、RGB设置、两窗口本地CPU候选和核查备注。')
elif phase in {'snapshot','restart-check'}:
    with connection() as c:
        scenes=call(c,'GET','/geo/scenes');assert len(scenes)==1;scene=scenes[0];runs=call(c,'GET','/geo/scenes/'+scene['id']+'/runs');assert len(runs)==1;run=runs[0];r=run['result']
        assert run['status']=='succeeded' and run['review']['decisions']=={'1':'uncertain'} and r['cloud_calls']==0
        saved={}
        for name,url,expected in [('original','/geo/scenes/'+scene['id']+'/original',scene['input_sha256']),('preview','/geo/scenes/'+scene['id']+'/preview',scene['preview']['sha256']),('rgb','/geo/runs/'+run['id']+'/rgb',r['image_sha256'])]+[('mask-'+str(m['candidate_index']),'/geo/runs/'+run['id']+'/masks/'+m['mask_evidence_id'],m['mask_sha256']) for m in r['masks']]:
            response=c.get('/api'+url);response.raise_for_status();assert hashlib.sha256(response.content).hexdigest()==expected;saved[name]=expected
        export=c.get('/api/geo/runs/'+run['id']+'/native-geojson');export.raise_for_status();saved['export']=hashlib.sha256(export.content).hexdigest()
        assert export.json()['review']['note']==run['review']['note'] and export.json()['candidate_only']
        with Session(engine()) as db:
            clouds=sorted(db.scalars(select(CloudCall.id)).all());assert clouds==json.loads((OUT/'baseline.json').read_text())['cloud_ids']
            jobs=[{'id':x.id,'version':x.version,'status':x.status,'kind':x.kind,'data':x.data} for x in db.scalars(select(Job).where(Job.workspace_id==access()['workspace_id']).order_by(Job.id))]
        record={'scenes':scenes,'runs':runs,'jobs':jobs,'artifact_hashes':saved,'cloud_ids':clouds}
        if phase=='snapshot':save('before-restart',record)
        else:
            assert record==json.loads((OUT/'before-restart.json').read_text()),'重启后记录或原件变化';save('after-restart',record);save('runtime',{'records_identical':True,'api_setup_media_restart':True,'database_restart':False,'actual_backend':r['backend'],'tile_count':len(r['tile_manifest']),'candidates':len(r['masks']),'geometry_failures':r['geometry_failures'],'valid_pixels':run['rgb_checkpoint']['valid_pixels'],'cloud_calls_created':0,'entity_state_updated':False,'browser_verified':False,'domain_quality_verified':False,'production_verified':False})
    print('实际服务保存记录与原件、预览、全部掩膜、空间导出摘要一致；未新增云调用。')
else:raise SystemExit('Unknown phase')
