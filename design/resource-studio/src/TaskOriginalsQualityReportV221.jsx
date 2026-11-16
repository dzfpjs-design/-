import React from 'react';
import {originalsQualityViewV221} from './task-originals-quality-report-v221.mjs';

export default function TaskOriginalsQualityReportV221({report,task,scope,source}){
 let view;try{view=originalsQualityViewV221(report,{task,scope,source});}catch{return <section className="media-form" aria-label="原件技术检查报告"><h5>原件技术检查报告</h5><p role="alert">这份报告暂未与当前任务的已完成来源对应，未显示文件名称或当作核验通过。请保留人工草稿并重新读取当前任务。</p></section>;}
 return <section className="media-form" aria-label="原件技术检查报告"><h5>原件技术检查报告</h5><p role={view.blocked?'alert':'status'}>已保存报告：{view.status}。</p><p>报告中可核对原件 {view.count} 份 · 字节摘要一致 {view.hashCount} 份 · 格式核验通过 {view.formatCount} 份。</p>
 {view.partialCount>0&&<p role="alert">{view.partialCount} 份资料仅有部分解析或OCR候选，仍需核对完整来源；这项缺口保留，没有改为整体通过。</p>}{view.unmatchedCount>0&&<p role="alert">{view.unmatchedCount} 份原件还有来源未对应，未猜测文件名称或借用其他任务资料。</p>}
 {view.rows.map((row,i)=><article className="media-form" key={i}><h6>{row.title}</h6>{row.version&&<p>任务保存的来源版本：{row.version}</p>}<p>字节检查：{row.bytes} · 格式检查：{row.format} · 文件大小：{row.size===null?'尚未核实':row.size+' 字节'}。</p>{row.referenceWarning&&<p role="alert">{row.referenceWarning}</p>}{row.issues.length?<ul>{row.issues.map((issue,j)=><li key={j} role={issue.blocked?'alert':undefined}>{issue.text}</li>)}</ul>:<p>报告未列出这份原件的技术问题；仍需相应业务核查。</p>}</article>)}
 {view.unboundIssues.length>0&&<aside className="info"><h6>尚未对应到具体原件的来源问题</h6><ul>{view.unboundIssues.map((issue,i)=><li key={i} role={issue.blocked?'alert':undefined}>{issue.text}</li>)}</ul></aside>}
 {view.duplicateGroups.length>0&&<section><h6>字节完全相同的原件</h6>{view.duplicateGroups.map((group,i)=><p key={i}>{group.join('、')}：文件字节相同，原件均不自动删除；不能据此认定是同一个体或同一事件。</p>)}</section>}
 <p>本次检查未删除原件，已有任务记录和本报告保留。技术检查不代表整份资料语义完整、物种识别或现场状态已确认，也不等于数据管理独立接收。</p><p>请结合原资料填写下方人工判断；报告中的缺口不会因填写判断自动消失。</p></section>;
}
