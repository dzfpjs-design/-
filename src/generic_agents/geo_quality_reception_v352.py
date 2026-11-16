"""Independent reception of new geospatial quality results.

No legacy result is retroactively signed. Outbox, producer audit and parent
success commit together. Consumer verifies bytes and pixel policy before ACK.
"""
import copy,hashlib,json,os,subprocess,tempfile
from pathlib import Path
from sqlalchemy import select
from fastapi import HTTPException
from . import models as m
from .config import settings
from .contracts import digest
from .security import Principal
from .services import scoped,enqueue_event,require
from .geo_storage import path_for

KIND='data.geo_quality_report_v352'
ISSUED='geo.quality_delivery_issued_v352'
RECEIVED='geo.quality_delivery_received_v352'
RETRY='geo.quality_delivery_retry_v352'
TYPE='geo_quality_evidence_v352'
VERIFY=Path(__file__).with_name('geo_quality_verify_v352.py')
VERIFY_HASH=hashlib.sha256(VERIFY.read_bytes()).hexdigest()


def need(value,message='质量报告的独立接收依据无法核对；原件保留'):
    if not value:raise HTTPException(409,message)


def key(job):return 'geo-quality-v352:'+job.id


def payload(job):
    return {'schema':'geo-quality-delivery-v352','job_id':job.id,'result_hash':job.data['result_hash'],
        'sources_hash':job.data['sources_hash'],'body_hash':job.data['body_hash'],
        'runner_hash':job.data['runner_hash'],'verifier_hash':VERIFY_HASH,
        'source_association':'user_declared_unverified','classification_accuracy':'not_verified',
        'buffer_effectiveness':'not_verified','candidate_only':True,'object_state_updated':False}


def audit_rows(db,p,action,identity):
    return list(db.scalars(select(m.Audit).where(m.Audit.workspace_id==p.workspace_id,
        m.Audit.action==action,m.Audit.object_id==identity).limit(2)))


def memories(db,p,event):
    return list(db.scalars(select(m.Memory).where(m.Memory.workspace_id==p.workspace_id,
        m.Memory.data['type'].as_string()==TYPE,m.Memory.data['event_id'].as_string()==event.id).limit(2)))


def emit(db,p,job):
    from .geo_quality_v351 import checked
    checked(db,p,job);need(job.status=='running' and job.data['request']['schema']=='geo-quality-request-v352')
    value=payload(job);prior=db.scalar(select(m.Event).where(m.Event.workspace_id==p.workspace_id,m.Event.idempotency_key==key(job)))
    if prior:
        issued=audit_rows(db,p,ISSUED,prior.id)
        need(len(issued)==1 and issued[0].actor_id==job.data['actor_id'] and
            issued[0].data=={'payload_hash':digest(value),'job_id':job.id} and prior.kind==KIND and prior.data==value)
        return prior
    event=enqueue_event(db,p,KIND,key(job),value)
    need(event is not None and event.kind==KIND and event.data==value and not audit_rows(db,p,ISSUED,event.id))
    db.add(m.Audit(workspace_id=p.workspace_id,actor_id=job.data['actor_id'],action=ISSUED,object_id=event.id,
        data={'payload_hash':digest(value),'job_id':job.id}));db.flush();return event


def checked_event(db,p,job,event):
    from .geo_quality_v351 import checked
    checked(db,p,job);need(job.status=='succeeded' and job.data['request']['schema']=='geo-quality-request-v352')
    need(event.workspace_id==p.workspace_id and event.kind==KIND and event.idempotency_key==key(job)
        and event.data==payload(job) and event.status in {'pending_delivery','received','blocked_delivery'})
    issued=audit_rows(db,p,ISSUED,event.id)
    need(len(issued)==1 and issued[0].actor_id==job.data['actor_id'] and issued[0].data==
        {'payload_hash':digest(event.data),'job_id':job.id})
    return event


def expected_verification(native):
    sides=native['sides'];need(type(sides) is list and len(sides)==2
        and [x.get('side') for x in sides]==['before','after'])
    fields=('class_counts','image_invalid_pixels','raw_eligible_pixels','valid_pixels',
        'buffer_excluded_pixels','unknown_or_excluded_pixels')
    return {'schema':'quality-independent-check-v352','total_pixels':native['total_pixels'],
        'common_valid_pixels':native['common_valid_pixels'],'unknown_or_excluded_pixels':native['unknown_or_excluded_pixels'],
        'sides':[{k:value[k] for k in fields} for value in sides],
        'classification_accuracy':'not_verified','source_association':'user_declared_unverified',
        'buffer_effectiveness':'not_verified','cloud_calls':0}


def _verify(db,p,job):
    sources=job.data['sources'];result=job.data['result'];entries={x['filename']:x for x in result['artifacts']}
    paths={key:path_for(db,p,value['evidence_id'])[1] for key,value in sources.items()}
    paths.update({key:path_for(db,p,value['evidence_id'])[1] for key,value in entries.items()})
    need(hashlib.sha256(VERIFY.read_bytes()).hexdigest()==VERIFY_HASH and settings().geo_python.is_file())
    argv=[str(settings().geo_python.absolute()),str(VERIFY),'--before',str(paths['before_image']),'--after',str(paths['after_image']),
        '--before-quality',str(paths['before_quality']),'--after-quality',str(paths['after_quality']),
        '--before-derived',str(paths['before.tif']),'--after-derived',str(paths['after.tif']),
        '--before-mask',str(paths['before-valid.png']),'--after-mask',str(paths['after-valid.png']),
        '--common-mask',str(paths['common-valid.png'])]
    env={k:v for k,v in os.environ.items() if k in {'PATH','HOME','TMPDIR','LANG','LC_ALL','SYSTEMROOT'}}
    env.update(PROJ_NETWORK='OFF',GDAL_PAM_ENABLED='NO',GDAL_DISABLE_READDIR_ON_OPEN='EMPTY_DIR')
    try:
        with tempfile.TemporaryFile(mode='w+') as out,tempfile.TemporaryFile(mode='w+') as err:
            completed=subprocess.run(argv,stdin=subprocess.DEVNULL,stdout=out,stderr=err,env=env,timeout=120,start_new_session=True)
            need(completed.returncode==0,'质量报告的影像或掩膜核对未通过；原件保留')
            out.seek(0);raw=out.read(65537);need(len(raw)<=65536);value=json.loads(raw)
    except (subprocess.TimeoutExpired,ValueError,TypeError,OSError):
        raise HTTPException(409,'质量报告的本地核对失败或超时；原件保留') from None
    need(value==expected_verification(result['native']))
    return value


def receipt_value(job,event,verified):
    return {'schema':'geo-quality-data-reception-v352','event_id':event.id,'job_id':job.id,
        'event_payload_hash':digest(event.data),'result_hash':job.data['result_hash'],
        'sources_hash':job.data['sources_hash'],'verifier_hash':VERIFY_HASH,'verification':verified,
        'receiver':'data_management','candidate_only':True,'not_authoritative':True,
        'object_state_updated':False,'model_result_receipt':'not_asserted'}


def receive(db,event):
    p=Principal('geo-quality-data-receiver',event.workspace_id,'viewer','geo-quality-data-receiver')
    job=scoped(db,m.Job,event.data['job_id'],p);checked_event(db,p,job,event)
    need(event.status=='pending_delivery' and not memories(db,p,event) and not audit_rows(db,p,RECEIVED,event.id))
    verified=_verify(db,p,job);checked_event(db,p,job,event)
    receipt=receipt_value(job,event,verified);checksum=digest(receipt)
    memory=m.Memory(workspace_id=p.workspace_id,status='candidate',text='已接收影像质量依据；来源关联和分类准确性仍待核验',
        data={'type':TYPE,'event_id':event.id,'receipt':receipt,'receipt_hash':checksum})
    db.add(memory);db.flush()
    db.add(m.Audit(workspace_id=p.workspace_id,actor_id=job.data['actor_id'],action=RECEIVED,object_id=event.id,
        data={'receipt_hash':checksum,'memory_id':memory.id,'verifier_hash':VERIFY_HASH,'receiver':'data_management'}))
    db.flush()


def _status(db,p,job):
    if job.data['request']['schema']=='geo-quality-request-v351':return {'state':'legacy_not_issued'}
    event=db.scalar(select(m.Event).where(m.Event.workspace_id==p.workspace_id,m.Event.idempotency_key==key(job)))
    if job.status!='succeeded':
        # The Job may have been read immediately before the producer's
        # terminal commit, while this subsequent query sees its event. Refresh
        # the same original row instead of reporting a false integrity error.
        if event is not None:db.refresh(job)
    if job.status!='succeeded':
        need(event is None);return {'state':'waiting_for_processing' if job.status in {'queued','running'} else 'not_issued'}
    need(event is not None);checked_event(db,p,job,event)
    rows=memories(db,p,event);audits=audit_rows(db,p,RECEIVED,event.id)
    if event.status!='received' and (rows or audits):
        # The receiver may commit after the event was read and before the
        # independent receipt query. Re-read that same event before judging.
        db.refresh(event);checked_event(db,p,job,event)
        rows=memories(db,p,event);audits=audit_rows(db,p,RECEIVED,event.id)
    if event.status!='received':
        need(not rows and not audits);return {'state':'blocked' if event.status=='blocked_delivery' else 'pending'}
    need(len(rows)==len(audits)==1 and event.received_at is not None);memory=rows[0];record=memory.data.get('receipt');checksum=memory.data.get('receipt_hash')
    need(type(record) is dict and checksum==digest(record) and record==receipt_value(job,event,record.get('verification'))
        and audits[0].actor_id==job.data['actor_id'] and audits[0].data=={'receipt_hash':checksum,'memory_id':memory.id,'verifier_hash':VERIFY_HASH,'receiver':'data_management'}
        and memory.status=='candidate')
    verified=record['verification'];native=job.data['result']['native']
    need(verified==expected_verification(native))
    return {'state':'received','receipt_hash':checksum,'received_at':event.received_at.isoformat() if event.received_at else None}


def status(db,p,job):
    try:return _status(db,p,job)
    except HTTPException:raise
    except (ValueError,KeyError,TypeError,AttributeError,OSError,StopIteration):
        raise HTTPException(409,'质量报告的接收依据无法核对；原件保留') from None


def retry(db,p,job):
    require(p,'admin','operator');event=db.scalar(select(m.Event).where(m.Event.workspace_id==p.workspace_id,
        m.Event.idempotency_key==key(job)).with_for_update());need(event is not None);checked_event(db,p,job,event)
    need(event.status=='blocked_delivery' and not memories(db,p,event) and not audit_rows(db,p,RECEIVED,event.id),
        '只有接收受阻的原报告可以重试；已保存记录保留')
    event.status='pending_delivery';db.add(m.Audit(workspace_id=p.workspace_id,actor_id=p.user_id,action=RETRY,
        object_id=event.id,data={'payload_hash':digest(event.data),'job_id':job.id}));db.flush()


def deliver_pending(*,workspace_id):
    """Only consume this quality family inside one explicitly named workspace."""
    from uuid import UUID
    if not isinstance(workspace_id,str) or str(UUID(workspace_id))!=workspace_id:
        raise ValueError('质量接收处理器必须指定标准工作区编号')
    from .worker import deliver_events
    deliver_events(workspace_id=workspace_id,event_kind=KIND)
