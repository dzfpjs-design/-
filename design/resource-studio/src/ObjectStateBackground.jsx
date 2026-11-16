import React from 'react';
const labels={queued:'等待本地核对',running:'正在本地核对',succeeded:'本地核对已完成',failed:'后台核对未完成',blocked_integrity:'后台回执需要核对',cancelled:'旧到期检查已停止'};
const stamp=v=>v?new Date(v).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}):'时间待核';
const admissions={ready:'当前可等待后台领取',waiting_data:'等待原数据独立接收',waiting_parent:'等待前序保存完成',blocked_authorization:'原发起人当前授权受阻',stale:'原请求版本已变化',blocked_integrity:'原依据完整性受阻',not_checked:'当前尚未核查执行条件',historical:'历史任务，保留原处理结果'};
export default function ObjectStateBackground({background,stateHash,stateVersion,cache,verified=true,permissions}){
 if(!background)return null;
 const latest=background.latest,next=background.next_expiry;
 return <aside aria-label="后台状态核对" className="state-background">
 <h4>对象状态更新 · 后台重新核对</h4>
 {!verified&&<p role="alert">当前读取尚未核对成功；以下保留此前读回记录，不表示后台已更新。</p>}
 {cache&&<p>{cache.status==='saved'?'已保存状态已读回；没有重新计算。':cache.status==='missing'?'尚无已保存状态；等待后台实际核对。':'已保存状态依据受阻，未作为当前数值。'}{cache.reason&&' '+cache.reason}</p>}
 {latest?<><p role={['failed','blocked_integrity'].includes(latest.status)?'alert':'status'}>{labels[latest.status]||'需核对后台记录'} · {latest.reason||'原触发范围待核'}</p>
 {latest.error&&<p>{latest.error}。原资料保留；可以请求本地重新核对，不会调用云模型或设备。</p>}
 {latest.admission&&<p>当前条件：{admissions[latest.admission.state]||'需要核对'}{latest.admission.reason?' · '+latest.admission.reason:''}。原任务状态仍为“{labels[latest.status]||'需核对'}”，没有修改原任务或代替原发起人。</p>}
 {latest.result&&<p>核对时间：{stamp(latest.result.checked_at)} · 保存状态第{latest.result.state_version}版。{verified&&cache?.status==='saved'&&cache.background_result_matched===true&&latest.result.state_hash===stateHash&&latest.result.state_version===stateVersion?'与下方已保存核对结果一致。':'这是此前保存的后台结果；不能把旧回执当作当前状态。'}</p>}</>:<p>尚无后台核对回执。新登记处理完成或人工核查改变后，后台会保存本地核对结果。</p>}
 {next&&<p>下一次依据到期核对：{stamp(next.available_at)}。只核对已存记录，不采集新数据。</p>}
 <p>这里只读已保存状态、原任务与当前条件；当前可领取不代表处理器在线。声音等模型候选仍按原数据收件和事实门保留，不提升为现场事实。</p>
 {permissions&&!permissions.can_request_new&&<p>当前仅可读取原记录。{permissions.reason}</p>}
 {permissions?.can_request_new&&latest?.admission?.state==='blocked_authorization'&&<p>当前获授权人员可明确发起新的后台核对，原受阻任务及其发起人保留；不会借用原发起人的权限。</p>}
 <p>资料完整、模型候选和人工登记均不等于现场状态已验证。</p>
 {background.history?.length>0&&<details><summary>查看后台核对记录（最近{background.history.length}条）</summary>{background.history.map(j=><p key={j.id}>{stamp(j.created_at)} · {labels[j.status]||'原状态待核'} · {j.reason||'原范围待核'}{j.admission?' · 当前：'+admissions[j.admission.state]+(j.admission.reason?'（'+j.admission.reason+'）':''):''}{j.error?' · '+j.error:''}{j.cancel_reason?' · '+j.cancel_reason:''}</p>)}{background.history.length>=background.history_limit&&<p>这里只列出最近{background.history_limit}条，旧后台记录仍保存在服务器。</p>}</details>}
 </aside>;
}
