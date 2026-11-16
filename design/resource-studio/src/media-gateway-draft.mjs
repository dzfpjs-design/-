// Gateway origins accept only the IP-literal syntax supported by the backend.
// Credentials, hostnames, query strings and paths are not kept in a draft.
const protocols={api_origin:['http://','https://'],rtsp_origin:['rtsp://']};
const sensitive=/(?:api[_ -]?key|password|secret|token|密钥|密码)\s*[:=：]\s*\S|sk-[a-z0-9_-]{12,}/i;
export function safeGatewayField(key,value){
 if(typeof value!=='string'||value.length>200||sensitive.test(value))return false;
 if(key==='name')return true;
 const schemes=protocols[key];if(!schemes)return false;
 if(schemes.some(s=>s.startsWith(value)))return true;
 return schemes.some(s=>value.startsWith(s)&&/^[0-9a-fA-F.:[\]]*$/.test(value.slice(s.length)));
}
export function validGatewayDraft(value){
 return !!value&&typeof value==='object'&&!Array.isArray(value)
  &&typeof value.request_id==='string'&&/^[A-Za-z0-9_-]{12,100}$/.test(value.request_id)
  &&['name','api_origin','rtsp_origin'].every(key=>safeGatewayField(key,value[key]))
  &&(!value.id||typeof value.id==='string'&&Number.isInteger(value.version))
  &&(value.pending===undefined||typeof value.pending==='boolean');
}
export function newGatewayDraft(){return {request_id:crypto.randomUUID(),name:'',api_origin:'http://127.0.0.1:9997',rtsp_origin:'rtsp://127.0.0.1:8554',pending:false};}
export function gatewayDraftForEdit(row){return {request_id:crypto.randomUUID(),id:row.id,version:row.version,name:row.name,api_origin:row.api_origin,rtsp_origin:row.rtsp_origin,pending:false};}
