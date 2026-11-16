// The server owns wait decisions. This module validates their display projection;
// it never infers a timeout from the browser clock or grants retry authority.
export const GRAPH_MESSAGE_RETRY_REASON='恢复原消息检查；保留原等待窗口、期限、输入与处理回执。';

const terminal=new Set(['cancelled','succeeded','stopped','failed']);
const graphStates=new Set(['running','waiting','blocked',...terminal]);
const jobStates=new Set(['queued','running','succeeded','failed','blocked','cancelled']);
const statusKeys=['arrival_basis','last_checked_at','error','can_retry','windows','candidate_only','physical_state_verified'];
const windowKeys=['activation_id','status','opened_at','deadline','closed_at','event_id'];
const uuidPattern=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const identifierPattern=/^[-A-Za-z0-9_][-A-Za-z0-9_.:]{0,199}$/;
const record=value=>value!==null&&typeof value==='object'&&!Array.isArray(value)
 &&[Object.prototype,null].includes(Object.getPrototypeOf(value));
const own=(value,key)=>Object.hasOwn(value,key);
const exact=(value,keys)=>record(value)&&Object.keys(value).length===keys.length&&keys.every(key=>own(value,key));
const integer=(value,minimum=0)=>Number.isSafeInteger(value)&&value>=minimum;
const epoch=value=>typeof value==='number'&&Number.isFinite(value)&&value>=0&&value<=8640000000000;
const text=(value,maximum=1000)=>typeof value==='string'&&value.length<=maximum&&!value.includes('\0')
 &&!/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value);
function fail(code){const error=Error('消息等待状态暂无法核对，原草稿和任务记录保留。');error.code=code;throw error;}
function need(ok,code){if(!ok)fail(code);}
function jsonSafe(value){
 let left=100000;
 function visit(item,depth=0){
  need(--left>=0&&depth<=32,'projection_limit');
  if(item===null||typeof item==='boolean')return;
  if(typeof item==='number'){need(Number.isFinite(item),'projection_number');return;}
  if(typeof item==='string'){need(text(item,16000),'projection_text');return;}
  if(Array.isArray(item)){for(const child of item)visit(child,depth+1);return;}
  need(record(item),'projection_json');
  for(const [key,child] of Object.entries(item)){need(text(key,200),'projection_text');visit(child,depth+1);}
 }
 visit(value);
}

export function inspectGraphMessageStatus(task){
 need(record(task),'task_shape');
 if(!own(task,'graph_message_status'))return null;
 const status=task.graph_message_status;
 need(exact(status,statusKeys),'status_shape');jsonSafe(status);
 need(status.arrival_basis==='durable_admission'&&status.candidate_only===true
  &&status.physical_state_verified===false,'evidence_boundary');
 need(epoch(status.last_checked_at)&&typeof status.can_retry==='boolean','status_fields');
 const state=task.graph_runtime;
 need(record(state)&&graphStates.has(state.status)&&record(state.activations)
  &&epoch(state.clock)&&state.clock>=status.last_checked_at,'runtime_shape');
 need(Array.isArray(status.windows)&&status.windows.length<=4096,'window_inventory');
 const seen=new Set();
 for(const window of status.windows){
  need(exact(window,windowKeys),'window_shape');
  need(typeof window.activation_id==='string'&&identifierPattern.test(window.activation_id)
   &&!seen.has(window.activation_id),'window_identity');seen.add(window.activation_id);
  const activation=state.activations[window.activation_id];
  need(record(activation)&&activation.id===window.activation_id&&epoch(activation.created_at)
   &&activation.created_at<=window.opened_at,'window_activation');
  need(epoch(window.opened_at)&&epoch(window.deadline)&&window.opened_at<window.deadline
   &&window.opened_at<=status.last_checked_at&&activation.wait_started_at===window.opened_at
   &&activation.deadline===window.deadline,'window_time');
  if(window.status==='open'){
   need(['waiting_event','waiting_guard'].includes(activation.status)
    &&window.closed_at===null&&window.event_id===null,'window_open');
  }else{
   need(['consumed','timed_out','cancelled'].includes(window.status)&&epoch(window.closed_at)
    &&window.opened_at<=window.closed_at&&window.closed_at<=status.last_checked_at,'window_closed');
   if(window.status==='consumed')need(activation.status==='completed'&&activation.completed_at===window.closed_at
    &&typeof window.event_id==='string'&&uuidPattern.test(window.event_id),'window_consumed');
   else if(window.status==='timed_out')need(activation.status==='timed_out'&&activation.completed_at===window.closed_at
    &&window.closed_at>=window.deadline&&window.event_id===null,'window_timeout');
   else need(activation.status==='cancelled'&&window.event_id===null,'window_cancelled');
  }
 }
 if(status.error!==null){
  const error=status.error;
  need(record(error)&&['job_id','reason'].every(key=>own(error,key))
   &&Object.keys(error).every(key=>['job_id','reason','job_version','status','attempts'].includes(key)), 'error_shape');
  need(typeof error.job_id==='string'&&uuidPattern.test(error.job_id)&&text(error.reason,1000),'error_fields');
  const extras=['job_version','status','attempts'].filter(key=>own(error,key));
  need(extras.length===0||extras.length===3,'error_job_fields');
  if(extras.length)need(integer(error.job_version,1)&&jobStates.has(error.status)&&integer(error.attempts),'error_job_fields');
 }
 if(status.can_retry){
  const error=status.error;
  need(error!==null&&integer(error.job_version,1)&&['failed','blocked'].includes(error.status)
   &&!terminal.has(state.status),'retry_authority');
  need(record(task.graph_wait_poll)&&task.graph_wait_poll.job_id===error.job_id,'retry_original_job');
 }
 return JSON.parse(JSON.stringify(status));
}

export function formatGraphMessageTime(value){
 need(epoch(value),'display_time');
 const parts=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',
  hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).formatToParts(new Date(value*1000));
 const fields=Object.fromEntries(parts.map(part=>[part.type,part.value]));
 return `${fields.year}年${fields.month}月${fields.day}日 ${fields.hour}:${fields.minute}:${fields.second}（北京时间）`;
}

function businessLabel(task,identity,index){
 const activation=task.graph_runtime.activations[identity];
 const labels=record(task.graph_node_labels)?task.graph_node_labels:{};
 const info=labels[identity]||labels[activation.node_id]||{};
 const steps=Array.isArray(task.business?.flow?.steps)?task.business.flow.steps:[];
 const source=steps.find(step=>record(step)&&step.id===info.source_step_id);
 const choices=[source?.name,info.name];
 for(const choice of choices)if(text(choice,300)&&choice.trim()&&choice!==identity&&choice!==activation.node_id
  &&!uuidPattern.test(choice)&&!/^[a-f0-9]{64}$/.test(choice))return choice;
 return '等待处理结果 '+(index+1);
}

const windowWords={
 open:{title:'等待已登记来源的处理结果',detail:'等待状态由后台核对；页面时间不会把这项等待改判为超时。'},
 consumed:{title:'处理结果已到达',detail:'已保存消息到达回执，仍需按业务要求核实处理结果。'},
 timed_out:{title:'后台已确认等待超时',detail:'该项等待已保存超时结果，后续处理以任务进度为准。'},
 cancelled:{title:'该项等待已取消',detail:'已有记录保留，后到结果不会推进这项已取消的等待。'},
};

export function graphMessageStatusView(task){
 const status=inspectGraphMessageStatus(task);if(status===null)return null;
 let problem=null;
 if(status.error){
  const phase=status.error.status;
  if(phase==='queued'||phase==='running')problem={kind:phase,title:'上次检查问题已申请恢复',
   detail:phase==='queued'?'原消息检查正在排队；尚不代表检查成功或所等结果已经到达。':'后台正在核对原消息；尚不代表检查成功或所等结果已经到达。'};
  else if(phase==='succeeded'||phase==='cancelled')problem={kind:'ended',title:'原消息检查已结束',
   detail:'请更新等待进度核对当前结果；原问题记录不代表本次又发生了失败。'};
  else if(terminal.has(task.graph_runtime.status))problem={kind:'history',title:'已保留原消息检查问题',
   detail:'任务已结束，不会重新开启等待；已有消息、结果和草稿保留。'};
  else problem={kind:'blocked',title:'消息检查尚未完成',
   detail:'请恢复原检查，或联系管理员核对来源。已有等待窗口、期限和草稿保留。'};
 }
 return {status,terminal:terminal.has(task.graph_runtime.status),checkedAt:formatGraphMessageTime(status.last_checked_at),problem,
  windows:status.windows.map((window,index)=>({key:window.activation_id,label:businessLabel(task,window.activation_id,index),
   status:window.status,title:windowWords[window.status].title,detail:windowWords[window.status].detail,
   openedAt:formatGraphMessageTime(window.opened_at),deadline:formatGraphMessageTime(window.deadline),
   closedAt:window.closed_at===null?null:formatGraphMessageTime(window.closed_at)}))};
}
