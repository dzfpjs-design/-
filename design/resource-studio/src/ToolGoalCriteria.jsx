import React from 'react';
import {buildToolGoalCriteria,nativeGoalToolMetadata} from './native-goal-contract.mjs';
import {numericDisplay} from './typed-numbers-v132.mjs';

const operators={eq:'等于',gt:'大于',gte:'至少',lt:'小于',lte:'至多'};
const kinds={number:'数值',integer:'整数',string:'文本',boolean:'是否'};
const sourceLabels={planning_tool:'人工步骤可用的只读工具',workflow_step:'已确认流程能力'};
const editable=value=>typeof value==='string'||typeof value==='number'||typeof value==='boolean';

export default function ToolGoalCriteria({form,onChange,metadata,disabled=false}){
 const malformed=Object.hasOwn(form,'toolCriteria')&&!Array.isArray(form.toolCriteria);
 const rows=Array.isArray(form.toolCriteria)?form.toolCriteria:[];
 const {tools,error}=nativeGoalToolMetadata(metadata);
 const maximum=12-2-(form.targetField?1:0)-(form.queryCriterion?.enabled?1:0);
 let draftError='';try{buildToolGoalCriteria(form,metadata,undefined,{typedNumbers:true});}catch(e){draftError=e.message;}
 function change(index,patch){onChange({...form,toolCriteria:rows.map((row,i)=>i===index?{...row,...patch}:row)});}
 function remove(index){onChange({...form,toolCriteria:rows.filter((_,i)=>i!==index)});}
 function add(){onChange({...form,toolCriteria:[...rows,{id:'tool_'+crypto.randomUUID().replaceAll('-',''),capabilityId:'',kind:'capability_success',field:'',operator:'eq',expected:''}]});}
 return <details className="tool-goal-criteria media-form" open={rows.length>0||malformed||undefined}>
  <summary>工具完成条件（可选）{rows.length>0?' · 已设置'+rows.length+'项':''}</summary>
  <p>从当前业务已固定的工具和模型中选择。设置条件不会增加执行权限；验收仍需核对真实回执、原业务流程和最终人工确认。</p>
  {error&&<p role="alert">工具清单暂时无法核对：{error}。原输入保留，已设置的工具条件须重新核对后才能提交。</p>}
  {!error&&!tools.length&&<p>当前业务没有可选的工具完成条件，可以继续设置业务目标。</p>}
  {draftError&&<p role="alert">{draftError}</p>}
  {malformed?<button type="button" className="btn" disabled={disabled} onClick={()=>onChange({...form,toolCriteria:[]})}>明确移除无法恢复的工具条件</button>:<>
   {rows.map((row,index)=>{
    const valid=row!==null&&typeof row==='object'&&!Array.isArray(row);
    if(!valid)return <article className="media-session" key={'invalid_'+index}><h4>工具条件 {index+1}</h4><p role="alert">此项草稿无法读取，原记录已保留。请核对后明确移除。</p><button type="button" className="btn" disabled={disabled} onClick={()=>remove(index)}>移除此项工具条件</button></article>;
    const tool=tools.find(item=>item.id===row.capabilityId);
    const field=tool?.output_fields.find(item=>item.key===row.field);
    const numeric=['number','integer'].includes(field?.kind);
    const key=typeof row.id==='string'&&row.id?row.id:'unreadable_'+index;
    return <fieldset className="media-session" key={key} disabled={disabled} style={{minWidth:0}}>
     <legend>工具条件 {index+1}</legend>
     <label>使用哪个工具<select aria-label={'工具条件'+(index+1)+'的工具'} value={typeof row.capabilityId==='string'?row.capabilityId:''} onChange={e=>change(index,{capabilityId:e.target.value})}>
      <option value="">请选择本业务工具</option>
      {row.capabilityId&&!tool&&typeof row.capabilityId==='string'&&<option value={row.capabilityId}>原选工具暂不可用，请重新核对</option>}
      {tools.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}
     </select></label>
     {tool&&<p>登记版本：{tool.version}。使用范围：{tool.sources.map(source=>sourceLabels[source]).join('、')||'尚未声明'}。</p>}
     {tool?.step_names?.length>0&&<p>原业务步骤：{tool.step_names.join('、')}。</p>}
     <label>要求什么结果<select aria-label={'工具条件'+(index+1)+'的结果要求'} value={typeof row.kind==='string'?row.kind:''} onChange={e=>change(index,{kind:e.target.value})}>
      {!['capability_success','tool_value'].includes(row.kind)&&<option value={typeof row.kind==='string'?row.kind:''}>原条件类型无法识别，请重新选择</option>}
      <option value="capability_success">工具成功完成并核对真实结果</option><option value="tool_value">输出结果满足具体标准</option>
     </select></label>
     {row.kind==='tool_value'&&<>
      <label>核对哪个输出<select aria-label={'工具条件'+(index+1)+'的输出字段'} value={typeof row.field==='string'?row.field:''} onChange={e=>change(index,{field:e.target.value})}>
       <option value="">请选择登记输出字段</option>
       {row.field&&!field&&typeof row.field==='string'&&<option value={row.field}>原选输出暂不可用，请重新核对</option>}
       {(tool?.output_fields||[]).map(item=><option key={item.key} value={item.key}>{item.label||item.key}（{kinds[item.kind]}{item.unit?'，'+item.unit:''}）</option>)}
      </select></label>
      {tool&&!tool.output_fields.length&&<p>此工具没有可用于比较的单值输出；可改为核对工具成功完成，或明确移除此条件。</p>}
      {numeric&&!field.unit&&<p>单位未声明，保持工具原值；不推断或换算。</p>}
      <label>比较方式<select aria-label={'工具条件'+(index+1)+'的比较方式'} value={typeof row.operator==='string'?row.operator:''} onChange={e=>change(index,{operator:e.target.value})}>
       {!Object.hasOwn(operators,row.operator)&&<option value={typeof row.operator==='string'?row.operator:''}>原比较方式无法识别，请重新选择</option>}
       {!numeric&&row.operator&&row.operator!=='eq'&&Object.hasOwn(operators,row.operator)&&<option value={row.operator}>原比较方式需重新核对（{operators[row.operator]}）</option>}
       {Object.entries(operators).filter(([value])=>value==='eq'||numeric).map(([value,label])=><option key={value} value={value}>{label}</option>)}
      </select></label>
      {field?.kind==='boolean'?<label>期望结果<select aria-label={'工具条件'+(index+1)+'的期望结果'} value={editable(row.expected)?String(row.expected):''} onChange={e=>change(index,{expected:e.target.value==='true'?true:e.target.value==='false'?false:''})}>
       <option value="">请选择“是”或“否”</option>{editable(row.expected)&&!['','true','false'].includes(String(row.expected))&&<option value={String(row.expected)}>原期望结果需重新核对</option>}<option value="true">是</option><option value="false">否</option>
      </select></label>:<label>期望{numeric?'数值':'内容'}{field?.unit?'（'+field.unit+'）':''}<input aria-label={'工具条件'+(index+1)+'的期望结果'} inputMode={numeric?'decimal':undefined} value={typeof row.expected==='number'?numericDisplay(row.expected):editable(row.expected)?String(row.expected):''} onChange={e=>change(index,{expected:e.target.value})}/></label>}
     </>}
     <button type="button" className="btn" onClick={()=>remove(index)}>移除工具条件 {index+1}</button>
    </fieldset>;
   })}
   <button type="button" className="btn" disabled={disabled||!!error||!tools.length||rows.length>=maximum} onClick={add}>添加工具完成条件</button>
   <p>资料充分、字段标准、派生结果标准、工具条件和人工验收合计最多12项。{rows.length>=maximum?'已达到当前可添加的数量。':''}</p>
  </>}
 </details>;
}
