"""Guard new local retry attempts before delegating to the frozen v1 handler.

The workspace router includes this adapter before the legacy route. Both paths
use the same request contract and transaction; saved nonce receipts remain
readable after a release changes, while new work requires its original scope.
"""
from fastapi import APIRouter,HTTPException
from pydantic import Field
from sqlalchemy import select
from .contracts import VersionAction,digest
from .models import Job,Observation
from .services import require,scoped
from .setup_api import P,DB

router=APIRouter()


class ScopedObservationRetry(VersionAction):
    request_id:str|None=Field(default=None,pattern=r'^[a-zA-Z0-9_-]{12,100}$')


@router.post('/api/observations/{object_id}/retry')
def retry_observation_scoped(object_id:str,body:ScopedObservationRetry,p:P,db:DB):
    require(p,'admin','operator')
    # Preserve the established Job -> Observation lock order. The legacy
    # handler reuses these locks and commits only after the scope guard.
    job=db.scalar(select(Job).where(Job.workspace_id==p.workspace_id,
        Job.idempotency_key=='extract:'+object_id).with_for_update())
    row=scoped(db,Observation,object_id,p,True)
    retries=row.data.get('retry_requests',[])
    if retries and digest(retries)!=row.data.get('retry_requests_hash'):
        raise HTTPException(409,'原重试回执不一致，请保留记录核对')
    saved=next((value for value in retries if value['request_id']==body.request_id),None) if body.request_id else None
    if not saved and (row.data.get('candidate_only') or (job and job.kind=='process_local_observation')):
        from .local_observations import verify
        # Keep the actual instance locked until the delegated commit, closing
        # the race between a release activation and retry queueing.
        verify(db,p,row,row.data['extractor_id'],execution=True,lock=True)
    from .api import ObservationRetry,retry_observation
    return retry_observation(object_id,ObservationRetry.model_validate(body.model_dump()),p,db)
