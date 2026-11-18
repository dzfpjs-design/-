import React,{useEffect,useState} from 'react';
import {json} from './runtime-api';
import {scopedPreparationRequester} from './understanding-preparation-contract.mjs';
import {reportMaterialEligible,verifyReportReading} from './report-draft-contract.mjs';
export default function ReportDraftMaterialAction({material,account,Btn,onOpen,onOpenFullReading,onOpenLegacyFullReading}){
 const [reading,setReading]=useState(null),[error,setError]=useState('');
 const eligible=reportMaterialEligible(material);
 useEffect(()=>{let active=true;setReading(null);setError('');if(!eligible||!account?.user_id)return;const request=scopedPreparationRequester(account,json);(async()=>{try{const value=await request('/setup/report-drafts/readings/'+material.id+'?version='+material.version+'&hash='+material.hash);const me=await request('/me');const verified=await verifyReportReading(value,{id:material.id,version:material.version,hash:material.hash});if(active&&me.user_id===account.user_id&&me.workspace_id===account.workspace_id)setReading(verified.reading);}catch(e){if(active)setError(e.status===404?'正文读取范围将在草案预览中核对。':e.message);}})();return()=>{active=false;};},[material.id,material.version,material.hash,eligible,account?.user_id,account?.workspace_id]);
 if(!eligible)return null;
 return <section className="report-material-action"><p>{reading?'接受修订后的正文与表格：'+reading.blocks.length+'段':'可基于正文读取视图生成草案'}</p><small>原资料第{material.version}版 · 报告文字与图表内容分别核对，建议需要你确认。</small>{error&&<small>{error}</small>}{onOpenFullReading?<><Btn primary onClick={()=>onOpenFullReading(material.id)}>阅读报告并生成业务草案</Btn><Btn onClick={()=>onOpen(material.id)}>仅根据已读取正文生成</Btn></>:<Btn primary onClick={()=>onOpen(material.id)}>基于已读取正文生成待核业务草案</Btn>}{onOpenLegacyFullReading&&<Btn onClick={()=>onOpenLegacyFullReading(material.id)}>此前的完整读取记录</Btn>}</section>;
}
