const IDENT=/^[a-z][a-z0-9_]{0,63}$/;
const FIELD=/^[A-Za-z_][A-Za-z0-9_]{0,63}$/;
const VERS=/^\d+\.\d+\.\d+$/;
const TYPES=new Set(['string','number','integer','boolean']);

export function initialExternalRegistration(kind) {
 return {kind,namespace:'external',name:'',version:'1.0.0',description:'',owner:'',adapter:'http',endpoint:'',secret_ref:'',tool_name:'',
  roles:[],input:[],output:[],read_only:false,external_allowed:false,reason:''};
}

function schema(rows,label) {
 if(!Array.isArray(rows)||rows.length>30)throw Error(label+'字段不能超过 30 项。');
 const properties=Object.create(null),required=[];
 for(const row of rows){
  if(!row||!FIELD.test(row.name||'')||['__proto__','constructor','prototype'].includes(row.name))throw Error(label+'字段名只能使用英文字母、数字和下划线，且不能以数字开头或使用保留名称。');
  if(Object.hasOwn(properties,row.name))throw Error(label+'字段名不能重复。');
  if(!TYPES.has(row.type))throw Error(label+'字段类型不支持。');
  properties[row.name]={type:row.type};if(row.required)required.push(row.name);
 }
 return {type:'object',properties,required,additionalProperties:false};
}

export function buildExternalManifest(form) {
 if(!form||!['tool','specialist_model'].includes(form.kind))throw Error('请选择工具或专业模型。');
 if(!IDENT.test(form.namespace||'')||!IDENT.test(form.name||''))throw Error('所属空间和英文标识须以小写字母开头，只能包含小写字母、数字和下划线。');
 if(!VERS.test(form.version||''))throw Error('版本请填写为 1.0.0 这样的三段数字。');
 if(!form.description?.trim()||!form.owner?.trim())throw Error('请填写能力说明和负责人。');
 if(!['http','mcp'].includes(form.adapter))throw Error('请选择 HTTP JSON 或 MCP。');
 let url;try{url=new URL(form.endpoint);}catch{throw Error('请填写完整服务地址。');}
 if((url.protocol!=='https:'&&!(url.protocol==='http:'&&url.hostname==='127.0.0.1'))||url.username||url.password||url.search||url.hash)
  throw Error('服务地址须为无凭据、无查询参数的 HTTPS 地址；测试节点允许获授权的本机 HTTP 地址。');
 if(form.secret_ref&&!/^GA_CONNECTOR_SECRET_[A-Z0-9_]+$/.test(form.secret_ref))throw Error('凭据只能填写服务端引用名称 GA_CONNECTOR_SECRET_…，不能输入密钥本身。');
 if(form.adapter==='mcp'&&!form.tool_name?.trim())throw Error('请填写 MCP 服务中的工具名称。');
 const roles=Array.isArray(form.roles)?form.roles:[];
 if(!roles.length||new Set(roles).size!==roles.length||roles.some(r=>!['business','perception','data'].includes(r)))throw Error('请至少选择一个可调用的智能体。');
 if(form.read_only!==true)throw Error('请确认服务只读取或分析，不执行外部写入或设备控制。');
 if(form.external_allowed!==true)throw Error('请确认允许按已选字段向所填服务发送数据。');
 const execution={adapter:form.adapter,endpoint:form.endpoint.trim(),side_effect:false};
 if(form.secret_ref)execution.secret_ref=form.secret_ref.trim();
 if(form.adapter==='mcp')execution.tool_name=form.tool_name.trim();
 return {contract_version:'1.0',kind:form.kind,namespace:form.namespace,name:form.name,version:form.version,
  owner:form.owner.trim(),description:form.description.trim(),input_schema:schema(form.input,'输入'),output_schema:schema(form.output,'输出'),
  allowed_agent_roles:roles,execution,data_policy:{external_allowed:true},budget:{max_attempts:1,max_concurrency:1},dependencies:[],specification:{}};
}

export function externalLookupPath(identity){
 return '/external-capability-registrations/lookup?'+new URLSearchParams({namespace:identity.namespace,name:identity.name,version:identity.version}).toString();
}

export function safeExternalError(error,fallback){
 const message=typeof error?.message==='string'&&error.message?error.message:fallback;
 return message.replace(/\b(?:https?|rtsp):\/\/[^\s<>]+/gi,'[地址已隐藏]').replace(/\b(api[_-]?key|token|password|authorization)\s*[:=]\s*\S+/gi,'$1=[已隐藏]').replace(/(密码|密钥|令牌|凭据)\s*[:：=]\s*\S+/g,'$1=[已隐藏]').slice(0,500);
}

const canonical = x => Array.isArray(x)?x.map(canonical):x&&typeof x==='object'?Object.fromEntries(Object.keys(x).sort().map(k=>[k,canonical(x[k])])):x;
export function sameIdentity(record,manifest,workspaceId){
 return !!record&&record.workspace_id===workspaceId&&record.namespace===manifest.namespace&&record.name===manifest.name&&record.release_version===manifest.version&&record.kind===manifest.kind
  &&JSON.stringify(canonical(record.data))===JSON.stringify(canonical(manifest));
}
