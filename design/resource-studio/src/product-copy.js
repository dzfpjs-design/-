import {categories} from './catalog.js';
export function connectionName(name){
 for(const c of categories){if(name===c.gateway+'（示例）')return c.gateway;if(name===c.gateway+'（示例）（副本）')return c.gateway+'（副本）';}
 return name;
}
export function statusText(text){return typeof text==='string'?text.replaceAll('模拟',''):text;}
export function productWorkspace(w){return {...w,connections:w.connections.map(c=>({...c,name:connectionName(c.name),lastError:statusText(c.lastError),gatewayConfig:c.gatewayConfig?{...c.gatewayConfig,name:connectionName(c.gatewayConfig.name)}:c.gatewayConfig,gatewayReport:c.gatewayReport?{...c.gatewayReport,rows:c.gatewayReport.rows.map(([k,v])=>[k,statusText(v)])}:c.gatewayReport}))};}
export function productEditorCache(cache){
 const clean=form=>({...form,name:connectionName(form.name)});
 return {...cache,forms:Object.fromEntries(Object.entries(cache.forms).map(([key,form])=>[key,clean(form)])),discarded:cache.discarded?{...cache.discarded,form:clean(cache.discarded.form)}:cache.discarded};
}
