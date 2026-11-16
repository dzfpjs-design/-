// Human editing uses strings; conversion happens only at an explicit save/check.
export function newRuleDraft(entityID='', suggestions=[]){return {
 requestId:crypto.randomUUID(),title:'',entity_id:entityID,rationale:'',baseVersion:null,
 rules:(suggestions.length?suggestions:[{field:'',unit:null}]).map(f=>({id:crypto.randomUUID(),field:f.field,unit:f.unit||'',operator:'gt',threshold:''})),
 samples:{},publishReason:'',confirmed:false
};}
export function savedRuleDraft(row){return {...newRuleDraft(),title:row.title,entity_id:row.entity_id,rationale:row.rationale,
 baseVersion:row.version,rules:row.rules.map(r=>({...r,unit:r.unit||'',threshold:String(r.threshold)})),
 samples:Object.fromEntries(Object.entries(row.last_check?.values||{}).map(([k,v])=>[k,String(v)]))};}
export function finiteInput(value){
 if(typeof value!=='string'||!value.trim()||!/^[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?$/.test(value.trim()))throw Error('请填写完整的有限数值。');
 const n=Number(value);if(!Number.isFinite(n)||Math.abs(n)>1e15)throw Error('数值的绝对值不能超过 1e15。');return n;
}
export function ruleDefinition(draft){
 if(!draft.title.trim()||!draft.rationale.trim()||!draft.entity_id)throw Error('请填写规则名称、业务对象和阈值依据。');
 if(!draft.rules.length||draft.rules.length>32)throw Error('请保留 1 至 32 条数值条件。');
 return {title:draft.title,rationale:draft.rationale,entity_id:draft.entity_id,rules:draft.rules.map(r=>{
  if(!r.field||!['gt','lt'].includes(r.operator))throw Error('请选择业务字段和判断条件。');
  return {id:r.id,field:r.field,unit:r.unit.trim()||null,operator:r.operator,threshold:finiteInput(r.threshold)};
 })};
}
const canonical=value=>JSON.stringify(value,(_key,v)=>v&&typeof v==='object'&&!Array.isArray(v)?Object.fromEntries(Object.keys(v).sort().map(k=>[k,v[k]])):v);
export function sameRuleDefinition(draft,row){try{return canonical(ruleDefinition(draft))===canonical({title:row.title,rationale:row.rationale,entity_id:row.entity_id,rules:row.rules});}catch{return false;}}
export function ruleSamples(draft){return Object.fromEntries([...new Set(draft.rules.map(r=>r.field))].map(f=>[f,finiteInput(draft.samples?.[f]||'')]));}
export function compatibleRule(cap,object,fieldLinks,measurements){
 const meta=cap.data?.specification?.managed_numeric_rule;if(!meta)return true;
 if(!object||meta.package_id!==object.package_id||meta.entity_type!==object.entity_type)return false;
 const units=Object.fromEntries(measurements.map(m=>[fieldLinks?.[m.attribute],m.unit||null]));
 return cap.data.specification.rules.every(r=>Object.hasOwn(units,r.field)&&units[r.field]===r.unit);
}
