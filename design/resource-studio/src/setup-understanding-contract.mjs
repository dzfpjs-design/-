// New requests use v5; recovery always retains the original request contract.
import {understandingVersion} from './setup-object-proposals.mjs';
export function newUnderstandingRequest({revision,model_id,material_ids,instruction,mode='understand'},request_id){
 if(!['understand','edit'].includes(mode))throw Error('当前修改方式尚未支持，未创建云请求。');
 return {revision,model_id,material_ids:[...material_ids],instruction,contract_version:mode==='edit'?6:5,request_id};
}
const hash=/^[0-9a-f]{64}$/i;
const fail=()=>{throw Error('完整资料发送范围或容量摘要无法核对；暂不授权发送，资料和已保存的人工核对保留。');};
export function verifyUnderstandingPreview(preview,body){
 if(preview?.edit_mode===true&&body.contract_version!==6)fail();
 if([1,2].includes(body.contract_version))return preview;
 if(![3,4,5,6].includes(body.contract_version))fail();
 const version=body.contract_version,policy=preview?.generation_contract?.input_policy;
 if(version>=4&&!verifiedFullReportPolicy(preview))fail();
 const summary=preview?.input_summary,contract=preview?.projection_contract,generation=preview?.generation_contract,snapshot=preview?.source_snapshot;
 if(preview?.contract_version!==version||preview?.request_id!==body.request_id||preview.revision!==body.revision||preview.model_id!==body.model_id||!summary||contract?.version!=='setup-cloud-source-v3'||contract.single_batch!==true||JSON.stringify(contract.block_fields)!==JSON.stringify(['material_id','block_id','text','location'])||JSON.stringify(contract.location_fields)!==JSON.stringify(['page','body_index'])||generation?.version!==(version===6?'setup-edits-v6':`setup-grounded-v${version}`)||typeof generation.task!=='string'||!generation.task||generation.input_policy?.single_batch!==true||(version===3&&(generation.input_policy.max_input_bytes!==50000||generation.input_policy.transport_timeout_seconds!==60))||snapshot?.version!=='setup-source-snapshot-v1'||!Array.isArray(snapshot.materials)||!Array.isArray(preview.materials)||!Array.isArray(preview.chunks)||preview.chunks.length!==1||!Array.isArray(preview.chunks[0]))fail();
 if(version===6&&(preview.edit_mode!==true||!hash.test(preview.edit_scope_hash)||Object.hasOwn(preview,'edit_handles'))||version!==6&&preview.edit_mode===true)fail();
 const blocks=preview.chunks[0],materials=new Set(preview.materials.map(material=>material.id)),members=new Set(),snapshotIDs=new Set(),ordered=[];
 if(materials.size!==body.material_ids.length||body.material_ids.some(id=>!materials.has(id))||materials.size!==preview.materials.length||snapshot.materials.length!==materials.size||summary.material_count!==materials.size||summary.block_count!==blocks.length||summary.batch_count!==1||summary.full_text_single_batch!==true||summary.max_input_bytes!==policy.max_input_bytes)fail();
 for(const key of ['source_text_bytes','source_snapshot_bytes','projection_bytes','prepared_messages_bytes','schema_bytes','max_tokens','token_bound'])if(!Number.isInteger(summary[key])||summary[key]<0)fail();
 if(summary.prepared_messages_bytes>summary.max_input_bytes)fail();
 for(const key of ['source_snapshot_hash','projection_contract_hash','projection_hash','generation_contract_hash','output_schema_hash','first_dispatch_messages_hash','preview_hash'])if(!hash.test(preview[key]))fail();
 if(generation.projection_contract_hash!==preview.projection_contract_hash)fail();
 for(const material of snapshot.materials){
  if(!material||!materials.has(material.id)||snapshotIDs.has(material.id)||!Array.isArray(material.blocks))fail();snapshotIDs.add(material.id);
  for(const block of material.blocks){if(!block)fail();const key=material.id+':'+block.id;if(typeof block.id!=='string'||members.has(key))fail();members.add(key);ordered.push({key,block});}
 }
 const projected=new Set();
 for(const [index,block] of blocks.entries()){
  if(!block)fail();const keys=['material_id','block_id','text','location'],key=block.material_id+':'+block.block_id;
  if(Object.keys(block).length!==4||!keys.every(name=>Object.hasOwn(block,name))||!materials.has(block.material_id)||typeof block.block_id!=='string'||typeof block.text!=='string'||!block.location||Array.isArray(block.location)||Object.entries(block.location).some(([name,value])=>!['page','body_index'].includes(name)||!Number.isInteger(value)||value<0)||projected.has(key)||!members.has(key))fail();
  if(version>=4){
   const source=ordered[index];
   if(source?.key!==key||typeof source.block.text!=='string'||!source.block.location||Boolean(source.block.text.trim())!==Boolean(block.text.trim()))fail();
   for(const name of ['page','body_index'])if(Object.hasOwn(source.block.location,name)!==Object.hasOwn(block.location,name)||source.block.location[name]!==block.location[name])fail();
  }
  projected.add(key);
 }
 if(projected.size!==members.size)fail();
 return preview;
}

function verifiedFullReportPolicy(preview){
 const options=preview?.generation_contract?.dispatch_options,policy=preview?.generation_contract?.input_policy;
 if(!options||options.model!==preview.model||!Number.isInteger(options.max_tokens)||options.max_tokens<128||options.max_tokens>8192)return false;
 const long=(preview.provider==='qwen'&&['qwen-plus','qwen-plus-2025-12-01'].includes(preview.model))||(preview.provider==='deepseek'&&['deepseek-flash','deepseek-v4-pro'].includes(preview.model)&&options.thinking?.type==='disabled');
 if(long&&preview.provider==='qwen'&&options.enable_thinking!==false)return false;
 const expected={single_batch:true,version:long?'cloud-full-report-2026-10-14':'cloud-bounded-text-v1',max_input_bytes:long?900000:50000,transport_timeout_seconds:long?180:60,max_output_tokens:8192,max_images:0,context_token_bound:long?1000000:null,full_report:true,long_context_verified:long};
 return !!policy&&Object.keys(policy).length===Object.keys(expected).length&&Object.entries(expected).every(([key,value])=>policy[key]===value)&&preview.input_summary?.max_tokens===options.max_tokens&&preview.input_summary?.token_bound===preview.input_summary?.prepared_messages_bytes+options.max_tokens+1024;
}

export function sourcePosition(location){
 const parts=[];if(Number.isInteger(location?.page))parts.push('第'+location.page+'页');if(Number.isInteger(location?.body_index))parts.push('正文位置 '+location.body_index);
 return parts.join(' · ')||'未记录页码或正文位置';
}
export function understandingRecovery(record){
 const version=understandingVersion(record);
 if(version===null)return {legacy:false,canRetry:false,requiresNewPreview:false,reason:'此记录版本或修改方式无法核对，只能查看，不执行重试或修改。'};
 if([1,2].includes(version))return {legacy:true,canRetry:record?.status==='failed',requiresNewPreview:false,reason:''};
 const mode=record.recovery?.mode;
 return {legacy:false,canRetry:record.status==='failed'&&mode==='local_only',requiresNewPreview:record.status==='failed'&&mode==='new_preview_required',reason:typeof record.recovery?.reason==='string'?record.recovery.reason:'原调用恢复方式尚未核对，请刷新理解记录；不自动重发。'};
}

export const localUnderstandingProgress=record=>[3,4,5,6].includes(understandingVersion(record))&&['queued','running'].includes(record.status)&&record.recovery?.mode==='local_only';
