export const relationCardinalities={unspecified:'尚未核对数量限制',many:'不限制关联数量',one_per_source:'每个起点最多关联一个目标',one_per_target:'每个目标最多关联一个起点'};
const record=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const text=value=>typeof value==='string'&&!!value.trim();
export const relationTypeFields=['name','description','source_type','target_type','cardinality','allow_self'];
export function configuredRelationTypes(workspace){return Array.isArray(workspace.instanceRelationTypes)?workspace.instanceRelationTypes:[];}
export function relationTypeProblems(workspace,objects){
 if(Object.hasOwn(workspace,'instanceRelationTypes')&&!Array.isArray(workspace.instanceRelationTypes))return ['原关系定义无法读取，请保留原记录并核对。'];
 const names=new Set(),types=new Set(objects.map(item=>item.name)),issues=[];
 for(const row of configuredRelationTypes(workspace)){
  if(!record(row)){issues.push('存在无法读取的关系定义，请明确移除后继续。');continue;}
  if(!text(row.name)||names.has(row.name.trim()))issues.push('请填写不重复的关系名称。');names.add(row.name?.trim());
  if(!text(row.description))issues.push((row.name||'关系')+'：请说明关系含义与定义依据。');
  if(!types.has(row.source_type)||!types.has(row.target_type))issues.push((row.name||'关系')+'：起点或目标对象类型已变化，请重新选择。');
  if(!Object.hasOwn(relationCardinalities,row.cardinality)||row.cardinality==='unspecified')issues.push((row.name||'关系')+'：数量限制尚未核对，请明确选择。');
 }
 return [...new Set(issues)];
}
export function renameObjectRelations(workspace,oldName,newName){if(!text(oldName))return workspace;return {...workspace,instanceRelationTypes:configuredRelationTypes(workspace).map(row=>record(row)?{...row,source_type:row.source_type===oldName?newName:row.source_type,target_type:row.target_type===oldName?newName:row.target_type}:row)};}
export const relatedObjectTypes=(workspace,name)=>!text(name)?[]:configuredRelationTypes(workspace).filter(row=>row?.source_type===name||row?.target_type===name);
export const newRelationEditor=()=>({relationId:null,relationVersion:null,type:'',source:'',target:'',statement:'',reason:'',refs:[],documents:[],period:false,start:'',end:''});
export function switchRelationEditor(form,next,restoreIndex=null){
 const saved=Array.isArray(form.savedEditors)?form.savedEditors:[],current=form.editor;
 if(Object.hasOwn(form,'savedEditors')&&!Array.isArray(form.savedEditors))throw Error('其他关系草稿记录无法核对，已阻止覆盖。');
 const keep=current&&(current.type||current.source||current.target||current.statement||current.reason||current.refs?.length||current.documents?.length||current.start||current.end)&&JSON.stringify(current)!==JSON.stringify(next);
 const history=saved.filter((_,index)=>index!==restoreIndex);
 if(keep){if(history.length>=30)throw Error('已有30份其他关系草稿，请先恢复核对，或明确移除不再需要的恢复稿；当前输入保留。');history.push({at:new Date().toISOString(),editor:current});}
 return {...form,open:true,editor:next,savedEditors:history};
}
export function normalizeRelationDraft(value){
 const validEditor=editor=>record(editor)&&['type','source','target','statement','reason','start','end'].every(key=>typeof editor[key]==='string')&&Array.isArray(editor.refs)&&editor.refs.every(id=>typeof id==='string')&&Array.isArray(editor.documents)&&editor.documents.every(ref=>record(ref)&&['material_id','block_id','quote'].every(key=>typeof ref[key]==='string'))&&typeof editor.period==='boolean';
 if(record(value)&&validEditor(value.editor)&&record(value.reasons)){
  if(value.savedEditors!==undefined&&(!Array.isArray(value.savedEditors)||value.savedEditors.some(entry=>!record(entry)||!validEditor(entry.editor))))throw Error('其他关系恢复稿无法核对，原记录保留。');
  return value;
 }
 if(record(value)&&!Object.hasOwn(value,'editor')&&['statement','source_entity_id','target_entity_id','document_refs','evidence_refs'].some(key=>Object.hasOwn(value,key))){
  if(value.statement!==undefined&&typeof value.statement!=='string'||value.document_refs!==undefined&&!Array.isArray(value.document_refs)||value.evidence_refs!==undefined&&!Array.isArray(value.evidence_refs))throw Error('原关系草稿无法核对，原记录保留。');
  return {open:true,editor:{...newRelationEditor(),type:value.relation_type||'',source:value.source_entity_id||'',target:value.target_entity_id||'',statement:value.statement||'',reason:value.reason||'',refs:(value.evidence_refs||[]).filter(ref=>ref.kind==='observation').map(ref=>ref.id),documents:value.document_refs||[],period:!!value.valid_from,start:chinaInput(value.valid_from),end:chinaInput(value.valid_until)},reasons:{},originalDraft:value};
 }
 throw Error('已保存的关系草稿格式无法核对，原记录保留。');
}
export function validateRelationSources(editor,catalog){
 if(editor.refs.length>10||editor.documents.length>10)throw Error('登记记录与具体原文分别最多选择10条；原选择保留，请明确减少。');
 if(editor.refs.some(id=>!catalog.evidence_options?.some(option=>option.kind==='observation'&&option.id===id)))throw Error('已选登记来源当前不可用，请恢复来源或明确移除后重新核对。');
 for(const ref of editor.documents){
  if(typeof ref.quote!=='string'||!ref.quote.trim()||ref.quote.length>6000)throw Error('每条原文须保留1至6000字的连续原句，请在已选原文中明确摘取；完整来源仍保留。');
  const block=catalog.evidence_options?.find(option=>option.kind==='document'&&option.material_id===ref.material_id)?.blocks?.find(block=>block.block_id===ref.block_id);
  if(!block||!block.quote.includes(ref.quote))throw Error('所选引文须逐字来自本段原文；来源已变化时先恢复或明确移除此引用。');
 }
}
export function relationshipProposals(record){return record?.contract_version===5&&Array.isArray(record.result?.relationships)?record.result.relationships:[];}
export const emptyRelationScope=()=>({enabled:false,types:[],roots:[],direction:'outgoing',hops:1,includeRoots:false,at:''});
export function chinaInstant(value,label='关系时间'){
 if(typeof value!=='string'||!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value))throw Error('请填写完整的北京时间：'+label);
 const date=new Date(value+':00+08:00');
 if(!Number.isFinite(date.getTime())||new Date(date.getTime()+8*3600000).toISOString().slice(0,16)!==value)throw Error(label+'无效');
 return date.toISOString();
}
export function chinaInput(value){if(!value)return '';const date=new Date(value);return Number.isFinite(date.getTime())?new Date(date.getTime()+8*3600000).toISOString().slice(0,16):'';}
export function nativeRelationMetadata(metadata={}){
 if(!record(metadata))return {types:[],entities:[],error:'关系清单无法读取',packageId:null};
 const error=metadata.relation_types_error||null,types=metadata.relation_types,entities=metadata.entities;
 if(types===undefined&&entities===undefined)return {types:[],entities:[],error,packageId:metadata.package_id||null};
 if(!Array.isArray(types)||!Array.isArray(entities)||types.some(row=>!record(row)||!text(row.id)||!text(row.title)||!Array.isArray(row.source_types)||!Array.isArray(row.target_types))||entities.some(row=>!record(row)||!text(row.id)||!text(row.type)||!text(row.title)))return {types:[],entities:[],error:error||'关系类型或对象清单无法核对',packageId:metadata.package_id||null};
 return {types,entities,error,packageId:metadata.package_id||null};
}
export function buildRelationQuery(form,metadata){
 if(!Object.hasOwn(form,'relationScope'))return null;
 const scope=form.relationScope;
 if(!record(scope)||typeof scope.enabled!=='boolean')throw Error('原关系范围草稿无法恢复，请明确移除或重新核对。');
 if(!scope.enabled)return null;
 const {types,entities,error,packageId}=nativeRelationMetadata(metadata);
 if(error)throw Error('关系范围暂无法核对，原草稿保留：'+error);
 if(!text(packageId)||!types.length)throw Error('当前业务版本没有可核对的关系类型，原关系草稿保留。');
 if(!Array.isArray(scope.types)||scope.types.length<1||scope.types.length>5||new Set(scope.types).size!==scope.types.length||scope.types.some(id=>!types.some(row=>row.id===id)))throw Error('请选择当前业务版本的1至5个关系类型。');
 if(!Array.isArray(scope.roots)||scope.roots.length<1||scope.roots.length>10||new Set(scope.roots).size!==scope.roots.length||scope.roots.some(id=>!metadata.entity_ids?.includes(id)||!entities.some(row=>row.id===id)))throw Error('关系起查对象须属于明确选择的任务对象，最多10个。');
 if(!['outgoing','incoming'].includes(scope.direction))throw Error('请选择有效的关系方向。');
 const hops=Number(scope.hops);if(!Number.isInteger(hops)||hops<1||hops>3)throw Error('关系最多查询1至3层。');
 if(typeof scope.includeRoots!=='boolean')throw Error('原关系起查对象读取设置无法核对。');
 for(const id of scope.roots){const entity=entities.find(row=>row.id===id);if(!scope.types.some(type=>types.find(row=>row.id===type)[scope.direction==='outgoing'?'source_types':'target_types'].includes(entity.type)))throw Error('起查对象类型不符合所选关系方向，请核对。');}
 return {package_id:packageId,relation_types:[...scope.types],root_ids:[...scope.roots],direction:scope.direction,max_hops:hops,include_roots:scope.includeRoots,at:scope.at?chinaInstant(scope.at,'关系历史时点'):null};
}
export function relationCacheKey(account,instanceId,releaseId){return 'ga-native-relations:'+encodeURIComponent(account.workspace_id)+':'+encodeURIComponent(account.username)+':'+encodeURIComponent(instanceId)+':'+encodeURIComponent(releaseId);}
export function persistRelationCache(storage,key,value){const encoded=JSON.stringify(value);storage.setItem(key,encoded);if(storage.getItem(key)!==encoded)throw Error('关系草稿或原请求无法完整保存，已阻止发送。');}
export function readRelationCache(storage,key){const raw=storage.getItem(key);if(!raw)return null;let value;try{value=JSON.parse(raw);}catch{throw Error('关系恢复记录无法读取，原记录保留，请勿覆盖。');}if(!record(value)||!record(value.form)||typeof value.dirty!=='boolean'||value.pending&&(!record(value.pending.body)||!uuid.test(value.pending.body.request_id)||!Number.isInteger(value.pending.body.expected_version)||value.pending.body.expected_version<1||!text(value.pending.body.release_id)||!['draft','create','correct','review'].includes(value.pending.operation)))throw Error('关系恢复记录不完整，原记录保留，请先核对。');return value;}
