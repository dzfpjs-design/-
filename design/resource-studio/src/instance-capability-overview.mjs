// Snapshot descriptions are configuration evidence, not claims that a device
// is online, a model has passed domain evaluation, or an action may execute.
const labels={video:'摄像头与音视频',camera:'摄像头与音视频',maps:'影像与地图',sensor:'传感与时序',models:'专业模型',tools:'工具与服务',docs:'文档与内容',business:'业务数据',control:'设备控制'};
const text=(value,fallback)=>typeof value==='string'&&value.trim()?value.trim().slice(0,200):fallback;

export function releasedResources(instance,release,formal,publicSources){
 if(!instance||!release||release.instance_id!==instance.id||instance.active_release_id!==release.id||!['active','partially_active'].includes(instance.status))return null;
 if(!release.snapshot||!Array.isArray(release.snapshot.resources))return null;
 const formalRows=Array.isArray(formal)?formal:[],publicRows=Array.isArray(publicSources)?publicSources:[];
 return release.snapshot.resources.filter(resource=>resource&&typeof resource==='object'&&!Array.isArray(resource)).map((resource,index)=>{
  const formalSource=formalRows.find(row=>row?.id===resource.id);
  const publicSource=resource.protocol==='PUBLIC_OBSERVATION'?publicRows.find(row=>row?.source_id===resource.details?.public_source_id):null;
  let state='已纳入版本配置；可用性待核对';
  if(['disabled','inactive','deleted','paused'].includes(resource.status))state='配置已停用';
  else if(formalSource?.status==='available')state='来源配置核验通过；在线状态待检查';
  else if(formalSource?.status==='unavailable')state='来源当前不可用，请核对配置';
  else if(publicSource?.status==='published')state='公开来源已发布；实际可用性以来源为准';
  return {key:typeof resource.id==='string'&&resource.id?resource.id:'entry-'+index,
   name:text(publicSource?.name,text(resource.name,'未命名资源')),category:labels[resource.category]||'其他资源',state,
   association:Array.isArray(resource.businesses)&&resource.businesses.length?'已关联 '+resource.businesses.length+' 项业务':'未声明业务关联'};
 });
}

export function enabledWorkflowSteps(instance,release){
 if(!instance||!release||release.instance_id!==instance.id||instance.active_release_id!==release.id||!['active','partially_active'].includes(instance.status)||!Array.isArray(instance.enabled_business_ids))return [];
 const businesses=Array.isArray(release.snapshot?.businesses)?release.snapshot.businesses:[];
 return businesses.filter(b=>b&&instance.enabled_business_ids.includes(b.id)).flatMap(b=>{
  const steps=Array.isArray(b.flow?.steps)?b.flow.steps:[];
  return steps.filter(s=>s&&typeof s==='object'&&s.execution).map((s,index)=>({key:String(b.id)+':'+String(s.id||index),business:text(b.name,'未命名业务'),name:text(s.name,'未命名步骤')}));
 });
}
