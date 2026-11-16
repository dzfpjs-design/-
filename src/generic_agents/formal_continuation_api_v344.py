"""Read-only inspection of a possible formal continuation."""
from fastapi import APIRouter
from .setup_api import P,DB
from .services import scoped
from .contracts import digest
from .models import Task
from .authored_goal_binding_parse_scope_v300 import authored_goal_binding_parse_scope
from .formal_continuation_context_v344 import context as read_context

router=APIRouter()
@router.get('/tasks/{identity}/business-advice/preparations/{advice_id}/formal-continuation/context')
@authored_goal_binding_parse_scope()
def context(identity:str,advice_id:str,p:P,db:DB):
    task=scoped(db,Task,identity,p)
    value=read_context(db,p,task,advice_id)
    context_hash=digest(value)
    if value['available']:
        from .formal_continuation_store_v345 import listing
        plans=listing(db,p,identity,advice_id)['plans']
        if any(row['phase'] in {'draft','in_progress','awaiting_acceptance'} for row in plans):
            value={**value,'reason':'正式续段已保存，请在办理区继续；原业务目标尚未验收。'}
        elif plans:
            value={**value,'reason':'已有已撤回续段记录；可核对原资料后重新办理。'}
    return {**value,'task_version':task.version,'context_hash':context_hash}
