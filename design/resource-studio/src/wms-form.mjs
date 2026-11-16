export const wmsDraftKey=(workspace,user)=>'ga-wms-v1:'+workspace+':'+user;
export function wmsDraft(){return {version:1,service:{name:'',endpoint:'',version:'1.3.0',secret_ref:''},approval:{},read:{},scopeBusinesses:{},pending:null};}
export function readWMSDraft(storage,key){
 const raw=storage.getItem(key);if(!raw)return wmsDraft();
 let value;try{value=JSON.parse(raw);}catch{throw Error('WMS草稿无法读取，原数据保留；请先恢复浏览器草稿。');}
 if(value?.version!==1||!value.service||!value.approval||!value.read)throw Error('WMS草稿格式不匹配，原数据保留。');
 if(value.pending&&!['connection','read','approval','publication'].includes(value.pending.kind))throw Error('待核对WMS请求格式不匹配，原数据保留。');
 return value;
}
export function normalizeWMSEndpoint(value){
 let url;try{url=new URL(value);}catch{throw Error('请填写完整WMS服务地址。');}
 if(!['http:','https:'].includes(url.protocol)||url.username||url.password||url.hash)throw Error('WMS地址不允许内嵌凭证或片段；请用服务器密钥引用。');
 const seen=new Set();for(const [raw,v] of url.searchParams){const k=raw.toLowerCase();
  if(seen.has(k)||!['service','request','version'].includes(k)||k==='service'&&v.toUpperCase()!=='WMS'||k==='request'&&v.toLowerCase()!=='getcapabilities'||k==='version'&&!['1.1.1','1.3.0'].includes(v))throw Error('地址只可带WMS目录参数；服务凭据请使用服务器密钥引用。');seen.add(k);}
 url.search='';return url.toString();
}
export function wmsWindow(values,crs){
 if(!Array.isArray(values)||values.length!==4||values.some(v=>String(v).trim()===''||!Number.isFinite(Number(v))))throw Error('请填写四项有效坐标。');
 const box=values.map(Number),[west,south,east,north]=box;
 if(west>=east||south>=north)throw Error('范围需要西小于东、南小于北。');
 if(['EPSG:4326','CRS:84'].includes(crs)&&!(west>=-180&&east<=180&&south>=-90&&north<=90))throw Error('经纬度超出允许范围。');
 if(crs==='EPSG:3857'&&box.some(v=>Math.abs(v)>20037508.35))throw Error('投影坐标超出允许范围。');
 return box;
}
export function wmsDimensions(width,height){
 const dims=[width,height].map(Number);if([width,height].some(v=>String(v).trim()==='')||dims.some(v=>!Number.isInteger(v)||v<32||v>2048))throw Error('地图宽高需要32到2048的整数像素。');return dims;
}
// A damaged stored receipt must remain visible without breaking every record.
// This is a display guard; cryptographic provenance is checked by the backend.
export function wmsRecordFieldsPresent(row){
 const request=row?.request,scope=row?.connection_snapshot?.scope;
 if(!row?.id||typeof scope?.title!=='string'||!Array.isArray(request?.bbox)||request.bbox.length!==4||request.bbox.some(v=>typeof v!=='number'||!Number.isFinite(v))||![request.width,request.height].every(v=>Number.isInteger(v)&&v>=32&&v<=2048))return false;
 if(row.status==='succeeded')return typeof row.scene_id==='string'&&!!row.result&&Number.isInteger(row.result.transparent_pixels)&&row.result.transparent_pixels>=0&&row.result.transparent_pixels<=request.width*request.height;
 return ['queued','running','failed','cancelled'].includes(row.status);
}
export function sameWMSRead(row,pending){
 const body=pending.body,r=row.request;
 return row.request_id===body.request_id&&row.connection_id===pending.connection_id&&r?.scope_id===body.scope_id&&r.width===body.width&&r.height===body.height&&JSON.stringify(r.bbox)===JSON.stringify(body.bbox);
}
export function sameWMSConnection(row,pending){
 const body=pending.body;
 return row.request_id===body.request_id&&row.name===body.name.trim()&&row.configuration?.endpoint===body.endpoint&&row.configuration.version===body.version&&row.configuration.secret_ref===body.secret_ref;
}
export function savedWMSScope(connection,pending){
 if(connection.id!==pending.connection_id||connection.catalog_sha256!==pending.catalog_sha256)return null;
 const body=pending.body;
 return Object.values(connection.scopes||{}).find(s=>s.layer===body.layer&&s.crs===body.crs&&s.style===body.style&&JSON.stringify(s.bbox)===JSON.stringify(body.bbox))||null;
}
export function wmsBusinessNames(configuration){return (configuration?.businesses||[]).map(b=>typeof b==='string'?b:b.name);}
export function sameWMSScope(connection,pending){
 const scope=connection?.scopes?.[pending.body.scope_id],original=pending.scope;
 return connection?.id===pending.connection_id&&connection.catalog_sha256===pending.catalog_sha256&&scope&&original&&['id','layer','title','crs','style'].every(k=>scope[k]===original[k])&&JSON.stringify(scope.bbox)===JSON.stringify(original.bbox);
}
export function wmsSourceConfiguration(configuration,connection,pending){
 if(!sameWMSScope(connection,pending))throw Error('地图目录或原范围已变化，不能把新来源当作原登记结果。');
 const pub=connection.publications?.[pending.body.scope_id];
 if(!pub?.capability_id||!pub.manifest_hash||pub.catalog_sha256!==pending.catalog_sha256)throw Error('地图来源的服务器登记结果不完整，原选择保留。');
 if(pending.businesses.some(b=>!wmsBusinessNames(configuration).includes(b)))throw Error('业务清单已有变化，请先核对原选择；不恢复已移除的业务。');
 const scope=connection.scopes[pending.body.scope_id],gatewayID='wms-gateway:'+connection.id,id='wms-source:'+connection.id+':'+scope.id;
 const previous=(configuration.resources||[]).find(r=>r.id===id);
 const gateway={id:gatewayID,category:'maps',name:connection.name,protocol:'WMS',endpoint:connection.configuration.endpoint,status:'ready',auth:'服务端凭据引用',secret_ref:connection.configuration.secret_ref};
 const resource={id,category:'maps',connection:gatewayID,protocol:'WMS',name:previous?.name||scope.title,status:'candidate',caps:['查询已保存地图','固定只读图层与范围'],businesses:[...pending.businesses],checks:{registered:true,domain_evaluation:false},
  details:{wms_connection_id:connection.id,scope_id:scope.id,capability_id:pub.capability_id,manifest_hash:pub.manifest_hash,source_kind:'wms_rendered_map',deployment:'local',raw_satellite_bands:false}};
 return {...configuration,connections:[...(configuration.connections||[]).filter(c=>c.id!==gatewayID),gateway],resources:[...(configuration.resources||[]).filter(r=>r.id!==id),resource]};
}
