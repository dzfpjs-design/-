import React,{useEffect,useRef,useState} from 'react';
import {appendEvidenceStructure,structureAvailability} from './report-evidence-structure.mjs';
import {copy,same} from './report-full-reading-contract.mjs';

const Button=({children,...props})=><button type="button" {...props}>{children}</button>;
const titles={business:'补充业务',step:'补充步骤',participant:'补充参与方',interaction:'补充互动',condition:'补充条件',coordination:'补充协同'};
const agents=['业务智能体','多模感知智能体','数据管理智能体','人工确认'];
export const evidenceStructureFormKey=(kind,businessIndex)=>kind+':'+(kind==='business'?'new':businessIndex);
function initial(kind){
 const fields={business:{name:''},step:{name:'',purpose:'',output:'',agent:'',role:'',next_index:''},participant:{agent:'',responsibility:''},interaction:{from_participant_index:'',to_participant_index:'',purpose:'',condition:'',step_indices:[]},condition:{kind:'',statement:'',step_indices:[]},coordination:{kind:'',start_rule:'',wait_rule:'',join_rule:'',step_indices:[]}}[kind];
 return {...fields,reason:''};
}

export default function EvidenceStructureAddForm({value,kind,businessIndex,scopeKey,writable=false,busy=false,Btn=Button,formDraft=null,onFormDraft,onStructure}){
 const title=titles[kind],formKey=evidenceStructureFormKey(kind,businessIndex),mounted=useRef(scopeKey),latest=useRef(null),alive=useRef(true),submitting=useRef(false),shadowRef=useRef(null);
 const [shadow,setShadow]=useState(null),[error,setError]=useState(''),[notice,setNotice]=useState(''),[confirmCancel,setConfirmCancel]=useState(false);
 latest.current={scopeKey,value,writable,busy,onFormDraft,onStructure,formDraft};
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);
 function current(){if(!alive.current||mounted.current!==latest.current.scopeKey)throw Error('账号或人工稿范围已变化，本次补充未加入其他稿。');return latest.current;}
 function fail(failure){if(alive.current&&mounted.current===latest.current.scopeKey)setError(failure.message);}
 function retained(){return shadowRef.current||latest.current.formDraft;}
 function persist(next){
  const now=current();if(!now.writable||now.busy||!now.onFormDraft)throw Error('当前只读或正在处理，本次填写保留。');
  try{if(now.onFormDraft(next)===false)throw Error('补充内容尚未可靠保存，请保留页面并重试保护输入。');shadowRef.current=null;setShadow(null);setError('');return true;}
  catch(failure){if(next!==null){shadowRef.current=copy(next);setShadow(copy(next));}throw failure;}
 }
 function open(){try{const now=current();if(!now.writable||now.busy||!now.onStructure)return;if(retained())throw Error('请先完成或明确放弃当前补充。');const available=structureAvailability(now.value,kind,businessIndex);if(!available.allowed)throw Error(available.reason);persist({key:formKey,kind,business_index:kind==='business'?null:businessIndex,base_value:copy(now.value),fields:initial(kind)});setNotice('');setConfirmCancel(false);}catch(failure){fail(failure);}}
 function change(field,value){try{const now=current(),draft=retained();if(!now.writable||now.busy||draft?.key!==formKey)return;persist({...copy(draft),fields:{...copy(draft.fields),[field]:value}});setConfirmCancel(false);}catch(failure){fail(failure);}}
 function discard(){try{current();persist(null);setConfirmCancel(false);setNotice('已明确放弃本次补充，原人工稿保持原状。');}catch(failure){fail(failure);}}
 function cancel(){try{const draft=retained();if(!current().busy&&draft?.key===formKey){if(!same(draft.fields,initial(kind)))setConfirmCancel(true);else discard();}}catch(failure){fail(failure);}}
 function rebase(){try{const now=current(),draft=retained();if(draft?.key!==formKey)return;const available=structureAvailability(now.value,kind,businessIndex);if(!available.allowed)throw Error(available.reason);persist({...copy(draft),base_value:copy(now.value)});setNotice('已保留全部填写内容，请按当前业务、参与方和步骤重新核对后再加入。');}catch(failure){fail(failure);}}
 function append(){
  if(submitting.current)return;submitting.current=true;
  try{
   const now=current(),draft=retained();if(!now.writable||now.busy||!now.onStructure||draft?.key!==formKey)return;
   if(!same(now.value,draft.base_value))throw Error('人工稿已有变化，请保留填写内容，按当前人工稿重新核对补充。');
   const payload=copy(draft.fields);
   if(kind==='step'){if(payload.next_index==='')throw Error('请明确选择完成后的去向，或明确选择本路结束；不会自动替你选择。');payload.next_index=payload.next_index==='end'?null:Number(payload.next_index);}
   if(kind==='interaction')for(const key of ['from_participant_index','to_participant_index']){if(payload[key]==='')throw Error('请明确选择本业务中的参与方。');payload[key]=Number(payload[key]);}
   const next=appendEvidenceStructure(now.value,{kind,businessIndex,payload});
   if(now.onStructure(next)===false)throw Error('人工补充尚未可靠保存，本次填写保留，请重试保护输入。');
   shadowRef.current=null;setShadow(null);setConfirmCancel(false);setError('');setNotice('已加入当前人工稿，仍需核对并保存服务器回执。');
  }catch(failure){fail(failure);}finally{submitting.current=false;}
 }
 if(!title||mounted.current!==scopeKey)return null;
 const draft=shadow||formDraft,opened=draft?.key===formKey,other=!!draft&&!opened,fields=opened?draft.fields:initial(kind),changed=opened&&!same(value,draft.base_value);
 let availability={allowed:false,reason:'仅完整人工稿可补充，原模型候选不会被修改。'};
 try{availability=structureAvailability(value,kind,businessIndex);}catch{/* Existing incomplete edits remain in the current manual value. */}
 const disabled=busy||!writable,business=(opened?draft.base_value:value)?.result?.businesses?.[businessIndex],steps=business?.steps||[],people=business?.participants||[];
 const input=(key,label,max=1000)=><label>{label}<textarea aria-label={title+'：'+label} value={fields[key]} maxLength={max} disabled={disabled} onChange={event=>change(key,event.target.value)}/></label>;
 const select=(key,label,options)=><label>{label}<select aria-label={title+'：'+label} value={fields[key]} disabled={disabled} onChange={event=>change(key,event.target.value)}><option value="">请选择</option>{options.map(([key,label])=><option value={key} key={key}>{label}</option>)}</select></label>;
 const stepRefs=()=> <fieldset disabled={disabled}><legend>涉及的本业务步骤（勾选顺序不决定时序）</legend>{steps.length?steps.map((step,index)=><label key={index}><input type="checkbox" aria-label={title+'：涉及步骤 '+(index+1)+' '+step.name} checked={fields.step_indices.includes(index)} onChange={event=>change('step_indices',event.target.checked?[...fields.step_indices,index]:fields.step_indices.filter(value=>value!==index))}/>{step.name}</label>):<p>本业务尚无步骤；不会为这次补充编造步骤。</p>}</fieldset>;
 return <section className="evidence-structure-add" aria-label={title+'人工待核结构'}>
  {!opened&&<Btn disabled={disabled||other||!onFormDraft||!onStructure||!availability.allowed} onClick={open}>{title}</Btn>}
  {other&&<small>请先完成或明确放弃当前补充。</small>}
  {!opened&&!other&&writable&&!availability.allowed&&<p>{availability.reason}</p>}
  {notice&&<p role="status">{notice}</p>}
  {error&&<p role="alert">{error} 本次填写保留，尚未自动修正。</p>}
  {opened&&<>
   <h5>{title} · 人工补充待核</h5><p>本次补充不代表原文明示事实，也不授予执行权限。填写由本机草稿保护；加入后仍需保存服务器人工稿。</p>
   {kind!=='business'&&<p>本次补充原属业务：{business?.name||'原业务暂不可定位'}。请按打开补充时的参与方与步骤核对。</p>}
   {shadow&&<p role="alert">末次填写尚未可靠保存，请保留页面。<Btn disabled={disabled} onClick={()=>{try{persist(shadow);}catch(failure){fail(failure);}}}>重试保护补充输入</Btn></p>}
   {changed&&<p role="alert">人工稿已有变化，当前填写没有被丢弃。<Btn disabled={disabled} onClick={rebase}>按当前人工稿重新核对补充</Btn></p>}
   {kind==='business'&&input('name','业务名称',100)}
   {kind==='step'&&<>{input('name','步骤名称',120)}{input('purpose','步骤目的')}{input('output','步骤产出')}{select('agent','负责智能体',agents.map(agent=>[agent,agent]))}{input('role','负责角色',100)}{select('next_index','完成后去向',[['end','本路结束（本候选中的边界）'],...steps.map((step,index)=>[String(index),step.name])])}<p>新步骤不会自动接到已有步骤；加入后仍需核对启动条件、产出和各步骤去向。</p></>}
   {kind==='participant'&&<>{input('agent','参与方称谓',100)}{input('responsibility','参与职责')}</>}
   {kind==='interaction'&&<>{select('from_participant_index','由谁发起',people.map((person,index)=>[String(index),person.agent]))}{select('to_participant_index','交给谁',people.map((person,index)=>[String(index),person.agent]))}{input('purpose','互动目的')}{input('condition','互动条件（未明确可留空）')}{stepRefs()}</>}
   {kind==='condition'&&<>{select('kind','条件类别',[['precondition','开始前提'],['trigger','触发条件'],['stop','停止条件'],['exception','例外情况']])}{input('statement','条件内容')}{stepRefs()}</>}
   {kind==='coordination'&&<>{select('kind','协同关系',[['sequence','顺序'],['branch','分支'],['parallel','并行'],['unspecified','尚未明确']])}{input('start_rule','开始规则（未明确可留空）')}{input('wait_rule','等待规则（未明确可留空）')}{input('join_rule','汇合规则（未明确可留空）')}{stepRefs()}</>}
   {input('reason','补充依据与待核事项',['business','step'].includes(kind)?500:1000)}
   <p>这段说明会保留为待核问题；不会自动补引用、角色、节点或连接。</p>
   <div className="actions"><Btn disabled={disabled||changed||!onStructure||!availability.allowed} onClick={append}>加入人工稿，仍待核对</Btn><Btn disabled={busy} onClick={cancel}>取消本次补充</Btn></div>
   {confirmCancel&&<div role="group" aria-label={'确认放弃'+title}><p>本次填写尚未加入人工稿。只有明确放弃才会清除这些输入。</p><Btn disabled={busy} onClick={()=>setConfirmCancel(false)}>继续填写</Btn><Btn disabled={busy} onClick={discard}>确认放弃本次填写</Btn></div>}
  </>}
 </section>;
}
