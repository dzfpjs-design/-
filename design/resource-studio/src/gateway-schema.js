import {categories,withoutSecrets,validateConnection} from './catalog.js';
const text=(key,label,placeholder='',required=false)=>({key,label,placeholder,required});
const select=(key,label,options)=>({key,label,options,default:options[0]});
const many=(key,label,options)=>({key,label,options,multiple:true,default:[options[0]]});
const num=(key,label,value,min=0)=>({key,label,type:'number',default:value,min});
const fileScope=[text('path','授权目录 / 路径','例如：/observations',true),select('recursive','读取子目录',['包含子目录','仅当前目录'])];
const layerScope=[text('layers','图层白名单','填写图层名称，逗号分隔',true),text('crs','目标坐标系','例如：EPSG:4326'),text('bbox','管理范围','范围名称或坐标边界'),text('time','观测时间字段 / 维度','来自影像元数据或服务时间维度')];
const mapFileScope=[...fileScope,text('crs','坐标系来源','元数据优先，缺失时待确认'),text('bbox','覆盖范围','范围名称或坐标边界'),text('time','影像时间来源','元数据 / 文件名规则')];
const modelScope=[text('models','允许调用的模型 / 版本','通过模型目录选择，或手动填入标识',true),many('abilities','开放的模型能力',['文本推理','多模态理解','专业识别','向量表示']),text('input','输入约定','文本、影像、观测数据等'),text('output','输出约定','结论、位置、类别、置信度与单位')];
const professionalScope=[...modelScope,select('deployment','专业模型运行位置',['本地运行节点','云端服务（需授权数据发送）'])];
export const gatewayDesign={
 video:{title:'音视频网关',summary:'统一接入视频通道，供感知智能体读取画面与录像。',handoff:'画面、录像引用、采集时间与通道身份',scope:{
  ONVIF:[text('network','授权发现范围','例如：现场网络范围',true),text('profile','媒体配置','默认配置或指定Profile')],
  'RTSP / RTMP':[text('streams','流地址清单','每行一条流地址，不嵌入账号密码',true),select('transport','传输优先',['TCP优先','UDP优先','由适配器决定'])],
  GB28181:[text('platformId','平台标识','20位平台编码',true),text('domain','SIP域','所属平台注册域',true),num('sipPort','SIP监听端口',5060,1),select('registration','接入方向',['设备向本网关注册','连接上级平台同步目录'])],
  '已有视频平台':[text('adapter','平台适配器','选择已注册的平台适配器',true),text('channels','通道白名单','授权读取的通道 / 分组',true)]},
  processing:[select('streamPolicy','视频读取方式',['按业务请求读取','定时抽帧','持续流分析']),num('frameInterval','抽帧间隔（秒）',30,1),select('recording','录像读取范围',['按需读取授权录像','仅实时画面'])],
  runtime:[num('streamsLimit','同时读取通道数',4,1),select('outage','断流处理',['重连并报告数据缺口','仅报告，等待人工处理'])],warning:'录像读取与云台控制分开登记；本页不授予云台控制权限。'},
 maps:{title:'影像与地图网关',summary:'将卫星、无人机影像与地图服务统一为可查询的空间资料。',handoff:'原影像 / 图层引用、坐标系、覆盖范围与观测时间',scope:{FTP:mapFileScope,SFTP:mapFileScope,SMB:mapFileScope,WMS:layerScope,WMTS:[...layerScope,text('tileMatrix','瓦片矩阵集','由服务目录读取后选择')],'ArcGIS Server':[...layerScope,text('serviceType','服务类型','MapServer / ImageServer 等')],SuperMap:[...layerScope,text('workspace','服务 / 工作空间','选择授权服务')],GeoServer:[...layerScope,text('workspace','工作空间','指定工作空间与服务类型')],'影像文件':[text('formats','允许影像格式','GeoTIFF、JPEG2000等'),text('metadata','元数据来源','伴随文件 / 影像内部元数据')]},
  processing:[select('product','接入数据形态',['保留原始影像与元数据','图层与瓦片引用','两者分别登记']),select('coordinates','坐标不一致时',['保留原坐标并标记','转换为已确认目标坐标']),select('missingTime','缺失观测时间时',['标记待确认，排除时序分析','人工补充后再使用'])],
  runtime:[select('refresh','目录 / 图层更新',['每日检查变化','按业务请求读取','仅人工刷新']),num('cacheHours','目录缓存时长（小时）',24,1)],warning:'地图渲染图层不等于原始影像；可下载能力必须单独验证。'},
 sensor:{title:'传感与时序网关',summary:'接入观测点，规范时间、测量值与质量信息。',handoff:'设备 / 测点、观测时间、数值、单位与质量标记',scope:{
  MQTT:[text('topic','订阅主题白名单','observations/+',true),text('clientId','客户端标识','唯一订阅标识'),select('qos','订阅QoS',['1','0','2'])],
  HTTP:[text('readPath','只读接口路径','/observations',true),num('pollSeconds','轮询间隔（秒）',60,1)],
  'Modbus TCP':[num('unitId','单元 / 从站编号',1,1),text('registers','测点与寄存器表','地址、类型、量程和缩放系数',true),select('byteOrder','字节序',['由测点表指定','ABCD','CDAB','BADC','DCBA'])],
  'OPC UA':[text('nodes','节点白名单','节点标识列表',true),select('securityMode','安全模式',['SignAndEncrypt','Sign','None（仅隔离测试环境）']),text('certificateRef','证书引用','由网关运行环境管理')]},
  processing:[text('timestamp','观测时间字段','timestamp',true),text('unit','单位 / 换算规则','每个测点指定单位'),text('dedupe','重复数据识别字段','设备 + 时间 + 序号'),select('quality','异常值处理',['保留并标记，通知感知智能体','隔离异常值并记录原因'])],
  runtime:[num('lateMinutes','迟到数据窗口（分钟）',10),num('bufferHours','断网缓冲上限（小时）',24,1)],warning:'网关标记数据质量，感知智能体结合多源证据判断对象异常。'},
 docs:{title:'文档与内容网关',summary:'统一接入初始化资料与持续更新资料，保留原文依据。',handoff:'原文件引用、提取内容、页码 / 章节、版本与访问范围',scope:{
  '本地上传':[text('types','接受文件类型','DOCX、PDF、XLSX、TXT等'),num('maxFileMB','单文件上限（MB）',100,1)],FTP:fileScope,SMB:fileScope,WebDAV:fileScope,'对象存储':[text('bucket','存储桶', '资料桶名称',true),text('prefix','对象前缀','reports/'),text('region','区域','与存储服务一致')]},
  processing:[many('extract','提取内容',['正文与来源引用','表格','扫描件OCR']),select('versioning','版本识别',['内容摘要 + 来源版本','仅来源版本']),select('trust','文档内容处理',['仅作为资料与业务依据'])],
  runtime:[select('refresh','资料更新方式',['每日检查增量','新增文件触发','仅人工上传 / 刷新']),select('badFiles','损坏 / 加密文件',['隔离并提示重试或补充','保留引用，等待人工处理'])],warning:'文档中的指令不能改变智能体权限；解析结果保留可追溯引用。'},
 business:{title:'业务系统与数据网关',summary:'读取已有业务数据，形成可追溯、可关联的数据来源。',handoff:'来源记录、业务对象键、更新时间与字段含义',scope:{
  'REST API':[text('readPath','只读接口范围','允许读取的路径 / 方法',true),select('paging','分页方式',['游标分页','页码分页','无需分页']),text('cursor','分页 / 增量字段','next_cursor / updated_at')],
  PostgreSQL:[text('database','数据库 / 模式','数据库与模式',true),text('tables','表 / 视图白名单','优先只读视图',true)],MySQL:[text('database','数据库','授权数据库',true),text('tables','表 / 视图白名单','优先只读视图',true)],'SQL Server':[text('database','数据库 / 模式','数据库与模式',true),text('tables','表 / 视图白名单','优先只读视图',true)],'CSV / XLSX':[text('sheet','工作表 / 文件范围','选择工作表或文件规则'),select('header','表头位置',['第一行','人工指定'])]},
  processing:[text('objectKey','业务对象关联字段','地块编号、设备编号等',true),text('updatedAt','增量时间 / 游标字段','来源更新时间'),select('deletes','来源删除处理',['保留历史并标记来源删除','人工确认后调整关联'])],
  runtime:[select('sync','同步方式',['定时增量读取','按业务查询','全量快照']),num('syncMinutes','同步间隔（分钟）',60,1),select('schemaChange','字段结构变化',['暂停受影响映射并提示','兼容新增字段，缺失字段暂停'])],warning:'源系统读取范围在这里确认，数据管理智能体的目标存储由业务与资源自动派生。'},
 models:{title:'模型服务网关',summary:'专业小模型默认本地运行；Qwen、DeepSeek使用公有云，三个智能体按能力调用。',handoff:'模型版本、输入摘要、结果、耗时与费用记录',scope:{Qwen:modelScope,DeepSeek:modelScope,'OpenAI兼容':modelScope,'本地受管模型':[...modelScope,select('deployment','专业模型运行位置',['本地运行节点']),text('pack','已安装模型包标识','从本地安装目录读取',true)],'专业模型 HTTP':[...professionalScope,text('task','专业任务类型','分类、检测、分割、预测等')],Triton:[...professionalScope,text('tensor','输入 / 输出张量约定','名称、类型、维度与预处理')]},
  processing:[{...many('callers','可使用此连接的智能体',['业务智能体','多模感知智能体','数据管理智能体']),default:['业务智能体','多模感知智能体','数据管理智能体']},select('routing','默认选型规则',['按任务能力与已通过评估的模型','使用指定默认模型']),text('defaultModel','默认模型标识','从本连接登记模型中选择'),text('fallback','备用连接引用','可选；不自动切换到未授权云端'),select('dataSend','允许向服务发送的内容',['脱敏文本与结果摘要','经确认的影像 / 视频片段','仅非敏感测试样例'])],
  runtime:[num('budget','每日费用上限（元）',50),num('concurrency','最大并发调用数',4,1),select('overBudget','超限处理',['停止调用并报告','等待人工调整额度']),select('fallbackPolicy','故障切换条件',['同等数据权限、同等能力且已验证','不自动切换'])],warning:'本地专业模型故障时不能自动外发原始媒体。模型安装、硬件兼容与代表性样例评估通过后才能启用。'},
 tools:{title:'工具与服务网关',summary:'注册可复用操作，区分只读计算与会改变外部状态的操作。',handoff:'工具版本、输入输出、执行回执与结果引用',scope:{
  'HTTP / OpenAPI':[text('spec','OpenAPI描述地址','描述文件地址',true),text('operations','操作白名单','operationId列表',true)],
  '异步任务服务':[text('submit','任务提交路径','接口约定（本页不提交）',true),text('statusPath','状态查询路径','任务状态读取路径',true),text('cancelPath','任务取消路径','有则填写，无则声明不支持')],
  '内置工具':[text('package','已注册工具包 / 版本','选择平台工具包',true),text('operations','操作白名单','允许开放的操作',true)]},
  processing:[text('input','输入参数约定','必填项、类型与范围'),text('output','输出结果约定','结果字段、文件引用'),select('effect','操作性质',['只读查询 / 计算','生成平台内产物','修改外部业务状态']),text('idempotency','幂等标识字段','有副作用的操作必须提供')],
  runtime:[select('receipt','回执方式',['同步结果','状态轮询','已登记回调接收服务']),select('retryWrite','写操作重试',['仅确认未执行且具备幂等保护后重试','不自动重试'])],warning:'本页只登记操作声明；写操作自动执行条件在下一步确认。'},
 control:{title:'设备控制网关',summary:'复用已登记设备身份，声明可执行动作与回执机制。',handoff:'关联设备、动作约束、执行状态、回执与取消结果',scope:{
  'ONVIF / PTZ':[text('device','关联摄像头身份','从已登记音视频设备选择',true),text('profile','控制Profile','兼容的控制配置')],
  'MQTT控制':[text('device','关联设备身份','已登记设备标识',true),text('commandTopic','命令主题','仅登记，不发布消息',true),text('receiptTopic','回执主题','设备状态 / 动作回执',true)],
  'HTTP控制':[text('device','关联设备身份','已登记设备标识',true),text('actionsPath','动作目录读取路径','只读描述接口',true),text('statusPath','动作状态查询路径','只读状态接口')],
  '无人机任务服务':[text('device','关联机队 / 设备身份','已登记机队或机库',true),text('taskCatalog','任务模板范围','观测、补拍等已登记模板',true),text('airspace','观测区域约束','许可范围与任务限制')]},
  processing:[many('actions','声明可提供的动作',['转向 / 定位','拍摄 / 采样','观测任务','状态查询']),text('limits','参数与范围限制','角度、区域、采样频率等',true),text('cancelMethod','取消 / 停止方式','支持的接口或明确不支持',true)],
  runtime:[num('receiptTimeout','动作回执等待（秒）',30,1),select('unknownState','状态未知时',['停止后续调度并报告','人工核对后再继续']),select('controlRetry','控制命令重试',['不盲目重发，先查询状态'])],warning:'验证只读取设备能力和状态；自动执行权限需要下一步单独确认。'}
};
export const localProtocols=['本地受管模型','本地上传','影像文件','CSV / XLSX','内置工具'];
export const endpointHints={'FTP':'ftp://files.example.invalid','SFTP':'sftp://files.example.invalid','SMB':'\\\\files.example.invalid\\share','MQTT':'mqtts://broker.example.invalid:8883','Modbus TCP':'tcp://device.example.invalid:502','OPC UA':'opc.tcp://device.example.invalid:4840'};
export function gatewayRuntimeFields(form){
 const fields=gatewayDesign[form.category]?.runtime||[];
 return form.category==='models'&&form.protocol==='DeepSeek'?fields.filter(field=>!['budget','overBudget'].includes(field.key)):fields;
}
export function makeGatewayForm(record,category){
 const c=categories.find(c=>c.id===category),protocol=record?.protocol||c.protocols[0];
 return {id:record?.id||null,category,name:record?.name||'',protocol,endpoint:record?.endpoint||'',auth:record?.auth||'无需认证',account:record?.account||'',node:'平台托管网关',nodeAddress:'',timeout:30,retries:2,protocolValues:{[protocol]:record?.details||{}},settings:{},...record?.gatewayConfig};
}
export function fieldValues(form,group){return group==='scope'?form.protocolValues?.[form.protocol]||{}:form.settings||{};}
export function fieldValue(form,group,field){return fieldValues(form,group)[field.key]??field.default??(field.multiple?[]:'');}
export function cleanGatewayForm(form){
 const c=categories.find(c=>c.id===form.category),design=gatewayDesign[form.category];if(!c||!c.protocols.includes(form.protocol))throw Error('未知网关类型或协议');
 const safe={id:form.id,category:form.category,name:form.name,protocol:form.protocol,endpoint:form.endpoint,auth:form.auth,account:form.account,node:form.node,nodeAddress:form.nodeAddress,timeout:form.timeout,retries:form.retries,protocolValues:{},settings:{}};
 for(const [protocol,fields] of Object.entries(design.scope)){safe.protocolValues[protocol]={};for(const field of fields)safe.protocolValues[protocol][field.key]=form.protocolValues?.[protocol]?.[field.key]??field.default??(field.multiple?[]:'');}
 for(const field of [...design.processing,...design.runtime])safe.settings[field.key]=form.settings?.[field.key]??field.default??(field.multiple?[]:'');
 return withoutSecrets(safe);
}
export function gatewayProblems(form){
 const c=categories.find(c=>c.id===form.category),design=gatewayDesign[form.category],problems=[];
 const base=validateConnection({...form,endpoint:localProtocols.includes(form.protocol)?'':form.endpoint},c);
 if(base&&!((form.protocol==='CSV / XLSX')&&base==='请填写服务地址或授权目录。'))problems.push(base);
 if(form.node==='已有网关节点'&&!form.nodeAddress?.trim())problems.push('请填写网关节点管理地址。');
 if(form.node==='计划部署现场网关')problems.push('现场网关尚待部署，只能保存计划草稿。');
 if(form.auth!=='无需认证'&&!form.account?.trim())problems.push('请填写凭证引用名称。');
 for(const [group,fields] of [['scope',design.scope[form.protocol]||[]],['processing',design.processing],['runtime',design.runtime]])for(const f of fields){const v=fieldValue(form,group,f);if(f.required&&!String(v).trim())problems.push('请填写'+f.label+'。');if(f.type==='number'&&(!Number.isFinite(Number(v))||v===''||Number(v)<f.min))problems.push(f.label+'需为不小于'+f.min+'的数值。');if(f.multiple&&!v.length)problems.push('请选择至少一项'+f.label+'。');}
 if(!Number.isFinite(Number(form.timeout))||Number(form.timeout)<=0)problems.push('连接超时需大于0秒。');
 if(!Number.isInteger(Number(form.retries))||Number(form.retries)<0||Number(form.retries)>5)problems.push('读取重试次数需为0至5的整数。');
 if(form.category==='tools'&&form.settings.effect==='修改外部业务状态'&&!form.settings.idempotency?.trim())problems.push('修改外部状态的工具需要幂等标识。');
 const encoded=JSON.stringify(form);if(/(?:https?|rtsp|ftp|mqtts?):\/\/[^/\s"]+:[^/\s"]+@/i.test(encoded)||/[?&](api_key|token|password|secret)=/i.test(encoded))problems.push('地址或说明中含凭证，请移除并改用凭证引用。');
 return [...new Set(problems)];
}
export function gatewayRecord(form,id){
 const clean=cleanGatewayForm({...form,id}),values=clean.protocolValues[clean.protocol]||{};
 return {id,category:clean.category,name:clean.name.trim(),protocol:clean.protocol,endpoint:clean.endpoint.trim(),auth:clean.auth,account:clean.account,details:{topic:values.topic||'',database:values.database||''},gatewayConfig:clean,status:'pending',lastError:null};
}
export function commitGateway(workspace,record,{invalidate=true}={}){
 const exists=workspace.connections.some(x=>x.id===record.id);
 return {...workspace,connections:exists?workspace.connections.map(x=>x.id===record.id?record:x):[...workspace.connections,record],resources:invalidate?workspace.resources.map(x=>x.connection===record.id?{...x,status:'candidate',checks:{},lastError:null}:x):workspace.resources,selection:{category:record.category,connection:record.id,resource:workspace.resources.find(x=>x.connection===record.id)?.id}};
}
