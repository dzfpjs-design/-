"""Scoped saved business records and observation receipts, without network reads."""
import json
from datetime import datetime
from fastapi import HTTPException
from sqlalchemy import select
from .contracts import digest
from .models import Entity,Observation,Capability,Invocation,Evidence
from .services import scoped,validate
from .evidence import evidence_bytes

def fields(values,labels=None):
    if not isinstance(values,dict):raise ValueError('字段不是已保存的对象')
    output=[];truncated=len(values)>100
    for key,value in list(values.items())[:100]:
        if isinstance(value,str) and len(value)>2000:value=value[:2000];truncated=True
        if isinstance(value,(dict,list)) and len(json.dumps(value,ensure_ascii=False).encode())>8000:
            value='结构化内容超出本次展示范围，请核对原登记或提取记录';truncated=True
        name=str((labels or {}).get(key,key))
        if len(name)>100:name=name[:100]+'…';truncated=True
        output.append({'name':name, 'value':value})
    return output,truncated

def collect(db,p,request,release,objects):
    from .evidence_assessment import ref,MAX_SOURCES
    result=[];dependencies=[];truncated=False
    names={r['id']:r['name'] for r in request['objects']}
    specs={s['key']:s for s in release.data['snapshot']['object_types']}
    if 'archive' in request['required_modalities']:
        for obj in objects:
            good=True;issue=None;values=[];cut=False
            try:
                schema=release.data['snapshot']['entity_schemas'][obj.entity_type]
                validate(schema,obj.data)
                values,cut=fields(obj.data,{f['key']:f['label'] for f in specs[obj.entity_type]['fields']})
            except (HTTPException,KeyError,TypeError,ValueError):good=False;issue='业务档案未通过当前实例字段校验，不能显示为已核对资料。'
            dependencies.append(ref(obj,extended=True));truncated=truncated or cut
            result.append({'key':'Entity:'+obj.id,'modality':'archive','kind':'business_archive','name':names[obj.id],
                'source_ref':ref(obj,extended=True),'entity_ids':[obj.id],'task_id':None,'task_title':None,
                'association_basis':'saved_business_record_not_verified_observation','status':'saved',
                'integrity_verified':good,'integrity_basis':'instance_field_schema_only','integrity_issue':issue,
                'source_times':[{'value':obj.observed_at.isoformat() if obj.observed_at else None,'basis':'user_declared_unverified' if obj.observed_at else 'unknown'}],
                'source_time_verified':False,'source_sha256':[],'quality_gaps':[], 'review_state':'not_applicable',
                'metrics':{'saved_fields':len(values)},'reported_fields':values,'fields_truncated':cut,
                'record_stage':'saved_record','domain_quality_verified':False})
    if 'observations' in request['required_modalities']:
        rows=list(db.scalars(select(Observation).where(Observation.workspace_id==p.workspace_id,
            Observation.entity_id.in_(names),Observation.status!='extracted_unclocked').order_by(Observation.created_at.desc(),Observation.id).limit(MAX_SOURCES+1)))
        truncated=truncated or len(rows)>MAX_SOURCES
        native_seen=set()
        for row in rows[:MAX_SOURCES]:
            if row.data.get('native_packet'):
                native_key=(row.source_id,row.source_record_id)
                if native_key in native_seen:continue
                native_seen.add(native_key)
                from .native_receipts import checked as checked_native,TYPES
                native=row.data['native_packet'];values=[];shas=[];issue=None;review='blocked';cut=False
                dependencies.append(ref(row,extended=True))
                try:
                    original=checked_native(db,p,row);values,cut=fields(native['attributes']);shas=native['source_sha256'];review=original['review_state']
                    kind,identity=native['source_key'].split(':',1)
                    dependencies.append(ref(scoped(db,TYPES[kind],identity,p),extended=True))
                    dependencies.append(ref(scoped(db,Capability,row.source_id,p),extended=True))
                    for identity in native['evidence_ids']:dependencies.append(ref(scoped(db,Evidence,identity,p),extended=True))
                    if native['task_id']:
                        from .models import Task
                        dependencies.append(ref(scoped(db,Task,native['task_id'],p),extended=True))
                except (HTTPException,KeyError,TypeError,ValueError,OSError):issue='原处理、原件或对象关联需核对，不能显示归集值。'
                result.append({'key':'Observation:'+row.id,'native_source_key':native.get('source_key'),'modality':'observations','kind':'native_receipt',
                    'name':names[row.entity_id]+' · 本地处理归集','source_ref':ref(row,extended=True),'entity_ids':[row.entity_id],
                    'task_id':native.get('task_id'),'task_title':None,'association_basis':'original_task_or_device_binding_not_verified_coverage','status':row.status,
                    'integrity_verified':issue is None,'integrity_issue':issue,'source_times':native.get('source_times',[]),'source_time_verified':False,
                    'source_sha256':shas,'quality_gaps':[],'review_state':review,'metrics':{'saved_fields':len(values)},'reported_fields':values,
                    'fields_truncated':cut,'record_stage':'processed','domain_quality_verified':False})
                truncated=truncated or cut
                continue
            dependencies.append(ref(row,extended=True));good=True;issue=None;values=[];shas=[];cut=False
            review='unreviewed';basis='saved_ingest_request';source_name='登记观测来源';stage='saved_record'
            try:
                source=scoped(db,Capability,row.source_id,p)
                if source.kind!='source':raise ValueError('不是登记来源')
                dependencies.append(ref(source,extended=True));source_name=source.data.get('description') or source.name
                original=row.data.get('original_request')
                if original is not None:
                    if digest(original)!=row.data['input_hash']:raise ValueError('原登记内容不一致')
                    when=datetime.fromisoformat(original['observed_at']) if original.get('observed_at') else None
                    if (when is None and not row.data.get('candidate_only')) or (when is not None and when.tzinfo is None) or when!=row.observed_at:raise ValueError('原观测时间不一致')
                    for key in ['source_id','source_record_id','processing_version','entity_id','business_day']:
                        if original[key]!=getattr(row,key):raise ValueError('原来源或对象不一致')
                    if original['attributes']!=row.data.get('attributes') or original.get('evidence_ids',[])!=row.data.get('evidence_ids',[]):raise ValueError('原属性或证据不一致')
                    if original['extractor_id']!=row.data.get('extractor_id'):raise ValueError('原提取器不一致')
                elif row.status!='processed':raise ValueError('旧未处理登记缺少可核对的原请求')
                for identity in row.data.get('evidence_ids',[]):
                    ev,_=evidence_bytes(db,p,identity);shas.append(ev.sha256);dependencies.append(ref(ev,extended=True))
                extracted=row.data.get('extracted',{})
                if row.status=='processed':
                    from .observation_integrity import checked_report
                    report=checked_report(db,p,row,allow_candidates=True)
                    call=scoped(db,Invocation,report['invocation_id'],p)
                    dependencies.append(ref(call,extended=True))
                    basis=report['basis']
                    stage='processed';values,cut=fields(report['attributes'])
                    decision=report['review']
                    review='confirmed' if decision in {'confirmed','verified'} else 'rejected' if decision=='rejected' else 'unreviewed'
                else:values,cut=fields(row.data.get('attributes',{}))
            except (HTTPException,KeyError,TypeError,ValueError,OSError):
                good=False;issue='原登记、来源、提取调用或文件未通过核对；不能显示其中的数值。';values=[];shas=[]
            truncated=truncated or cut
            result.append({'key':'Observation:'+row.id,'modality':'observations','kind':'registered_observation',
                'name':names[row.entity_id]+' · '+source_name,'source_ref':ref(row,extended=True),
                'entity_ids':[row.entity_id],'task_id':None,'task_title':None,'association_basis':'saved_observation_assignment_not_verified_coverage',
                'status':row.status,'integrity_verified':good,'integrity_basis':basis,'integrity_issue':issue,
                'source_times':[{'value':row.observed_at.isoformat() if row.observed_at else None,'basis':'reported_observation_time_unverified' if row.observed_at else 'unknown'}],
                'source_time_verified':False,'source_sha256':shas,'quality_gaps':[],'review_state':review,
                'metrics':{'saved_fields':len(values)},'reported_fields':values,'fields_truncated':cut,
                'record_stage':stage,'domain_quality_verified':False})
    return result,dependencies,truncated
