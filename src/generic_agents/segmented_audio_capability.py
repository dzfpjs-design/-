"""Registered read-only local pipeline; invocation computes and saves actual new ASR."""
from types import SimpleNamespace
from uuid import UUID
from fastapi import HTTPException
from .contracts import Manifest,digest
from .services import require,register,validate_capability,publish,scoped
from .models import SegmentedAudioRun,SpeechActivityRun
from .local_segmented import RECIPE_HASH,installations,source_value,process,checked

def manifest(db,p):
 traces=installations(db,p);version=digest({'recipe':RECIPE_HASH,'installations':traces})
 return Manifest(kind='specialist_model',namespace='platform_local',name='vad_sensevoice_segments',version='1.0.'+str(int(version[:12],16)),owner='本地分段语音处理',description='固定本地VAD区间按明确语言执行SenseVoice。保留原音频与200ms识别上下文，段边界不等于词时间。',allowed_agent_roles=['perception','data'],
 input_schema={'type':'object','required':['source_run_id','source_snapshot_hash','indexes','language'],'additionalProperties':False,'properties':{'source_run_id':{'type':'string'},'source_snapshot_hash':{'type':'string','pattern':'^[a-f0-9]{64}$'},'indexes':{'type':'array','maxItems':250,'uniqueItems':True,'items':{'type':'integer','minimum':0}},'language':{'type':'string','enum':['auto','zh','yue','en','ja','ko']}}},
 output_schema={'type':'object','required':['attributes','anomalies','segmented_audio','run_id'],'additionalProperties':False,'properties':{'attributes':{'type':'object'},'anomalies':{'type':'array','maxItems':0},'segmented_audio':{'type':'object'},'run_id':{'type':'string'}}},
 execution={'adapter':'local_segmented_audio','recipe_hash':RECIPE_HASH,'installations':traces,'backend':'cpu','side_effect':False},data_policy={'deployment':'local','cloud_allowed':False,'external_allowed':False},specification={'automatic_domain_use':False,'domain_evaluation':'pending','word_timestamps':False,'speaker_identity':False,'environment_classifier':False,'context_ms':200,'license_chain':'pending'})
def validate_manifest(db,p,value):
 if value.model_dump()!=manifest(db,p).model_dump():raise HTTPException(422,'分段语音能力必须固定本地CPU、当前两套安装及只读来源，不得扩大权限')
def register_pipeline(db,p,source_id,source_hash):
 require(p,'admin');s,_=source_value(db,p,source_id)
 if scoped(db,SpeechActivityRun,source_id,p).data.get('workflow_task_id'):raise HTTPException(409,'业务来源请使用原业务能力，不能在资源页另行登记')
 if digest(s)!=source_hash:raise HTTPException(409,'用于登记的语音区间来源已变化')
 cap=register(db,p,manifest(db,p));validate_capability(db,p,cap.id,cap.version);publish(db,p,cap.id,cap.version,'本地分段转写候选，不授予设备控制、云外发、对象状态或经验发布权限');return cap

def execute(db,p,cap,inputs,key):
 require(p,'admin','operator');validate_manifest(db,p,Manifest.model_validate(cap.data))
 from .segmented_audio_api import create_record
 identity=str(UUID(digest({'capability':cap.content_hash,'invocation':key,'inputs':inputs})[:32]));row=create_record(db,p,identity,inputs['source_run_id'],inputs['source_snapshot_hash'],{'indexes':inputs['indexes'],'language':inputs['language']},queue=False)
 if row.status!='succeeded':
  if row.status=='cancelled':raise HTTPException(409,'原分段请求已取消，已有段保留')
  process(db,p,SimpleNamespace(data={'run_id':row.id,'generation':row.data['generation']}));row=scoped(db,SegmentedAudioRun,row.id,p)
 checked(db,p,row)
 if row.status!='succeeded':raise HTTPException(409,'分段转写未完成，原件和已有段保留')
 return {'attributes':{'segmented_text_candidates':[s['text'] for s in row.data['segments']],'not_authoritative':True},'anomalies':[],'segmented_audio':row.data['result'],'run_id':row.id}
