import React,{useEffect,useMemo,useRef,useState} from 'react';
import {json,api as binary} from './runtime-api.js';
import {PixelGatewayControllerV249} from './pixel-change-v249-controller.mjs';
import {pixelCanonical,pixelPins} from './pixel-change-v249-protocol.mjs';
import PixelAnnotationPanelV256 from './PixelAnnotationPanelV256';
import {createRetainedControllerLifecycle} from './retained-controller-lifecycle.mjs';
import PixelCapabilityRegistrationV250 from './PixelCapabilityRegistrationV250.jsx';
import './pixel-pair-v249.css';

const states={queued:'已保存，等待本地分析',running:'正在本地分析',succeeded:'分析完成，等待人工核对',failed:'本次分析失败，原件保留',cancelled:'本次分析已停止，原件保留'};
const installStates={queued:'安装请求已保存，等待准备',installing:'正在准备本地模型',ready:'本地工程检查已通过',failed:'本地模型准备失败',cancelled:'本地模型准备已停止'};
const instanceStates={partially_active:'已启用',paused:'已暂停',draft:'未启用'};
const artifacts={probability_data:['概率数据（NumPy）','npy'],probability_preview:['概率预览图（16位PNG）','png'],binary_mask:['变化候选掩膜','png'],candidate_overlay:['变化候选叠加图','png'],valid_mask:['有像素范围图','png'],manifest:['本次处理说明','json']};
const clock=value=>new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});
const noop=()=>{};
function EvidenceImage({url,label}){return <figure><figcaption>{label}</figcaption>{url?<img src={url} alt={label}/>:<p>尚未读取核对</p>}</figure>;}
function SelectedOriginal({controller,image,label,Btn}){
 const [url,setURL]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false),alive=useRef(true),owned=useRef('');
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;if(owned.current)URL.revokeObjectURL(owned.current);};},[image.id,image.sha256]);
 async function load(){setBusy(true);setError('');try{const raw=await controller.access.bytes(controller.imageBase+'/'+image.id+'/content',image);controller.check();if(!alive.current)return;const next=URL.createObjectURL(new Blob([raw],{type:'image/png'}));if(owned.current)URL.revokeObjectURL(owned.current);owned.current=next;setURL(next);}catch(e){if(alive.current)setError(e.message);}finally{if(alive.current)setBusy(false);}}
 return <><Btn disabled={busy} onClick={load}>核对并查看所选{label}原图</Btn>{error&&<p role="alert">{error}</p>}{url&&<EvidenceImage url={url} label={'所选'+label+'原图'}/>}</>;
}

function PixelEvidence({controller,run,Btn,onError}){
 const [urls,setURLs]=useState({}),[busy,setBusy]=useState(false),[error,setError]=useState(''),alive=useRef(true),owned=useRef([]);
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;for(const url of owned.current)URL.revokeObjectURL(url);owned.current=[];};},[run.id,run.version]);
 const objectURL=(raw,type)=>{const url=URL.createObjectURL(new Blob([raw],{type}));owned.current.push(url);return url;};
 async function preview(){setBusy(true);setError('');try{const entries=[];for(const side of ['before','after']){const bytes=await controller.binaryOriginal(run,side);if(!alive.current)return;entries.push([side,objectURL(bytes,'image/png')]);}if(run.result)for(const kind of ['candidate_overlay','binary_mask','probability_preview','valid_mask']){const bytes=await controller.binaryArtifact(run,kind);if(!alive.current)return;entries.push([kind,objectURL(bytes,'image/png')]);}controller.check();if(alive.current)setURLs(Object.fromEntries(entries));}catch(e){if(alive.current)setError(e.message);}finally{if(alive.current)setBusy(false);}}
 async function download(kind){setBusy(true);setError('');try{const original=['before','after'].includes(kind),raw=original?await controller.binaryOriginal(run,kind):await controller.binaryArtifact(run,kind);controller.check();if(!alive.current)return;const url=objectURL(raw,original?'image/png':run.result.artifacts.find(a=>a.kind===kind).media_type),link=document.createElement('a');link.href=url;link.download=original?run.sources[kind].name:'pixel-change-'+kind+'.'+artifacts[kind][1];document.body.appendChild(link);link.click();link.remove();}catch(e){if(alive.current)setError(e.message);}finally{if(alive.current)setBusy(false);}}
 return <section className="pixel-evidence" aria-label="两期原图与变化候选证据"><p>原图与结果文件须完整读回并核对内容后才显示或下载。概率图是模型输出，不代表识别准确率。</p>{error&&<p role="alert">{error}</p>}
  <div className="actions"><Btn disabled={busy} onClick={preview}>核对并查看原图与结果图</Btn>{['before','after'].map(side=><Btn key={side} disabled={busy} onClick={()=>download(side)}>下载{side==='before'?'A前期':'B后期'}原图</Btn>)}</div>
  {busy&&<p role="status">正在读取并核对文件内容…</p>}
  {Object.keys(urls).length>0&&<><div className="pixel-pair-images"><EvidenceImage url={urls.before} label="A · 前期原图"/><EvidenceImage url={urls.after} label="B · 后期原图"/></div>{run.result&&<div className="pixel-pair-images"><figure><figcaption>B原图上的变化候选</figcaption><div className="pixel-overlay"><img src={urls.after} alt="后期原图作为叠加底图"/><img src={urls.candidate_overlay} alt="模型变化候选透明叠加层"/></div></figure><EvidenceImage url={urls.binary_mask} label="变化候选掩膜（白色为候选）"/><EvidenceImage url={urls.probability_preview} label="变化概率预览（16位PNG）"/><EvidenceImage url={urls.valid_mask} label="有像素范围（不代表已去云）"/></div>}</>}
  {run.result&&<details><summary>下载全部结果文件</summary><div className="actions">{Object.entries(artifacts).map(([kind,[label]])=><Btn key={kind} disabled={busy} onClick={()=>download(kind)}>下载{label}</Btn>)}</div><p>概率数据保留原始数值；预览可能受浏览器对16位PNG的显示方式影响。处理说明作为文件下载。</p></details>}
 </section>;
}

function PairForm({c,v,account,Btn,run,upload}){
 const f=v.draft.form,options=v.options,editable=['admin','operator'].includes(account.role),locked=v.busy||!!v.pending||!!v.uploadPending||!editable;
 let currentPins=false;try{currentPins=!!options&&pixelCanonical(pixelPins(options))===pixelCanonical(v.draft.pins);}catch{}
 const enabled=options?.instance.status==='partially_active',ready=options?.installations.find(i=>i.ready),selectImage=(side,id)=>run(()=>c.chooseImage(side,id));
 return <section className="pixel-pair-form" aria-label="选择两期已对齐原图"><h4>选择两期已对齐原图</h4><p>仅接受8位RGB PNG，两图宽高相同、每边至少32像素，总计不超过1600万像素。原件按A前期、B后期顺序保存；系统不缩放、不配准、不补造像素。</p>
  {!enabled&&<p className="info">当前实例未启用。可以核对已有分析和原件；新上传、比较与失败重试须在实例启用后进行。</p>}
  {options?.reasons.map((reason,i)=><p key={i} role="status">{reason}</p>)}
  <fieldset disabled={locked}><legend>原件与前后顺序</legend><div className="pixel-pair-columns">{['before','after'].map(side=>{const label=side==='before'?'A前期':'B后期',selected=v.draft.images[side];return <div key={side}><label>{label}已保存原图<select aria-label={label+'已保存原图'} value={f[side+'_image_id']} onChange={e=>selectImage(side,e.target.value)}><option value="">请选择原图</option>{selected&&!options?.images.some(i=>i.id===selected.id)&&<option value={selected.id}>{selected.name}（原选择，当前目录尚未核对）</option>}{options?.images.map(image=><option key={image.id} value={image.id}>{image.name} · {image.width}×{image.height} · {clock(image.created_at)}</option>)}</select></label>
   <label className="pixel-file-label">上传{label}原PNG<input type="file" accept="image/png,.png" aria-label={'上传'+label+'原PNG'} disabled={!enabled||v.detached||!v.localSaved} onChange={e=>{const file=e.target.files?.[0];e.target.value='';if(file)upload(side,file);}}/></label>
   {selected&&<><p>{label}：{selected.name} · {selected.width}×{selected.height}像素 · {selected.size_bytes.toLocaleString('zh-CN')}字节</p><SelectedOriginal key={selected.id} controller={c} image={selected} label={label} Btn={Btn}/></>}</div>;})}</div>
   <label>变化候选阈值<input aria-label="变化候选阈值" type="text" inputMode="decimal" value={f.threshold_text} onChange={e=>run(()=>c.edit({threshold_text:e.target.value}))}/></label><p>固定默认0.5。可手动输入0与1之间、最多六位小数的数值；不会根据结果自动调参。</p>
   <label className="cap-option"><input aria-label="确认两图像素位置对应" type="checkbox" checked={f.pixel_correspondence_confirmed} onChange={e=>run(()=>c.edit({pixel_correspondence_confirmed:e.target.checked}))}/>我已核对两张图像素位置对应，同一位置表示同一处。</label>
   <label className="cap-option"><input aria-label="确认两图全部像素均有原始图像" type="checkbox" checked={f.full_image_present_confirmed} onChange={e=>run(()=>c.edit({full_image_present_confirmed:e.target.checked}))}/>我已核对两张图全部像素都有原始图像；这不表示已经筛除云、阴影或其它遮挡。</label>
  </fieldset>
  <p>本地模型：Changer R18 · CPU。{ready?'当前安装已核对，仍需人工评价识别效果。':'当前尚无通过核对的固定安装，请在上方准备模型。'}</p>
  {!currentPins&&<div className="info"><p>实例或模型版本与当前输入不一致。原图和阈值保留，不会自动换版本。</p><Btn disabled={locked||!v.localSaved||v.detached||!v.optionsCurrent} onClick={()=>run(()=>c.adoptCurrent())}>保留原图并采用当前版本</Btn></div>}
  {!v.localSaved&&<p role="alert">当前尝试输入尚未可靠保存；可继续修改或重试保存，暂不能提交或离开。</p>}
  <div className="actions"><Btn disabled={v.busy||!!v.pending||!!v.uploadPending} onClick={()=>run(()=>c.saveAgain())}>保存当前输入</Btn><Btn primary disabled={locked||!v.localSaved||v.detached||!currentPins||!v.optionsCurrent||!options?.can_create||!f.before_image_id||!f.after_image_id||!f.pixel_correspondence_confirmed||!f.full_image_present_confirmed||v.submitted} onClick={()=>run(()=>c.create())}>保存并开始本地比较</Btn></div>
  {v.submitted&&<p>这组条件已提交，请在下方原记录查看进度。没有再次开始同一分析。</p>}
 </section>;
}

function InstancePanel({c,v,account,Btn,run,upload,onRegisterGuard,onAnnotationGuard,annotationProps,onUseFlow}){
 const [annotationId,setAnnotationId]=useState(null);
 const focused=v.focused,pending=v.pending;
 return <>
  {v.error&&<p role="alert">{v.error}</p>}{v.notice&&<p role="status">{v.notice}</p>}
  {!v.authenticated?<p role="status">正在核对当前账号与所选实例，完成前隐藏保存的原图与输入。</p>:<>
   {v.stageFailedBeforePost&&<article role="alert"><p>原操作尚未在本机完整保存，未发送新的请求。请保留此页并重试保存；恢复时沿用原文和同一次操作。</p><Btn disabled={v.busy} onClick={()=>run(()=>c.repairLocalOriginals())}>重试保存原操作（不发送）</Btn></article>}
   {v.detached&&<article className="info"><h4>这份输入已作为独立副本保留</h4><p>另一页面可能已保存不同输入。先选定要继续使用的副本，恢复时会同时保留另一页当前稿，不会开始模型分析。</p><Btn disabled={v.busy||!!pending||!!v.uploadPending} onClick={()=>run(()=>c.retainDraft())}>另存当前输入副本</Btn></article>}
   {pending&&<article className="info" aria-label="原图像比较操作待核对"><h4>上次{pending.operation==='create'?'分析提交':pending.operation==='cancel'?'停止':'失败重试'}结果尚待核对</h4><p>原图、阈值和原操作已固定。尚未查到回执不能证明没有保存；离开或刷新后继续核对同一次操作。</p><div className="actions"><Btn disabled={v.busy} onClick={()=>run(()=>c.recover())}>只核对上次操作与进度</Btn><Btn disabled={v.busy||v.stageFailedBeforePost||!['admin','operator'].includes(account.role)} onClick={()=>run(()=>c.resend())}>明确按原操作重试</Btn></div><p>重试保持原文与原请求；若上次尚未保存，此按钮可能实际开始原分析、停止或重试。暂停实例不会放行新的分析。</p></article>}
   {v.uploadPending&&<article className="info" aria-label="原图上传待核对"><h4>{v.uploadPending.side==='before'?'A前期':'B后期'}原图上传结果尚待核对</h4><p>{v.uploadPending.name} · 原前后顺序已保留。近期目录找不到文件仍然不能证明没有上传。</p><div className="actions"><Btn disabled={v.busy} onClick={()=>run(()=>c.reconcileUpload())}>只核对已保存原图</Btn><Btn disabled={v.busy} onClick={()=>run(()=>c.retainUpload())}>保留上传记录，先选已有原图</Btn></div><label>明确重选同一原PNG并重传<input type="file" aria-label="重选原PNG并重传" accept="image/png,.png" disabled={v.busy||v.options?.instance.status!=='partially_active'} onChange={e=>{const file=e.target.files?.[0];e.target.value='';if(file)upload(v.uploadPending.side,file);}}/></label><p>原图上传接口不保证重复请求只保存一次。这里仅接受同一原件，不会自动重传或删除文件。</p></article>}
   <PairForm {...{c,v,account,Btn,run,upload}}/>
   {v.history.length>0&&<details><summary>恢复本机输入副本（{v.history.length}份）</summary>{v.history.map((item,i)=><article key={item.key}><p>副本{i+1} · A：{item.draft.images.before?.name||'未选'} · B：{item.draft.images.after?.name||'未选'} · 阈值 {item.draft.form.threshold_text}</p><Btn disabled={v.busy||!!pending||!!v.uploadPending} onClick={()=>run(()=>c.restoreDraft(item.key))}>保留两份原稿并使用副本{i+1}</Btn></article>)}</details>}
   {v.uploadHistory.length>0&&<details><summary>此前保留的上传信息</summary>{v.uploadHistory.map(row=><p key={row.local_id}>{row.side==='before'?'A前期':'B后期'} · {row.name} · 原件信息保留；是否成功以服务器原图目录读回为准。</p>)}</details>}
   <section aria-label="两期图像比较历史"><div className="section-top"><h4>已保存的比较记录</h4><Btn disabled={v.busy} onClick={()=>run(()=>c.refresh())}>刷新原件与处理进度</Btn></div>{!v.listCurrent&&<p>当前历史尚未完整读回，已保存记录继续保留。</p>}{v.listCurrent&&!v.runs.length&&<p>还没有已保存的两期图像比较。</p>}
    {v.runs.map(row=><article key={row.id} className="pixel-history-row"><p>{row.sources.before.name} → {row.sources.after.name} · {states[row.status]} · 第{row.generation}次处理 · {clock(row.created_at)}（北京时间）</p><Btn disabled={v.busy} onClick={()=>run(()=>c.focus(row.id))}>查看这组原图与结果</Btn></article>)}{v.nextCursor&&<Btn disabled={v.busy} onClick={()=>run(()=>c.more())}>继续读取更早记录</Btn>}
   </section>
   {focused&&<article className="pixel-focused" aria-label="当前两期图像比较"><h4>{focused.sources.before.name} → {focused.sources.after.name}</h4><p role="status">{states[focused.status]}</p><p>{focused.reason}</p><p>前后顺序固定 · 阈值 {focused.request.threshold_text} · 本地CPU · 模型安装第{focused.model_trace.generation}代</p>
    {focused.owner_id===account.user_id&&<div className="actions">{['queued','running'].includes(focused.status)&&<Btn disabled={v.busy||!!pending||!v.localSaved} onClick={()=>run(()=>c.command(focused.id,'cancel'))}>停止本次比较并保留原件</Btn>}{['failed','cancelled'].includes(focused.status)&&<Btn disabled={v.busy||!!pending||!v.localSaved||v.options?.instance.status!=='partially_active'} onClick={()=>run(()=>c.command(focused.id,'retry'))}>保留失败记录，按原图与参数重试</Btn>}</div>}
    {focused.result&&<><p className="info">{focused.result.candidate_pixels===0?'本次模型未检出变化候选，仍可能存在漏检':'本次模型检出变化候选像素，需结合原图人工核对。'}</p><dl className="pixel-result-numbers"><div><dt>候选像素</dt><dd>{focused.result.candidate_pixels.toLocaleString('zh-CN')}</dd></div><div><dt>占有像素范围</dt><dd>{focused.result.common_valid_pixels?((focused.result.candidate_pixels/focused.result.common_valid_pixels)*100).toLocaleString('zh-CN',{maximumFractionDigits:4})+'%':'无法计算'}</dd></div><div><dt>图像尺寸</dt><dd>{focused.result.width}×{focused.result.height}</dd></div><div><dt>缺少像素</dt><dd>{focused.result.unknown_pixels.toLocaleString('zh-CN')}</dd></div></dl><p>仅像素候选，没有地图坐标、地理面积或GeoJSON；未进行云和阴影筛选。结果不会更新对象状态，也不会自动启动业务或扩大模型权限。</p></>}
    <PixelEvidence key={focused.id+':'+focused.version} controller={c} run={focused} Btn={Btn}/>
    {focused.status==='succeeded'&&<div className="media-form"><h4>人工标注与模型核对</h4><p>对照前后原图标记变化、未变化和未知区域，保留新的标注版本。保存标注不会修改本次模型结果或自动采用模型。</p>{annotationId!==focused.id?<Btn onClick={()=>run(async()=>setAnnotationId(focused.id))}>打开原图并标注</Btn>:<><Btn onClick={()=>run(async()=>setAnnotationId(null))}>收起标注区，保留已保存原稿</Btn><PixelAnnotationPanelV256 key={'annotation:'+c.scope.instance_id+':'+focused.id} {...annotationProps} account={account} instanceId={c.scope.instance_id} run={focused} Btn={Btn} onRegisterGuard={onAnnotationGuard}/></>}</div>}
    {focused.status==='succeeded'&&<PixelCapabilityRegistrationV250 key={'registration:'+focused.id} account={account} instanceId={c.scope.instance_id} referenceRunId={focused.id} Btn={Btn} onRegisterGuard={onRegisterGuard} onUseFlow={onUseFlow}/>}
    <details><summary>本次分析与此前重试记录</summary>{focused.attempts.map(item=><p key={item.generation}>第{item.generation}次 · {states[item.status]}{item.error?' · '+item.error:''}</p>)}</details>
   </article>}
  </>}
 </>;
}

export default function PixelPairStudioV249({account,Btn,onSave=async()=>{},onGo,onRegisterGuard,api=json,readBinary=binary,storage=globalThis.localStorage,tabStorage=globalThis.sessionStorage,locks=globalThis.navigator?.locks}){
 const latest=useRef(),life=useRef();if(!life.current)life.current=createRetainedControllerLifecycle();let token='';try{token=tabStorage.getItem('ga-setup-csrf')||'';}catch{}
 const owner=pixelCanonical([account?.user_id,account?.workspace_id,account?.role,token]),[stored,setStored]=useState(null),[localError,setLocalError]=useState(''),fileWork=useRef(0),registrationGuard=useRef(null),annotationGuard=useRef(null);latest.current={owner,onRegisterGuard};
 const actions=useRef(null),[parentWork,setParentWork]=useState(null);if(!actions.current||actions.current.owner!==owner)actions.current={owner,count:0};const isInteractionBlocked=useMemo(()=>()=>latest.current.owner!==owner||actions.current.owner!==owner||actions.current.count>0,[owner]);
 const holder=useMemo(()=>{try{return {g:new PixelGatewayControllerV249({account,api,readBinary,storage,session:tabStorage,locks,current:()=>latest.current.owner===owner})};}catch(e){return {error:e.message};}},[owner,api,readBinary,storage,tabStorage,locks]),g=holder.g,v=stored?.owner===owner?stored.value:null;
 useEffect(()=>{setLocalError('');const guard=()=>latest.current.owner===owner&&fileWork.current===0&&!!g&&g.guard()&&(!registrationGuard.current||registrationGuard.current())&&(!annotationGuard.current||annotationGuard.current());latest.current.onRegisterGuard?.(guard);if(!g)return()=>latest.current.onRegisterGuard?.(null,guard);const lease=life.current.setup(g,owner);if(!lease)return;let mounted=true;g.onChange=value=>{if(mounted)setStored({owner,value});};setStored({owner,value:g.view()});const unload=e=>{if(!guard()){e.preventDefault();e.returnValue='';}};globalThis.addEventListener?.('beforeunload',unload);return()=>{mounted=false;latest.current.onRegisterGuard?.(null,guard);globalThis.removeEventListener?.('beforeunload',unload);life.current.cleanup(lease);};},[owner,g]);
 useEffect(()=>{if(g&&!g.started){g.started=true;g.start().catch(noop);}},[g]);
 useEffect(()=>{const timer=setInterval(()=>{if(!g||g.busy||g.child?.busy||fileWork.current)return;const current=g.view(),working=current.installations.some(row=>['queued','installing'].includes(row.status))||current.child?.runs.some(row=>['queued','running'].includes(row.status));if(working)g.refresh().catch(noop);},3000);return()=>clearInterval(timer);},[g]);
 async function run(fn){setLocalError('');const lease=actions.current;let entered=false;try{if(lease.count)throw Error('正在读取或切换当前记录，请等待后再操作。');if(registrationGuard.current&&!registrationGuard.current())throw Error('原能力登记尚未可靠保存，请先在登记区补存同一次操作。');if(annotationGuard.current&&!annotationGuard.current())throw Error('标注原稿或原申请尚未可靠保存，请先在标注区补存后继续。');lease.count++;entered=true;setParentWork({owner,value:true});await fn();}catch(e){if(latest.current.owner===owner)setLocalError(e.message);}finally{if(entered){lease.count--;if(actions.current===lease&&latest.current.owner===owner)setParentWork({owner,value:lease.count>0});}}}
 async function upload(side,file){fileWork.current++;try{await run(()=>g.child.upload(side,file));}finally{fileWork.current--;}}
 if(v?.denied||v?.child?.denied)return <section className="local-model-card" aria-label="图像比较范围已隐藏"><h3>已对齐图像对</h3><p role="alert">当前账号或工作区已变化，原图、输入与原操作已隐藏并保留。请重新登录后从原范围核对。</p></section>;
 const install=v?.installations[0],admin=account?.role==='admin';
 return <section className="local-model-card pixel-pair-studio" aria-label="已对齐图像对比较"><h3>已对齐图像对</h3><p>用本地Changer比较两期已对齐的RGB PNG。这里单独管理像素图像，不使用带地图坐标的影像配对流程。</p>
  {[holder.error,v?.error,localError].filter(Boolean).filter((e,i,a)=>a.indexOf(e)===i).map((error,i)=><p role="alert" key={i}>{error}</p>)}{v?.notice&&<p role="status">{v.notice}</p>}
  {!v?.authenticated?<p role="status">正在核对当前账号与可用实例…</p>:<>
   <section className="pixel-install" aria-label="两期比较本地模型准备"><h4>本地模型准备</h4><p>固定模型：Changer R18 · 本地CPU · 使用节点上已经准备的固定文件。工程检查通过不代表领域识别准确，城市公开样例也不能证明林业效果。</p>{install?<><p role="status">{installStates[install.status]||'正在核对安装状态'} · 第{install.generation}代</p>{install.error&&<p role="alert">{install.error}</p>}</>:<p>还没有本地安装记录。</p>}
    {(!install||['failed','cancelled'].includes(install.status))&&!v.installPending&&<Btn disabled={v.busy||!admin} onClick={()=>run(()=>g.install(onSave))}>{install?'按固定文件重试本地安装':'准备本地Changer模型'}</Btn>}
    {v.installPending&&<><p>上次模型准备请求尚待核对，原参数已保留；不会自动重复安装。</p><Btn disabled={v.busy||!admin} onClick={()=>run(()=>g.install(onSave,true))}>明确按原参数重试模型准备</Btn></>}
    {!admin&&<p>本地模型准备需要管理员办理，已有历史可以继续查看。</p>}<Btn disabled={v.busy||v.child?.busy} onClick={()=>run(()=>g.refresh())}>读取模型和实例最新状态</Btn>
   </section>
   <label>保存与查看比较的业务实例<select aria-label="两期图像比较业务实例" value={v.selected} disabled={v.busy||v.child?.busy} onChange={e=>{const id=e.target.value;run(()=>g.chooseInstance(id));}}><option value="">请选择业务实例</option>{v.selected&&!v.instances.some(row=>row.id===v.selected)&&<option value={v.selected}>原实例当前不可见，未替换</option>}{v.instances.map(row=><option key={row.id} value={row.id}>{row.name} · {row.domain} · {instanceStates[row.status]||'当前不可新执行'}</option>)}</select></label>
   {!v.instances.some(row=>row.status==='partially_active')&&<p>先完成配置并启用业务实例，再试用两期图像比较。暂停实例仍可选择查看原记录。</p>}
   {!v.selected&&<p>请明确选择保存本次原图和结果的实例；不会自动选用第一个业务。</p>}
   {v.child&&g.child&&<InstancePanel c={g.child} v={v.child} account={account} Btn={Btn} run={run} upload={upload} onRegisterGuard={(guard,previous)=>{if(guard)registrationGuard.current=guard;else if(!previous||registrationGuard.current===previous)registrationGuard.current=null;}} onAnnotationGuard={(guard,previous)=>{if(guard)annotationGuard.current=guard;else if(!previous||annotationGuard.current===previous)annotationGuard.current=null;}} annotationProps={{request:api,readBinary,storage,tabStorage,locks,interactionBlocked:parentWork?.owner===owner&&parentWork.value,isInteractionBlocked}} onUseFlow={onGo?()=>onGo(1):undefined}/>}
  </>}
 </section>;
}
