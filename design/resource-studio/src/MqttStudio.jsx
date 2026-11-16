import React,{useEffect,useRef,useState} from 'react';
import {api,json,hashBytes} from './runtime-api';
import {MQTT_FIELDS,newMQTTDraft,gatewayDraft,mqttBody,mqttIssue,referenceInputAllowed,authorizationReviewProblem} from './mqtt-ui-policy.mjs';
import MqttAssociationsPanel from './MqttAssociationsPanel';
import {associationHeading,associationDeliveryState,retryAssociationDelivery,mqttReceiptSummary} from './mqtt-association-policy.mjs';
import MqttReceptionPanel from './MqttReceptionPanel';
import MqttDailyStudio from './MqttDailyStudio';
import ManagedMqttAuthorizations from './ManagedMqttAuthorizations';
import {mergeWorkspacePatch} from './workspace-patches.mjs';

const displayTime=value=>value?new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'采集时间未知';
const tabs=['来源连接','报文与单位','业务对象关联','原件与处理记录'];
export default function MqttStudio({account,workspace,onCommit,onSave,Btn,Field,onBusy,onRuleBusy,onRegisterGuard}){
 const [rows,setRows]=useState([]),[loading,setLoading]=useState(true),[error,setError]=useState(''),[notice,setNotice]=useState(''),[ownBusy,setBusy]=useState(false),[associationBusy,setAssociationBusy]=useState(false),[dailyBusy,setDailyBusy]=useState(false);
 const busy=ownBusy||associationBusy||dailyBusy,busyReporter=useRef(onBusy);busyReporter.current=onBusy;
 const [selected,setSelected]=useState(()=>workspace.mqttSelection||'new'),[tab,setTab]=useState(0),[bindings,setBindings]=useState([]);
 const selectionRef=useRef(selected);selectionRef.current=selected;
 const [receipts,setReceipts]=useState([]),[total,setTotal]=useState(0),[cursor,setCursor]=useState(''),[focus,setFocus]=useState('');
 const [download,setDownload]=useState(null);const alive=useRef(true),latest=useRef(workspace);latest.current=workspace;
 const [mqttAuthorizations,setMqttAuthorizations]=useState([]);
 const row=rows.find(r=>r.id===selected),admin=account.role==='admin',operator=['admin','operator'].includes(account.role);
 // Browsing must not create a server draft. Keep the prospective request ID
 // stable in memory until the first explicit edit journals the complete draft.
 const emptyDraft=useRef(null);if(!emptyDraft.current)emptyDraft.current=newMQTTDraft();
 const form=admin?(workspace.mqttGatewayDrafts?.[selected]||(row?gatewayDraft(row):emptyDraft.current)):(row?gatewayDraft(row):emptyDraft.current);
 const currentBindings=bindings.filter(x=>x.config_hash===row?.config_hash);
 const currentReceipt=receipts.find(x=>x.id===focus);
 function persist(patch){let next;if(!onCommit(current=>{next=mergeWorkspacePatch(current,latest.current,patch);return next;}))throw Error('编辑尚未保存，请先处理页面上方的保存提示。');latest.current=next;}
 function changeForm(patch){try{persist({mqttGatewayDrafts:{...latest.current.mqttGatewayDrafts,[selected]:{...form,...patch}}});}catch(e){setError(e.message);}}
 function changeReference(key,value){if(!referenceInputAllowed(value)){setError('这里只接受 GA_MQTT_ 开头的服务端引用，不接受账号或密码原文。该输入未保存。');return;}changeForm({[key]:value});}
 async function load(){const saved=await json('/mqtt/gateways');if(alive.current){setRows(saved);setLoading(false);}return saved;}
 async function loadAuthorizations(){if(account.role!=='admin')return;const data=await json('/mqtt/authorizations');if(alive.current)setMqttAuthorizations((data.authorizations||[]).filter(x=>x.enabled));}
 useEffect(()=>{alive.current=true;load().catch(e=>{if(alive.current){setError(e.message);setLoading(false);}});loadAuthorizations().catch(()=>{});return()=>{alive.current=false;};},[]);
 useEffect(()=>{let cancelled=false;setReceipts([]);setBindings([]);setFocus('');setCursor('');setTotal(0);
   if(!row)return;
   (async()=>{const [b,r]=await Promise.all([json('/mqtt/gateways/'+row.id+'/bindings'),api('/mqtt/gateways/'+row.id+'/receipts?limit=50')]);
     const records=await r.json();if(!cancelled){setBindings(b);setReceipts(records);setTotal(Number(r.headers.get('X-Total-Count')));setCursor(r.headers.get('X-Next-Cursor')||'');}
   })().catch(e=>{if(!cancelled)setError(e.message);});return()=>{cancelled=true;};
 },[row?.id,row?.version]);
 useEffect(()=>()=>{if(download)URL.revokeObjectURL(download.url);},[download]);
 useEffect(()=>{busyReporter.current?.(busy);},[busy]);
 useEffect(()=>()=>busyReporter.current?.(false),[]);
 async function action(fn){if(busy)return;setBusy(true);setError('');setNotice('');try{if(operator)await onSave();await fn();}catch(e){setError(e.message);}finally{if(alive.current)setBusy(false);}}
 function choose(id){try{if(admin)persist({mqttSelection:id});setSelected(id);setTab(0);setDownload(null);setError('');setNotice('');}catch(e){setError(e.message);}}
 async function save(){await action(async()=>{
   const body=mqttBody(form),saved=await json('/mqtt/gateways'+(row?'/'+row.id:''),{method:row?'PUT':'POST',body:JSON.stringify(body)});
   // The request ID remains in the journal if a response is lost. Retrying cannot create another connection.
   persist({mqttSelection:saved.id,mqttGatewayDrafts:{...latest.current.mqttGatewayDrafts,[saved.id]:gatewayDraft(saved),...(!row?{new:newMQTTDraft()}:{})}});
   await onSave();setSelected(saved.id);await load();setNotice('接入配置已保存。接收处于暂停状态，尚未验证实际连接。');
 });}
 async function setStatus(status){await action(async()=>{
   if(status==='active'){const problem=authorizationReviewProblem(form,row);if(problem)throw Error(problem);}
   const updated=await json('/mqtt/gateways/'+row.id+'/status',{method:'POST',body:JSON.stringify({expected_version:row.version,status})});
   if(form?.baseVersion===row.version)changeForm({baseVersion:updated.version});
   await load();setNotice(status==='active'?'已授权接收此来源。实际连接仍需由后台只读接收服务确认。':'接收已暂停；原件、处理记录和已保存的编辑均保留。');
 });}
 async function page(append=false){const response=await api('/mqtt/gateways/'+row.id+'/receipts?limit=50'+(append&&cursor?'&after='+encodeURIComponent(cursor):''));
   const list=await response.json();if(!alive.current||selectionRef.current!==row.id)return;
   setReceipts(old=>append?[...old,...list.filter(x=>!old.some(o=>o.id===x.id))]:list);
   setTotal(Number(response.headers.get('X-Total-Count')));setCursor(response.headers.get('X-Next-Cursor')||'');
 }
 async function showReceipts(){setTab(3);try{await page();}catch(e){if(alive.current)setError(e.message);}}
 async function original(receipt){await action(async()=>{
   setDownload(null);const response=await api('/mqtt/receipts/'+receipt.id+'/original'),blob=await response.blob();
   if(await hashBytes(await blob.arrayBuffer())!==receipt.input_sha256)throw Error('原报文摘要不一致，停止下载；原记录保留待恢复。');
   setDownload({id:receipt.id,url:URL.createObjectURL(blob)});setNotice('原报文已读回并核对摘要，可以下载原字节。');
 });}
 async function retry(receipt,kind){await action(async()=>{await json('/mqtt/receipts/'+receipt.id+'/'+kind,{method:'POST',body:JSON.stringify({expected_version:receipt.version})});
   await page();setNotice(kind==='retry-data'?'已请求再次送达原件，后台处理结果可刷新查看。':'已按原件请求再次处理，原处理记录保留。');});}
 async function retryDelivery(delivery){await action(async()=>{await json('/mqtt/deliveries/'+delivery.delivery_id+'/submit',{method:'POST',body:JSON.stringify({expected_version:delivery.version})});await page();setNotice('已按此原件的关联版本请求恢复处理，历史结果保留。');});}
 if(loading)return <p role="status">正在读取传感接入记录…</p>;
 if(selected!=='new'&&!row)return <><p className="error" role="alert">此来源不在当前可见列表中，原编辑仍保留。当前仅读取最新 100 个来源，请核查工作区与访问权限。</p><Btn onClick={()=>action(load)}>重新读取来源</Btn><Btn onClick={()=>choose('new')}>新增其它来源</Btn></>;
 return <section className="mqtt-studio" aria-label="传感接入配置">
  <div className="section-title"><h2>传感与时序数据</h2><span className="badge">MQTT · 只读接收</span></div>
  <p>先接入设备数据，生成业务后再关联对象和本地规则。原件会送给数据管理智能体，缺少采集时间的数据单列待核查。</p>
  {admin&&<ManagedMqttAuthorizations account={account} Btn={Btn} onChanged={loadAuthorizations} onRegisterGuard={onRegisterGuard}/>}
  {error&&<div className="error" role="alert">{error}<Btn disabled={busy} onClick={()=>action(load)}>重新读取接入记录</Btn></div>}
  {notice&&<p role="status">{notice}</p>}
  <Field label="选择来源连接"><select id="mqtt-gateway-select" value={selected} disabled={busy} onChange={e=>choose(e.target.value)}><option value="new">新增传感接入</option>{rows.map(x=><option key={x.id} value={x.id}>{x.name}</option>)}</select></Field>
  <p className="info">{row?row.status_meaning:'尚未保存来源连接'}。接收服务只订阅已确认的数据主题，不发送设备指令。</p>
  {row&&<MqttReceptionPanel gateway={row} Btn={Btn} onShowReceipts={showReceipts} />}
  <nav className="gateway-tabs" aria-label="传感配置内容">{tabs.map((name,i)=><button type="button" key={name} disabled={busy} className={tab===i?'active':''} aria-current={tab===i?'page':undefined} onClick={()=>i===3?showReceipts():setTab(i)}>{name}</button>)}</nav>
  {form&&<fieldset className="mqtt-editor" disabled={busy||!admin}>
   {tab===0&&<><div className="gateway-fields">
    <Field label="接入名称"><input value={form.name} onChange={e=>changeForm({name:e.target.value})} placeholder="例如：北侧气象站" maxLength={200}/></Field>
    <Field label="MQTT 服务地址" note="包含端口，服务端须先批准此地址；远程服务使用 mqtts。"><input value={form.broker} onChange={e=>changeForm({broker:e.target.value})} placeholder="mqtts://broker.example.com:8883"/></Field>
    {mqttAuthorizations.length>0&&<Field label="选择已授权 MQTT 服务"><select aria-label="已授权 MQTT 服务" value="" onChange={e=>{const grant=mqttAuthorizations.find(x=>x.id===e.target.value);if(grant)changeForm({broker:grant.broker,accessLoginRef:grant.username_ref,accessKeyRef:grant.password_ref})}}><option value="">请选择已授权服务</option>{mqttAuthorizations.map(g=><option key={g.id} value={g.id}>{g.name} · {g.broker}</option>)}</select></Field>}
   </div><h3>确认设备与具体主题</h3><p>不使用通配符扫描未知设备。设备编号来自报文声明，仍需现场核验设备身份。</p>
    {form.topics.map((item,i)=><div className="mqtt-mapping-row" key={i}><Field label={'设备编号 '+(i+1)}><input value={item.device} onChange={e=>changeForm({topics:form.topics.map((x,n)=>n===i?{...x,device:e.target.value}:x)})}/></Field><Field label={'数据主题 '+(i+1)}><input value={item.topic} placeholder="station/north/data" onChange={e=>changeForm({topics:form.topics.map((x,n)=>n===i?{...x,topic:e.target.value}:x)})}/></Field><Btn aria-label={'移除主题 '+(i+1)} disabled={form.topics.length===1} onClick={()=>changeForm({topics:form.topics.filter((_,n)=>n!==i)})}>移除</Btn></div>)}
    <Btn disabled={form.topics.length>=32} onClick={()=>changeForm({topics:[...form.topics,{topic:'',device:''}]})}>添加设备主题</Btn>
    <details><summary>服务认证引用</summary><p>由运维登记服务端凭据，本页只保存引用名称。不要在地址或草稿里填写密码。</p><div className="gateway-fields"><Field label="用户名引用"><input value={form.accessLoginRef} placeholder="GA_MQTT_SITE_USER" onChange={e=>changeReference('accessLoginRef',e.target.value)}/></Field><Field label="密码引用"><input value={form.accessKeyRef} placeholder="GA_MQTT_SITE_PASS" onChange={e=>changeReference('accessKeyRef',e.target.value)}/></Field></div></details>
   </>}
   {tab===1&&<><h3>报文里的名称如何解释</h3><p>目前接收 UTF-8 JSON 数值报文。采集时间未带时区时按中国时间解释并标记；不会用接收时间填补缺失的采集时间。</p><div className="gateway-fields">{Object.entries(MQTT_FIELDS).map(([key,label])=><Field key={key} label={label}><input value={form.fields[key]} onChange={e=>changeForm({fields:{...form.fields,[key]:e.target.value}})}/></Field>)}</div>
    <h3>测量字段与单位</h3><p>填写测量值对象内的字段名，以及便于识别的测量名称。业务字段在生成业务后再对应。单位未写入报文时采用本页明确确认的单位，不自动换算。</p>
    {form.measurements.map((item,i)=><div className="mqtt-mapping-row" key={i}><Field label={'报文字段 '+(i+1)}><input value={item.input} placeholder="t" onChange={e=>changeForm({measurements:form.measurements.map((x,n)=>n===i?{...x,input:e.target.value}:x)})}/></Field><Field label={'测量名称 '+(i+1)}><input value={item.attribute} placeholder="空气温度" onChange={e=>changeForm({measurements:form.measurements.map((x,n)=>n===i?{...x,attribute:e.target.value}:x)})}/></Field><Field label={'计量单位 '+(i+1)}><input value={item.unit} placeholder="与报文一致，例如 C" onChange={e=>changeForm({measurements:form.measurements.map((x,n)=>n===i?{...x,unit:e.target.value}:x)})}/></Field><Btn aria-label={'移除测量 '+(i+1)} disabled={form.measurements.length===1} onClick={()=>changeForm({measurements:form.measurements.filter((_,n)=>n!==i)})}>移除</Btn></div>)}
    <Btn disabled={form.measurements.length>=64} onClick={()=>changeForm({measurements:[...form.measurements,{input:'',attribute:'',unit:''}]})}>添加测量字段</Btn>
   </>}
   {(tab===0||tab===1)&&<div className="mqtt-actions"><p>编辑草稿自动保存。保存接入配置后暂停接收，原件不删除；约定变化须重新关联对象。</p><Btn primary onClick={save}>保存接入配置</Btn>{row&&<Btn onClick={()=>{if(confirm('用服务器当前配置替换本页编辑草稿？已接收的原件保留。'))changeForm(gatewayDraft(row));}}>用服务器配置重新编辑</Btn>}</div>}
  </fieldset>}
  {tab===2&&(!row?<p>先保存来源连接。业务尚未生成时也可以先接收原件。</p>:<MqttAssociationsPanel key={row.id} gateway={row} account={account} workspace={workspace} onCommit={onCommit} onSave={onSave} Btn={Btn} Field={Field}
   onBusy={setAssociationBusy} onRuleBusy={onRuleBusy} onRefreshSource={load} onChanged={async()=>{const sourceID=row.id,b=await json('/mqtt/gateways/'+sourceID+'/bindings');if(alive.current&&selectionRef.current===sourceID)setBindings(b);}}/>)}
  {tab===3&&<><div className="section-title"><h3>原件与处理记录</h3><Btn disabled={busy||!row} onClick={()=>action(()=>page())}>刷新接收记录</Btn></div><p>接收时间和原采集时间分开显示。送达数据管理不代表已用于对象状态，异常仍由已确认的业务规则判断。</p>
   {row&&<MqttDailyStudio key={account.workspace_id+':'+account.username+':'+row.id} {...{account,Btn}} gatewayId={row.id} onBusy={setDailyBusy}/>}
   {!row?<p>请先保存来源连接。</p>:!receipts.length?<p>尚未收到数据。已保存配置不代表设备已连接，请由运维检查只读接收服务与设备发送。</p>:<>
    <p>共 {total} 份原件，已显示 {receipts.length} 份。</p><div className="mqtt-receipt-list">{receipts.map(r=><button type="button" key={r.id} className={focus===r.id?'active':''} onClick={()=>{setFocus(r.id);setDownload(null);}}><strong>{r.decoded?.reported_record_id||'无法识别数据编号'}</strong><span>{mqttReceiptSummary(r)}</span><small>原采集：{displayTime(r.decoded?.observed_at)} · 接收：{displayTime(r.received_at)}</small><small>数据管理：{r.data_received?'原件已送达':r.data_delivery?.status==='blocked_delivery'?'待恢复送达':'等待送达'}</small></button>)}</div>
    {cursor&&<Btn disabled={busy} onClick={()=>action(()=>page(true))}>查看更早记录</Btn>}
    {currentReceipt&&<article className="mqtt-receipt-detail"><h3>核对原件与处理依据</h3><p>主题：{currentReceipt.topic} · 接收次数：{currentReceipt.deliveries} · {mqttReceiptSummary(currentReceipt)}</p>
     <p>原采集时间：{displayTime(currentReceipt.decoded?.observed_at)}{currentReceipt.decoded?.timezone_assumed_china?'（报文未带时区，按中国时间解释）':''}</p>
     <p>接收时间：{displayTime(currentReceipt.received_at)}</p>{currentReceipt.issues.length>0&&<ul>{currentReceipt.issues.map(i=><li key={i}>{mqttIssue(i)}</li>)}</ul>}
     {currentReceipt.decoded&&<dl>{Object.entries(currentReceipt.decoded.attributes).map(([field,value])=><React.Fragment key={field}><dt>{field}</dt><dd>{value} {currentReceipt.measurement_units?.[field]||'（单位未声明）'}<small> · {currentReceipt.decoded.unit_origin[field]==='message'?'单位来自报文':'单位采用原接入约定'}</small></dd></React.Fragment>)}</dl>}
     {(currentReceipt.processing?.error||currentReceipt.submission?.error||currentReceipt.data_delivery?.error)&&<p className="error">{currentReceipt.processing?.error||currentReceipt.submission?.error||currentReceipt.data_delivery?.error}</p>}
     {currentReceipt.associations?.length>0&&<section className="mqtt-association-deliveries" aria-label="各对象的处理记录"><h4>同一原件交给哪些业务对象</h4><p>每个对象按这里保存的关联版本处理。后来修改关联，不覆盖这些原处理依据。</p>{currentReceipt.associations.map(d=><article key={d.delivery_id}><strong>{associationHeading(d)} · 第 {d.revision} 版</strong><p>本地规则：{d.rule_name||'已发布本地规则'} · {associationDeliveryState(d)}</p>{(d.processing?.error||d.submission?.error)&&<p className="error">{d.processing?.error||d.submission?.error}</p>}{retryAssociationDelivery(d)&&<Btn disabled={busy||!operator||row.status!=='active'} onClick={()=>retryDelivery(d)}>按此关联恢复处理</Btn>}</article>)}</section>}
     <details><summary>查看原件摘要</summary><p className="mqtt-hash">{currentReceipt.input_sha256}</p></details>
     <div className="actions"><Btn disabled={busy} onClick={()=>original(currentReceipt)}>核对并下载原报文</Btn>{download?.id===currentReceipt.id&&<a className="btn primary" href={download.url} download={'传感原报文-'+(currentReceipt.decoded?.reported_record_id||'未识别')+'.bin'}>下载已核对的原报文</a>}
     {currentReceipt.data_delivery?.status==='blocked_delivery'&&<Btn disabled={busy||!operator} onClick={()=>retry(currentReceipt,'retry-data')}>原件恢复后重试送达</Btn>}
     {currentReceipt.status!=='quarantined'&&!['processed','pending'].includes(currentReceipt.processing?.status)&&currentBindings.some(b=>b.topic===currentReceipt.topic)&&<Btn disabled={busy||!operator||row.status!=='active'} onClick={()=>retry(currentReceipt,'submit')}>按原件请求处理</Btn>}</div>
    </article>}
   </>}
  </>}
  {row&&<div className="mqtt-actions"><Btn disabled={busy||!admin} onClick={()=>setStatus(row.status==='active'?'paused':'active')}>{row.status==='active'?'暂停接收':'授权接收此来源'}</Btn><small>此操作只影响数据接收，不会启动或控制设备。</small></div>}
 </section>;
}
