import React,{useEffect,useRef,useState} from 'react';

const SOURCE_KEYS=['analysis_id','source_version','contract_version','raw_hash','raw_candidate','source_blocks','item_hashes','citation_hashes'];
const REF_KEYS=['analysis_id','source_version','raw_hash','contract_version'];
const HASH=/^[a-fA-F0-9]{64}$/;
const TYPES={businesses:'业务',objects:'对象',relationships:'关系'};
const fail=message=>{throw Error(message||'原建议、引用或授权原文无法完整核对；原回答与人工输入保留，暂不提交。');};
const need=(condition,message)=>{if(!condition)fail(message);};
const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const exact=(value,keys)=>object(value)&&Object.keys(value).length===keys.length&&keys.every(key=>Object.hasOwn(value,key));
const allowed=(value,keys)=>object(value)&&Object.keys(value).every(key=>keys.includes(key));
const text=value=>typeof value==='string';
const nonempty=value=>text(value)&&value.trim().length>0;
const validHash=value=>text(value)&&value.length===64&&HASH.test(value);
const integerText=value=>text(value)&&value.length>0&&value[0]!=='0'&&!/[^0-9]/u.test(value);
const copy=value=>JSON.parse(JSON.stringify(value));
const chars=value=>Array.from(value);
const refOf=source=>Object.fromEntries(REF_KEYS.map(key=>[key,source[key]]));
const sameRef=(a,b)=>exact(a,REF_KEYS)&&exact(b,REF_KEYS)&&REF_KEYS.every(key=>a[key]===b[key]);
const blockKey=block=>JSON.stringify([block.material_id,block.block_id]);
const sameKeys=(value,keys)=>object(value)&&Object.keys(value).length===keys.length&&keys.every(key=>Object.hasOwn(value,key));
const positive=value=>Number.isSafeInteger(value)&&value>=1;
function scalarFields(value,spec){for(const[key,kind]of Object.entries(spec))if(Object.hasOwn(value,key))need(kind==='nullable'?value[key]===null||text(value[key]):typeof value[key]===kind);}

// These are display/operation paths through the original answer, never a JSON
// Patch interpreter. Source hashes are opaque server anchors, not re-encoded raw.
export function inspectLocalProposalSource(source){
 need(exact(source,SOURCE_KEYS)&&nonempty(source.analysis_id)&&positive(source.source_version)&&[4,5].includes(source.contract_version)&&validHash(source.raw_hash));
 need(Array.isArray(source.source_blocks));const blocks=new Map();
 for(const block of source.source_blocks){
  need(exact(block,['material_id','block_id','material_name','ordinal','text'])&&nonempty(block.material_id)&&nonempty(block.block_id)&&nonempty(block.material_name)&&positive(block.ordinal)&&text(block.text));
  need(!blocks.has(blockKey(block)));blocks.set(blockKey(block),block);
 }
 const raw=source.raw_candidate,rootKeys=source.contract_version===5?['domain','objects','businesses','questions','relationships']:['domain','objects','businesses','questions'];
 need(exact(raw,rootKeys)&&text(raw.domain)&&Array.isArray(raw.questions)&&raw.questions.every(text));
 const items=[],citations=new Map();
 function cites(row,path,parentPath,label){
  need(Array.isArray(row.citations));
  row.citations.forEach((citation,index)=>{
   need(exact(citation,['material_id','block_id','quote'])&&text(citation.material_id)&&text(citation.block_id)&&text(citation.quote));
   const pointer=path+'/citations/'+index;
   citations.set(pointer,{path:pointer,parentPath,label:label+' · 引用'+(index+1),original:citation,originalBlock:blocks.get(blockKey(citation))||null});
  });
 }
 for(const collection of Object.keys(TYPES)){
  if(collection==='relationships'&&source.contract_version===4)continue;
  need(Array.isArray(raw[collection]));
  raw[collection].forEach((row,index)=>{
   const path='/'+collection+'/'+index,label=TYPES[collection]+'建议'+(index+1);
   const keys=collection==='businesses'?['operation','original_name','name','trigger','output','steps','citations','inference_reason']:collection==='objects'?['name','fields','citations','inference_reason']:['name','description','source_type','target_type','cardinality','citations','inference_reason'];
   need(allowed(row,keys));
   scalarFields(row,Object.fromEntries(keys.filter(key=>!['steps','fields','citations'].includes(key)).map(key=>[key,'string'])));
   const item={path,collection,index,label,raw:row};items.push(item);cites(row,path,path,label);
   if(collection==='businesses'){
    need(Array.isArray(row.steps));row.steps.forEach((step,i)=>{
     const stepKeys=['key','name','purpose','agent','role','output','next_key','condition','else_key','citations','inference_reason'];
     need(allowed(step,stepKeys));scalarFields(step,Object.fromEntries(stepKeys.filter(key=>key!=='citations').map(key=>[key,'string'])));
     cites(step,path+'/steps/'+i,path,label+' · 步骤'+(i+1));
    });
   }
   if(collection==='objects'){
    need(Array.isArray(row.fields));row.fields.forEach((field,i)=>{
     need(allowed(field,['label','kind','required','unit','citations','inference_reason']));
     scalarFields(field,{label:'string',kind:'string',required:'boolean',unit:'nullable',inference_reason:'string'});
     cites(field,path+'/fields/'+i,path,label+' · 字段'+(i+1));
    });
   }
  });
 }
 need(sameKeys(source.item_hashes,items.map(item=>item.path))&&sameKeys(source.citation_hashes,[...citations.keys()]));
 for(const value of [...Object.values(source.item_hashes),...Object.values(source.citation_hashes)])need(validHash(value));
 return {ref:refOf(source),items,citations,blocks,raw};
}

export function emptyLocalProposalReviewDraft(source){return {source:inspectLocalProposalSource(source).ref,edits:[]};}

function checkedDraft(model,draft){
 if(draft===undefined||draft===null)return {source:{...model.ref},edits:[]};
 need(exact(draft,['source','edits'])&&sameRef(draft.source,model.ref)&&Array.isArray(draft.edits),'这份人工草稿属于其他原建议，或草稿格式尚未核对；输入保留，请恢复对应记录。');
 const paths=new Set(),discarded=new Set();
 for(const edit of draft.edits){
  need(object(edit)&&text(edit.path)&&!paths.has(edit.path)&&text(edit.reason),'人工草稿包含重复或无法核对的修改，原输入保留。');paths.add(edit.path);
  if(edit.op==='discard_item'){
   need(exact(edit,['op','path','reason'])&&model.items.some(item=>item.path===edit.path));discarded.add(edit.path);
  }else{
   need(edit.op==='replace_citation'&&exact(edit,['op','path','reason','material_id','block_id','start_text','end_text'])&&model.citations.has(edit.path));
   need((edit.material_id===null||text(edit.material_id))&&(edit.block_id===null||text(edit.block_id))&&text(edit.start_text)&&text(edit.end_text));
   need((edit.material_id===null)===(edit.block_id===null));
   if(edit.material_id!==null)need(model.blocks.has(blockKey(edit)),'人工草稿选择的段落不在本次授权原文中，输入保留，暂不提交。');
  }
 }
 for(const edit of draft.edits)if(edit.op==='replace_citation')need(!discarded.has(model.citations.get(edit.path).parentPath),'同一项不能既舍弃又替换内部引用，请先恢复其中一种修改。');
 return draft;
}

export function selectedLocalProposalQuote(source,edit){
 const model=inspectLocalProposalSource(source),block=model.blocks.get(blockKey(edit));
 need(block,'请先选择本次已授权的原文段落。');
 need(integerText(edit.start_text)&&integerText(edit.end_text),'请选择连续原文的起始和结束字符。');
 const start=Number(edit.start_text),end=Number(edit.end_text),letters=chars(block.text);
 need(positive(start)&&positive(end)&&start<=end&&end<=letters.length,'原文范围必须位于同一段落内，起始不能晚于结束。');
 const quote=letters.slice(start-1,end).join('');
 need(nonempty(quote)&&chars(quote).length<=250,'请选取同一段连续原文，最多250个字符；不会自动删字或拼接。');
 return {material_id:block.material_id,block_id:block.block_id,quote};
}

export function localProposalReviewPayload(source,draft){
 const model=inspectLocalProposalSource(source),value=checkedDraft(model,draft);
 need(value.edits.length>0,'请先明确舍弃一项建议，或替换一处引用。');
 const operations=value.edits.map(edit=>{
  need(chars(edit.reason.replace(/\s/gu,'')).length>=6&&chars(edit.reason).length<=1000,'每项修改请填写至少6个非空白字符、最多1000个字符的理由。');
  if(edit.op==='discard_item')return {op:edit.op,path:edit.path,expected_hash:source.item_hashes[edit.path],reason:edit.reason};
  return {op:edit.op,path:edit.path,expected_hash:source.citation_hashes[edit.path],replacement:selectedLocalProposalQuote(source,edit),reason:edit.reason};
 });
 return {source:{...model.ref},operations};
}

const display=(value,fallback='未提供，仍需核对')=>text(value)&&value.length?value:fallback;
const enumLabel=(values,key,fallback)=>Object.hasOwn(values,key)?values[key]:fallback;
const PlainButton=props=><button type="button" {...props}/>;

export default function LocalProposalReviewV261({source,draft,onDraftChange,durableState='unknown',onSubmit,busy=false,error='',Btn=PlainButton}){
 const [actionError,setActionError]=useState(''),[submitting,setSubmitting]=useState(false),[notice,setNotice]=useState('');
 const alive=useRef(true),current=useRef(null),paragraphs=useRef(new Map()),sequence=useRef(0),flight=useRef(null);
 let model=null,value=null,invalid='';
 try{model=inspectLocalProposalSource(source);value=checkedDraft(model,draft);need(['saved','saving','error','unknown'].includes(durableState),'人工草稿保存状态无法核对，输入保留，暂不提交。');}catch(cause){invalid=cause.message;}
 const scope=model?JSON.stringify(model.ref):null,editable=!invalid&&durableState!=='unknown'&&!busy&&!submitting&&!flight.current&&typeof onDraftChange==='function';
 current.current={scope,model,value,editable,durableState,busy,submitting,source,onDraftChange,onSubmit};
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;sequence.current+=1;};},[]);
 useEffect(()=>{setActionError('');setNotice('');setSubmitting(Boolean(flight.current));sequence.current+=1;},[scope]);
 function change(update){
  const now=current.current;if(!now.editable||now.scope!==scope||flight.current)return;
  try{const next=copy(now.value);update(next,now.model);checkedDraft(now.model,next);now.onDraftChange(next);setActionError('');setNotice('');}
  catch(cause){setActionError(cause.message||'人工修改尚未保存，原输入保留。');}
 }
 function updateEdit(path,patch){change(next=>{const edit=next.edits.find(row=>row.path===path);need(edit);Object.assign(edit,patch);});}
 function toggleDiscard(item){change((next,m)=>{
  const existing=next.edits.find(edit=>edit.path===item.path);
  if(existing){next.edits=next.edits.filter(edit=>edit.path!==item.path);return;}
  need(!next.edits.some(edit=>edit.op==='replace_citation'&&m.citations.get(edit.path).parentPath===item.path),'请先恢复本项已修改的引用，再舍弃整项；不会自动删除您的理由。');
  next.edits.push({op:'discard_item',path:item.path,reason:''});
 });}
 function toggleCitation(citation){change(next=>{
  const existing=next.edits.find(edit=>edit.path===citation.path);
  if(existing){next.edits=next.edits.filter(edit=>edit.path!==citation.path);return;}
  need(!next.edits.some(edit=>edit.op==='discard_item'&&edit.path===citation.parentPath),'本项已标记舍弃，不能同时替换其内部引用。');
  next.edits.push({op:'replace_citation',path:citation.path,reason:'',material_id:null,block_id:null,start_text:'',end_text:''});
 });}
 function useSelection(citation){
  const now=current.current;if(!now.editable||now.scope!==scope||flight.current)return;
  try{
   const node=paragraphs.current.get(citation.path),edit=now.value.edits.find(row=>row.path===citation.path),block=now.model.blocks.get(blockKey(edit||{}));
   need(node&&block&&node.value===block.text&&node.selectionEnd>node.selectionStart,'请先在这段只读原文中选中连续文字。');
   const offsets=[0];for(const letter of chars(block.text))offsets.push(offsets.at(-1)+letter.length);
   const start=offsets.indexOf(node.selectionStart),end=offsets.indexOf(node.selectionEnd);
   need(start>=0&&end>start,'请选择完整字符，不截断字符。');
   updateEdit(citation.path,{start_text:String(start+1),end_text:String(end)});
  }catch(cause){setActionError(cause.message);}
 }
 async function submit(){
  const now=current.current;
  if(!now.editable||now.scope!==scope||flight.current||now.durableState!=='saved'||typeof now.onSubmit!=='function')return;
  let payload;try{payload=localProposalReviewPayload(now.source,now.value);}catch(cause){setActionError(cause.message);return;}
  const id=++sequence.current,expected=now.scope,active={id,scope:expected};flight.current=active;setSubmitting(true);setActionError('');setNotice('');
  try{
   // No HTTP, nonce, storage or configuration mutation lives in this component.
   await now.onSubmit(copy(payload));
   if(alive.current&&sequence.current===id&&current.current.scope===expected)setNotice('本地核对草案已交给保存流程；仍须核对服务器结果，未应用配置或授权执行。');
  }catch{
   if(alive.current&&sequence.current===id&&current.current.scope===expected)setActionError('本地核对草案未能提交，全部人工输入保留，请核对保存状态后重试。');
  }finally{if(flight.current===active){flight.current=null;if(alive.current)setSubmitting(false);}}
 }
 const edits=value?.edits||[],discarded=new Set(edits.filter(edit=>edit.op==='discard_item').map(edit=>edit.path));
 let payloadError='';if(model&&value)try{localProposalReviewPayload(source,value);}catch(cause){payloadError=cause.message;}
 function reasonInput(edit,label){return <label>修改理由（至少6个非空白字符）<textarea aria-label={label+'的修改理由'} value={edit.reason} onChange={event=>updateEdit(edit.path,{reason:event.target.value})}/><small>当前 {chars(edit.reason).length} / 1000 字符</small></label>;}
 function citationView(path){
  const citation=model.citations.get(path),edit=edits.find(row=>row.path===path),isDiscarded=discarded.has(citation.parentPath);
  const block=edit?.material_id!==null&&edit?model.blocks.get(blockKey(edit)):null;
  let replacement=null,rangeError='';if(edit)try{replacement=selectedLocalProposalQuote(source,edit);}catch(cause){rangeError=cause.message;}
  return <article key={path} aria-label={citation.label} className="local-proposal-citation">
   <p><strong>{citation.label}</strong> · 原引用</p><blockquote>{citation.original.quote||'（原引用为空）'}</blockquote>
   {citation.originalBlock?<details><summary>查看原引用对应的授权段落</summary><p>{citation.originalBlock.material_name} · 第{citation.originalBlock.ordinal}段</p><p style={{whiteSpace:'pre-wrap'}}>{citation.originalBlock.text}</p></details>:<p>原引用未匹配本次授权段落，请核对后替换；不会把引用存在当作内容正确。</p>}
   <Btn type="button" disabled={!editable||isDiscarded} onClick={()=>toggleCitation(citation)}>{edit?'恢复这处原引用':'替换这处引用'}</Btn>
   {edit&&<fieldset disabled={!editable||isDiscarded}><legend>从已授权原文选择替换内容</legend>
    <label>原文段落<select aria-label={citation.label+'的授权段落'} value={block?blockKey(block):''} onChange={event=>{const chosen=model.blocks.get(event.target.value);if(chosen)updateEdit(path,{material_id:chosen.material_id,block_id:chosen.block_id,start_text:'',end_text:''});else updateEdit(path,{material_id:null,block_id:null,start_text:'',end_text:''});}}><option value="">请选择原文段落</option>{[...model.blocks].map(([key,entry])=><option key={key} value={key}>{entry.material_name} · 第{entry.ordinal}段</option>)}</select></label>
    {block&&<><label>授权原文（只读，可选中文字）<textarea aria-label={citation.label+'的只读授权原文'} readOnly value={block.text} rows={6} ref={node=>{if(node)paragraphs.current.set(path,node);else paragraphs.current.delete(path);}}/></label><Btn type="button" disabled={!editable} onClick={()=>useSelection(citation)}>使用这段原文中选中的文字</Btn><p>按完整字符从1计数，结束位置包含该字符；整段共 {chars(block.text).length} 个字符。</p></>}
    <label>起始字符<input aria-label={citation.label+'的起始字符'} type="number" min="1" step="1" value={edit.start_text} onChange={event=>updateEdit(path,{start_text:event.target.value})}/></label>
    <label>结束字符（含）<input aria-label={citation.label+'的结束字符'} type="number" min="1" step="1" value={edit.end_text} onChange={event=>updateEdit(path,{end_text:event.target.value})}/></label>
    <p><strong>替换后的引用预览</strong></p>{replacement?<blockquote aria-label={citation.label+'的新引用预览'}>{replacement.quote}</blockquote>:<p>{rangeError}</p>}
    {reasonInput(edit,citation.label)}
   </fieldset>}
  </article>;
 }
 function ownCitations(path,row){return row.citations.map((_,index)=>citationView(path+'/citations/'+index));}
 function itemView(item){
  const row=item.raw,edit=edits.find(value=>value.path===item.path),isDiscarded=discarded.has(item.path);
  return <article key={item.path} aria-label={item.label} className="local-proposal-item">
   <h3>{item.label}：{display(row.name,'名称待核对')}</h3>
   {item.collection==='businesses'&&<><p>原建议操作：{enumLabel({upsert:row.original_name?'修改已有业务':'新增业务',remove:'移除业务'},row.operation,'操作待核对')}</p><p>启动条件：{display(row.trigger)}</p><p>产出：{display(row.output)}</p><ol>{row.steps.map((step,index)=>{const destination=key=>key==='auto'?'按顺序进入下一步':key==='end'?'结束':row.steps.find(candidate=>candidate.key===key)?.name||'去向待核对';return <li key={index}><h4>步骤{index+1}：{display(step.name,'名称待核对')}</h4><p>{display(step.purpose)}</p><p>处理职责：{display(step.agent)} · 岗位：{display(step.role)}</p><p>输出：{display(step.output)} · 下一步：{destination(step.next_key)}</p>{step.condition&&<p>条件：{step.condition}；未满足时：{destination(step.else_key)}</p>}{ownCitations(item.path+'/steps/'+index,step)}{step.inference_reason&&<p>原推断说明：{step.inference_reason}</p>}</li>;})}</ol></>}
   {item.collection==='objects'&&<ul>{row.fields.map((field,index)=><li key={index}><h4>字段{index+1}：{display(field.label,'名称待核对')}</h4><p>类型：{display(field.kind)} · {field.required===true?'必填':field.required===false?'可选':'必填要求待核对'} · 单位：{display(field.unit,'未知')}</p>{ownCitations(item.path+'/fields/'+index,field)}{field.inference_reason&&<p>原推断说明：{field.inference_reason}</p>}</li>)}</ul>}
   {item.collection==='relationships'&&<><p>{display(row.description)}</p><p>源对象类型：{display(row.source_type)} → 目标对象类型：{display(row.target_type)}</p><p>多重性：{enumLabel({many:'多个对应',one_per_source:'每个源最多一个目标',one_per_target:'每个目标最多一个源',unspecified:'未明确'},row.cardinality,'待核对')}</p></>}
   {ownCitations(item.path,row)}{row.inference_reason&&<p>原推断说明：{row.inference_reason}</p>}
   <Btn type="button" disabled={!editable} onClick={()=>toggleDiscard(item)}>{isDiscarded?'恢复这项'+TYPES[item.collection]+'建议':'舍弃这项'+TYPES[item.collection]+'建议'}</Btn>
   {isDiscarded&&<fieldset disabled={!editable}><legend>本项已标记舍弃（尚未提交）</legend>{reasonInput(edit,item.label)}{item.collection==='objects'&&<p>相关关系不会自动舍弃或重连；请逐项核对，服务器仍会检查关系端点。</p>}</fieldset>}
  </article>;
 }
 return <section aria-label="仅在本地核对原建议">
  <h2>仅在本地核对原建议</h2><p>原回答完整保留。本页只整理人工纠错草案，不调用云、不应用配置，也不授权执行。</p>
  {model&&<p>原理解记录第{model.ref.source_version}版 · 第{model.ref.contract_version}版资料理解。引用存在不代表业务、对象关系或能力权限已通过核对。</p>}
  {invalid&&<p role="alert">{invalid}</p>}
  {durableState==='unknown'&&<p role="status">正在核对人工草稿的保存与恢复状态，暂不能编辑或提交。</p>}
  {durableState==='saving'&&<p role="status">人工草稿正在保存，可以继续编辑；保存完成前不能提交。</p>}
  {durableState==='error'&&<p role="alert">人工草稿尚未可靠保存，输入保留；请恢复保存后再提交。</p>}
  {(busy||submitting)&&<p role="status">正在提交本地核对草案，请保留本页输入并等待结果。</p>}
  {error&&<p role="alert">{text(error)?error:'提交状态无法核对，原输入保留。'}</p>}{actionError&&<p role="alert">{actionError}</p>}{notice&&<p role="status">{notice}</p>}
  {model&&value&&<>{model.raw.domain&&<p>原领域说明：{model.raw.domain}</p>}{Object.keys(TYPES).map(collection=>{const items=model.items.filter(item=>item.collection===collection);if(collection==='relationships'&&model.ref.contract_version===4)return null;return <section key={collection} aria-label={TYPES[collection]+'建议清单'}><h3>{TYPES[collection]}建议 · {items.length}项</h3>{items.length?items.map(itemView):<p>原回答没有此类建议。</p>}</section>;})}{model.raw.questions.length>0&&<aside aria-label="原回答待澄清事项"><h3>原回答待澄清事项</h3><ul>{model.raw.questions.map((question,index)=><li key={index}>{question}</li>)}</ul></aside>}</>}
  <section aria-label="本次人工修改摘要"><h3>本次人工修改 · {edits.length}项</h3>{edits.length?<ol>{edits.map(edit=><li key={edit.path}>{edit.op==='discard_item'?'舍弃'+model.items.find(item=>item.path===edit.path).label:'替换'+model.citations.get(edit.path).label}：{edit.reason||'理由尚未填写'}</li>)}</ol>:<p>尚未选择修改；原建议未改变。</p>}{payloadError&&edits.length>0&&<p>{payloadError}</p>}</section>
  <Btn type="button" disabled={!editable||durableState!=='saved'||!!payloadError||typeof onSubmit!=='function'} onClick={submit}>另存为待核对建议（仅本地）</Btn>
  <p>提交后仍须由服务端核对完整结构、原文和关系；未通过不会自动再次付费，也不代表已采纳、已发布或可以执行。</p>
 </section>;
}
