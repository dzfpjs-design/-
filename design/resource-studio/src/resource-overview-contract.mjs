// The overview reports records, never live connectivity or instance readiness.
export const OVERVIEW_CARDS = Object.freeze([
  {id:'video', gateway:'视频网关', title:'摄像头与音视频', description:'发现局域网 ONVIF 候选设备，按授权接入 MediaMTX 并读取 RTSP/H.264 通道。', boundary:'仅支持受信任 HTTPS 和 HTTP Digest 的 ONVIF Media 1；发现或配置不代表画面已验证，GB28181 尚未接通。', paths:['/media/gateways','/media/channels','/public-observations/registrations']},
  {id:'maps', gateway:'影像服务接入', title:'影像与地图', description:'连接 WMS、WMTS 及 ArcGIS MapServer、ImageServer、FeatureServer，读取并核对资料。', boundary:'WMTS 支持有限范围的多瓦片拼接；FeatureServer 支持受限范围的要素读取。超图专用服务尚未接通。', paths:['/wms/connections','/public-observations/registrations','/map-services/connections']},
  {id:'sensor', gateway:'传感数据网关', title:'传感与时序数据', description:'配置 MQTT 来源、设备主题和字段映射，再按业务关联数据。', boundary:'读取授权不代表设备在线；Modbus、OPC UA 尚未由此入口接入。', paths:['/mqtt/gateways']},
  {id:'docs', gateway:'文档接入', title:'文档与内容', description:'上传资料，或从已授权 FTPS 目录选择单个文件导入并查看解析进度。', boundary:'普通 FTP、SMB 和远程目录自动同步尚未接通。', paths:['/setup/materials?limit=100']},
  {id:'business', gateway:'业务数据接入', title:'业务系统与数据', description:'连接 HTTP JSON 或上传表格，核对字段与导入结果。', boundary:'配置已保存不代表来源已读取成功或对象已导入。', paths:['/business-data/sources']},
  {id:'control', gateway:'设备能力网关', title:'设备控制能力', description:'接入测试设备服务，核对设备声明并分别配置读取与控制授权。', boundary:'当前展示的是测试连接，物理设备及现场控制尚需核验。', paths:['/workspace/device-gateways']},
  {id:'models', gateway:'模型接入', title:'通用与专业模型', description:'本地 RT-DETR、SAM2、语音与声音模型；云端通用模型按当前环境策略配置。', boundary:'模型登记、安装或契约验证不代表领域效果已验证。', paths:['/specialists/installations']},
  {id:'tools', gateway:'工具接入', title:'工具与服务', description:'登记 HTTP JSON/MCP 工具，或配置本地数值规则。', boundary:'外部服务能力与可执行权限需分别确认。', paths:['/local-rules?limit=50','/capabilities']},
]);
const list = x => { if (!Array.isArray(x) || x.some(item=>!item || typeof item!=='object' || Array.isArray(item))) throw Error('invalid response'); return x; };
const requireFields = (items, fields) => {if (items.some(item=>fields.some(([key,type])=>typeof item[key]!==type))) throw Error('invalid response'); return items;};
const count = (xs, f) => xs.filter(x => x && typeof x === 'object' && f(x)).length;
const withLimit = (n, limit) => n >= limit ? `至少 ${n}` : `${n}`;

export function overviewSummary(id, values) {
  if (!Array.isArray(values)) throw Error('invalid response');
  switch (id) {
    case 'models': { const items=requireFields(list(values[0]),[['status','string']]); return `${items.length} 条安装记录 · ${count(items,x=>x.status==='ready')} 条本机检查通过`; }
    case 'video': { const gateways=list(values[0]), channels=requireFields(list(values[1]),[['status','string'],['read_authorized','boolean']]), publicItems=requireFields(list(values[2]),[['provider_id','string'],['status','string']]);
      const pub=count(publicItems,x=>['nps_redwood','usgs_kilauea'].includes(x.provider_id)&&x.status==='published');
      return `${gateways.length} 个视频网关 · ${count(channels,x=>x.read_authorized===true && x.status!=='disabled')} 个已授权通道 · ${pub} 项已发布公开来源`; }
    case 'maps': { const connections=list(values[0]), publicItems=requireFields(list(values[1]),[['provider_id','string'],['status','string']]), maps=list(values[2]);
      return `${connections.length} 个 WMS 连接 · ${withLimit(maps.length,100)} 个 WMTS / ArcGIS 连接 · ${count(publicItems,x=>x.provider_id==='nasa_modis'&&x.status==='published')} 项已发布公开影像来源`; }
    case 'sensor': { const items=list(values[0]); return `${withLimit(items.length,100)} 个 MQTT 来源 · 接收状态需查看记录`; }
    case 'docs': { const items=list(values[0]); return `${withLimit(items.length,100)} 份资料记录 · 请查看原件及解析状态`; }
    case 'business': { const items=list(values[0]); return `${items.length} 个业务数据来源记录`; }
    case 'tools': { const rules=list(values[0]), caps=requireFields(list(values[1]),[['kind','string']]);
      return `${withLimit(rules.length,50)} 条本地规则 · ${withLimit(count(caps,x=>x.kind==='tool'),100)} 项工具记录${caps.length>=100?'（仅查最近 100 项能力）':''}`; }
    case 'control': { const result=values[0]; if (!result || typeof result.has_more!=='boolean') throw Error('invalid response'); const connections=list(result.connections);
      return `${result.has_more?'至少 ':''}${connections.length} 个测试设备连接 · 状态需逐项核验`; }
    default: throw Error('unknown category');
  }
}
