import React,{useEffect,useRef,useState} from 'react';
import {json} from './runtime-api';
import {initialExternalRegistration,buildExternalManifest,externalLookupPath,sameIdentity,safeExternalError} from './external-registration-contract.mjs';
import './external-registration.css';

const fresh=kind=>({schema:1,form:initialExternalRegistration(kind),manifest:null,record:null,pending:null});
const roleNames={business:'业务智能体',perception:'多模感知智能体',data:'数据管理智能体'};
const fieldTypes=[['string','文字'],['number','数值'],['integer','整数'],['boolean','是 / 否']];

function stored(key,kind){
 try{
  const raw=localStorage.getItem(key);if(raw===null)return {entry:fresh(kind),raw:null,error:''};
  const entry=JSON.parse(raw);
  if(!entry||entry.schema!==1||entry.form?.kind!==kind||!entry.form||typeof entry.form!=='object')throw Error();
  return {entry,raw,error:''};
 }catch{return {entry:fresh(kind),raw:null,error:'本机保留的登记输入无法读取；原记录已保留。请检查浏览器存储后重试。'};}
}

function Fields({title,rows,disabled,onChange}){
 return <fieldset disabled={disabled} className="external-schema-fields"><legend>{title}</legend>
  <p>填写服务使用的一级字段；当前支持文字、数值、整数和真假。嵌套对象、数组及复杂约束暂不支持此表单。</p>
  {rows.map((row,index)=><div className="external-schema-row" key={index}>
   <label>{title}字段名<input aria-label={`${title}字段 ${index+1} 名称`} value={row.name} onChange={e=>onChange(rows.map((x,i)=>i===index?{...x,name:e.target.value}:x))}/></label>
   <label>类型<select aria-label={`${title}字段 ${index+1} 类型`} value={row.type} onChange={e=>onChange(rows.map((x,i)=>i===index?{...x,type:e.target.value}:x))}>{fieldTypes.map(([v,label])=><option value={v} key={v}>{label}</option>)}</select></label>
   <label><input type="checkbox" aria-label={`${title}字段 ${index+1} 必填`} checked={row.required} onChange={e=>onChange(rows.map((x,i)=>i===index?{...x,required:e.target.checked}:x))}/>必填</label>
   <button type="button" onClick={()=>onChange(rows.filter((_,i)=>i!==index))}>删除字段</button>
  </div>)}
  <button type="button" disabled={rows.length>=30} onClick={()=>onChange([...rows,{name:'',type:'string',required:false}])}>添加{title}字段</button>
 </fieldset>;
}

export default function ExternalCapabilityRegistration({account,kind,Btn,request=json,onChanged,onRegisterGuard}){
 const key=`ga-external-registration-v1:${account.user_id}:${account.workspace_id}:${kind}`;
 const first=useRef(null);if(first.current===null)first.current=stored(key,kind);
 const [entry,setEntry]=useState(first.current.entry),[storageError,setStorageError]=useState(first.current.error),[error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false),[checked,setChecked]=useState(false),[conflict,setConflict]=useState(false);
 const [endpointInput,setEndpointInput]=useState(first.current.entry.form.endpoint||''),[endpointIssue,setEndpointIssue]=useState('');
 const entryRef=useRef(entry),lastRaw=useRef(first.current.raw),storageProblem=useRef(first.current.error),alive=useRef(true),pendingRequest=useRef(null);
 const actionBusy=useRef(false),guard=useRef(()=>true),guardCallback=useRef(onRegisterGuard),endpointProblem=useRef(false);
 guardCallback.current=onRegisterGuard;
 guard.current=()=>!storageProblem.current&&!endpointProblem.current&&!actionBusy.current&&!entryRef.current.pending;
 useEffect(()=>{
  alive.current=true;const check=()=>guard.current();guardCallback.current?.(check);
  return()=>{alive.current=false;pendingRequest.current?.abort();guardCallback.current?.(null,check);};
 },[]);
 useEffect(()=>{
  const warn=e=>{if(storageProblem.current||endpointProblem.current||actionBusy.current){e.preventDefault();e.returnValue='';}};
  window.addEventListener('beforeunload',warn);
  return()=>window.removeEventListener('beforeunload',warn);
 },[]);

 function save(next){
  entryRef.current=next;setEntry(next);
  try{
   if(storageProblem.current&&first.current.error)throw Error('original unreadable');
   if(localStorage.getItem(key)!==lastRaw.current)throw Error('changed by another page');
   const raw=JSON.stringify(next);localStorage.setItem(key,raw);lastRaw.current=raw;
   storageProblem.current='';setStorageError('');return true;
  }catch{
   const msg='本页输入未能安全保存，可能是浏览器存储不可用或其他页面正在编辑；请先处理后重试，不会覆盖原记录。';
   storageProblem.current=msg;setStorageError(msg);return false;
  }
 }
 function edit(field,value){if(entryRef.current.pending||entryRef.current.record)return;save({...entryRef.current,form:{...entryRef.current.form,[field]:value}});setError('');}
 function editEndpoint(value){
  setEndpointInput(value);let valid=!value;
  try{if(value){const u=new URL(value);valid=(u.protocol==='https:'||u.protocol==='http:'&&u.hostname==='127.0.0.1')&&!u.username&&!u.password&&!u.search&&!u.hash;}}catch{valid=false;}
  endpointProblem.current=!valid;setEndpointIssue(valid?'':'地址尚不完整，或包含凭据、查询参数；此输入尚未保存。');
  if(valid)edit('endpoint',value);
 }
 function editReference(value){
  if(!'GA_CONNECTOR_SECRET_'.startsWith(value)&&!/^GA_CONNECTOR_SECRET_[A-Z0-9_]*$/.test(value)){
   setError('这里只能填写服务端凭据引用名称，不能粘贴密钥；未保存该输入。');return;
  }
  edit('secret_ref',value);
 }
 function operationOptions(body,controller){return {method:'POST',headers:{'X-Workspace-ID':account.workspace_id},body:JSON.stringify(body),signal:controller.signal};}
 function changed(){onChanged?.();}
 function verified(row,manifest){
  if(!sameIdentity(row,manifest,account.workspace_id)||typeof row.id!=='string'||!Number.isInteger(row.version)||!['draft','validated','published','revoked'].includes(row.status))throw Error('服务器返回的能力版本与原登记内容不一致，请核对原记录。');
  return {id:row.id,version:row.version,status:row.status};
 }
 async function lookup(manifest,controller){return request(externalLookupPath(manifest),{cache:'no-store',signal:controller.signal,headers:{'X-Workspace-ID':account.workspace_id}});}
 async function perform(action,retry=false){
  if(actionBusy.current||storageProblem.current||endpointProblem.current)return;
  let current=entryRef.current,manifest=current.manifest;
  if(!retry){
   try{if(action==='create')manifest=buildExternalManifest(current.form);
    if(action==='validate'&&current.record?.status!=='draft')throw Error('请先保存草稿。');
    if(action==='publish'&&(account.role!=='admin'||current.record?.status!=='validated'))throw Error('需由管理员发布已校验的契约。');
    if(action==='publish'&&!current.form.reason?.trim())throw Error('请填写发布说明。');
   }catch(e){setError(e.message);return;}
   const pending={action,expected_version:current.record?.version||null,reason:action==='publish'?current.form.reason.trim():'核对外部能力契约',...(action==='create'?{request_id:crypto.randomUUID()}: {})};
   current={...current,manifest,pending};if(!save(current))return;
  }
  const pending=current.pending;if(!pending||pending.action!==action||!manifest)return;
  actionBusy.current=true;setBusy(true);setChecked(false);setConflict(false);setError('');setNotice('');
  const controller=new AbortController();pendingRequest.current=controller;
  let dispatched=false;
  try{
   let row;
   if(action==='create'){
    dispatched=true;
    if(pending.request_id){
     const result=await request('/external-capability-registrations/attempts',operationOptions({request_id:pending.request_id,manifest},controller));
     if(result?.request_id!==pending.request_id||!['created','cancelled'].includes(result.state))throw Error('登记回执无法核对。');
     if(result.state==='cancelled'){if(save({...entryRef.current,pending:null,manifest:null,record:null}))setNotice('原登记已取消，输入已保留，可以修改。');return;}
     row=result.record;
    }else row=await request('/external-capability-registrations',operationOptions(manifest,controller));
   }
   else {
    const before=verified(await lookup(manifest,controller),manifest);
    if(before.id!==current.record.id||before.version!==pending.expected_version||before.status!==(action==='validate'?'draft':'validated'))throw Error('服务器记录已更新，请先核对原操作。');
    dispatched=true;row=await request('/capabilities/'+encodeURIComponent(before.id)+'/'+(action==='validate'?'validate':'publish'),operationOptions({expected_version:pending.expected_version,reason:pending.reason},controller));
   }
   if(!alive.current)return;
   const saved=verified(row,manifest);
   if(action!=='create'&&saved.id!==current.record.id||action==='validate'&&!['validated','published'].includes(saved.status)||action==='publish'&&saved.status!=='published')throw Error('服务器返回的状态与操作不一致，请先核对原记录。');
   if(!save({...entryRef.current,record:saved,pending:null}))return;
   setNotice(action==='create'?(saved.status==='draft'?'登记草稿已保存在当前工作区。':'已读取当前工作区的登记记录。'):action==='validate'?'契约已校验；这不代表服务可用或模型效果通过。':'契约已发布；业务实例仍需单独选择和发布。');changed();
  }catch(e){if(alive.current&&e.name!=='AbortError'){
   if(!retry&&!dispatched){
    if(save({...entryRef.current,pending:null}))setError(safeExternalError(e,'服务器已拒绝该操作。')+' 原记录未被覆盖；输入已保留，可修改后重试。');
   }else setError(safeExternalError(e,'操作结果未确认。')+' 请先核对原操作，避免重复提交。');
  }}
  finally{actionBusy.current=false;if(alive.current){setBusy(false);pendingRequest.current=null;}}
 }
 async function cancelAttempt(){
  const current=entryRef.current,op=current.pending;
  if(!op||op.action!=='create'||!op.request_id||actionBusy.current||storageProblem.current)return;
  actionBusy.current=true;setBusy(true);setError('');setNotice('');const controller=new AbortController();pendingRequest.current=controller;
  try{
   const receipt=await request('/external-capability-registrations/attempts/'+encodeURIComponent(op.request_id)+'/cancel',operationOptions({},controller));
   if(!alive.current)return;
   if(receipt?.request_id!==op.request_id||!['created','cancelled'].includes(receipt.state))throw Error('服务器取消回执无法核对。');
   if(receipt.state==='cancelled'){
    if(save({...entryRef.current,pending:null,manifest:null,record:null})){setChecked(false);setConflict(false);setNotice('原登记已安全取消，输入已保留，可以修改后重新登记。');}
   }else{
    const saved=verified(receipt.record,current.manifest);
    if(save({...entryRef.current,pending:null,record:saved})){setChecked(false);setNotice('原登记已先完成，服务器记录已保留；没有重复创建。');changed();}
   }
  }catch(e){if(alive.current&&e.name!=='AbortError')setError(safeExternalError(e,'暂时无法确认取消结果。')+' 原操作和输入仍保留，请重新核对。');}
  finally{actionBusy.current=false;if(alive.current){setBusy(false);pendingRequest.current=null;}}
 }
 async function recover(){
  const current=entryRef.current;if(!current.pending||!current.manifest||actionBusy.current)return;
  actionBusy.current=true;setBusy(true);setError('');setNotice('');setChecked(false);setConflict(false);
  const controller=new AbortController();pendingRequest.current=controller;
  try{
   const row=await lookup(current.manifest,controller);if(!alive.current)return;
   if(current.pending.action==='create'&&row?.workspace_id===account.workspace_id&&row.namespace===current.manifest.namespace&&row.name===current.manifest.name&&row.release_version===current.manifest.version&&!sameIdentity(row,current.manifest,account.workspace_id)){
    setConflict(true);setError('同一标识和版本已有不同内容。原记录不会被覆盖，可保留输入并修改版本。');return;
   }
   const saved=verified(row,current.manifest),op=current.pending;
   const done=op.action==='create'||op.action==='validate'&&['validated','published'].includes(saved.status)||op.action==='publish'&&saved.status==='published';
   if(done){if(save({...entryRef.current,record:saved,pending:null})){setNotice('已从服务器核对当前状态；如由其他管理员操作，请查看原记录。');changed();}return;}
   const retryable=op.action==='validate'&&saved.status==='draft'&&saved.version===op.expected_version||op.action==='publish'&&saved.status==='validated'&&saved.version===op.expected_version;
   if(retryable){if(save({...entryRef.current,record:saved})){setChecked(true);setNotice('服务器仍是操作前版本，可以按原内容重试。');}return;}
   if(saved.version!==op.expected_version){setConflict(true);setError('服务器记录已发生变化。原操作无法再以旧版本提交；可保留输入并登记新版本。');}
   else setError('服务器记录已发生变化，请联系管理员核对该能力版本；本页不会覆盖原记录。');
  }catch(e){
   if(!alive.current||e.name==='AbortError')return;
   if(e.status===404&&current.pending.action==='create'){setChecked(true);setNotice('尚未查到这个版本，可以按原内容重试登记。');}
   else if(e.status===409&&current.pending.action==='create'){setConflict(true);setError('此标识和版本已由其他能力占用；可保留输入并修改版本。');}
   else setError(safeExternalError(e,'服务器状态暂时无法核对。')+' 原操作和输入均保留。');
  }finally{actionBusy.current=false;if(alive.current){setBusy(false);pendingRequest.current=null;}}
 }
 const form=entry.form,locked=busy||!!entry.pending||!!entry.record,canWrite=['admin','operator'].includes(account.role),noun=kind==='tool'?'工具':'专业模型';
 return <section className="external-registration" aria-label={`新增外部${noun}`}>
  <h3>新增外部{noun}</h3>
  <p>登记只读 HTTP JSON 或 MCP 服务。服务主机须由运行环境授权；只填写凭据引用，不在此输入密钥。登记后仍需校验、管理员发布，再在业务配置中选择。</p>
  <p>HTTP 校验检查契约和地址，不证明服务可连接；MCP 校验会访问服务核对工具和字段。模型效果需要另行评估。</p>
  {!canWrite&&<p role="status">当前账号可查看记录；新增和校验需操作员或管理员，发布需管理员。</p>}
  {storageError&&<p role="alert">{storageError} <Btn onClick={()=>save(entryRef.current)}>重试保存本页输入</Btn></p>}
  {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
  <fieldset disabled={!canWrite||locked}>
   <label>能力说明<input aria-label="能力说明" value={form.description} onChange={e=>edit('description',e.target.value)} maxLength={2000}/></label>
   <label>负责人<input aria-label="负责人" value={form.owner} onChange={e=>edit('owner',e.target.value)} maxLength={200}/></label>
   <label>所属空间（英文）<input aria-label="所属空间" value={form.namespace} onChange={e=>edit('namespace',e.target.value)} /></label>
   <label>英文标识<input aria-label="英文标识" value={form.name} onChange={e=>edit('name',e.target.value)} /></label>
   <label>版本<input aria-label="能力版本" value={form.version} onChange={e=>edit('version',e.target.value)} /></label>
   <label>接入方式<select aria-label="接入方式" value={form.adapter} onChange={e=>edit('adapter',e.target.value)}><option value="http">HTTP JSON</option><option value="mcp">MCP</option></select></label>
   <label>服务地址<input aria-label="服务地址" value={endpointInput} onChange={e=>editEndpoint(e.target.value)} placeholder="https://…" autoComplete="off"/></label>{endpointIssue&&<p role="alert">{endpointIssue}</p>}
   <label>服务端凭据引用（可留空）<input aria-label="服务端凭据引用" value={form.secret_ref} onChange={e=>editReference(e.target.value)} placeholder="GA_CONNECTOR_SECRET_…" autoComplete="off"/></label>
   {form.adapter==='mcp'&&<label>MCP 工具名称<input aria-label="MCP 工具名称" value={form.tool_name} onChange={e=>edit('tool_name',e.target.value)}/></label>}
   <fieldset><legend>允许哪些智能体调用</legend>{Object.entries(roleNames).map(([id,name])=><label key={id}><input type="checkbox" checked={form.roles.includes(id)} onChange={e=>edit('roles',e.target.checked?[...form.roles,id]:form.roles.filter(x=>x!==id))}/>{name}</label>)}</fieldset>
   <Fields title="输入" rows={form.input} disabled={locked} onChange={rows=>edit('input',rows)}/><Fields title="输出" rows={form.output} disabled={locked} onChange={rows=>edit('output',rows)}/>
   <label><input type="checkbox" checked={form.read_only} onChange={e=>edit('read_only',e.target.checked)}/>我确认服务只读取或分析，不执行外部写入或设备控制</label>
   <label><input type="checkbox" checked={form.external_allowed} onChange={e=>edit('external_allowed',e.target.checked)}/>我确认允许所选智能体按已登记字段向该服务发送数据</label>
  </fieldset>
  {entry.record&&<p role="status">当前版本：{({draft:'草稿',validated:'契约已校验',published:'契约已发布',revoked:'已撤销'})[entry.record.status]}。登记、校验和发布状态以服务器为准。</p>}
  {entry.record?.status==='validated'&&<label>发布说明<input aria-label="发布说明" disabled={!canWrite||busy||!!entry.pending} value={form.reason} onChange={e=>{const next={...entryRef.current,form:{...entryRef.current.form,reason:e.target.value}};save(next);}} /></label>}
  <div className="actions">
   {!entry.record&&!entry.pending&&<Btn primary disabled={!canWrite||busy||!!storageError||!!endpointIssue} onClick={()=>perform('create')}>保存登记草稿</Btn>}
   {entry.record?.status==='draft'&&!entry.pending&&<Btn disabled={!canWrite||busy||!!storageError} onClick={()=>perform('validate')}>校验契约</Btn>}
   {entry.record?.status==='validated'&&!entry.pending&&<Btn primary disabled={account.role!=='admin'||busy||!!storageError} onClick={()=>perform('publish')}>发布能力</Btn>}
   {entry.pending&&<><Btn disabled={busy||!!storageError} onClick={recover}>核对原操作</Btn>{checked&&<Btn disabled={!canWrite||entry.pending.action==='publish'&&account.role!=='admin'||busy||!!storageError} onClick={()=>perform(entry.pending.action,true)}>按原内容重试</Btn>}{entry.pending.action==='create'&&entry.pending.request_id&&<Btn disabled={!canWrite||busy||!!storageError} onClick={cancelAttempt}>取消原登记并修改</Btn>}{conflict&&!entry.pending.request_id&&<Btn disabled={busy||!!storageError} onClick={()=>{const current=entryRef.current;if(save({...current,form:{...current.form,version:''},manifest:null,record:null,pending:null})){setConflict(false);setNotice('原记录未改动，输入已保留。请填写新的版本。');}}}>保留输入并修改版本</Btn>}</>}
   {entry.record&&!entry.pending&&<><Btn disabled={busy||!!storageError} onClick={()=>{const old=entryRef.current;if(save({...old,form:{...old.form,version:''},manifest:null,record:null,pending:null})){setNotice('已保留输入和服务器原版本，请填写新版本后登记。');changed();}}}>保留输入，登记新版本</Btn>
    <Btn disabled={busy||!!storageError} onClick={()=>{if(save(fresh(kind))){setEndpointInput('');setEndpointIssue('');endpointProblem.current=false;setNotice('已保留服务器原记录，可以登记另一个能力。');changed();}}}>登记另一个能力</Btn></>}
  </div>
  {entry.pending&&<p role="status">上次{({create:'登记',validate:'校验',publish:'发布'})[entry.pending.action]}结果尚未确认。输入已保留，请先核对原操作；不会自动重新提交。</p>}
 </section>;
}
