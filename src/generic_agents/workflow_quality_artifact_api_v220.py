"""Normal Task-reader access to the exact successful V220 Artifact pair.

No generic Evidence download, current-inventory gate, lease, repair or writes.
The response owns already verified bounded bytes; it never reopens a path via
FileResponse after validation.
"""
import hashlib
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter,Depends,HTTPException,Request,Response

from . import workflow_quality_archive_v220 as archive
from .models import Evidence,Invocation,Job
from .security import Principal
from .services import scoped
from .signal_dual_read_session import read_session
from .snapshot_authentication_v141 import snapshot_authenticated

P=Annotated[Principal,Depends(snapshot_authenticated)]
DB=Annotated[object,Depends(read_session)]
router=APIRouter(prefix='/api/workspace/tasks')
FILES={'markdown':('text/markdown','task-technical-summary.md','任务内部技术汇总.md'),
    'machine_json':('application/json','task-technical-summary.json','任务内部技术汇总.json')}
ERROR='原内部技术成果或完整保存证明无法核对，未重新执行、修复或替换成果'


def download_mode(request):
    values=request.query_params
    if set(values)-{'download'} or len(values.getlist('download'))>1:
        raise HTTPException(422,'技术成果仅接受一个download=false或download=true参数')
    mode=values.get('download','false')
    if mode not in {'false','true'}:raise HTTPException(422,'download只能为false或true')
    return mode=='true'


def headers(kind,descriptor,raw,download):
    if type(download) is not bool or kind not in FILES:raise HTTPException(422,'请选择有效技术成果读取方式')
    media,ascii_name,chinese=FILES[kind]
    hashed=hashlib.sha256(raw).hexdigest()
    if (descriptor.get('media_type')!=media or descriptor.get('size_bytes')!=len(raw)
            or descriptor.get('sha256')!=hashed or not 0<len(raw)<=archive.MAX_ARTIFACT_BYTES):
        raise HTTPException(409,ERROR)
    disposition='attachment' if download else 'inline'
    return {'Content-Type':media+('; charset=utf-8' if kind=='markdown' else ''),
        'Content-Length':str(len(raw)),'X-Content-SHA256':hashed,'X-Technical-Artifact-Kind':kind,
        'Content-Disposition':disposition+'; filename="'+ascii_name+'"; filename*=UTF-8\'\''+quote(chinese,safe=''),
        'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'}


def read_artifact(db,p,task_id,evidence_id,*,download=False):
    from .workspace_api import saved_execution
    from .report_text_parse_scope import report_text_parse_scope
    from .report_full_source_read_scope import task_full_source_read_scope
    from .workflow_task_context_v196 import ProducerPending
    try:
        with db.no_autoflush,report_text_parse_scope(),task_full_source_read_scope(db,p,task_id):
            # The original Task/detail reader, never the current execution or
            # device-state guard. This remains available after instance pause.
            task,_,_=saved_execution(db,p,task_id)
            evidence=scoped(db,Evidence,evidence_id,p)
            marker=evidence.data.get(archive.KEY)
            if (evidence.data.get('purpose')!=archive.KEY or type(marker) is not dict
                    or marker.get('schema')!='workflow-quality-archive-evidence-v220'):
                raise HTTPException(404,'此证据不是该任务已保存的内部技术成果')
            call=scoped(db,Invocation,marker.get('invocation_id'),p)
            job=scoped(db,Job,call.data.get('job_id'),p)
            if job.data.get('task_id')!=task.id or marker.get('task_id')!=task.id:
                raise HTTPException(404,'此证据不是该任务已保存的内部技术成果')
            output=archive.checked_saved_result(db,p,call)
            selected=[(kind,value) for kind,value in output['artifacts'].items() if value['evidence_id']==evidence.id]
            if len(selected)!=1:raise HTTPException(404,'此证据不是该任务已保存的内部技术成果')
            kind,descriptor=selected[0]
            raw=archive.quality.read_original(archive.settings().storage_dir,p.workspace_id,evidence,
                remaining=archive.MAX_ARTIFACT_BYTES)
            return Response(raw,headers=headers(kind,descriptor,raw,download))
    except HTTPException as error:
        if error.status_code in {401,403,404,409}:raise
        raise HTTPException(409,ERROR) from None
    except (ValueError,KeyError,TypeError,AttributeError,OSError,ProducerPending):
        raise HTTPException(409,ERROR) from None


@router.get('/{task_id}/technical-artifacts/{evidence_id}')
def technical_artifact(task_id:UUID,evidence_id:UUID,request:Request,p:P,db:DB):
    return read_artifact(db,p,str(task_id),str(evidence_id),download=download_mode(request))
