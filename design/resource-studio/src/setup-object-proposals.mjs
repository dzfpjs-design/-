export const objectFieldKinds=['文本','整数','数值','是否','日期'];
export const objectTypeSchemaV170='report-object-type-v170';
const plainObject=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const exactObject=(value,keys)=>plainObject(value)&&Object.keys(value).length===keys.length&&keys.every(key=>Object.hasOwn(value,key));
const objectIssue='已采用的对象结构、字段或身份说明无法核对；原配置保留，请返回业务结构核对后重新采用。';
const needObject=value=>{if(!value)throw Error(objectIssue);};
const objectText=(value,max,min=0)=>typeof value==='string'&&Array.from(value).length>=min&&Array.from(value).length<=max&&!/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value);
// Compare with Python str.strip without changing any saved value. In particular,
// JavaScript trim also removes BOM, while the backend keeps it as declared text.
const pythonStrip=value=>value.replace(/^[\u0009-\u000D\u001C-\u0020\u0085\u00A0\u1680\u2000-\u200A\u2028\u2029\u202F\u205F\u3000]+|[\u0009-\u000D\u001C-\u0020\u0085\u00A0\u1680\u2000-\u200A\u2028\u2029\u202F\u205F\u3000]+$/gu,'');
export function checkedObjectTypeV170(value){
 needObject(exactObject(value,['schema','name','fields','business_identity','platform_identity_confirmation'])&&value.schema===objectTypeSchemaV170&&objectText(value.name,100,1)&&value.name===pythonStrip(value.name)&&Array.isArray(value.fields)&&value.fields.length>=1&&value.fields.length<=40);
 for(const field of value.fields){needObject(exactObject(field,['label','kind','required','unit'])&&objectText(field.label,80,1)&&field.label===pythonStrip(field.label)&&objectFieldKinds.includes(field.kind)&&typeof field.required==='boolean'&&(field.unit===null||objectText(field.unit,40,1)&&field.unit===pythonStrip(field.unit)&&numericObjectField(field.kind)));}
 const labels=value.fields.map(field=>field.label),identity=value.business_identity,confirmation=value.platform_identity_confirmation;
 needObject(new Set(labels).size===labels.length&&exactObject(identity,['status','field_labels','citations','explanation'])&&['not_established','referenced'].includes(identity.status)&&Array.isArray(identity.field_labels)&&identity.field_labels.length<=40&&new Set(identity.field_labels).size===identity.field_labels.length&&identity.field_labels.every(label=>typeof label==='string'&&labels.includes(label))&&Array.isArray(identity.citations)&&identity.citations.length<=4&&identity.citations.every(citation=>exactObject(citation,['span_ref'])&&objectText(citation.span_ref,100)&&/^[wo]:p[1-9][0-9]*\/s[1-9][0-9]*$/.test(citation.span_ref))&&objectText(identity.explanation,1000,1)&&!!pythonStrip(identity.explanation));
 needObject(identity.status==='not_established'?identity.field_labels.length===0:identity.field_labels.length>0&&identity.citations.length>0);
 needObject(exactObject(confirmation,['object_name','acknowledge_platform_identity'])&&confirmation.object_name===value.name&&confirmation.acknowledge_platform_identity===true);
 // This validates the wire shape only. A server-issued source and adoption
 // ledger must independently authorize compilation; a client marker cannot.
 return JSON.parse(JSON.stringify(value));
}
export function protectedObjectDefinition(object,origin){return object?.schema===objectTypeSchemaV170||typeof origin?.schema==='string'&&(origin.schema.startsWith('report-object-schema-')||origin.schema.startsWith('report-full-semantic-schema-'));}
const equalObjectJSON=(left,right)=>Array.isArray(left)?Array.isArray(right)&&left.length===right.length&&left.every((item,index)=>equalObjectJSON(item,right[index])):plainObject(left)?plainObject(right)&&Object.keys(left).length===Object.keys(right).length&&Object.keys(left).every(key=>Object.hasOwn(right,key)&&equalObjectJSON(left[key],right[key])):left===right;
export function verifyObjectTypeSnapshotV170(objects,snapshot){
 const selected=objects.filter(object=>Object.hasOwn(object,'schema')).map(checkedObjectTypeV170);if(!selected.length)return snapshot;
 needObject(plainObject(snapshot)&&Array.isArray(snapshot.object_types)&&plainObject(snapshot.entity_schemas));
 const kinds={'文本':{type:'string',maxLength:2000},'整数':{type:'integer'},'数值':{type:'number'},'是否':{type:'boolean'},'日期':{type:'string',format:'date'}};
 for(const expected of selected){const matches=snapshot.object_types.filter(object=>object?.name===expected.name);needObject(matches.length===1);const actual=matches[0];needObject(exactObject(actual,['key','name','fields','contract_schema','business_identity','platform_identity_confirmation'])&&actual.contract_schema===objectTypeSchemaV170&&typeof actual.key==='string'&&/^object_[a-f0-9]{16}$/.test(actual.key)&&Array.isArray(actual.fields)&&actual.fields.length===expected.fields.length);
  const fields=actual.fields.map(field=>{needObject(exactObject(field,['key','label','kind','required','unit'])&&typeof field.key==='string'&&/^field_[a-f0-9]{16}$/.test(field.key));const {key,...declared}=field;return declared;});
  needObject(equalObjectJSON(checkedObjectTypeV170({schema:objectTypeSchemaV170,name:actual.name,fields,business_identity:actual.business_identity,platform_identity_confirmation:actual.platform_identity_confirmation}),expected)&&new Set(actual.fields.map(field=>field.key)).size===actual.fields.length);
  const properties={},required=[];for(const field of actual.fields){const property={...kinds[field.kind],title:field.label};if(field.unit!==null)property.unit=field.unit;if(field.required){required.push(field.key);if(field.kind==='文本')Object.assign(property,{minLength:1,pattern:'\\S'});}Object.defineProperty(properties,field.key,{enumerable:true,value:property});}
  needObject(equalObjectJSON(snapshot.entity_schemas[actual.key],{type:'object',title:actual.name,properties,required,additionalProperties:false}));
 }
 return snapshot;
}
export const numericObjectField=kind=>kind==='整数'||kind==='数值';
export const fieldUnit=field=>numericObjectField(field?.kind)&&typeof field.unit==='string'&&field.unit.trim()?field.unit.trim():null;
export function schemaField(field){return {label:field.label,kind:field.kind,required:!!field.required,unit:numericObjectField(field.kind)&&typeof field.unit==='string'&&field.unit.trim()?field.unit:null};}
export function schemaObjects(objects){return objects.map(object=>{if(Object.hasOwn(object,'schema'))return checkedObjectTypeV170(object);return {name:object.name,fields:object.fields.map(schemaField)};});}
export function configuredObjects(workspace){
 if(Array.isArray(workspace.instanceObjects))return schemaObjects(workspace.instanceObjects);
 return [...new Set((workspace.instanceProfile?.objectCandidates||[]).filter(name=>typeof name==='string'))].map(name=>({name,fields:[{label:'名称',kind:'文本',required:true,unit:null}]}));
}
export function understandingVersion(record){const version=record?.contract_version;if(record?.edit_mode===true&&version!==6||version===6&&record?.edit_mode!==true)return null;return version===undefined?1:[1,2,3,4,5,6].includes(version)?version:null;}
export function objectProposals(record){return understandingVersion(record)>=2&&Array.isArray(record?.result?.objects)?record.result.objects.filter(object=>object&&typeof object.name==='string'&&Array.isArray(object.fields)):[];}
export function objectChoices(record,workspace){const existing=new Set(configuredObjects(workspace).map(object=>object.name.trim()));return objectProposals(record).map(object=>({object,existing:existing.has(object.name.trim())}));}
export function reviewSelection(value){return {names:Array.isArray(value?.names)?value.names.filter(name=>typeof name==='string'):[],object_names:Array.isArray(value?.object_names)?value.object_names.filter(name=>typeof name==='string'):[],accept_domain:!!value?.accept_domain};}
export function selectionBody(record,value){if(!understandingVersion(record)||understandingVersion(record)===6)throw Error('此记录不能使用整业务审核操作。');const selection=reviewSelection(value);return {names:selection.names,accept_domain:selection.accept_domain,...(understandingVersion(record)>=2?{object_names:selection.object_names}:{})};}
export function legacyExistingUpsert(record,workspace,business){
 if(![1,2,3,4,5].includes(understandingVersion(record))||record?.status!=='review'||!business||(business.operation||'upsert')!=='upsert')return false;
 const target=business.original_name||business.name,names=[...(record.before_context?.businesses||[]),...(workspace?.businesses||[])].map(value=>typeof value==='string'?value:value?.name);
 return names.includes(target);
}
export function canApplySelection(record,workspace,value){const selection=reviewSelection(value),available=new Set(objectChoices(record,workspace).filter(choice=>!choice.existing).map(choice=>choice.object.name));return !!understandingVersion(record)&&understandingVersion(record)!==6&&record?.status==='review'&&(selection.names.length>0||selection.object_names.length>0||selection.accept_domain)&&selection.object_names.every(name=>available.has(name))&&selection.names.every(name=>!legacyExistingUpsert(record,workspace,record.result?.businesses?.find(business=>business.name===name)));}
export const suggestedUnitText=field=>numericObjectField(field.kind)?fieldUnit(field)||'原文未明确，保持未知':'不适用';
export function changeObjectField(field,key,value){const next={...schemaField(field),[key]:value};return schemaField(next);}
export function originChanges(current,original){
 if(!original||!Array.isArray(original.fields))return [];
 const changes=[];if(current.name!==original.name)changes.push(`对象名称：${original.name} → ${current.name||'尚未填写'}`);
 const originalFields=original.fields.map(schemaField),currentFields=current.fields.map(schemaField);
 for(const field of originalFields){const next=currentFields.find(item=>item.label===field.label);if(!next){changes.push(`已移除或改名字段：${field.label}`);continue;}
  if(next.kind!==field.kind)changes.push(`${field.label}的类型：${field.kind} → ${next.kind}`);
  if(next.required!==field.required)changes.push(`${field.label}的必填要求：${field.required?'必填':'可选'} → ${next.required?'必填':'可选'}`);
  if(fieldUnit(next)!==fieldUnit(field))changes.push(`${field.label}的单位：${field.unit||'未声明'} → ${next.unit||'未声明'}`);
 }
 for(const field of currentFields)if(!originalFields.some(item=>item.label===field.label))changes.push(`已新增或改名字段：${field.label||'尚未填写'}`);
 return changes;
}

export function detachObjectOrigin(workspace,name){if(protectedObjectDefinition(workspace.instanceObjects?.find(object=>object.name===name),workspace.instanceObjectOrigins?.[name]))throw Error('此对象来自已采用的业务结构，不能解除来源后按人工定义覆盖；请返回业务结构核对并重新采用。');const origins={...workspace.instanceObjectOrigins};delete origins[name];return {...workspace,instanceObjectOrigins:origins};}
export const canReviewUnderstanding=account=>!account||['admin','operator'].includes(account.role);

export function renameObjectDefinition(workspace,index,name){
 const objects=configuredObjects(workspace),oldName=objects[index].name,linked=new Set(objects.map(object=>object.name));
 if(protectedObjectDefinition(objects[index],workspace.instanceObjectOrigins?.[oldName]))throw Error('已采用对象的名称与身份确认须在业务结构中核对，不能在生成页改写。');
 const origins=Object.fromEntries(Object.entries(workspace.instanceObjectOrigins||{}).filter(([key])=>linked.has(key)&&(oldName===name||key!==oldName)));
 return {...workspace,instanceObjects:objects.map((object,i)=>i===index?{...object,name}:object),instanceObjectOrigins:origins};
}
export function citationSourceMatches(material,citation){return material?.id===citation.material_id&&material.status==='parsed'&&!material.archived&&typeof citation.quote==='string'&&citation.quote.length>0&&!!material.blocks?.some(block=>block.id===citation.block_id&&typeof block.text==='string'&&block.text.includes(citation.quote));}
const requestUUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const previewHash=/^[0-9a-f]{64}$/i;
export function understandingStartKey(account){return account?.workspace_id&&account?.username?'ga-setup-understanding-start:'+encodeURIComponent(account.workspace_id)+':'+encodeURIComponent(account.username):null;}
export function startPending(preview){const pending={body:JSON.parse(JSON.stringify(preview.body)),preview_hash:preview.preview_hash};return validateStartPending(pending);}
export function validateStartPending(pending){
 const body=pending?.body,keys=['revision','model_id','material_ids','instruction','contract_version','request_id'];
 if(!body||Object.keys(body).length!==keys.length||!keys.every(key=>Object.hasOwn(body,key))||![2,3,4,5,6].includes(body.contract_version)||!Number.isInteger(body.revision)||body.revision<1||typeof body.model_id!=='string'||!body.model_id||!Array.isArray(body.material_ids)||body.material_ids.some(id=>typeof id!=='string')||typeof body.instruction!=='string'||!requestUUID.test(body.request_id)||!previewHash.test(pending.preview_hash))throw Error('上次启动请求的恢复记录无法核对。请保留已保存记录并检查浏览器存储，暂不另起云请求。');
 return {body,preview_hash:pending.preview_hash};
}
export function sameStartPending(a,b){if(!a||!b)return false;return a.preview_hash===b.preview_hash&&JSON.stringify([a.body.revision,a.body.model_id,a.body.material_ids,a.body.instruction,a.body.contract_version,a.body.request_id])===JSON.stringify([b.body.revision,b.body.model_id,b.body.material_ids,b.body.instruction,b.body.contract_version,b.body.request_id]);}
export function readStartPending(storage,key){if(!key)return null;let raw;try{raw=storage.getItem(key);}catch(cause){throw Object.assign(Error('本机恢复记录暂不能读取，请检查浏览器存储；暂不另起云请求。'),{cause});}if(!raw)return null;let pending;try{pending=JSON.parse(raw);}catch(cause){throw Object.assign(Error('上次启动恢复记录已损坏，请保留记录并检查浏览器存储；暂不另起云请求。'),{cause});}return validateStartPending(pending);}
export function writeStartPending(storage,key,pending){if(!key)throw Error('当前账号和工作区尚未确定，不能保存云启动请求。');validateStartPending(pending);const existing=readStartPending(storage,key);if(existing&&!sameStartPending(existing,pending))throw Error('另一个页面已有启动请求尚未确认，请先核对上次启动请求。');try{storage.setItem(key,JSON.stringify(pending));}catch(cause){throw Object.assign(Error('本机恢复记录无法保存，请检查浏览器存储；启动请求尚未发送。'),{cause});}}
export function clearStartPending(storage,key,pending){const existing=readStartPending(storage,key);if(!sameStartPending(existing,pending))return false;try{storage.removeItem(key);}catch(cause){throw Object.assign(Error('本机恢复记录暂不能清理，请保留原请求并检查浏览器存储。'),{cause});}return true;}

export function savedMaterialUpload(item,state){return {...item,state,error:false};}
export function materialUploadCounts(outcomes,total){const failed=outcomes.filter(item=>item.error===true).length;return {saved:outcomes.length-failed,failed,unprocessed:Math.max(0,total-outcomes.length)};}
