// Offline verification of an already-read API DTO. Never calls the API/provider.
// Run with NODE_ENV=development --input <JSON> --output <private summary JSON>.
import {readFile,writeFile,mkdtemp,rename,rm,mkdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {resolve,dirname,join} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {isDeepStrictEqual} from 'node:util';

const project=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const pointer=parts=>'/'+parts.map(part=>String(part).replaceAll('~','~0').replaceAll('/','~1')).join('/');
const check=(condition,code)=>{if(!condition){const error=new Error(code);error.checkCode=code;throw error;}};
const phaseLabels={steps:'依次完成步骤',decision:'条件分支',parallel:'并行协作',repeat:'循环核查'};

function inventory(value,flowIndex){
 const flow=value.business.flows[flowIndex],base=['business','flows',flowIndex],actions=[],phases=[],branches=[],citations=[],fields=[];
 const addField=(path,value)=>{check(typeof value==='string','dto_field_not_string');fields.push({path:pointer(path),value});};
 const attribution=(value,path)=>{check(object(value)&&Array.isArray(value.citations),'dto_attribution_invalid');citations.push({path:pointer([...path,'citations']),refs:value.citations});};
 const action=(value,path)=>{
  check(object(value)&&Array.isArray(value.inputs)&&Array.isArray(value.outputs),'dto_action_invalid');
  actions.push({path:pointer(path),actor:value.actor,inputs:value.inputs.map((item,index)=>({path:pointer([...path,'inputs',index]),value:item})),outputs:value.outputs.map((item,index)=>({path:pointer([...path,'outputs',index]),value:item}))});
  for(const key of ['name','role','responsibility'])addField([...path,key],value[key]);
  check(typeof value.actor==='string','dto_actor_invalid');attribution(value.attribution,[...path,'attribution']);
 };
 function visit(values,path,depth,parentBranch=null){
  check(Array.isArray(values),'dto_phases_invalid');
  values.forEach((phase,index)=>{
   check(object(phase)&&Object.hasOwn(phaseLabels,phase.kind),'dto_phase_kind_invalid');
   const at=[...path,index],nextDepth=depth+(phase.kind==='steps'?0:1);
   check(nextDepth<=3,'dto_control_depth_exceeded');
   phases.push({path:pointer(at),kind:phase.kind,depth:nextDepth,parentBranch,waitMode:phase.wait_mode});attribution(phase.attribution,[...at,'attribution']);
   if(phase.kind==='steps'||phase.kind==='repeat'){
    check(Array.isArray(phase.steps),'dto_steps_invalid');phase.steps.forEach((step,index)=>action(step,[...at,'steps',index]));
    if(phase.kind==='repeat'){action(phase.check,[...at,'check']);addField([...at,'repeat_if'],phase.repeat_if);addField([...at,'finish_if'],phase.finish_if);}
   }else{
    action(phase[phase.kind==='decision'?'decision':'dispatch'],[...at,phase.kind==='decision'?'decision':'dispatch']);
    check(Array.isArray(phase.branches),'dto_branches_invalid');
    phase.branches.forEach((branch,index)=>{
     check(object(branch)&&Array.isArray(branch.phases),'dto_branch_invalid');const bp=[...at,'branches',index];
     branches.push({path:pointer(bp),parent:pointer(at),empty:branch.phases.length===0});
     if(phase.kind==='decision'){addField([...bp,'condition'],branch.condition);attribution(branch.attribution,[...bp,'attribution']);}
     visit(branch.phases,[...bp,'phases'],nextDepth,pointer(bp));
    });
    if(phase.kind==='parallel'){check(['all','any'].includes(phase.wait_mode),'dto_wait_mode_invalid');attribution(phase.join_attribution,[...at,'join_attribution']);}
   }
  });
 }
 check(object(flow)&&object(flow.trigger),'dto_flow_invalid');addField([...base,'name'],flow.name);addField([...base,'trigger','description'],flow.trigger.description);
 attribution(flow.trigger.attribution,[...base,'trigger','attribution']);attribution(flow.phase_order,[...base,'phase_order']);
 visit(flow.phases,[...base,'phases'],0);action(flow.completion,[...base,'completion']);
 check(Array.isArray(value.business.questions),'dto_questions_invalid');value.business.questions.forEach((question,index)=>{
  check(object(question)&&Array.isArray(question.citations),'dto_question_invalid');addField(['business','questions',index,'question'],question.question);addField(['business','questions',index,'reason'],question.reason);citations.push({path:pointer(['business','questions',index,'citations']),refs:question.citations});
 });
 return {flow,actions,phases,branches,citations,fields};
}

async function verify(input){
 check(process.env.NODE_ENV==='development','development_runtime_required');
 const parsed=JSON.parse(await readFile(input,'utf8')),view=object(parsed?.readback)?parsed.readback:parsed;
 check(object(view)&&object(view.value)&&view.value.schema==='report-business-flow-nested-plan-v192'&&object(view.value.business)&&Array.isArray(view.value.business.flows),'nested_api_dto_required');
 check(object(view.baseline_value),'original_baseline_required');
 const sources=view.diagnostics?.source_evidence;check(Array.isArray(sources)&&sources.every(source=>object(source)&&typeof source.span_ref==='string'&&typeof source.source_text==='string'),'actual_source_catalog_required');
 const recovered=view.source_binding?.schema==='business-flow-nested-component-review-source-v194'||view.generation_recovery?.schema==='business-flow-nested-component-view-v194';
 if(recovered){check(view.source_binding?.value_schema==='report-business-flow-nested-plan-v192'&&view.source_binding?.generation_profile==='flow-v193','recovered_source_binding_invalid');if(view.generation_recovery)check(view.generation_recovery.schema==='business-flow-nested-component-view-v194'&&view.generation_recovery.component_path==='/plan'&&view.generation_recovery.component_only===true&&view.generation_recovery.original_protocol_valid===false&&Array.isArray(view.generation_recovery.issues),'recovered_component_contract_invalid');}
 const original=JSON.stringify(view),currentChanged=!isDeepStrictEqual(view.value,view.baseline_value);
 const React=await import('react'),{act}=React,{JSDOM,ResourceLoader,VirtualConsole}=await import('jsdom'),{build}=await import('vite');
 const errors=[],network=[],virtualConsole=new VirtualConsole();virtualConsole.on('jsdomError',error=>errors.push(error));
 const forbid=()=>{network.push(true);throw Error('offline_network_forbidden');};
 class NoResources extends ResourceLoader{fetch(){return forbid();}}
 const dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',{url:'https://offline-nested-verification.invalid/',resources:new NoResources(),virtualConsole});
 dom.window.fetch=forbid;dom.window.XMLHttpRequest=class{constructor(){forbid();}};dom.window.WebSocket=class{constructor(){forbid();}};
 const globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,Element:dom.window.Element,Node:dom.window.Node,Event:dom.window.Event,MouseEvent:dom.window.MouseEvent,fetch:forbid,XMLHttpRequest:dom.window.XMLHttpRequest,WebSocket:dom.window.WebSocket,IS_REACT_ACT_ENVIRONMENT:true};
 const prior=new Map(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));for(const[key,value]of Object.entries(globals))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});
 let directory,root;const perFlow=[],renderedInputPaths=[];
 try{
  directory=await mkdtemp(join(tmpdir(),'nested-api-dom-'));
  const bundle=await build({root:project,configFile:false,envDir:directory,cacheDir:join(directory,'cache'),logLevel:'silent',mode:'test',define:{'process.env.NODE_ENV':JSON.stringify('development')},build:{ssr:join(project,'src/NestedFlowReview.jsx'),write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:name=>name==='react'?pathToFileURL(join(project,'node_modules/react/index.js')).href:name}}}});
  const entry=bundle.output.find(item=>item.type==='chunk'&&item.isEntry);check(!!entry,'jsx_bundle_missing');const modulePath=join(directory,'nested-api-dom.mjs');await writeFile(modulePath,entry.code,{mode:0o600});
  const Component=(await import(pathToFileURL(modulePath).href)).default,{createRoot}=await import('react-dom/client'),container=dom.window.document.getElementById('root');
  for(let flowIndex=0;flowIndex<view.value.business.flows.length;flowIndex++){
   const expected=inventory(view.value,flowIndex);root=createRoot(container,{onCaughtError:error=>errors.push(error),onUncaughtError:error=>errors.push(error),onRecoverableError:error=>errors.push(error)});
   await act(async()=>{root.render(React.createElement(React.StrictMode,null,React.createElement(Component,{value:view.value,flowIndex,sources,generationRecovery:recovered?view.generation_recovery:undefined,generationCausality:recovered?undefined:view.generation_causality,currentChanged,onChange:()=>check(false,'unexpected_edit'),onCitation:()=>check(false,'unexpected_citation_action')})));});
   check(errors.length===0,'react_dom_error');
   const pathNodes=[...container.querySelectorAll('[data-review-path]')],at=path=>{const matches=pathNodes.filter(element=>element.dataset.reviewPath===path);check(matches.length===1,'rendered_path_missing_or_duplicate');return matches[0];};
   const actualField=(path,value)=>{check(typeof value==='string','dto_port_not_string');const control=at(path).querySelector('input,textarea');check(!!control&&control.value===value,'rendered_field_value_mismatch');};
   expected.fields.forEach(({path,value})=>actualField(path,value));
   for(const action of expected.actions){
    const element=at(action.path);check(element.tagName==='SECTION','action_hierarchy_missing');const actorLabel=[...element.children].find(child=>child.tagName==='LABEL'&&child.querySelector('span')?.textContent.endsWith('由谁承担职责'));check(actorLabel?.querySelector('select')?.value===action.actor,'rendered_actor_mismatch');
    for(const kind of ['inputs','outputs']){
     const ports=action[kind];ports.forEach(({path,value})=>actualField(path,value));
     const prefix=action.path+'/'+kind+'/',rendered=pathNodes.filter(element=>element.tagName==='LABEL'&&element.dataset.reviewPath.startsWith(prefix)&&/^\d+$/.test(element.dataset.reviewPath.slice(prefix.length)));
     check(rendered.length===ports.length,'port_ordinal_count_mismatch');
    }
   }
   const actionPaths=new Set(expected.actions.map(action=>action.path));check(isDeepStrictEqual(pathNodes.filter(element=>actionPaths.has(element.dataset.reviewPath)).map(element=>element.dataset.reviewPath),expected.actions.map(action=>action.path)),'action_order_or_hierarchy_mismatch');const phasePaths=new Set(expected.phases.map(phase=>phase.path));check(isDeepStrictEqual(pathNodes.filter(element=>phasePaths.has(element.dataset.reviewPath)).map(element=>element.dataset.reviewPath),expected.phases.map(phase=>phase.path)),'phase_order_or_hierarchy_mismatch');
   for(const phase of expected.phases){const element=at(phase.path);check(element.querySelector(':scope > h4')?.textContent.includes(phaseLabels[phase.kind]),'control_kind_missing');if(phase.parentBranch)check(at(phase.parentBranch).contains(element),'recursive_control_flattened');if(phase.kind==='parallel'){const label=[...element.children].find(child=>child.tagName==='LABEL'&&child.querySelector('span')?.textContent.endsWith('汇合规则'));check(label?.querySelector('select')?.value===phase.waitMode,'parallel_wait_rule_mismatch');}}
   for(const branch of expected.branches){const element=at(branch.path);check(at(branch.parent).contains(element),'branch_parent_mismatch');const empty=[...element.children].some(child=>child.tagName==='P'&&child.textContent.startsWith('空分支：'));check(empty===branch.empty,'empty_branch_changed');}
   let resolvedCitationCount=0;
   for(const item of expected.citations){const element=at(item.path),articles=[...element.children].filter(child=>child.tagName==='ARTICLE');check(articles.length===item.refs.length,'citation_ordinal_count_mismatch');item.refs.forEach((ref,index)=>{const source=sources.find(source=>source.span_ref===ref);if(source){check(articles[index].querySelector('p.bfr-source-text')?.textContent===source.source_text,'adjacent_full_source_text_mismatch');resolvedCitationCount++;}});}
   check(container.querySelectorAll('input[type="checkbox"][aria-label*="关联原文"]').length===0,'closed_source_catalog_eagerly_mounted');
   check([...container.querySelectorAll('details')].filter(element=>element.querySelector(':scope > summary')?.textContent==='选择或调整关联原文').every(element=>element.open===false),'source_picker_not_initially_closed');
   const inputPorts=expected.actions.flatMap(action=>action.inputs),issues=recovered?(view.generation_recovery?.issues||[]):[],affected=inputPorts.filter(port=>issues.some(issue=>issue?.path===port.path||typeof issue?.path==='string'&&issue.path.startsWith(port.path+'/')));renderedInputPaths.push(...inputPorts.map(port=>port.path));
   check(container.querySelectorAll('[data-original-recovery-issue]').length===affected.length,'recovery_issue_count_mismatch');for(const port of affected)check(at(port.path).parentElement.querySelectorAll(':scope > [data-original-recovery-issue]').length===1,'known_input_issue_not_at_unique_port');
   if(recovered)check(!container.querySelector('[aria-label="递归步骤资料来源核对"]'),'failed_sidecar_rendered_as_causal_proof');
   check(network.length===0,'offline_network_attempted');check(JSON.stringify(view)===original,'input_dto_mutated');
   perFlow.push({flow_ordinal:flowIndex+1,element_count:container.querySelectorAll('*').length,dom_utf8_bytes:new TextEncoder().encode(container.outerHTML).length,source_count:sources.length,action_count:expected.actions.length,phase_count:expected.phases.length,maximum_control_depth:Math.max(0,...expected.phases.map(phase=>phase.depth)),empty_branch_count:expected.branches.filter(branch=>branch.empty).length,resolved_adjacent_citation_count:resolvedCitationCount,known_input_issue_count:affected.length,closed_catalog_candidate_count:0});
   await act(async()=>root.unmount());root=null;check(errors.length===0,'react_dom_error');
  }
  if(recovered)check((view.generation_recovery?.issues||[]).filter(issue=>/^\/business\/flows\/\d+\/.*\/inputs\/\d+(?:\/|$)/.test(issue?.path||'')).every(issue=>renderedInputPaths.some(path=>issue.path===path||issue.path.startsWith(path+'/'))),'known_input_issue_path_missing');
  return {schema:'nested-api-offline-dom-verification-v1',passed:true,offline_DOM_not_browser:true,no_API_provider_write:true,input_kind:'caller_supplied_saved_API_DTO',recovered_component:recovered,current_draft_changed:currentChanged,source_content_disclosed:false,input_dto_unchanged:true,network_attempts:network.length,flow_count:perFlow.length,flows:perFlow,release_boundary:'offline_render_of_saved_API_DTO_only_no_browser_latency_or_business_quality_acceptance'};
 }finally{
  if(root)await act(async()=>root.unmount());dom.window.close();for(const[key,descriptor]of prior){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}if(directory)await rm(directory,{recursive:true,force:true});
 }
}

async function save(output,value){await mkdir(dirname(output),{recursive:true});const temporary=output+'.tmp-'+process.pid;try{await writeFile(temporary,JSON.stringify(value,null,2)+'\n',{mode:0o600,flag:'wx'});await rename(temporary,output);}finally{await rm(temporary,{force:true});}}
let output;
try{
 const args=process.argv.slice(2),options={};check(args.length===4,'arguments_required');for(let index=0;index<args.length;index+=2){check(['--input','--output'].includes(args[index])&&!Object.hasOwn(options,args[index])&&typeof args[index+1]==='string','invalid_arguments');options[args[index]]=args[index+1];}
 check(options['--input']&&options['--output'],'arguments_required');const input=resolve(options['--input']),destination=resolve(options['--output']);check(input!==destination,'input_output_must_differ');output=destination;
 const summary=await verify(input);await save(output,summary);process.stdout.write(JSON.stringify({passed:true,offline_DOM_not_browser:true,flow_count:summary.flow_count})+'\n');
}catch(error){const code=typeof error?.checkCode==='string'?error.checkCode:'offline_verification_failed';if(output)await save(output,{schema:'nested-api-offline-dom-verification-v1',passed:false,offline_DOM_not_browser:true,no_API_provider_write:true,source_content_disclosed:false,failure_code:code}).catch(()=>{});process.stderr.write('Offline nested verification failed: '+code+'\n');process.exitCode=1;}
