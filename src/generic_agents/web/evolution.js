const learningCategories={normal:'相关经验检索',no_answer:'无足够答案',conflict:'来源冲突',stale:'资料过期',ambiguous:'对象尚未明确'};
function learningMemory(id,snapshots={}) {
  const m=overview.memories.find(m=>m.id===id)||snapshots[id];
  if(!m)return '<p class="muted">未选择经验或当前不可用</p>';
  return `<div class="evidence"><b>${esc(m.data.goal||'已核实经验')}</b><p>${esc(m.text)}</p>
    <details><summary>查看依据与适用限制</summary>${receiptEvidence(m.data.evidence||{})}</details></div>`;
}
function learningField(packageId,key) {
  const schemas=caps.find(c=>c.id===packageId)?.data.specification.entity_schemas||{};
  for(const schema of Object.values(schemas))if(schema.properties?.[key])return schema.properties[key].title||key;
  return key;
}
function learningFields(packageId) {
  const schemas=caps.find(c=>c.id===packageId)?.data.specification.entity_schemas||{};
  const fields=new Map();
  for(const schema of Object.values(schemas))for(const [key,value] of Object.entries(schema.properties||{}))fields.set(key,value.title||key);
  return [...fields];
}
function learningChoices(packageId) {
  return overview.memories.filter(m=>m.status==='trusted'&&m.data.package_id===packageId);
}
function learningOptions(packageId) {
  return '<option value="">只核查证据状态，不标注第一条经验</option>'+learningChoices(packageId)
    .map(m=>`<option value="${esc(m.id)}">${esc((m.data.goal||m.text).slice(0,120))}</option>`).join('');
}
function renderLearning() {
  const state=learningOverview,packages=caps.filter(c=>c.kind==='domain_package'&&c.status==='published');
  const canEdit=me.role!=='viewer',admin=me.role==='admin';
  const drafts=state.sets.filter(s=>s.status==='draft');
  const left=`<div class="card"><h2>从已核实案例生成候选</h2><p>候选只调整经验排序，不能改事实、权限、审批或设备动作。</p>
    ${canEdit?`<form id="learningCandidateForm"><label for="learningPackage">业务包</label><select id="learningPackage">${selectOptions(packages,p=>p.data.description)}</select>
    <label for="learningTitle">候选名称</label><input id="learningTitle" required><div id="learningSources"></div><button>保存候选</button></form>`:''}</div>
    <div class="card"><h2>建立回归评测集</h2><p class="muted">保存标签后才能冻结。训练与评测须按对象版本、任务和原件分离；合成标签只证明流程。</p>
    ${canEdit?`<form id="learningSetForm"><label for="setPackage">业务包</label><select id="setPackage">${selectOptions(packages,p=>p.data.description)}</select>
    <label for="setTitle">评测集名称</label><input id="setTitle" required><button>保存评测集草稿</button></form>`:''}
    ${drafts.map(s=>`<div class="evidence"><h3>${esc(s.title)}</h3><p>${s.data.cases.length}条用例已保存，可离开后继续。</p>
    <details><summary>添加一条已核查用例</summary><form data-case-form="${s.id}">
    <label>用例名称<input name="name" required></label><label>用例类型<select name="category">${Object.entries(learningCategories).map(([k,v])=>`<option value="${k}">${v}</option>`).join('')}</select></label>
    <p>对象范围（未明确对象时保持为空）</p>${entities.filter(e=>e.schema_capability_id===s.package_id).map(e=>`<label class="choices"><input name="entity" type="checkbox" value="${e.id}">${esc(e.data.name||e.external_id)}</label>`).join('')}
    <p>需核查的字段</p>${learningFields(s.package_id).map(([key,title])=>`<label class="choices"><input name="field" type="checkbox" value="${esc(key)}">${esc(title)}</label>`).join('')||empty('该业务包尚未登记字段，请先补充业务包定义。')}
    <label>正确的证据状态<select name="expected_status">${['sufficient','insufficient','conflict'].map(v=>`<option value="${v}">${esc(statusLabels[v])}</option>`).join('')}</select></label>
    <label>应排第一的经验<select name="expected_memory">${learningOptions(s.package_id)}</select></label>
    <label>标签依据<textarea name="label_reason" required></textarea></label><button>保存此用例</button></form></details>
    ${s.data.cases.map(c=>`<div><p>${esc(c.name)} · ${esc(learningCategories[c.category])} · ${esc(statusLabels[c.expected_status])}</p>
    <small>${esc(c.label_reason)}</small>${canEdit?`<button class="secondary" data-remove-case="${c.case_id}" data-set="${s.id}">移除用例</button>`:''}</div>`).join('')}
    ${canEdit&&s.data.removed_cases?.length?`<button class="secondary" data-restore-set="${s.id}">恢复最近移除的用例</button>`:''}
    ${admin?`<button style="margin-top:12px" data-freeze-set="${s.id}">核对标签并冻结</button>`:''}</div>`).join('')}</div>`;
  const frozenSets=state.sets.filter(s=>s.status==='frozen').map(s=>`<div class="card"><h3>${esc(s.title)}</h3>${badge(s.status)}<p>${s.data.cases.length}条标签与证据快照已保存；冻结后保留原版本。</p><details><summary>查看冻结标签与核查依据</summary>${s.data.cases.map(c=>`<div class="evidence"><b>${esc(c.name)} · ${esc(learningCategories[c.category])}</b><p>字段：${c.fields.map(f=>esc(learningField(s.package_id,f))).join('、')||'未指定'} · ${esc(statusLabels[c.expected_status])}</p><p>${esc(c.label_reason)}</p>${c.expected_memory_ids.map(id=>learningMemory(id,s.data.pool||{})).join('')}</div>`).join('')}</details></div>`).join('');
  const candidates=state.candidates.map(c=>{
    const run=state.runs.find(r=>r.candidate_id===c.id);
    return `<div class="card"><h3>${esc(c.title)}</h3>${badge(c.status)}<p>${Object.entries(c.data.config.field_weights).map(([k,v])=>`${esc(learningField(c.package_id,k))}：排序权重${v}`).join('；')||'沿用时间排序'} · 最多${c.data.config.max_cases}条</p>
      <details><summary>查看学习依据（${c.data.sources.length}条）</summary>${c.data.sources.map(s=>learningMemory(s.id)).join('')}</details>
      ${canEdit&&['draft','failed','evaluated'].includes(c.status)?`<label for="eval-${c.id}">冻结评测集</label><select id="eval-${c.id}">${selectOptions(state.sets.filter(s=>s.status==='frozen'&&s.package_id===c.package_id),s=>s.title)}</select><button data-evaluate="${c.id}">排队评测</button>`:''}
      ${run?`<div class="evidence"><h3>最近评测</h3>${badge(run.status)}${run.data.error?`<p>${esc(run.data.error)}</p>`:''}
      ${run.data.metrics?`<p>固定${run.data.metrics.cases}例：基线正确${run.data.metrics.baseline_correct}例；候选正确${run.data.metrics.candidate_correct}例。</p><small>检索耗时${run.data.metrics.retrieval_ms}ms；本地评测不调用云模型。此指标仅衡量已标注经验选择。</small>
      <details><summary>逐例核查</summary>${run.data.results.map(r=>`<p>${esc(r.name)} · ${esc(learningCategories[r.category])} · 基线${r.baseline_correct?'符合':'不符'}／候选${r.candidate_correct?'符合':'不符'}</p>`).join('')}</details>`:'<p>后台处理并保存结果；可刷新或离开后继续。</p>'}</div>`:''}
      ${admin&&c.status==='evaluated'&&run?.status==='passed'?`<button data-shadow="${c.id}" data-run="${run.id}">核查通过后进入影子运行</button>`:''}</div>`;
  }).join('');
  const deployments=state.deployments.map(d=>`<div class="card"><h3>${esc(state.candidates.find(c=>c.id===d.candidate_id)?.title||'经验策略版本')}</h3>${badge(d.status)}
    <p>已记录${d.data.request_count}次查询；${d.status==='shadow'?'对用户返回基线，候选只做比较。':d.status==='canary'?`候选比例${d.data.percent}%，最多${d.data.max_requests}次查询。`:'版本与反馈保留。'}</p>
    ${d.data.rollback_reason?`<p>${esc(d.data.rollback_reason)}</p>`:''}
    ${admin&&d.status==='shadow'?`<label>候选比例<select data-percent="${d.id}"><option value="10">10%</option><option value="25">25%</option></select></label><button data-canary="${d.id}">核查影子反馈后有限发布</button>`:''}
    ${admin&&d.status==='canary'?`<button data-release="${d.id}">核查候选与对照反馈后发布</button>`:''}
    ${admin&&['shadow','canary','published'].includes(d.status)?`<button class="secondary" data-learning-rollback="${d.id}">回滚此版本</button>`:''}</div>`).join('');
  const trials=state.trials.map(t=>`<div class="card"><h3>${esc(t.data.request.context||'保存的证据请求')}</h3>${badge(t.status)}<p>${esc(learningCategories[t.data.stage]||({shadow:'影子',canary:'有限发布',published:'发布后'}[t.data.stage]))} · 本次使用${t.data.served==='candidate'?'候选':'基线'} · ${date(t.created_at)}</p>
    <div class="grid"><div><h3>基线第一条</h3>${learningMemory(t.data.baseline_ids[0],t.data.case_snapshots)}</div><div><h3>候选第一条</h3>${learningMemory(t.data.candidate_ids[0],t.data.case_snapshots)}</div></div>
    ${t.data.review?`<p>已保存核查：${esc(t.data.review.reason)}</p><p>基线${t.data.review.baseline_correct?'符合':'不符'}／候选${t.data.review.candidate_correct?'符合':'不符'}</p>`:''}
    ${canEdit&&t.status==='pending_review'?`<button data-trial-review="${t.id}">核查并保存反馈</button>`:''}</div>`).join('');
  return `<p class="muted">先比较固定回归集，再核查真实查询的影子与受限发布反馈。案例撤回、依据变更、核查失败或额度耗尽会停止候选；已完成成果保留。</p><div class="grid"><div>${left}${frozenSets}</div><div><h2>学习候选</h2>${candidates||empty('新案例审核后会自动提出候选；有标签才可评测。')}<h2>发布版本</h2>${deployments||empty('评测通过并经管理员核查后，先进入影子阶段。')}</div></div><h2>在线核查与反馈</h2>${trials||empty('影子运行后，本业务包的证据查询会保存基线与候选比较。')}`;
}

function bindLearning() {
  const state=learningOverview;
  const sourceSelect=$('#learningPackage');
  if(sourceSelect){sourceSelect.onchange=()=>{$('#learningSources').innerHTML=learningChoices(sourceSelect.value).map(m=>`<label class="choices"><input type="checkbox" value="${m.id}">${esc(m.data.goal||m.text)}</label>`).join('')||empty('该业务包尚无有效的已核实案例。')};sourceSelect.onchange();}
  $('#learningCandidateForm')?.addEventListener('submit',e=>{e.preventDefault();action(e.submitter,async()=>{await api('/learning/candidates','POST',{
    package_id:sourceSelect.value,title:$('#learningTitle').value,memory_ids:[...$('#learningSources input:checked')].map(x=>x.value)});await load();notice('候选及学习依据已保存，尚未改变运行策略。')})});
  $('#learningSetForm')?.addEventListener('submit',e=>{e.preventDefault();action(e.submitter,async()=>{await api('/learning/sets','POST',{package_id:$('#setPackage').value,title:$('#setTitle').value});await load();notice('评测草稿已保存，可以逐条核查标签。')})});
  document.querySelectorAll('[data-case-form]').forEach(form=>form.onsubmit=e=>{e.preventDefault();action(e.submitter,async()=>{
    const row=state.sets.find(s=>s.id===form.dataset.caseForm),get=n=>form.elements.namedItem(n).value;
    await api('/learning/sets/'+row.id+'/cases','POST',{expected_version:row.version,reason:'操作者核查标签并保存',case:{name:get('name'),category:get('category'),
      entity_ids:[...form.querySelectorAll('[name="entity"]:checked')].map(x=>x.value),fields:[...form.querySelectorAll('[name="field"]:checked')].map(x=>x.value),
      expected_status:get('expected_status'),expected_memory_ids:get('expected_memory')?[get('expected_memory')]:[],label_reason:get('label_reason')}});
    await load();notice('用例与核查依据已保存。')})});
  const review=(title,url,row,extra={})=>simpleReview(title,reason=>api(url,'POST',{expected_version:row.version,reason,...extra}));
  document.querySelectorAll('[data-freeze-set]').forEach(b=>b.onclick=()=>{const s=state.sets.find(s=>s.id===b.dataset.freezeSet);review('冻结已核查的五类回归标签','/learning/sets/'+s.id+'/freeze',s)});
  document.querySelectorAll('[data-remove-case]').forEach(b=>b.onclick=()=>{const s=state.sets.find(s=>s.id===b.dataset.set);review('移除草稿用例（保留恢复记录）','/learning/sets/'+s.id+'/cases/'+b.dataset.removeCase+'/remove',s)});
  document.querySelectorAll('[data-restore-set]').forEach(b=>b.onclick=()=>{const s=state.sets.find(s=>s.id===b.dataset.restoreSet);review('恢复最近移除的草稿用例','/learning/sets/'+s.id+'/restore',s)});
  document.querySelectorAll('[data-evaluate]').forEach(b=>b.onclick=()=>{const c=state.candidates.find(c=>c.id===b.dataset.evaluate);review('排队比较冻结回归集','/learning/candidates/'+c.id+'/evaluate',c,{set_id:$('#eval-'+c.id).value})});
  document.querySelectorAll('[data-shadow]').forEach(b=>b.onclick=()=>{const c=state.candidates.find(c=>c.id===b.dataset.shadow);review('批准只比较、不替换用户结果的影子版本','/learning/candidates/'+c.id+'/shadow',c,{run_id:b.dataset.run})});
  document.querySelectorAll('[data-canary]').forEach(b=>b.onclick=()=>{const d=state.deployments.find(d=>d.id===b.dataset.canary);review('限定业务包与查询额度的有限发布','/learning/deployments/'+d.id+'/canary',d,{percent:Number($('[data-percent="'+d.id+'"]').value),max_requests:30})});
  document.querySelectorAll('[data-release]').forEach(b=>b.onclick=()=>{const d=state.deployments.find(d=>d.id===b.dataset.release);review('批准该业务包的经验选择策略','/learning/deployments/'+d.id+'/publish',d)});
  document.querySelectorAll('[data-learning-rollback]').forEach(b=>b.onclick=()=>{const d=state.deployments.find(d=>d.id===b.dataset.learningRollback);review('回滚后续经验选择，保留已完成成果','/learning/deployments/'+d.id+'/rollback',d)});
  document.querySelectorAll('[data-trial-review]').forEach(b=>b.onclick=()=>{
    const trial=state.trials.find(t=>t.id===b.dataset.trialReview),dialog=document.createElement('dialog');dialog.className='card';dialog.style.maxWidth='660px';
    dialog.innerHTML=`<h2>核查并保存反馈</h2><p>${esc(trial.data.request.context)}</p><label for="trialExpected">正确的证据状态</label><select id="trialExpected">${['sufficient','insufficient','conflict'].map(s=>`<option value="${s}">${esc(statusLabels[s])}</option>`).join('')}</select>
    <label for="trialMemory">应排第一的经验</label><select id="trialMemory">${learningOptions(trial.data.request.entity_package)}</select><label for="trialReason">核查依据</label><textarea id="trialReason" required></textarea>
    <div class="actions"><button id="trialSave">保存反馈</button><button class="secondary" id="trialClose">取消</button></div>`;
    document.body.append(dialog);dialog.showModal();dialog.querySelector('#trialExpected').value=trial.data.status_at_query;
    dialog.querySelector('#trialClose').onclick=()=>dialog.remove();dialog.querySelector('#trialSave').onclick=e=>action(e.target,async()=>{
      const reason=dialog.querySelector('#trialReason').value.trim(),id=dialog.querySelector('#trialMemory').value;
      if(!reason)throw Error('请先填写核查依据');
      await api('/learning/trials/'+trial.id+'/review','POST',{expected_version:trial.version,reason,expected_status:dialog.querySelector('#trialExpected').value,expected_memory_ids:id?[id]:[]});
      dialog.remove();await load();notice('反馈已保存；发现不符时已执行恢复基线保护。');
    });
  });
}
