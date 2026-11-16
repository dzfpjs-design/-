// Registered parameters are edited as human controls; source records remain server-bound.
const queryMatchModes={all_terms:'全部词项匹配',any_terms:'任意词项匹配'};
const queryOperations={mean:'平均值',sum:'合计',min:'最小值',max:'最大值',count:'证据记录数'};
function dataToolDestination(c){return c.data.execution.adapter==='cloud_retrieval'?c.data.description+' · dashscope.aliyuncs.com · '+(caps.find(m=>m.id===c.data.execution.model_profile_id)?.data.description||'向量模型'):c.data.description+' · '+new URL(c.data.execution.endpoint).host;}
function dataToolChoices(ids){
 const packages=[...new Set(ids.map(id=>entities.find(e=>e.id===id)?.schema_capability_id))];
 return caps.filter(c=>c.status==='published'&&c.data.execution.query_tool===true&&packages.every(id=>(caps.find(p=>p.id===id)?.data.specification.data_tool_ids||[]).includes(c.id)));
}
function dataToolSchema(cap){const schema=ordinaryNumericClone(cap?.data.input_schema||{properties:{},required:[]});
 for(const k of Object.keys(cap?.data.execution.input_bindings||{})){delete schema.properties[k];schema.required=(schema.required||[]).filter(x=>x!==k);}return schema;}
function ordinaryQueryNumericTokenV146(owner,key){
 if(!owner||!Object.hasOwn(owner,key)||typeof owner[key]!=='number')return null;
 const token=ordinaryNumericDisplay(owner,key);
 return /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$/.test(token)&&(!/[.eE]/.test(token)||Number.isFinite(Number(token)))?token:null;
}
// JSON Schema/Python compares integer values exactly, including against the
// actual finite IEEE float. A float spelling such as 1e23 is not an exact int.
function ordinaryQueryNumericOrderV146(left,right){
 const floating=token=>/[.eE]/.test(token),order=(a,b)=>a===b?0:a<b?-1:1;
 const lf=floating(left),rf=floating(right);
 if(!lf&&!rf)return order(BigInt(left),BigInt(right));
 if(lf&&rf)return order(Number(left),Number(right));
 const integer=BigInt(lf?right:left),number=Number(lf?left:right);
 const result=Number.isInteger(number)?order(integer,BigInt(number)):(integer<=BigInt(Math.floor(number))?-1:1);
 return lf?-result:result;
}
function mountQueryTools(host,run,draft){
 const choices=dataToolChoices(run.data.request.entity_ids),fields=needFieldCatalog(run);let steps=ordinaryNumericClone(draft.tool_steps||[]);
 const originals=new WeakMap(),numericEnums=new WeakMap();let enumSerial=0;
 const freeze=value=>{if(value&&typeof value==='object'){for(const child of Object.values(value))freeze(child);Object.freeze(value);}return value;};
 const remember=step=>originals.set(step,{capability_id:step.capability_id,parameters:freeze(ordinaryNumericClone(step.parameters||{}))});
 steps.forEach(remember);
 const read=(discard=-1)=>{host.querySelectorAll('[data-tool-row]').forEach(row=>{const i=Number(row.dataset.toolRow),s=steps[i];if(i===discard)return;
  s.capability_id=row.querySelector('[data-tool-cap]').value;s.input_from=row.querySelector('[data-tool-source]').value;
  const cap=choices.find(c=>c.id===s.capability_id),schema=dataToolSchema(cap);
  for(const el of row.querySelectorAll('[data-tool-param]')){const k=el.dataset.toolParam,definition=schema.properties?.[k]||{},label=definition.title||k;
   if(el.value===''){ordinaryNumericDelete(s.parameters,k);continue;}
   if(['number','integer'].includes(el.dataset.type)){
    const context=numericEnums.get(el);
    if(Array.isArray(definition.enum)){
     if(!context||context.step!==s||context.field!==k||context.capability_id!==s.capability_id
       ||context.capability_hash!==cap?.content_hash||context.capability_version!==cap?.version
       ||context.tokens.length!==definition.enum.length||context.tokens.some((token,index)=>token!==ordinaryQueryNumericTokenV146(definition.enum,index)))throw Error(label+'：登记选项已经变化，请保留原稿后重新核对');
     const edited=el.dataset.numericEdited==='true',original=originals.get(s);
     if(el.value===context.originalID&&context.originalToken!==null){
      if(!original||original.capability_id!==s.capability_id)throw Error(label+'：原已保存值无法核实');
      if(edited)ordinaryNumericCopyField(s.parameters,k,original.parameters,k);
      else if(ordinaryQueryNumericTokenV146(s.parameters,k)!==context.originalToken)throw Error(label+'：原数值已经变化，请重新选择');
     }else if(el.value===context.currentID&&!edited){
      if(ordinaryQueryNumericTokenV146(s.parameters,k)!==context.currentToken)throw Error(label+'：本次数值无法核实');
     }else if(context.options.has(el.value)){
      const token=context.tokens[context.options.get(el.value)];if(token===null)throw Error(label+'：数值原文未核实，请重新选择');
      if(edited)ordinaryNumericSet(s.parameters,k,token,el.dataset.type,label);
      else{const present=ordinaryQueryNumericTokenV146(s.parameters,k);if(present===null||ordinaryQueryNumericOrderV146(present,token)!==0)throw Error(label+'：本次选项与原数值不一致');}
      const actual=ordinaryQueryNumericTokenV146(s.parameters,k);
      if(actual!==token){let current=el.querySelector('[data-numeric-current]');if(!current){current=document.createElement('option');current.dataset.numericCurrent='';current.value=context.currentID;current.disabled=true;el.append(current);}
       current.textContent=actual+'（本次值；登记选项 '+token+'）';context.currentToken=actual;el.value=context.currentID;}
     }else throw Error(label+'：原数值未核实，请重新选择登记值');
     el.dataset.numericEdited='false';
    }else if(el.dataset.numericEdited==='true')ordinaryNumericSet(s.parameters,k,el.value,el.dataset.type,label);
    else if(typeof s.parameters[k]!=='number'||!/^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$/.test(ordinaryNumericDisplay(s.parameters,k)))throw Error(label+'：数值原文未核实，请重新填写');
    const token=ordinaryQueryNumericTokenV146(s.parameters,k);if(token===null)throw Error(label+'：数值原文未核实，请重新填写');
    for(const [bound,direction,equal] of [['minimum',-1,false],['maximum',1,false],['exclusiveMinimum',-1,true],['exclusiveMaximum',1,true]])if(definition[bound]!==undefined){
     const limit=ordinaryQueryNumericTokenV146(definition,bound);if(limit===null)throw Error(label+'：登记数值范围未核实');const compared=ordinaryQueryNumericOrderV146(token,limit);
     if(compared===direction||(equal&&compared===0))throw Error(label+'：数值不在登记允许范围内；原输入保留');}
    if(Array.isArray(definition.enum)&&!definition.enum.some((item,index)=>{const member=ordinaryQueryNumericTokenV146(definition.enum,index);return member!==null&&ordinaryQueryNumericOrderV146(token,member)===0;}))throw Error(label+'：数值不在登记允许范围内；原输入保留');
   }else{ordinaryNumericDelete(s.parameters,k);s.parameters[k]=el.dataset.type==='boolean'?el.value==='true':el.value;}
  }
 });return ordinaryNumericClone(steps);};
 const render=()=>{const contexts=[];host.innerHTML='<h3>登记查询工具与依赖</h3><p>参数用于本次原范围证据；原始记录由服务器绑定。后序工具可使用前序结果，最多6步。计算结果保留来源，原档案不被改写。</p>'+steps.map((s,i)=>{
  const cap=choices.find(c=>c.id===s.capability_id),schema=dataToolSchema(cap),previous=steps.slice(0,i),derived={...fields};
  for(const a of previous){const prev=choices.find(c=>c.id===a.capability_id);Object.assign(derived,prev?.data.output_schema.properties.records.items.properties.values.properties||{});}
  const params=Object.entries(schema.properties||{}).map(([k,v])=>{const value=s.parameters[k],id='toolParam_'+i+'_'+k,label=esc(v.title||k),required=(schema.required||[]).includes(k)?'required':'';
   if(k==='field')return `<label for="${id}">${label}</label><select id="${id}" data-tool-param="${esc(k)}" data-type="string" ${required}><option value="">请选择字段</option>${Object.entries(derived).map(([f,d])=>`<option value="${esc(f)}" ${f===value?'selected':''}>${esc(d.title||f)}</option>`).join('')}</select>`;
   if(Array.isArray(v.enum)&&['number','integer'].includes(v.type)){
    const serial=++enumSerial,original=originals.get(s),originalToken=original?.capability_id===s.capability_id?ordinaryQueryNumericTokenV146(original.parameters,k):null,
      currentToken=ordinaryQueryNumericTokenV146(s.parameters,k),tokens=v.enum.map((item,index)=>ordinaryQueryNumericTokenV146(v.enum,index)),options=new Map(tokens.map((token,index)=>['registered:'+serial+':'+index,index])),
      originalID='original:'+serial,currentID='current:'+serial,unknownID='unverified:'+serial;
    let selected=currentToken!==null&&currentToken===originalToken?originalID:'';
    if(!selected&&currentToken!==null){const index=tokens.findIndex(token=>token===currentToken);selected=index>=0?'registered:'+serial+':'+index:currentID;}
    if(!selected&&value!==undefined)selected=unknownID;
    const context={step:s,field:k,capability_id:s.capability_id,capability_hash:cap?.content_hash,capability_version:cap?.version,
      tokens,options,originalID,originalToken,currentID,currentToken};const index=contexts.push(context)-1;
    return `<label for="${id}">${label}</label><select id="${id}" data-tool-param="${esc(k)}" data-type="${v.type}" data-numeric-enum="${index}" ${required}><option value="" ${selected===''?'selected':''}>请选择</option>`
      +(originalToken!==null?`<option value="${originalID}" ${selected===originalID?'selected':''}>${esc(originalToken)}（原已保存值）</option>`:'')
      +(selected===unknownID?`<option value="${unknownID}" selected>原数值未核实，请重新选择登记值</option>`:'')
      +(selected===currentID?`<option data-numeric-current value="${currentID}" selected disabled>${esc(currentToken)}（本次值）</option>`:'')
      +tokens.map((token,choice)=>`<option value="registered:${serial}:${choice}" ${selected==='registered:'+serial+':'+choice?'selected':''} ${token===null?'disabled':''}>${esc(token===null?'登记数值原文未核实':token)}</option>`).join('')+'</select>';
   }
   if(v.enum||v.type==='boolean'){const options=v.enum||['true','false'];return `<label for="${id}">${label}</label><select id="${id}" data-tool-param="${esc(k)}" data-type="${v.type||'string'}" ${required}><option value="">请选择</option>${options.map((x,optionIndex)=>`<option value="${esc(ordinaryNumericDisplay(options,optionIndex))}" ${String(x)===String(value)?'selected':''}>${esc(k==='operation'||k==='match_mode'?queryOperations[x]||queryMatchModes[x]||x:x===true||x==='true'?'是':x===false||x==='false'?'否':ordinaryNumericDisplay(options,optionIndex))}</option>`).join('')}</select>`;}
   if(['number','integer','string'].includes(v.type))return `<label for="${id}">${label}</label><input id="${id}" data-tool-param="${esc(k)}" data-type="${v.type}" type="text" ${v.type==='string'?'':`inputmode="${v.type==='integer'?'numeric':'decimal'}"`} value="${esc(value===undefined?'':ordinaryNumericDisplay(s.parameters,k))}" ${required}>`;
   return `<p>${label}：此参数需要专用表单，已保存的候选值保留；可移除此步骤并改选已支持的工具。</p>`;
  }).join('');
  return `<section class="evidence" data-tool-row="${i}"><h4>第${i+1}步</h4><label for="toolCap_${i}">登记能力</label><select id="toolCap_${i}" data-tool-cap><option value="">请选择工具</option>${choices.map(c=>`<option value="${esc(c.id)}" ${s.capability_id===c.id?'selected':''}>${esc(c.data.description)} · ${esc(c.release_version)}</option>`).join('')}</select><label for="toolSource_${i}">输入依据</label><select id="toolSource_${i}" data-tool-source>${!(['evidence',...previous.map(a=>a.id)].includes(s.input_from))?`<option value="${esc(s.input_from)}" selected>依赖已移除，请重新选择输入</option>`:''}<option value="evidence" ${s.input_from==='evidence'?'selected':''}>原查询证据</option>${previous.map((a,j)=>`<option value="${esc(a.id)}" ${s.input_from===a.id?'selected':''}>第${j+1}步结果</option>`).join('')}</select>${params}<button type="button" class="secondary" data-tool-remove="${i}">移除此步骤</button></section>`;
 }).join('')+'<button type="button" class="secondary" data-tool-add>添加登记查询工具</button>';
 host.querySelector('[data-tool-add]').disabled=!choices.length||steps.length>=6;
 host.querySelectorAll('[data-numeric-enum]').forEach(el=>numericEnums.set(el,contexts[Number(el.dataset.numericEnum)]));
 const edit=fn=>{try{fn();}catch(error){const target=host.closest('dialog')?.querySelector('#needError');if(target)target.textContent=error.message;else notice(error.message,true);}};
 host.querySelector('[data-tool-add]').onclick=()=>edit(()=>{read();let n=steps.length+1;while(steps.some(s=>s.id==='query_'+n))n++;const step={id:'query_'+n,capability_id:choices[0].id,input_from:steps.at(-1)?.id||'evidence',parameters:{}};steps.push(step);remember(step);render();});
 host.querySelectorAll('[data-tool-remove]').forEach(b=>b.onclick=()=>edit(()=>{const index=Number(b.dataset.toolRemove);read(index);steps.splice(index,1);render();}));
 host.querySelectorAll('[data-tool-cap]').forEach(el=>el.onchange=()=>edit(()=>{const index=Number(el.closest('[data-tool-row]').dataset.toolRow),next=el.value;read(index);steps[index].capability_id=next;steps[index].parameters={};remember(steps[index]);render();}));
 host.querySelectorAll('[data-tool-param]').forEach(el=>{for(const event of ['input','change'])el.addEventListener(event,()=>{el.dataset.numericEdited='true';});});
 };render();return read;
}
function queryToolPlanPreview(run){const steps=run.data.need?.tool_steps||run.data.need_checkpoint?.value?.tool_steps||run.data.request.tool_steps||[];
 return steps.length?'<section class="evidence"><h3>登记查询计划</h3>'+steps.map((s,i)=>`<p>第${i+1}步：${esc(caps.find(c=>c.id===s.capability_id)?.data.description||'待核查的工具')} · ${s.input_from==='evidence'?'原查询证据':'前序结果'}</p>`).join('')+'</section>':'';}
function appendQueryTools(run,card){card.insertAdjacentHTML('beforeend',queryToolPlanPreview(run));
  const result=run.data.result,trace=result?.tool_trace||run.data.tool_trace||[];
  if(result?.tool_blocker){const p=document.createElement('p');p.className='notice error';p.textContent=result.tool_blocker;card.append(p);}
  if(trace.length){const section=document.createElement('section');section.className='evidence';section.innerHTML='<h3>查询执行回执</h3>'+trace.map((t,i)=>{
   const cap=caps.find(c=>c.id===t.capability_id),parameters=t.step.parameters;
   return `<section><h4>第${i+1}步 · ${esc(t.title)} · ${esc(t.release_version)}</h4>${badge(t.status)}${Object.entries(parameters).map(([k,v])=>`<p>${esc(cap?.data.input_schema.properties[k]?.title||k)}：${esc(k==='operation'?queryOperations[v]||v:k==='field'?needFieldCatalog(run)[v]?.title||v:k==='match_mode'?queryMatchModes[v]||v:ordinaryNumericDisplay(parameters,k))}</p>`).join('')}<p>输入${Object.values(t.inputs||{}).find(x=>Array.isArray(x))?.length||0}条证据记录${t.error?' · '+esc(t.error):''}</p></section>`;
  }).join('');card.append(section);}
  const searches=trace.filter(t=>['bounded_fulltext_bm25','source_preserving_semantic_ranking'].includes(t.output?.summary?.method));
  if(searches.length){const section=document.createElement('section');section.className='evidence';section.innerHTML='<h3>检索范围与排序依据</h3>'+searches.map(t=>{const s=t.output.summary;return `<p>${s.method==='bounded_fulltext_bm25'?'授权记录全文排序':'授权记录语义排序'}：读取${esc(ordinaryNumericDisplay(s,'scanned_records'))}条，匹配${esc(ordinaryNumericDisplay(s,'matched_records'))}条，返回${esc(ordinaryNumericDisplay(s,'returned_records'))}条。</p><p>只返回本次范围内的排序子集；后序汇总只统计该子集。无匹配不能证明其他范围不存在对象。相似度不代表事实已确认。</p>${s.model?`<p>向量模型：${esc(s.model)} · ${esc(ordinaryNumericDisplay(s,'dimensions'))}维 · 新建${esc(ordinaryNumericDisplay(s,'new_vectors'))}条，复用${esc(ordinaryNumericDisplay(s,'cached_vectors'))}条</p>`:''}${(s.ranking||[]).map(rank=>{const record=t.output.records.find(r=>JSON.stringify(r.evidence_refs)===JSON.stringify(rank.evidence_refs));return `<p>${esc((record?.entity_ids||[]).map(relationName).join('、'))} · ${s.method==='source_preserving_semantic_ranking'?'余弦相似度':'全文匹配分'} ${esc(ordinaryNumericDisplay(rank,'score'))}</p>`;}).join('')}`;}).join('');card.append(section);}
  if(result?.derived_records){const section=document.createElement('section');section.className='evidence';section.dataset.derivedQuery=run.id;
   section.innerHTML='<h3>登记查询工具的推导结果</h3>'+result.derived_records.map(r=>`<div class="evidence">${Object.entries(r.values).map(([k,v])=>`<div class="kv"><span>${esc(({value:'计算结果',sample_count:'证据记录数',field:'依据字段',operation:'计算方式'})[k]||needFieldCatalog(run)[k]?.title||k)}</span><b>${esc(k==='field'?needFieldCatalog(run)[v]?.title||v:k==='operation'?queryOperations[v]||v:ordinaryNumericDisplay(r.values,k))}</b></div>`).join('')}<p>对象来源：${esc(r.entity_ids.map(relationName).join('、'))} · ${r.evidence_refs.length}条版本证据</p></div>`).join('')+`<p>${result.derivation_verification==='deterministic_registered_computation'?'按登记规则计算，样本单位是本次已授权证据记录。':result.derivation_verification==='source_preserving_semantic_ranking'?'语义模型仅排序原来源证据，未增加确认等级。':result.derivation_verification==='human_confirmed_extension'?'外部推导已保存人工确认与依据。':result.derivation_verification==='human_rejected_extension'?'外部推导已被人工拒绝，不能作为已确认结果使用。':'外部服务推导尚需专业核查。'}原档案与观测保留原值。</p>`;card.append(section);
   if(run.status==='needs_review'&&me.role!=='viewer'){for(const [decision,label] of [['confirmed','核查并确认推导'],['rejected','拒绝此推导']]){const b=document.createElement('button');b.textContent=label;b.className='secondary';b.onclick=()=>simpleReview('核对原证据与推导正确性，保存专业核查依据',reason=>api('/data/queries/'+run.id+'/review-derived','POST',{expected_version:run.version,reason,decision}));section.append(b);}}
   if(result.derivation_verification==='human_confirmed_extension'&&me.role!=='viewer'){const b=document.createElement('button');b.textContent='撤回此推导的核查确认';b.className='secondary';b.onclick=()=>simpleReview('保存撤回依据，旧验收历史保留，后续不能复用此推导',reason=>api('/data/queries/'+run.id+'/review-derived','POST',{expected_version:run.version,reason,decision:'rejected'}));section.append(b);}
  }
}
function augmentQueryTools(){if(!['data','relations'].includes(current))return;for(const run of queries){if(dataQueryReferenceLike(run))continue;const card=document.querySelector('[data-query="'+run.id+'"]');if(card)appendQueryTools(run,card);}}
