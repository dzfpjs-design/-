// current_valid describes live reuse, not whether a pending or historical result failed.
export function nativeQueryStatus(goal){
 const query=goal.data_query||goal.check?.evidence?.data_query,current=goal.check?.evidence?.data_query;
 const historical=!!(query?.historical||goal.acceptance||goal.cancelled||goal.task_status==='workflow_cancelled');
 const validationError=query?.scope_error||(!historical&&(goal.scope_error||goal.check?.current_gate_error));
 const savedStatus=query?.status||current?.status||'not_requested';
 const invalid=!!(validationError||(!historical&&savedStatus==='sufficient'&&query?.current_valid===false));
 let status=invalid?'blocked_evidence':savedStatus;
 if(!invalid&&historical){
  if(goal.acceptance)status='accepted_history';
  else if(goal.cancelled||goal.task_status==='workflow_cancelled')status=savedStatus==='cancelled'?'cancelled':'stopped_history';
  else status='historical';
 }else if(!invalid&&['queued','running'].includes(savedStatus)&&query?.worker){
  const worker=query.worker.state;
  if(['waiting_recovery','failed','cancelled'].includes(worker))status=worker;
  else if(['unverified','stale','succeeded'].includes(worker))status='worker_unverified';
 }
 else if(!invalid&&current?.id===query?.id&&current?.status&&current.status!=='sufficient'&&!['queued','running'].includes(savedStatus))status=current.status;
 const reason=validationError||['waiting_recovery','worker_unverified','failed','cancelled'].includes(status)&&query?.worker?.reason||query?.reason||current?.reason||query?.result?.tool_blocker;
 return {query,current,historical,invalid,status,reason};
}
