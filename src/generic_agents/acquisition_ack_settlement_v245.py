"""Leased, local settlement after the original independent data ACK.

The receiver queues only a pinned original request. It never takes parent
locks while holding Event/Observation locks. The later ordinary worker uses
the original Gap/Task/Monitor/Acquisition order and sends no device request.
Specialized reception and reducing derivation holds keep their own paths.
"""
import copy
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from . import models as m
from .contracts import digest
from .services import scoped,audit

KIND='settle_received_acquisition_v245'
SCHEMA='received-acquisition-settlement-v245'
ACTION='acquisition.data_ack_settlement_queued_v245'
ERROR='原补采、原观测或独立收件依据无法核对；未结案或重新采集'


def need(value):
    if not value:raise HTTPException(409,ERROR)


def checked_input(db,p,event,observation):
    from . import instance_data_receipts as receipts
    from .mapped_acquisition_origin_v177 import guard as mapped_origin
    from .original_acquisition_reception_v127 import context,hold
    acquisition_id=observation.data.get('acquisition_id')
    if not acquisition_id:return None
    need(observation.workspace_id==event.workspace_id==p.workspace_id and observation.status=='processed'
        and event.kind=='observation.data' and event.idempotency_key=='data:'+observation.id
        and event.data.get('observation_id')==observation.id and event.status=='received' and event.received_at is not None)
    receipt=receipts.observation_receipt(db,p,observation).get('data_reception')
    # Native audio/geo/video recipients have their own completion semantics.
    if receipt is None or receipt.get('schema')!=receipts.SCHEMA:return None
    instance=scoped(db,m.DomainInstance,receipt['instance_id'],p)
    # Match the ordinary generated-instance reducing gate as well as V127's
    # version-bound native gate. A pause after queueing is not a new grant.
    if instance.status!='partially_active' or not instance.active_release_id:return None
    if event.data.get('derivation_receipt') is not None or hold(db,p,observation) is not None:return None
    acquisition=scoped(db,m.Acquisition,acquisition_id,p)
    original=observation.data.get('original_request',{})
    need(original.get('acquisition_id')==acquisition.id and acquisition.entity_id==observation.entity_id
        and acquisition.capability_id==observation.source_id and acquisition.data.get('observation_plan_id'))
    source=scoped(db,m.Capability,observation.source_id,p)
    asset=scoped(db,m.SensorAsset,acquisition.data['sensor_asset_id'],p)
    need(source.kind=='source' and source.content_hash==digest(source.data)==acquisition.data['source_hash']
        and acquisition.data['dispatch_execution']==source.data['execution']
        and asset.source_id==source.id and observation.entity_id in acquisition.data['dispatch_asset']['coverage_entity_ids']
        and set(acquisition.data['selected_fields']).issubset(acquisition.data['dispatch_asset']['fields']))
    supplier=acquisition.data.get('reconciled_receipt') or acquisition.data.get('receipt')
    need(type(supplier) is dict and supplier.get('request_key')==acquisition.idempotency_key
        and supplier.get('asset_external_id')==asset.external_id
        and type(supplier.get('receipt_id')) is str and bool(supplier['receipt_id'])
        and supplier.get('status') in {'accepted','completed'})
    if supplier['status']=='completed':
        need(supplier.get('terminal') is True and supplier.get('no_future_device_activity') is True
            and acquisition.data.get('device_action_terminal') is True)
    else:need(acquisition.data.get('device_action_terminal') is not True)
    sensor=observation.data.get('sensor_evidence',{})
    need(type(sensor) is dict and sensor.get('asset_id')==acquisition.data.get('sensor_asset_id')
        and sensor.get('asset_version')==acquisition.data.get('sensor_asset_version')
        and sensor.get('device_id')==acquisition.data.get('sensor_device_id')
        and sensor.get('device_version')==acquisition.data.get('sensor_device_version')
        and sensor.get('binding_version')==acquisition.data.get('device_binding_version'))
    mapped=mapped_origin(db,p,observation)
    native=context(db,p,observation)
    # The existing receipt verifies the original input/invocation. Original
    # managed requests add their existing V127 contract; V177 never falls back
    # when any of its exchange/source/inventory markers are damaged or lost.
    if mapped is not None:need(mapped['acquisition_id']==acquisition.id)
    return {'schema':SCHEMA,'workspace_id':p.workspace_id,'instance_id':instance.id,'acquisition_id':acquisition.id,
        'observation_id':observation.id,'event_id':event.id,'actor_id':acquisition.data['actor_id'],
        'input_hash':observation.data['input_hash'],'receipt_hash':digest(receipt),
        'family':'http_mapped_v177' if mapped is not None else 'original_managed_acquisition',
        'mapped_origin_hash':digest(mapped) if mapped is not None else None,
        'original_acquisition_hash':digest(native.proof) if native is not None else None}


def enqueue(db,p,event,observation):
    # Flush the actual ACK and Memory in this same transaction before the cold
    # reader queries them. A crash rolls back receipt and queue together.
    db.flush()
    pin=checked_input(db,p,event,observation)
    if pin is None:return None
    acquisition=scoped(db,m.Acquisition,pin['acquisition_id'],p)
    if acquisition.status!='awaiting_observation':return None
    key='acquisition-data-ack:'+acquisition.id+':'+observation.id
    identity=m.uid();value={'actor_id':pin['actor_id'],'settlement':pin,'settlement_hash':digest(pin)}
    created=db.scalar(insert(m.Job).values(id=identity,workspace_id=p.workspace_id,kind=KIND,
        idempotency_key=key,data=value).on_conflict_do_nothing(index_elements=['workspace_id','idempotency_key']).returning(m.Job.id))
    job=db.scalar(select(m.Job).where(m.Job.workspace_id==p.workspace_id,m.Job.idempotency_key==key))
    need(job is not None and job.kind==KIND and job.data.get('settlement')==pin and job.data.get('settlement_hash')==digest(pin))
    record={'job_id':job.id,'settlement':pin,'settlement_hash':digest(pin)}
    if created:
        from .security import Principal
        audit(db,Principal(pin['actor_id'],p.workspace_id,'viewer','original-data-ack'),ACTION,job.id,record)
        db.flush()
    events=list(db.scalars(select(m.Audit).where(m.Audit.workspace_id==p.workspace_id,m.Audit.action==ACTION,m.Audit.object_id==job.id).limit(2)))
    need(len(events)==1 and events[0].actor_id==pin['actor_id'] and events[0].data==record)
    return job


def process(db,p,job):
    from .leases import checkpoint
    from .relations import lock_current_actor
    from .observation_requests import lock_acquisition_origin
    from .perception import receive_acquisition_observation
    pin=copy.deepcopy(job.data.get('settlement'))
    need(type(pin) is dict and pin.get('schema')==SCHEMA and job.kind==KIND and job.status=='running'
        and job.workspace_id==p.workspace_id==pin.get('workspace_id') and pin.get('actor_id')==p.user_id==job.data.get('actor_id')
        and job.data.get('settlement_hash')==digest(pin)
        and job.idempotency_key=='acquisition-data-ack:'+pin['acquisition_id']+':'+pin['observation_id'])
    owner=db.info.get('worker_lease',{})
    need(owner.get('job_id')==job.id and owner.get('lease_token')==job.lease_token)
    checkpoint(db);lock_current_actor(db,p)
    events=list(db.scalars(select(m.Audit).where(m.Audit.workspace_id==p.workspace_id,m.Audit.action==ACTION,m.Audit.object_id==job.id).limit(2)))
    need(len(events)==1 and events[0].actor_id==p.user_id and events[0].data=={
        'job_id':job.id,'settlement':pin,'settlement_hash':digest(pin)})
    db.scalar(select(m.DomainInstance).where(m.DomainInstance.workspace_id==p.workspace_id,
        m.DomainInstance.id==pin['instance_id']).with_for_update(read=True).execution_options(populate_existing=True))
    acquisition=scoped(db,m.Acquisition,pin['acquisition_id'],p)
    lock_acquisition_origin(db,p,acquisition)
    scoped(db,m.MonitorPolicy,acquisition.data['policy_id'],p,True)
    acquisition=scoped(db,m.Acquisition,acquisition.id,p,True)
    observation=scoped(db,m.Observation,pin['observation_id'],p)
    db.refresh(observation)
    event=scoped(db,m.Event,pin['event_id'],p)
    current=checked_input(db,p,event,observation)
    need(current==pin)
    if acquisition.status=='awaiting_observation':
        receive_acquisition_observation(db,p,observation,settlement_only=True)
    elif acquisition.status=='succeeded':
        need(acquisition.data.get('closing_observation_id')==observation.id)
    # Cancelled/rejected originals remain ended. An ACK is not a new grant.
    plan=scoped(db,m.ObservationPlan,acquisition.data['observation_plan_id'],p)
    result={'schema':SCHEMA,'acquisition_id':acquisition.id,'observation_id':observation.id,
        'event_id':event.id,'status':acquisition.status,'plan_status':plan.status,
        'cloud_calls':0,'device_actions':0,'new_acquisitions':0}
    job.data={**job.data,'settlement_result':result,'settlement_result_hash':digest(result)}
    audit(db,p,'acquisition.data_ack_settled_v245',acquisition.id,{'job_id':job.id,**result})
