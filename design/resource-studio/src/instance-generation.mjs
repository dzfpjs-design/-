import {checkedObjectTypeV170,verifyObjectTypeSnapshotV170} from './setup-object-proposals.mjs';
const schema='instance-generation-v1';
const plain=value=>!!value&&typeof value==='object'&&!Array.isArray(value);
const uuid=value=>typeof value==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value);
const sha=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
const issue='原生成请求或回执无法核对，原编号与内容保留，请核对服务器记录。';
const codepointOrder=(a,b)=>{const aa=[...a],bb=[...b];for(let i=0;i<Math.min(aa.length,bb.length);i++){const n=aa[i].codePointAt(0)-bb[i].codePointAt(0);if(n)return n;}return aa.length-bb.length;};
// Match Python json.dumps(ensure_ascii=False, sort_keys=True, separators=(',', ':')).
export function generationCanonical(value){
 if(Array.isArray(value))return '['+value.map(generationCanonical).join(',')+']';
 if(plain(value))return '{'+Object.keys(value).sort(codepointOrder).map(key=>generationCanonical(key)+':'+generationCanonical(value[key])).join(',')+'}';
 if(typeof value==='string'&&/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value))throw Error(issue);
 if(value===undefined||typeof value==='number'&&!Number.isSafeInteger(value))throw Error(issue);
 return JSON.stringify(value);
}
export async function generationHash(value){return [...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(generationCanonical(value))))].map(x=>x.toString(16).padStart(2,'0')).join('');}
// JavaScript trim differs from Python str.strip for BOM and several control characters.
const pythonTrim=value=>value.replace(/^[\u0009-\u000D\u001C-\u0020\u0085\u00A0\u1680\u2000-\u200A\u2028\u2029\u202F\u205F\u3000]+|[\u0009-\u000D\u001C-\u0020\u0085\u00A0\u1680\u2000-\u200A\u2028\u2029\u202F\u205F\u3000]+$/gu,'');
export function generationBody({revision,objects,role_bindings={},instance_id=null,confirmed_configuration_hash,request_id=crypto.randomUUID()}){
 const body={revision,objects:objects.map(object=>Object.hasOwn(object,'schema')?checkedObjectTypeV170(object):({name:pythonTrim(object.name),fields:object.fields.map(field=>({label:pythonTrim(field.label),kind:field.kind,required:!!field.required,unit:typeof field.unit==='string'?pythonTrim(field.unit)||null:null}))})),role_bindings:{...role_bindings},instance_id:instance_id||null,confirmed_configuration_hash,confirm_configuration:true,generation_intent:instance_id?'new_release':'new_instance',request_id:request_id.toLowerCase()};
 checkBody(body);return body;
}
function checkBody(body){
 if(!plain(body)||Object.keys(body).sort().join(',')!=='confirm_configuration,confirmed_configuration_hash,generation_intent,instance_id,objects,request_id,revision,role_bindings'||!uuid(body.request_id)||!sha(body.confirmed_configuration_hash)||!Number.isSafeInteger(body.revision)||body.revision<1||body.confirm_configuration!==true||!plain(body.role_bindings)||!Array.isArray(body.objects)||!['new_instance','new_release'].includes(body.generation_intent)||body.generation_intent==='new_instance'&&body.instance_id!==null||body.generation_intent==='new_release'&&(typeof body.instance_id!=='string'||!body.instance_id))throw Error(issue);
 for(const object of body.objects)if(plain(object)&&Object.hasOwn(object,'schema'))checkedObjectTypeV170(object);
 generationCanonical(body);return body;
}
export function generationScope(account){const scope={workspace_id:account?.workspace_id,actor_id:account?.user_id};if(Object.values(scope).some(value=>typeof value!=='string'||!value))throw Error('当前登录账号和工作区尚未核实。');return scope;}
export const generationScopeKey=scope=>[scope.workspace_id,scope.actor_id].map(encodeURIComponent).join(':');
const prefix=scope=>'ga-instance-generation:'+generationScopeKey(scope)+':';
export const generationKey=(scope,requestID)=>prefix(scope)+requestID;
const same=(a,b)=>generationCanonical(a)===generationCanonical(b);
// Release snapshots can contain finite decimal thresholds; they are compared as
// parsed JSON values, never used as the canonical request hash input.
function releaseCanonical(value){if(Array.isArray(value))return '['+value.map(releaseCanonical).join(',')+']';if(plain(value))return '{'+Object.keys(value).sort(codepointOrder).map(key=>generationCanonical(key)+':'+releaseCanonical(value[key])).join(',')+'}';if(typeof value==='number'){if(!Number.isFinite(value))throw Error(issue);return JSON.stringify(value);}return generationCanonical(value);}
export function requireGenerationIdentity(identity,scope,admin=false){if(identity?.workspace_id!==scope.workspace_id||identity?.user_id!==scope.actor_id)throw Error('当前登录账号或工作区已改变，原人稿与请求保留，请重新登录核对。');if(admin&&identity.role!=='admin')throw Error('当前账号已无生成权限；可以核对本人原回执，不能重新提交。');return identity;}
function checkedRecord(value,scope){
 if(!plain(value)||value.schema!==schema||!same(value.scope,scope)||!plain(value.body)||value.exact_body!==JSON.stringify(value.body)||!sha(value.body_hash)||!['pending','confirmed','saved'].includes(value.status)||!plain(value.selection)||typeof value.selection.name!=='string'||value.selection.instance_id!==value.body.instance_id||!plain(value.preview)||value.preview.revision!==value.body.revision||value.preview.configuration_hash!==value.body.confirmed_configuration_hash||!Number.isFinite(Date.parse(value.created_at)))throw Error(issue);
 checkBody(value.body);if(value.status==='pending'){if(value.receipt!==null||value.release!==null)throw Error(issue);}else{const receipt=value.receipt;if(!plain(receipt)||receipt.saved!==true||receipt.request_id!==value.body.request_id||receipt.request_hash!==value.body_hash||receipt.actor_id!==scope.actor_id||receipt.workspace_id!==scope.workspace_id||receipt.generation_intent!==value.body.generation_intent||receipt.configuration_hash!==value.body.confirmed_configuration_hash||value.body.instance_id!==null&&receipt.instance_id!==value.body.instance_id)throw Error(issue);verifyRelease(receipt,value.release,value.body);}return value;
}
export function readGeneration(storage,scope,requestID){const raw=storage.getItem(generationKey(scope,requestID));if(raw===null)return null;let value;try{value=checkedRecord(JSON.parse(raw),scope);}catch{throw Error(issue);}if(value.body.request_id!==requestID)throw Error(issue);return {raw,value};}
export function listGenerations(storage,scope){const rows=[];for(let i=0;i<storage.length;i++){const key=storage.key(i);if(key?.startsWith(prefix(scope))){const row=readGeneration(storage,scope,key.slice(prefix(scope).length));if(row)rows.push(row);}}return rows.sort((a,b)=>b.value.created_at.localeCompare(a.value.created_at)||b.value.body.request_id.localeCompare(a.value.body.request_id));}
function write(storage,journal,value,current=()=>{}){current();const key=generationKey(value.scope,value.body.request_id);if(storage.getItem(key)!==(journal?.raw??null))throw Error('另一页面已更新此生成请求，原记录保留，请重新读取。');checkedRecord(value,value.scope);const raw=JSON.stringify(value);storage.setItem(key,raw);if(storage.getItem(key)!==raw)throw Error('生成恢复记录尚未完整保存，未发送请求。');return {raw,value};}
export async function stageGeneration(storage,scope,body,{name='',preview}={},current=()=>{}){
 checkBody(body);const value={schema,scope:{...scope},body:JSON.parse(JSON.stringify(body)),exact_body:JSON.stringify(body),body_hash:await generationHash(body),selection:{instance_id:body.instance_id,name},preview:{revision:preview?.revision??body.revision,configuration_hash:preview?.configuration_hash??body.confirmed_configuration_hash},status:'pending',receipt:null,release:null,created_at:new Date().toISOString()};
 return write(storage,null,value,current);
}
export async function verifyGenerationReceipt(journal,response){
 const value=checkedRecord(journal.value,journal.value.scope),body=value.body;
 if(await generationHash(body)!==value.body_hash)throw Error(issue);
 const receipt=response?.generation_receipt||response,release=response?.generation_receipt?Object.fromEntries(Object.entries(response).filter(([key])=>key!=='generation_receipt')):response?.release;
 if(receipt?.saved===false){if(receipt.request_id!==body.request_id)throw Error(issue);return null;}
 if(receipt?.saved!==true||receipt.request_id!==body.request_id||receipt.request_hash!==value.body_hash||receipt.actor_id!==value.scope.actor_id||receipt.workspace_id!==value.scope.workspace_id||receipt.generation_intent!==body.generation_intent||typeof receipt.instance_id!=='string'||!receipt.instance_id||typeof receipt.release_id!=='string'||!receipt.release_id||receipt.configuration_hash!==body.confirmed_configuration_hash||!Number.isSafeInteger(receipt.release_number)||receipt.release_number<1||body.instance_id!==null&&receipt.instance_id!==body.instance_id)throw Error(issue);
 verifyRelease(receipt,release,body);return {receipt:Object.fromEntries(Object.entries(receipt).filter(([key])=>key!=='release')),release};
}
function verifyRelease(receipt,release,body){
 if(typeof receipt.instance_id!=='string'||!receipt.instance_id||typeof receipt.release_id!=='string'||!receipt.release_id||!Number.isSafeInteger(receipt.release_number)||receipt.release_number<1||!sha(receipt.configuration_hash)||!plain(release)||release.id!==receipt.release_id||release.instance_id!==receipt.instance_id||release.release_number!==receipt.release_number||release.configuration_hash!==receipt.configuration_hash||!['draft','prepared'].includes(release.status)||!plain(release.snapshot)||!Array.isArray(release.snapshot.businesses)||!Array.isArray(release.available_business_ids))throw Error(issue);
 const businesses=release.snapshot.businesses,ids=businesses.map(business=>business?.id);
 if(businesses.some(business=>!plain(business)||typeof business.id!=='string'||!business.id||typeof business.name!=='string'||!business.name||typeof business.available!=='boolean')||new Set(ids).size!==ids.length||release.available_business_ids.some(id=>typeof id!=='string'||!id||!businesses.some(business=>business.id===id&&business.available))||new Set(release.available_business_ids).size!==release.available_business_ids.length||businesses.some(business=>business.available&&!release.available_business_ids.includes(business.id)))throw Error(issue);
 const profile=release.snapshot.profile;if(profile!==undefined&&(!plain(profile)||typeof profile.name!=='string'||typeof profile.domain!=='string'))throw Error(issue);
 if(body)verifyObjectTypeSnapshotV170(body.objects,release.snapshot);
}
export function confirmGeneration(storage,journal,verified,current){return write(storage,journal,{...journal.value,status:'confirmed',...verified},current);}
export function settleGeneration(storage,journal,release,current){verifyRelease(journal.value.receipt,release,journal.value.body);if(releaseCanonical(release)!==releaseCanonical(journal.value.release))throw Error('服务器读回版本与原回执内容不一致，原请求保留。');return write(storage,journal,{...journal.value,status:'saved',release},current);}
