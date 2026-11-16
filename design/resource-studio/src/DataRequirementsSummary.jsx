import React from 'react';

const hash=/^[a-f0-9]{64}$/;
const dependencies='businesses,entity_schemas,object_types,resources,sensor_sources,materials,automation,equipment,general_issues';
const roles={business:'业务智能体',perception:'多模感知智能体',data:'数据管理智能体'};
const inputNames={records:'业务对象记录',attributes:'业务对象字段',field:'要处理的业务字段',operation:'统计方式',evidence_ids:'原始资料'};
const sources={task_audio_ref:'本任务选择的原音频与固定设置',task_identifier:'当前任务',constant:'固定参数',object_records:'本任务选择的对象记录',object_attributes:'本任务唯一对象的字段',object_ids:'本任务选择的业务对象',observation_records:'已保存的观测记录',task_context:'当前任务保存的上下文',request_key:'本次请求的固定编号',original_evidence_ids:'本任务引用的原件',task_image_ids:'任务中明确选中的图片',task_scene_refs:'任务中选定的影像与分析范围',task_comparison_refs:'任务中选定的对比资料与固定设置',perception_frame_ids:'关联视频线索的保存画面',previous_output:'前序步骤执行后保存的结果'};
const record=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const text=value=>typeof value==='string'&&value.trim().length>0;
const keys=(value,list)=>record(value)&&Object.keys(value).sort().join(',')===list.split(',').sort().join(',');
function jsonSafe(value,seen=new Set(),depth=0){
 if(depth>100)return false;
 if(value===null||typeof value==='boolean')return true;
 if(typeof value==='number')return Number.isFinite(value);
 if(typeof value==='string')return !value.includes('\0');
 if(!record(value)&&!Array.isArray(value)||seen.has(value))return false;
 seen.add(value);const ok=Object.values(value).every(item=>jsonSafe(item,seen,depth+1));seen.delete(value);return ok;
}
function canonical(value){
 if(Array.isArray(value))return '['+value.map(canonical).join(',')+']';
 if(record(value))return '{'+Object.keys(value).sort().map(key=>JSON.stringify(key)+':'+canonical(value[key])).join(',')+'}';
 return JSON.stringify(value);
}
function validOutput(output){
 if(!keys(output,'step_id,step_name,agent_role,capability_id,capability_hash,release_version,output_schema,output_schema_hash,input_bindings,fields,storage,status')||!['step_id','step_name','capability_id'].every(key=>text(output[key]))||!Object.hasOwn(roles,output.agent_role)||!hash.test(output.capability_hash)||!hash.test(output.output_schema_hash)||typeof output.release_version!=='string'||!/^\d+\.\d+\.\d+$/.test(output.release_version)||!record(output.output_schema)||!record(output.input_bindings)||!Array.isArray(output.fields)||output.storage!=='existing_evidence_and_receipts'||output.status!=='candidate')return false;
 const properties=output.output_schema.properties??{};
 if(!record(properties)||output.output_schema.required!==undefined&&(!Array.isArray(output.output_schema.required)||output.output_schema.required.some(name=>typeof name!=='string')))return false;
 if(Object.values(output.input_bindings).some(binding=>!record(binding)||!text(binding.source)))return false;
 return output.fields.length===Object.keys(properties).length&&new Set(output.fields.map(field=>field?.name)).size===output.fields.length&&output.fields.every(field=>keys(field,'name,schema')&&typeof field.name==='string'&&(record(field.schema)||typeof field.schema==='boolean')&&Object.hasOwn(properties,field.name)&&canonical(field.schema)===canonical(properties[field.name]));
}
// Display guards only. The server binds and cold-checks the generated contract.
function validRequirements(value){
 if(!keys(value,'schema,origin,dependency_hash,dependency_hashes,confirmed_object_catalog,businesses,evidence_policy,summary,requirements_hash')||!jsonSafe(value)||value.schema!=='instance-data-requirements-v1'||value.origin!=='system_design'||!hash.test(value.dependency_hash)||!hash.test(value.requirements_hash)||!keys(value.dependency_hashes,dependencies)||Object.values(value.dependency_hashes).some(item=>!hash.test(item))||!Array.isArray(value.confirmed_object_catalog)||!Array.isArray(value.businesses)||!text(value.summary))return false;
 const policy=value.evidence_policy;
 if(!keys(policy,'status,source_timestamp,unit,retention_authorized,deletion_authorized')||policy.status!=='candidate'||policy.source_timestamp!=='unknown_until_actual_output'||policy.unit!=='unknown_until_actual_output'||policy.retention_authorized!==false||policy.deletion_authorized!==false)return false;
 return new Set(value.businesses.map(b=>b?.id)).size===value.businesses.length&&value.businesses.every(b=>keys(b,'id,name,status,issues,outputs,summary')&&text(b.id)&&text(b.name)&&['derived','blocked'].includes(b.status)&&Array.isArray(b.issues)&&b.issues.every(text)&&Array.isArray(b.outputs)&&b.outputs.every(validOutput)&&new Set(b.outputs.map(output=>output.step_id)).size===b.outputs.length&&text(b.summary)&&(b.status==='blocked'?b.outputs.length===0&&b.issues.length>0:b.issues.length===0));
}
function fieldName(field){return record(field.schema)&&text(field.schema.title)?field.schema.title:field.name;}
function inputSource(binding,business,output){
 if(binding.source==='previous_output_object'){
  if(!keys(binding,'source,step_id')||!text(binding.step_id))return '此前步骤完整结果的绑定无法核对，原绑定保留待核对';
  const index=business.outputs.findIndex(item=>item.step_id===output.step_id),previous=business.outputs.slice(0,Math.max(0,index)).find(item=>item.step_id===binding.step_id);
  return '此前步骤保存的完整结果 · '+(previous?previous.step_name:'前序步骤待核对')+'（完整结构化输出，不是单个字段）';
 }
 if(binding.source!=='previous_output')return sources[binding.source]||'尚不能识别的输入来源，原绑定保留待核对';
 const previous=business.outputs.find(output=>output.step_id===binding.step_id),field=previous?.fields.find(item=>item.name===binding.field);
 return sources.previous_output+' · '+(previous?previous.step_name:'前序步骤待核对')+(field?' · '+fieldName(field):' · 输出字段待核对');
}
function fieldRequirement(field,output){
 const required=output.output_schema.required;
 const necessity=Array.isArray(required)?required.includes(field.name)?'必填（能力契约声明）':'非必填（能力契约声明）':'必填性未明确';
 const types=record(field.schema)?[].concat(field.schema.type||[]):[];
 const unit=record(field.schema)&&text(field.schema.unit)?'契约声明单位：'+field.schema.unit:types.some(type=>type==='number'||type==='integer')?'单位未明确':'';
 return necessity+(unit?'；'+unit:'')+(record(field.schema)&&field.schema.$ref?'；字段细节引用另一定义，需按完整能力契约核对':'');
}

export default function DataRequirementsSummary({snapshot,historical=false}){
 if(!record(snapshot)||!Object.hasOwn(snapshot,'data_requirements'))return null;
 const value=snapshot.data_requirements;
 if(!validRequirements(value))return <section aria-label="数据保存需求无法核对"><h3>数据保存需求</h3><p className="error" role="alert">此版本的数据保存需求格式无法核对，不能据此确认保存字段。原业务配置与生成记录保留，请重新检查当前配置；已保存版本仍按原记录核对。</p></section>;
 return <section aria-label={historical?'此版本的数据保存需求':'此次生成的数据保存需求'}>
  <h3>{historical?'此版本的数据保存需求':'此次生成的数据保存需求'}</h3>
  <p>{historical?'下列需求固定于此版本生成时的业务和能力版本，不代表当前能力状态。':'下列需求来自本次检查的业务与固定能力版本；修改业务或能力后需重新检查。'}</p>
  <p>系统根据已确认业务、接入资源和能力版本安排数据保存，无需单独配置数据库。此处展示保存方案；实际结果仍按来源核实，执行、外发和删除继续遵守原授权。</p>
  <p>沿用已确认的 {value.confirmed_object_catalog.length} 项对象类型。</p>
  {!value.businesses.length&&<p>当前没有可列出的业务数据保存需求。</p>}
  {value.businesses.map(business=><article className="media-form" key={business.id}>
   <h4>{business.name} · {business.status==='blocked'?'需先补齐执行条件':'已形成候选保存需求'}</h4>
   {business.issues.length>0&&<ul>{business.issues.map((issue,index)=><li key={index}>{issue}</li>)}</ul>}
   <details><summary>查看生成说明</summary><p>{business.summary}</p></details>
   {business.outputs.map(output=><div key={output.step_id}>
    <h5>{output.step_name} · {roles[output.agent_role]} · 能力版本 {output.release_version}</h5>
    <p>以下是此步骤执行后拟保存的候选结果，尚未产生或核实；采集时间与实际单位待输出后核对。</p>
    <strong>输入来源</strong>
    {Object.keys(output.input_bindings).length?<ul>{Object.entries(output.input_bindings).map(([name,binding],index)=><li key={name}>{inputNames[name]||('输入项 '+(index+1))}：{inputSource(binding,business,output)}</li>)}</ul>:<p>此输出契约未列出输入来源绑定；固定参数与其他输入仍需按步骤能力核对。</p>}
    <strong>拟保存字段</strong>
    {output.fields.length?<ul>{output.fields.map(field=><li key={field.name}><strong>{fieldName(field)}</strong>：{fieldRequirement(field,output)}</li>)}</ul>:<p>此契约没有直接列出字段，需按完整输出契约核对；不能据此认定没有输出。</p>}
    <p>保存位置：沿用现有证据与处理回执；不代表已写入实际结果。</p>
   </div>)}
  </article>)}
 </section>;
}
