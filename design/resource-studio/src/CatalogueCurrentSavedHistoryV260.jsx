import React,{useEffect,useMemo,useRef,useState} from 'react';
import {CatalogueCurrentSavedHistoryClientV260} from './catalogue-current-saved-history-v260.mjs';

const STATUS={queued:'原活动等待处理',running:'原活动正在处理',response_unknown:'原调用结果待核对',failed:'原活动处理失败',review:'建议已保存，等待核对',needs_clarification:'建议已保存，仍有待澄清问题'};
const DEFAULT_BUTTON=({children,...props})=><button type="button" {...props}>{children}</button>;
function Proposal({decision:d,businesses}){
 const label=d.raw_decision.disposition==='background'?'建议作为背景资料':d.raw_decision.disposition==='pending'?'归属仍待确认':'建议归入'+d.raw_decision.business_keys.length+'项原业务';
 const names=d.raw_decision.business_keys.map(key=>(Array.isArray(businesses)?businesses:[]).find(row=>row?.key===key&&typeof row.name==='string'&&row.name.trim())?.name),known=names.filter(Boolean),missing=names.length-known.length;
 return <article aria-label={'报告要求第'+(d.input_position+1)+'条的已保存建议'}>
  <h5>报告要求第{d.input_position+1}条</h5><p>{d.claim.statement}</p>{d.claim.explanation&&<p>{d.claim.explanation}</p>}
  <section aria-label="这条要求的已保存原文依据"><h6>已保存的原文依据</h6>{d.source_spans.map((span,i)=><blockquote key={span.span_ref}><small>引用{i+1}</small><p>{span.record.text}</p><details><summary>查看引用位置</summary><p>已保存来源：{span.record.source_ref} · 字符{span.record.start}—{span.record.end}</p></details></blockquote>)}<p>这些是已保存引用；本次没有重新读取或核验来源文件。</p></section>
  <h6>{label}</h6>{known.length>0&&<><p>当前工作稿中的对应业务：{known.join('、')}</p><p>显示的是当前工作稿名称，不能据此认定为当时的业务名称。</p></>}{missing>0&&<p>{missing}项对应业务已调整或名称未明确，请对照当前业务清单。</p>}<p>{d.raw_decision.reason}</p>
  {d.proposed_questions.map(q=><p key={q.key}>{q.kind==='ownership'?'归属待确认：':'仍需补充资料：'}{q.question}</p>)}
  {d.before_questions.length>0&&<details><summary>本条原有待核问题（{d.before_questions.length}项）</summary>{d.before_questions.map(q=><p key={q.key}>{q.question}{q.resolution&&'；原处理说明：'+q.resolution}</p>)}</details>}
 </article>;
}
export default function CatalogueCurrentSavedHistoryV260(props){
 const latest=useRef(props);latest.current=props;
 const scope=[props.account?.user_id,props.account?.workspace_id,props.account?.role,props.producerId,props.bindingId].join(':');
 const holder=useMemo(()=>{try{return {c:new CatalogueCurrentSavedHistoryClientV260({...props,api:(...args)=>latest.current.api(...args)})};}catch(error){return {error:error.message};}},[scope]);
 const c=holder.c,[state,setState]=useState(()=>c?.view());
 useEffect(()=>{if(!c)return;c.closed=false;const epoch=++c.epoch;c.onChange=v=>{if(c.current(epoch))setState(v);else if(!c.closed&&c.epoch===epoch&&v.denied)setState(v);};setState(c.view());void c.open();return()=>c.dispose();},[holder]);
 const Btn=props.Btn||DEFAULT_BUTTON;
 if(!c)return <section aria-label="已保存归类历史"><h4>已保存归类历史</h4><p role="alert">{holder.error||'当前历史读取范围尚不能核对。'}</p></section>;
 const v=state?.key===c.key?state:c.view(),invoke=fn=>{try{const result=fn();if(result?.catch)result.catch(error=>{if(!c.closed){c.error=error.message;c.emit();}});}catch(error){if(!c.closed){c.error=error.message;c.emit();}}};
 if(v.denied)return <section aria-label="已保存归类历史"><h4>已保存归类历史</h4><p role="alert">登录身份或工作区已变化，原历史内容已隐藏。</p></section>;
 return <section aria-label="已保存归类历史">
  <h4>快速查看已保存归类结果</h4>
  <p><strong>查看的是已保存结果；采纳前仍需重新核对当前资料。</strong>这里不重新核验原文件、当前配置或完整在途活动，也不提供新调用或发布许可。</p>
  {v.loading&&<p role="status">正在读取本人已保存活动索引…</p>}{v.reading&&<p role="status">正在核对这份已保存建议和原回执…</p>}
  {v.error&&<p role="alert">{v.error}</p>}{v.recordError&&<p role="alert">{v.recordError}</p>}
  {v.listUnverified&&<p>下面保留上次已读取的列表；本次刷新没有完成重新核对。</p>}
  <nav aria-label="已保存归类历史分页"><Btn disabled={v.loading||v.reading} onClick={()=>invoke(()=>c.recent())}>返回最新记录</Btn><Btn disabled={v.loading||v.reading||v.offset===0} onClick={()=>invoke(()=>c.previous())}>上一页历史</Btn><Btn disabled={v.loading||v.reading||!v.hasMore} onClick={()=>invoke(()=>c.next())}>下一页历史</Btn>{v.loaded&&<span>第{Math.floor(v.offset/v.limit)+1}页 · 本页{v.items.length}项</span>}{v.error&&<Btn disabled={v.loading||v.reading} onClick={()=>invoke(()=>c.retry())}>重试读取这页</Btn>}</nav>
  {v.loaded&&!v.items.length&&<p>这一页没有本人已保存活动。索引为空不代表没有在途调用，也不能据此另建付费申请。</p>}
  {v.items.map((row,i)=><article key={row.consumer_id} aria-label={'已保存活动第'+(v.offset+i+1)+'项'}><h5>已保存活动{v.offset+i+1} · {STATUS[row.status]}</h5><p>{row.note||'本项没有补充说明'}</p>{['review','needs_clarification'].includes(row.status)?<Btn disabled={v.loading||v.reading} onClick={()=>invoke(()=>c.select(row.consumer_id))}>查看第{v.offset+i+1}项已保存建议</Btn>:<p>尚无可读取的终态建议；这里只显示已保存状态，不重发原请求。</p>}</article>)}
  {v.record&&<section aria-label="已保存机答与原文依据"><h4>这份已保存的归类建议</h4><p>{v.selected?.note||'本项没有补充说明'}</p><p>共{v.record.answer.projection.decisions.length}条建议，均列在下方。以下不代表已采纳或当前资料已通过核对。</p>
   <p>{v.record.call.usage_unavailable?'原回执未提供完整用量；已保存记账数不能当作供应商真实结算。':'原回执用量：输入'+v.record.call.usage.prompt_tokens+'、输出'+v.record.call.usage.completion_tokens+'、合计'+v.record.call.usage.total_tokens+' tokens。'}</p>
   <p>投影摘要已核对；原回答字节摘要来自服务器保存的原件锚点，本页没有把解析后的回答重编码成原字节。</p>
   {v.record.answer.projection.decisions.map(d=><Proposal key={d.decision_key} decision={d} businesses={props.businesses}/>)}
  </section>}
 </section>;
}
