import React from 'react';
const labels={not_requested:'尚未保存本次处理请求',queued:'请求已保存，等待后台领取',running:'后台已领取，处理回执尚未完成',waiting_recovery:'原租约已失效，等待受控恢复',waiting_approval:'已保存的检查等待业务审批状态允许执行',approval_closed:'原业务已结束，旧检查请求保留',succeeded:'已保存后台处理回执',failed:'后台处理失败，请核对原因和原回执',blocked:'当前条件阻止执行，尚未创建后台任务',cancelled:'已停止后续处理，原回执保留',stale:'原处理范围或依据已变化，需要核对',unverified:'原作业尚未通过身份核对'};
const stamp=value=>value&&Number.isFinite(Date.parse(value))?new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}):'尚未取得';
export default function WorkflowWorkerProgress({worker,label='自动步骤处理进度'}){
 return <section aria-label={label}><h4>{label}</h4>{worker?.historical&&<p>历史处理记录：不能据此认定当前步骤或本次核查已完成。</p>}<p role="status">{labels[worker?.state]||'尚未取得可核对的后台处理状态'}</p>{worker?.reason&&<p>{worker.reason}</p>}<p>最近保存活动（北京时间）：{stamp(worker?.last_activity_at)}。数据库核对时间：{stamp(worker?.database_now)}。</p>{worker?.lease_until&&<p>当前登记租约至：{stamp(worker.lease_until)}。租约记录不证明处理器仍在线。</p>}{worker?.lease_recoveries>0&&<p>原作业已登记恢复 {worker.lease_recoveries} 次，沿原任务核对。</p>}<p>处理器在线状态：尚未取得可核实的心跳。排队记录会保留；离开页面不会保证后台已经启动。</p></section>;
}
