import React,{useEffect,useRef,useState} from 'react';
import {json} from './runtime-api';

const blank={mode:'point',longitude:'',latitude:'',west:'',south:'',east:'',north:''};
function readable(value){
 if(!value||!['point','bounds'].includes(value.mode))return {...blank};
 const out={...blank,mode:value.mode};
 for(const key of ['longitude','latitude','west','south','east','north']){
  if(typeof value[key]==='string'&&value[key].length<=24)out[key]=value[key];
 }
 return out;
}
function setupStorage(account,connection){
 const prefix=`wmts-position:${account.workspace_id}:${account.user_id}:${connection.id}:`, anchor=prefix+'current';
 try{
  const prior=sessionStorage.getItem(anchor);let form={...blank};
  if(prior?.startsWith(prefix))form=readable(JSON.parse(localStorage.getItem(prior)||'null'));
  // Each document writes a new key, including when a tab was duplicated.
  const key=prefix+crypto.randomUUID();localStorage.setItem(key,JSON.stringify(form));sessionStorage.setItem(anchor,key);
  return {key,anchor,prefix,form,error:''};
 }catch{return {key:'',anchor,prefix,form:{...blank},error:'浏览器不能保存位置输入，请检查存储权限后重试。'};}
}
function decimal(text){
 if(typeof text!=='string'||!/^[-+]?\d{1,3}(?:\.\d{1,12})?$/.test(text.trim()))throw Error('请输入有效的十进制度数');
 const n=Number(text);if(!Number.isFinite(n))throw Error('请输入有效的十进制度数');return n;
}

/**
 * Selects candidate row/column only; never reads or saves a map.
 * Parent must key this component by workspace/user/connection ID and pass
 * onGuard into its existing leave guard (see the component tests).
 */
export default function WmtsPositionPicker({account,connection,selection,disabled,Btn,Field,onSelected,onGuard,request=json}){
 const [storage]=useState(()=>setupStorage(account,connection));
 const [form,setForm]=useState(storage.form),[error,setError]=useState(storage.error),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false);
 const busyRef=useRef(false),storageFailed=useRef(!!storage.error),alive=useRef(true),abort=useRef(null),callback=useRef(onGuard);
 const binding=useRef(null);callback.current=onGuard;
 binding.current={workspace:account.workspace_id,id:connection.id,version:connection.version,layer:selection.layer,matrix_set:selection.matrix_set,matrix:selection.matrix};
 useEffect(()=>{
  alive.current=true;const guard=()=>!storageFailed.current&&!busyRef.current;callback.current?.(guard);
  const before=e=>{if(!guard()){e.preventDefault();e.returnValue='';}};window.addEventListener('beforeunload',before);
  return()=>{alive.current=false;abort.current?.abort();callback.current?.(null,guard);window.removeEventListener('beforeunload',before);};
 },[]);
 function edit(key,value){
  if(busyRef.current)return;
  const next={...form,[key]:value};setForm(next);setNotice('');
  try{if(!storage.key)throw Error();localStorage.setItem(storage.key,JSON.stringify(readable(next)));storageFailed.current=false;setError('');}
  catch{storageFailed.current=true;setError('位置输入尚未保存，请检查浏览器存储后再离开。');}
 }
 function retrySave(){
  try{if(!storage.key)storage.key=storage.prefix+crypto.randomUUID();localStorage.setItem(storage.key,JSON.stringify(readable(form)));sessionStorage.setItem(storage.anchor,storage.key);storageFailed.current=false;setError('');setNotice('位置输入已保存在本浏览器。');}
  catch{storageFailed.current=true;setError('位置输入仍无法保存，请检查浏览器存储权限后重试。');}
 }
 async function calculate(){
  if(busyRef.current||storageFailed.current)return;
  try{
   const fixed={...binding.current},body={version:fixed.version,layer:fixed.layer,matrix_set:fixed.matrix_set,matrix:fixed.matrix};
   if(form.mode==='point')Object.assign(body,{longitude:decimal(form.longitude),latitude:decimal(form.latitude)});
   else body.bounds=['west','south','east','north'].map(k=>decimal(form[k]));
   busyRef.current=true;setBusy(true);setError('');setNotice('');const controller=new AbortController();abort.current=controller;
   const result=await request(`/map-services/connections/${encodeURIComponent(fixed.id)}/locate`,{method:'POST',headers:{'X-Workspace-ID':fixed.workspace},body:JSON.stringify(body),signal:controller.signal});
   if(!alive.current)return;
   const now=binding.current;
   if(Object.keys(fixed).some(k=>fixed[k]!==now[k]))throw Error('地图或矩阵已变化，请重新选择位置');
   if(!result||result.connection_id!==fixed.id||result.version!==fixed.version||result.layer!==fixed.layer||result.matrix_set!==fixed.matrix_set||result.matrix!==fixed.matrix||(connection.catalog_sha256&&result.catalog_sha256!==connection.catalog_sha256))throw Error('位置结果与当前地图不一致，请重新核对');
   const row=result.kind==='point'?result.row:result.min_row,col=result.kind==='point'?result.col:result.min_col;
   if(!Number.isSafeInteger(row)||row<0||!Number.isSafeInteger(col)||col<0)throw Error('服务返回的瓦片行列无效');
   if(result.kind==='bounds'){
    const rows=result.max_row-row+1,cols=result.max_col-col+1;
    if(!Number.isSafeInteger(result.tile_count)||result.tile_count<1||!Number.isSafeInteger(result.max_row)||!Number.isSafeInteger(result.max_col)||rows<1||cols<1||!Number.isSafeInteger(rows*cols)||rows*cols!==result.tile_count)throw Error('服务返回的范围无效');
    if(result.tile_count>16)throw Error('范围超过 16 张瓦片，请缩小范围或选择更低级别。');
   }else if(result.kind!=='point')throw Error('服务返回的位置类型无效');
   if(onSelected(result.kind==='bounds'?{row:String(row),col:String(col),max_row:String(result.max_row),max_col:String(result.max_col)}:{row:String(row),col:String(col),max_row:undefined,max_col:undefined})===false)throw Error('瓦片位置未能保存，原瓦片选择保留；请恢复浏览器存储后重试。');setNotice(result.kind==='bounds'?`已选择 ${result.tile_count} 张瓦片，确认后拼接保存。`:`已定位到第 ${row} 行、第 ${col} 列。请核对后确认读取。`);
  }catch(e){if(alive.current)setError(e?.message||'无法计算位置，请重新核对。');}
  finally{if(alive.current){busyRef.current=false;setBusy(false);abort.current=null;}}
 }
 const input=(field,label)=><Field label={label}><input aria-label={label} inputMode="decimal" maxLength={24} disabled={disabled||busy} value={form[field]} onChange={e=>edit(field,e.target.value)}/></Field>;
 return <section className="local-sample" aria-label="按位置选择瓦片">
  <h4>按位置选择瓦片</h4><p>输入经纬度定位当前矩阵；这里只计算行列，不读取地图。支持 EPSG:3857、EPSG:4326 和 CRS:84。位置输入保存在本浏览器当前标签，关闭标签后可能需要重新输入；旧目录需重新读取。</p>
  <Field label="位置方式"><select aria-label="位置方式" disabled={disabled||busy} value={form.mode} onChange={e=>edit('mode',e.target.value)}><option value="point">经纬度位置</option><option value="bounds">经纬度范围</option></select></Field>
  <div className="local-model-fields">{form.mode==='point'?<>{input('longitude','经度')}{input('latitude','纬度')}</>:<>{input('west','西侧经度')}{input('south','南侧纬度')}{input('east','东侧经度')}{input('north','北侧纬度')}</>}</div>
  <Btn disabled={disabled||busy||storageFailed.current} onClick={calculate}>计算瓦片位置</Btn>
  {storageFailed.current&&<Btn onClick={retrySave}>重试保存位置输入</Btn>}
  {error&&<p className="error" role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
 </section>;
}
