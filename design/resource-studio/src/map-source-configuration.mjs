function stable(value){if(Array.isArray(value))return value.map(stable);if(value&&typeof value==='object')return Object.fromEntries(Object.keys(value).sort().map(k=>[k,stable(value[k])]));return value;}
export function verifySourcePublication(result,original){
 if(!result||result.state!=='created'||typeof result.source_valid!=='boolean'||result.request_id!==original.body.request_id||result.preview_id!==original.body.preview_id||result.connection_id!==original.id||result.protocol!==original.protocol||result.name!==original.name||JSON.stringify(stable(result.selection))!==JSON.stringify(stable(original.selection))||!(/^[0-9a-f-]{36}$/i.test(result.capability_id||''))||!(/^[a-f0-9]{64}$/.test(result.manifest_hash||'')))throw Error('服务器来源回执与原地图不一致，请核对');
 return true;
}
export function requireMapBusinesses(businesses,current){
 if(!Array.isArray(businesses)||!businesses.length)throw Error('请至少选择一个使用此地图的业务');
 if(!current)throw Error('业务配置尚未读取');
 if(businesses.some(b=>!(current.businesses||[]).some(item=>(typeof item==='string'?item:item.name)===b)))throw Error('业务清单已变化，原选择保留，请核对');
 return true;
}
export function mapSourceConfiguration(current,result,original){
 verifySourcePublication(result,original);
 if(!result.source_valid)throw Error('地图来源授权或原件已失效；请恢复授权并核对原件后再关联业务。');
 requireMapBusinesses(original.businesses,current);
 const gatewayID='map-gateway:'+original.id,resourceID='map-source:'+original.body.preview_id,protocol=original.protocol==='wmts'?'WMTS':original.protocol==='arcgis_image'?'ArcGIS ImageServer':'ArcGIS MapServer';
 const gateway={id:gatewayID,category:'maps',name:result.name,protocol,status:'ready'};
 const previous=(current.resources||[]).find(x=>x.id===resourceID);
 const resource={id:resourceID,category:'maps',protocol,name:previous?.name||result.name,status:'candidate',businesses:[...original.businesses],connection:gatewayID,caps:['查看已保存地图'],checks:{registered:true,domain_evaluation:false},details:{map_connection_id:original.id,map_preview_id:original.body.preview_id,capability_id:result.capability_id,manifest_hash:result.manifest_hash,source_kind:'saved_gateway_map',raw_satellite_bands:false}};
 return {...current,connections:[...(current.connections||[]).filter(x=>x.id!==gatewayID),gateway],resources:[...(current.resources||[]).filter(x=>x.id!==resourceID),resource]};
}
