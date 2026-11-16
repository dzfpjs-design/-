import React,{useState} from 'react';

// These are capabilities of the existing adapters, not claims that a gateway
// is installed, online, or connected to an instance.
export const GATEWAY_GUIDES = Object.freeze({
 video:{title:'视频网关：添加连接与接入通道', description:'主动发现局域网 ONVIF 候选设备，按设备和网关授权后接入 MediaMTX，或管理已有通道。', boundary:'仅支持受信任 HTTPS、HTTP Digest、ONVIF Media 1 和 H.264；发现或配置不代表画面已验证。GB28181 自动发现、云台及设备控制尚未接通。', target:'video-gateway-add', action:'前往添加视频网关', extraTarget:'onvif-discovery', extraAction:'发现局域网设备'},
 maps:{title:'影像服务：添加地图连接', description:'连接 WMS、WMTS 或 ArcGIS 地图服务，也可从已授权 FTP、SMB 目录导入 GeoTIFF 原件。FeatureServer 要素可单独接入。', boundary:'保存的地图或要素需关联业务并生成、启用新实例；渲染地图不等于原始卫星波段。', target:'wms-gateway-add', action:'前往添加 WMS 服务', extraTarget:'map-services-add', extraAction:'添加 WMTS / ArcGIS 影像服务'},
 sensor:{title:'传感网关：新增 MQTT 来源', description:'配置 MQTT 服务、设备主题和报文字段；保存后按授权接收，再关联业务对象和数据处理规则。在“选择来源连接”中选择“新增传感接入”。', boundary:'MQTT 按明确主题只读接收 JSON 报文；本页也可登记已授权的 Modbus TCP 与 OPC UA 只读连接。设备发现和自动采集支持情况以各连接为准。', target:'mqtt-gateway-select', action:'前往选择新增 MQTT 来源'},
 docs:{title:'文档接入：上传或导入资料', description:'上传设计报告、需求文档，或从已授权的 FTP、FTPS、SMB 目录选择单个文件导入。', boundary:'目录需由远端服务器隔离；自动同步尚未接通。', action:'前往上传业务资料', upload:true, extraTarget:'ftps-document-add', extraAction:'从已授权目录导入'},
 business:{title:'业务数据接入：新增数据连接', description:'连接经授权的 HTTP JSON 接口，或上传 CSV、Excel、JSON 文件，再核对字段与导入结果。', boundary:'保存连接不等于数据已经读取或导入。', target:'business-gateway-add', action:'前往新增数据连接'},
 control:{title:'设备能力网关：添加测试连接', description:'保存测试设备服务连接，读取设备声明，再分别确认读取和控制授权。在“已保存的设备连接”中选择“新增测试连接”。', boundary:'当前仅支持测试设备服务；设备声明不代表现场物理设备已连接或获准执行控制。', target:'control-gateway-add', action:'前往选择新增测试连接'},
 models:{title:'模型接入：新增外部模型或管理本地模型', description:'可登记符合约定的外部 HTTP JSON/MCP 专业模型，也可管理本地模型；云端通用模型按当前环境启用策略使用。', boundary:'登记、契约验证和发布分别进行；发布不代表模型在线或领域效果达标。', target:'external-registration-models', action:'前往新增或管理外部专业模型'},
 tools:{title:'工具接入：新增外部工具或配置本地规则', description:'可登记符合约定的外部 HTTP JSON/MCP 工具，并将已发布工具用于确认后的业务流程。', boundary:'写操作及外部系统访问仍受授权、审批和回执约束。', target:'external-registration-tools', action:'前往新增或管理外部工具'},
});

export default function GatewayGuide({category,onUpload,Btn}){
 const guide=GATEWAY_GUIDES[category];
 const [error,setError]=useState('');
 if(!guide)return null;
 function locate(targetId=guide.target){
  setError('');
  if(guide.upload&&!targetId){onUpload();return;}
  const target=document.getElementById(targetId);
  if(!target){setError('配置入口尚未读取完成，或当前账号无权新增；请检查本页提示后重试。');return;}
  if(target.tagName==='DETAILS')target.open=true;
  if(target.tagName==='SUMMARY'&&target.parentElement?.tagName==='DETAILS')target.parentElement.open=true;
  const focus=target.tagName==='DETAILS'?target.querySelector('summary'):target;
  target.scrollIntoView?.({behavior:'smooth',block:'start'});
  focus?.focus?.({preventScroll:true});
 }
 return <section className="gateway-guide" aria-label={guide.title}>
  <div><h2>{guide.title}</h2><p>{guide.description}</p><p className="gateway-guide-boundary">{guide.boundary}</p></div>
  <div className="actions"><Btn onClick={()=>locate()}>{guide.action}</Btn>{guide.extraTarget&&<Btn onClick={()=>locate(guide.extraTarget)}>{guide.extraAction}</Btn>}</div>
  {error&&<p role="alert" className="error">{error}</p>}
 </section>;
}
