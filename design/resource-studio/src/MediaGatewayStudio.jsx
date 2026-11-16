import React,{useEffect,useRef,useState} from 'react';
import {json} from './runtime-api';
import ContinuousStudio from './ContinuousStudio';
import MediaMonitorStudio from './MediaMonitorStudio';
import VideoDomainStudio from './VideoDomainStudio';
import VideoDailyStudio from './VideoDailyStudio';
import VideoReadBoundary from './VideoReadBoundary';
import AudioSourcePanel from './AudioSourcePanel';
import OnvifDiscovery from './OnvifDiscovery';
import {safeGatewayField,validGatewayDraft,newGatewayDraft,gatewayDraftForEdit} from './media-gateway-draft.mjs';

const statusText={draft:'连接待检查',ready:'目录已读取',failed:'读取失败',confirmed:'已确认读取',validated:'读取与本地处理通过',disabled:'已停用',queued:'等待读取',connecting:'正在连接',capturing:'正在读取视频',captured:'原视频已保存',partial:'视频不完整',cancelled:'已停止',running:'正在本地分析',succeeded:'本地分析已完成'};
const request=(path,body,method='POST')=>json('/media'+path,{method,body:JSON.stringify(body)});

export default function MediaGatewayStudio({installation,account,workspace,onCommit,onSave,onViewVideo,onAudioSourceGuard,onOpenSoundModels,Btn,Field}){
 const [gateways,setGateways]=useState([]),[channels,setChannels]=useState([]),[sessions,setSessions]=useState([]),[monitors,setMonitors]=useState([]),[streams,setStreams]=useState([]);
 const initialDraft=useRef(workspace.mediaGatewayDraft??null),initialValid=initialDraft.current===null||validGatewayDraft(initialDraft.current);
 const [selected,setSelected]=useState(()=>initialValid?initialDraft.current?.id||'':''),[form,setForm]=useState(()=>initialValid?initialDraft.current:null),[checked,setChecked]=useState([]),[authorized,setAuthorized]=useState(false);
 const [loading,setLoading]=useState(true),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[recoveryMissing,setRecoveryMissing]=useState(false);
 const [seconds,setSeconds]=useState(6),[hz,setHz]=useState(1),[label,setLabel]=useState('none');
 const alive=useRef(true),formRef=useRef(form),draftUnsafe=useRef(!initialValid),busyRef=useRef(false);const admin=account.role==='admin',operator=['admin','operator'].includes(account.role);
 formRef.current=form;
 const audioGuards=useRef(new Map());
 function canLeaveAudio(){if(draftUnsafe.current||busyRef.current){setError('视频网关人稿或正在提交的请求尚未可靠保存，请先核对本区提示。');return false;}for(const guard of audioGuards.current.values())if(guard()===false){setError('音轨设置或原申请尚未可靠保存，请先在环境声音区域核对。');return false;}return true;}
 useEffect(()=>{onAudioSourceGuard?.(canLeaveAudio);return()=>onAudioSourceGuard?.(null,canLeaveAudio);},[]);
 function registerAudioGuard(id,guard,previous){if(guard)audioGuards.current.set(id,guard);else if(!previous||audioGuards.current.get(id)===previous)audioGuards.current.delete(id);}
 async function load(){const [g,c,s,m,t]=await Promise.all([json('/media/gateways'),json('/media/channels'),json('/media/sessions'),json('/media/monitors'),json('/media/continuous')]);if(alive.current){setGateways(g);setChannels(c);setSessions(s);setMonitors(m);setStreams(t);setLoading(false);}}
 useEffect(()=>{alive.current=true;load().catch(e=>{setError(e.message);setLoading(false);});const timer=setInterval(()=>load().catch(e=>setError(e.message)),2500);return()=>{alive.current=false;clearInterval(timer);};},[]);
 const gateway=gateways.find(g=>g.id===selected),directory=gateway?.inventory?.channels||[];
 const associated=channels.filter(c=>c.gateway_id===selected);
 async function act(fn){busyRef.current=true;setBusy(true);setError('');setNotice('');try{await fn();await load();}catch(e){setError(e.message);}finally{busyRef.current=false;setBusy(false);}}
 function persistForm(next){
  if(next!==null&&!validGatewayDraft(next)){setError('网关地址只能包含协议、IP 和端口；请勿输入账号、密码或查询参数。');return false;}
  formRef.current=next;setForm(next);
  try{if(onCommit(current=>({...current,mediaGatewayDraft:next}))!==true)throw Error();draftUnsafe.current=false;return true;}
  catch{draftUnsafe.current=true;setError('网关输入暂未可靠保存，请保留本页并重试保存人稿。');return false;}
 }
 function editForm(key,value){if(!safeGatewayField(key,value)){setError('地址只接受协议、IP 和端口；请勿输入账号、密码或查询参数。');return;}setError('');persistForm({...formRef.current,[key]:value});}
 function choose(id){if(!canLeaveAudio())return;if(formRef.current){setError('请先保存或放弃当前网关输入，再切换连接；人稿已保留。');return;}setSelected(id);setChecked([]);setAuthorized(false);setError('');}
 function beginForm(next){if(!canLeaveAudio())return;if(formRef.current){setError('已有未完成的网关输入，请先保存或放弃。');return;}setError('');persistForm(next);}
 function newGateway(){beginForm(newGatewayDraft());}
 async function commitAudioResource(value,verifyCurrent){
  verifyCurrent();if(!gateway||value.channel_id!==channels.find(c=>c.id===value.channel_id&&c.gateway_id===gateway.id)?.id)throw Error('通道或网关已变化，请重新读取原登记。');
  await onSave();verifyCurrent();const channel=channels.find(c=>c.id===value.channel_id),connectionID='media-gateway:'+gateway.id,id='media-channel:'+channel.id;
  const accepted=onCommit(current=>{
   verifyCurrent();const existing=current.resources.find(r=>r.id===id),connection=current.connections.find(c=>c.id===connectionID);
   const resource={...(existing||{}),id,category:'video',name:existing?.name||channel.name,connection:connectionID,
    protocol:existing?.protocol||'RTSP / 音轨',status:existing?.status||'candidate',businesses:existing?.businesses||[],
    caps:[...new Set([...(existing?.caps||[]),'音轨读取','本地声音分类'])],checks:{...(existing?.checks||{}),audio_registered:true},
    details:{...(existing?.details||{}),channel_id:channel.id,deployment:'local',audio:{enabled:true,source_capability_id:value.source_capability_id,model_capability_id:value.model_capability_id}}};
   return {...current,connections:[...current.connections.filter(c=>c.id!==connectionID),connection||{id:connectionID,category:'video',name:gateway.name,protocol:'MediaMTX / RTSP',endpoint:gateway.rtsp_origin,auth:'由运行节点管理',status:'read_verified',gateway_id:gateway.id}],
    resources:[...current.resources.filter(r=>r.id!==id),resource]};
  });
  if(accepted!==true)throw Error('音轨能力已登记，但业务资源关联尚未保存；原登记保留，请重试同步配置。');
  await onSave();verifyCurrent();setNotice('音轨能力关联已保存。请为此资源选择业务，并在自动化范围中确认允许自动观察，发布后可在业务工作区启动。');return true;
 }
 async function finishGateway(saved,original){
  if(saved?.registration_matches_current===false)throw Error('原申请对应的网关后来已被修改，请核对现有连接；原输入继续保留。');
  if(!saved||!saved.id||saved.name!==original.name.trim()||saved.api_origin!==original.api_origin.replace(/\/$/,'')||saved.rtsp_origin!==original.rtsp_origin.replace(/\/$/,''))throw Error('返回的网关与原申请不一致，请保留人稿并核对。');
  if(original.id){const id='media-gateway:'+saved.id;if(saved.id!==original.id)throw Error('返回的网关与原申请不一致，请保留人稿并核对。');
   if(!onCommit(current=>({...current,connections:(current.connections||[]).map(c=>c.id===id?{...c,endpoint:saved.rtsp_origin,status:'needs_validation'}:c),resources:(current.resources||[]).map(r=>r.connection===id?{...r,checks:{...r.checks,read_verified:false}}:r)})))throw Error('连接已修改，但资源关联未同步保存，请核对配置冲突。');await onSave();}
  if(!persistForm(null)){formRef.current=original;setForm(original);throw Error('连接已保存，但未能清除本机人稿；请先核对已保存的连接。');}try{await onSave();}catch(e){persistForm(original);throw Error('连接已保存，但配置同步未完成；原输入保留，请核对后重试。');}
  setSelected(saved.id);setChecked([]);setAuthorized(false);setNotice('连接已保存。请读取通道目录，再确认允许读取哪些通道。');
 }
 async function save(){if(!formRef.current||formRef.current.pending)return;await act(async()=>{
  const original={...formRef.current},pending={...original,pending:true};
  if(!persistForm(pending))throw Error('人稿尚未保存，未提交网关请求。');await onSave();
  const body={name:original.name,api_origin:original.api_origin,rtsp_origin:original.rtsp_origin,...(original.id?{version:original.version}:{request_id:original.request_id})};
  let saved;try{saved=original.id?await request('/gateways/'+original.id,body,'PUT'):await request('/gateway-registrations',body);}
  catch(e){if(e.status>=400&&e.status<500&&(![408,409,429].includes(e.status)||(original.id&&e.status===409))){persistForm({...original,pending:false});await onSave();throw e;}throw Error('原请求结果尚未确认，请先核对已保存连接；不会自动重复提交。');}
  await finishGateway(saved,original);
 });}
 async function recoverGateway(){if(!formRef.current?.pending)return;await act(async()=>{
  const original={...formRef.current};let saved;setRecoveryMissing(false);
  if(original.id){const rows=await json('/media/gateways');saved=rows.find(row=>row.id===original.id);if(saved?.version===original.version){setNotice('连接仍为原版本，可按原版本和原输入重试；若原请求稍后完成，版本检查会阻止重复修改。');setRecoveryMissing(true);return;}if(!saved||saved.version!==original.version+1||saved.name!==original.name.trim()||saved.api_origin!==original.api_origin||saved.rtsp_origin!==original.rtsp_origin)throw Error('目前无法确认原修改是否完成，输入已保留。请核对已保存连接及操作记录。');await finishGateway(saved,original);return;}
  try{saved=await json('/media/gateway-registrations/requests/'+encodeURIComponent(original.request_id));}
  catch(e){if(e.status===404){setNotice('服务器尚未找到该申请。可按原内容和原申请号安全重试。');setRecoveryMissing(true);return;}throw e;}
  await finishGateway(saved,original);setRecoveryMissing(false);
 });}
 async function retryOriginal(){if(!formRef.current?.pending||!recoveryMissing)return;await act(async()=>{
  const original={...formRef.current};setRecoveryMissing(false);await onSave();let saved;
  try{saved=original.id?await request('/gateways/'+original.id,{version:original.version,name:original.name,api_origin:original.api_origin,rtsp_origin:original.rtsp_origin},'PUT'):await request('/gateway-registrations',{request_id:original.request_id,name:original.name,api_origin:original.api_origin,rtsp_origin:original.rtsp_origin});}
  catch(e){throw Error(original.id?'原修改尚未确认，请再次核对连接；输入已保留。':'原申请尚未确认，请再次核对连接；输入已保留。');}
  await finishGateway(saved,original);
 });}
 async function discover(){await act(async()=>{const saved=await request('/gateways/'+gateway.id+'/discover',{version:gateway.version});setChecked([]);setAuthorized(false);if(saved.status==='failed')setError(saved.error);else setNotice('通道目录已保存；尚未自动添加或读取视频。');});}
 async function confirm(){await act(async()=>{await onSave();for(const path of checked){const old=associated.find(c=>c.path===path);if(old?.status==='disabled')await request('/channels/'+old.id+'/reconfirm',{version:old.version});else await request('/channels',{gateway_id:gateway.id,gateway_version:gateway.version,path,name:path,read_authorized:true});}setChecked([]);setAuthorized(false);setNotice('所选通道的读取授权已保存。下一步分别读取一小段视频，核对本地处理效果。');});}
 async function capture(channel){await act(async()=>{await onSave();await request('/channels/'+channel.id+'/capture',{channel_version:channel.version,installation_id:installation.id,seconds,configuration:{sample_hz:hz,threshold:.5,presence_rule:label==='none'?null:{label_id:Number(label),seconds:1}}});setNotice('读取请求与设置已保存，离开或刷新后可继续查看进度。此操作只读取一段视频。');});}
 async function registerLive(channel){await act(async()=>{await onSave();const connectionID='media-gateway:'+gateway.id,id='media-channel:'+channel.id;
  const connection={id:connectionID,category:'video',name:gateway.name,protocol:'MediaMTX / RTSP',endpoint:gateway.rtsp_origin,auth:'由运行节点管理',status:'read_verified',gateway_id:gateway.id};
  if(!onCommit(current=>{const previous=current.resources.find(r=>r.id===id);if(previous?.details?.capability_id&&!previous?.details?.live_readonly)return current;
   const resource={...(previous||{}),id,category:'video',name:previous?.name||channel.name,connection:connectionID,protocol:'RTSP / H.264',status:previous?.status||'candidate',caps:[...new Set([...(previous?.caps||[]),'实时画面'])],businesses:previous?.businesses||[],checks:{...(previous?.checks||{}),read_authorized:true},details:{...(previous?.details||{}),channel_id:channel.id,live_readonly:true,deployment:'local'}};
   return {...current,connections:[...current.connections.filter(c=>c.id!==connectionID),connection],resources:[...current.resources.filter(r=>r.id!==id),resource]};}))throw Error('实时通道关联未保存，请重试。');await onSave();setNotice('通道关联已保存。请审核并发布业务实例；是否在线以实时画面为准。此操作未验证模型、未保存录像。');});}
 async function register(channel){await act(async()=>{await onSave();const saved=await request('/channels/'+channel.id+'/register',{version:channel.version});const connectionID='media-gateway:'+gateway.id,id='media-channel:'+channel.id;
  const connection={id:connectionID,category:'video',name:gateway.name,protocol:'MediaMTX / RTSP',endpoint:gateway.rtsp_origin,auth:'由运行节点管理',status:'read_verified',gateway_id:gateway.id};
  const resource={id,category:'video',name:channel.name,connection:connectionID,protocol:'RTSP / H.264',status:'candidate',caps:['视频读取','本地目标检测'],businesses:[],checks:{read_verified:true,domain_evaluation:false},details:{capability_id:saved.capability_id,channel_id:channel.id,live_readonly:false,deployment:'local'}};
  if(!onCommit(current=>{const previous=current.resources.find(r=>r.id===id);const savedResource={...resource,...previous,details:{...previous?.details,...resource.details},checks:{...previous?.checks,...resource.checks},caps:[...new Set([...(previous?.caps||[]),...resource.caps])],businesses:previous?.businesses||[]};return {...current,connections:[...current.connections.filter(c=>c.id!==connectionID),connection],resources:[...current.resources.filter(r=>r.id!==id),savedResource]};}))throw Error('通道能力已登记，但资源关联未保存，请重试保存关联。');await onSave();setNotice('通道能力与资源关联已保存。设备身份、领域准确率和全天运行容量仍待验证。');});}
 return <section className="local-model-card media-studio"><h2>摄像头与视频网关</h2><p>连接 MediaMTX 网关并确认通道读取授权后，可以接入实时画面；需要分析时再用本地模型验证视频处理效果。</p>
 <OnvifDiscovery key={[account.workspace_id,account.user_id,account.role].join(':')} account={account} Btn={Btn} onEnrollmentGuard={registerAudioGuard}/>
 <div className="info">当前支持 RTSP/TCP 的 H.264 视频读取验证。每段读取 4—12 秒，支持按间隔读取和单路持续接收；不授予摄像头转动或其他设备控制权限。网关通道名称尚未核实为具体物理设备。</div>
 {error&&<p className="error" role="alert">{error}<Btn disabled={busy} onClick={()=>act(load)}>重新读取保存记录</Btn></p>}{notice&&<p role="status">{notice}</p>}
 {!initialValid&&<p className="error" role="alert">已保存的视频网关人稿格式无法核对，原人稿保留。请联系管理员处理，暂不能切换页面或覆盖。</p>}
 {loading?<p>正在读取连接与通道记录…</p>:<>
 <div className="actions"><Field label="选择视频网关"><select aria-label="选择视频网关" value={selected} onChange={e=>choose(e.target.value)}><option value="">请选择已保存连接</option>{gateways.map(g=><option key={g.id} value={g.id}>{g.name} · {statusText[g.status]}</option>)}</select></Field><Btn id="video-gateway-add" disabled={!admin||busy} onClick={newGateway}>添加视频网关</Btn></div>
 {form&&<div className="media-form"><h3>{form.id?'修改网关连接':'添加网关连接'}</h3><div className="local-model-fields">{[['name','网关名称'],['api_origin','通道目录地址'],['rtsp_origin','视频读取地址']].map(([key,title])=><Field key={key} label={title}><input aria-label={title} value={form[key]} disabled={busy||form.pending} onChange={e=>editForm(key,e.target.value)}/></Field>)}</div><p>地址填写协议、IP 和端口。访问地址须先由运维授权；读取凭据由运行节点保管，不填写在地址中。</p>{form.id&&<p className="warning-text">保存修改后，原通道将停用，需要重新读取目录并确认授权。此前视频和核查记录保留。</p>}{form.pending&&<p className="warning-text">原请求结果尚待核对，输入已保留，暂不能修改或放弃。</p>}<div className="actions">{draftUnsafe.current&&<Btn disabled={busy} onClick={()=>act(async()=>{if(!persistForm(formRef.current))throw Error('人稿仍未保存，请保留本页。');await onSave();})}>重试保存人稿</Btn>}{form.pending?<><Btn disabled={busy||draftUnsafe.current} onClick={recoverGateway}>核对原请求</Btn>{recoveryMissing&&<Btn disabled={busy||draftUnsafe.current} onClick={retryOriginal}>按原请求重试</Btn>}</>:<><Btn primary disabled={busy||draftUnsafe.current||!form.name.trim()||!form.api_origin||!form.rtsp_origin} onClick={save}>保存连接</Btn><Btn disabled={busy||draftUnsafe.current} onClick={()=>act(async()=>{const original=formRef.current;if(!persistForm(null)){formRef.current=original;setForm(original);throw Error('未能保存放弃操作，请保留本页。');}try{await onSave();}catch(e){persistForm(original);throw e;}})}>放弃本次输入</Btn></>}</div></div>}
 {gateway&&<><h3>{gateway.name}</h3><p>{statusText[gateway.status]} · {gateway.api_origin} · {gateway.rtsp_origin}</p><div className="actions"><Btn disabled={busy||!admin||!!form} onClick={discover}>{gateway.inventory?'重新读取通道目录':'读取通道目录'}</Btn><Btn disabled={busy||!admin||!!form} onClick={()=>beginForm(gatewayDraftForEdit(gateway))}>修改连接</Btn></div>
 {gateway.error&&<p className="error">{gateway.error}。已保存视频仍可查看。</p>}{gateway.inventory&&<><h3>选择允许读取的通道</h3><p>目录检查时间：{new Date(gateway.inventory.checked_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})}。在线状态以本次检查为准。</p>{!directory.length?<p>网关中还没有通道，请在网关添加视频来源后重新读取目录。</p>:directory.map(item=>{const old=associated.find(c=>c.path===item.path),added=old&&old.status!=='disabled';return <label className="media-channel-choice" key={item.path}><input type="checkbox" aria-label={'选择通道 '+item.path} disabled={!admin||busy||!!added} checked={!!added||checked.includes(item.path)} onChange={e=>setChecked(e.target.checked?[...checked,item.path]:checked.filter(x=>x!==item.path))}/><span>{item.path} · {item.ready?'有视频':'当前无视频'} · {item.tracks.join('、')||'编码未知'}{added?' · 已添加':''}</span></label>;})}{gateway.inventory.truncated&&<p>目录过大，只保存前 500 个通道；其余需进一步接入。</p>}
 {!!checked.length&&<><label className="media-channel-choice"><input aria-label="确认有权读取所选通道" type="checkbox" checked={authorized} disabled={busy||!admin} onChange={e=>setAuthorized(e.target.checked)}/>我确认有权读取所选通道，并允许在本地保存视频及分析结果</label><Btn primary disabled={busy||!admin||!authorized} onClick={confirm}>确认添加 {checked.length} 个通道</Btn></>}</>}
 {!!associated.length&&<><h3>下一次读取的处理设置</h3><p>用于下一次验证或新建后台读取。此前保存记录保留原设置；全部在本地处理。</p><div className="local-model-fields"><Field label="读取多少秒"><input aria-label="读取多少秒" type="number" min="4" max="12" step="1" value={seconds} onChange={e=>setSeconds(Number(e.target.value))}/></Field><Field label="视频通道每秒分析画面数"><select aria-label="视频通道每秒分析画面数" value={hz} onChange={e=>setHz(Number(e.target.value))}><option value="1">1张</option><option value="2">2张</option></select></Field><Field label="视频通道的报告条件"><select aria-label="视频通道的报告条件" value={label} onChange={e=>setLabel(e.target.value)}><option value="none">只提取信息，暂不报告线索</option><option value="0">人持续出现至少1秒</option><option value="2">汽车持续出现至少1秒</option><option value="15">猫持续出现至少1秒</option><option value="16">狗持续出现至少1秒</option></select></Field></div></>}
 {associated.map(channel=>{const records=sessions.filter(s=>s.channel_id===channel.id&&!s.continuous_id),active=records.find(s=>['queued','connecting','capturing'].includes(s.status))||monitors.find(m=>m.channel_id===channel.id&&['active','paused','blocked'].includes(m.status))||streams.find(m=>m.channel_id===channel.id&&['queued','recording','reconnecting','paused','blocked'].includes(m.status));return <article key={channel.id} className="local-sample"><h3>{channel.name} · {channel.status==='validated'&&!channel.validation_current?'处理版本变化，需重新验证':statusText[channel.status]}</h3><p>通道 {channel.path} · {channel.read_authorized?'已确认读取授权':'读取已停止'}</p><div className="actions">{channel.read_authorized&&['confirmed','validated'].includes(channel.status)&&<Btn disabled={busy||!admin||gateway.status!=='ready'} onClick={()=>registerLive(channel)}>接入实时画面</Btn>}<Btn primary disabled={busy||!operator||!channel.read_authorized||gateway.status!=='ready'||installation?.status!=='ready'||!!active||!Number.isInteger(seconds)||seconds<4||seconds>12} onClick={()=>capture(channel)}>{records.length?'重新读取一段视频验证':'读取一段视频验证'}</Btn>{channel.read_authorized&&<Btn disabled={busy||!admin} onClick={()=>act(()=>request('/channels/'+channel.id+'/disable',{version:channel.version}))}>停用通道</Btn>}{channel.status==='disabled'&&<Btn disabled={busy||!admin||gateway.status!=='ready'} onClick={()=>act(()=>request('/channels/'+channel.id+'/reconfirm',{version:channel.version}))}>重新确认读取授权</Btn>}{channel.validation_current&&<Btn disabled={busy||!admin} onClick={()=>register(channel)}>{channel.capability_id?'保存通道资源关联':'登记视频来源能力'}</Btn>}</div>
 {['confirmed','validated'].includes(channel.status)&&<AudioSourcePanel key={['audio-source',account.user_id,account.workspace_id,channel.id].join(':')} mode="configuration" {...{account,channel,gateway,Btn,Field}} onAudioResourceCommitted={commitAudioResource} onOpenSoundModels={onOpenSoundModels} onRegisterGuard={(guard,previous)=>registerAudioGuard(channel.id,guard,previous)}/>}<VideoReadBoundary channel={channel} seconds={seconds} Btn={Btn}/><VideoDomainStudio channel={channel} busy={busy} operator={operator} act={act} request={request} onSave={onSave} onViewVideo={onViewVideo} Btn={Btn} Field={Field}/><ContinuousStudio channel={channel} streams={streams} sessions={sessions} installation={installation} configuration={{sample_hz:hz,threshold:.5,presence_rule:label==='none'?null:{label_id:Number(label),seconds:1}}} seconds={seconds} busy={busy} operator={operator} act={act} request={request} onSave={onSave} onViewVideo={onViewVideo} Btn={Btn} Field={Field}/><MediaMonitorStudio channel={channel} monitors={monitors} sessions={sessions} installation={installation} configuration={{sample_hz:hz,threshold:.5,presence_rule:label==='none'?null:{label_id:Number(label),seconds:1}}} seconds={seconds} busy={busy} operator={operator} act={act} request={request} onSave={onSave} onViewVideo={onViewVideo} Btn={Btn} Field={Field}/>{!records.length?<p>还未读取视频。验证结果、原件和核查记录会保存到当前工作区。</p>:records.map(s=><div className="media-session" key={s.id}><p>{new Date(s.created_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})} · {statusText[s.status]}{s.processing_status?' · '+statusText[s.processing_status]:''} · 请求 {s.seconds} 秒</p>{s.progress&&['connecting','capturing'].includes(s.status)&&<p>已收到约 {s.progress.seconds} 秒视频，可停止本次读取。</p>}{s.receipt&&<p>已保存约 {s.receipt.duration_seconds.toFixed(1)} 秒 · 来源拍摄时间未校准 · 原视频{ s.receipt.audio_captured?'含音轨':'仅保存画面'}</p>}{s.error&&<p className="error">{s.error}</p>}{s.receipt?.error_code==='source_pts_missing'&&<p>来源中有画面缺少时间戳，已停止本段读取，不能假定前后连续。</p>}{s.status==='partial'&&<p className="warning-text">视频不完整，不能据此通过通道验证或认定没有异常。收到的画面仍可核查；可重新读取新片段。</p>}{s.attempt_history?.length>0&&<p>此前读取因任务中断而未保存完整片段；恢复后重新读取当前视频，不冒充连续录像。</p>}<div className="actions">{['queued','connecting','capturing'].includes(s.status)&&<Btn disabled={busy||!operator} onClick={()=>act(()=>request('/sessions/'+s.id+'/cancel',{version:s.version}))}>停止本次读取</Btn>}{s.video_run_id&&<Btn onClick={()=>onViewVideo(s.video_run_id)}>查看原视频与本地分析</Btn>}</div></div>)}</article>;})}</>}
 </>}
 <VideoDailyStudio operator={operator} account={account} onViewVideo={onViewVideo} Btn={Btn}/>
 </section>;
}
