import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToString} from 'react-dom/server';
import {build} from 'vite';
import {mkdtemp,mkdir,writeFile,rm} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
import {resolve} from 'node:path';
import react from '@vitejs/plugin-react';

test('real understanding component SSR renders the full v3 range, legacy range and paid recovery states truthfully',async()=>{
 const previous=globalThis.sessionStorage;globalThis.sessionStorage={getItem:()=>null,setItem(){},removeItem(){}};
 await mkdir(resolve('.m62-validation'),{recursive:true});const dir=await mkdtemp(resolve('.m62-validation/.understanding-render-regression-'));
 try{
  const output=await build({configFile:false,plugins:[react()],logLevel:'silent',build:{ssr:resolve('src/UnderstandingDialog.jsx'),write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true}}}});
  await writeFile(resolve(dir,'component.mjs'),output.output.find(item=>item.type==='chunk'&&item.isEntry).code);
  const {default:UnderstandingDialog,UnderstandingSendingScope,CitationNormalizationNotice,ModelExplanation}=await import(pathToFileURL(resolve(dir,'component.mjs')).href);
  const explanation=renderToString(React.createElement(ModelExplanation,{text:'模型原补充说明 b227',hasCitation:true}));
  assert.match(explanation,/<details/);assert.doesNotMatch(explanation,/<details[^>]* open/);assert.match(explanation,/待核对/);assert.match(explanation,/未逐句核验/);assert.match(explanation,/模型原补充说明 b227/);
  const inferred=renderToString(React.createElement(ModelExplanation,{text:'尚无直接原文',hasCitation:false}));assert.match(inferred,/缺少直接原文引用/);
  const normalizations=[{material_id:'private-material-id',block_id:'private-block-id',original_quote:'原文开头……原文结尾',expanded_quote:'原文开头，中间保留人工核对限制，原文结尾'}];
  const notice=renderToString(React.createElement(CitationNormalizationNotice,{entries:normalizations}));
  assert.match(notice,/原回答仍保留/);assert.match(notice,/查看引用展开记录/);assert.match(notice,/原文开头……原文结尾/);assert.match(notice,/中间保留人工核对限制/);assert.doesNotMatch(notice,/private-material-id|private-block-id/);
  assert.equal(renderToString(React.createElement(CitationNormalizationNotice,{entries:[]})),'');
  const blocks=Array.from({length:31},(_,i)=>({material_id:'material-internal-id',block_id:'internal-block-'+i,text:'完整发送片段'+i,location:i===0?{}:{page:i%2+1}}));
  const preview={body:{contract_version:3},provider:'qwen',model:'text',materials:[{id:'material-internal-id',name:'完整核查资料'}],chunks:[blocks],context:{profile:{name:'配置',domain:'核查',goal:'报告优先'},businesses:[],flows:{},object_names:['已登记对象']},instruction:'按完整资料核对',input_summary:{material_count:1,block_count:31,prepared_messages_bytes:21000,max_input_bytes:50000},source_snapshot:{private:'OCR_PRIVATE_SERVER_ONLY'},generation_contract:{version:'setup-grounded-v3',task:'禁止扩大资料明确的范围',dispatch_options:{model:'text',max_tokens:6000}}};
  const html=renderToString(React.createElement(UnderstandingSendingScope,{preview}));
  for(let i=0;i<31;i++)assert.ok(html.includes('完整发送片段'+i));assert.match(html,/1 次云请求/);assert.match(html,/21,000/);assert.match(html,/原图、识别运行与校验信息/);assert.match(html,/未记录页码或正文位置/);assert.match(html,/第2页/);assert.doesNotMatch(html,/internal-block-|OCR_PRIVATE_SERVER_ONLY|material-internal-id/);
  const v4=renderToString(React.createElement(UnderstandingSendingScope,{preview:{...preview,body:{contract_version:4},input_summary:{...preview.input_summary,prepared_messages_bytes:180000,max_input_bytes:900000,token_bound:187024},generation_contract:{...preview.generation_contract,version:'setup-grounded-v4',input_policy:{transport_timeout_seconds:180},dispatch_options:{model:'qwen-plus',max_tokens:6000,enable_thinking:false}}}}));
  assert.match(v4,/180,000/);assert.match(v4,/900,000/);assert.match(v4,/187,024/);assert.match(v4,/网络等待超时：180秒/);assert.match(v4,/思考模式：关闭/);assert.match(v4,/不是实际收费量/);assert.doesNotMatch(v4,/传输等待上限：60秒|批云请求/);
  const citation=quote=>({material_id:'context-private-material',block_id:'context-private-block',quote});
  const existingFlow={trigger:'人工既定启动',output:'人工既定产出',citations:[citation('业务依据保留B')],inference_reason:'业务既有推断待核对',omitted_citation_count:2,steps:[{name:'人工原步骤',purpose:'按原顺序核查',agent:'人工确认',role:'原负责人',output:'核查说明',citations:[citation('步骤依据保留A')],inference_reason:'步骤既有推断待核对',omitted_citation_count:1,next_label:'结束流程'}]};
  const existingContext={...preview.context,businesses:['已有业务'],flows:{'已有业务':existingFlow}};
  const omittedNotice='已有原文依据未发送：原资料未选择或引用已变化，请补选资料或重新核对';
  for(const version of [4,5]){
   const contextHTML=renderToString(React.createElement(UnderstandingSendingScope,{preview:{...preview,body:{contract_version:version},context:existingContext}}));
   for(const text of ['业务依据保留B','业务既有推断待核对','步骤依据保留A','步骤既有推断待核对','人工原步骤','原负责人'])assert.ok(contextHTML.includes(text));
   assert.equal(contextHTML.split(omittedNotice).length-1,2);assert.match(contextHTML,/查看业务依据与推断/);assert.match(contextHTML,/未逐句核验/);
   assert.doesNotMatch(contextHTML,/context-private-material|context-private-block/);
  }
  const legacyFlow={trigger:'旧启动',output:'旧产出',steps:[{name:'旧步骤',agent:'人工确认',role:'旧负责人',citation:'旧版文字依据',next_label:'结束流程'}]};
  const legacyContextHTML=renderToString(React.createElement(UnderstandingSendingScope,{preview:{...preview,context:{...preview.context,businesses:['旧业务'],flows:{'旧业务':legacyFlow}}}}));
  assert.match(legacyContextHTML,/旧版文字依据/);assert.doesNotMatch(legacyContextHTML,/已有原文依据未发送|查看业务依据与推断/);
  const omittedOnlyHTML=renderToString(React.createElement(UnderstandingSendingScope,{preview:{...preview,body:{contract_version:5},context:{...preview.context,businesses:['未发依据'],flows:{'未发依据':{trigger:'原启动',output:'原产出',citations:[],inference_reason:'',omitted_citation_count:1,steps:[]}}}}}));
  assert.match(omittedOnlyHTML,/已有原文依据未发送/);assert.doesNotMatch(omittedOnlyHTML,/查看业务依据与推断|缺少直接原文引用/);
  const legacy=renderToString(React.createElement(UnderstandingSendingScope,{preview:{...preview,body:{contract_version:2},chunks:[blocks.slice(0,15),blocks.slice(15)],generation_contract:null}}));
  assert.match(legacy,/原第 .*2.* 版授权/);assert.match(legacy,/批云请求/);assert.doesNotMatch(legacy,/不分批累计建议/);
  const Dialog=({children,title})=>React.createElement('section',{},title,children),Btn=({children,disabled})=>React.createElement('button',{disabled},children),Field=({children,label})=>React.createElement('label',{},label,children);
  const props={Dialog,Btn,Field,materials:[],workspace:{instanceProfile:{domain:'核查'},instanceObjects:[]},onClose(){},onApplied(){},prepareDraft(){}};
  const reviewRecord={id:'context-review',contract_version:4,status:'review',provider:'qwen',model:'text',source_revision:2,completed_batches:1,total_batches:1,materials:[],before_context:existingContext,result:{domain:'核查',objects:[],questions:[],businesses:[{operation:'upsert',original_name:'已有业务',name:'已有业务',trigger:'人工既定启动',output:'人工既定产出',citations:[],inference_reason:'候选业务说明',steps:[{key:'one',name:'候选步骤',purpose:'核查',agent:'人工确认',role:'新负责人',output:'核查说明',citations:[citation('候选步骤依据')],inference_reason:'候选步骤说明',next_key:'end',condition:'',else_key:'end'}]}]}};
  const reviewHTML=renderToString(React.createElement(UnderstandingDialog,{...props,account:{workspace_id:'w',username:'p',role:'operator'},initialRecord:reviewRecord}));
  for(const text of ['当前已保存流程','业务依据保留B','业务既有推断待核对','步骤依据保留A','步骤既有推断待核对','候选步骤依据','候选步骤说明'])assert.ok(reviewHTML.includes(text));
  assert.equal(reviewHTML.split(omittedNotice).length-1,2);assert.match(reviewHTML,/应用选中的建议/);
  const originalMaterial={id:'original',name:'原报告.docx',status:'parsed',ocr:{coverage_status:'blocked',reason:'脚注覆盖待核对'}},confirmedPDF={id:'copy',name:'已确认副本.pdf',status:'parsed',document_copy:{source_material_id:'original'},ocr:{coverage_status:'complete'}};
  const scope=renderToString(React.createElement(UnderstandingDialog,{...props,account:{workspace_id:'w',username:'p',role:'operator'},materials:[originalMaterial,confirmedPDF]}));
  const labels=scope.match(/<label[^>]*class="understanding-choice"[\s\S]*?<\/label>/g)||[];
  const blocked=labels.find(value=>value.includes('原报告.docx')),ready=labels.find(value=>value.includes('已确认副本.pdf'));
  assert.match(blocked,/disabled=""/);assert.doesNotMatch(blocked,/checked=""/);assert.match(blocked,/覆盖存在缺口/);assert.match(blocked,/脚注覆盖待核对/);assert.match(ready,/checked=""/);assert.doesNotMatch(ready,/disabled=""/);assert.match(ready,/PDF 副本文字已全部核对/);assert.match(scope,/已逐页确认的 PDF 副本进行后续理解/);
  const row={id:'record',contract_version:3,status:'failed',provider:'qwen',model:'text',source_revision:2,completed_batches:0,total_batches:1,materials:[],result:null,input_summary:preview.input_summary};
  const unknown=renderToString(React.createElement(UnderstandingDialog,{...props,account:{workspace_id:'w',username:'p',role:'operator'},initialRecord:{...row,recovery:{mode:'new_preview_required',reason:'响应未保存，可能计费'}}}));
  assert.match(unknown,/旧授权再次发送/);assert.match(unknown,/核对范围并申请新的理解/);assert.doesNotMatch(unknown,/核对后重试未完成部分|用已保存原答重新核对/);
  const paid=renderToString(React.createElement(UnderstandingDialog,{...props,account:{workspace_id:'w',username:'p',role:'operator'},initialRecord:{...row,recovery:{mode:'local_only',reason:'保留原答，仅本地重新核对'}}}));
  assert.match(paid,/用已保存原答重新核对/);assert.doesNotMatch(paid,/核对后重试未完成部分/);
  const viewer=renderToString(React.createElement(UnderstandingDialog,{...props,account:{workspace_id:'w',username:'v',role:'viewer'},initialRecord:{...row,recovery:{mode:'local_only',reason:'仅本地'}}}));assert.match(viewer,/当前账号只读/);assert.match(viewer,/<button disabled="">用已保存原答重新核对/);
  for(const version of [1,2]){const saved=renderToString(React.createElement(UnderstandingDialog,{...props,initialRecord:{...row,contract_version:version}}));assert.match(saved,/核对后重试未完成部分/);assert.doesNotMatch(saved,/第3版完整资料理解/);}
 }finally{await rm(dir,{recursive:true,force:true});if(previous===undefined)delete globalThis.sessionStorage;else globalThis.sessionStorage=previous;}
});
