"""Workspace-bound local quality worker; never runs global schedulers.

It claims V352 quality requests, their provably issued derived-scene previews,
and explicitly submitted local comparisons using the same received report.
Business tasks, other scenes, devices and cloud calls are outside its scope.
"""
import signal,threading,time
from uuid import UUID
from sqlalchemy import select,func,or_,and_
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from .db import engine
from .models import Workspace,Job,GeoScene,GeoPair
from .security import Principal
from . import geo_quality_v351 as quality
from .geo_quality_reception_v352 import deliver_pending


def scope(value):
    try:
        if not isinstance(value,str) or str(UUID(value))!=value:raise ValueError()
    except (ValueError,TypeError,AttributeError):raise ValueError('必须指定标准小写工作区编号') from None
    return value


def available():
    return and_(Job.available_at<=func.clock_timestamp(),or_(Job.status=='queued',
        and_(Job.status=='running',or_(Job.lease_until.is_(None),Job.lease_until<func.clock_timestamp()))))


def tick(workspace_id,stop=None):
    from .worker import process_once,actor
    from fastapi import HTTPException
    workspace_id=scope(workspace_id);stop=stop or threading.Event()
    with Session(engine()) as db:
        if db.get(Workspace,workspace_id) is None:raise ValueError('指定工作区不存在')
        parents=list(db.scalars(select(Job.id).where(Job.workspace_id==workspace_id,
            Job.kind==quality.KIND_V352,available()).order_by(Job.available_at,Job.id).limit(20)))
    count=0
    for identity in parents:
        if stop.is_set():return count
        count+=int(process_once(job_id=identity,kinds=[quality.KIND_V352],deliver=False,
            workspace_id=workspace_id,_stop_event=stop))
    if stop.is_set():return count
    deliver_pending(workspace_id=workspace_id)
    with Session(engine()) as db:
        rows=db.execute(select(Job.id,GeoScene.id).join(GeoScene,GeoScene.data['job_id'].as_string()==Job.id)
            .where(Job.workspace_id==workspace_id,GeoScene.workspace_id==workspace_id,
                Job.kind=='parse_geo_scene',available(),
                GeoScene.data['quality_origin_v351']['quality_job_id'].as_string().is_not(None))
            .order_by(Job.available_at,Job.id).limit(100)).all()
    for identity,scene_id in rows:
        if stop.is_set():return count
        try:
            with Session(engine()) as db:
                job=db.get(Job,identity);scene=db.get(GeoScene,scene_id)
                if not job or not scene or job.workspace_id!=workspace_id or scene.workspace_id!=workspace_id:continue
                marker=scene.data.get('quality_origin_v351') or {};parent=db.get(Job,marker.get('quality_job_id'))
                if (not parent or parent.workspace_id!=workspace_id or parent.kind!=quality.KIND_V352
                    or parent.status!='succeeded' or job.data.get('scene_id')!=scene.id
                    or job.data.get('generation')!=scene.data.get('generation') or scene.data.get('job_id')!=job.id
                    or job.data.get('actor_id')!=parent.data.get('actor_id')):continue
                p=actor(db,workspace_id,job.data['actor_id']);checked=quality.checked_scene(db,p,scene)
                if not checked or not checked.get('data_reception_hash'):continue
            count+=int(process_once(job_id=identity,kinds=['parse_geo_scene'],deliver=False,
                workspace_id=workspace_id,_stop_event=stop))
        except (HTTPException,KeyError,ValueError,TypeError,AttributeError,OSError):
            # Preserve suspicious jobs and evidence; never broaden the claim.
            continue
    with Session(engine()) as db:
        rows=db.execute(select(Job.id,GeoPair.id).join(GeoPair,GeoPair.data['job_id'].as_string()==Job.id)
            .where(Job.workspace_id==workspace_id,GeoPair.workspace_id==workspace_id,
                Job.kind=='compare_geo_pair',available(),
                GeoPair.data['sources'][0]['quality_origin_v351']['data_reception_hash'].as_string().is_not(None))
            .order_by(Job.available_at,Job.id).limit(100)).all()
    for identity,pair_id in rows:
        if stop.is_set():return count
        try:
            with Session(engine()) as db:
                from .geo_pairs import inputs
                from .contracts import digest
                job=db.get(Job,identity);pair=db.get(GeoPair,pair_id)
                if (not job or not pair or job.workspace_id!=workspace_id or pair.workspace_id!=workspace_id
                    or job.data.get('pair_id')!=pair.id or pair.data.get('job_id')!=job.id
                    or job.data.get('generation')!=pair.data.get('generation')
                    or pair.data.get('workflow_task_id') or pair.data.get('workflow_graph_scope')
                    or pair.data.get('request_kind') not in {None,'learned_binary_change'}
                    or digest(pair.data['request'])!=pair.data['request_hash']):continue
                sources=pair.data['sources'];quality.validate_pair(*sources)
                if len(sources)!=2 or not all(x.get('quality_origin_v351',{}).get('data_reception_hash') for x in sources):continue
                parent=db.get(Job,sources[0]['quality_origin_v351']['quality_job_id'])
                if not parent or parent.workspace_id!=workspace_id or parent.kind!=quality.KIND_V352:continue
                p=actor(db,workspace_id,job.data['actor_id']);inputs(db,p,pair)
            count+=int(process_once(job_id=identity,kinds=['compare_geo_pair'],deliver=False,
                workspace_id=workspace_id,_stop_event=stop))
        except (HTTPException,KeyError,ValueError,TypeError,AttributeError,OSError):
            continue
    return count


def run(workspace_id):
    workspace_id=scope(workspace_id);stop=threading.Event();previous={}
    def halt(*_):stop.set()
    for sig in (signal.SIGINT,signal.SIGTERM):previous[sig]=signal.getsignal(sig);signal.signal(sig,halt)
    print('质量处理器已启动：仅指定工作区的质量任务、派生影像和对应本地对比',flush=True)
    try:
        while not stop.is_set():
            try:worked=tick(workspace_id,stop)
            except DBAPIError:
                print('数据库暂不可用，原任务保留并等待恢复',flush=True);stop.wait(2);continue
            if not worked:stop.wait(1)
        return 0
    finally:
        for sig,handler in previous.items():signal.signal(sig,handler)
        print('质量处理器已停止领取，持久任务保留',flush=True)


def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--workspace-id',required=True);args=parser.parse_args()
    return run(args.workspace_id)

if __name__=='__main__':raise SystemExit(main())
