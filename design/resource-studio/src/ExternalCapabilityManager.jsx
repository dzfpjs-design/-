import React,{useEffect,useRef,useState} from 'react';
import {json} from './runtime-api';
import {externalLookupPath,safeExternalError} from './external-registration-contract.mjs';

function safeName(text){return typeof text==='string'?text.replace(/\b(?:https?|rtsp):\/\/[^\s<>]+/gi,'[地址已隐藏]').replace(/\b(api[_-]?key|token|password|authorization)\s*[:=]\s*\S+/gi,'$1=[已隐藏]').replace(/(密码|密钥|令牌|凭据)\s*[:：=]\s*\S+/g,'$1=[已隐藏]').slice(0,500):'';}
function safeEndpoint(value){try{const u=new URL(value);return u.username||u.password||u.search||u.hash?'地址包含特殊参数，请在原登记处核对':u.origin+'（服务路径已隐藏）';}catch{return '地址待核对';}}
function fields(schema){
 if(!schema||typeof schema!=='object'||schema.type!=='object'||!schema.properties||typeof schema.properties!=='object'||Array.isArray(schema.properties))return '复杂契约，请核对原登记';
 const entries=Object.entries(schema.properties);if(entries.length>30)return '字段超过 30 项，请核对原登记';
 if(!entries.length)return '无一级字段';
 return entries.map(([name,item])=>`${safeName(name)}（${({string:'文字',number:'数值',integer:'整数',boolean:'真假',object:'对象',array:'数组'})[item?.type]||'复杂类型'}${Array.isArray(schema.required)&&schema.required.includes(name)?'，必填':''}）`).join('、');
}
function readSaved(key){try{const raw=localStorage.getItem(key);if(!raw)return {value:{schema:1,reason:'',pending:null},raw:null,error:''};const value=JSON.parse(raw);if(!value||value.schema!==1||typeof value.reason!=='string')throw Error();return {value,raw,error:''};}catch{return {value:{schema:1,reason:'',pending:null},raw:null,error:'本机保留的原操作无法读取，已保留原记录。'};}}

export default function ExternalCapabilityManager({account,identity,kind,Btn,request=json,onChanged,onRegisterGuard,onClose}){
 const key=`ga-external-manage-v1:${account.user_id}:${account.workspace_id}:${identity.id}`;
 const initial=useRef(null);if(initial.current===null)initial.current=readSaved(key);
 const [local,setLocal]=useState(initial.current.value),[storageError,setStorageError]=useState(initial.current.error),[record,setRecord]=useState(null),[loading,setLoading]=useState(true),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[checked,setChecked]=useState(false),[conflict,setConflict]=useState(false);
 const localRef=useRef(local),raw=useRef(initial.current.raw),problem=useRef(initial.current.error),running=useRef(false),alive=useRef(true),controller=useRef(null),guardRef=useRef(onRegisterGuard);
 guardRef.current=onRegisterGuard;
 useEffect(()=>{alive.current=true;const check=()=>!problem.current&&!running.current&&!localRef.current.pending;guardRef.current?.(check);return()=>{alive.current=false;controller.current?.abort();guardRef.current?.(null,check);};},[]);
 useEffect(()=>{const warn=e=>{if(problem.current||running.current){e.preventDefault();e.returnValue='';}};window.addEventListener('beforeunload',warn);return()=>window.removeEventListener('beforeunload',warn);},[]);
 function save(next){localRef.current=next;setLocal(next);try{if(initial.current.error||localStorage.getItem(key)!==raw.current)throw Error();const value=JSON.stringify(next);localStorage.setItem(key,value);raw.current=value;problem.current='';setStorageError('');return true;}catch{const message='本机操作记录未能安全保存；请检查浏览器存储或其他页面的编辑，不会覆盖原记录。';problem.current=message;setStorageError(message);return false;}}
 function check(row){
  if(!row||row.id!==identity.id||row.workspace_id!==account.workspace_id||row.namespace!==identity.namespace||row.name!==identity.name||row.release_version!==identity.version||row.kind!==kind||!Number.isInteger(row.version)||!['draft','validated','published','revoked'].includes(row.status)||!['http','mcp'].includes(row.data?.execution?.adapter))throw Error('原记录与当前工作区或所选版本不一致，请重新打开。');
  return row;
 }
 async function get(signal){return check(await request(externalLookupPath(identity),{cache:'no-store',signal,headers:{'X-Workspace-ID':account.workspace_id}}));}
 async function load(){const c=new AbortController();controller.current=c;setLoading(true);setError('');try{const row=await get(c.signal);if(alive.current)setRecord(row);}catch(e){if(alive.current&&e.name!=='AbortError')setError(safeExternalError(e,'原记录暂时无法读取。'));}finally{if(alive.current)setLoading(false);}}
 useEffect(()=>{load();return()=>controller.current?.abort();},[identity.id,account.user_id,account.workspace_id,account.role]);
 async function perform(action,retry=false){
  if(running.current||problem.current)return;let current=localRef.current;
  if(!retry){
   if(!record||action==='validate'&&record.status!=='draft'||action==='publish'&&(record.status!=='validated'||account.role!=='admin'))return;
   if(action==='publish'&&!current.reason.trim()){setError('请填写发布说明。');return;}
   current={...current,pending:{action,expected_version:record.version,reason:action==='publish'?current.reason.trim():'核对外部能力契约'}};
   if(!save(current))return;
  }
  const pending=current.pending;if(!pending||pending.action!==action)return;
  running.current=true;setBusy(true);setError('');setNotice('');setChecked(false);setConflict(false);const c=new AbortController();controller.current=c;let dispatched=false;
  try{
   const before=await get(c.signal);
   if(before.version!==pending.expected_version||before.status!==(action==='validate'?'draft':'validated'))throw Error('原记录已发生变化，请核对最新状态。');
   dispatched=true;const row=check(await request('/capabilities/'+encodeURIComponent(identity.id)+'/'+(action==='validate'?'validate':'publish'),{method:'POST',headers:{'X-Workspace-ID':account.workspace_id},body:JSON.stringify({expected_version:pending.expected_version,reason:pending.reason}),signal:c.signal}));
   if(!alive.current)return;
   if(action==='validate'&&!['validated','published'].includes(row.status)||action==='publish'&&row.status!=='published')throw Error('服务器状态与操作不一致，请核对原记录。');
   if(save({...localRef.current,pending:null})){setRecord(row);setNotice(action==='validate'?'契约已校验；不代表服务在线或模型领域效果通过。':'契约已发布；业务实例仍须单独选择。');onChanged?.();}
  }catch(e){if(alive.current&&e.name!=='AbortError'){
   if(!retry&&!dispatched){if(save({...localRef.current,pending:null})){setError(safeExternalError(e,'服务器已拒绝操作。')+' 原记录未覆盖，请核对状态。');load();}}
   else setError(safeExternalError(e,'操作结果尚未确认。')+' 请先核对原操作，不会自动重试。');
  }}finally{running.current=false;if(alive.current){setBusy(false);controller.current=null;}}
 }
 async function recover(){
  if(!localRef.current.pending||running.current)return;running.current=true;setBusy(true);setError('');setNotice('');setConflict(false);const c=new AbortController();controller.current=c;
  try{const row=await get(c.signal);if(!alive.current)return;const p=localRef.current.pending;
   const done=p.action==='validate'&&['validated','published'].includes(row.status)||p.action==='publish'&&row.status==='published';
   if(done){if(save({...localRef.current,pending:null})){setRecord(row);setNotice('已从服务器核对当前状态；如由其他管理员操作，请查看原记录。');onChanged?.();}return;}
   const retry=p.action==='validate'&&row.status==='draft'&&row.version===p.expected_version||p.action==='publish'&&row.status==='validated'&&row.version===p.expected_version;
   if(retry){setRecord(row);setChecked(true);setNotice('服务器仍是操作前版本，可按原内容重试。');return;}
   if(row.version!==p.expected_version){setRecord(row);setConflict(true);setError('原记录版本已变化，旧操作无法再按原版本提交；请核对后停止等待。');}
   else setError('原记录状态已变化，请联系管理员核对；本页不会覆盖原记录。');
  }catch(e){if(alive.current&&e.name!=='AbortError')setError(safeExternalError(e,'暂时无法核对。')+' 原操作保留。');}
  finally{running.current=false;if(alive.current){setBusy(false);controller.current=null;}}
 }
 const canWrite=['admin','operator'].includes(account.role),s=record?.status;
 return <section className="local-model-card" aria-label="管理已登记的外部能力">
  <h3>管理已登记的{kind==='tool'?'工具':'专业模型'}</h3><Btn disabled={busy||!!local.pending||!!storageError} onClick={onClose}>返回登记清单</Btn>
  {loading&&<p role="status">正在核对服务器原记录…</p>}{storageError&&<p role="alert">{storageError} <Btn onClick={()=>save(localRef.current)}>重试保存</Btn></p>}{error&&<p role="alert">{error} {!local.pending&&<Btn onClick={load}>重新读取</Btn>}</p>}{notice&&<p role="status">{notice}</p>}
  {record&&<><p><strong>{safeName(record.data?.description)||record.name}</strong> · 版本 {record.release_version} · {({draft:'草稿',validated:'契约已校验',published:'契约已发布',revoked:'已撤销'})[s]}</p>
   <p>接入方式：{record.data.execution.adapter==='mcp'?'MCP':'HTTP JSON'}；服务地址：{safeEndpoint(record.data.execution.endpoint)}。凭据仅使用服务端引用。</p>
   <p>输入：{fields(record.data.input_schema)}。输出：{fields(record.data.output_schema)}。</p>
   <p>这里显示字段摘要；复杂规则及服务的真实行为需在原登记与服务方资料中核对。HTTP 校验不代表连通；MCP 校验会请求工具目录；发布不代表已绑定业务实例。</p>
   {record.data.execution.side_effect&&<p role="alert">此能力声明了外部写入或设备动作，仍受后台管理员审批与回执约束；请在发布前核对原登记。</p>}
   {!record.data.data_policy?.external_allowed&&<p>此登记尚未允许数据外发，发布不等于允许发送数据。</p>}
   {s==='validated'&&<label>发布说明<input aria-label="已有能力发布说明" disabled={!canWrite||busy||!!local.pending} value={local.reason} onChange={e=>save({...localRef.current,reason:e.target.value})}/></label>}
   <div className="actions">{s==='draft'&&!local.pending&&<Btn disabled={!canWrite||busy||!!storageError} onClick={()=>perform('validate')}>校验已有契约</Btn>}{s==='validated'&&!local.pending&&<Btn primary disabled={account.role!=='admin'||busy||!!storageError} onClick={()=>perform('publish')}>发布已有能力</Btn>}
    {local.pending&&<><Btn disabled={busy||!!storageError} onClick={recover}>核对已有能力原操作</Btn>{checked&&<Btn disabled={!canWrite||local.pending.action==='publish'&&account.role!=='admin'||busy||!!storageError} onClick={()=>perform(local.pending.action,true)}>按原内容重试已有能力</Btn>}{conflict&&<Btn disabled={busy||!!storageError} onClick={()=>{if(save({...localRef.current,pending:null})){setConflict(false);setNotice('已停止等待旧版本操作，服务器记录保持不变。');}}}>停止等待并保留记录</Btn>}</>}</div>
  </>}
  {local.pending&&<p role="status">上次{local.pending.action==='validate'?'校验':'发布'}结果尚未确认；已保留原操作，请先核对，系统不会自动重发。</p>}
 </section>;
}
