// Root executes this file. Sources, hashes and parent durability states below
// are explicit synthetic fixtures, not model quality or persistence evidence.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
import {build} from 'vite';
import {JSDOM,ResourceLoader,VirtualConsole} from 'jsdom';

const project=resolve(fileURLToPath(new URL('..',import.meta.url))),require=createRequire(join(project,'package.json')),reactPath=require.resolve('react'),reactURL=pathToFileURL(reactPath).href;
const clone=value=>JSON.parse(JSON.stringify(value)),submitLabel='另存为待核对建议（仅本地）',citeLabel='业务建议1 · 引用1';
const hash=letter=>letter.repeat(64);
function fixture(){
 const citation=()=>({material_id:'material-synthetic',block_id:'block-one',quote:'这句模型引用未匹配原文'});
 const raw={domain:'工程合成资料，非真实业务验收',objects:[{name:'设备',fields:[{label:'设备名称',kind:'文本',required:true,unit:null,citations:[citation()],inference_reason:''}],citations:[citation()],inference_reason:''}],businesses:[{operation:'upsert',original_name:'',name:'核对环境资料',trigger:'人工提出核对要求',output:'待核对记录',steps:[{key:'review',name:'核对原文',purpose:'由配置岗位核对',agent:'业务智能体',role:'核查岗位',output:'人工说明',next_key:'end',condition:'',else_key:'end',citations:[citation()],inference_reason:''}],citations:[citation()],inference_reason:''},{operation:'upsert',original_name:'',name:'保留第二项业务',trigger:'人工启动',output:'待核对资料',steps:[],citations:[],inference_reason:''}],questions:['关系尚未通过原文核对。'],relationships:[{name:'设备安装在地点',description:'待核对的关系建议',source_type:'设备',target_type:'地点',cardinality:'unspecified',citations:[citation()],inference_reason:''}]};
 return {analysis_id:'synthetic-analysis-v261',source_version:3,contract_version:5,raw_hash:hash('a'),raw_candidate:raw,source_blocks:[{material_id:'material-synthetic',block_id:'block-one',material_name:'授权资料甲',ordinal:1,text:'甲😀乙。\n设备应由核查岗位检查。后续原文完整保留。'},{material_id:'material-synthetic',block_id:'block-two',material_name:'授权资料甲',ordinal:2,text:'第二段独立原文，不得与第一段拼接。'}],item_hashes:{'/businesses/0':hash('b'),'/businesses/1':hash('c'),'/objects/0':hash('d'),'/relationships/0':hash('e')},citation_hashes:{'/businesses/0/citations/0':hash('1'),'/businesses/0/steps/0/citations/0':hash('2'),'/objects/0/citations/0':hash('3'),'/objects/0/fields/0/citations/0':hash('4'),'/relationships/0/citations/0':hash('5')}};
}
const discarded=(path='/businesses/0',reason='此项建议缺少可靠原文依据')=>({op:'discard_item',path,reason});
const replaced=(path='/businesses/0/citations/0',overrides={})=>({op:'replace_citation',path,reason:'原引用不匹配授权段落，需要改正',material_id:'material-synthetic',block_id:'block-one',start_text:'2',end_text:'4',...overrides});
const ref=source=>Object.fromEntries(['analysis_id','source_version','raw_hash','contract_version'].map(key=>[key,source[key]]));
const draft=(source,edits=[])=>({source:ref(source),edits});
let folder,bundle;
after(async()=>{if(folder)await rm(folder,{recursive:true,force:true});});
async function production(){
 if(!bundle)bundle=(async()=>{folder=await mkdtemp(join(tmpdir(),'local-proposal-review-'));const entry=join(folder,'entry.jsx');await writeFile(entry,'export {default as Panel,inspectLocalProposalSource,emptyLocalProposalReviewDraft,selectedLocalProposalQuote,localProposalReviewPayload} from '+JSON.stringify(join(project,'src/LocalProposalReviewV261.jsx'))+';');const result=await build({root:project,configFile:false,envDir:folder,cacheDir:join(folder,'cache'),logLevel:'silent',define:{'process.env.NODE_ENV':'"development"'},build:{ssr:entry,write:false,minify:false,rollupOptions:{external:name=>['react',reactPath,reactURL].includes(name),output:{format:'es',inlineDynamicImports:true,paths:name=>['react',reactPath,reactURL].includes(name)?reactURL:name}}}}),chunk=result.output.find(row=>row.type==='chunk'&&row.isEntry);assert.ok(chunk);const path=join(folder,'production.mjs');await writeFile(path,chunk.code);return import(pathToFileURL(path).href);})();return bundle;
}

test('V261 source traversal preserves original item and nested citation paths with opaque server hashes',async()=>{
 const code=await production(),source=fixture(),before=JSON.stringify(source),model=code.inspectLocalProposalSource(source);
 assert.deepEqual(model.items.map(item=>item.path),['/businesses/0','/businesses/1','/objects/0','/relationships/0']);assert.equal(model.citations.size,5);assert.equal(model.citations.get('/objects/0/fields/0/citations/0').parentPath,'/objects/0');assert.equal(model.citations.get('/businesses/0/citations/0').original.quote,'这句模型引用未匹配原文');assert.deepEqual(code.emptyLocalProposalReviewDraft(source),draft(source));assert.equal(JSON.stringify(source),before);
 const v4=clone(source);v4.contract_version=4;delete v4.raw_candidate.relationships;delete v4.item_hashes['/relationships/0'];delete v4.citation_hashes['/relationships/0/citations/0'];assert.equal(code.inspectLocalProposalSource(v4).items.length,3);
});
test('V261 unknown fields missing or dangling hashes and invalid source blocks fail closed',async()=>{
 const code=await production();
 for(const mutate of [s=>{s.extra=true;},s=>{s.contract_version=6;},s=>{delete s.item_hashes['/businesses/0'];},s=>{delete s.citation_hashes['/objects/0/fields/0/citations/0'];},s=>{s.item_hashes['/businesses/01']=hash('f');},s=>{s.citation_hashes['/businesses/0/steps/0/citations/99']=hash('f');},s=>{s.source_blocks.push(clone(s.source_blocks[0]));},s=>{s.source_blocks[0].text={unsafe:'not paragraph text'};},s=>{s.source_blocks[0].unknown=true;},s=>{s.raw_candidate.businesses[0].unknown=true;},s=>{s.raw_candidate.objects[0].fields[0].unknown=true;},s=>{s.raw_candidate.relationships[0].citations[0].unknown=true;}]){const source=fixture();mutate(source);assert.throws(()=>code.inspectLocalProposalSource(source));assert.throws(()=>code.localProposalReviewPayload(source,draft(source,[discarded()])));}
});
test('V261 operations contain only original paths expected hashes and exact source reference without mutating original',async()=>{
 const code=await production(),source=fixture(),value=draft(source,[discarded('/businesses/0','  保留完整理由，不缩减原文  '),replaced('/objects/0/fields/0/citations/0')]),before=JSON.stringify({source,value});
 assert.deepEqual(code.localProposalReviewPayload(source,value),{source:ref(source),operations:[{op:'discard_item',path:'/businesses/0',expected_hash:source.item_hashes['/businesses/0'],reason:'  保留完整理由，不缩减原文  '},{op:'replace_citation',path:'/objects/0/fields/0/citations/0',expected_hash:source.citation_hashes['/objects/0/fields/0/citations/0'],replacement:{material_id:'material-synthetic',block_id:'block-one',quote:'😀乙。'},reason:'原引用不匹配授权段落，需要改正'}]});
 assert.equal(JSON.stringify({source,value}),before);assert.equal(source.raw_candidate.businesses[1].name,'保留第二项业务');
});
test('V261 quote selection uses one real contiguous Unicode range and never truncates or joins paragraphs',async()=>{
 const code=await production(),source=fixture();assert.equal(code.selectedLocalProposalQuote(source,replaced()).quote,'😀乙。');assert.equal(code.selectedLocalProposalQuote(source,replaced(undefined,{start_text:'4',end_text:'5'})).quote,'。\n');
 for(const range of [{start_text:'0'},{start_text:'1.5'},{start_text:'02'},{start_text:'5',end_text:'2'},{end_text:'999'},{start_text:''},{material_id:'unapproved'},{block_id:'unknown'}])assert.throws(()=>code.selectedLocalProposalQuote(source,replaced(undefined,range)));
 source.source_blocks[0].text='字'.repeat(251);assert.throws(()=>code.selectedLocalProposalQuote(source,replaced(undefined,{start_text:'1',end_text:'251'})),/250/);assert.equal(code.selectedLocalProposalQuote(source,replaced(undefined,{start_text:'1',end_text:'250'})).quote.length,250);
});
test('V261 rejects overlap duplicate paths generic patches foreign drafts and incomplete reasons',async()=>{
 const code=await production(),source=fixture();
 for(const edits of [[discarded(),replaced()],[replaced(),discarded()],[discarded(),discarded()],[{...discarded(),op:'remove'}],[discarded('/businesses/0/steps/0')],[{...replaced(),replacement:{quote:'free text'}}],[discarded('/businesses/0','一 二 三 四 五')],[discarded('/businesses/0','字'.repeat(1001))],[replaced(undefined,{start_text:''})]])assert.throws(()=>code.localProposalReviewPayload(source,draft(source,edits)));
 const old=draft(source,[discarded()]);old.source.raw_hash=hash('f');assert.throws(()=>code.localProposalReviewPayload(source,old),/其他原建议/);assert.throws(()=>code.localProposalReviewPayload(source,draft(source)),/先明确/);
});
test('V261 component safely displays names and evidence while unknown durability or bad source blocks all edits and submit',async()=>{
 const code=await production(),source=fixture();source.raw_candidate.objects[0].name='物种<svg onload="unsafe()">';source.raw_candidate.businesses[0].name='声纹<script>unsafe()</script>';source.raw_candidate.businesses[0].operation='__proto__';source.raw_candidate.relationships[0].cardinality='constructor';
 const html=renderToStaticMarkup(React.createElement(code.Panel,{source,draft:draft(source),onDraftChange:()=>{},onSubmit:()=>{}}));assert.match(html,/暂不能编辑或提交/);assert.match(html,/原回答完整保留/);assert.match(html,/&lt;svg/);assert.equal(html.includes('<svg'),false);assert.equal(html.includes('<script'),false);assert.equal(html.includes('<pre'),false);assert.equal(html.includes('raw_candidate'),false);
 const bad=fixture();delete bad.item_hashes['/objects/0'];const invalid=renderToStaticMarkup(React.createElement(code.Panel,{source:bad,draft:null,durableState:'saved',onDraftChange:()=>{},onSubmit:()=>{}}));assert.match(invalid,/role="alert"/);assert.match(invalid,/暂不提交/);assert.equal(invalid.includes('aria-label="对象建议1"'),false);
});

async function scene(options={}){
 assert.equal(process.env.NODE_ENV,'development');const code=await production(),errors=[],http=[],writes=[],vc=new VirtualConsole();vc.on('jsdomError',cause=>errors.push(cause));
 class Offline extends ResourceLoader{fetch(){throw Error('External resource forbidden');}}
 const window=new JSDOM('<div id="root"></div>',{url:'https://local-review.invalid/',resources:new Offline(),virtualConsole:vc}).window,prior=new Map(),deny=(...args)=>{http.push(args);throw Error('No HTTP in local proposal component');},storage={getItem:()=>null,setItem:(...args)=>{writes.push(args);throw Error('Parent owns persistence');},removeItem:(...args)=>{writes.push(args);throw Error('Parent owns persistence');}};
 for(const[key,value]of Object.entries({window,document:window.document,navigator:window.navigator,HTMLElement:window.HTMLElement,Event:window.Event,MouseEvent:window.MouseEvent,IS_REACT_ACT_ENVIRONMENT:true,fetch:deny,localStorage:storage,sessionStorage:storage})){prior.set(key,Object.getOwnPropertyDescriptor(globalThis,key));Object.defineProperty(globalThis,key,{value,writable:true,configurable:true});}
 window.fetch=deny;const{createRoot}=await import('react-dom/client'),container=document.getElementById('root'),root=createRoot(container,{onUncaughtError:cause=>errors.push(cause),onCaughtError:cause=>errors.push(cause),onRecoverableError:cause=>errors.push(cause)});
 let flags={source:fixture(),durableState:'saved',busy:false,error:'',...options},latestDraft=clone(options.initialDraft??null),setDraft,changes=[],submitted=[];
 function Host(props){const[value,setValue]=React.useState(latestDraft);latestDraft=value;setDraft=setValue;return React.createElement(code.Panel,{...props,draft:value,onDraftChange:next=>{changes.push(clone(next));setValue(next);},onSubmit:payload=>{submitted.push(clone(payload));return props.submit?.(payload);}});}
 async function render(next={}){flags={...flags,...next};await act(async()=>root.render(React.createElement(Host,flags)));}
 await render();
 const button=(label,within=container)=>[...within.querySelectorAll('button')].find(node=>node.textContent===label),aria=(label,within=container)=>[...within.querySelectorAll('[aria-label]')].find(node=>node.getAttribute('aria-label')===label);
 async function click(label,within=container){const node=typeof label==='string'?button(label,within):label;assert.ok(node,'button '+label);assert.equal(node.matches(':disabled'),false,'button enabled: '+node.textContent);await act(async()=>node.dispatchEvent(new window.MouseEvent('click',{bubbles:true})));}
 async function value(label,next){const node=aria(label);assert.ok(node,'field '+label);assert.equal(node.matches(':disabled'),false,'field enabled: '+label);await act(async()=>{const prototype=node.tagName==='TEXTAREA'?window.HTMLTextAreaElement.prototype:node.tagName==='SELECT'?window.HTMLSelectElement.prototype:window.HTMLInputElement.prototype;Object.getOwnPropertyDescriptor(prototype,'value').set.call(node,next);node.dispatchEvent(new window.Event(node.tagName==='SELECT'?'change':'input',{bubbles:true}));});}
 return {code,window,container,button,aria,click,value,render,submitted,changes,draft:()=>clone(latestDraft),replaceDraft:async next=>{await act(async()=>setDraft(next));},close:async()=>{try{await act(async()=>root.unmount());assert.deepEqual(errors,[]);assert.deepEqual(http,[]);assert.deepEqual(writes,[]);}finally{window.close();for(const[key,descriptor]of prior){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}}}};
}

test('V261 actual ReactDOM unknown and busy states guard event handlers as well as visible controls',async()=>{
 const h=await scene({durableState:'unknown'});try{const item=h.aria('业务建议1'),discard=h.button('舍弃这项业务建议',item);assert.equal(discard.matches(':disabled'),true);await act(async()=>discard.dispatchEvent(new h.window.MouseEvent('click',{bubbles:true})));assert.equal(h.changes.length,0);assert.equal(h.submitted.length,0);await h.render({durableState:'saved'});await h.click('舍弃这项业务建议',item);await h.value('业务建议1的修改理由','明确原文没有支持这项业务建议');const before=h.draft();await h.render({busy:true});assert.equal(h.aria('业务建议1的修改理由').matches(':disabled'),true);assert.equal(h.button(submitLabel).matches(':disabled'),true);await act(async()=>h.button(submitLabel).dispatchEvent(new h.window.MouseEvent('click',{bubbles:true})));assert.deepEqual(h.draft(),before);assert.equal(h.submitted.length,0);}finally{await h.close();}
});
test('V261 actual ReactDOM controlled partial reasons survive saving and error without claiming persistence',async()=>{
 const h=await scene();try{await h.click('舍弃这项业务建议',h.aria('业务建议1'));await h.value('业务建议1的修改理由','理由未填完');assert.equal(h.button(submitLabel).matches(':disabled'),true);await h.render({durableState:'saving'});assert.equal(h.aria('业务建议1的修改理由').matches(':disabled'),false);await h.value('业务建议1的修改理由','继续补全理由，原文无法支持本项建议');assert.equal(h.button(submitLabel).matches(':disabled'),true);const before=h.draft();await h.render({durableState:'error',error:'父保存流程报告写入失败'});assert.match(h.container.textContent,/人工草稿尚未可靠保存/);assert.equal(h.aria('业务建议1的修改理由').value,before.edits[0].reason);assert.equal(h.button(submitLabel).matches(':disabled'),true);await h.render({durableState:'saved',error:''});assert.equal(h.button(submitLabel).matches(':disabled'),false);assert.equal(h.submitted.length,0);}finally{await h.close();}
});
test('V261 actual ReactDOM replacement selects authorized paragraph and previews exact original Unicode text',async()=>{
 const h=await scene();try{const citation=h.aria(citeLabel);await h.click('替换这处引用',citation);const select=h.aria(citeLabel+'的授权段落');assert.deepEqual([...select.options].map(option=>option.textContent),['请选择原文段落','授权资料甲 · 第1段','授权资料甲 · 第2段']);await h.value(citeLabel+'的授权段落',JSON.stringify(['material-synthetic','block-one']));assert.equal(h.aria(citeLabel+'的只读授权原文').readOnly,true);await h.value(citeLabel+'的起始字符','2');await h.value(citeLabel+'的结束字符','4');await h.value(citeLabel+'的修改理由','原引用不对应原文，改用连续完整原句');assert.equal(h.aria(citeLabel+'的新引用预览').textContent,'😀乙。');await h.click(submitLabel);assert.equal(h.submitted.length,1);assert.deepEqual(h.submitted[0].operations[0],{op:'replace_citation',path:'/businesses/0/citations/0',expected_hash:hash('1'),replacement:{material_id:'material-synthetic',block_id:'block-one',quote:'😀乙。'},reason:'原引用不对应原文，改用连续完整原句'});assert.equal(h.draft().edits[0].start_text,'2');assert.match(h.container.textContent,/未应用配置或授权执行/);}finally{await h.close();}
});
test('V261 actual ReactDOM selection helper converts browser UTF16 offsets without splitting a Unicode character',async()=>{
 const h=await scene();try{await h.click('替换这处引用',h.aria(citeLabel));await h.value(citeLabel+'的授权段落',JSON.stringify(['material-synthetic','block-one']));const original=h.aria(citeLabel+'的只读授权原文');original.setSelectionRange(1,5);await h.click('使用这段原文中选中的文字');assert.equal(h.aria(citeLabel+'的起始字符').value,'2');assert.equal(h.aria(citeLabel+'的结束字符').value,'4');assert.equal(h.aria(citeLabel+'的新引用预览').textContent,'😀乙。');const before=h.draft();original.setSelectionRange(2,3);await h.click('使用这段原文中选中的文字');assert.match(h.container.textContent,/不截断字符/);assert.deepEqual(h.draft(),before);}finally{await h.close();}
});
test('V261 actual ReactDOM whole item discard never silently removes a citation reason or dependent relationship',async()=>{
 const h=await scene();try{await h.click('替换这处引用',h.aria(citeLabel));await h.value(citeLabel+'的修改理由','这一处引用需要重新选择原文段落');const before=h.draft();await h.click('舍弃这项业务建议',h.aria('业务建议1'));assert.match(h.container.textContent,/不会自动删除您的理由/);assert.deepEqual(h.draft(),before);await h.click('恢复这处原引用',h.aria(citeLabel));await h.click('舍弃这项对象建议',h.aria('对象建议1'));await h.value('对象建议1的修改理由','对象字段缺少必要依据，先舍弃整项');assert.ok(h.aria('关系建议1'));assert.match(h.container.textContent,/相关关系不会自动舍弃或重连/);assert.equal(h.draft().edits.length,1);assert.equal(h.draft().edits[0].path,'/objects/0');await h.click('恢复这项对象建议',h.aria('对象建议1'));assert.deepEqual(h.draft().edits,[]);}finally{await h.close();}
});
test('V261 actual ReactDOM failed submission retains all edits and a synchronous double click submits only once',async()=>{
 let reject;const h=await scene({submit:()=>new Promise((resolve,rejectPromise)=>{reject=rejectPromise;})});try{await h.click('舍弃这项业务建议',h.aria('业务建议1'));await h.value('业务建议1的修改理由','这项建议没有充分原文，保留说明');const before=h.draft(),button=h.button(submitLabel);await act(async()=>{button.dispatchEvent(new h.window.MouseEvent('click',{bubbles:true}));button.dispatchEvent(new h.window.MouseEvent('click',{bubbles:true}));});assert.equal(h.submitted.length,1);assert.equal(h.button(submitLabel).matches(':disabled'),true);await act(async()=>reject(Error('Synthetic parent submit failure')));assert.match(h.container.textContent,/全部人工输入保留/);assert.deepEqual(h.draft(),before);assert.equal(h.button(submitLabel).matches(':disabled'),false);}finally{await h.close();}
});
test('V261 actual ReactDOM a different source cannot reuse an old controlled draft or disclose a late success',async()=>{
 let finish;const h=await scene({submit:()=>new Promise(resolve=>{finish=resolve;})});try{await h.click('舍弃这项业务建议',h.aria('业务建议1'));await h.value('业务建议1的修改理由','此原建议没有充分依据，请保留说明');const before=h.draft();await h.click(submitLabel);const next=fixture();next.analysis_id='another-synthetic-analysis';next.raw_hash=hash('f');await h.render({source:next});assert.match(h.container.textContent,/草稿属于其他原建议/);assert.equal(h.button(submitLabel).matches(':disabled'),true);await act(async()=>finish());assert.equal(h.container.textContent.includes('草案已交给保存流程'),false);assert.deepEqual(h.draft(),before);assert.equal(h.submitted.length,1);}finally{await h.close();}
});
test('V261 actual full unmount remount restores the parent supplied draft without localStorage or HTTP',async()=>{
 const first=await scene();let retained;try{await first.click('舍弃这项业务建议',first.aria('业务建议1'));await first.value('业务建议1的修改理由','保留尚未完成的人工核对说明，刷新后继续');retained=first.draft();assert.equal(first.submitted.length,0);}finally{await first.close();}
 // This exercises the controlled restoration contract, not an actual durable
 // parent/server store. Root must separately verify that owner draft service.
 const second=await scene({initialDraft:retained});try{assert.equal(second.aria('业务建议1的修改理由').value,retained.edits[0].reason);assert.ok(second.button('恢复这项业务建议',second.aria('业务建议1')));assert.equal(second.submitted.length,0);assert.equal(second.button(submitLabel).matches(':disabled'),false);assert.deepEqual(second.draft(),retained);}finally{await second.close();}
});
