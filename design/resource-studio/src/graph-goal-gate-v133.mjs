// Read-only server projection.  This module never infers execution authority
// from a local clock, GoalSpec, transport history or private reservations.
export const GRAPH_GOAL_GATE_SCHEMA='workflow-graph-goal-gate-v133';
const keys=['schema','status','reason','can_submit_new_result','can_retry_ability'];
const reasons={active:[null],expired:['deadline_expired'],stopped:['goal_stopped'],accepted:['goal_accepted'],budget_blocked:['max_tool_calls','max_steps','human_budget_approved'],unavailable:['goal_context_unavailable']};
const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
function need(value){if(!value){const error=new Error('目标执行限制暂无法核对，原输入、草稿和请求记录保留。');error.code='GRAPH_GOAL_GATE_INVALID';throw error;}}

export function inspectGraphGoalGate(task){
 need(object(task));
 if(!Object.hasOwn(task,'graph_goal_gate_v133')){
  need((task.goal_spec===null||task.goal_spec===undefined)
   &&(task.native_goal_context===null||task.native_goal_context===undefined));
  return null;
 }
 const value=task.graph_goal_gate_v133;
 need(object(value)&&Object.keys(value).length===keys.length&&keys.every(key=>Object.hasOwn(value,key))
  &&value.schema===GRAPH_GOAL_GATE_SCHEMA&&Object.hasOwn(reasons,value.status)
  &&reasons[value.status].includes(value.reason)&&typeof value.can_submit_new_result==='boolean'
  &&typeof value.can_retry_ability==='boolean');
 // A human allowance leaves the capability limits in force.
 const expected=value.status==='active'?[true,true]
  :value.status==='budget_blocked'&&['max_tool_calls','human_budget_approved'].includes(value.reason)?[true,false]:[false,false];
 need(value.can_submit_new_result===expected[0]&&value.can_retry_ability===expected[1]);
 return Object.fromEntries(keys.map(key=>[key,value[key]]));
}

export function graphGoalGateView(task){
 const gate=inspectGraphGoalGate(task);if(gate===null)return null;
 const titles={active:'目标执行限制',expired:'原目标已到截止时间',stopped:'原目标已停止',accepted:'原目标已保存验收结果',budget_blocked:'原目标执行额度已受限',unavailable:'原目标执行条件需核对'};
 const details={
  active:'每次新的结果提交与能力执行前，服务器仍会核对原目标、有效期和累计额度。这里不表示剩余额度或新的执行授权。',
  expired:'不能继续提交此目标的新结果或重新启动能力。到期前已发出的调用仍按原回执核对，返回结果会保留。',
  stopped:'不能继续提交此目标的新结果或重新启动能力。已发出的调用和返回结果保留，不会因此认定已撤销现场动作。',
  accepted:'原目标的验收结果与依据保留，不能继续向同一目标提交新结果或重新启动能力。',
  max_tool_calls:'服务器已确认原目标的能力调用额度已用尽，当前不能启动新的能力调用。人工结果提交仍受原步骤限制；原额度不会因重试增加。',
  max_steps:'服务器已确认原目标的步骤额度已用尽，当前不能提交新的步骤结果或重新启动能力；原额度不会因重试增加。',
  human_budget_approved:'当前人工待办已获单独批准的步骤额度，可以提交处理结果。能力调用额度仍受原目标限制。',
  unavailable:'服务器暂不能确认原目标的执行条件；请由任务负责人核对原目标与当前执行范围。'
 };
 return {gate,title:titles[gate.status],detail:details[gate.reason]||details[gate.status],
  limited:gate.status!=='active',
  continuation:gate.status==='active'||gate.reason==='human_budget_approved'?null:gate.reason==='max_tool_calls'
   ?'仍可在原步骤额度内提交人工结果，执行前仍由服务器核对；需要追加能力调用时，请按当前依据另行确认新任务。刷新或重试不能解除原目标限制。':gate.status==='unavailable'
   ?'重新读取可核对最新状态，不会自动解除限制或重新执行能力。'
   :'如需继续新的工作，请按当前依据另行确认新任务；刷新或重试不能解除原目标限制。',
  preservation:'原输入、草稿、已保存结果和原请求记录保留。仍可编辑、保存草稿、查看历史及只核对原请求。'};
}
