// Append-only human edits to a private manual value, never an original answer.
// The outer save path still verifies source semantics, scope, CAS and receipts.
import {copy,fullKeys,fullText} from './report-full-reading-contract.mjs';
import {evidenceManualValidationView} from './report-evidence-manual-v5.mjs';

const LIMITS={business:20,step:16,participant:40,interaction:40,condition:40,coordination:40};
const COLLECTIONS={business:'businesses',step:'steps',participant:'participants',interaction:'interactions',condition:'conditions',coordination:'coordination'};
const FIELDS={business:'name,reason',step:'name,purpose,output,agent,role,next_index,reason',participant:'agent,responsibility,reason',interaction:'from_participant_index,to_participant_index,purpose,condition,step_indices,reason',condition:'kind,statement,step_indices,reason',coordination:'kind,start_rule,wait_rule,join_rule,step_indices,reason'};
const AGENTS=['业务智能体','多模感知智能体','数据管理智能体','人工确认'];
const need=(condition,message)=>{if(!condition)throw Error(message);};
const text=(value,max,required=false)=>fullText(value,max)&&(!required||!!value.trim());
const bytes=value=>new TextEncoder().encode(JSON.stringify(value)).length;
const validIndex=(value,length)=>Number.isSafeInteger(value)&&!Object.is(value,-0)&&value>=0&&value<length;

function baseline(value){
 evidenceManualValidationView(value);
 need(bytes(value)<=512*1024,'完整人工稿超过512 KiB保存上限，原稿保留，未截断。');
 return value.result;
}
function scope(value,kind,businessIndex){
 const result=baseline(value);
 need(Object.hasOwn(LIMITS,kind),'请选择可追加的业务结构。');
 if(kind==='business'){
  need(businessIndex===undefined||businessIndex===null,'新增业务不能指定另一业务位置。');
  return {result,business:null,items:result.businesses};
 }
 need(validIndex(businessIndex,result.businesses.length),'请选择当前人工稿中已有的业务。');
 const business=result.businesses[businessIndex];
 return {result,business,items:business[COLLECTIONS[kind]]};
}
function selectedSteps(value,business){
 need(Array.isArray(value)&&value.length<=16&&new Set(value).size===value.length&&value.every(i=>validIndex(i,business.steps.length)),
  '关联步骤须明确选择本业务已有且不重复的步骤。');
 return copy(value);
}
function checkedText(value,max,label,required=true){
 need(text(value,max,required),label+'须为有效文字'+(required?'且不能只含空白':'')+'，并符合长度上限。');
 return value;
}
function newItem(kind,payload,business){
 need(fullKeys(payload,FIELDS[kind]),'新增内容字段不完整或含未支持字段，未修改人工稿。');
 const reason=checkedText(payload.reason,['business','step'].includes(kind)?500:1000,'补充理由');
 const evidence=()=>({status:'unknown',citations:[],explanation:reason});
 let item;
 if(kind==='business'){
  const name=checkedText(payload.name,100,'业务名称');need(name===name.trim(),'业务名称不能带首尾空白，未自动改名。');
  item={operation:'upsert',original_name:'',name,trigger:'',output:'',steps:[],citations:[],inference_reason:reason,participants:[],interactions:[],conditions:[],coordination:[]};
 }
 else if(kind==='step'){
  need(AGENTS.includes(payload.agent),'步骤须明确选择执行能力类别。');
  need(payload.next_index===null||validIndex(payload.next_index,business.steps.length),
   '请明确选择候选结束标记或本业务已有步骤；不会默认结束或连接前一步。');
  item={name:checkedText(payload.name,120,'步骤名称'),purpose:checkedText(payload.purpose,1000,'步骤目的'),
   agent:payload.agent,role:checkedText(payload.role,100,'职责称谓',payload.agent==='人工确认'),
   output:checkedText(payload.output,1000,'步骤产出'),branch_condition:'',citations:[],inference_reason:reason,
   next_index:payload.next_index,else_index:null};
 }else if(kind==='participant')item={agent:checkedText(payload.agent,100,'参与方称谓'),
  responsibility:checkedText(payload.responsibility,1000,'参与方职责'),evidence:evidence()};
 else if(kind==='interaction'){
  need(validIndex(payload.from_participant_index,business.participants.length)&&validIndex(payload.to_participant_index,business.participants.length)&&payload.from_participant_index!==payload.to_participant_index,
   '互动双方须明确选择本业务已有的两个不同参与方。');
  item={from_participant_index:payload.from_participant_index,to_participant_index:payload.to_participant_index,
   purpose:checkedText(payload.purpose,1000,'互动目的'),condition:checkedText(payload.condition,1000,'互动条件',false),
   step_indices:selectedSteps(payload.step_indices,business),evidence:evidence()};
 }else if(kind==='condition'){
  need(['precondition','trigger','stop','exception'].includes(payload.kind),'请选择前提、触发、停止或异常条件。');
  item={kind:payload.kind,statement:checkedText(payload.statement,1000,'条件陈述'),
   step_indices:selectedSteps(payload.step_indices,business),evidence:evidence()};
 }else{
  need(['sequence','branch','parallel','unspecified'].includes(payload.kind),'请选择支持的协同形态或未明形态。');
  const steps=selectedSteps(payload.step_indices,business);
  need(payload.kind==='unspecified'||steps.length>=2,'明确顺序、分支或并行须关联至少两个不同已有步骤。');
  item={kind:payload.kind,step_indices:steps,start_rule:checkedText(payload.start_rule,1000,'开始规则',false),
   wait_rule:checkedText(payload.wait_rule,1000,'等待规则',false),join_rule:checkedText(payload.join_rule,1000,'汇合规则',false),evidence:evidence()};
 }
 return {item,reason};
}

export function structureAvailability(value,kind,businessIndex){
 let count=null,limit=Object.hasOwn(LIMITS,kind)?LIMITS[kind]:null;
 try{
  const scoped=scope(value,kind,businessIndex);count=scoped.items.length;
  need(count<limit,'此类结构已达数量上限，原内容保留，未裁剪。');
  need(scoped.result.questions.length<30,'待核问题已达30条，无法保留此次补充理由；原稿未修改。');
  if(kind==='step')need(!!scoped.business.trigger.trim()&&!!scoped.business.output.trim(),'请先在原业务中核对并填写启动条件与产出，再追加步骤；不会猜补。');
  return {allowed:true,reason:'',count,limit};
 }catch(error){return {allowed:false,reason:error.message,count,limit};}
}

export function appendEvidenceStructure(value,command){
 need(command&&typeof command==='object'&&!Array.isArray(command)&&
  (fullKeys(command,'kind,payload')||fullKeys(command,'kind,businessIndex,payload')),
 '新增结构命令字段无法核对，原稿未修改。');
 const {kind,businessIndex,payload}=command,{result,business}=scope(value,kind,businessIndex);
 const available=structureAvailability(value,kind,businessIndex);need(available.allowed,available.reason);
 const {item,reason}=newItem(kind,payload,business);
 if(kind==='business')need(!result.businesses.some(b=>b.name===item.name),'同名业务已存在，请核对原业务；未合并或改名。');
 const next=copy(value),target=kind==='business'?next.result.businesses:next.result.businesses[businessIndex][COLLECTIONS[kind]];
 target.push(item);next.result.questions.push(reason);
 baseline(next);
 return next;
}
