// Clarification is human work: save incomplete choices, restore them, then confirm a new query.
function needFieldCatalog(run){return Object.fromEntries((run.data.request.entity_ids||[]).flatMap(id=>{
  const e=entities.find(x=>x.id===id),p=caps.find(x=>x.id===e?.schema_capability_id);
  return Object.entries(p?.data.specification?.entity_schemas[e?.entity_type]?.properties||{});
}));}
function needProposal(run){const n=run.data.need||run.data.need_checkpoint?.value;if(!n)return '';
  const rel=n.relation_query;
  return `<div class="evidence"><b>系统理解的核查需求</b><p>${esc(n.purpose||'')}</p><p>字段：${esc((n.fields||[]).map(k=>needFieldCatalog(run)[k]?.title||k).join('、')||'尚未明确')}</p>${rel?`<p>关系起点：${esc(rel.root_ids.map(relationName).join('、'))}<br>关系：${esc(rel.relation_types.map(k=>caps.find(c=>c.id===rel.package_id)?.data.specification?.relation_types?.[k]?.title||k).join('、'))}<br>${rel.direction==='incoming'?'沿指向起点的关系':'沿从起点出发的关系'} · 最多${rel.max_hops||1}层 · ${rel.include_roots?'包含起点档案':'只取关联对象档案'}</p>`:''}${(n.clarifications||[]).map(q=>`<p>待澄清：${esc(q)}</p>`).join('')}<small>目录与名称用于理解需求；已确认关系及字段证据另行核查。模型提出的关系范围需要人工确认。</small></div>`;
}
function openNeedEditor(run){
  let version=run.version;const request=run.data.request,need=run.data.need||run.data.need_checkpoint?.value||{},
    draft=ordinaryNumericClone(run.data.resolution_draft||{question:run.question,fields:need.fields||request.fields||[],
      relation_query:need.relation_query||request.relation_query||null,evidence_requirement:request.evidence_requirement||null,
      business_timezone:request.business_timezone||null,tool_steps:need.tool_steps||request.tool_steps||[],max_tool_calls:request.max_tool_calls||6,max_tool_seconds:request.max_tool_seconds||60,allow_external_tools:request.allow_external_tools||false}),fields=needFieldCatalog(run),
    evidence=draft.evidence_requirement||{source:'archive',minimum_verification:'recorded',time:{mode:'archive',timezone:draft.business_timezone}};
  const d=document.createElement('dialog');d.className='card';d.style.cssText='width:740px;max-width:92vw;max-height:86vh;overflow:auto';
  d.innerHTML=`<h2>澄清并确认查询方案</h2><p>原问题：${esc(run.question)}</p>${needProposal(run)}<p>授权对象范围：${esc(request.entity_ids.map(relationName).join('、'))}</p><p class="muted">这里只能在原授权范围内选择起点和关系；如需扩大对象范围，请另建查询。保存草稿后可关闭并恢复。确认会保存新查询，保留原问题和模型建议。</p><label for="needQuestion">澄清后的问题</label><textarea id="needQuestion">${esc(draft.question||run.question)}</textarea><label>需要核查的字段</label><div class="choices">${Object.entries(fields).map(([k,s])=>`<label><input type="checkbox" data-need-field value="${esc(k)}" ${(draft.fields||[]).includes(k)?'checked':''}>${esc(s.title||k)}</label>`).join('')}</div>${relationScopeControls('needRelation',request.entity_ids,draft.relation_query)}<details><summary>日期、来源和确认等级</summary>${timeControls('needTime',evidence.time)}${evidenceControls('needEvidence',evidence)}</details><div id="needTools"></div><label for="needToolCalls">最多工具调用次数</label><input id="needToolCalls" type="text" inputmode="numeric" value="${esc(draft.max_tool_calls===undefined?'6':ordinaryNumericDisplay(draft,'max_tool_calls'))}"><label for="needToolSeconds">工具执行时间预算（秒）</label><input id="needToolSeconds" type="text" inputmode="numeric" value="${esc(draft.max_tool_seconds===undefined?'60':ordinaryNumericDisplay(draft,'max_tool_seconds'))}"><label><input id="needExternal" type="checkbox" ${draft.allow_external_tools?'checked':''}>允许本次原范围证据交已登记外部查询服务</label><p>外部目的地：${esc(dataToolChoices(request.entity_ids).filter(c=>['http','mcp','cloud_retrieval'].includes(c.data.execution.adapter)).map(dataToolDestination).join('、')||'此范围没有登记外部查询服务')}</p><label for="needReason">确认或保存依据</label><textarea id="needReason">${esc(draft.form_reason||'')}</textarea><p id="needError" role="alert"></p><div class="actions"><button id="needSave" class="secondary">保存查询草稿</button><button id="needConfirm">确认方案并查询</button><button id="needClose" class="secondary">关闭</button></div>`;
  document.body.append(d);bindRelationScope(d,'needRelation',request.entity_ids,draft.relation_query);bindTime(d,'needTime',evidence.time);bindEvidence(d,'needEvidence',evidence);const readTools=mountQueryTools(d.querySelector('#needTools'),run,draft);d.showModal();
  for(const id of ['needToolCalls','needToolSeconds'])d.querySelector('#'+id).addEventListener('input',()=>{d.querySelector('#'+id).dataset.numericEdited='true';});
  const read=()=>{const value=ordinaryNumericClone(draft);
    Object.assign(value,{question:d.querySelector('#needQuestion').value.trim(),fields:[...d.querySelectorAll('[data-need-field]:checked')].map(x=>x.value),relation_query:readRelationScope(d,'needRelation'),evidence_requirement:readEvidence(d,'needEvidence',readTime(d,'needTime')),business_timezone:d.querySelector('#needTimeZone').value.trim()||null,form_reason:d.querySelector('#needReason').value.trim(),tool_steps:readTools(),allow_external_tools:d.querySelector('#needExternal').checked});
    for(const [id,key,fallback,maximum] of [['needToolCalls','max_tool_calls',6,6],['needToolSeconds','max_tool_seconds',60,120]]){
      const input=d.querySelector('#'+id);if(input.dataset.numericEdited==='true')ordinaryNumericSet(value,key,input.value,'integer','查询工具预算');
      else if(value[key]===undefined)value[key]=fallback;
      if(!Number.isSafeInteger(value[key])||value[key]<1||value[key]>maximum)throw Error('查询工具预算必须为允许范围内的整数；原输入保留');
    }return value;};
  const save=async()=>{const value=read();if(!value.form_reason)throw Error('请填写确认或保存依据');const saved=await api('/data/queries/'+run.id+'/draft','PUT',{expected_version:version,reason:value.form_reason,draft:value});version=saved.version;return value;};
  const act=async(confirm)=>{d.querySelectorAll('button').forEach(b=>b.disabled=true);try{const value=await save();if(confirm){const result=await api('/data/queries/'+run.id+'/confirm','POST',{expected_version:version,reason:value.form_reason});d.close();d.remove();await load();document.querySelector('[data-query="'+result.query.id+'"]')?.scrollIntoView({block:'start'});notice('确认依据和新查询已保存；原问题、澄清与模型回执继续保留。');}else{await load();d.querySelector('#needError').textContent='草稿已保存，可以关闭后恢复。';}}catch(error){d.querySelector('#needError').textContent=error.message;}finally{d.querySelectorAll('button').forEach(b=>b.disabled=false);}};
  d.querySelector('#needSave').onclick=()=>act(false);d.querySelector('#needConfirm').onclick=()=>act(true);
  d.querySelector('#needClose').onclick=()=>{d.close();d.remove();};
}
function augmentQueryNeeds(){if(!['data','relations'].includes(current))return;
  queries.forEach(run=>{if(dataQueryReferenceLike(run))return;const card=document.querySelector('[data-query="'+run.id+'"]');if(!card)return;
    if(!run.data.result)card.insertAdjacentHTML('beforeend',needProposal(run));
    const receipt=run.data.model_receipt||run.data.need_checkpoint?.receipt;
    if(receipt){const p=document.createElement('p');p.className='muted';p.textContent=receipt.usage?.total_tokens?`需求解析模型回执：${receipt.usage.total_tokens} token。确认方案查询不再调用模型。`:'模型调用回执已保存；缺少usage时不能认定费用为零。';card.append(p);}
    if(run.data.goal_delegation){const p=document.createElement('p');p.textContent='业务委托方案由已确认目标控制；调整参数请回到原业务目标并重新规划。';card.append(p);}
    if(run.data.parent_query_id&&run.data.human_confirmation){const p=document.createElement('p');p.textContent='来自人工澄清确认：'+run.data.human_confirmation.source_question+' · '+run.data.human_confirmation.reason;card.append(p);}
    if(run.data.resolution_draft){const p=document.createElement('p');p.textContent='查询草稿已保存 · '+date(run.data.draft_saved_at);card.append(p);}
    if(me.role!=='viewer'&&!run.data.goal_delegation&&!run.data.resolved_query_id&&(['needs_clarification','needs_confirmation','blocked_model','failed'].includes(run.status)||(['blocked_evidence','insufficient','rejected','sufficient'].includes(run.status)&&(run.data.request.tool_steps?.length||run.data.tool_trace?.length)))){const b=document.createElement('button');b.textContent=run.data.result?'核查条件并新建查询':'澄清并确认查询方案';b.onclick=()=>openNeedEditor(run);card.append(b);}
    if(me.role!=='viewer'&&['queued','running'].includes(run.status)){const b=document.createElement('button');b.className='secondary';b.textContent='取消此查询';b.onclick=()=>simpleReview('停止查询并保留原请求与回执',reason=>api('/data/queries/'+run.id+'/cancel','POST',{expected_version:run.version,reason}));card.append(b);}
    if(run.data.resolved_query_id){const p=document.createElement('p');p.textContent='已按人工确认方案创建新查询；原解析记录保留。';card.append(p);}
  });
}
