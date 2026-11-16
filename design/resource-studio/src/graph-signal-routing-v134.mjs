import {checkedTaskInputMetadata,createTaskInputDraft,buildTaskGraphInputs,taskInputForm} from './graph-task-inputs.mjs';
import {freshNumber,numericValue,typedRequestJSON,plainTyped,typedEqual} from './typed-numbers-v132.mjs';
import {graphWireRequestJSON,graphWireParseCanonical,verifyGraphWire,graphWireCanonical,graphWireDigest,graphWireTypedCopy} from './full-semantic-graph-wire.mjs';
import {HTTP_SOURCE_CONTRACT_KEY,checkHTTPGraphSourceContract} from './graph-http-source-contract-v177.mjs';

export const GRAPH_ROUTE_MAX_RECORD=256*1024;
const HTTP_SOURCE_IDS_KEY='http_mapped_source_ids_v177';
export const graphRouteUUID=value=>typeof value==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value);
export const graphRouteHash=value=>typeof value==='string'&&/^[0-9a-f]{64}$/.test(value);
const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
export const graphRouteEqual=(a,b)=>graphWireRequestJSON(a)===graphWireRequestJSON(b);
const freshEqual=(server,fresh)=>typedEqual(graphWireTypedCopy(server),graphWireTypedCopy(fresh));
export const graphRouteNeed=(value,message='完整流程授权的范围或原记录无法核对，原稿和请求仍保留。')=>{if(!value){const error=Error(message);error.graphRouteSafe=true;throw error;}};
const exact=(value,keys)=>object(value)&&Object.keys(value).sort().join('|')===[...keys].sort().join('|');
const clone=value=>JSON.parse(JSON.stringify(value));
const mirrorOrder=value=>Array.isArray(value)?value.map(mirrorOrder):object(value)?Object.fromEntries(Object.keys(value).sort().map(key=>[key,mirrorOrder(value[key])])):value;
const safeText=(value,max=2000)=>typeof value==='string'&&[...value].length<=max&&!value.includes('\0')&&!/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value);
const unique=(value,limit,check)=>Array.isArray(value)&&value.length<=limit&&new Set(value).size===value.length&&value.every(check);
function httpSourceIDs(options){
 if(!Object.hasOwn(options,HTTP_SOURCE_IDS_KEY))return [];
 const ids=options[HTTP_SOURCE_IDS_KEY];
 graphRouteNeed(Array.isArray(options.sources)&&unique(ids,options.sources.length,graphRouteUUID)&&ids.length>0
  &&ids.every(id=>options.sources.some(source=>source.id===id)),'HTTP模拟来源清单与当前已核对来源不同；原选择保留，请重新读取范围。');
 return ids;
}
export function graphRouteScope(value){graphRouteNeed(exact(value,['actor_id','workspace_id','instance_id'])&&Object.values(value).every(graphRouteUUID));return clone(value);}
export const graphRouteBase=scope=>'/workspace/instances/'+scope.instance_id+'/signal-graph-routing-v134';
export const graphRoutePrefix=scope=>'ga-signal-graph-routing-v134:'+scope.workspace_id+':'+scope.actor_id+':'+scope.instance_id+':';
export const emptyGraphRouteForm=()=>({source_id:'',extractor_id:'',entity_ids:[],business_id:'',entity_input_fields:[],input_draft:null,input_history:[],purpose:'',required_fields:[],max_steps:'12',max_tool_calls:'12',max_tasks:'10',reason:'',pause_reason:'',confirmed:false,field_labels:{}});
const formKeys=Object.keys(emptyGraphRouteForm());
export function checkGraphRouteForm(value){
 graphRouteNeed(exact(value,formKeys));
 for(const key of ['source_id','extractor_id','business_id','max_steps','max_tool_calls','max_tasks','purpose','reason','pause_reason'])graphRouteNeed(safeText(value[key],key==='purpose'||key.includes('reason')?2000:200));
 graphRouteNeed(unique(value.entity_ids,30,graphRouteUUID)&&unique(value.entity_input_fields,80,key=>safeText(key,200))&&unique(value.required_fields,30,key=>safeText(key,200))&&typeof value.confirmed==='boolean'&&object(value.field_labels)&&Object.entries(value.field_labels).every(([key,label])=>safeText(key,200)&&safeText(label,300)));
 graphRouteNeed(value.input_draft===null||object(value.input_draft));
 graphRouteNeed(Array.isArray(value.input_history)&&value.input_history.length<=50);
 for(const row of value.input_history)graphRouteNeed(exact(row,['business_id','input_draft','field_labels','entity_input_fields'])&&safeText(row.business_id,200)&&object(row.input_draft)&&object(row.field_labels)&&unique(row.entity_input_fields,100,key=>safeText(key,200)));
 typedRequestJSON(value);return clone(value);
}
export function routeInputScope(scope,business){const metadata=business.input_metadata;return {...scope,release_id:metadata.release_id,business_id:business.id,configuration_hash:metadata.configuration_hash};}
export function routeConstantMetadata(business,replaced){
 const original=business.input_metadata,removed=new Set(replaced),metadata={...original,user_input_schema:{...original.user_input_schema,properties:Object.fromEntries(Object.entries(original.user_input_schema.properties).filter(([key])=>!removed.has(key))),required:(original.user_input_schema.required||[]).filter(key=>!removed.has(key))},entity_fields:original.entity_fields.filter(field=>!removed.has(field.field))};
 // Display-only layout: the original metadata remains intact.  No hash from
 // this filtered form is authorization; server preview validates every actual
 // object's complete expansion against the original graph input schema.
 return metadata;
}
export function routeInputDraft(scope,business,replaced,previous=null){
 const metadata=routeConstantMetadata(business,replaced),inputScope=routeInputScope(scope,business);
 checkedTaskInputMetadata(metadata,inputScope);const draft=createTaskInputDraft(metadata,inputScope,{typedNumbers:true});
 if(previous&&graphRouteEqual(previous.scope,draft.scope))for(const key of Object.keys(draft.values))if(previous.values?.[key])draft.values[key]=clone(previous.values[key]);
 return draft;
}
export function routeInteger(raw,label,min,max){const value=numericValue(freshNumber(raw,'integer',label));graphRouteNeed(value>=min&&value<=max,label+'须在'+min+'至'+max+'之间；原输入仍保留。');return value;}
const previewKeys=['schema','expected_version','release_id','source_id','extractor_id','entity_ids','business_id','entity_binding','entity_input_fields','graph_inputs','goal_spec','max_tasks','reason'];
export function checkGraphRouteCommand(command,kind='preview'){
 const keys=kind==='pause'?['schema','request_id','expected_version','reason']:kind==='enable'?[...previewKeys,'request_id','preview_hash','confirm_graph_execution']:previewKeys;
 graphRouteNeed(exact(command,keys)&&command.schema==='signal-graph-routing-'+kind+'-command-v134'&&Number.isSafeInteger(command.expected_version)&&command.expected_version>=1&&safeText(command.reason)&&command.reason.trim());
 if(kind!=='preview')graphRouteNeed(graphRouteUUID(command.request_id));
 if(kind==='pause')return command;
 graphRouteNeed(graphRouteUUID(command.release_id)&&graphRouteUUID(command.source_id)&&graphRouteUUID(command.extractor_id)&&/^business_[a-f0-9]{16}$/.test(command.business_id)&&unique(command.entity_ids,30,graphRouteUUID)&&command.entity_ids.length>0);
 graphRouteNeed(command.entity_binding==='each_trigger_object'&&unique(command.entity_input_fields,80,key=>safeText(key,200))&&object(command.graph_inputs)&&!command.entity_input_fields.some(key=>Object.hasOwn(command.graph_inputs,key)));
 const goal=command.goal_spec;graphRouteNeed(object(goal)&&graphRouteEqual(goal.entity_ids,command.entity_ids)&&safeText(goal.purpose)&&goal.purpose.trim()&&unique(goal.required_fields,30,key=>safeText(key,200))&&goal.required_fields.length>0&&goal.observation_mode==='existing_only'&&goal.deadline===null&&goal.data_query==null&&goal.relation_query==null&&Number.isSafeInteger(goal.max_steps)&&goal.max_steps>=1&&goal.max_steps<=12&&Number.isSafeInteger(goal.max_tool_calls)&&goal.max_tool_calls>=0&&goal.max_tool_calls<=36);
 graphRouteNeed(Number.isSafeInteger(command.max_tasks)&&command.max_tasks>=1&&command.max_tasks<=100);
 if(kind==='enable')graphRouteNeed(graphRouteHash(command.preview_hash)&&command.confirm_graph_execution===true);
 graphWireRequestJSON(command);return command;
}
export function buildGraphRoutePreview(form,options,scope){
 checkGraphRouteForm(form);const business=options.businesses.find(row=>row.id===form.business_id);
 graphRouteNeed(business&&business.available!==false,business?.reason||'请选择当前可用的完整流程业务。');
 graphRouteNeed(options.sources.some(row=>row.id===form.source_id)&&options.extractors.some(row=>row.id===form.extractor_id),'请明确选择当前来源和本地规则。');
 graphRouteNeed(form.entity_ids.length>0&&form.entity_ids.length<=options.max_entities&&form.entity_ids.every(id=>options.entities.some(row=>row.id===id)),'请选择当前范围内的对象；不会自动使用第一个对象。');
 const possible=new Set(business.entity_input_fields.map(row=>typeof row==='string'?row:row.field));
 graphRouteNeed(form.entity_input_fields.every(field=>possible.has(field)),'原触发对象替换字段已有变化，请重新核对。');
 const fields=new Set(business.goal_fields.filter(row=>form.entity_ids.every(id=>row.entity_ids.includes(id))).map(row=>row.key));graphRouteNeed(form.required_fields.length&&form.required_fields.every(field=>fields.has(field)),'请明确选择所有已选对象共有的目标核查字段，失效选择仍保留。');
 const metadata=routeConstantMetadata(business,form.entity_input_fields),inputScope=routeInputScope(scope,business),model=taskInputForm(metadata,inputScope,{typedNumbers:true});graphRouteNeed(model.supported,model.reason);
 const inputs=buildTaskGraphInputs(metadata,inputScope,form.input_draft,form.entity_ids,{typedNumbers:true});
 const httpSource=httpSourceIDs(options).includes(form.source_id);
 const criterion=(id,kind,description)=>({id,kind,description,capability_id:null,field:null,operator:null,expected:null});
 const goal={contract_version:'1.0',purpose:form.purpose,entity_ids:[...form.entity_ids],required_fields:[...form.required_fields],criteria:[criterion('evidence','evidence_sufficient','所选原目标字段资料充分且来源可核对'),criterion('human','human_confirmation','原流程完成后由负责人明确验收')],subgoals:[{id:'original',description:'核对本次触发对象的原业务要求',criterion_ids:['evidence','human']}],evidence:{source:'archive',minimum_verification:'recorded',minimum_records_per_entity:1,time:{mode:'archive',timezone:'Asia/Shanghai',start:null,end:null,at:null,relative:null,days:null,max_age_seconds:null}},observation_mode:'existing_only',deadline:null,max_steps:routeInteger(form.max_steps,'目标步骤上限',1,12),max_tool_calls:routeInteger(form.max_tool_calls,'目标能力调用上限',0,36),stop_on_conflict:true,relation_query:null,data_query:null};
 if(httpSource)goal.evidence={source:'observations',minimum_verification:'unverified_extraction',minimum_records_per_entity:1,
  time:{mode:'latest',timezone:'Asia/Shanghai',start:null,end:null,at:null,relative:null,days:null,max_age_seconds:3600}};
 const command={schema:'signal-graph-routing-preview-command-v134',expected_version:options.instance_version,release_id:options.release_id,source_id:form.source_id,extractor_id:form.extractor_id,entity_ids:[...form.entity_ids],business_id:form.business_id,entity_binding:'each_trigger_object',entity_input_fields:[...form.entity_input_fields],graph_inputs:inputs,goal_spec:goal,max_tasks:routeInteger(form.max_tasks,'新任务总数上限',1,100),reason:form.reason};
 return checkGraphRouteCommand(command);
}
export function graphRouteEnableCommand(preview,requestID){return checkGraphRouteCommand({...preview.command,schema:'signal-graph-routing-enable-command-v134',request_id:requestID,preview_hash:preview.preview_hash,confirm_graph_execution:true},'enable');}
export function graphRouteRecord(scope,action,routeID,body){
 checkGraphRouteCommand(body,action);graphRouteNeed(action==='enable'?routeID===null:graphRouteUUID(routeID));const exact_body=graphWireRequestJSON(body);graphRouteNeed(exact_body.length<=GRAPH_ROUTE_MAX_RECORD);
 return {schema:'signal-graph-routing-outbox-v134',scope:graphRouteScope(scope),action,route_id:routeID,exact_body,body:JSON.parse(JSON.stringify(plainTyped(body)))};
}
export function checkGraphRouteRecord(value,scope){
 graphRouteNeed(exact(value,['schema','scope','action','route_id','exact_body','body'])&&value.schema==='signal-graph-routing-outbox-v134'&&graphRouteEqual(value.scope,scope)&&['enable','pause'].includes(value.action)&&typeof value.exact_body==='string'&&value.exact_body.length<=GRAPH_ROUTE_MAX_RECORD);
 const body=graphWireParseCanonical(value.exact_body);checkGraphRouteCommand(body,value.action);
 graphRouteNeed(JSON.stringify(mirrorOrder(value.body))===JSON.stringify(mirrorOrder(JSON.parse(JSON.stringify(JSON.parse(value.exact_body)))))&&(value.action==='enable'?value.route_id===null:graphRouteUUID(value.route_id)));
 return {record:value,body};
}
export const graphRouteWire=verifyGraphWire;

const positive=value=>Number.isSafeInteger(value)&&value>=1;
const date=value=>typeof value==='string'&&Number.isFinite(Date.parse(value));
const named=value=>object(value)&&graphRouteUUID(value.id)&&safeText(value.name,2000);
function cap(value,rules=false){
 graphRouteNeed(exact(value,['id','name','version','content_hash',...(rules?['rules']:[])])&&named(value)&&positive(value.version)&&graphRouteHash(value.content_hash));
 if(rules)graphRouteNeed(Array.isArray(value.rules)&&value.rules.length>0&&value.rules.every(rule=>exact(rule,['id','field','operator','threshold'])&&safeText(rule.id,200)&&safeText(rule.field,200)&&['gt','lt'].includes(rule.operator)&&typeof rule.threshold==='string'&&/^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(rule.threshold)&&Number.isFinite(Number(rule.threshold))));
}
function entity(value){graphRouteNeed(exact(value,['id','name','version','type','definition_hash','attributes_hash','fields'])&&named(value)&&positive(value.version)&&safeText(value.type,200)&&graphRouteHash(value.definition_hash)&&graphRouteHash(value.attributes_hash)&&Array.isArray(value.fields));}
function summary(value){
 graphRouteNeed(exact(value,['graph_hash','binding_hash','nodes','abilities'])&&graphRouteHash(value.graph_hash)&&graphRouteHash(value.binding_hash)&&Array.isArray(value.nodes)&&Array.isArray(value.abilities));
 graphRouteNeed(value.nodes.length>0&&value.nodes.length<=128&&new Set(value.nodes.map(node=>node.id)).size===value.nodes.length);
 for(const node of value.nodes)graphRouteNeed(exact(node,['id','kind','name','actor_id'])&&safeText(node.id,200)&&safeText(node.name,2000)&&['human','ability','decision','fork','join','wait','end'].includes(node.kind)&&(node.actor_id===null||graphRouteUUID(node.actor_id)));
 for(const ability of value.abilities)graphRouteNeed(exact(ability,['node_id','capability_id','release_version','adapter','actor_id','name'])&&value.nodes.some(node=>node.id===ability.node_id&&node.kind==='ability')&&graphRouteUUID(ability.capability_id)&&graphRouteUUID(ability.actor_id)&&safeText(ability.release_version,100)&&safeText(ability.adapter,100)&&!['http','mcp','cloud_extractor'].includes(ability.adapter)&&safeText(ability.name,2000));
}
async function sealed(value,key){graphRouteNeed(graphRouteHash(value[key])&&await graphWireDigest(value,{omit:key})===value[key]);}
export async function checkGraphRouteOptions(envelope,scope){
 const value=verifyGraphWire(envelope);
 const hasHTTPSourceIDs=object(value)&&Object.hasOwn(value,HTTP_SOURCE_IDS_KEY);
 graphRouteNeed(exact(value,['schema','instance_id','instance_version','release_id','ttl_seconds','max_tasks_limit','max_entities','sources','extractors','entities','businesses','can_enable','blocking_reason','options_hash',...(hasHTTPSourceIDs?[HTTP_SOURCE_IDS_KEY]:[])])&&value.schema==='signal-graph-routing-options-v134'&&value.instance_id===scope.instance_id&&positive(value.instance_version)&&graphRouteUUID(value.release_id)&&value.ttl_seconds===86400&&value.max_tasks_limit===100&&value.max_entities===30&&typeof value.can_enable==='boolean'&&(value.can_enable?value.blocking_reason===null:safeText(value.blocking_reason,2000)));
 for(const key of ['sources','extractors','entities','businesses'])graphRouteNeed(Array.isArray(value[key])&&new Set(value[key].map(row=>row.id)).size===value[key].length);
 value.sources.forEach(row=>cap(row));value.extractors.forEach(row=>cap(row,true));value.entities.forEach(entity);
 httpSourceIDs(value);
 for(const business of value.businesses){graphRouteNeed(exact(business,['id','name','available','reason','input_metadata','graph_summary','goal_fields','entity_input_fields'])&&/^business_[a-f0-9]{16}$/.test(business.id)&&safeText(business.name,2000)&&typeof business.available==='boolean'&&(business.available?business.reason===null:safeText(business.reason,2000))&&Array.isArray(business.goal_fields)&&unique(business.entity_input_fields,100,key=>safeText(key,200)));
  if(business.available){checkedTaskInputMetadata(business.input_metadata,routeInputScope(scope,business));summary(business.graph_summary);graphRouteNeed(business.input_metadata.release_id===value.release_id&&business.input_metadata.mode==='graph');}
  for(const field of business.goal_fields)graphRouteNeed(exact(field,['key','label','kind','unit','entity_ids'])&&safeText(field.key,200)&&safeText(field.label,2000)&&safeText(field.kind,100)&&(field.unit===null||safeText(field.unit,200))&&unique(field.entity_ids,10000,graphRouteUUID)&&field.entity_ids.every(id=>value.entities.some(row=>row.id===id)));
 }
 await sealed(value,'options_hash');return value;
}
export async function checkGraphRoutePreview(envelope,original,scope){
 const value=verifyGraphWire(envelope);
 graphRouteNeed(exact(value,['schema','instance_id','instance_version','command','binding','expanded','deadline_policy','preview_hash'])&&value.schema==='signal-graph-routing-preview-v134'&&value.instance_id===scope.instance_id&&value.instance_version===original.expected_version&&value.deadline_policy==='original_grant_expiry'&&freshEqual(value.command,original));checkGraphRouteCommand(value.command);
 const binding=value.binding;
 const hasHTTPSource=object(binding)&&Object.hasOwn(binding,HTTP_SOURCE_CONTRACT_KEY);
 graphRouteNeed(exact(binding,['schema','release_id','configuration_hash','business','graph_hash','binding_hash','automation_hash','source','extractor','entities','actors','abilities','materials','materials_hash','input_metadata','graph','graph_summary',...(hasHTTPSource?[HTTP_SOURCE_CONTRACT_KEY]:[])])&&binding.schema==='signal-graph-routing-binding-v134'&&binding.release_id===original.release_id&&binding.business.id===original.business_id&&safeText(binding.business.name,2000));
 for(const key of ['configuration_hash','graph_hash','binding_hash','automation_hash','materials_hash'])graphRouteNeed(graphRouteHash(binding[key]));cap(binding.source);cap(binding.extractor,true);graphRouteNeed(binding.source.id===original.source_id&&binding.extractor.id===original.extractor_id&&Array.isArray(binding.entities)&&graphRouteEqual(binding.entities.map(row=>row.id).sort(),[...original.entity_ids].sort()));binding.entities.forEach(entity);summary(binding.graph_summary);
 if(hasHTTPSource)await checkHTTPGraphSourceContract(binding[HTTP_SOURCE_CONTRACT_KEY],{scope,command:original,binding});
 graphRouteNeed(object(binding.graph)&&binding.graph.schema==='full-semantic-graph-v1'&&Array.isArray(binding.graph.nodes)&&Array.isArray(binding.graph.edges)&&Array.isArray(binding.graph.loops)&&Array.isArray(binding.graph.subscriptions)&&Array.isArray(binding.graph.goals)&&object(binding.graph.predicates)&&object(binding.graph.references)&&object(binding.graph.guards)&&await graphWireDigest(binding.graph)===binding.graph_hash&&binding.graph_summary.graph_hash===binding.graph_hash&&binding.graph_summary.binding_hash===binding.binding_hash);
 graphRouteNeed(Array.isArray(binding.actors)&&Array.isArray(binding.abilities)&&Array.isArray(binding.materials)&&await graphWireDigest(binding.materials)===binding.materials_hash);
 for(const actor of binding.actors)graphRouteNeed(exact(actor,['id','role','username'])&&graphRouteUUID(actor.id)&&['operator','admin'].includes(actor.role)&&safeText(actor.username,200));
 for(const ability of binding.abilities)graphRouteNeed(exact(ability,['node_id','capability_id','name','content_hash','release_version','adapter','actor_id','agent_role','input_schema','output_schema'])&&graphRouteUUID(ability.capability_id)&&graphRouteHash(ability.content_hash)&&safeText(ability.release_version,100)&&!['http','mcp','cloud_extractor'].includes(ability.adapter)&&binding.actors.some(row=>row.id===ability.actor_id)&&binding.graph.nodes.some(node=>node.id===ability.node_id&&node.kind==='ability'&&node.actor_id===ability.actor_id&&node.execution.capability_id===ability.capability_id)&&object(ability.input_schema)&&object(ability.output_schema));
 graphRouteNeed(new Set(binding.abilities.map(item=>item.node_id)).size===binding.abilities.length&&binding.graph.nodes.filter(node=>node.kind==='ability').every(node=>binding.abilities.some(item=>item.node_id===node.id))&&graphRouteEqual(binding.graph.nodes.map(node=>({id:node.id,kind:node.kind,actor_id:node.actor_id??null})),binding.graph_summary.nodes.map(({id,kind,actor_id})=>({id,kind,actor_id}))));
 checkedTaskInputMetadata(binding.input_metadata,{...scope,release_id:binding.release_id,business_id:binding.business.id,configuration_hash:binding.configuration_hash});
 graphRouteNeed(Array.isArray(value.expanded)&&value.expanded.length===original.entity_ids.length&&new Set(value.expanded.map(row=>row.entity_id)).size===value.expanded.length);
 for(const row of value.expanded){graphRouteNeed(exact(row,['entity_id','graph_inputs','goal_spec'])&&original.entity_ids.includes(row.entity_id)&&freshEqual(row.graph_inputs,{...graphWireTypedCopy(original.graph_inputs),...Object.fromEntries(original.entity_input_fields.map(field=>[field,row.entity_id]))})&&freshEqual(row.goal_spec,{...graphWireTypedCopy(original.goal_spec),entity_ids:[row.entity_id]}));}
 graphRoutePreviewReadable(value);await sealed(value,'preview_hash');return {...value,original_command:original};
}
export function graphRoutePreviewReadable(preview){
 let items=0,characters=0;
 const visit=(value,depth=0)=>{graphRouteNeed(depth<=12&&++items<=10000,'原固定执行范围超过当前逐项核对表单的容量；原稿保留，尚不能确认启用，请先拆分并重新确认流程。');if(typeof value==='string'){characters+=value.length;graphRouteNeed(characters<=200000,'原固定执行说明过长，尚不能在本页完整核对；原稿保留，未授权启用。');}else if(value!==null&&typeof value==='object'){const rows=Object.values(value);graphRouteNeed(rows.length<=(Array.isArray(value)?500:200),'原固定执行范围包含过多条目，尚不能在本页完整核对；请先拆分流程，原稿保留。');rows.forEach(child=>visit(child,depth+1));}};
 preview.binding.graph.nodes.filter(node=>node.kind==='ability').forEach(node=>visit(node.execution.arguments));preview.expanded.forEach(row=>visit(row.graph_inputs));return true;
}
export async function checkGraphRouteListing(envelope,scope){
 const value=verifyGraphWire(envelope);graphRouteNeed(exact(value,['schema','instance_id','instance_version','routes','receipt','current_matches','entity_state_updated','source_time_verified','device_actions'])&&value.schema==='signal-graph-routing-response-v134'&&value.instance_id===scope.instance_id&&positive(value.instance_version)&&value.entity_state_updated===false&&value.source_time_verified===false&&value.device_actions===false&&Array.isArray(value.routes)&&value.routes.length<=100);
 graphRouteNeed(new Set(value.routes.map(row=>row.id)).size===value.routes.length);
 for(const row of value.routes){graphRouteNeed(exact(row,['id','actor_id','release_id','enabled_at','expires_at','status','paused_at','max_tasks','used_tasks','business','source','extractor','entities'])&&graphRouteUUID(row.id)&&graphRouteUUID(row.actor_id)&&graphRouteUUID(row.release_id)&&date(row.enabled_at)&&date(row.expires_at)&&Date.parse(row.expires_at)-Date.parse(row.enabled_at)===86400000&&['active','paused','expired','exhausted'].includes(row.status)&&(row.status==='paused'?date(row.paused_at):row.paused_at===null)&&positive(row.max_tasks)&&row.max_tasks<=100&&Number.isSafeInteger(row.used_tasks)&&row.used_tasks>=0&&row.used_tasks<=row.max_tasks&&safeText(row.business?.name,2000));cap(row.source);cap(row.extractor,true);graphRouteNeed(Array.isArray(row.entities));row.entities.forEach(entity);}
 if(value.receipt===null)graphRouteNeed(value.current_matches===null);else{await checkReceipt(value.receipt,scope);graphRouteNeed(typeof value.current_matches==='boolean'&&value.receipt.accepted_version<=value.instance_version&&value.current_matches===(value.receipt.accepted_version===value.instance_version));}
 return value;
}
async function checkReceipt(value,scope){graphRouteNeed(exact(value,['schema','request_id','action','actor_id','workspace_id','instance_id','route_id','command_hash','grant_hash','accepted_version','accepted_at','receipt_hash'])&&value.schema==='signal-graph-routing-receipt-v134'&&graphRouteUUID(value.request_id)&&['enable','pause'].includes(value.action)&&value.actor_id===scope.actor_id&&value.workspace_id===scope.workspace_id&&value.instance_id===scope.instance_id&&graphRouteUUID(value.route_id)&&graphRouteHash(value.command_hash)&&graphRouteHash(value.grant_hash)&&positive(value.accepted_version)&&date(value.accepted_at));await sealed(value,'receipt_hash');}
export async function checkGraphRouteSaved(envelope,record,scope){
 const recovery=verifyGraphWire(envelope);graphRouteNeed(exact(recovery,['schema','found','result'])&&recovery.schema==='signal-graph-routing-recovery-v134'&&typeof recovery.found==='boolean');if(!recovery.found){graphRouteNeed(recovery.result===null);return null;}
 const result=await checkGraphRouteListing({schema:'report-full-semantic-graph-canonical-wire-v1',canonical:graphWireCanonical(recovery.result),value:recovery.result},scope),receipt=result.receipt,body=checkGraphRouteRecord(record,scope).body;
 graphRouteNeed(receipt&&receipt.request_id===body.request_id&&receipt.action===record.action&&(record.action==='enable'||receipt.route_id===record.route_id)&&receipt.accepted_version===body.expected_version+1&&receipt.command_hash===await graphWireDigest({schema:'signal-graph-routing-request-v134',workspace_id:scope.workspace_id,actor_id:scope.actor_id,instance_id:scope.instance_id,action:record.action,route_id:record.route_id,body}));return result;
}
export function inspectSignalGraphTask(task){
 const value=task.signal_graph_route_v134;
 if(value===undefined){graphRouteNeed(task.signal_graph_origin_v134==null,'此任务的自动交接限制尚未核对。');return null;}
 graphRouteNeed(exact(value,['schema','status','message','can_submit_new_result','can_retry_ability','delivery_id'])&&value.schema==='signal-graph-task-state-v134'&&['active','paused','expired','unavailable'].includes(value.status)&&safeText(value.message,2000)&&(value.delivery_id===null||graphRouteUUID(value.delivery_id))&&value.can_submit_new_result===(value.status==='active')&&value.can_retry_ability===(value.status==='active'));
 return value;
}
export function inspectSignalGraphDelivery(signal){const value=signal.graph_route;if(value==null)return null;graphRouteNeed(exact(value,['schema','delivery_id','status','task_id','message','terminal','automatic_retry'])&&value.schema==='signal-graph-routing-public-v134'&&['reserved','created','blocked','skipped_existing_task'].includes(value.status)&&(value.delivery_id===null||graphRouteUUID(value.delivery_id))&&(value.task_id===null||graphRouteUUID(value.task_id))&&safeText(value.message,2000)&&value.terminal===(value.status!=='reserved')&&value.automatic_retry===false&&(value.status!=='created'||graphRouteUUID(value.task_id)));return value;}
