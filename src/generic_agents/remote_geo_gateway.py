"""Read-only FTP/SMB GeoTIFF import into the existing local GeoScene pipeline.

A directory listing, import and cancellation each retain their own durable request
identity. The TIFF never enters SetupMaterial or document parsing.
"""
import asyncio,os,tempfile
from pathlib import Path
from typing import Literal
from uuid import UUID
from fastapi import APIRouter,HTTPException,UploadFile
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from .contracts import Contract,digest
from .setup_api import P,DB
from .models import Job,GeoScene,User,Membership,AuthSession,now
from .geo_storage import upload,path_for
from .geo_api import queue
from .geo_codec import RECIPE_HASH,RECIPE
from .config import settings
from .services import audit
from sqlalchemy import select

class Cancel(Contract):
    kind:Literal['geo_listing','geo_import']
    submission:dict


def make_router(protocol,transport,base):
    router=APIRouter(prefix='/api/'+protocol+'/geo')
    Error=transport.__dict__[protocol.upper()+'Error']
    
    @router.post('/connections/{identity}/listings')
    def listing(identity:UUID,body:base.Listing,p:P,db:DB):
        identity=str(identity);base._admin(p);submission={**body.model_dump(mode='json'),'connection_id':identity};base._connection(db,p,identity,body.version)
        row,perform=base._start(db,p,'geo_listing',submission)
        if not perform:return base._receipt(row)
        row=base._resume(db,p,'geo_listing',submission)
        if row.data['state']!='pending':return base._receipt(row)
        try:
            base._current_admin(db,p,lock=False);connection,grant=base._connection(db,p,identity,body.version)
            facts=[x for x in transport.list_files(grant,max_file_bytes=RECIPE['max_upload_bytes']) if Path(x['name']).suffix.lower() in {'.tif','.tiff'}]
            by_id={digest(x):x for x in facts}
            if len(by_id)!=len(facts):raise Error('目录存在无法区分的重复文件')
            base._current_admin(db,p);base._connection(db,p,identity,body.version)
            files=[{'id':k,'name':x['name'],'size':x['size'],'modified':x['modified']} for k,x in by_id.items()]
            row.data={**row.data,'state':'succeeded','facts':by_id,'result':{'listing_id':row.id,'files':files}}
            audit(db,p,protocol+'.geo.directory.read',connection.id,{'count':len(files)});db.commit();return base._receipt(row)
        except HTTPException as exc:
            if exc.status_code==403:db.rollback();raise
            return base._fail(db,p,row,exc)
        except Error as exc:return base._fail(db,p,row,exc)

    @router.get('/connections/{identity}/listings')
    def listing_history(identity:UUID,p:P,db:DB):return base._history(str(identity),p,db,'geo_listing','listings')

    @router.get('/connections/{identity}/listings/{listing_id}')
    def exact_listing(identity:UUID,listing_id:UUID,p:P,db:DB):
        identity=str(identity);base._admin(p);connection=base._get(db,p,identity,base.CONNECTION);row=base._get(db,p,listing_id,base.REQUEST)
        submission=row.data.get('submission');result=row.data.get('result')
        if (row.data.get('state')!='succeeded' or row.data.get('kind')!='geo_listing' or not isinstance(submission,dict)
            or submission.get('connection_id')!=identity or not isinstance(result,dict) or result.get('listing_id')!=row.id):
            raise HTTPException(404,'本工作区未找到影像目录快照')
        try:
            if protocol in {'ftp','smb'}:transport.find_grant(p.workspace_id,connection.data['grant_id'],db=db)
            else:transport.find_grant(p.workspace_id,connection.data['grant_id'])
            available=True
        except (Error,KeyError):available=False
        return {'listing':base._receipt(row),'source_available':available}

    @router.post('/connections/{identity}/imports')
    async def import_file(identity:UUID,body:base.Import,p:P,db:DB):
        identity=str(identity);base._admin(p);submission={**body.model_dump(mode='json'),'connection_id':identity};base._connection(db,p,identity,body.version)
        row,perform=base._start(db,p,'geo_import',submission)
        if not perform:return base._receipt(row)
        row=base._resume(db,p,'geo_import',submission)
        if row.data['state']!='pending':return base._receipt(row)
        try:
            base._current_admin(db,p,lock=False);connection,grant=base._connection(db,p,identity,body.version);snapshot=base._get(db,p,body.listing_id,base.REQUEST)
            if snapshot.data.get('state')!='succeeded' or snapshot.data.get('kind')!='geo_listing' or snapshot.data.get('submission',{}).get('connection_id')!=identity:raise Error('原影像目录快照不可用，请重新读取')
            facts=snapshot.data.get('facts',{}).get(body.file_id)
            if not isinstance(facts,dict) or digest(facts)!=body.file_id or Path(facts['name']).suffix.lower() not in {'.tif','.tiff'}:raise Error('原影像快照无法核对，请重新读取')
            directory=settings().storage_dir/p.workspace_id;directory.mkdir(parents=True,exist_ok=True,mode=0o700)
            # The transport and geo_storage both stream; never materialize a raster in API RAM.
            with tempfile.TemporaryFile(dir=directory) as staging:
                size,sha=await run_in_threadpool(transport.stream_file,grant,facts,staging,max_file_bytes=RECIPE['max_upload_bytes'])
                if size!=facts['size']:raise Error('远端影像大小无法核对')
                def sync_staging():
                    staging.flush();os.fsync(staging.fileno());staging.seek(0)
                await run_in_threadpool(sync_staging)
                base._current_admin(db,p);base._connection(db,p,identity,body.version)
                file=UploadFile(file=staging,filename=facts['name'])
                # The existing upload performs filesystem writes, fsync and
                # final hashing. Run it off the API event loop. The session is
                # handed to this one thread sequentially and never concurrently.
                ev=await run_in_threadpool(lambda:asyncio.run(upload(db,p,file)))
            if ev.sha256!=sha or ev.size_bytes!=facts['size']:raise Error('影像原件摘要或大小不一致')
            await run_in_threadpool(path_for,db,p,ev.id)
            base._current_admin(db,p);base._connection(db,p,identity,body.version)
            source={'protocol':protocol,'connection_id':connection.id,'listing_id':snapshot.id,'file_id':body.file_id,'name':facts['name'],'size':facts['size'],'modified':facts['modified'],'sha256':sha}
            scene=GeoScene(workspace_id=p.workspace_id,evidence_id=ev.id,data={'name':ev.data['name'],'input_sha256':sha,'size_bytes':ev.size_bytes,'upload_request_id':protocol+'geo-'+submission['request_id'],'runtime_hash':RECIPE_HASH,'generation':1,'configuration':None,'metadata':None,'preview':None,'error':None,'history':[],'source':source})
            db.add(scene);db.flush();queue(db,p,scene)
            row.data={**row.data,'state':'succeeded','result':{'scene_id':scene.id,'evidence_id':ev.id,'status':scene.status,'source':source},'source':{**source,'grant_id':grant['id'],'file':facts}}
            audit(db,p,protocol+'.geo.imported',scene.id,{'request_id':submission['request_id'],'sha256':sha});db.commit();return base._receipt(row)
        except HTTPException as exc:
            if exc.status_code==403:db.rollback();raise
            return base._fail(db,p,row,exc)
        except (Error,KeyError,OSError) as exc:return base._fail(db,p,row,exc)

    @router.get('/connections/{identity}/imports')
    def import_history(identity:UUID,p:P,db:DB):return base._history(str(identity),p,db,'geo_import','imports')

    def current_member(db,p):
        query=(select(User,Membership,AuthSession).join(Membership,Membership.user_id==User.id)
               .join(AuthSession,AuthSession.user_id==User.id)
               .where(User.id==p.user_id,User.active.is_(True),Membership.workspace_id==p.workspace_id,
                      AuthSession.id==p.session_id,AuthSession.revoked.is_(False),AuthSession.expires_at>now())
               .with_for_update(read=True,of=[User,Membership,AuthSession]).execution_options(populate_existing=True))
        if db.execute(query).first() is None:raise HTTPException(403,'当前账号或工作区权限已失效')

    @router.get('/requests/{request_id}')
    def request_status(request_id:UUID,p:P,db:DB):
        row=base._find_request(db,p,str(request_id))
        if not row or row.kind not in (base.REQUEST,base.STOP) or row.data.get('actor_id')!=p.user_id or row.data.get('kind') not in ('geo_listing','geo_import'):raise HTTPException(404,'未找到当前账号的影像原请求')
        return base._receipt(row)

    @router.get('/requests/{request_id}/result')
    def verify_import(request_id:UUID,p:P,db:DB):
        row=base._find_request(db,p,str(request_id))
        if not row or row.kind!=base.REQUEST or row.data.get('actor_id')!=p.user_id or row.data.get('kind')!='geo_import' or row.data.get('state')!='succeeded':raise HTTPException(404,'本账号未找到已保存的影像导入')
        result=row.data.get('result',{});source=result.get('source',{})
        scene=db.get(GeoScene,result.get('scene_id'))
        if not scene or scene.workspace_id!=p.workspace_id or scene.evidence_id!=result.get('evidence_id') or scene.data.get('source')!=source or scene.data.get('input_sha256')!=source.get('sha256') or scene.data.get('size_bytes')!=source.get('size'):raise HTTPException(409,'影像保存记录与来源不一致')
        submission=row.data.get('submission',{});listing=base._get(db,p,submission.get('listing_id'),base.REQUEST)
        facts=listing.data.get('facts',{}).get(submission.get('file_id'))
        if (listing.data.get('kind')!='geo_listing' or listing.data.get('state')!='succeeded'
            or listing.data.get('submission',{}).get('connection_id')!=submission.get('connection_id')
            or not isinstance(facts,dict) or digest(facts)!=submission.get('file_id')
            or source.get('connection_id')!=submission.get('connection_id') or source.get('listing_id')!=submission.get('listing_id')
            or source.get('file_id')!=submission.get('file_id') or any(source.get(k)!=facts.get(k) for k in ('name','size','modified'))):raise HTTPException(409,'影像来源与原目录快照不一致')
        evidence,_=path_for(db,p,scene.evidence_id)
        if evidence.sha256!=source.get('sha256') or evidence.size_bytes!=source.get('size'):raise HTTPException(409,'影像原件与来源不一致')
        return {'scene_id':scene.id,'evidence_id':evidence.id,'status':scene.status,'source':source,'metadata':scene.data.get('metadata'),'error':scene.data.get('error')}

    @router.post('/requests/{request_id}/cancel')
    def cancel(request_id:UUID,body:Cancel,p:P,db:DB):
        request=str(request_id)
        try:
            raw=dict(body.submission);identity=str(UUID(raw.pop('connection_id')))
            submission=(base.Listing if body.kind=='geo_listing' else base.Import).model_validate(raw).model_dump(mode='json')
            submission={**submission,'connection_id':identity}
        except (ValueError,TypeError,KeyError,ValidationError):raise HTTPException(422,'原影像请求内容格式无法核对') from None
        if submission.get('request_id')!=request:raise HTTPException(422,'原请求编号与提交内容不一致')
        base._lock(db,p.workspace_id,base._key(request));current_member(db,p);row=base._find_request(db,p,request)
        if row:
            base._validate_record(row,p,body.kind,submission)
            if row.data['state']=='succeeded':return base._receipt(row)
            row.kind=base.STOP;row.data={**row.data,'state':'cancelled','error':None};db.commit();return base._receipt(row)
        row=Job(workspace_id=p.workspace_id,kind=base.STOP,status='internal',idempotency_key=base._key(request),data={'request_id':request,'kind':body.kind,'submission':submission,'submission_hash':digest(submission),'actor_id':p.user_id,'state':'cancelled','result':None})
        db.add(row);audit(db,p,protocol+'.geo.request.cancelled',request,{'kind':body.kind});db.commit();return base._receipt(row)

    return router
