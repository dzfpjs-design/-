// Original questions stay immutable; evidence and next action remain adjacent.
const gapLabels={pending_delivery:'等待感知接收',received:'感知已接收',unresolved:'证据仍不足',
 needs_existing_evidence:'原目标仅允许既有证据',needs_authorization:'需要监测授权',needs_record:'需要有效档案',needs_historical_evidence:'需要历史证据',
 needs_review:'需要核查现有观测',needs_coordination:'需要协调采集条件',blocked_connector:'设备接口未接入',
 unknown_action:'已有动作结果未知',awaiting_observation:'等待补采观测',awaiting_collection:'等待数据归集回执',
 blocked_recovery:'后台处理受阻',verifying:'原需求复核中',resolved:'原需求已满足',cancelled:'请求已停止'};
async function showGapHistory(gap){
 const history=await api('/data/gaps/'+gap.id+'/history');
 const dialog=document.createElement('dialog');dialog.className='card';dialog.style.maxWidth='760px';dialog.style.maxHeight='85vh';dialog.style.overflowY='auto';
 const labels={detected:'发现缺口',received:'感知接收',perception_decision:'感知决定',verification_failed:'复核未通过',
  verified:'原需求复核通过',coordinated:'业务已接收协调请求',requested:'操作者请求',cancelled:'请求停止',worker_failed:'后台受阻'};
 dialog.innerHTML='<h2>补证与复核历史</h2><button>关闭补证历史</button><p>原查询保留，每次复核另存结果；复核不调用云模型。</p>'+history.items.map(h=>
  `<section class="evidence"><h3>${esc(labels[h.data.operation]||'保存的决定')}</h3><p>${esc(gapLabels[h.data.status]||'处理中')} · ${date(h.created_at)}</p><p>${esc(h.data.reason||'')}</p>${h.data.boundary?`<p>${esc(h.data.boundary)}</p>`:''}</section>`).join('')+
  (history.truncated?'<p>仅显示最近100条；更早记录仍保存在系统中。</p>':'');
 dialog.addEventListener('close',()=>dialog.remove(),{once:true});dialog.querySelector('button').onclick=()=>dialog.close();document.body.append(dialog);dialog.showModal();
}
function approveGapStrategy(gap,run){
 const entity=entities.find(e=>e.id===gap.entity_id),sources=caps.filter(c=>c.kind==='source'&&c.status==='published');
 const dialog=document.createElement('dialog');dialog.className='card';dialog.style.maxWidth='660px';
 const age=gap.data.request.evidence_requirement?.time?.max_age_seconds||3600;
 dialog.innerHTML=`<h2>批准此对象的补采范围</h2><p>${esc(entity?.data.name||entity?.external_id)} · ${esc(gap.data.missing_fields.map(f=>fieldLabel(entity,f)).join('、'))}</p>
 <p>策略允许感知在有效期内自主补采。下发前还会校验接口、资源与原授权；手工上传来源不能自动控制设备。</p>
 <form><label for="gapSource">允许使用的观测来源</label><select id="gapSource" required>${sources.map(s=>`<option value="${esc(s.id)}">${esc(s.data.description)} · ${s.data.execution.adapter==='http'&&s.data.execution.side_effect?'已登记控制接口，真实性待集成验收':'没有自动控制接口'}</option>`).join('')}</select>
 <label for="gapAge">观测最长年龄（秒）</label><input id="gapAge" type="number" min="60" max="2592000" value="${Math.max(60,Math.min(2592000,age))}" required>
 <label for="gapLimit">每日最多补采次数</label><input id="gapLimit" type="number" min="1" max="100" value="2" required>
 <label for="gapDuration">单次最长采集时间（秒）</label><input id="gapDuration" type="number" min="1" max="3600" value="300" required>
 <label for="gapUntil">授权到期时间</label><input id="gapUntil" type="datetime-local" required>
 <label for="gapReason">批准范围与限制的依据</label><textarea id="gapReason" required></textarea><p class="error" role="alert"></p>
 <div class="actions"><button>保存并批准补采策略</button><button type="button" class="secondary" data-close>取消</button></div></form>`;
 dialog.querySelector('#gapUntil').value=new Date(Date.now()+86400000-new Date().getTimezoneOffset()*60000).toISOString().slice(0,16);
 dialog.addEventListener('close',()=>dialog.remove(),{once:true});dialog.querySelector('[data-close]').onclick=()=>dialog.close();
 let saved;
 dialog.querySelector('form').onsubmit=e=>{e.preventDefault();action(e.submitter,async()=>{try{
  const reason=dialog.querySelector('#gapReason').value.trim();if(!reason)throw Error('请填写批准依据');
  const value={title:'补证策略 · '+run.question.slice(0,80),entity_ids:[gap.entity_id],source_ids:[dialog.querySelector('#gapSource').value],fields:gap.data.missing_fields,
   max_age_seconds:Number(dialog.querySelector('#gapAge').value),max_acquisitions_per_day:Number(dialog.querySelector('#gapLimit').value),max_duration_seconds:Number(dialog.querySelector('#gapDuration').value),
   cooldown_seconds:3600,valid_until:new Date(dialog.querySelector('#gapUntil').value).toISOString()};
  if(saved&&JSON.stringify(value)!==saved.value)throw Error('已保存策略草稿，请先核查该草稿；不能以不同范围覆盖原批准');
  if(!saved){const policy=await api('/monitor-policies','POST',value);saved={policy,value:JSON.stringify(value)};}
  await api('/monitor-policies/'+saved.policy.id+'/approve','POST',{expected_version:saved.policy.version,reason});
  dialog.close();await load();notice('监测策略已批准；核对后选择重新评估授权与补采。');
 }catch(error){dialog.querySelector('[role=alert]').textContent=error.message+(saved?'；监测草稿已持久保存，可在观测页继续批准。':'');}})};
 document.body.append(dialog);dialog.showModal();
}
function augmentDataGaps(){
 if(!['data','relations'].includes(current))return;
 if(gapsOverview.truncated){const p=document.createElement('p');p.textContent='当前仅加载最近100个缺口，更早记录仍保存；原查询与历史不被删除。';$('#content').append(p);}
 for(const gap of gapsOverview.items){
  const card=document.querySelector('[data-query="'+gap.query_id+'"]');if(!card)continue;
  const run=queries.find(q=>q.id===gap.query_id),entity=entities.find(e=>e.id===gap.entity_id),panel=document.createElement('section');panel.className='evidence';panel.dataset.dataGap=gap.id;
  const latest=queries.find(q=>q.id===gap.data.latest_recheck_query_id);
  panel.innerHTML=`<h3>感知补证 · ${esc(entity?.data.name||entity?.external_id||'对象')}</h3><p><b>${esc(gapLabels[gap.status]||'等待复核')}</b> · ${esc(gap.data.missing_fields.map(f=>fieldLabel(entity,f)).join('、'))}</p>
   <p>${esc(['received','verifying'].includes(gap.status)?'请求已保存，等待后台按原范围核验；未新增设备授权':gap.status==='resolved'?gap.data.resolution_reason:gap.data.decision_reason||gap.data.reason||'')}</p><small>${gap.data.received_at?'感知接收：'+date(gap.data.received_at):'送达回执尚未确认'} · 原查询继续保留</small>
   ${(gap.data.acquisition_ids||[]).map(id=>{const a=overview.acquisitions.find(x=>x.id===id);return a?`<p>原补采请求：${esc(gapLabels[a.status]||statusLabels[a.status]||'待核查')} · ${esc(a.data.reason||'')}</p>`:''}).join('')}
   ${latest?.data.result?`<h4>最近原需求复核</h4><p>${badge(latest.status)} · ${date(latest.created_at)}</p>${observationEvidence(latest.data.result.observation_evidence||[])}`:''}
   ${gap.data.business_received_at?`<p>业务中枢已接收协调请求 · ${date(gap.data.business_received_at)}。证据满足后仍须核查业务调查是否继续。</p>`:''}<p>补采动作完成不等于原需求已满足；新观测仍须提取、归集及按原日期/来源/确认等级复核。</p><div class="actions"></div>`;
  card.append(panel);const actions=panel.querySelector('.actions');
  const button=(text,fn,history=false)=>{const b=document.createElement('button');b.textContent=text;b.className='secondary';if(history)b.dataset.gapHistory=gap.id;b.onclick=fn;actions.append(b);};
  button('查看补证历史',()=>showGapHistory(gap).catch(e=>notice(e.message,true)),true);
  if(gap.data.business_task_id)button('查看原业务协调任务',()=>{current='tasks';render();const target=document.querySelector('[data-task="'+gap.data.business_task_id+'"]');if(target)target.scrollIntoView({block:'center'});else notice('原业务任务仍保存，请刷新或按任务列表定位。');});
  if(!['resolved','cancelled'].includes(gap.status)&&me.role!=='viewer'){
   button('按原需求重新复核',()=>simpleReview('重新核查原日期、来源与确认等级',reason=>api('/data/gaps/'+gap.id+'/recheck','POST',{expected_version:gap.version,reason})));
   button('重新评估授权与补采',()=>simpleReview('仅按仍有效的监测授权评估，不重发未知动作',reason=>api('/data/gaps/'+gap.id+'/retry','POST',{expected_version:gap.version,reason})));
   if(me.role==='admin'&&gap.status==='needs_authorization')button('批准此对象的补采范围',()=>approveGapStrategy(gap,run));
   button('停止此补证请求',()=>simpleReview('停止此请求；已派发或共用的设备动作仍需核对',reason=>api('/data/gaps/'+gap.id+'/cancel','POST',{expected_version:gap.version,reason})));
  }
 }
}
