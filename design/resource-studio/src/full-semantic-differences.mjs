// Read-only positional comparison of two already verified manual result values. Never maps names or edits facts.
import {evidenceManualValidationView} from './report-evidence-manual-v5.mjs';
const fields={schema:'候选格式',source_hash:'资料内容版本',catalog_hash:'出处目录版本',domain:'领域',global_claims:'跨业务职责与规则',objects:'对象',relationships:'对象关系',businesses:'业务',questions:'待核事项',kind:'类型',statement:'内容',span_refs:'引用出处',status:'依据状态',explanation:'推断与待核说明',name:'名称',operation:'保存方式',original_name:'原名称',trigger:'何时启动',output:'产出',steps:'步骤',citations:'引用出处',span_ref:'出处位置',inference_reason:'推断与待核说明',participants:'参与方',interactions:'互动',conditions:'条件',coordination:'协同',purpose:'目的',agent:'负责方',role:'人工角色',branch_condition:'完成后的分支条件',next_index:'完成后去向',else_index:'条件不满足时去向',responsibility:'职责',evidence:'依据',from_participant_index:'发起参与方',to_participant_index:'接收参与方',condition:'互动条件',step_indices:'涉及步骤',start_rule:'开始规则',wait_rule:'等待规则',join_rule:'汇合规则',fields:'字段',label:'字段名称',required:'是否必填',unit:'单位',description:'说明',source_type:'起点对象',target_type:'终点对象',cardinality:'多重性'};
const values={explicit:'原文明示，仍待核',inferred:'推断，仍待核',unknown:'尚未明确',precondition:'开始前提',trigger:'触发条件',stop:'停止条件',exception:'例外情况',sequence:'顺序',branch:'分支',parallel:'并行',unspecified:'尚未明确',many:'多对多或一对多',one_per_source:'每个起点最多一个终点',one_per_target:'每个终点最多一个起点',upsert:'保存完整业务'};
const order=Object.keys(fields),absent=Symbol('absent');
const equal=(a,b)=>a===absent||b===absent?a===b:a===null||b===null||typeof a!=='object'||typeof b!=='object'?Object.is(a,b):Array.isArray(a)||Array.isArray(b)?Array.isArray(a)&&Array.isArray(b)&&a.length===b.length&&a.every((v,i)=>equal(v,b[i])):Object.keys(a).length===Object.keys(b).length&&Object.keys(a).every(k=>Object.hasOwn(b,k)&&equal(a[k],b[k]));
function span(ref){const m=/^([wo]):p(\d+)\/s(\d+)$/.exec(ref);return m?(m[1]==='w'?'Word正文位置':'PDF保存文字位置')+m[2]+' · 片段'+m[3]:'出处待核';}
function display(value,key,context){
 if(value===absent)return {present:false,type:'absent',text:'此位置没有此项'};
 const type=value===null?'null':Array.isArray(value)?'array':typeof value;let text;
 if(value===null)text=['next_index','else_index'].includes(key)?'本路结束（空值）':'未填写（空值）';
 else if(Array.isArray(value))text='空列表';
 else if(typeof value==='boolean')text=value?'是':'否';
 else if(typeof value==='number'){
  const collection=['next_index','else_index','step_indices'].includes(key)?context?.steps:['from_participant_index','to_participant_index'].includes(key)?context?.participants:null;
  text=collection?(collection===context.steps?'第'+(value+1)+'步':'第'+(value+1)+'位参与方')+'：'+(collection[value]?.name??collection[value]?.agent??'引用待核'):String(value);
 }else if(['source_hash','catalog_hash','schema'].includes(key))text='已绑定的'+fields[key]+'（内容见原、新版本）';
 else if(key==='span_ref'||key==='span_refs')text=span(value);
 else text=value===''?'空文本':['status','kind','cardinality','operation'].includes(key)?values[value]??value:value;
 return {present:true,type,value,text};
}
export function fullSemanticDifferences(previous,next){
 // Reuse the exact closed result shape. Supplying a manual wrapper or note is rejected rather than shared.
 for(const result of [previous,next])evidenceManualValidationView({schema:'report-evidence-manual-review-v5',result,note:''});
 const groups=[],businesses=[];
 function group(label,a,b,contextA,contextB){
  const changes=[];
  function walk(left,right,labelParts,key){
   if(equal(left,right))return;
   const array=Array.isArray(left)||Array.isArray(right),object=!array&&((left!==absent&&left!==null&&typeof left==='object')||(right!==absent&&right!==null&&typeof right==='object'));
   if(array){const l=left===absent?[]:left,r=right===absent?[]:right;
    if(l.length||r.length){for(let i=0;i<Math.max(l.length,r.length);i++)walk(i<l.length?l[i]:absent,i<r.length?r[i]:absent,[...labelParts,'第'+(i+1)+'项'],key);return;}
   }else if(object){const keys=[...new Set([...Object.keys(left===absent?{}:left),...Object.keys(right===absent?{}:right)])].sort((x,y)=>order.indexOf(x)-order.indexOf(y));for(const k of keys)walk(left!==absent&&Object.hasOwn(left,k)?left[k]:absent,right!==absent&&Object.hasOwn(right,k)?right[k]:absent,[...labelParts,fields[k]],k);return;}
   changes.push({label:labelParts.join(' · '),before:display(left,key,contextA),after:display(right,key,contextB)});
  }
  walk(a,b,[],null);if(changes.length)groups.push({label,changes});
 }
 for(const key of ['schema','source_hash','catalog_hash','domain','global_claims'])group(fields[key],previous[key],next[key]);
 for(let i=0;i<Math.max(previous.businesses.length,next.businesses.length);i++){
  const a=previous.businesses[i]??absent,b=next.businesses[i]??absent,start=groups.length,oldName=a===absent?'无此位置':a.name,newName=b===absent?'无此位置':b.name;
  group('业务位置'+(i+1)+' · 原：'+oldName+'；新：'+newName,a,b,a===absent?null:a,b===absent?null:b);
  if(groups.length>start)businesses.push({position:i+1,previous_name:a===absent?null:a.name,current_name:b===absent?null:b.name});
 }
 for(const key of ['objects','relationships','questions'])group(fields[key],previous[key],next[key]);
 return {count:groups.reduce((n,g)=>n+g.changes.length,0),businesses,groups};
}
