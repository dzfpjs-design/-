"""Fixed official wheel extraction and a real VAD probe in the existing install job."""
import hashlib,os,zipfile
from pathlib import Path
import httpx
from fastapi import HTTPException
from sqlalchemy import select
from .local_audio_paths import vad_paths
from .local_vad import RECIPE,RECIPE_HASH,call,ready
from .local_audio_install import safe_path,download_file,probe
from .local_specialists import node_lock,guard_record
from .models import SpecialistInstallation,Capability
from .contracts import Manifest,digest
from .services import scoped,require,audit,register,validate_capability,publish
from .evidence import evidence_bytes

def extract(root,guard):
 root=safe_path(root);wheel=safe_path(root/RECIPE['wheel']['name'])
 if not wheel.is_file() or wheel.stat().st_size!=RECIPE['wheel']['bytes'] or hashlib.sha256(wheel.read_bytes()).hexdigest()!=RECIPE['wheel']['sha256']:raise ValueError('固定VAD发行包摘要不符')
 with zipfile.ZipFile(wheel) as z:
  for item in RECIPE['files']:
   target=safe_path(root/item['name']);guard()
   if target.is_file() and target.stat().st_size==item['bytes'] and hashlib.sha256(target.read_bytes()).hexdigest()==item['sha256']:
    continue
   guard();info=z.getinfo(item['archive_name'])
   if info.file_size!=item['bytes'] or info.file_size>3*1024*1024:raise ValueError('VAD模型归档长度不符')
   raw=z.read(info)
   if hashlib.sha256(raw).hexdigest()!=item['sha256']:raise ValueError('归档中的VAD模型或许可摘要不符')
   tmp=safe_path(root/(item['name']+'.verified-part'))
   fd=os.open(tmp,os.O_CREAT|os.O_TRUNC|os.O_WRONLY|os.O_NOFOLLOW,0o600)
   with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
   guard();os.replace(tmp,target)

def process(db,p,job):
 require(p,'admin');row=scoped(db,SpecialistInstallation,job.data['installation_id'],p,True);generation=job.data['generation'];identity=row.id
 if row.status in {'ready','cancelled'} or row.data['generation']!=generation:return
 if row.pack_id!=RECIPE['pack_id'] or row.data['recipe_hash']!=RECIPE_HASH or row.data['source']!='official' or row.data['backend']!='cpu':raise ValueError('VAD配方或官方CPU执行方式不一致')
 paths=vad_paths()
 if not paths.python.is_file():raise ValueError('本节点独立VAD环境未部署；不会自动安装依赖或转云')
 guard=lambda:guard_record(db,p,SpecialistInstallation,identity,generation)
 def progress(stage,**values):
  current=guard();current.status='installing';current.version+=1;current.data={**current.data,'stage':stage,'progress':values};db.commit()
 with node_lock():
  progress('license_check')
  wheel={**RECIPE['wheel']}
  def https_request(request):
   if request.url.scheme!='https':raise ValueError('公开模型下载必须使用HTTPS')
  with httpx.Client(trust_env=False,follow_redirects=True,timeout=httpx.Timeout(30,connect=8),event_hooks={'request':[https_request]}) as client:download_file(client,wheel,paths.weights,progress,guard)
  extract(paths.weights,guard);progress('model_check')
  result=call(probe(),{'channel':'mono','threshold':.5},guard,on_started=lambda pid:progress('model_check',pid=pid),_paths=paths);current=guard();current.status='ready';current.version+=1
  current.data={**current.data,'stage':'ready','error':None,'smoke':{'inference_performed':True,'deployment':'local','backend':'cpu','cloud_calls':0,'interval_count':len(result['activity']['intervals']),'probe_sha256':result['input_sha256'],'probe_kind':'synthetic_engineering_only_not_speech_quality','os_network_isolation':'macos_sandbox_exec_deny_network','domain_quality':'not_verified'},'artifact_hashes':{f['name']:f['sha256'] for f in RECIPE['files']},'domain_evaluation':'pending'}
  audit(db,p,'speech.activity.install.ready',current.id,{'recipe_hash':RECIPE_HASH,'cloud_calls':0});db.commit()

def installed(db,p,identity=None):
 row=scoped(db,SpecialistInstallation,identity,p) if identity else db.scalar(select(SpecialistInstallation).where(SpecialistInstallation.workspace_id==p.workspace_id,SpecialistInstallation.pack_id==RECIPE['pack_id']))
 if not row or row.pack_id!=RECIPE['pack_id'] or row.status!='ready' or row.data.get('recipe_hash')!=RECIPE_HASH or row.data.get('smoke',{}).get('inference_performed') is not True:raise HTTPException(409,'当前工作区VAD模型尚未安装并通过真实本机检查')
 try:ready()
 except (ValueError,OSError) as e:raise HTTPException(409,str(e))
 return row

def trace(row):return {'installation_id':row.id,'pack_id':row.pack_id,'generation':row.data['generation'],'recipe_hash':RECIPE_HASH,'backend':'cpu'}

def manifest(row):
 return Manifest(kind='specialist_model',namespace='platform_local',name='silero_vad_onnx',version='1.0.'+str(int(RECIPE_HASH[:12],16)),owner='平台本地语音活动检测',description=RECIPE['title']+'。'+RECIPE['limitations'],allowed_agent_roles=['perception','data'],
 input_schema={'type':'object','required':['evidence_ids'],'additionalProperties':False,'properties':{'evidence_ids':{'type':'array','minItems':1,'maxItems':1,'items':{'type':'string'}},'channel':{'type':'string','enum':['mono','mix','1','2','3','4','5','6','7','8']},'threshold':{'type':'number','minimum':.1,'maximum':.9}}},
 output_schema={'type':'object','required':['attributes','anomalies','speech_activity'],'additionalProperties':False,'properties':{'attributes':{'type':'object'},'anomalies':{'type':'array','maxItems':0},'speech_activity':{'type':'object'}}},
 execution={'adapter':'local_speech_activity','installation_id':row.id,'generation':row.data['generation'],'recipe_hash':RECIPE_HASH,'backend':'cpu','side_effect':False},data_policy={'deployment':'local','cloud_allowed':False,'external_allowed':False},specification={'license':RECIPE['license'],'license_evidence':RECIPE['license_evidence'],'domain_evaluation':'pending','automatic_domain_use':False,'word_timestamps':False,'speaker_identity':False,'environment_classifier':False,'time_step_seconds':.032})

def validate_manifest(db,p,value):
 row=installed(db,p,value.execution.get('installation_id'))
 if value.model_dump()!=manifest(row).model_dump():raise HTTPException(422,'VAD能力必须使用当前安装、固定CPU配方与只读原音频契约')

def publish_capability(db,p,row):
 cap=register(db,p,manifest(row));validate_capability(db,p,cap.id,cap.version);publish(db,p,cap.id,cap.version,'仅登记语音活动候选，不更新对象、不发布经验、不授予设备或云外发权限');return cap

def detect(db,p,installation_id,evidence_id,configuration,execution):
 row=installed(db,p,installation_id);generation=row.data['generation']
 if execution.get('generation')!=generation or execution.get('recipe_hash')!=RECIPE_HASH or execution.get('backend')!='cpu':raise HTTPException(409,'已登记VAD能力与当前安装代次或配方不一致，请重新核对登记')
 ev,content=evidence_bytes(db,p,evidence_id)
 if ev.media_type not in {'audio/mpeg','audio/mp3','audio/wav','audio/x-wav','audio/wave','audio/flac','audio/x-flac','audio/ogg','audio/mp4','audio/x-m4a'} or not 0<len(content)<=20*1024*1024:raise HTTPException(422,'VAD仅支持已保存的有界音频原件，不能读取其他文件')
 def guard():
  db.expire_all()
  current=installed(db,p,row.id)
  if current.data['generation']!=generation:raise ValueError('VAD安装代次已变化，结果不接受')
 with node_lock():result=call(content,configuration,guard)
 return result
