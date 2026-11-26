import React,{useEffect,useRef,useState} from 'react';
import {json} from './runtime-api';
import GraphTaskInputs from './GraphTaskInputs';
import {createGraphSignalRoutingController} from './graph-signal-routing-session-v134.mjs';
import {routeConstantMetadata,routeInputScope,inspectSignalGraphDelivery,graphRouteUUID} from './graph-signal-routing-v134.mjs';
import {numericDisplay} from './typed-numbers-v132.mjs';
import {HTTP_SOURCE_CONTRACT_KEY} from './graph-http-source-contract-v177.mjs';

const time=value=>new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'});
const kind={human:'人工处理',ability:'本机能力',decision:'条件判断',fork:'并行启动',join:'全部分支汇合',wait:'等待已登记结果',end:'流程结束'};
const routeStatus={active:'已启用',paused:'已暂停',expired:'原24小时授权已到期',exhausted:'交接额度已占用完毕'};
const guardLabel={start:'启动条件',precondition:'执行前条件',stop:'停止条件',exception:'异常停止条件'};
const httpMeasurement={temperature_c:'模拟温度',humidity_percent:'模拟湿度',wind_m_s:'模拟风速',pressure_hpa:'模拟气压',battery_percent:'模拟电量',pan_degrees:'模拟水平角度',tilt_degrees:'模拟俯仰角度',zoom:'模拟变焦倍数'};
const Check=({children,...props})=><label style={{display:'flex',gap:8,alignItems:'flex-start'}}><input type="checkbox" style={{width:18,height:18,flex:'0 0 18px'}} {...props}/><span>{children}</span></label>;
const valueText=value=>value===null?'明确为空':typeof value==='boolean'?(value?'是':'否'):typeof value==='number'?numericDisplay(value):typeof value==='string'?value:Array.isArray(value)?'已固定的列表（'+value.length+'项）':'已固定的结构化资料';
const fieldLabel=(schema,key,index=0)=>schema?.properties?.[key]?.title||({operation:'统计方式',field:'待统计业务字段',records:'资料列表',value:'处理结果',summary:'处理说明',labels:'目标类别',threshold:'判断门槛',regions:'核查区域',filters:'筛选范围',category:'目标类别',note:'核查说明'})[key]||(/[\u3400-\u9fff]/u.test(key)?key:'资料字段 '+(index+1));
function RoutingValues({value,schema={},entities=[]}){
 if(value===null||typeof value!=='object'){const entity=typeof value==='string'&&entities.find(entity=>entity.id===value),field=typeof value==='string'&&entities.flatMap(entity=>entity.fields).find(field=>field.key===value);return <span style={{whiteSpace:'pre-wrap'}}>{entity?entity.name:field?field.label:valueText(value)}</span>;}
 if(Array.isArray(value))return <ol>{value.map((item,index)=><li key={index}><RoutingValues value={item} schema={schema.items||{}} entities={entities}/></li>)}</ol>;
 return <dl>{Object.entries(value).map(([key,item],index)=><React.Fragment key={key}><dt>{fieldLabel(schema,key,index)}</dt><dd>{schema.properties?.[key]?.description&&<p>{schema.properties[key].description}</p>}{key==='operation'&&typeof item==='string'&&({min:'取最小值',max:'取最大值',sum:'求和',mean:'取平均值',count:'统计数量'})[item]?({min:'取最小值',max:'取最大值',sum:'求和',mean:'取平均值',count:'统计数量'})[item]:<RoutingValues value={item} schema={schema.properties?.[key]||{}} entities={entities}/>}</dd></React.Fragment>)}</dl>;
}
function SchemaFields({schema}){return <ul>{Object.entries(schema?.properties||{}).map(([key,spec],index)=><li key={key}>{fieldLabel(schema,key,index)}{schema.required?.includes(key)?'（必填）':'（可选）'}{spec.description?'：'+spec.description:''}</li>)}</ul>;}
function HTTPGoalEvidence({evidence}){
 if(evidence?.source==='observations'&&evidence.minimum_verification==='unverified_extraction'
  &&evidence.time?.mode==='latest'&&evidence.time.max_age_seconds===3600&&evidence.time.timezone==='Asia/Shanghai')return <p>近 1 小时的模拟观测（SIM，北京时间），每个对象至少 {evidence.minimum_records_per_entity} 条。</p>;
 if(evidence?.source==='archive')return <p>核对已登记的对象档案；新结果不会自动写入档案。</p>;
 return <p role="alert">资料范围与当前显示不一致，请核对。</p>;
}
export function GraphRoutingPreviewV134({preview}){
 const binding=preview.binding,graph=binding.graph,summary=binding.graph_summary;
 const httpSource=Object.hasOwn(binding,HTTP_SOURCE_CONTRACT_KEY)?binding[HTTP_SOURCE_CONTRACT_KEY]:null;
 const name=id=>{const node=summary.nodes.find(node=>node.id===id);return node?.name&&node.name!==node.kind?node.name:kind[node?.kind]||'已确认的控制步骤';};
 const refName=key=>{const ref=graph.references[key];if(!ref)return '需核对的保存值';const schema=ref.source==='input'?binding.input_metadata.user_input_schema:(graph.nodes.find(node=>node.id===ref.node_id)?.output_schema||graph.nodes.find(node=>node.id===ref.node_id)?.execution?.output_schema);return (ref.source==='input'?'本次业务输入':name(ref.node_id)+'的结果')+' · '+fieldLabel(schema,ref.field,Math.max(0,Object.keys(schema?.properties||{}).indexOf(ref.field)));};
 const predicate=ast=>{if(!ast)return '未设置';if(ast.op==='ref')return refName(ast.key);if(ast.op==='value')return valueText(ast.value);if(ast.op==='not')return '不满足（'+predicate(ast.arg)+'）';if(ast.op==='all'||ast.op==='any')return '('+ast.args.map(predicate).join(ast.op==='all'?' 且 ':' 或 ')+')';return predicate(ast.left)+' '+({eq:'等于',ne:'不等于',gt:'大于',gte:'至少',lt:'小于',lte:'至多'}[ast.op]||'需核对比较')+' '+predicate(ast.right);};
 const guards=value=>Object.entries(value||{}).filter(([,id])=>id!==null).map(([key,id])=><p key={key}>{guardLabel[key]||'已确认条件'}：{predicate(graph.predicates[id])}</p>);
 return <section className="media-form" aria-label="完整业务流程授权预览"><h4>确认流程</h4><p>{binding.source.name} → {binding.extractor.name} → {binding.business.name}。仅使用本机只读功能，不操作设备。</p>
  {httpSource&&<section className="media-form" aria-label="HTTP模拟来源与字段映射"><h5>模拟设备</h5><p>模拟数据（SIM），仅供测试，非真实设备观测或现场事实；预览不会发起采集。</p><details><summary>查看字段映射</summary><ul>{Object.entries(httpSource.mapping.mapping.links).map(([raw,link],index)=>{const schema=httpSource.mapping.field_schemas[link.field_key],label=binding.entities.flatMap(entity=>entity.fields).find(field=>field.key===link.field_key)?.label||schema.title||'测量字段 '+(index+1);return <li key={raw}>{httpMeasurement[raw]} → {label}；原单位：{httpSource.field_units[link.field_key]}（不换算）</li>;})}</ul><p>采样时间由模拟器提供，非现场时间。</p></details><p>仅另经授权的后续采集可触发任务，旧数据不会重复派单；审批、设备操作和补采须另行授权。</p></section>}
  <p>只处理启用后的新异常；任务针对触发对象，截止时间为授权到期时间。</p>
  <details><summary>查看流程步骤和负责人</summary><h5>流程条件</h5>{guards(graph.guards)}
  {graph.nodes.map(node=><article className="media-form" key={node.id}><h5>{name(node.id)} · {kind[node.kind]}</h5>{node.actor_id&&<p>负责人：{binding.actors.find(actor=>actor.id===node.actor_id)?.username||'原负责人需核对'}</p>}{guards(node.guards)}
   {node.kind==='decision'&&<p>判断：{predicate(graph.predicates[node.predicate_id])}；条件未能确定时保持等待。</p>}
   {node.kind==='fork'&&<p>同时启动 {node.branch_ids.length} 个明确分支；须在“{name(node.join_id)}”等全部分支到齐。</p>}
   {node.kind==='join'&&<p>所有分支完成后继续。</p>}
   {node.kind==='wait'&&<><p>等待结果，最长 {node.timeout_seconds} 秒；超时按流程处理。</p><SchemaFields schema={graph.subscriptions.find(sub=>sub.id===node.subscription_id)?.output_schema}/></>}
   {node.kind==='human'&&<><p>需要填写：</p><SchemaFields schema={node.output_schema}/></>}
   {node.kind==='ability'&&(()=>{const ability=binding.abilities.find(item=>item.node_id===node.id);return <><p>{ability.name} · 发布版本 {ability.release_version} · 本机只读</p><p>输入：</p><ul>{Object.entries(node.execution.input_bindings).map(([key,ref],index)=><li key={key}>{fieldLabel(ability.input_schema,key,index)}：{refName(ref)}</li>)}</ul>{Object.keys(node.execution.arguments).length>0&&<details><summary>查看固定参数</summary><RoutingValues value={node.execution.arguments} schema={ability.input_schema} entities={binding.entities}/></details>}<SchemaFields schema={ability.output_schema}/></>;})()}
   <ul>{graph.edges.filter(edge=>edge.from===node.id).map((edge,index)=><li key={edge.id}>{({success:'完成后',true:'判断成立',false:'判断不成立',timeout:'等待超时',branch:'并行分支 '+(index+1)})[edge.on]} → {name(edge.to)}{edge.loop_id?'；按已确认循环次数限制返回':''}</li>)}</ul>
  </article>)}
  {!!graph.loops.length&&<><h5>重复规则</h5><ul>{graph.loops.map((loop,index)=><li key={loop.id}>循环 {index+1}：最多 {loop.max_iterations} 次；每次核对资料。</li>)}</ul></>}
  <h5>流程完成要求</h5>{graph.goals.map((goal,index)=><p key={goal.id}>完成要求 {index+1}：{goal.required_nodes.map(name).join('、')}均需完成。</p>)}</details>
  <h5>对象与目标</h5>{preview.expanded.map(row=>{const entity=binding.entities.find(item=>item.id===row.entity_id);return <article className="media-form" key={row.entity_id}><h5>{entity.name}</h5><dl>{Object.entries(row.graph_inputs).map(([key,value],index)=><React.Fragment key={key}><dt>{fieldLabel(binding.input_metadata.user_input_schema,key,index)}</dt><dd>{preview.command.entity_input_fields.includes(key)?'本次触发对象：'+entity.name:<RoutingValues value={value} schema={binding.input_metadata.user_input_schema.properties[key]} entities={binding.entities}/>}</dd></React.Fragment>)}</dl><p>目的：{row.goal_spec.purpose}</p>{httpSource&&<HTTPGoalEvidence evidence={row.goal_spec.evidence}/>}<p>核查字段：{row.goal_spec.required_fields.map(key=>entity.fields.find(field=>field.key===key)?.label||'原目标字段').join('、')}</p><p>需资料充分并由负责人验收；不会自动补采或更新对象档案。</p><details><summary>查看执行限制</summary><p>最多 {row.goal_spec.max_steps} 步、{row.goal_spec.max_tool_calls} 次能力调用。</p></details></article>;})}
 </section>;
}

export function SignalGraphDeliveryV134({signal,Btn,disabled=false,onViewTask}){
 let value;try{value=inspectSignalGraphDelivery(signal);}catch{return <p role="alert">任务状态暂无法读取，请刷新重试。</p>;}
 if(!value)return null;
 return <section className="media-form" aria-label="异常完整流程交接进度"><h4>{({reserved:'已保存异常，等待创建完整流程任务',created:'完整流程任务已创建',blocked:'完整流程交接受阻',skipped_existing_task:'同一原处理已有交接，未重复创建'})[value.status]}</h4><p>{value.message}</p><p>任务完成不代表现场异常已确认，也不会自动更新对象状态。{value.status==='blocked'?'请核对来源和授权，补充核查说明。':''}</p>{value.task_id&&<Btn disabled={disabled} onClick={()=>onViewTask?.(value.task_id)}>查看任务</Btn>}</section>;
}

export default function SignalGraphRoutingStudioV134(props){
 if(![props.account?.user_id,props.account?.workspace_id,props.instanceId].every(graphRouteUUID))return <section className="media-form" aria-label="异常启动完整业务流程"><h3>由新异常启动完整业务流程</h3><p role="alert">当前账号或工作区无法核对，暂不能启用。</p></section>;
 return <ScopedGraphRoutingStudioV134 {...props}/>;
}
function ScopedGraphRoutingStudioV134({instanceId,account,releaseID,enabled=true,Btn,onRegisterGuard,request=json,storage=globalThis.localStorage,tabStorage=globalThis.sessionStorage,locks=globalThis.navigator?.locks}){
 const scopeKey=[account?.user_id,account?.workspace_id,account?.role,instanceId].join(':'),holder=useRef(null),[,render]=useState(0),[pauseID,setPauseID]=useState(null);
 if(holder.current?.key!==scopeKey){holder.current?.controller.dispose();holder.current={key:scopeKey,controller:createGraphSignalRoutingController({api:request,scope:{actor_id:account.user_id,workspace_id:account.workspace_id,instance_id:instanceId},account,storage,session:tabStorage,locks})};}
 const c=holder.current.controller;
 useEffect(()=>{c.onChange=()=>render(value=>value+1);c.disposed=false;c.setEnvironment({releaseID,enabled});const guard=()=>!c.busy&&c.guard();onRegisterGuard?.(guard);
  const unload=event=>{if(c.busy||!c.guard()){event.preventDefault();event.returnValue='';}},focus=()=>c.recheckIdentity().catch(()=>{}),visibility=()=>{if(document.visibilityState==='hidden'){c.identityEpoch++;c.identityStatus='checking';c.notify();}else focus();},changed=event=>{if(event.key?.startsWith(c.prefix)&&c.identityStatus==='verified')try{c.refreshLocal();c.notify();}catch{c.error='本机原记录需要核对，原输入与请求保留。';c.notify();}};
  window.addEventListener('beforeunload',unload);window.addEventListener('focus',focus);window.addEventListener('storage',changed);document.addEventListener('visibilitychange',visibility);c.open().catch(()=>{});
  return()=>{onRegisterGuard?.(null);c.dispose();window.removeEventListener('beforeunload',unload);window.removeEventListener('focus',focus);window.removeEventListener('storage',changed);document.removeEventListener('visibilitychange',visibility);};
 },[c]);
 useEffect(()=>{c.setEnvironment({releaseID,enabled});},[c,releaseID,enabled]);
 const run=async callback=>{try{await callback();}catch(error){if(!c.disposed&&c.identityStatus==='verified'){c.error=error.graphRouteSafe?error.message:'操作结果待核对，请刷新后查看。';c.notify();}}};
 const edit=(key,value)=>run(()=>c.edit(key,value)),form=c.form,business=c.options?.businesses.find(row=>row.id===form.business_id),canRecover=c.writable()&&!c.busy,writable=canRecover&&!c.corrupt,preview=c.preview;
 function picker(key,rows,label,change=value=>edit(key,value)){return <label>{label}<select aria-label={label} disabled={!writable} value={form[key]} onChange={event=>change(event.target.value)}><option value="">请选择{label}</option>{form[key]&&!rows?.some(row=>row.id===form[key])&&<option value={form[key]}>原选择当前不可用，输入仍保留</option>}{rows?.map(row=><option key={row.id} value={row.id} disabled={row.available===false}>{row.name}{row.version?' · 第'+row.version+'版':''}{row.available===false?'（当前不可用）':''}</option>)}</select></label>;}
 const toggle=(key,value,checked)=>edit(key,checked?[...form[key],value]:form[key].filter(item=>item!==value));
 return <section className="media-form signal-graph-routing-v134" aria-label="异常启动完整业务流程"><h3>异常自动处理</h3><p>授权新异常启动本机只读流程，有效 24 小时。云服务与设备操作须另行授权。</p>
  {c.identityStatus!=='verified'?<><p role={c.identityStatus==='error'?'alert':'status'}>{c.identityStatus==='error'?'当前登录或保存范围未能核对，原输入和原请求暂时隐藏并保留。':'正在核对当前登录，本机输入与原请求保留。'}</p><Btn disabled={c.busy} onClick={()=>run(()=>c.open())}>重新核对登录与保存范围</Btn></>:<>
  <Btn disabled={c.busy} onClick={()=>run(()=>c.load())}>刷新授权</Btn>{!enabled&&<p>当前实例未启用，暂不能新增授权。</p>}{!['admin','operator'].includes(account.role)&&<p>当前账号仅可查看。</p>}
  {c.listing?.routes.map(route=><article className="media-form" key={route.id}><h4>{routeStatus[route.status]}</h4><p>{route.source.name} → {route.extractor.name} → {route.business.name}</p><p>对象：{route.entities.map(entity=>entity.name).join('、')}。已使用 {route.used_tasks} / {route.max_tasks}；授权到期：{time(route.expires_at)}（北京时间）。</p><p>受阻或跳过的任务仍计入已使用数量。</p>{route.status==='exhausted'&&<p>数量用尽后不再创建新任务，已安排的任务继续执行；如需停止，请暂停授权。</p>}{['active','exhausted'].includes(route.status)&&<><Btn disabled={!writable||!!c.pending} onClick={()=>setPauseID(pauseID===route.id?null:route.id)}>暂停授权</Btn>{pauseID===route.id&&<div className="media-form"><p>暂停后不再创建新任务，已有自动任务也将停止执行和提交结果；不会关闭设备。</p><label>暂停原因<textarea aria-label="完整流程暂停原因" disabled={!writable} value={form.pause_reason} maxLength={2000} onChange={event=>edit('pause_reason',event.target.value)}/></label><Btn disabled={!writable||!c.localSaved||!!c.pending||!form.pause_reason.trim()} onClick={()=>run(async()=>{await c.pause(route);if(!c.pending)setPauseID(null);})}>确认暂停</Btn><Btn disabled={c.busy} onClick={()=>setPauseID(null)}>暂不暂停</Btn></div>}</>}</article>)}
  {c.pending&&<aside className="media-form"><h4>原{c.pending.action==='enable'?'启用':'暂停'}操作待核对</h4><p>请先核对操作结果，勿重复提交。</p><Btn disabled={c.busy} onClick={()=>run(()=>c.recover())}>查看处理结果</Btn>{c.pendingNotFound&&<><p>未找到申请；若范围已变化，继续可能被拒绝。</p><Btn disabled={!writable||!c.localSaved} onClick={()=>run(()=>c.resend())}>继续原申请</Btn></>}</aside>}
  <h4>新增授权</h4>{c.options?.blocking_reason&&<p role="status">{c.options.blocking_reason}</p>}{c.options&&c.options.release_id!==releaseID&&<p role="alert">当前配置已变化，请刷新后重新预览。</p>}
  {picker('source_id',c.options?.sources,'完整流程异常来源')}{picker('extractor_id',c.options?.extractors,'完整流程本地规则')}
  {c.options?.http_mapped_source_ids_v177?.includes(form.source_id)&&<p>使用近 1 小时模拟观测（SIM），非现场事实；设备采集与补采须另行授权。</p>}
  {c.options?.extractors.find(row=>row.id===form.extractor_id)&&<p>规则条件：{c.options.extractors.find(row=>row.id===form.extractor_id).rules.map((rule,index)=>{const label=c.options.entities.flatMap(entity=>entity.fields).find(field=>field.key===rule.field)?.label||'规则字段 '+(index+1);return label+(rule.operator==='gt'?'大于':'小于')+rule.threshold;}).join('；')}。匹配结果需核实。</p>}
  <fieldset disabled={!writable}><legend>触发对象（最多 30 个）</legend>{c.options?.entities.map(entity=><Check key={entity.id} aria-label={'完整流程对象 '+entity.name} checked={form.entity_ids.includes(entity.id)} onChange={event=>toggle('entity_ids',entity.id,event.target.checked)}>{entity.name} · 第{entity.version}版</Check>)}{form.entity_ids.some(id=>!c.options?.entities.some(entity=>entity.id===id))&&<p role="alert">部分已选对象不可用，请核对或移除。</p>}{form.entity_ids.filter(id=>!c.options?.entities.some(entity=>entity.id===id)).map((id,index)=><Btn key={id} onClick={()=>toggle('entity_ids',id,false)}>移除不可用对象 {index+1}</Btn>)}</fieldset>
  {picker('business_id',c.options?.businesses,'完整业务流程',id=>run(()=>c.selectBusiness(id)))}{business?.reason&&<p role="alert">{business.reason}。请检查实例配置。</p>}
  {business?.input_metadata&&<><fieldset disabled={!writable}><legend>随异常变化的对象输入</legend><p>未勾选的输入需填写固定值。</p>{business.entity_input_fields.map((key,index)=><Check key={key} aria-label={'使用触发对象 '+fieldLabel(business.input_metadata.user_input_schema,key,index)} checked={form.entity_input_fields.includes(key)} onChange={event=>run(()=>c.setEntityFields(event.target.checked?[...form.entity_input_fields,key]:form.entity_input_fields.filter(field=>field!==key)))}>{fieldLabel(business.input_metadata.user_input_schema,key,index)} → 每次实际触发对象</Check>)}</fieldset>
   <GraphTaskInputs metadata={routeConstantMetadata(business,form.entity_input_fields)} scope={routeInputScope(c.scope,business)} draft={form.input_draft} objects={c.options.entities.map(entity=>({id:entity.id,reference:entity.name}))} selectedObjects={form.entity_ids} disabled={!writable} onChange={draft=>edit('input_draft',draft)} onReset={()=>run(()=>c.resetInput())}/></>}
  <label>业务目的<textarea aria-label="异常流程目标目的" value={form.purpose} maxLength={2000} disabled={!writable} onChange={event=>edit('purpose',event.target.value)}/></label>
  <fieldset disabled={!writable}><legend>核查字段</legend>{business?.goal_fields.filter(field=>form.entity_ids.length&&form.entity_ids.every(id=>field.entity_ids.includes(id))).map(field=><Check key={field.key} aria-label={'目标核查 '+field.label} checked={form.required_fields.includes(field.key)} onChange={event=>toggle('required_fields',field.key,event.target.checked)}>{field.label}{field.unit?'（'+field.unit+'）':''}</Check>)}{form.required_fields.filter(key=>!business?.goal_fields.some(field=>field.key===key&&form.entity_ids.every(id=>field.entity_ids.includes(id)))).map((key,index)=><p key={key}>所选字段不适用于全部对象。<Btn disabled={!writable} onClick={()=>toggle('required_fields',key,false)}>移除不可用字段 {index+1}</Btn></p>)}</fieldset>
  {[['max_steps','每个任务的步骤上限'],['max_tool_calls','每个任务的能力调用上限'],['max_tasks','24小时内交接总数上限']].map(([key,label])=><label key={key}>{label}<input type="text" inputMode="numeric" aria-label={label} value={form[key]} disabled={!writable} onChange={event=>edit(key,event.target.value)}/></label>)}
  <p>仅使用已有资料，结果须人工验收；增加步骤或调用需另行确认。</p>
  <label>启用理由<textarea aria-label="完整流程启用理由" value={form.reason} disabled={!writable} maxLength={2000} onChange={event=>edit('reason',event.target.value)}/></label>
  <Btn disabled={!writable||!c.localSaved||!!c.pending||!c.options?.can_enable||c.options?.release_id!==releaseID} onClick={()=>run(()=>c.makePreview())}>预览流程</Btn>
  {preview&&<><GraphRoutingPreviewV134 preview={preview}/><Check aria-label="确认完整流程本机执行范围" disabled={!writable||!!c.pending||!c.localSaved} checked={form.confirmed} onChange={event=>edit('confirmed',event.target.checked)}>我已核对流程和负责人。同意在 24 小时及数量上限内使用本机只读能力处理新异常；设备、云服务和补采须另行授权。</Check><Btn primary disabled={!writable||!c.localSaved||!form.confirmed||!!c.pending} onClick={()=>run(()=>c.enable())}>启用 24 小时</Btn></>}
  {!!form.input_history.length&&<details><summary>输入模板（{form.input_history.length}份）</summary>{form.input_history.map((row,index)=><article key={index}><p>模板 {index+1}：{c.options?.businesses.find(item=>item.id===row.business_id)?.name||'原业务范围'}；填写内容：</p><dl>{Object.entries(row.input_draft.values||{}).map(([key,value],fieldIndex)=><React.Fragment key={key}><dt>{row.field_labels[key]||'原输入 '+(fieldIndex+1)}</dt><dd>{value.mode==='unset'?'尚未填写':value.mode==='null'?'明确为空':value.raw}</dd></React.Fragment>)}</dl><Btn disabled={!writable||!!c.pending} onClick={()=>run(()=>c.restoreInput(index))}>保留当前模板并恢复这份输入</Btn></article>)}</details>}
  {!!c.retained.length&&<details><summary>恢复草稿（{c.retained.length}份）</summary>{c.retained.map(row=><article key={row.owner}><p>{row.form.purpose||row.form.reason||'尚未完成的范围选择'}</p><Btn disabled={!canRecover||!!c.pending} onClick={()=>run(()=>c.restoreForm(row))}>保留当前稿并找回这份草稿</Btn></article>)}</details>}
  {!!c.requests.length&&<details><summary>历史授权操作（{c.requests.length}份）</summary>{c.requests.map((row,index)=><article key={row.key}><p>原{row.record.action==='enable'?'启用':'暂停'}操作 {index+1}：{row.record.body.reason}。状态待核对。</p><Btn disabled={c.busy||!!c.pending&&c.pending.body.request_id!==row.record.body.request_id} onClick={()=>run(()=>c.restoreRequest(row))}>查看状态</Btn></article>)}</details>}
  {c.localProblems>0&&<p role="alert">部分本机记录无法读取，请核对。</p>}
  {!c.localSaved&&<><p role="alert">内容尚未保存，请勿离开。</p><Btn disabled={c.busy} onClick={()=>run(()=>c.persist())}>重试保存</Btn></>}
  {c.error&&<p role="alert">{c.error}</p>}{c.notice&&<p role="status">{c.notice}</p>}
  </>}
 </section>;
}
