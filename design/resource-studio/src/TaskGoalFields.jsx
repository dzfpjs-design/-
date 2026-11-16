import React from 'react';
import {numericDisplay} from './typed-numbers-v132.mjs';

export default function TaskGoalFields({form,onChange,metadata,title,context='creation',disabled=false}){
 const selected=Array.isArray(form.fields)?form.fields:[],numeric=['number','integer'].includes(metadata.field_kinds[form.targetField]);
 const approval=context==='approval',fieldName=key=>metadata.fields[key]+(approval&&metadata.field_units?.[key]?'（'+metadata.field_units[key]+'）':''),aria=name=>approval?name.replace(/^发起任务/,'补证要求').replace(/^发起目标/,'补证要求'):name;
 return <section className="media-form" aria-label={approval?'审批草稿的补证要求':'发起任务时确定业务目标'}><fieldset disabled={disabled} style={{border:0,padding:0}}>
  <h3>{approval?'本次补证要求和完成条件':'业务目标和完成条件'}</h3>
  {!approval&&<label className="media-channel-choice"><input type="checkbox" aria-label="先发起任务让智能体理解查询需求" checked={!!form.queryFirst} onChange={e=>onChange({...form,queryFirst:e.target.checked,enabled:e.target.checked?false:form.manualGoalEnabled??true,manualGoalEnabled:e.target.checked?form.enabled:form.manualGoalEnabled,queryQuestion:form.queryQuestion??form.purpose??''})}/>先发起任务，让智能体理解查询需求</label>}
  {!approval&&form.queryFirst?<><label>你想查询什么<textarea aria-label="发起任务查询需求" maxLength={4000} value={form.queryQuestion??form.purpose??''} onChange={e=>onChange({...form,queryQuestion:e.target.value})} placeholder="直接说明范围、要查的内容和希望核对的结果"/></label><p>选择对象并发起后，说明会随原任务保存；在原任务核对资料范围与云发送内容，再获取查询建议。原手动目标输入保留，尚未确认正式目标。已配置流程仍按原规则执行。</p></>:<>
  {!approval&&<label className="media-channel-choice"><input type="checkbox" aria-label="发起时确认业务目标" checked={form.enabled} onChange={e=>onChange({...form,enabled:e.target.checked})}/>发起时确认业务目标</label>}
  {!form.enabled?<p className="warning">此次仅运行已确认流程，不包含正式目标验收。自动第一步执行后无法补改它的原目标约束；已有结果会保留。</p>:<>
   <label>这个任务要达到什么目标<textarea aria-label={aria('发起任务业务目标')} value={form.purpose} placeholder={title||'说明要解决的问题和预期结果'} onChange={e=>onChange({...form,purpose:e.target.value})}/></label>
   <p>需要核对哪些业务资料？{!metadata.entity_ids.length?'请先选择任务对象。':'字段来自所选对象的已确认业务配置。'}</p>
   {Object.entries(metadata.fields).map(([key,name])=><label key={key} className="media-channel-choice"><input type="checkbox" aria-label={(approval?'补证需要 ':'目标需要 ')+(approval?fieldName(key):name)} checked={selected.includes(key)} onChange={e=>onChange({...form,fields:e.target.checked?[...selected,key]:selected.filter(x=>x!==key),...(!e.target.checked&&form.targetField===key?{targetField:'',targetOperator:'eq',targetValue:''}:{})})}/>{fieldName(key)}</label>)}
   <label>目标采用什么资料<select aria-label={aria('发起目标资料来源')} value={form.source} onChange={e=>onChange({...form,source:e.target.value})}><option value="archive">原业务档案</option><option value="observations">设备或人工登记记录</option><option value="either">档案与登记记录</option></select></label>
   {form.source!=='archive'&&<>
    <label>登记记录要求<select aria-label={aria('发起目标登记要求')} value={form.confirmation} onChange={e=>onChange({...form,confirmation:e.target.value})}><option value="confirmed">已人工核查</option><option value="unverified_extraction">已提取，可尚未人工核查</option></select></label>
    <label>登记距核查时最多几小时<input aria-label={aria('发起目标资料有效小时数')} type="text" inputMode="numeric" value={form.hours} onChange={e=>onChange({...form,hours:e.target.value})}/></label><p>请填写 1 至 720 的整数小时；未完成的输入会原样保留，提交时核对。</p>
    <label className="media-channel-choice"><input aria-label={aria('发起目标允许按既有授权申请补采')} type="checkbox" checked={form.allowNew} onChange={e=>onChange({...form,allowNew:e.target.checked})}/>资料不足时允许按既有有效授权申请补采</label>
    <p>不会因此新增设备权限；接口未接通、授权不足或动作结果未知时会明确阻止。</p>
   </>}
   <label>还需满足具体字段标准吗<select aria-label={aria('发起目标字段标准')} value={form.targetField} onChange={e=>onChange({...form,targetField:e.target.value,targetOperator:'eq',targetValue:''})}><option value="">无需额外数值或内容标准</option>{selected.filter(key=>metadata.fields[key]).map(key=><option key={key} value={key}>{fieldName(key)}</option>)}</select></label>
   {form.targetField&&<><label>比较方式<select aria-label={aria('发起目标比较方式')} value={form.targetOperator} onChange={e=>onChange({...form,targetOperator:e.target.value})}><option value="eq">等于</option>{numeric&&<><option value="gt">大于</option><option value="gte">至少</option><option value="lt">小于</option><option value="lte">至多</option></>}</select></label><label>期望数值或内容<input aria-label={aria('发起目标期望值')} type="text" inputMode={numeric?'decimal':undefined} value={typeof form.targetValue==='number'?numericDisplay(form.targetValue):form.targetValue} onChange={e=>onChange({...form,targetValue:e.target.value})}/></label>{numeric&&<p>按原输入保存数值；可填写 1.0、-0.0 或 1e21，整数须在可准确保存的范围内。</p>}</>}
   <label>业务目标截止时间（北京时间，可选）<input aria-label={aria('发起目标截止时间')} type="datetime-local" value={form.deadline||''} onInput={e=>onChange({...form,deadline:e.currentTarget.value})} onChange={e=>onChange({...form,deadline:e.target.value})}/></label>
   <p>最近资料到期时自动核查；到目标截止时间后停止旧协调，原证据与已保存说明保留。无需设置时区。</p>
   <p>{approval?'本页只修改原任务审批草稿。先保存修订稿，再按原层级审批并由指定岗位明确开始；此前不补采、不运行目标协调。':'任务名称可作为默认目标说明。目标在首个模型或工具执行前保存；原资料、字段标准、流程步骤和最终人工验收均需满足。步骤和能力调用各最多12次；专业小模型默认本地运行，失败不自动转云。'}</p>
  </>}</>}
 </fieldset></section>;
}
