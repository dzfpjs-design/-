import {actions,defaults,normalize,modes} from './automation-schema.js';
import {stableStringify} from './content-signature.js';
import {equipmentModes,equipmentTypes} from './equipment-schema.js';
const object=value=>!!value&&typeof value==='object'&&!Array.isArray(value);
function invalid(){const error=Error('已保存的自动化规则格式无法核对，原业务、规则和本页输入保留；请重新读取配置后处理。');error.code='AUTOMATION_CONFIG_INVALID';throw error;}
export function checkAutomationConfiguration(value){
 if(value===undefined)return;
 if(!object(value)||!object(value.policies)||value.version!==undefined&&value.version!==1)invalid();
 if(value.equipment!==undefined&&(!object(value.equipment)||value.equipment.types!==undefined&&!object(value.equipment.types)))invalid();
 const numeric=value=>typeof value==='number'?Number.isFinite(value):typeof value==='string'&&(value===''||Number.isFinite(Number(value)));
 const equipmentRule=rule=>{
  if(!object(rule)||rule.caps!==undefined&&(!Array.isArray(rule.caps)||rule.caps.some(cap=>typeof cap!=='string'))||rule.mode!==undefined&&!equipmentModes.includes(rule.mode))invalid();
  for(const field of ['readScope','scope','readRole','role','timezone','start','end'])if(rule[field]!==undefined&&typeof rule[field]!=='string')invalid();
  for(const field of ['read','readScopeEnabled','scopeEnabled','refreshOverride','limitEnabled','cooldownEnabled','durationEnabled','receipt','cancel'])if(rule[field]!==undefined&&typeof rule[field]!=='boolean')invalid();
  for(const field of ['interval','limit','cooldown','duration'])if(rule[field]!==undefined&&!numeric(rule[field]))invalid();
 };
 for(const rule of Object.values(value.equipment?.types||{}))equipmentRule(rule);
 if(value.equipment?.devices!==undefined&&!object(value.equipment.devices))invalid();
 for(const device of Object.values(value.equipment?.devices||{})){
  if(!object(device)||device.custom!==undefined&&typeof device.custom!=='boolean'||device.type!==undefined&&!equipmentTypes.some(type=>type.id===device.type))invalid();
  if(device.rule!==undefined)equipmentRule(device.rule);
 }
 for(const policy of Object.values(value.policies)){
  if(!object(policy)||policy.rules!==undefined&&!object(policy.rules)||policy.triggers!==undefined&&(!Array.isArray(policy.triggers)||policy.triggers.some(trigger=>!['定时运行','新观测到达','异常事件','人工发起'].includes(trigger))))invalid();
  for(const field of ['time','timezone'])if(policy[field]!==undefined&&typeof policy[field]!=='string')invalid();
  if(policy.budgetEnabled!==undefined&&typeof policy.budgetEnabled!=='boolean')invalid();
  if(policy.budget!==undefined&&!numeric(policy.budget))invalid();
  for(const [id,rule]of Object.entries(policy.rules||{})){
   if(!object(rule))invalid();
   if(actions.some(action=>action.id===id)){
    if(rule.mode!==undefined&&!modes.includes(rule.mode)||rule.resources!==undefined&&(!Array.isArray(rule.resources)||rule.resources.some(resource=>typeof resource!=='string')))invalid();
    for(const field of ['role','scope','cap'])if(rule[field]!==undefined&&typeof rule[field]!=='string')invalid();
    for(const field of ['limitsEnabled','receipt','cancel','idempotent'])if(rule[field]!==undefined&&typeof rule[field]!=='boolean')invalid();
    for(const field of ['limit','cooldown'])if(rule[field]!==undefined&&!numeric(rule[field]))invalid();
   }
  }
 }
}
export function automationConfigurationIssue(value){try{checkAutomationConfiguration(value);return '';}catch(error){return error.message;}}
export function runtimeAutomationDisplay(value,names,aliases={}){return automationConfigurationIssue(value)?defaults(names,true):normalize(structuredClone(value),names,aliases,true);}
function policyAt(value,name,aliases){const seen=new Set();while(name&&!seen.has(name)){seen.add(name);if(Object.hasOwn(value?.policies||{},name))return value.policies[name];name=aliases?.[name];}return undefined;}
export function initializeBusinessPolicies(value,newNames,aliases={}){
 checkAutomationConfiguration(value);const missing=newNames.filter(name=>policyAt(value,name,aliases)===undefined);if(!missing.length)return value;
 return {...value,version:value?.version??1,policies:{...value?.policies,...defaults(missing,true).policies}};
}
// Keep untouched saved fields and unknown policy extensions. Only missing or explicitly edited visible fields are materialized.
function mergeVisible(raw,visible,baseline,materialize=false){
 if(!object(visible))return visible;
 const out={...(object(raw)?raw:{})};
 for(const [key,value]of Object.entries(visible)){
  if(!materialize&&stableStringify(value)===stableStringify(baseline?.[key]))continue;
  if(object(value))out[key]=mergeVisible(raw?.[key],value,baseline?.[key],materialize);
  else if(object(raw)&&Object.hasOwn(raw,key)&&stableStringify(value)===stableStringify(baseline?.[key]))out[key]=raw[key];
  else out[key]=value;
 }
 return out;
}
export function prepareAutomationPersistence(saved,visible,names,aliases={},materialize=false){
 checkAutomationConfiguration(saved);checkAutomationConfiguration(visible);
 const baseline=normalize(structuredClone(saved),names,aliases,true);
 if(!materialize&&stableStringify(visible)===stableStringify(baseline))return {automation:saved,changed:false};
 const automation=mergeVisible(saved,visible,baseline,materialize);
 for(const [name,policy]of Object.entries(visible.policies)){
  if(!materialize&&!Object.hasOwn(saved?.policies||{},name)&&stableStringify(policy)===stableStringify(baseline.policies[name]))continue;
  const raw=policyAt(saved,name,aliases);automation.policies[name]=mergeVisible(raw,policy,baseline.policies[name]);
  if(saved?.policies?.[name]?.flow_review!==undefined)automation.policies[name].flow_review=saved.policies[name].flow_review;
  else delete automation.policies[name].flow_review;
 }
 return {automation,changed:stableStringify(saved)!==stableStringify(automation)};
}

// Only an explicit confirmation may turn a legacy empty placeholder into the
// safe rules shown to its reviewer. Other policies and extensions remain raw.
export function materializeEmptyReviewPolicy(saved,visible,names,aliases,business){
 checkAutomationConfiguration(saved);checkAutomationConfiguration(visible);
 if(!names.includes(business)||!Object.hasOwn(visible?.policies||{},business))throw Error('当前业务已变化，请重新核对自动化规则。');
 const raw=policyAt(saved,business,aliases);
 if(raw?.rules&&Object.keys(raw.rules).length)return {automation:saved,changed:false};
 const baseline=normalize(structuredClone(saved),names,aliases,true);
 if(stableStringify(visible.policies[business])!==stableStringify(baseline.policies[business]))throw Error('页面规则与服务器不同，请先保存并重新核对。');
 const shown=visible.policies[business],policy=structuredClone(raw||{});
 // Existing values must never be replaced by normalization defaults. If the
 // page masks an old value (for example a different timezone), require an
 // explicit repair instead of confirming settings the person cannot see.
 for(const key of ['triggers','time','timezone','budgetEnabled','budget']){
  if(Object.hasOwn(policy,key)&&stableStringify(policy[key])!==stableStringify(shown[key]))throw Error('已保存的自动化设置与当前页面显示不同，请先核对原设置。');
  if(!Object.hasOwn(policy,key))policy[key]=structuredClone(shown[key]);
 }
 policy.rules=structuredClone(shown.rules);
 const automation={...(saved||{}),version:saved?.version??1,policies:{...(saved?.policies||{}),[business]:policy}};
 return {automation,changed:stableStringify(saved)!==stableStringify(automation)};
}
