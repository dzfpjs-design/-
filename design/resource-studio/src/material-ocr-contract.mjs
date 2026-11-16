export const ocrStatusLabel=value=>({queued:'等待本地识别',running:'正在本地识别',review:'识别已保存，等待核对',confirmed:'文字已确认',failed:'本地识别失败',blocked:'需要补充本地识别条件',cancelled:'已取消未完成识别'})[value]||'状态待核对';
export const ocrWriteAllowed=(account,permission)=>['admin','operator'].includes(account?.role)&&permission?.allowed===true;
export function ocrScopeKey(account,materialID){if(!account?.workspace_id||!account?.username||!materialID)return null;return 'ga-material-ocr:'+encodeURIComponent(account.workspace_id)+':'+encodeURIComponent(account.username)+':'+encodeURIComponent(materialID);}
export function canonicalOCR(value){if(Array.isArray(value))return '['+value.map(canonicalOCR).join(',')+']';if(value&&typeof value==='object')return '{'+Object.keys(value).sort().map(key=>JSON.stringify(key)+':'+canonicalOCR(value[key])).join(',')+'}';return JSON.stringify(value);}
export const sameOCRValue=(a,b)=>canonicalOCR(a)===canonicalOCR(b);
export function visibleOCRPoints(points,width,height){if(!Number.isFinite(width)||!Number.isFinite(height)||width<=0||height<=0||!Array.isArray(points)||points.length<3)return null;if(points.some(p=>!Array.isArray(p)||p.length!==2||!p.every(Number.isFinite)||p[0]<0||p[0]>width||p[1]<0||p[1]>height))return null;return points.map(p=>[...p]);}
export function ocrEngineScore(score){return Number.isFinite(score)&&score>=0&&score<=1?score:null;}
export function newerOCR(current,incoming){if(current&&incoming&&Number.isInteger(current.version)&&Number.isInteger(incoming.version)&&incoming.version<current.version)return current;return incoming;}
export function reconcileOCRDraft(local,current,submitted){if(submitted&&sameOCRValue(local,submitted)&&sameOCRValue(current,submitted))return {value:local,dirty:false,changed:false};return {value:local,dirty:true,changed:!!submitted&&!sameOCRValue(current,submitted)};}

const sha=/^[0-9a-f]{64}$/;const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const ocrBase=id=>'/setup/materials/'+encodeURIComponent(id)+'/ocr';
export function ocrCommandPath(materialID,pending){const base=ocrBase(materialID),id=pending.unit_id&&encodeURIComponent(pending.unit_id);switch(pending.action){case 'start':case 'retry':case 'cancel':case 'resume_with_profile':return base+'/'+pending.action;case 'document_confirm':return base+'/confirm';case 'save':return base+'/units/'+id;case 'unit_confirm':return base+'/units/'+id+'/confirm';case 'restore':return base+'/units/'+id+'/restore';default:throw Error('原OCR请求操作无法核对。');}}
export function checkedOCRBox(box,width,height){return Array.isArray(box)&&box.length===4&&box.every(Number.isFinite)&&box[0]>=0&&box[1]>=0&&box[2]>0&&box[3]>0&&box[0]+box[2]<=width&&box[1]+box[3]<=height;}
export function checkOCROverview(value,materialID){if(value?.material?.id!==materialID||!Number.isInteger(value.material.version)||!value.permissions||!value.coverage||!value.engine)throw Error('资料OCR进度无法核对，保留已有记录。');if(value.engine.ready&&!sha.test(value.engine.manifest_hash||''))throw Error('本地引擎摘要未完整登记，不能开始识别。');if(value.run&&(!Number.isInteger(value.run.version)||!value.run.progress||value.run.source_sha256!==value.material.hash))throw Error('识别进度与原件来源不一致，暂不能继续。');return value;}
export function newerOCROverview(current,incoming){if(!current)return incoming;if(incoming.material.version<current.material.version)return current;if(incoming.material.version===current.material.version&&current.run?.id===incoming.run?.id&&(incoming.run?.version||0)<(current.run?.version||0))return current;return incoming;}
export function checkOCRUnit(value,materialID,runID,unitID){if(value?.material_id!==materialID||value.run_id!==runID||value.unit?.id!==unitID||!Number.isInteger(value.unit.version)||value.original?.sha256!==value.unit.image_sha256||!sha.test(value.original?.sha256||'')||value.original.coordinate_space!=='pixel_top_left'||value.original.media_type!=='image/png'||!(value.unit.width>0&&value.unit.height>0)||value.original.width!==value.unit.width||value.original.height!==value.unit.height)throw Error('识别页与原图范围无法核对，未作为核查依据。');for(const version of [value.raw,value.draft,value.confirmed].filter(Boolean))checkOCRVersion(version,value.unit);return value;}
export function checkOCRVersion(version,unit){if(!sha.test(version?.hash||'')||!Array.isArray(version.regions)||version.regions.length>500||new Set(version.regions.map(r=>r.id)).size!==version.regions.length||version.regions.reduce((n,r)=>n+(typeof r.text==='string'?r.text.length:20001),0)>20000||version.regions.some(r=>typeof r.text!=='string'||!checkedOCRBox(r.box,unit.width,unit.height)||r.score!==null&&ocrEngineScore(r.score)===null))throw Error('已保存文字或位置无法核对，未覆盖当前编辑。');return version;}
export function editableOCRRegions(version){return (version?.regions||[]).map(r=>({id:r.id,text:r.text,box:[...r.box],source_region_id:version.kind==='raw'?r.id:r.source_region_id??null}));}
export function checkEditedOCRRegions(regions,detail){if(!Array.isArray(regions)||regions.length>500||new Set(regions.map(r=>r.id)).size!==regions.length||regions.reduce((n,r)=>n+(typeof r.text==='string'?r.text.length:20001),0)>20000||regions.some(r=>!r.id||typeof r.text!=='string'||!checkedOCRBox(r.box,detail.unit.width,detail.unit.height)))throw Error('文字或区域超出原图范围及本页容量，请核对后保存。');const required=new Set((detail.raw?.regions||[]).map(r=>r.id));for(const r of regions)required.delete(r.source_region_id);if(required.size)throw Error('不能删除原识别区域；不采用的文字请留空并写明原因，原识别保留。');return regions;}
export function newOCRCache(){return {schema:1,selected_unit:null,drafts:{},pending:null,rejections:[]};}
export function readOCRCache(storage,key){if(!key)throw Error('当前账号或资料尚未确定，恢复记录无法核对。');let text;try{text=storage.getItem(key);}catch{throw Error('无法读取本机文字恢复记录，请检查浏览器存储。');}if(!text)return newOCRCache();let data;try{data=JSON.parse(text);}catch{throw Error('本机文字恢复记录已损坏，不能覆盖或另起请求。');}if(data?.schema!==1||!data.drafts||typeof data.drafts!=='object'||!Array.isArray(data.rejections))throw Error('本机文字恢复记录结构无法核对。');for(const draft of Object.values(data.drafts))if(!draft||typeof draft.run_id!=='string'||!sha.test(draft.parent_hash||'')||!Number.isInteger(draft.unit_version)||!Array.isArray(draft.regions)||draft.regions.length>500||draft.regions.some(r=>typeof r?.text!=='string'||!Array.isArray(r.box)||r.box.length!==4||!r.box.every(Number.isFinite)))throw Error('本机改稿记录无法核对，未覆盖原记录。');if(data.pending)checkOCRPending(data.pending);return data;}
export function checkOCRPending(p){if(!['start','save','unit_confirm','restore','document_confirm','retry','cancel','resume_with_profile'].includes(p?.action)||!uuid.test(p.body?.request_id||'')||!Number.isInteger(p.body?.expected_material_version)||(['save','unit_confirm','restore'].includes(p.action)&&!p.unit_id))throw Error('原OCR请求无法核对，不能换编号重送。');if(p.action==='resume_with_profile'){const b=p.body;if(p.unit_id!=null||Object.keys(b).sort().join(',')!=='engine_id,engine_manifest_hash,expected_material_version,expected_run_version,reason,request_id,run_id'||b.expected_material_version<1||!uuid.test(b.run_id||'')||!Number.isInteger(b.expected_run_version)||b.expected_run_version<1||typeof b.reason!=='string'||!b.reason.trim()||b.reason.length>2000||typeof b.engine_id!=='string'||!b.engine_id||b.engine_id.length>100||!sha.test(b.engine_manifest_hash||''))throw Error('原低内存识别请求无法完整核对，保留原编号与正文，未发送。');}return p;}
export function writeOCRCache(storage,key,data){if(!key)throw Error('当前账号和资料未确定，不能保存恢复记录。');if(data.pending)checkOCRPending(data.pending);try{storage.setItem(key,JSON.stringify(data));}catch{throw Error('无法保存本机恢复记录；当前文字保留，请求尚未发送，请检查浏览器存储。');}return data;}
export function withoutPendingOCR(cache,pending){if(!cache.pending||!sameOCRValue(cache.pending,pending))return cache;return {...cache,pending:null};}
export const ocrCASRejected=error=>error?.status===409&&error.message==='资料或识别版本已更新，本次请求未提交。请重新核对。';

export function ocrImagePoint(clientX,clientY,rect,width,height){if(!rect||![clientX,clientY,rect.left,rect.top,rect.width,rect.height,width,height].every(Number.isFinite)||rect.width<=0||rect.height<=0||width<=0||height<=0)throw Error('原图显示范围无法核对。');const scale=Math.min(rect.width/width,rect.height/height),left=rect.left+(rect.width-width*scale)/2,top=rect.top+(rect.height-height*scale)/2;return [Math.max(0,Math.min(width,(clientX-left)/scale)),Math.max(0,Math.min(height,(clientY-top)/scale))];}

export const needsOCRSavedDraft=detail=>!!detail?.raw&&!detail?.draft&&!detail?.confirmed;

export function ocrRegionViewBox(box,width,height,padding=12){if(!checkedOCRBox(box,width,height)||!Number.isFinite(padding)||padding<0)return null;const left=Math.max(0,box[0]-padding),top=Math.max(0,box[1]-padding),right=Math.min(width,box[0]+box[2]+padding),bottom=Math.min(height,box[1]+box[3]+padding);return [left,top,right-left,bottom-top];}
export const sameOCRImageScope=(image,materialID,runID,unit)=>!!image&&image.materialID===materialID&&image.runID===runID&&image.unitID===unit?.id&&image.sha256===unit?.image_sha256&&image.width===unit?.width&&image.height===unit?.height;

export function ocrMaterialPresentation(material){const ocr=material?.ocr,confirmed=material?.status==='parsed'&&ocr?.coverage_status==='complete'&&ocr?.run_status==='confirmed'&&typeof ocr.run_id==='string'&&ocr.run_id.length>0&&Number.isInteger(ocr.total)&&ocr.total>0&&ocr.confirmed===ocr.total,warnings=Array.isArray(material?.warnings)?material.warnings:[];return {confirmed,statusLabel:confirmed?'资料文字已确认 · 正文解析完成':({queued:'等待解析',parsing:'正在解析',parsed:'正文解析完成',resource_ready:'已识别影像',needs_input:'需要补充解析能力',failed:'解析失败',cancelled:'已取消解析'})[material?.status]||'资料状态待核对',currentError:confirmed?null:material?.error||null,currentWarnings:confirmed?[]:warnings,currentOCRReason:confirmed?null:ocr?.reason||null,historyMessages:confirmed?[...new Set([material?.error,...warnings,ocr?.reason].filter(Boolean))]:[]};}
export async function readOCRMaterialStatus(load,isCurrent){const rows=await load('/setup/materials?limit=500');if(!isCurrent())return null;if(!Array.isArray(rows)||rows.some(row=>!row?.id||!Number.isInteger(row.version)))throw Error('资料状态读取范围无法核对，已有清单保留。');return rows;}
export function newerOCRMaterials(current,incoming){const before=new Map(current.map(item=>[item.id,item]));return incoming.map(item=>before.get(item.id)?.version>item.version?before.get(item.id):item);}


// A new explicit action; old retry/start bodies and frozen pending records stay unchanged.
function legacyOCRLowMemoryOption(view){
 const engine=view?.low_memory_engine,permission=view?.permissions?.resume_with_profile;
 if(!engine)return {allowed:false,reason:'当前服务尚未提供较少内存的继续识别方式；原进度与人工稿保留。',engine:null};
 if(engine.ready!==true)return {allowed:false,reason:engine.reason||'较少内存的本地识别条件尚未就绪；原进度与人工稿保留。',engine};
 if(typeof engine.id!=='string'||!engine.id||engine.id.length>100||!sha.test(engine.manifest_hash||'')||!sha.test(engine.summary_hash||'')||!engine.manifest||typeof engine.manifest!=='object'||Array.isArray(engine.manifest)||engine.manifest.manifest_hash!==engine.manifest_hash||engine.manifest.id!==engine.id)return {allowed:false,reason:'较少内存的本地配置无法完整核对；原进度与人工稿保留。',engine};
 return {allowed:permission?.allowed===true,reason:permission?.allowed===true?null:permission?.reason||'当前识别状态不能切换继续方式；原进度与人工稿保留。',engine};
}
const profileChoice=value=>!!value&&typeof value.id==='string'&&!!value.id&&value.id.length<=100&&(value.manifest_hash===null||sha.test(value.manifest_hash||''));
export function ocrResumeProfileChoices(view){
 const legacy=view?.resume_profiles===undefined,entries=legacy?(view?.low_memory_engine?[{engine:view.low_memory_engine,permission:view.permissions?.resume_with_profile}]:[]):view.resume_profiles;
 const bad=()=>({items:[],defaultSelection:null,reason:'继续识别方式的清单无法完整核对；保留原选择、原请求与人工稿，请刷新后重新核对。'});
 if(!Array.isArray(entries)||entries.length>8)return bad();
 const ids=new Set(),hashes=new Set();
 for(const entry of entries){const engine=entry?.engine,permission=entry?.permission;if(!profileChoice(engine)||typeof engine.ready!=='boolean'||typeof permission?.allowed!=='boolean'||permission.reason!=null&&typeof permission.reason!=='string'||ids.has(engine.id))return bad();if(engine.ready&&(!sha.test(engine.manifest_hash||'')||!sha.test(engine.summary_hash||'')||!engine.manifest||typeof engine.manifest!=='object'||Array.isArray(engine.manifest)||engine.manifest.id!==engine.id||engine.manifest.manifest_hash!==engine.manifest_hash||hashes.has(engine.manifest_hash)))return bad();ids.add(engine.id);if(engine.ready)hashes.add(engine.manifest_hash);}
 const original=view?.low_memory_engine,defaultEntry=entries.find(entry=>entry.engine.id===original?.id&&entry.engine.manifest_hash===original?.manifest_hash);
 return {items:entries,defaultSelection:defaultEntry?{id:defaultEntry.engine.id,manifest_hash:defaultEntry.engine.manifest_hash}:null,reason:null};
}
export function ocrLowMemoryOption(view,selection){
 // Preserve old DTO behavior and the exact old command shape when no list exists.
 if(view?.resume_profiles===undefined&&selection===undefined)return legacyOCRLowMemoryOption(view);
 const choices=ocrResumeProfileChoices(view);if(choices.reason)return {allowed:false,reason:choices.reason,engine:null};
 const selected=selection===undefined?choices.defaultSelection:selection;
 if(!profileChoice(selected))return {allowed:false,reason:'请明确核对并选择一种本地继续识别方式；不会自动替换原选择或原请求。',engine:null};
 const entry=choices.items.find(item=>item.engine.id===selected.id&&item.engine.manifest_hash===selected.manifest_hash);
 if(!entry)return {allowed:false,reason:'原选择的本地识别版本已变化或不在当前清单；请重新核对并明确选择，原请求不会改签。',engine:null};
 return legacyOCRLowMemoryOption({low_memory_engine:entry.engine,permissions:{resume_with_profile:entry.permission}});
}

async function ocrDigest(value){const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(canonicalOCR(value)));return Array.from(new Uint8Array(bytes),n=>n.toString(16).padStart(2,'0')).join('');}
export async function ocrResumeWithProfileBody(view,reason,selection){
 const option=ocrLowMemoryOption(view,selection);if(!option.allowed)throw Error(option.reason);
 if(!view?.run||!uuid.test(view.run.id||'')||!Number.isInteger(view.run.version)||view.run.version<1)throw Error('当前识别范围无法核对，原进度与人工稿保留。');
 if(typeof reason!=='string'||!reason.trim()||reason.length>2000)throw Error('请填写不超过2000字的继续识别说明。');
 if(await ocrDigest(option.engine.manifest)!==option.engine.summary_hash)throw Error('较少内存的本地配置摘要无法核对，未发送。');
 return {run_id:view.run.id,expected_run_version:view.run.version,reason,engine_id:option.engine.id,engine_manifest_hash:option.engine.manifest_hash};
}
export async function verifyOCRResumeReceipt(reply,original){
 checkOCRPending(original);if(original.action!=='resume_with_profile')throw Error('原继续识别操作无法核对。');
 const request=reply?.request;
 if(!request||request.id!==original.body.request_id||request.action!==original.action||!['saved','abandoned'].includes(request.state)||reply.state!==undefined&&reply.state!==request.state||request.body_hash!==await ocrDigest({action:original.action,target:null,body:original.body})||!reply.view||reply.unit!=null||request.state==='saved'&&request.result?.run_id!==original.body.run_id)throw Error('原继续识别回执与冻结正文无法核对，原请求保留。');
 return reply;
}
