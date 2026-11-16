// Display/preview contract consistency only. Hashes and SIM labels do not grant
// source access, route authorization, Task creation, automation or device I/O.
// Actual registration, full JSON Schema validity and current authority remain
// the backend's independent gates. No source manifest is reconstructed here.
import {graphWireCanonical,graphWireDigest,graphWireParseCanonical} from './full-semantic-graph-wire.mjs';

export const HTTP_SOURCE_CONTRACT_KEY='http_mapped_source_contract_v177';
const ERROR='HTTP异常来源的冻结映射、原数值类型或当前预览范围无法完整核对；原稿保留，未授予来源或执行权限。';
const CONTRACT_KEYS=['schema','family','scope','source_id','source_hash','extractor_id','extractor_hash','environment_hash','configuration_hash','mapping','field_units','simulation','test_only','field_verified'];
const SCOPE_KEYS=['workspace_id','environment_id','instance_id','release_id','package_id','package_hash','entity_type'];
const FREEZE_KEYS=['schema','mapping','mapping_hash','source_id','source_manifest_hash','entity_schemas_hash','field_schemas','allowed_actions','freeze_hash'];
const CONFIG_KEYS=['schema','scope','links','supplier_contract','time_semantics'];
const UNITS=Object.freeze({temperature_c:'°C',humidity_percent:'%',wind_m_s:'m/s',pressure_hpa:'hPa',battery_percent:'%',pan_degrees:'°',tilt_degrees:'°',zoom:'倍'});
const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const own=(value,key)=>Object.prototype.hasOwnProperty.call(value,key);
const exact=(value,keys)=>object(value)&&Object.keys(value).length===keys.length&&keys.every(key=>own(value,key));
const uuid=value=>typeof value==='string'&&/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(value);
const sha=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
const trim=value=>value.replace(/^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+|[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+$/gu,'');
const text=(value,max)=>typeof value==='string'&&[...value].length>0&&[...value].length<=max&&trim(value)===value;
const same=(left,right)=>graphWireCanonical(left)===graphWireCanonical(right);
function need(ok){if(!ok){const error=Error(ERROR);error.graphRouteSafe=true;throw error;}}
function dataPins(value,keys){
 need(object(value)&&[Object.prototype,null].includes(Object.getPrototypeOf(value))
  &&Object.getOwnPropertySymbols(value).length===0);
 return Object.fromEntries(keys.map(key=>{
  const descriptor=Object.getOwnPropertyDescriptor(value,key);
  need(descriptor&&descriptor.enumerable&&own(descriptor,'value'));return [key,descriptor.value];
 }));
}
function scopeShape(value){
 need(exact(value,SCOPE_KEYS));
 for(const key of SCOPE_KEYS.filter(key=>!['package_hash','entity_type'].includes(key)))need(uuid(value[key]));
 need(sha(value.package_hash)&&text(value.entity_type,100));
}
function noNullText(value){
 if(typeof value==='string'){need(!value.includes('\u0000'));return;}
 if(Array.isArray(value)){value.forEach(noNullText);return;}
 if(object(value))for(const [key,item]of Object.entries(value)){need(!key.includes('\u0000'));noNullText(item);}
}
function noReferences(value){
 if(Array.isArray(value)){value.forEach(noReferences);return;}
 if(object(value)){need(!['$ref','$dynamicRef','$recursiveRef'].some(key=>own(value,key)));Object.values(value).forEach(noReferences);}
}

/** Return a detached, immutable typed snapshot; never an authorization receipt.
 * Caller still fences its live account/scope after this async check finishes.
 */
export async function checkHTTPGraphSourceContract(contract,options){
 // Use the real wire codec before reading properties. It rejects accessors,
 // unsafe/non-finite/unproven numbers and preserves server-certified 1 vs 1.0.
 const value=graphWireParseCanonical(graphWireCanonical(contract));
 noNullText(value);
 need(exact(value,CONTRACT_KEYS)&&value.schema==='signal-graph-http-source-contract-v177'
  &&value.family==='http-mapped-acquisition-v177'
  &&value.simulation===true&&value.test_only===true&&value.field_verified===false);
 scopeShape(value.scope);
 need(uuid(value.source_id)&&uuid(value.extractor_id));
 for(const key of ['source_hash','extractor_hash','environment_hash','configuration_hash'])need(sha(value[key]));

 // Capture only own data pins, synchronously before any digest await. Neither
 // inherited properties nor a getter may supply an allegedly current scope.
 const context=dataPins(options,['scope','command','binding']);
 const scope=dataPins(context.scope,['workspace_id','instance_id']);
 const command=dataPins(context.command,['release_id','source_id','extractor_id']);
 const binding=dataPins(context.binding,['release_id','configuration_hash','source','extractor']);
 const source=dataPins(binding.source,['id','content_hash']),extractor=dataPins(binding.extractor,['id','content_hash']);
 need(uuid(scope.workspace_id)&&uuid(scope.instance_id)
  &&uuid(command.release_id)&&uuid(command.source_id)&&uuid(command.extractor_id)
  &&uuid(binding.release_id)&&uuid(source.id)&&uuid(extractor.id)
  &&sha(source.content_hash)&&sha(extractor.content_hash)&&sha(binding.configuration_hash));
 need(value.scope.workspace_id===scope.workspace_id&&value.scope.instance_id===scope.instance_id
  &&value.scope.release_id===command.release_id&&value.scope.release_id===binding.release_id
  &&value.source_id===command.source_id&&value.source_id===source.id
  &&value.extractor_id===command.extractor_id&&value.extractor_id===extractor.id
  &&value.source_hash===source.content_hash&&value.extractor_hash===extractor.content_hash
  &&value.configuration_hash===binding.configuration_hash);

 const frozen=value.mapping;
 need(exact(frozen,FREEZE_KEYS)&&frozen.schema==='http-observation-mapping-freeze-v177'&&uuid(frozen.source_id));
 for(const key of ['mapping_hash','source_manifest_hash','entity_schemas_hash','freeze_hash'])need(sha(frozen[key]));
 const mapping=frozen.mapping;
 need(exact(mapping,CONFIG_KEYS)&&mapping.schema==='http-observation-mapping-v177'
  &&mapping.supplier_contract==='sim-acquisition-receipt-v176'&&mapping.time_semantics==='simulator_sample_time');
 scopeShape(mapping.scope);
 need(same(value.scope,mapping.scope)&&value.source_id===frozen.source_id&&value.source_hash===frozen.source_manifest_hash);
 need(object(mapping.links)&&Object.keys(mapping.links).length>=1&&Object.keys(mapping.links).length<=8);
 const links=Object.entries(mapping.links),targets=[];
 for(const [raw,link]of links){
  need(own(UNITS,raw)&&exact(link,['field_key','unit'])&&text(link.field_key,100)
   &&text(link.unit,40)&&link.unit===UNITS[raw]);targets.push(link.field_key);
 }
 need(new Set(targets).size===targets.length&&exact(frozen.field_schemas,targets));
 for(const [,link]of links){
  const schema=frozen.field_schemas[link.field_key];
  need(object(schema)&&['number','integer'].includes(schema.type)&&schema.unit===link.unit);noReferences(schema);
 }
 need(Array.isArray(frozen.allowed_actions)&&frozen.allowed_actions.length>0
  &&new Set(frozen.allowed_actions).size===frozen.allowed_actions.length
  &&frozen.allowed_actions.every(action=>['read_existing','observe'].includes(action)));
 need(same(value.field_units,Object.fromEntries(links.map(([,link])=>[link.field_key,link.unit]))));
 need(await graphWireDigest(mapping)===frozen.mapping_hash
  &&await graphWireDigest(frozen,{omit:'freeze_hash'})===frozen.freeze_hash);
 return value;
}
