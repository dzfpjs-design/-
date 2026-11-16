import {safeReviewJSON} from './business-flow-review-controller.mjs';

export const NESTED_PLAN_SCHEMA='report-business-flow-nested-plan-v192';
export const NESTED_LIMITS=Object.freeze({flows:16,phases:12,actions:32,depth:3,branches:6,questions:80});
const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const need=(ok,message)=>{if(!ok)throw Error(message);};
const copy=value=>structuredClone(value);
export const isNestedFlowPlan=value=>object(value)&&value.schema===NESTED_PLAN_SCHEMA;
export const nestedPointer=path=>'/'+path.map(part=>String(part).replaceAll('~','~0').replaceAll('/','~1')).join('/');
export const nestedAttribution=()=>({citations:[],inferred:true,reason:''});
export const nestedAction=()=>({name:'',actor:'human',role:'',responsibility:'',inputs:[],outputs:[],attribution:nestedAttribution()});
export function nestedPhase(kind){
 const common={kind,attribution:nestedAttribution()};
 if(kind==='steps')return {...common,steps:[nestedAction()]};
 if(kind==='repeat')return {...common,steps:[nestedAction()],check:nestedAction(),repeat_if:'',finish_if:''};
 if(kind==='decision')return {...common,decision:nestedAction(),branches:[0,1].map(()=>({condition:'',attribution:nestedAttribution(),phases:[]}))};
 if(kind==='parallel')return {...common,dispatch:nestedAction(),branches:[0,1].map(()=>({phases:[]})),wait_mode:'all',join_attribution:nestedAttribution()};
 throw Error('当前不能添加这种阶段，原稿保留。');
}
export const nestedFlow=()=>({name:'',trigger:{kind:'manual',description:'',attribution:nestedAttribution()},phase_order:nestedAttribution(),phases:[],completion:nestedAction()});

function target(value,path){
 need(Array.isArray(path)&&path.length>0&&path.every(key=>(typeof key==='string'&&!['__proto__','constructor','prototype'].includes(key))||(Number.isSafeInteger(key)&&key>=0)),'修改位置无法核对，未改动原稿。');
 let current=value;
 for(const key of path){need((object(current)||Array.isArray(current))&&Object.hasOwn(current,key),'原结构已变化，请重新选择修改位置。');current=current[key];}
 return current;
}
export function nestedSet(value,path,next){
 const result=copy(value),parent=target(result,path.slice(0,-1));
 need(object(parent)||Array.isArray(parent),'原结构暂不能编辑，原稿保留。');
 const key=path.at(-1);need((typeof key==='string'&&!['__proto__','constructor','prototype'].includes(key))||(Number.isSafeInteger(key)&&key>=0),'修改位置无法核对。');
 parent[key]=copy(next);safeReviewJSON(result);return result;
}
export function nestedCapacity(value){
 const flows=Array.isArray(value?.business?.flows)?value.business.flows:[];
 return flows.map(flow=>{
  let phases=0,actions=object(flow?.completion)?1:0,depth=0;
  const pending=(Array.isArray(flow?.phases)?flow.phases:[]).map(phase=>({phase,parentDepth:0}));
  while(pending.length){const {phase,parentDepth}=pending.pop();if(!object(phase))continue;phases++;
   const level=parentDepth+(['decision','parallel','repeat'].includes(phase.kind)?1:0);depth=Math.max(depth,level);
   if(['steps','repeat'].includes(phase.kind))actions+=(Array.isArray(phase.steps)?phase.steps:[]).length+(phase.kind==='repeat'?1:0);
   else if(['decision','parallel'].includes(phase.kind)){actions++;for(const branch of Array.isArray(phase.branches)?phase.branches:[])for(const child of Array.isArray(branch?.phases)?branch.phases:[])pending.push({phase:child,parentDepth:level});}
  }
  return {phases,actions,depth};
 });
}
export function assertNestedCapacity(value){
 const flows=value?.business?.flows;
 need(Array.isArray(flows)&&flows.length<=NESTED_LIMITS.flows,'每项业务最多保留16个工作流程；原输入未裁剪。');
 need(!Array.isArray(value?.business?.questions)||value.business.questions.length<=NESTED_LIMITS.questions,'待核问题最多80项；原输入未裁剪。');
 for(const count of nestedCapacity(value))need(count.phases<=NESTED_LIMITS.phases&&count.actions<=NESTED_LIMITS.actions&&count.depth<=NESTED_LIMITS.depth,'本流程最多12个阶段、32个动作和3层控制结构；原输入未裁剪。');
 const visit=phases=>{for(const phase of Array.isArray(phases)?phases:[])if(Array.isArray(phase?.branches)){need(phase.branches.length<=NESTED_LIMITS.branches,'每个条件或并行阶段最多6个分支。');phase.branches.forEach(branch=>visit(branch?.phases));}};
 flows.forEach(flow=>visit(flow?.phases));return value;
}
export function nestedArrayEdit(value,path,{kind,index,item}){
 const result=copy(value),array=target(result,path);need(Array.isArray(array),'原列表暂不能编辑，原稿保留。');
 if(kind==='append')array.push(copy(item));
 else {need(Number.isSafeInteger(index)&&index>=0&&index<array.length,'所选条目已变化，未改动其他内容。');
  if(kind==='remove')array.splice(index,1);
  else if(kind==='up'||kind==='down'){const next=index+(kind==='up'?-1:1);need(next>=0&&next<array.length,'已到列表边界，未改变顺序。');[array[index],array[next]]=[array[next],array[index]];}
  else throw Error('当前结构操作无法核对。');
 }
 safeReviewJSON(result);return assertNestedCapacity(result);
}
export function nestedCitationUses(value){
 const uses=[];
 const add=(refs,path,label)=>{for(const [index,ref]of (Array.isArray(refs)?refs:[]).entries())if(typeof ref==='string')uses.push({span_ref:ref,path:nestedPointer([...path,index]),label});};
 const attr=(value,path,label)=>add(value?.citations,[...path,'citations'],label);
 const action=(value,path,label)=>attr(value?.attribution,[...path,'attribution'],label+(value?.name?' · '+value.name:''));
 const phases=(values,path)=>{for(const [index,phase]of (Array.isArray(values)?values:[]).entries()){
  const at=[...path,index];attr(phase?.attribution,[...at,'attribution'],'阶段依据');
  if(['steps','repeat'].includes(phase?.kind)){(Array.isArray(phase.steps)?phase.steps:[]).forEach((step,i)=>action(step,[...at,'steps',i],'步骤'));if(phase.kind==='repeat')action(phase.check,[...at,'check'],'循环核查');}
  else if(['decision','parallel'].includes(phase?.kind)){action(phase[phase.kind==='decision'?'decision':'dispatch'],[...at,phase.kind==='decision'?'decision':'dispatch'],phase.kind==='decision'?'条件核对':'并行组织');if(phase.kind==='parallel')attr(phase.join_attribution,[...at,'join_attribution'],'并行汇合');(Array.isArray(phase.branches)?phase.branches:[]).forEach((branch,i)=>{const bp=[...at,'branches',i];if(phase.kind==='decision')attr(branch?.attribution,[...bp,'attribution'],'分支条件');phases(branch?.phases,[...bp,'phases']);});}
 }};
 (Array.isArray(value?.business?.flows)?value.business.flows:[]).forEach((flow,i)=>{const path=['business','flows',i];attr(flow?.trigger?.attribution,[...path,'trigger','attribution'],'触发条件');attr(flow?.phase_order,[...path,'phase_order'],'阶段顺序');phases(flow?.phases,[...path,'phases']);action(flow?.completion,[...path,'completion'],'流程结束');});
 (Array.isArray(value?.business?.questions)?value.business.questions:[]).forEach((question,i)=>add(question?.citations,['business','questions',i,'citations'],'待核问题 '+(i+1)));
 return uses;
}
