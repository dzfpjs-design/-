"""Bind a saved video candidate to a user-initiated, normally approved workflow.

A candidate remains evidence, never a verified physical anomaly. This does not
create an automatic-event authorization and never initializes a workflow itself.
"""
import copy
from fastapi import HTTPException
from .contracts import Contract, digest
from .models import Task, VideoRun, VideoFrame, VideoAlert, Event
from .services import scoped, audit
from .evidence import evidence_bytes
from pydantic import Field, StrictInt

KEY='video_workflow_handoff_v347'
SCHEMA='video-workflow-handoff-v347'
FRAME_FIELDS=('frame_sha256','pts','time_base','seconds','width','height','segment','detections','tracks','events','gap_before')

class Selection(Contract):
    source_task_id: str
    source_task_version: StrictInt = Field(ge=1)
    basis_hash: str = Field(pattern=r'^[a-f0-9]{64}$')


def need(value,message='原视频线索或交接依据无法核对，原记录保留'):
    if not value: raise HTTPException(409,message)


def is_candidate(task,business):
    return bool(business.get('execution_mode')=='confirmed_graph_workflow'
        and task.status in {'workflow_pending','workflow_waiting','workflow_cancelled','workflow_completed'}
        and not task.data.get('graph_runtime')
        and task.data.get('trigger')=='video.presence' and task.data.get('perception_event'))


def _origin(db,p,source,*,lock=False,allow_reviewed=True,allow_terminal=False):
    value=source.data.get('perception_event')
    need(type(value) is dict and source.data.get('trigger')=='video.presence'
         and (source.status in {'workflow_pending','workflow_waiting'} or
              allow_terminal and source.status in {'workflow_cancelled','workflow_completed'})
         and not source.data.get('graph_runtime'))
    required=('run_id','frame_id','video_alert_id','original_evidence_id','frame_evidence_id',
              'original_sha256','frame_sha256','label','label_id','track_ref','from_seconds','to_seconds',
              'source_clock_utc','association','not_physical_identity','device_action_authorized')
    need(all(k in value for k in required) and value['source_clock_utc']=='unknown'
         and value['association']=='observed_scene' and value['not_physical_identity'] is True
         and value['device_action_authorized'] is False)
    run=scoped(db,VideoRun,value['run_id'],p,lock)
    frame=scoped(db,VideoFrame,value['frame_id'],p,lock)
    alert=scoped(db,VideoAlert,value['video_alert_id'],p,lock)
    event=scoped(db,Event,source.data.get('event_id'),p,lock)
    need(frame.run_id==run.id and alert.run_id==run.id and run.evidence_id==value['original_evidence_id']
         and frame.evidence_id==value['frame_evidence_id'] and source.data.get('run_id')==run.id
         and source.data.get('video_alert_id')==alert.id and alert.data.get('event_id')==event.id
         and alert.data.get('frame_id')==frame.id and event.kind=='video.presence' and event.status=='received'
         and event.data.get('run_id')==run.id and event.data.get('frame_id')==frame.id)
    need(allow_reviewed or alert.status not in {'rejected','dismissed'},'原视频线索已被拒绝，请先重新核对')
    candidate=event.data.get('candidate');need(type(candidate) is dict)
    need(all(candidate.get(k)==value.get(k)==alert.data.get(k) for k in ('track_ref','label','from_seconds','to_seconds'))
         and candidate.get('event_key')==alert.event_key and candidate.get('event_key')=='presence:'+value['track_ref'])
    need(type(frame.data) is dict and all(k in frame.data for k in FRAME_FIELDS)
         and digest({k:frame.data[k] for k in FRAME_FIELDS})==frame.data.get('receipt_hash')
         and candidate in frame.data['events'])
    track=[x for x in frame.data['tracks'] if x.get('track_ref')==value['track_ref']
           and x.get('label')==value['label'] and x.get('label_id')==value['label_id']]
    need(len(track)==1 and frame.data.get('pipeline_hash')==run.data.get('pipeline_hash')
         and run.data.get('input_sha256')==value['original_sha256'] and frame.data.get('frame_sha256')==value['frame_sha256'])
    context=frame.data.get('source_context') or {};association=source.data.get('domain_binding')
    need(type(association) is dict and context.get('domain_binding')==association
         and association.get('instance_id')==source.data.get('instance_id')
         and association.get('instance_release_id')==source.data.get('instance_release_id')
         and association.get('business_id')==source.data.get('business_id')
         and source.data.get('entity_ids')==[association.get('entity_id')])
    original,_=evidence_bytes(db,p,value['original_evidence_id']);image,_=evidence_bytes(db,p,value['frame_evidence_id'])
    need(original.sha256==value['original_sha256'] and image.sha256==value['frame_sha256'])
    base={'schema':'video-workflow-origin-v347','source_task_id':source.id,
          'instance_id':source.data['instance_id'],'release_id':source.data['instance_release_id'],
          'business_id':source.data['business_id'],'entity_id':association['entity_id'],
          'event_id':event.id,'event_hash':digest(event.data),'frame_receipt_hash':frame.data['receipt_hash'],
          'source_binding_hash':digest(association),'candidate_hash':digest(candidate),
          'perception_event':copy.deepcopy(value),'observation_id':source.data.get('observation_id'),
          'source_clock_verified':False,'physical_state_verified':False,'device_action_authorized':False}
    return {**base,'basis_hash':digest(base)}


def context(db,p,source,business):
    need(is_candidate(source,business),'此记录不是待交接的协同视频线索')
    origin=_origin(db,p,source)
    link=source.data.get(KEY)
    if link is not None:
        need(type(link) is dict and link.get('schema')==SCHEMA and link.get('basis_hash')==origin['basis_hash']
             and link.get('link_hash')==digest({k:v for k,v in link.items() if k!='link_hash'}))
        child=scoped(db,Task,link.get('task_id'),p)
        verify_task(db,p,child,lock_sources=False)
    from .video_reception_status_v349 import status as reception_status
    reception = reception_status(db,p,origin)
    return {'schema':'video-workflow-context-v347','source_task_id':source.id,'source_task_version':source.version,
        'instance_id':origin['instance_id'],'release_id':origin['release_id'],'business_id':origin['business_id'],
        'entity_id':origin['entity_id'],'basis_hash':origin['basis_hash'],'linked_task_id':link.get('task_id') if link else None,
        'source_clock_verified':False,'physical_state_verified':False,
        'data_reception_verified':reception['data_reception_verified'], 'data_reception':reception}


def task_reception(db,p,task,business):
    """Resolve the same original evidence for a candidate or its linked task."""
    from .video_reception_status_v349 import status, _result
    if KEY in task.data:
        # Source candidates have the link itself; child tasks have origin+link.
        need(type(task.data[KEY]) is dict)
        if 'origin' in task.data[KEY]:
            verify_task(db,p,task,lock_sources=False)
            return status(db,p,task.data[KEY]['origin'])
    if task.data.get('trigger')=='video.presence' and task.data.get('perception_event'):
        return status(db,p,_origin(db,p,task,allow_terminal=True))
    return _result('not_available')


def prepare(db,p,selection,identity,body,business):
    source=scoped(db,Task,selection.source_task_id,p,True)
    need(is_candidate(source,business),'原线索不再可交接，请核对已保存记录')
    need(source.status in {'workflow_pending','workflow_waiting'},'原线索已结束，请保留原记录并重新核对')
    need(source.version==selection.source_task_version,'原线索已变化，请重新核对')
    origin=_origin(db,p,source,lock=True,allow_reviewed=False)
    need(origin['basis_hash']==selection.basis_hash and origin['instance_id']==identity
         and origin['release_id']==body.release_id and origin['business_id']==body.business_id
         and body.entity_ids==[origin['entity_id']],'业务、对象、版本或原视频依据已变化，请重新核对')
    need(KEY not in source.data,'此视频线索已有办理任务，请查看已保存记录')
    return source,origin


def attach(db,p,task,source,origin,creation_hash):
    need(KEY not in source.data and task.id and origin['source_task_id']==source.id)
    link={'schema':SCHEMA,'source_task_id':source.id,'task_id':task.id,'basis_hash':origin['basis_hash'],
          'creation_hash':creation_hash,'actor_id':p.user_id}
    link['link_hash']=digest(link)
    source.data={**source.data,KEY:copy.deepcopy(link)};source.version+=1
    task.data={**task.data,'perception_event':copy.deepcopy(origin['perception_event']),
               'evidence_ids':[origin['perception_event']['frame_evidence_id'],origin['perception_event']['original_evidence_id']],
               KEY:{'origin':copy.deepcopy(origin),'link':copy.deepcopy(link)}}
    audit(db,p,'video.workflow_handoff_v347',source.id,copy.deepcopy(link));db.flush()


def verify_task(db,p,task,*,lock_sources=False,require_active=False):
    value=task.data.get(KEY)
    if value is None:return
    need(type(value) is dict and set(value)=={'origin','link'} and type(value['origin']) is dict and type(value['link']) is dict)
    link=value['link'];origin=value['origin'];source=scoped(db,Task,link.get('source_task_id'),p,lock_sources)
    need(link.get('schema')==SCHEMA and link.get('task_id')==task.id and link.get('actor_id')==task.owner_id
         and link.get('creation_hash')==task.data.get('creation_hash') and link.get('basis_hash')==origin.get('basis_hash')
         and link.get('link_hash')==digest({k:v for k,v in link.items() if k!='link_hash'})
         and source.data.get(KEY)==link and task.data.get('perception_event')==origin.get('perception_event')
         and task.data.get('instance_id')==origin.get('instance_id')
         and task.data.get('instance_release_id')==origin.get('release_id')
         and task.data.get('business_id')==origin.get('business_id')
         and task.data.get('entity_ids')==[origin.get('entity_id')])
    need(_origin(db,p,source,lock=lock_sources,allow_reviewed=not require_active)==origin,'原视频线索的保存依据已变化，原任务保留')
    from sqlalchemy import select
    from .models import Audit
    entries=list(db.scalars(select(Audit).where(Audit.workspace_id==p.workspace_id,
        Audit.object_id==source.id,Audit.action=='video.workflow_handoff_v347')))
    need(len(entries)==1 and entries[0].actor_id==link['actor_id'] and entries[0].data==link)
