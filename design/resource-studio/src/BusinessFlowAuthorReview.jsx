import React from 'react';
import {authorReviewState,editAuthorReview,previewAuthorReview,submitAuthorReview,recoverAuthorReview,archiveRejectedAuthorReview,loadAuthorReviews} from './business-flow-author-review.mjs';

export default function BusinessFlowAuthorReview({controller:c,onConfigureExecution}){
 const s=authorReviewState(c),p=s?.preview;
 const action=fn=>{try{Promise.resolve(fn()).catch(()=>{});}catch(error){c.error=error.message;c.notify();}};
 const ready=c.canEdit&&c.localSaved&&!c.busy&&!c.pending;
 return <section className="bfr-card" aria-label="保存版本的作者核对"><h3>核对已保存版本</h3>
  <p>先保存修订稿，再检查原文与连线。核对记录标明当前账号及具体版本；正式采用、岗位授权和设备执行另行设置。</p>
  <button disabled={!ready||!c.form||c.form.dirty||!c.detail?.saved||!!s?.pending} onClick={()=>action(()=>previewAuthorReview(c))}>检查已保存版本</button>
  {p&&<><p>业务：{p.business_name} · 保存版本 {p.expected_version}</p><ul>{p.flows.map(f=><li key={f.flow_id}>{f.name}：{f.trigger.description}；{f.node_count} 个步骤，{f.edge_count} 条连线。</li>)}</ul>
   <p>本次已检查原件和引用。连线及文字匹配通过不代表系统已经认定业务设计正确。</p>
   <label className="bfr-check"><input type="checkbox" checked={s.ack_scope===true} disabled={!ready||!!s.pending} onChange={e=>action(()=>editAuthorReview(c,{ack_scope:e.target.checked}))}/>我已对照全部流程的触发、分支、职责、输入输出及原文核对业务范围。</label>
   <label className="bfr-check"><input type="checkbox" checked={s.ack_inferences===true} disabled={!ready||!!s.pending} onChange={e=>action(()=>editAuthorReview(c,{ack_inferences:e.target.checked}))}/>我已核对推断和冲突处理，当前没有未解决的待核问题。</label>
   <label className="bfr-field"><span>核对说明</span><textarea aria-label="作者核对说明" value={s.reason} disabled={!ready||!!s.pending} onChange={e=>action(()=>editAuthorReview(c,{reason:e.target.value}))} rows={3}/></label>
   <button disabled={!ready||!!s.pending||c.form?.dirty||c.form?.version!==p.expected_version||!s.ack_scope||!s.ack_inferences||s.reason.trim().length<2} onClick={()=>action(()=>submitAuthorReview(c))}>记录这一版本的作者核对</button>
  </>}
  {s?.pending&&<div className="bfr-pending"><strong>原核对结果待确认</strong><p>原编号和原文已保留，刷新后可继续查询。没有查到回执不表示请求未到达。</p><button disabled={!ready} onClick={()=>action(()=>recoverAuthorReview(c))}>查询原核对结果</button>{!s.pending.rejection&&<button disabled={!ready} onClick={()=>action(()=>recoverAuthorReview(c,true))}>按原编号与原内容重试</button>}{s.pending.rejection&&<button disabled={!ready} onClick={()=>action(()=>archiveRejectedAuthorReview(c))}>保留被拒原请求，重新核对版本</button>}</div>}
  {s?.result&&<div><p role="status">保存版本 {s.result.draft_version} 的作者核对记录已存入服务器。之后的修订需要重新核对，当前没有启动执行。</p>{onConfigureExecution&&<button disabled={!ready} onClick={()=>action(()=>onConfigureExecution(s.result))}>配置这一版本的实际执行</button>}</div>}
  <p><button disabled={!ready||!c.state.selected} onClick={()=>action(()=>loadAuthorReviews(c))}>读取服务器核对历史</button></p>
  {s?.history&&<ul>{s.history.map(v=><li key={v.id}>保存版本 {v.draft_version} · {v.reviewed_at} · {v.reason}。这是该旧版的作者核对，后续修订不继承。{onConfigureExecution&&<button disabled={!ready} onClick={()=>action(()=>onConfigureExecution(v))}>查看此核对版本的执行配置</button>}</li>)}{!s.history.length&&<li>服务器尚无这一业务稿的作者核对记录。</li>}</ul>}
 </section>;
}
