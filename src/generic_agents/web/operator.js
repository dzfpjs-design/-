// Operator controls sit next to saved evidence. Every decision is persisted first.
// Ordinary API records keep numeric source text beside the plain JS projection.
// Only Clone/Set/Delete transfer or change that binding. Direct equal-value
// assignment and structuredClone cannot convey whether 1 was originally 1.0.
const ordinaryNumericCodecV145=(()=>{
  const tokens=new WeakMap(),MAX_BYTES=24*1024*1024,MAX_DEPTH=128,MAX_VALUES=1000000,MAX_TOKEN=2048;
  const UNKNOWN='数值原文未核实，请重新填写',ERROR='数值或原文无法准确核对，请保留输入后重试。';
  const need=(ok,message=ERROR)=>{if(!ok)throw Error(message);};
  const jsonNumber=/-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/y;
  const safeString=value=>typeof value==='string'&&!/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value);
  const bytes=value=>new TextEncoder().encode(value).length;
  function fields(value){
    need(value!==null&&typeof value==='object'&&!Object.getOwnPropertySymbols(value).length);
    const array=Array.isArray(value),prototype=Object.getPrototypeOf(value);
    need(array?prototype===Array.prototype:prototype===Object.prototype||prototype===null);
    const descriptors=Object.getOwnPropertyDescriptors(value),keys=Object.keys(descriptors);
    if(array)need(value.length<=MAX_VALUES&&keys.length===value.length+1&&keys.includes('length')
      &&Array.from({length:value.length},(_,i)=>String(i)).every(key=>Object.hasOwn(descriptors,key)));
    return keys.filter(key=>!(array&&key==='length')).map(key=>{
      const descriptor=descriptors[key];
      need(safeString(key)&&descriptor.enumerable&&Object.hasOwn(descriptor,'value'));
      return [key,descriptor.value];
    });
  }
  function bind(owner,key,record){
    let map=tokens.get(owner);if(!map){map=new Map();tokens.set(owner,map);}map.set(String(key),record);
  }
  function bound(owner,key,value){
    const map=tokens.get(owner),record=map?.get(String(key));
    if(record&&!Object.is(record.value,value)){map.delete(String(key));return null;}
    return record||null;
  }
  function put(owner,key,value,record){
    Object.defineProperty(owner,key,{value,writable:true,enumerable:true,configurable:true});
    if(record)bind(owner,key,record);
  }
  function sourceRecord(token){
    need(token.length<=MAX_TOKEN);
    const value=Number(token),kind=/[.eE]/.test(token)?'float':'integer';
    // Huge source integers are opaque exact tokens, even beyond the Number
    // projection. A non-finite source float is not a valid finite result.
    need(kind==='integer'||Number.isFinite(value));
    return {token,value,kind,source:'response'};
  }
  class Reader{
    constructor(text){need(typeof text==='string'&&bytes(text)<=MAX_BYTES);this.text=text;this.at=0;this.count=0;}
    space(){while(/[\x20\t\r\n]/.test(this.text[this.at]||'!'))this.at++;}
    string(){
      const start=this.at++;need(this.text[start]==='"');let closed=false;
      while(this.at<this.text.length){const char=this.text[this.at++];if(char==='"'){closed=true;break;}
        need(char.charCodeAt(0)>=32);if(char==='\\')this.at++;}
      need(closed);let result;try{result=JSON.parse(this.text.slice(start,this.at));}catch{need(false);}
      need(safeString(result));return result;
    }
    value(depth=0){
      this.space();need(depth<=MAX_DEPTH&&++this.count<=MAX_VALUES);const char=this.text[this.at];
      if(char==='{'){
        this.at++;this.space();const value={},seen=new Set();
        if(this.text[this.at]!=='}')while(true){
          this.space();const key=this.string();need(!seen.has(key));seen.add(key);this.space();need(this.text[this.at++]===':');
          const child=this.value(depth+1);put(value,key,child.value,child.record);this.space();
          if(this.text[this.at]!==',')break;this.at++;
        }
        need(this.text[this.at++]==='}');return {value};
      }
      if(char==='['){
        this.at++;this.space();const value=[];
        if(this.text[this.at]!==']')while(true){
          const child=this.value(depth+1);put(value,String(value.length),child.value,child.record);this.space();
          if(this.text[this.at]!==',')break;this.at++;
        }
        need(this.text[this.at++]===']');return {value};
      }
      if(char==='"')return {value:this.string()};
      for(const [word,value] of [['true',true],['false',false],['null',null]]){
        if(this.text.startsWith(word,this.at)){this.at+=word.length;return {value};}
      }
      jsonNumber.lastIndex=this.at;const match=jsonNumber.exec(this.text);
      need(match&&match.index===this.at);this.at=jsonNumber.lastIndex;const record=sourceRecord(match[0]);
      return {value:record.value,record};
    }
  }
  function parse(text){
    const reader=new Reader(text),result=reader.value();reader.space();need(reader.at===text.length);
    // This record/list API cannot bind a numeric root primitive to an owner.
    need(!result.record,'响应数字缺少原记录范围，暂不能无损读取。');return result.value;
  }
  function scalar(value,record){
    if(typeof value==='number'){
      if(record)return record.token;
      need(Number.isFinite(value)&&(!Number.isInteger(value)||Number.isSafeInteger(value)),
        '整数或数值原文缺失，不能舍入后发送；请保留输入。');
      return Object.is(value,-0)?'-0.0':JSON.stringify(value);
    }
    need(value===null||typeof value==='boolean'||safeString(value));return JSON.stringify(value);
  }
  function walk(value,copy){
    need(typeof value!=='number','数字缺少原记录范围，不能直接复制或发送。');
    if(copy)walk(value,false); // Includes byte limits before allocating a clone.
    const seen=new WeakSet();let count=0;
    function visit(item,owner,key,depth=0){
      need(depth<=MAX_DEPTH&&++count<=MAX_VALUES);
      const record=owner?bound(owner,key,item):null;
      if(item===null||typeof item!=='object'){
        const wire=scalar(item,record);return copy?item:wire;
      }
      need(!seen.has(item));seen.add(item);const entries=fields(item),array=Array.isArray(item),result=array?[]:{};
      const parts=[];
      for(const [name,child] of entries){
        const next=visit(child,item,name,depth+1);
        if(copy)put(result,name,next,typeof child==='number'?bound(item,name,child):null);
        else parts.push((array?'':JSON.stringify(name)+':')+next);
      }
      seen.delete(item);return copy?result:(array?'['+parts.join(',')+']':'{'+parts.join(',')+'}');
    }
    const result=visit(value,null,null);
    if(!copy)need(bytes(result)<=MAX_BYTES);
    return result;
  }
  function decimal(token){
    const negative=token.startsWith('-'),parts=(negative?token.slice(1):token).toLowerCase().split('e');
    const point=parts[0].split('.');let digits=(point[0]+(point[1]||'')).replace(/^0+/,'');
    if(!digits)return '0';
    const trailing=digits.match(/0+$/)?.[0].length||0;
    const exponent=BigInt(parts[1]||'0')-BigInt((point[1]||'').length)+BigInt(trailing);
    if(trailing)digits=digits.slice(0,-trailing);
    return (negative?'-':'')+digits+'e'+exponent.toString();
  }
  function fresh(raw,kind,label){
    need(kind==='number'||kind==='integer',label+'的数值类型无法核对。');
    if(typeof raw==='number'){
      need(Number.isFinite(raw)&&(!Number.isInteger(raw)||Number.isSafeInteger(raw)),label+'超出可准确填写的范围，请保留原文。');
      raw=Object.is(raw,-0)?'-0.0':String(raw);
    }
    need(typeof raw==='string'&&raw.trim().length>0&&raw.length<=MAX_TOKEN,label+'尚未填写完整。');
    let token=raw.trim();
    need(/^[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(token),label+'须填写完整的十进制数值。');
    token=token.replace(/^\+/,'').replace(/^(-?)\./,'$10.').replace(/^(-?)0+(?=\d)/,'$1');
    let value=Number(token);
    need(Number.isFinite(value)&&decimal(token)===decimal(String(value)),label+'的精度无法准确保存，请保留原输入。');
    if(kind==='integer'){
      need(Number.isSafeInteger(value)&&!Object.is(value,-0),label+'须为安全范围内的完整整数，不能使用负零。');
      token=String(value);
    }else{
      if(token==='-0')token='-0.0';
      need(/[.eE]/.test(token)||Number.isSafeInteger(value),label+'整数超出安全范围，请保留原输入。');
    }
    value=Number(token);return {token,value,kind:/[.eE]/.test(token)?'float':'integer',source:'edit'};
  }
  function set(owner,key,raw,kind='number',label='数值'){
    fields(owner);need(typeof key==='string'||Number.isSafeInteger(key));key=String(key);need(safeString(key));
    if(Array.isArray(owner))need(/^(?:0|[1-9]\d*)$/.test(key)&&Number(key)<=owner.length);
    const record=fresh(raw,kind,label);put(owner,key,record.value,record);return record.value;
  }
  function remove(owner,key){
    fields(owner);need(typeof key==='string'||Number.isSafeInteger(key));key=String(key);
    need(!Array.isArray(owner)||key!=='length');delete owner[key];tokens.get(owner)?.delete(key);
  }
  function copyField(target,key,source,sourceKey=key){
    fields(target);need(!Array.isArray(target));fields(source);
    need((typeof key==='string'||Number.isSafeInteger(key))
      &&(typeof sourceKey==='string'||Number.isSafeInteger(sourceKey)));
    key=String(key);sourceKey=String(sourceKey);need(safeString(key)&&safeString(sourceKey));
    if(Array.isArray(source))need(/^(?:0|[1-9]\d*)$/.test(sourceKey)&&Number(sourceKey)<source.length);
    const descriptor=Object.getOwnPropertyDescriptor(source,sourceKey);
    need(descriptor&&descriptor.enumerable&&Object.hasOwn(descriptor,'value'));
    const seen=new WeakSet();let count=0;
    function trusted(value,owner,name,depth=0){
      need(depth<=MAX_DEPTH&&++count<=MAX_VALUES);
      if(typeof value==='number'){
        need(bound(owner,name,value),'来源数值原文未核实，不能当作原选项恢复；原输入保留。');return;
      }
      if(value!==null&&typeof value==='object'){
        need(!seen.has(value));seen.add(value);
        for(const [field,child] of fields(value))trusted(child,value,field,depth+1);
        seen.delete(value);
      }else scalar(value,null);
    }
    trusted(descriptor.value,source,sourceKey);
    // A temporary own field carries the verified root token through the same
    // bounded clone as nested fields. All checks finish before target changes.
    const holder={};put(holder,'value',descriptor.value,
      typeof descriptor.value==='number'?bound(source,sourceKey,descriptor.value):null);
    const copied=walk(holder,true),value=copied.value;
    put(target,key,value,typeof value==='number'?bound(copied,'value',value):null);
    if(typeof value!=='number')tokens.get(target)?.delete(key);
    return value;
  }
  function display(owner,key){
    fields(owner);const descriptor=Object.getOwnPropertyDescriptor(owner,String(key));
    need(descriptor&&Object.hasOwn(descriptor,'value'));
    let count=0;const seen=new WeakSet();
    function show(value,parent,name,depth=0,nested=false){
      need(depth<=MAX_DEPTH&&++count<=MAX_VALUES);
      if(typeof value==='number')return bound(parent,name,value)?.token||(nested?JSON.stringify(UNKNOWN):UNKNOWN);
      if(value!==null&&typeof value==='object'){
        need(!seen.has(value));seen.add(value);const array=Array.isArray(value);
        const parts=fields(value).map(([field,child])=>(array?'':JSON.stringify(field)+':')+show(child,value,field,depth+1,true));
        seen.delete(value);return array?'['+parts.join(',')+']':'{'+parts.join(',')+'}';
      }
      need(value===null||typeof value==='boolean'||safeString(value));
      return typeof value==='string'&&!nested?value:JSON.stringify(value);
    }
    const result=show(descriptor.value,owner,String(key));need(bytes(result)<=MAX_BYTES);return result;
  }
  return {parse,stringify:value=>walk(value,false),clone:value=>walk(value,true),display,set,remove,copyField};
})();
function ordinaryNumericParse(text){return ordinaryNumericCodecV145.parse(text);}
function ordinaryNumericStringify(value){return ordinaryNumericCodecV145.stringify(value);}
function ordinaryNumericClone(value){return ordinaryNumericCodecV145.clone(value);}
function ordinaryNumericDisplay(owner,key){return ordinaryNumericCodecV145.display(owner,key);}
function ordinaryNumericSet(owner,key,raw,kind='number',label='数值'){return ordinaryNumericCodecV145.set(owner,key,raw,kind,label);}
function ordinaryNumericDelete(owner,key){return ordinaryNumericCodecV145.remove(owner,key);}
function ordinaryNumericCopyField(target,key,source,sourceKey=key){return ordinaryNumericCodecV145.copyField(target,key,source,sourceKey);}

// Domain-owned queries remain discoverable without entering the generic writer.
function dataQueryReferenceLike(value) {
  return value && typeof value==='object' && ('reference_only' in value || 'schema' in value || 'family' in value);
}
function dataQueryExact(value,keys) {
  return value!==null && typeof value==='object' && !Array.isArray(value)
    && Object.keys(value).length===keys.length && keys.every(key=>Object.prototype.hasOwnProperty.call(value,key));
}
function dataQueryUUID(value) {return typeof value==='string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value);}
function checkedDataQueryReference(value) {
  const fail=()=>{throw Error('原业务查询引用异常，暂不可核实；原记录未被改写。');};
  if(!dataQueryExact(value,['schema','id','version','status','question','created_at','reference_only','family','context','notice'])
    ||value.schema!=='data-query-reference-v144'||value.reference_only!==true||!dataQueryUUID(value.id)
    ||!Number.isSafeInteger(value.version)||value.version<1||typeof value.status!=='string'||!value.status
    ||typeof value.question!=='string'||typeof value.notice!=='string'
    ||typeof value.created_at!=='string'||!Number.isFinite(Date.parse(value.created_at))
    ||!['native_candidate','native_task'].includes(value.family)
    ||!dataQueryExact(value.context,['instance_id','task_id','detail_path']))fail();
  const context=value.context;
  if((context.instance_id!==null&&!dataQueryUUID(context.instance_id))
    ||(context.task_id!==null&&!dataQueryUUID(context.task_id))
    ||context.detail_path!=='/api/data/queries/'+encodeURIComponent(value.id))fail();
  return value;
}
function dataQueryTaskLink(instance,task) {
  return dataQueryUUID(instance)&&dataQueryUUID(task)
    ?`<a data-query-task-context href="/setup/?view=workspace&instance=${encodeURIComponent(instance)}&task=${encodeURIComponent(task)}">打开原业务任务</a>`:'';
}
function dataQueryReferenceCard(raw) {
  let row;try{row=checkedDataQueryReference(raw);}catch(error){
    return `<article class="card" data-query-reference="${esc(raw?.id||'unverified')}"><h3>原业务查询</h3><p role="status">${esc(error.message)}</p></article>`;
  }
  return `<article class="card" data-query-reference="${esc(row.id)}"><h3>${esc(row.question)}</h3>${badge(row.status)} <small>${date(row.created_at)}</small>
    <p>原业务查询 · ${row.family==='native_candidate'?'已保存候选资料检索':'原任务中的查询'}</p><p>${esc(row.notice)}</p>
    <p class="muted">此处只核对原记录；调整条件、取消或继续处理请回到原业务。</p><div class="actions">${row.family==='native_candidate'?'<button type="button" class="secondary" data-query-reference-read>核对原候选查询</button>':''}${dataQueryTaskLink(row.context.instance_id,row.context.task_id)}</div>
    <div data-query-reference-result></div><p data-query-reference-error role="alert"></p></article>`;
}
function checkedDataQueryCandidate(value,reference) {
  const fail=()=>{throw Error('原候选查询响应无法核实；未采用其中内容。');};
  if(!dataQueryExact(value,['schema','id','version','status','request_id','request','job_id','integrity_error','source_changed','not_authoritative','candidate_only','records','truncated'])
    ||value.schema!=='native-candidate-query-view-v139'||value.id!==reference.id||!Number.isSafeInteger(value.version)||value.version<reference.version
    ||typeof value.status!=='string'||!dataQueryUUID(value.request_id)||!dataQueryUUID(value.job_id)
    ||value.not_authoritative!==true||value.candidate_only!==true||typeof value.source_changed!=='boolean'
    ||typeof value.truncated!=='boolean'||(value.integrity_error!==null&&typeof value.integrity_error!=='string')
    ||!Array.isArray(value.records)||!dataQueryExact(value.request,['request_id','release_id','entity_id','field','text']))fail();
  const request=value.request;
  if(request.request_id!==value.request_id||!dataQueryUUID(request.release_id)||!dataQueryUUID(request.entity_id)
    ||!['search_text','review_status'].includes(request.field)||typeof request.text!=='string')fail();
  if((value.integrity_error!==null||value.source_changed)&&value.records.length)fail();
  for(const record of value.records){
    if(!dataQueryExact(record,['observation_id','entity_id','task_id','name','attributes','review_state','source_time_verified','physical_state_verified'])
      ||!dataQueryUUID(record.observation_id)||record.entity_id!==request.entity_id||!dataQueryUUID(record.task_id)
      ||typeof record.name!=='string'||typeof record.review_state!=='string'
      ||!record.attributes||typeof record.attributes!=='object'||Array.isArray(record.attributes)
      ||record.source_time_verified!==false||record.physical_state_verified!==false)fail();
  }
  return value;
}
function dataQueryCandidateContent(value,reference) {
  if(value.integrity_error!==null)return '<p>原记录当前不可核实，未展示候选内容。请回原业务核对。</p>';
  if(value.source_changed)return '<p>原资料或人工核查状态已有变化，未展示旧候选内容。请回原业务重新核对。</p>';
  return `<h4>原候选查询 · ${esc(statusLabels[value.status]||value.status)}</h4><p class="muted">机器候选资料；采集时间与现场状态尚未核实，不能当作现场事实。</p>`
    +(value.records.length?value.records.map(record=>`<section class="evidence"><b>${esc(record.name)}</b><p>人工核查：${record.review_state==='confirmed'?'已核查':record.review_state==='draft_saved'?'已有草稿':'待核查'}</p>`
      +Object.entries(record.attributes).map(([key,item])=>`<div class="kv"><span>${esc(key)}</span><b>${esc(item!==null&&typeof item==='object'?'复合候选内容请到原业务核对':ordinaryNumericDisplay(record.attributes,key))}</b></div>`).join('')
      +dataQueryTaskLink(reference.context.instance_id,record.task_id)+'</section>').join(''):'<p>这份原查询尚无可展示的匹配候选；状态与原记录继续保留。</p>')
    +(value.truncated?'<p>仅显示原查询保存的部分记录，请到原业务继续核对。</p>':'');
}
function augmentDataQueryReferences() {
  if(current!=='data')return;
  for(const button of document.querySelectorAll('[data-query-reference-read]')){
    const card=button.closest('[data-query-reference]'),raw=queries.find(row=>row.id===card.dataset.queryReference);
    let row;try{row=checkedDataQueryReference(raw);}catch(error){continue;}
    const scope={user_id:me?.user_id,workspace_id:me?.workspace_id,role:me?.role},generation=loadGeneration;
    const visible=()=>generation===loadGeneration&&current==='data'&&card.isConnected
      &&me?.user_id===scope.user_id&&me?.workspace_id===scope.workspace_id&&me?.role===scope.role;
    const read=async path=>{
      if(!visible())throw Error('页面范围已变化，请在当前账号重新核对原记录。');
      const result=await fetch(path,{method:'GET',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf,'X-Workspace-ID':scope.workspace_id}});
      const value=ordinaryNumericParse(await result.text());
      if(!visible())throw Error('页面范围已变化，请在当前账号重新核对原记录。');
      if(!result.ok){if(result.status===401)signoutView();throw Error(typeof value.detail==='string'?value.detail:'原业务查询当前不可核实。');}
      return value;
    };
    const identity=async()=>{const user=await read('/api/me');if(user.user_id!==scope.user_id||user.workspace_id!==scope.workspace_id||user.role!==scope.role){if(visible())signoutView();throw Error('实际账号、工作区或权限已变化，请重新登录后核对。');}};
    button.onclick=async()=>{
      if(button.disabled||!visible())return;button.disabled=true;
      const result=card.querySelector('[data-query-reference-result]'),error=card.querySelector('[data-query-reference-error]');result.replaceChildren();error.textContent='正在只读核对原记录…';
      try{await identity();const value=await read(row.context.detail_path);let content;
        if(value?.schema==='native-candidate-query-view-v139')content=dataQueryCandidateContent(checkedDataQueryCandidate(value,row),row);
        else if(dataQueryReferenceLike(value)){
          const unverified=checkedDataQueryReference(value);
          if(unverified.id!==row.id||unverified.family!==row.family||unverified.version<row.version)throw Error('原业务查询响应不匹配，未采用其中内容。');
          content='<p>原候选查询当前未核实，未展示候选内容。</p><p>'+esc(unverified.notice)+'</p>';
        }else throw Error('原候选查询响应无法核实；未采用其中内容。');
        await identity();if(visible()){result.innerHTML=content;error.textContent='';}
      }catch(cause){if(visible())error.textContent=cause.message;}finally{if(visible())button.disabled=false;}
    };
  }
}

function fieldLabel(entity, key) {
  const cap = caps.find(c => c.id === entity?.schema_capability_id);
  return cap?.data.specification.entity_schemas[entity?.entity_type]?.properties?.[key]?.title || key;
}

function simpleReview(title, submit) {
  const dialog = document.createElement('dialog');
  dialog.className = 'card'; dialog.style.maxWidth = '520px';
  dialog.innerHTML = `<h2>${esc(title)}</h2><label for="reviewReason">核查依据与决定</label>
    <textarea id="reviewReason" required></textarea><p id="reviewError" role="alert"></p><div class="actions">
    <button id="confirmReview">确认并保存</button><button id="closeReview" class="secondary">取消</button></div>`;
  dialog.addEventListener('close', () => dialog.remove(), {once:true});
  document.body.append(dialog); dialog.showModal();
  dialog.querySelector('#closeReview').onclick = () => dialog.remove();
  dialog.querySelector('#confirmReview').onclick = e => action(e.target, async () => {
    try {
    const reason = dialog.querySelector('#reviewReason').value.trim();
    if (!reason) throw Error('请填写核查依据');
    await submit(reason); dialog.remove(); await load(); notice('决定和依据已保存。');
    } catch(error) { dialog.querySelector('#reviewError').textContent=error.message; }
  });
}

function augmentTasks() {
  for(const task of overview.tasks.filter(t=>t.data.perception_escalations?.length)){
    const card=document.querySelector('[data-task="'+task.id+'"]');if(!card)continue;
    const events=overview.events.filter(e=>task.data.perception_escalations.includes(e.id)),panel=document.createElement('section');panel.className='evidence';
    panel.innerHTML='<h3>感知补采协调</h3><p>升级时原因：'+esc(events.map(e=>e.data.reason).join('；')||task.data.coordination_reason||'已收到感知补采升级，请查看原计划')+'</p><p>保留原业务目标；本回执不授予额外设备动作，也不自动确认目标完成。</p>';
    const planId=task.data.observation_plan_id||events[0]?.data.plan_id;
    const currentPlan=sensingOverview.plans.find(p=>p.id===planId);
    if(currentPlan)panel.insertAdjacentHTML('beforeend','<p>当前补采：'+esc(sensingStatus(currentPlan.status))+' · '+esc(currentPlan.data.reason||'请核查补采证据')+'</p>');
    if(planId){const b=document.createElement('button');b.textContent='查看补采证据与恢复';b.className='secondary';b.onclick=()=>{current='perception';render();document.querySelector('[data-sensing-plan="'+planId+'"]')?.scrollIntoView({block:'center'});};panel.append(b);}card.append(panel);
  }

  for(const task of overview.tasks.filter(t=>t.data.data_gap_id)){
    const card=document.querySelector('[data-task="'+task.id+'"]');if(!card)continue;
    const gap=gapsOverview.items.find(g=>g.id===task.data.data_gap_id),panel=document.createElement('section');panel.className='evidence';
    panel.innerHTML='<h3>原查询的补证协调</h3><p>'+esc(task.data.original_question)+'</p><p>'+esc(gap?.status==='resolved'?gap.data.resolution_reason:gap?.data.decision_reason||task.data.coordination_reason||'等待补证复核')+'</p><p>'+esc(gapLabels[gap?.status]||'请查原查询')+'。证据满足不自动确认此业务调查完成。</p>';
    const b=document.createElement('button');b.textContent='查看原问题与补证证据';b.className='secondary';b.dataset.gapHistory=task.data.data_gap_id;
    b.onclick=()=>{current='data';render();document.querySelector('[data-query="'+task.data.parent_query_id+'"]')?.scrollIntoView({block:'start'});};panel.append(b);card.append(panel);
  }
  document.querySelectorAll('[data-plan]').forEach(button => {
    const task = overview.tasks.find(t => t.id === button.dataset.plan), card = button.parentElement;
    const plan = overview.plans.find(p => p.id === task.data.current_plan_id);
    if(!['draft','review','blocked_model','waiting_evidence','waiting_tool','planning_failed'].includes(task.status))button.remove();
    if(task.status==='planning'&&plan){
      const progress=document.createElement('section');progress.className='evidence';
      const stage=plan.data.progress?.stage||'queued',job=overview.jobs.find(j=>j.id===plan.data.planning_job_id);
      progress.innerHTML=`<h3>正在生成协作计划</h3><p role="status">${esc(({queued:'请求已保存，等待后台领取',reading_evidence:'正在读取已确认范围内的证据',waiting_model:'云模型正在规划，目标与范围已固定',model_result_saved:'模型响应已保存，正在核验与整理',validating_saved_result:'正在核验已保存模型响应，恢复时复用结果'})[stage]||'后台处理中')}</p><p>可离开或刷新后继续；计划整理完成后再批准。取消会停止后续工作，已经发送的模型请求仍可能产生费用。</p>${job?.data.lease_recoveries?`<p>后台已接管 ${job.data.lease_recoveries} 次。</p>`:''}`;
      card.append(progress);
    }
    if(task.data.execution_error){
      const error=document.createElement('div');error.className='evidence';
      error.innerHTML=`<h3>${task.status==='waiting_evidence'?'等待原需求证据与业务核查':'后台执行已停止'}</h3><p>${esc(task.data.execution_error)}</p><p>已保存的调用和证据仍可核查。修复问题后重新规划；已发送的未知动作须先核对回执。</p>`;
      card.append(error);
    }
    if(task.data.execution_failure_history?.length){
      const history=document.createElement('details');
      history.innerHTML='<summary>查看先前后台中断记录</summary>'+task.data.execution_failure_history.map(r=>`<p>${esc(r.reason)} · ${date(r.saved_at)}</p>`).join('');
      card.append(history);
    }
    if (plan?.status === 'review' && plan.data.result?.plan) {
      const panel = document.createElement('div');
      panel.innerHTML = `<h3 style="margin-top:18px">待批准的协作计划</h3>${plan.data.result.capability_scoped ? '<p class="muted">按当前可执行能力整理步骤；依据已确认目标和取证对象范围执行。原模型建议另行保留，文件完整性核查不能代替事实核查。</p>' : ''}<ol>
        ${plan.data.result.plan.steps.map(s => `<li>${esc(s.title)} <small>由${esc({data:'数据管理',perception:'多模感知',business:'业务驱动'}[s.agent])}处理</small>${s.action==='invoke_capability'?toolPlanEvidence(s):''}</li>`).join('')}
        </ol><button class="approve">批准执行此计划</button>`;
      card.append(panel);
      panel.querySelector('button').onclick = () => simpleReview('批准执行协作计划', reason =>
        api('/plans/' + plan.id + '/approve', 'POST', {expected_version:plan.version, reason}));
    }
    if (task.data.step_receipts) {
      const panel = document.createElement('div');
      panel.innerHTML = '<h3 style="margin-top:18px">执行证据</h3>' + Object.entries(task.data.step_receipts)
        .filter(([key]) => key.startsWith(task.data.current_plan_id + ':')).map(([,r]) =>
        `<details class="evidence" ${r.step.action==='query_evidence'?'open':''}><summary><b>${esc(r.step.title)}</b> ${badge(r.result.status)}</summary>
        <p class="muted">${date(r.finished_at)}</p>${r.result.reason ? `<p>${esc(r.result.reason)}</p>` : ''}
        ${r.result.method==='registered_capability'?toolOutputEvidence(r.result):''}${receiptEvidence(r.result)}</details>`).join('');
      card.append(panel);
    }
    if(task.data.tool_failure_history?.length){
      const history=document.createElement('details');history.innerHTML=`<summary>查看先前失败与恢复记录（${task.data.tool_failure_history.length}次）</summary>`+task.data.tool_failure_history.map(r=>`<div class="evidence"><b>${esc(r.step.title)}</b><p>${esc(r.result.reason||'工具调用失败')} · ${date(r.finished_at)}</p>${r.retry?`<p>重试依据：${esc(r.retry.reason)}</p>`:''}</div>`).join('');card.append(history);
    }
    if(task.status==='waiting_tool'){
      const blocked=Object.values(task.data.step_receipts||{}).find(r=>['failed','blocked_evidence','blocked_model','unknown','running','not_executed'].includes(r.result.status));
      const panel=document.createElement('div');panel.className='evidence';
      panel.innerHTML=`<h3>工具执行受阻</h3><p>${esc(blocked?.result.reason||'核对工具服务、契约和授权后继续。')}</p>`;
      if(blocked?.result.status==='not_executed'){
        panel.innerHTML+='<p>服务确认原请求已终止且未执行；可重新规划并由管理员批准。</p>';
      }else if(blocked&&!blocked.step.side_effect&&!['unknown','running'].includes(blocked.result.status)){
        const retry=document.createElement('button');retry.textContent='重试已批准的只读工具';
        retry.onclick=()=>simpleReview('修复服务后重试（保留失败记录）',reason=>api('/tasks/'+task.id+'/retry_execution','POST',{expected_version:task.version,reason}));panel.append(retry);
      }else if(blocked){
        panel.innerHTML+='<p>外部动作结果未知或可能有副作用，禁止重放；需要真实服务回执核对。</p>';button.remove();
        const cap=caps.find(c=>c.id===blocked.result.capability_id);
        if(me.role==='admin'&&cap?.data.execution.receipt_endpoint&&blocked.result.invocation_id){
          const query=document.createElement('button');query.textContent='核对服务回执（不会重发动作）';
          query.onclick=()=>simpleReview('只读核对外部动作回执',async reason=>{
            const row=await api('/invocations/'+blocked.result.invocation_id);
            return api('/invocations/'+row.id+'/reconcile','POST',{expected_version:row.version,reason});});panel.append(query);
        }
      }else panel.innerHTML+='<p>动作回执已保存，但旧计划的依据需要重新核查。</p>';
      card.append(panel);
    }
    if (task.status === 'awaiting_acceptance') {
      const button = document.createElement('button'); button.textContent = '验收结果并留下反馈';
      button.style.marginTop = '14px'; card.append(button);
      button.onclick = () => simpleReview('验收业务结果', reason => api('/tasks/' + task.id + '/accept', 'POST',
        {expected_version:task.version, reason}));
    }
    if (!['completed','cancelled'].includes(task.status)) {
      const cancel = document.createElement('button'); cancel.className = 'secondary';
      cancel.textContent = '取消任务'; cancel.style.marginLeft = '8px'; card.append(cancel);
      cancel.onclick = () => simpleReview('取消业务任务', reason => api('/tasks/' + task.id + '/cancel', 'POST',
        {expected_version:task.version, reason}));
    }
    button.disabled = ['queued','running','planning','waiting_data','awaiting_acceptance','completed','cancelled'].includes(task.status);
  });
}

function structuredToolValue(value,schema={},depth=0,owner=null,key=null){
  if(value===null||value===undefined)return '<span>暂无足够数据</span>';
  if(depth>4)return '<span>嵌套结果请查看导出记录</span>';
  if(Array.isArray(value))return value.slice(0,30).map((v,index)=>`<div class="evidence">${structuredToolValue(v,schema.items||{},depth+1,value,index)}</div>`).join('')+(value.length>30?`<p>共${value.length}项，完整结果已保存。</p>`:'');
  if(typeof value==='object')return Object.entries(value).filter(([k])=>!['method','provenance','entity_id','invocation_id','capability_id','receipt_id'].includes(k)).map(([k,v])=>`<div class="kv"><span>${esc(schema.properties?.[k]?.title||({field:'字段',operation:'操作',value:'结果',samples:'样本数量',sample_count:'有效样本',missing_count:'缺失数量',status:'状态'}[k])||k)}</span><div>${structuredToolValue(v,schema.properties?.[k]||{},depth+1,value,k)}</div></div>`).join('');
  if(typeof value==='number')return `<b>${esc(owner?ordinaryNumericDisplay(owner,key):'数值原文未核实，请重新填写')}</b>`;
  return `<b>${esc(({mean:'平均值',sum:'总和',min:'最小值',max:'最大值',count:'计数'}[value])||value)}</b>`;
}

function toolPlanEvidence(step){
  const cap=caps.find(c=>c.id===step.capability_id);
  const bindings=cap?.data.execution.input_bindings||{},records=Object.entries(bindings).filter(([,source])=>source==='archive_records').flatMap(([key])=>step.input_snapshot[key]||[]);
  const argumentsView=ordinaryNumericClone(step.arguments||{}),entity=entities.find(e=>e.id===records[0]?.entity_id);
  if(argumentsView.field&&entity)argumentsView.field=fieldLabel(entity,argumentsView.field);
  return `<div class="evidence"><p>登记版本 ${esc(step.release_version)} · ${step.side_effect?'有外部动作，批准后才会发送':'只读分析'}</p><h4>调用参数</h4>${structuredToolValue(argumentsView,cap?.data.input_schema)}<details><summary>查看服务器绑定的输入依据</summary>${receiptEvidence({evidence:records})}</details></div>`;
}

function toolOutputEvidence(result){
  const cap=caps.find(c=>c.id===result.capability_id);
  return `<p>登记版本 ${esc(result.release_version||'')} · ${esc(result.quality_limit||'尚待核查')}</p>${result.receipt_check?`<p>最近回执核对：${esc(({succeeded:'已完成',pending:'仍在处理',not_executed:'确认未执行',unverified:'未能确认'}[result.receipt_check.status])||'待核查')} · ${date(result.receipt_check.checked_at)}</p>`:''}${result.output?structuredToolValue(result.output,cap?.data.output_schema):''}`;
}

function humanTaskText(text,task){
  let result=text||'';
  for(const id of task.data.entity_ids||[]){const entity=entities.find(e=>e.id===id);if(entity)result=result.split(id).join(entity.data.name||entity.external_id);}
  return result.replace(/entity_id:\s*/g,'');
}

function receiptEvidence(result) {
  const evidence = Array.isArray(result.evidence) ? result.evidence : result.evidence?.evidence || [];
  return evidence.map(item => {
    const entity = entities.find(e => e.id === item.entity_id);
    return `<div class="evidence"><b>${esc(entity?.data.name || entity?.external_id || '对象')}</b> · 版本${item.version}
      ${Object.entries(item.attributes).map(([key,value]) => `<div class="kv"><span>${esc(fieldLabel(entity,key))}</span>
      <b>${esc(ordinaryNumericDisplay(item.attributes,key))}</b></div>`).join('')}
      <small>来源：资源档案 · 观测时间：${date(item.observed_at)}${item.observed_at ? '' : '（不能作为实时状态依据）'}</small></div>`;
  }).join('') + observationEvidence(result.observation_evidence || result.evidence?.observation_evidence || [])
    + (result.file_integrity?.length ? `<p>${result.file_integrity.length}份原始文件：${result.file_integrity.every(item => item.status === 'verified') ? '完整性校验通过' : '存在文件缺失或校验失败，不能验收'}</p>` : (result.file_integrity?'<p>此数值观测未关联原始文件。</p>':''));
}

function observationEvidence(items) {
  return items.map(item => {
    const entity = entities.find(e => e.id === item.entity_id), source = caps.find(c => c.id === item.source_id);
    const timezone=source?.data.specification.timezone,stamp=timezone?new Date(item.observed_at).toLocaleString('zh-CN',{timeZone:timezone,hour12:false})+' · '+timezone:date(item.observed_at)+' · 浏览器本地时间';
    return `<div class="evidence"><b>观测提取 · ${item.verification==='confirmed'?'已人工确认':item.verification==='rejected'?'已撤回':'待核查'}</b><p>${esc(entity?.data.name || entity?.external_id || '对象')}</p>
      ${Object.entries(item.attributes || {}).map(([key,value]) => `<div class="kv"><span>${esc(fieldLabel(entity,key))}</span><b>${esc(ordinaryNumericDisplay(item.attributes,key))}</b></div>`).join('')}
      <small>${esc(source?.data.description || '已注册感知源')} · ${esc(stamp)}</small>
      ${(item.evidence_ids || []).map(id => `<a style="display:block" href="/api/evidence/${esc(id)}/content">查看原始证据</a>`).join('')}
      <p class="muted">${item.verification==='confirmed'?'提取值已人工核查；':item.verification==='rejected'?'此提取已撤回，不作为可用证据；':'模型或处理器提取值尚待核查；'}资源档案保留原记录。</p></div>`;
  }).join('');
}

function reusableCases(items) {
  if (!items?.length) return '';
  return '<details><summary>查询当时匹配的已核实经验（'+items.length+'条）</summary>' + items.map(item =>
    `<div class="evidence"><b>${esc(item.provenance?.goal || '已核实案例')}</b><p>${esc(item.text)}</p>
    <small>按本工作区、业务包和有效性过滤；不能用于超出案例范围的判断。</small></div>`).join('') + '</details>';
}

function augmentData() {
  const destination = $('#content .grid > div:last-child'), section = document.createElement('section');
  const memories = overview.memories.filter(m => m.data.type === 'verified_case');
  section.innerHTML = '<h2>经过验收的经验</h2>' + (memories.length ? memories.map(m =>
    `<div class="card"><h3>${esc(m.data.goal)}</h3>${badge(m.status)}${m.relation_reuse_blocked?'<p>关系依据已变化：此记录只保留历史，不用于可信复用或进化。请重新核查任务。</p>':''}<p>${esc(m.text)}</p>
      <small>依据：已验收业务任务及其证据记录</small><details><summary>查看核查证据</summary>
      ${receiptEvidence(m.data.evidence)}${relationEvidencePanel(m.data.evidence)}</details>${m.status === 'candidate' ?
      `<div class="actions"><button data-trust="${m.id}">纳入可信经验</button>
      <button data-reject="${m.id}" class="secondary">拒绝</button></div>` : ''}</div>`).join('') :
    empty('业务任务验收后，案例会进入审核。只有核实的经验才影响后续任务。'));
  destination.append(section);
  section.querySelectorAll('[data-trust],[data-reject]').forEach(button => button.onclick = () => {
    const memory = memories.find(m => m.id === (button.dataset.trust || button.dataset.reject));
    simpleReview('审核经验', reason => api('/memories/' + memory.id + '/review', 'POST',
      {expected_version:memory.version, reason, decision:button.dataset.trust ? 'trust' : 'reject'}));
  });
  const governance = document.createElement('section');
  governance.innerHTML = '<h2>数据治理方案</h2>' + overview.governance.map(g => {
    const entity = entities.find(e => e.id === g.data.entity_id);
    return `<div class="card"><h3>${esc(entity?.external_id || '对象纠错')}</h3>${badge(g.status)}<p>${esc(g.data.reason)}</p>
      ${Object.keys(g.data.after).map(k => `<div class="kv"><span>${esc(fieldLabel(entity,k))}</span>
      <b>${esc(g.data.before[k])} → ${esc(g.data.after[k])}</b></div>`).join('')}
      ${g.status === 'review' && me.role === 'admin' ? `<button style="margin-top:15px" data-governance="${g.id}">批准治理</button>` : ''}</div>`;
  }).join(''); destination.append(governance);
  governance.querySelectorAll('[data-governance]').forEach(button => button.onclick = () => {
    const g = overview.governance.find(g => g.id === button.dataset.governance);
    simpleReview('核对前后值并批准治理', reason => api('/governance/' + g.id + '/approve', 'POST',
      {expected_version:g.version, reason}));
  });
  for (const issue of overview.issues.filter(i => i.kind === 'invalid_schema' && i.status === 'open')) {
    const entity = entities.find(e => e.id === issue.entity_id), panel = document.createElement('div');
    panel.className = 'card'; panel.innerHTML = `<h3>纠正 ${esc(entity?.external_id)} 的无效字段</h3>
      <div id="repairFields_${issue.id}"></div><button style="margin-top:15px">提出纠错方案</button>`;
    destination.append(panel); const pkg = caps.find(c => c.id === entity.schema_capability_id);
    schemaInputs(pkg.data.specification.entity_schemas[entity.entity_type], '#repairFields_' + issue.id, 'repair_' + issue.id + '_');
    panel.querySelectorAll('[data-field]').forEach(input => input.value = entity.data[input.dataset.field] ?? '');
    panel.querySelector('button').onclick = () => simpleReview('提出数据纠错方案', reason =>
      api('/issues/' + issue.id + '/repair', 'POST', {expected_entity_version:entity.version, reason,
        attributes:attributes('#repairFields_' + issue.id)}));
  }
}

function augmentPerception() {
  attachFileProcessing();
  augmentSensing();
  const section = document.createElement('section');
  section.innerHTML = '<h2>主动补采与监测授权</h2>' + (overview.acquisitions.length ? overview.acquisitions.map(a =>
    `<div class="card"><h3>${esc(entities.find(e => e.id === a.entity_id)?.data.name || entities.find(e => e.id === a.entity_id)?.external_id || '对象')} · 补采</h3>
    ${badge(a.status)}<p>${esc(a.data.reason)}</p>${(a.status === 'unknown'||a.data.observation_plan_id&&a.status==='awaiting_observation') ?
    `<button data-reconcile="${a.id}">核对外部回执</button>` : ''}</div>`).join('') :
    empty('补采必须处于已批准监测范围。设备接口未接入时显示阻塞。'));
  $('#content .grid > div:last-child').append(section);
  for(const policy of overview.monitor_policies){
    const panel=document.createElement('div');panel.className='card';
    panel.innerHTML=`<h3>监测策略 · ${esc(policy.data.title)}</h3>${badge(policy.status)}<p>对象：${esc(policy.data.entity_ids.map(id=>entities.find(e=>e.id===id)?.data.name||entities.find(e=>e.id===id)?.external_id||'登记对象').join('、'))}</p><p>字段：${esc(policy.data.fields.map(f=>fieldLabel(entities.find(e=>e.id===policy.data.entity_ids[0]),f)).join('、'))} · 最长年龄${policy.data.max_age_seconds}秒 · 每日最多${policy.data.max_acquisitions_per_day}次 · 单次最长${policy.data.max_duration_seconds}秒 · 冷却${policy.data.cooldown_seconds}秒</p><p>来源：${esc(policy.data.source_ids.map(id=>caps.find(c=>c.id===id)?.data.description||'登记来源').join('、'))} · 到期${date(policy.data.valid_until)}</p>`;
    if(policy.data.selection_requirement){const r=policy.data.selection_requirement;panel.insertAdjacentHTML('beforeend',`<p>资产择优 · 最多${r.max_attempts}次 · 累计预留${r.max_total_cost_minor/100} ${esc(r.currency)} · 到数${r.observation_wait_seconds}秒 · 计划${r.plan_timeout_seconds}秒 · ${r.minimum_verification==='confirmed'?'人工确认后结案':'允许未核实提取，仅代表归集完成'}</p><p>白名单：${esc(policy.data.sensor_asset_ids.map(id=>sensingOverview.assets.find(a=>a.id===id)?.title||'其他页登记资产').join('、'))}</p><p>模态：${esc(r.allowed_modalities.map(x=>sensorModalityNames[x]).join('、'))} · 动作：${esc(r.allowed_actions.map(x=>sensorActionNames[x]).join('、'))}</p><p>分辨率数值上限${r.max_resolution_m??'不限制'}米 · 交付估计${r.max_latency_seconds}秒 · 日预算时区${esc(policy.data.timezone)}</p>`);}else panel.insertAdjacentHTML('beforeend','<p>指定单一来源的旧策略，未声明多来源择优或覆盖精度。</p>');
    if(policy.data.device_scope){const ds=Object.values(policy.data.device_scope);panel.insertAdjacentHTML('beforeend','<p>批准设备范围：'+esc(ds.map(s=>{const d=deviceOverview.devices.find(d=>d.id===s.device_id);return d?d.title+'（'+deviceIdentityText(d)+'）':'其他页统一设备，请先查看接口归属';}).join('、'))+'</p>');}
    if(policy.status==='active'&&me.role!=='viewer'){const b=document.createElement('button');b.textContent='检查缺口并保存选择';b.onclick=e=>action(e.target,async()=>{await api('/monitor-policies/'+policy.id+'/scan','POST',{});await load();notice('缺口检查与选择依据已保存。')});panel.append(b);}
    if(me.role==='admin'&&policy.status==='review'){const b=document.createElement('button');b.textContent='核查并批准已保存策略';b.onclick=()=>simpleReview('核查已保存的对象、字段、来源、次数与有效期',reason=>api('/monitor-policies/'+policy.id+'/approve','POST',{expected_version:policy.version,reason}));panel.append(b);}
    if(me.role==='admin'&&policy.status==='active'){const b=document.createElement('button');b.textContent='撤销此监测授权';b.className='secondary';b.onclick=()=>simpleReview('停止新的补采；已派发或未知动作仍需核对',reason=>api('/monitor-policies/'+policy.id+'/revoke','POST',{expected_version:policy.version,reason}));panel.append(b);}
    section.append(panel);
  }
  section.querySelectorAll('[data-reconcile]').forEach(button => button.onclick = () => action(button, async () => {
    await api('/acquisitions/' + button.dataset.reconcile + '/reconcile', 'POST', {}); await load(); notice('外部回执已核对。');
  }));
}

function observationJournalScope(user) {
  if (!user || typeof user.user_id !== 'string' || !user.user_id || typeof user.workspace_id !== 'string' || !user.workspace_id)
    throw Error('当前账号或工作区尚未核对，原输入保留，未发送。');
  return {user_id:user.user_id,workspace_id:user.workspace_id};
}
function observationJournalSame(a,b) {
  if (a === b) return true;
  if (!a || !b || typeof a !== 'object' || typeof b !== 'object' || Array.isArray(a) !== Array.isArray(b)) return false;
  const ak=Object.keys(a).sort(),bk=Object.keys(b).sort();
  return ak.length===bk.length && ak.every((key,i)=>key===bk[i] && observationJournalSame(a[key],b[key]));
}
function observationJournalJSON(value) {
  function check(item) {
    if (typeof item==='number' && !Number.isFinite(item)) throw Error('输入含无效数值，原输入保留，未发送。');
    if (typeof item==='string') {
      if (item.includes('\0')) throw Error('输入含无法保存的字符，原输入保留，未发送。');
      for (let i=0;i<item.length;i++) {
        const code=item.charCodeAt(i);
        if (code>=0xD800 && code<=0xDBFF) { const next=item.charCodeAt(++i);if (!(next>=0xDC00 && next<=0xDFFF)) throw Error('输入字符不完整，原输入保留，未发送。'); }
        else if (code>=0xDC00 && code<=0xDFFF) throw Error('输入字符不完整，原输入保留，未发送。');
      }
    } else if (item && typeof item==='object') {
      for (const key of Object.keys(item)) { check(key);const descriptor=Object.getOwnPropertyDescriptor(item,key);if (!descriptor || !Object.hasOwn(descriptor,'value')) throw Error('恢复记录不能含动态内容。');check(descriptor.value); }
    } else if (item!==null && !['number','boolean'].includes(typeof item)) throw Error('恢复记录含不完整内容，未发送。');
  }
  check(value);return JSON.stringify(value);
}
function observationJournalKeys(value,names) {
  return !!value && typeof value==='object' && !Array.isArray(value) && observationJournalSame(Object.keys(value).sort(),names.split(',').sort());
}
function observationJournalBlank() {
  return {source_id:'',source_record_id:'',entity_id:'',extractor_id:'',observed_local:'',startup_mode:'manual',evidence_id:'',fields:[]};
}
function observationJournalDraft(value) {
  if (!observationJournalKeys(value,'source_id,source_record_id,entity_id,extractor_id,observed_local,startup_mode,evidence_id,fields') ||
      !['source_id','source_record_id','entity_id','extractor_id','observed_local','startup_mode','evidence_id'].every(key=>typeof value[key]==='string') ||
      !['manual','automatic','historical'].includes(value.startup_mode) || !Array.isArray(value.fields) ||
      !value.fields.every(field=>observationJournalKeys(field,'key,type,value') && ['key','type','value'].every(key=>typeof field[key]==='string')) ||
      new Set(value.fields.map(field=>field.key)).size!==value.fields.length) throw Error('原观测草稿结构无法核对，尚未覆盖或发送。');
}
function observationJournalBody(wire) {
  if (typeof wire!=='string') throw Error('原观测正文尚未完整保存。');
  const body=JSON.parse(wire);
  if (!observationJournalKeys(body,'source_id,source_record_id,processing_version,entity_id,observed_at,business_day,attributes,evidence_ids,extractor_id,startup_mode') ||
      !['source_id','source_record_id','entity_id','observed_at','business_day','extractor_id'].every(key=>typeof body[key]==='string' && body[key]) ||
      body.processing_version!=='1.0.0' || !['manual','automatic','historical'].includes(body.startup_mode) ||
      !body.attributes || typeof body.attributes!=='object' || Array.isArray(body.attributes) || !Array.isArray(body.evidence_ids) ||
      !body.evidence_ids.every(id=>typeof id==='string' && id) || new Set(body.evidence_ids).size!==body.evidence_ids.length ||
      !Number.isFinite(Date.parse(body.observed_at)) || !/^\d{4}-\d{2}-\d{2}$/.test(body.business_day)) throw Error('原观测正文无法完整核对，尚未重发。');
  observationJournalJSON(body);return body;
}
function observationJournalRequest(value) {
  if (!observationJournalKeys(value,'wire,status,saved_id,error') || !['unknown','rejected','saved'].includes(value.status) ||
      !(value.error===null || typeof value.error==='string') ||
      (value.status==='saved' ? !(typeof value.saved_id==='string' && value.saved_id) : value.saved_id!==null)) throw Error('原观测请求状态无法核对，原记录保留。');
  observationJournalBody(value.wire);
}
function observationJournalChecked(value,scope) {
  if (!observationJournalKeys(value,'schema,scope,draft,request,history') || value.schema!=='operator-observation-journal-v128' ||
      !observationJournalKeys(value.scope,'user_id,workspace_id') || !observationJournalSame(value.scope,scope) || !Array.isArray(value.history)) throw Error('原观测恢复记录不属于当前账号或工作区，尚未覆盖。');
  observationJournalDraft(value.draft);if (value.request!==null) observationJournalRequest(value.request);
  for (const entry of value.history) {
    if (!observationJournalKeys(entry,'draft,request')) throw Error('保留的原观测记录无法核对。');
    observationJournalDraft(entry.draft);if (entry.request!==null) observationJournalRequest(entry.request);
  }
  observationJournalJSON(value);return value;
}
function observationJournalRead(storage,scope) {
  const key='ga-operator-observation-v128:'+encodeURIComponent(scope.workspace_id)+':'+encodeURIComponent(scope.user_id),raw=storage.getItem(key);
  const value=raw===null ? {schema:'operator-observation-journal-v128',scope:{...scope},draft:observationJournalBlank(),request:null,history:[]} : JSON.parse(raw);
  return {key,raw,value:observationJournalChecked(value,scope)};
}
function observationJournalWrite(storage,original,next) {
  observationJournalChecked(next,original.value.scope);
  if (storage.getItem(original.key)!==original.raw) throw Error('另一页面已更新原观测记录，本页输入未覆盖；请保留本页并重新核对。');
  const raw=observationJournalJSON(next);storage.setItem(original.key,raw);
  if (storage.getItem(original.key)!==raw) throw Error('本机观测记录未完整读回，未发送请求；请保留本页输入。');
  return {...original,raw,value:JSON.parse(raw)};
}
function observationJournalStage(storage,original,body,draft) {
  if (original.value.request) throw Error('请先处理已保留的原请求，不能另造正文覆盖它。');
  const request={wire:observationJournalJSON(body),status:'unknown',saved_id:null,error:null};
  return observationJournalWrite(storage,original,{...original.value,draft,request});
}
function observationJournalAttributes(fields) {
  observationJournalDraft({...observationJournalBlank(),fields});
  return Object.fromEntries(fields.filter(field=>field.value!=='').map(field=>{
    const raw=field.value;
    if(field.type==='string')return [field.key,raw];
    if(field.type==='boolean') {
      if(raw!=='true' && raw!=='false')throw Error('原稿字段“'+field.key+'”须明确填写 true 或 false；原输入保留，未发送。');
      return [field.key,raw==='true'];
    }
    if(!['number','integer'].includes(field.type))throw Error('原稿字段“'+field.key+'”的类型当前不支持提交；原输入保留。');
    if(!/^-?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$/.test(raw))throw Error('原稿字段“'+field.key+'”须填写有效十进制数值；原输入保留，未发送。');
    const value=Number(raw);
    if(!Number.isFinite(value) || field.type==='integer'&&!Number.isSafeInteger(value))throw Error('原稿字段“'+field.key+'”须填写'+(field.type==='integer'?'安全范围内的整数':'有限数值')+'；原输入保留，未发送。');
    return [field.key,value];
  }));
}
function observationJournalReply(row,wire,scope) {
  const body=observationJournalBody(wire),expected={...body,acquisition_id:null,sensor_asset_id:null,sensor_modality:null};
  if (!body.evidence_ids.length) delete expected.evidence_ids;
  if (!row || typeof row.id!=='string' || !row.id || row.workspace_id!==scope.workspace_id ||
      !['source_id','source_record_id','processing_version','entity_id','business_day'].every(key=>row[key]===body[key]) ||
      Date.parse(row.observed_at)!==Date.parse(body.observed_at) || row.data?.submitted_by!==scope.user_id ||
      row.data?.extractor_id!==body.extractor_id || !/^[0-9a-f]{64}$/.test(row.data?.input_hash||'') ||
      !observationJournalSame(row.data?.original_request,expected)) throw Error('服务器记录与原账号、编号或完整正文不一致；原请求仍待核对。');
  return row.id;
}
function observationJournalOutcome(storage,original,wire,{row=null,error=null}={}) {
  const request=original.value.request;
  if (!request || request.wire!==wire || request.status==='saved') throw Error('原观测请求已变化，不能采用迟到结果。');
  const next=row ? {...request,status:'saved',saved_id:observationJournalReply(row,wire,original.value.scope),error:null} :
    {...request,status:error?.status>=400 && error.status<500?'rejected':'unknown',saved_id:null,error:String(error?.message||'回复未知，请手动核对原请求。').slice(0,1000)};
  return observationJournalWrite(storage,original,{...original.value,request:next});
}
function observationJournalRetain(storage,original,index=null) {
  const previous={draft:original.value.draft,request:original.value.request},history=[...original.value.history];
  let draft=observationJournalBlank(),request=null;
  if (index!==null) {
    if (!Number.isSafeInteger(index) || index<0 || index>=history.length) throw Error('所选原请求已变化，请重新核对。');
    ({draft,request}=history[index]);history.splice(index,1);
  }
  history.push(previous);
  return observationJournalWrite(storage,original,{...original.value,draft,request,history});
}

function attachFileProcessing() {
  const container = $('#content .grid > div:first-child'), panel = document.createElement('div');
  panel.className = 'card';
  const models = caps.filter(c => c.kind === 'foundation_model' && c.status === 'published' && c.data.execution.task !== 'embedding');
  panel.innerHTML = `<h2>保存文件证据</h2><p class="muted">接收PNG、JPEG与UTF-8文本。原始文件保存后可重读；云端处理需要单独授权。</p>
    <form id="fileEvidenceForm"><label for="evidenceFile">观测文件</label><input id="evidenceFile" type="file" accept=".png,.jpg,.jpeg,.txt" required>
    <label for="evidenceModel">允许处理此文件的模型</label><select id="evidenceModel">
    ${selectOptions(models,m => m.data.description+' · '+m.data.execution.model)}</select>
    <div class="choices"><label><input id="fileCloudConsent" type="checkbox">文件已脱敏或不含敏感信息，允许所选模型处理</label></div>
    <div class="actions"><button>上传并保存文件</button></div></form><small>不勾选时只在本平台保存，不发送云端。文件上传若未收到回复，本页尚不能查询原上传回执；不会自动重传。下方只恢复已保存文件引用和观测提交。</small>`;
  container.prepend(panel);
  const observationForm = $('#observationForm'), field = document.createElement('div');
  observationForm.onsubmit=event=>{event.preventDefault();notice('原观测恢复记录尚未就绪，未发送。',true);};
  field.innerHTML = '<label for="savedEvidence">关联已保存文件（可选）</label><select id="savedEvidence"><option value="">不关联文件，处理数值属性</option></select><p class="muted" id="evidenceHint"></p>';
  observationForm.insertBefore(field, observationForm.querySelector('.actions'));
  let scope,storage;
  try{scope=observationJournalScope(me);storage=localStorage;}catch(error){
    observationForm.querySelectorAll('button').forEach(button=>{button.disabled=true;});panel.querySelector('button').disabled=true;
    notice(error.message+' 无法安全保存原请求，本页未开放发送。',true);return;
  }
  const locks=globalThis.navigator?.locks;
  const recovery=document.createElement('section');recovery.className='evidence';recovery.setAttribute('aria-label','原观测提交与恢复');
  recovery.innerHTML='<p data-observation-state role="status"></p><p data-observation-error role="alert"></p><div class="actions"><button type="button" data-observation-retry>按原请求重试</button><button type="button" class="secondary" data-observation-refresh data-observation-journal-read>刷新已保存结果</button><button type="button" class="secondary" data-observation-new>保留原记录，另建草稿</button><button type="button" class="secondary" data-observation-reread>保留本页输入并读取本机记录</button><button type="button" class="secondary" data-observation-export data-observation-journal-read>导出本机保留稿</button></div><details><summary>本机保留的原登记与草稿</summary><div data-observation-history></div></details>';
  observationForm.append(recovery);
  const control=name=>recovery.querySelector('[data-observation-'+name+']');
  const formControl=id=>observationForm.querySelector('#'+id);
  const submit=observationForm.querySelector('.actions button');
  const uploadSubmit=panel.querySelector('button');
  const timeLabel=observationForm.querySelector('label[for="observedAt"]');if(timeLabel)timeLabel.textContent='观测时间（北京时间）';
  let files=[],journal=null,busy=false,restoring=false,pendingEdits=0,cacheError='',refreshError='',blockedDraft=null,draftTail=Promise.resolve();
  const sameScope=()=>me?.user_id===scope.user_id && me?.workspace_id===scope.workspace_id;
  const visible=()=>sameScope() && current==='perception' && observationForm.isConnected && $('#observationForm')===observationForm;
  const scopeCheck=()=>{if(!sameScope())throw Error('账号或工作区已变化，原请求保留在原范围内，未采用迟到结果。');};
  const lock=fn=>{
    if(!locks || typeof locks.request!=='function')return Promise.reject(Error('浏览器不支持安全串行保存；原记录可读，本页不能提交或重发。'));
    return locks.request(journal?.key||('ga-operator-observation-v128:'+scope.workspace_id+':'+scope.user_id),{mode:'exclusive'},fn);
  };
  function snapshot() {
    return {source_id:formControl('source').value,source_record_id:formControl('record').value,
      entity_id:formControl('observedEntity').value,extractor_id:formControl('extractor').value,
      observed_local:formControl('observedAt').value,startup_mode:formControl('startMode').value,
      evidence_id:formControl('savedEvidence').value,fields:[...formControl('observationFields').querySelectorAll('[data-field]')]
        .map(input=>({key:input.dataset.field,type:input.dataset.type||'string',value:input.value}))};
  }
  function choice(input,value,label) {
    if(value && ![...input.options].some(option=>option.value===value)) {
      const option=document.createElement('option');option.value=value;option.textContent=label;input.append(option);
    }
    input.value=value;
  }
  function restore(draft) {
    restoring=true;
    try {
      choice(formControl('source'),draft.source_id,'原来源（当前列表未提供）');
      choice(formControl('observedEntity'),draft.entity_id,'原对象（当前列表未提供）');
      formControl('observedEntity').onchange?.();
      choice(formControl('extractor'),draft.extractor_id,'原提取器（当前列表未提供）');
      formControl('record').value=draft.source_record_id;formControl('observedAt').value=draft.observed_local;
      formControl('startMode').value=draft.startup_mode;
      const fields=formControl('observationFields');
      for(const saved of draft.fields) {
        const current=[...fields.querySelectorAll('[data-field]')].filter(row=>row.dataset.field===saved.key);
        const input=current.find(row=>row.dataset.type===saved.type);
        if(input){input.value=saved.value;if(input.value===saved.value)continue;}
        for(const old of current){fields.querySelectorAll('label').forEach(label=>{if(label.htmlFor===old.id)label.remove();});old.remove();}
        const label=document.createElement('label'),raw=document.createElement('textarea'),hint=document.createElement('small');
        label.textContent='原稿字段：'+saved.key;raw.dataset.field=saved.key;raw.dataset.type=saved.type;raw.value=saved.value;
        hint.textContent='当前对象或字段规范未提供这一原稿控件；原输入完整保留，提交仍须服务器核验。';
        label.append(raw);fields.append(label,hint);
      }
      choice(formControl('savedEvidence'),draft.evidence_id,'原文件引用（未在最近清单）');
    } finally {restoring=false;}
  }
  function draw() {
    if(!visible())return;
    const request=journal?.value.request,frozen=!!request,unsupported=!locks || typeof locks.request!=='function';
    formControl('observationFields').querySelectorAll('input,select,textarea').forEach(input=>{input.disabled=frozen||busy||!!formControl('savedEvidence').value;});
    for(const id of ['source','record','observedEntity','extractor','observedAt','startMode','savedEvidence'])formControl(id).disabled=frozen||busy;
    submit.disabled=busy||pendingEdits>0||!!cacheError||unsupported||frozen||me.role==='viewer';
    uploadSubmit.disabled=busy||pendingEdits>0||frozen||!!cacheError||unsupported||me.role==='viewer';
    control('retry').disabled=busy||pendingEdits>0||!!cacheError||unsupported||!request||request.status==='saved'||me.role==='viewer';
    control('new').disabled=busy||pendingEdits>0||!!cacheError||unsupported||!journal;
    control('reread').disabled=busy||pendingEdits>0||unsupported;
    control('refresh').disabled=busy||pendingEdits>0||!!cacheError;
    control('export').disabled=!journal;
    control('state').textContent=request?.status==='saved'?'原登记的完整正文已取得匹配服务器记录；不会再次提交。可单独刷新查看处理与送达结果。':
      request?'原编号、时间、已保存文件引用与完整正文已冻结。保存状态尚未确认；只可手动重发原请求，当前发布或权限失效时仍会明确受阻。':
      '输入会按当前账号和工作区保存在本机。提交前完整正文须保存并读回；离开本页不会自动发送。';
    control('error').textContent=cacheError||refreshError||(unsupported?'浏览器缺少安全串行保存能力；原记录可读，暂不能发送。':request?.error||'');
    const history=control('history');history.replaceChildren();
    (journal?.value.history||[]).forEach((entry,index)=>{
      const row=document.createElement('p'),label=document.createElement('span'),button=document.createElement('button');
      const body=entry.request?observationJournalBody(entry.request.wire):null;
      label.textContent=(body?.source_record_id||entry.draft.source_record_id||'尚未提交的草稿')+' · '+
        (entry.request?.status==='saved'?'已取得保存记录':entry.request?'原请求待核对':'输入已保留');
      button.type='button';button.className='secondary';button.textContent='保留当前稿并恢复这一条';
      button.disabled=busy||pendingEdits>0||!!cacheError||unsupported;
      button.onclick=()=>localAction(()=>{journal=observationJournalRetain(storage,journal,index);restore(journal.value.draft);});
      row.append(label,' ',button);history.append(row);
    });
  }
  function failure(error) {if(visible()){cacheError=error.message;control('error').textContent=error.message;draw();}}
  function saveDraft() {
    if(restoring||busy||!visible()||journal?.value.request)return;
    const draft=snapshot();blockedDraft=draft;pendingEdits++;draw();
    draftTail=draftTail.then(()=>lock(()=>{scopeCheck();if(!visible())throw Error('原输入仍待保存，请保留原页面。');
      if(!journal)throw Error('原本机记录尚未恢复，未覆盖。');
      const previous=journal.value.draft,inventory=value=>value.fields.map(field=>({key:field.key,type:field.type}));
      const changed=previous.fields.length>0 && (previous.entity_id!==draft.entity_id || !observationJournalSame(inventory(previous),inventory(draft)));
      const history=changed?[...journal.value.history,{draft:previous,request:null}]:journal.value.history;
      journal=observationJournalWrite(storage,journal,{...journal.value,draft,history});blockedDraft=null;cacheError='';
    })).catch(failure).finally(()=>{pendingEdits--;draw();});
  }
  async function localAction(fn) {
    if(busy)return;busy=true;draw();await draftTail;if(cacheError){busy=false;draw();return;}
    try{await lock(()=>{scopeCheck();if(!visible())throw Error('原观测页面已变化。');fn();});}
    catch(error){failure(error);}finally{busy=false;draw();}
  }
  async function request(path,method='GET',wire) {
    const requestGeneration=loadGeneration;
    const response=await fetch('/api'+path,{method,credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf,'X-Workspace-ID':scope.workspace_id},...(wire===undefined?{}:{body:wire})});
    let value;try{value=await response.json();}catch{throw Error('服务器回复尚不能核对；原观测请求保留。');}
    if(!response.ok) {
      if(response.status===401 && sameScope() && requestGeneration===loadGeneration)signoutView();
      const error=Error(typeof value?.detail==='string'?value.detail:'操作未完成，请核对原输入与当前权限。');error.status=response.status;throw error;
    }
    return value;
  }
  async function checkActor(write=false) {
    const user=await request('/me');scopeCheck();
    if(!observationJournalSame(observationJournalScope(user),scope)) {
      if(sameScope())signoutView();
      throw Error('实际登录账号或工作区已变化，原请求保留，未采用迟到结果。');
    }
    if(write&&!['admin','operator'].includes(user.role))throw Error('当前账号无提交权限；原请求保留。');
    return user;
  }
  async function sendOriginal(fresh) {
    if(busy)return;busy=true;draw();await draftTail;if(cacheError){busy=false;draw();return;}
    try {await lock(async()=>{
      scopeCheck();if(!visible()||!journal)throw Error('原观测页面或本机记录尚未恢复。');
      if(storage.getItem(journal.key)!==journal.raw)throw Error('另一页面已更新原请求，请保留本页输入并读取本机记录。');
      await checkActor(true);scopeCheck();
      if(!visible())throw Error('原观测页面已变化，未发送。');
      if(fresh) {
        const draft=snapshot(),fileId=draft.evidence_id,source=caps.find(cap=>cap.id===draft.source_id),processor=caps.find(cap=>cap.id===draft.extractor_id);
        if(!source || !processor)throw Error('原来源或提取器当前不可选，请核对原输入。');
        if(fileId && processor.data.execution.adapter!=='cloud_extractor')throw Error('请为文件选择已注册的云文件提取器');
        if(!draft.observed_local)throw Error('请填写观测时间（北京时间）。');
        const stamp=new Date(draft.observed_local+'+08:00');if(!Number.isFinite(stamp.getTime()))throw Error('观测时间无效。');
        const parts=new Intl.DateTimeFormat('en-CA',{timeZone:source.data.specification.timezone||'UTC',year:'numeric',month:'2-digit',day:'2-digit'})
          .formatToParts(stamp).reduce((map,part)=>({...map,[part.type]:part.value}),{});
        const attributes=fileId?{}:observationJournalAttributes(draft.fields);
        const body={source_id:draft.source_id,source_record_id:draft.source_record_id,processing_version:'1.0.0',entity_id:draft.entity_id,
          observed_at:stamp.toISOString(),business_day:parts.year+'-'+parts.month+'-'+parts.day,attributes,
          evidence_ids:fileId?[fileId]:[],extractor_id:draft.extractor_id,startup_mode:draft.startup_mode};
        journal=observationJournalStage(storage,journal,body,draft);draw();
      }
      const original=journal,wire=original.value.request?.wire;
      if(!wire || original.value.request.status==='saved')throw Error('没有需要重发的原请求；已保存结果只需刷新。');
      observationJournalBody(wire);
      if(storage.getItem(original.key)!==original.raw)throw Error('原正文已被另一页面更新，未发送。');
      try {
        const row=await request('/observations','POST',wire);await checkActor();scopeCheck();
        journal=observationJournalOutcome(storage,original,wire,{row});
      } catch(error) {
        scopeCheck();journal=observationJournalOutcome(storage,original,wire,{error});
      }
    });} catch(error){failure(error);} finally{busy=false;draw();}
  }
  function protectNavigation(event) {
    if(!visible()){document.removeEventListener('click',protectNavigation,true);window.removeEventListener('beforeunload',protectClose);return;}
    const target=event.target.closest('button,a');
    if((pendingEdits>0||cacheError||blockedDraft) && (event.target.closest('nav button,#refresh,#logout') || target && !observationForm.contains(target))) {
      event.preventDefault();event.stopImmediatePropagation();control('error').textContent='本页输入尚未可靠保存，请先处理存储提示；未丢弃原输入。';
    }
  }
  function protectClose(event) {if(visible()&&(pendingEdits>0||cacheError||blockedDraft)){event.preventDefault();event.returnValue='';}}
  document.addEventListener('click',protectNavigation,true);window.addEventListener('beforeunload',protectClose);
  observationForm.addEventListener('input',saveDraft);observationForm.addEventListener('change',saveDraft);
  control('retry').onclick=()=>sendOriginal(false);
  control('new').onclick=()=>localAction(()=>{journal=observationJournalRetain(storage,journal);restore(journal.value.draft);});
  control('reread').onclick=async()=>{
    if(busy||pendingEdits)return;busy=true;draw();
    try{await lock(()=>{scopeCheck();if(!visible())throw Error('原页面已变化。');const draft=blockedDraft||snapshot();let saved=observationJournalRead(storage,scope);
      saved=observationJournalWrite(storage,saved,{...saved.value,history:[...saved.value.history,{draft,request:null}]});
      journal=saved;blockedDraft=null;cacheError='';restore(journal.value.draft);
    });}catch(error){failure(error);}finally{busy=false;draw();}
  };
  control('refresh').onclick=async()=>{
    if(busy||pendingEdits||cacheError)return;busy=true;refreshError='';draw();
    try{await checkActor();scopeCheck();if(!visible())throw Error('原账号或页面已变化，未刷新其它范围。');await load();}
    catch(error){if(visible())refreshError=error.message+' 已取得的保存记录仍在本机，未重新提交。';}
    finally{busy=false;draw();}
  };
  control('export').onclick=()=>{
    if(!journal||!visible())return;
    const url=URL.createObjectURL(new Blob([observationJournalJSON(journal.value)],{type:'application/json'})),link=document.createElement('a');
    link.href=url;link.download='本机原观测请求与草稿.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
  try{journal=observationJournalRead(storage,scope);if(journal.raw!==null)restore(journal.value.draft);}catch(error){cacheError=error.message;}
  draw();
  async function hydrate() {
    const found=await request('/evidence');scopeCheck();if(!visible())return;files=found;
    const select=formControl('savedEvidence');
    const selected = select.value;
    select.innerHTML = '<option value="">不关联文件，处理数值属性</option>' + files.map(f =>
      `<option value="${esc(f.id)}">${esc(f.data.modality === 'image' ? '图片 '+f.data.width+'×'+f.data.height : '文本')} · ${date(f.created_at)} · ${(f.size_bytes/1024).toFixed(1)}KB</option>`).join('');
    choice(select,selected,'原文件引用（未在最近清单）');draw();
  }
  panel.querySelector('form').onsubmit = e => {e.preventDefault(); action(e.submitter, async () => {
    if(!locks||typeof locks.request!=='function'||busy||pendingEdits||cacheError||journal?.value.request)throw Error('请先核对原观测请求和本机安全保存状态，尚未上传新文件。');
    scopeCheck();if(!visible())throw Error('原页面已变化。');
    const file = $('#evidenceFile').files[0], consent = $('#fileCloudConsent').checked, model = $('#evidenceModel').value;
    if (consent && !model) throw Error('授权云处理时必须选择一个模型');
    const path = '/api/evidence' + (consent ? '?cloud_model_id='+encodeURIComponent(model) : '');
    const response = await fetch(path,{method:'POST',credentials:'same-origin',headers:{'Content-Type':file.type || 'text/plain','X-CSRF-Token':csrf,'X-Workspace-ID':scope.workspace_id},body:file});
    const result = await response.json(); if (!response.ok) throw Error(result.detail || '文件保存失败');
    await checkActor();scopeCheck();if(!visible())return;
    if(journal?.value.request){notice('文件已保存，但当前观测正文已冻结，未替换原文件引用；可保留原请求后另建草稿。');return;}
    choice(formControl('savedEvidence'),result.id,'刚保存的文件引用');saveDraft();await draftTail;draw();await hydrate();if(!visible())return;
    panel.querySelector('form').reset(); notice('文件已保存，可在观测中关联；观测表单中的未提交内容已保留。');
  })};
  formControl('savedEvidence').onchange = () => {
    const selected=formControl('savedEvidence').value;
    formControl('evidenceHint').textContent=selected?'将关联已保存文件。原始证据仍保留；提取结果需要核查。':'';draw();
  };
  observationForm.onsubmit=e=>{e.preventDefault();sendOriginal(true);};
  for (const observation of overview.observations) {
    const card = [...$('#content .grid > div:last-child').querySelectorAll('.card')].find(c => c.querySelector('h3')?.textContent === observation.source_record_id);
    if (!card) continue;
    if (observation.data.processing_error) {
      const error = document.createElement('p'); error.className='notice error'; error.textContent=observation.data.processing_error; card.append(error);
    }
    if (observation.data.extracted && observation.status !== 'processed') {
      const details = document.createElement('div'), entity = entities.find(e => e.id === observation.entity_id);
      details.innerHTML = '<h3>提取结果（待核查）</h3>' + Object.entries(observation.data.extracted.attributes || {}).map(([k,v]) =>
        `<div class="kv"><span>${esc(fieldLabel(entity,k))}</span><b>${esc(v)}</b></div>`).join(''); card.append(details);
    }
    for (const id of observation.data.evidence_ids || []) {
      const image = document.createElement('img'); image.src='/api/evidence/'+id+'/content';
      image.alt='观测原始证据；若为文本请下载核对';image.style.cssText='display:block;max-width:100%;max-height:220px;margin:12px 0;border:1px solid #dce3e8';
      image.onerror=() => image.remove();card.append(image);
      const link = document.createElement('a'); link.href='/api/evidence/'+id+'/content';link.textContent='下载已保存的原始证据';
      link.style.display='block'; card.append(link);
    }
    if (observation.status === 'failed') {
      const retry = document.createElement('button');retry.className='secondary';retry.disabled=me.role==='viewer';retry.textContent='修复原因后重试';card.append(retry);
      retry.onclick=() => simpleReview('核查并重新处理观测（最多3次）',reason => api('/observations/'+observation.id+'/retry','POST',
        {expected_version:observation.version,reason}));
      if (observation.data.evidence_ids?.length) {
        const allow = document.createElement('button');allow.className='secondary';allow.disabled=me.role==='viewer';allow.textContent='核对文件云端授权';allow.style.marginLeft='8px';card.append(allow);
        allow.onclick=async () => {try {
          await hydrate(); const model = caps.find(c => c.id === observation.data.extractor_id)?.data.execution.model_profile_id;
          const file = files.find(f => f.id === observation.data.evidence_ids[0]);
          if (!file || !model) throw Error('提取器或文件配置缺失');
          const label = caps.find(c => c.id === model)?.data.description || '已注册模型';
          simpleReview('允许此文件发送到：'+label,reason => api('/evidence/'+file.id+'/authorize','POST',
            {expected_version:file.version,reason,model_profile_id:model}));
        } catch(error) {notice(error.message,true)}};
      }
    }
  }
  hydrate().catch(error => {if(visible())notice(error.message,true);});
}

function augment() {
  if (current === 'tasks') augmentTasks();
  if (current === 'data') augmentData();
  if (current === 'data') augmentDataQueryReferences();
  if (current === 'perception') augmentPerception();
  augmentGoalData();
  augmentRelations();
  augmentQueryNeeds();
  augmentDataGaps();
  augmentQueryTools();
  augmentGoalQueries();
  if (me.role === 'viewer') $('#content').querySelectorAll('button,form input,form select,form textarea')
    .forEach(button => { if(!button.hasAttribute('data-relation-history')&&!button.hasAttribute('data-quality-history')&&!button.hasAttribute('data-gap-history')&&!button.hasAttribute('data-observation-journal-read')&&!button.hasAttribute('data-query-reference-read')) button.disabled = true; });
}
