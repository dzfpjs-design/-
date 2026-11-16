import React,{useEffect,useState} from 'react';
import {api,hashBytes} from './runtime-api';
export default function MaskOriginal({image,path,result,maskPath,Btn}){
 const geo=result.source_raster,sourceLabel=geo?'原分辨率RGB分析图':'原图';
 const viewKey='ga-mask-view:'+result.masks[0]?.mask_evidence_id;
 function restored(){try{const n=Number(sessionStorage.getItem(viewKey));return Number.isInteger(n)&&n>=0&&n<result.masks.length?n:0;}catch{return 0;}}
 const [original,setOriginal]=useState(''),[overlay,setOverlay]=useState(null),[index,setIndex]=useState(restored),[error,setError]=useState(''),[retry,setRetry]=useState(0),[maskError,setMaskError]=useState('');
 const mask=result.masks[index],maskURL=mask?maskPath(mask.mask_evidence_id):'';
 useEffect(()=>{let alive=true,url;setError('');setOriginal('');api(path).then(async response=>{
  const bytes=await response.arrayBuffer();if(await hashBytes(bytes)!==image.sha256)throw Error('原图摘要不一致，未显示为核查依据。');
  url=URL.createObjectURL(new Blob([bytes],{type:response.headers.get('content-type')||image.media_type}));if(alive)setOriginal(url);else URL.revokeObjectURL(url);
 }).catch(e=>{if(alive)setError(e.message);});return()=>{alive=false;if(url)URL.revokeObjectURL(url);};},[path,image.sha256,retry]);
 useEffect(()=>{let alive=true,url;setOverlay(null);setMaskError('');if(!mask)return;api(maskURL).then(async response=>{
  const bytes=await response.arrayBuffer();if(await hashBytes(bytes)!==mask.mask_sha256)throw Error('候选掩膜摘要不一致，未叠加到原图。');
  url=URL.createObjectURL(new Blob([bytes],{type:'image/png'}));if(alive)setOverlay({id:mask.mask_evidence_id,url});else URL.revokeObjectURL(url);
 }).catch(e=>{if(alive)setMaskError(e.message);});return()=>{alive=false;if(url)URL.revokeObjectURL(url);};},[maskURL,mask?.mask_sha256,retry]);
 if(result.image_sha256!==image.sha256||result.width!==image.width||result.height!==image.height)return <p role="alert" className="error">实例掩膜与原图版本不一致，请重新核对保存记录。</p>;
 return <article className="workflow-original"><strong>{image.name}</strong><small>{image.width} × {image.height} · {sourceLabel}与候选掩膜已保存</small>
  <p>本地 {result.backend.toUpperCase()} · {result.masks.length} 个候选区域 · 推理 {result.inference_ms} 毫秒（不含排队与加载）· 未调用云模型</p>
  {!!result.masks.length&&<label>查看候选区域 <select aria-label="查看候选区域" value={index} onChange={e=>{const n=Number(e.target.value);setIndex(n);try{sessionStorage.setItem(viewKey,String(n));}catch{}}}>{result.masks.map((m,i)=><option key={m.mask_evidence_id} value={i}>候选 {m.candidate_index} · {m.area_pixels} 像素</option>)}</select></label>}
  {error&&<p className="error" role="alert">{error}</p>}{maskError&&<p className="error" role="alert">{maskError}</p>}{(error||maskError)&&<Btn onClick={()=>setRetry(v=>v+1)}>重新读取原图与掩膜</Btn>}
  {!original&&!error&&<p role="status">正在核对原图…</p>}
  {original&&<><svg className="detection-image" role="img" aria-label={'原图与候选区域 '+(mask?.candidate_index||'无')} viewBox={`0 0 ${image.width} ${image.height}`}><image href={original} width={image.width} height={image.height}/>{mask&&overlay?.id===mask.mask_evidence_id&&<image href={overlay.url} x={mask.bbox[0]} y={mask.bbox[1]} width={mask.bbox[2]-mask.bbox[0]} height={mask.bbox[3]-mask.bbox[1]}/>}</svg><a href={original} download={image.name}>下载已核对的{sourceLabel}</a></>}
  {mask&&<>{!overlay&&!maskError&&<p role="status">正在核对候选掩膜…</p>}<p>候选 {mask.candidate_index} · 像素面积 {mask.area_pixels} · 边界质量分数 {mask.score.toFixed(3)} · 来自 {mask.source_tiles.length} 个影像窗口</p>{mask.merge_evidence?.length>0&&<details><summary>区域合并依据</summary>{mask.merge_evidence.map((m,i)=><p key={i}>合并 {i+1}：重合 {m.intersection_pixels} 像素，交并比 {m.iou.toFixed(3)}</p>)}</details>}<p>分数是模型对边界的估计，不是业务结论的概率。区域未确定语义类别。{geo?mask.geometry_native?'已按确认的地图参考生成原坐标边界，地面定位精度未核验。':'当前候选没有可用的地图位置，面积只按像素记录。':'没有地理参考，面积单位为像素。'}</p>{geo&&<><p>有效候选像素 {mask.area_pixels} · 剔除无效像素 {mask.nodata_removed_pixels} · 原影像像素边界 {mask.bbox_original.join('，')}</p>{mask.area_m2!=null&&<p>面积 {mask.area_m2.toLocaleString('zh-CN',{maximumFractionDigits:2})} 平方米 · 按原坐标系椭球测地计算；来源影像定位精度未核验。</p>}{mask.geometry_error&&<p role="alert" className="error">此候选空间转换失败：{mask.geometry_error}。掩膜仍保留。</p>}{mask.rgb_merge_evidence?.length>0&&<details><summary>剔除无效像素前的RGB候选合并依据</summary>{mask.rgb_merge_evidence.map((m,i)=><p key={i}>合并 {i+1}：重合 {m.intersection_pixels} 像素，交并比 {m.iou.toFixed(3)}</p>)}</details>}</>}</>}
  {!mask&&<p>当前门槛下没有候选区域，不能据此判定没有对象或业务异常。</p>}
  {result.candidate_limit_reached&&<p className="warning-text">本次候选达到处理上限，已舍弃 {result.discarded_proposals} 项。此结果不代表整幅影像的完整对象清单。</p>}
  {result.exif_orientation!==1&&<p className="warning-text">位置按文件解码后的原始像素记录，未按EXIF旋转。</p>}
 </article>;
}
