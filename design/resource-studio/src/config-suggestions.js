import {withoutSecrets} from './catalog.js';
export function gatewaySuggestion(text,form){
 if(withoutSecrets({text}).text!==text)return {error:'说明中可能含凭证，请删除后再提交。',rows:[]};
 const rows=[];let m;
 if(m=/超时(?:时间)?[^0-9]{0,12}(\d+)\s*秒/.exec(text)){const v=Number(m[1]);if(v<1||v>3600)return {error:'超时应为1–3600秒。',rows:[]};rows.push(['timeout','连接 / 读取超时',form.timeout,v]);}
 if(m=/重试[^0-9]{0,8}(\d+)\s*次/.exec(text)){const v=Number(m[1]);if(v>5)return {error:'重试次数应为0–5。',rows:[]};rows.push(['retries','读取失败重试次数',form.retries,v]);}
 if(m=/(?:连接名称|名称)(?:改为|设为)[：:\s]*(.+?)(?:[，。；]|$)/.exec(text))rows.push(['name','连接名称',form.name,m[1].trim()]);
 return rows.length?{rows}:{error:'当前支持调整连接名称、超时秒数和重试次数。其他设置可直接在表单中修改；通用模型理解服务尚未接入。',rows:[]};
}
export function resourceSuggestion(text){
 if(withoutSecrets({text}).text!==text)return {error:'说明中可能含凭证，请删除后再提交。'};
 const m=/^(?:记录|新增|添加)(?:待补充)?需求[：:\s]+(.+)$/.exec(text.trim());
 return m?{name:m[1].trim()}:{error:'智能接入规划服务尚未接入。可以填写“记录需求：具体能力名称”，或通过网关和资源表单配置。'};
}
