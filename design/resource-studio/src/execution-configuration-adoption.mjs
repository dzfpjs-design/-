import {decodeConfigurationReply,savedConfigurationWire} from './configuration-numeric-wire.mjs';
const object=value=>value&&typeof value==='object'&&!Array.isArray(value);
const need=(value,message)=>{if(!value)throw Error(message);};

/** Project one confirmed flow without closing the report or activating it. */
export async function adoptExecutionConfiguration({saved,scope,receipt,wire,account,checkEntry,adoptServerDraft,onStatus=()=>{}}){
 checkEntry();
 need(scope?.actor_id===account?.user_id&&scope?.workspace_id===account?.workspace_id,'账号或工作区已变化，原采用回执保留。');
 need(receipt?.schema==='business-flow-execution-adoption-result-v1'&&receipt.can_execute===false
  &&Number.isSafeInteger(receipt.accepted_revision)&&receipt.accepted_revision>=1,'原流程采用结果无法核对。');
 need(Number.isSafeInteger(saved?.revision)&&saved.revision>=receipt.accepted_revision&&object(saved.configuration),'当前配置尚未包含原采用版本。');
 const origin=receipt.origin,origins=saved.configuration.businessFlowExecutions,current=object(origins)&&Object.hasOwn(origins,origin?.business_name)?origins[origin.business_name]:null;
 need(origin?.adoption_id===receipt.id&&object(current)&&Object.keys(origin).length===Object.keys(current).length
  &&Object.keys(origin).every(key=>origin[key]===current[key]),'当前配置的流程来源与原采用记录不一致，回执保留。');
 let projected=saved;
 if(typeof wire?.raw==='string'){
  const original=decodeConfigurationReply(wire.raw);
  need(original.revision===saved.revision&&JSON.stringify(original)===JSON.stringify(saved),'原配置与页面完整镜像不一致，原采用回执保留。');
  projected=original;
 }else if(wire?.requires_lossless_sync!==false){
  return {accepted:false,reason:'此流程已加入服务器配置。当前配置含需保留原数值类型的字段，无损页面同步尚未接通；原回执与配置原文已保留，未覆盖本页配置。'};
 }
 const result=await adoptServerDraft(projected);checkEntry();
 onStatus({state:'saved',text:'此流程已加入配置，尚未生成或启用；其他流程可继续核对。'+(result?.preservedPending?'另一页面未提交的配置仍保留。':'')});
 return {accepted:true,preserved_numeric_tokens:savedConfigurationWire(projected)!==null,preservedPending:result?.preservedPending===true};
}
