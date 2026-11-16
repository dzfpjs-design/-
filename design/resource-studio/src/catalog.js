export const categories = [
 {id:'video',label:'摄像头与音视频',icon:'Camera',gateway:'园区视频网关',protocols:['ONVIF','RTSP / RTMP','GB28181','已有视频平台'],action:'扫描设备',manual:'手动添加',scope:'当前网络',caps:['实时视频','录像读取','云台控制'],fields:['设备位置','通道或流地址'],examples:['园区东入口摄像头','主干道固定摄像头','园区录像机'],outputs:['画面时间','通道','视频或录像引用'],hint:'ONVIF可发现兼容设备；RTSP通常手动填写，GB28181采用注册或目录同步。'},
 {id:'maps',label:'影像与地图',icon:'Map',gateway:'影像与地图网关',protocols:['FTP','SFTP','SMB','WMS','WMTS','ArcGIS Server','SuperMap','GeoServer','影像文件'],action:'读取目录或图层',manual:'登记影像来源',scope:'授权目录或服务',caps:['影像读取','图层查询','空间范围','历史影像'],fields:['坐标系','覆盖范围','观测时间'],examples:['年度卫星影像','无人机正射影像','管理范围底图'],outputs:['坐标系','空间范围','观测时间'],hint:'文件服务与地图服务分别适配；可读取的地图图层不一定提供原始影像下载。'},
 {id:'sensor',label:'传感与时序数据',icon:'Activity',gateway:'传感数据网关',protocols:['MQTT','HTTP','Modbus TCP','OPC UA'],action:'发现或读取测点',manual:'登记传感器',scope:'授权主题或测点',caps:['实时订阅','历史读取','质量标记'],fields:['测点或主题','计量单位','采样频率'],examples:['气象站温度测点','土壤湿度传感器','水位监测站'],outputs:['观测时间','测量值','单位','质量标记'],hint:'需要映射时间戳、单位和测点；主题可订阅不代表所有测量值都可信。'},
 {id:'docs',label:'文档与内容',icon:'Files',gateway:'文档资料网关',protocols:['本地上传','FTP','SMB','WebDAV','对象存储'],action:'读取资料目录',manual:'登记资料来源',scope:'指定资料目录',caps:['文本提取','表格提取','版本跟踪','目录同步'],fields:['资料用途','更新方式','来源版本'],examples:['业务初步设计报告.docx','巡护制度.pdf','巡查记录目录'],outputs:['来源文件','页码或章节','文档版本'],hint:'初始化资料和运行资料共用文档接入；原文只读，保留提取结果与引用来源。'},
 {id:'business',label:'业务系统与数据',icon:'Database',gateway:'业务数据网关',protocols:['REST API','PostgreSQL','MySQL','SQL Server','CSV / XLSX'],action:'读取接口或数据表',manual:'登记数据集',scope:'授权接口或数据表',caps:['只读查询','增量同步','字段映射'],fields:['接口或数据表','更新周期','对象关联字段'],examples:['地块基础档案','巡护记录接口','设备台账表'],outputs:['业务对象','来源记录','更新时间'],hint:'配置来源系统的读取范围；数据管理智能体的存储方案将自动派生。'},
 {id:'models',label:'通用与专业模型',icon:'Box',gateway:'模型服务网关',protocols:['Qwen','DeepSeek','OpenAI兼容','本地受管模型','专业模型 HTTP','Triton'],action:'读取可用模型',manual:'注册模型',scope:'授权模型服务',caps:['文本推理','多模态理解','专业识别','向量表示'],fields:['模型版本','输入格式','输出含义与单位','费用或调用限制'],examples:['Qwen通用模型','DeepSeek推理模型','病虫害识别模型'],outputs:['模型版本','输入摘要','结果与置信说明'],hint:'三个智能体共享模型能力；连接和格式通过，还需要代表性样例证明业务准确率。'},
 {id:'tools',label:'工具与服务',icon:'Blocks',gateway:'工具服务网关',protocols:['HTTP / OpenAPI','异步任务服务','内置工具'],action:'读取可用操作',manual:'注册工具',scope:'授权工具目录',caps:['只读计算','检索分析','报告生成','业务写操作'],fields:['输入参数说明','输出说明','超时与回执'],examples:['空间叠加分析','统计汇总工具','报告生成服务'],outputs:['输入参数','输出结果','执行回执'],hint:'登记工具的操作与输入输出；写操作是否可以自动执行，在下一步确认。'},
 {id:'control',label:'设备控制能力',icon:'Settings',gateway:'设备控制网关',protocols:['ONVIF / PTZ','MQTT控制','HTTP控制','无人机任务服务'],action:'读取可控设备',manual:'登记控制接口',scope:'已登记设备范围',caps:['云台转向','拍摄与采样','任务下发','状态与回执'],fields:['关联物理设备','动作参数限制','回执与取消方式'],examples:['东入口摄像头云台','自动采样设备','无人机观测任务'],outputs:['设备身份','动作状态','任务回执'],hint:'复用已登记物理设备身份。此页只读取能力声明与登记，绝不试发控制命令。'}
];
export const businesses=['日常巡护','灾害监测','资源调查'];
export const scenarios=[['success','成功'],['empty','无结果'],['credentials','凭证错误'],['timeout','连接超时'],['network','服务不可达'],['partial','部分成功'],['unsupported','能力不支持'],['format','格式不匹配'],['denied','语音权限失败'],['storage','保存失败']];
export const interactions=[
 ['connect','新增网关连接','表单 → 校验 → 保存待验证 → 取消'],['manage','管理网关连接','编辑 / 停用 / 恢复 / 删除影响 / 重试'],['verifyConnection','验证网关连接','加载 → 成功 / 凭证错误 / 超时 / 不可达 / 取消'],['discover','发现或读取资源','范围确认 → 进度 → 结果 / 空 / 部分 / 取消'],['manual','手动登记资源','专用表单 → 校验 → 候选资源'],['upload','上传文件','业务资料库 → 原件保存 / 去重 / 查看 / 归档 / 恢复'],['credentials','填写设备连接信息','独立认证 → 确认 / 取消'],['verifyResource','验证并添加资源','逐能力结果 → 不支持 / 部分通过 / 登记'],['sample','样例与字段映射','样例 → 输入输出映射 → 用途 → 保存'],['remove','移除资源','查看业务影响 → 确认 → 撤销'],['gap','记录待补充资源','填写需求与业务 → 保存缺口'],['nl','自然语言接入建议','修改建议 → 确认应用 / 取消'],['voice','语音输入','服务未接入 → 文字输入；未来需录音 / 转写 / 核对 / 取消'],['save','保存与恢复草稿','本地保存 → 刷新恢复 / 写入失败'],['export','查看讨论摘要','当前草稿可读摘要；无凭证；文件下载未验证'],['next','本页检查与下一步','已接入 / 待验证 / 待补充'],['help','帮助与顶栏导航','职责说明与配置步骤']
];
export function initialWorkspace(){return {version:1,businesses:[],connections:categories.map(c=>({id:c.id+'-gateway',category:c.id,name:c.gateway+'',protocol:c.protocols[0],endpoint:'',auth:'无需认证',status:'pending'})),resources:categories.flatMap(c=>c.examples.map((name,i)=>({id:c.id+'-'+i,category:c.id,connection:c.id+'-gateway',protocol:c.id==='video'?['ONVIF','RTSP / RTMP','GB28181'][i]:c.id==='models'?['Qwen','DeepSeek','专业模型 HTTP'][i]:c.protocols[0],name,status:'candidate',caps:i===0?(c.id==='video'?[c.caps[0],c.caps[2]]:[c.caps[0]]):[],details:{},businesses:[],checks:{}}))),gaps:[],notes:'',selection:{category:'video',resource:'video-0'},savedAt:null};}
export function validateConnection(f,c){if(!f.name?.trim())return '请填写连接名称。';if(!c.protocols.includes(f.protocol))return '请选择已支持的接入协议。';if(f.protocol!=='本地上传'&&f.protocol!=='本地受管模型'&&f.protocol!=='内置工具'&&f.protocol!=='影像文件'&&!f.endpoint?.trim())return '请填写服务地址或授权目录。';if(f.endpoint&&(/:\/\/[^/]*:[^/]*@/.test(f.endpoint)||/[?&](api_key|token|password|secret)=/i.test(f.endpoint)))return '请从地址移除凭证，改用独立认证设置。';if(f.endpoint&&/^(javascript|data):/i.test(f.endpoint))return '请填写有效的服务地址或目录。';return '';}
export function validateResource(f,resources,editing){if(!f.name?.trim())return '请填写资源名称。';if(resources.some(r=>r.id!==editing&&r.category===f.category&&r.name===f.name.trim()))return '同类资源已存在该名称，请选择原资源或修改名称。';if(!f.caps?.length)return '请选择至少一项准备接入的能力。';return '';}
export function withoutSecrets(w){
 const omitted=Symbol('omitted'),stack=new WeakSet(),secretKey=/password|token|secret|credentialValue/i,secretText=/(sk-[a-z0-9_-]{16,}|(?:api[_ -]?key|password|secret|token|密钥|密码)\s*[:=：]\s*\S{8,}|[?&](?:api_key|access_token|token|password|secret)=|(?:https?|rtsp|rtmp|ftp|sftp|mqtts?|opc\.tcp):\/\/[^/\s]+:[^/\s]+@)/i;
 function visit(value,key=''){
  if(secretKey.test(key)||value===undefined||typeof value==='function'||typeof value==='symbol')return omitted;
  if(typeof value==='string')return secretText.test(value)?'[疑似凭证已移除，请在连接设置中填写]':value;
  if(value===null||typeof value==='boolean'||typeof value==='number'&&Number.isFinite(value))return value;
  if(typeof value!=='object'||stack.has(value)||![Object.prototype,Array.prototype,null].includes(Object.getPrototypeOf(value)))throw Error('配置包含无法安全保存的内容，原输入保留。');
  stack.add(value);const array=Array.isArray(value),result=array?[]:{};
  for(const name of Object.keys(value)){
   if(secretKey.test(name))continue;
   const descriptor=Object.getOwnPropertyDescriptor(value,name);if(!Object.hasOwn(descriptor,'value'))throw Error('配置包含无法安全保存的属性，原输入保留。');
   const item=visit(descriptor.value,name);if(item!==omitted)Object.defineProperty(result,name,{value:item,enumerable:true,writable:true,configurable:true});
  }
  if(array)for(let i=0;i<value.length;i++)if(!Object.hasOwn(result,i))result[i]=null;
  stack.delete(value);return result;
 }
 const result=visit(w);if(result===omitted)throw Error('配置内容无法保存，原输入保留。');return result;
}
export function connectionDraft(f,category,id){return withoutSecrets({id,category,name:f.name.trim(),protocol:f.protocol,endpoint:f.endpoint.trim(),auth:f.auth,account:f.account||'',details:{topic:f.topic||'',database:f.database||''},status:'pending'});}
