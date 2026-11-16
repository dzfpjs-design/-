import React,{useEffect,useRef,useState} from 'react';
import AuthoredWorkflowSetupV208 from './AuthoredWorkflowSetupV208';
import AuthoredImageWorkflowSetupV243 from './AuthoredImageWorkflowSetupV243';
import AuthoredDecisionWorkflowSetupV286 from './AuthoredDecisionWorkflowSetupV286';
import {scopeKey as documentKey,canonical} from './authored-workflow-v208-protocol.mjs';
import {imageScopeKey} from './authored-image-workflow-v243-protocol.mjs';
import {decisionScopeKey,decisionHasPending} from './authored-decision-workflow-v286-protocol.mjs';
import {goalDataScopeKey,goalDataHasPending} from './authored-goal-data-workflow-v288-protocol.mjs';
const Button=({children,primary,...props})=><button type="button" {...props}>{children}</button>;
export default function AuthoredWorkflowDesignerV243(props){
 const {businessName,account,Btn=Button}=props,scope={actor_id:account.user_id,workspace_id:account.workspace_id,business_name:businessName},key=canonical(scope),prefixes={document:documentKey(scope),image:imageScopeKey(scope),decision:decisionScopeKey(scope),goal_data:goalDataScopeKey(scope)},preference='ga-authored-recipe-mode-v243:'+key,latest=useRef(),child=useRef(null),modes=['document','image','decision','goal_data'];
 function pending(mode){try{return mode==='goal_data'?goalDataHasPending(scope):mode==='decision'?decisionHasPending(scope):globalThis.localStorage.getItem(prefixes[mode]+':pending')!==null;}catch{return true;}}
 function initial(){try{for(const name of modes)if(pending(name))return name;const saved=globalThis.localStorage.getItem(preference);return modes.includes(saved)?saved:'document';}catch{return 'document';}}
 const [choice,setChoice]=useState(()=>({key,mode:initial()})),[error,setError]=useState(''),[,draw]=useState(0),mode=choice.key===key?choice.mode:initial();latest.current={key,props,mode,prefixes};
 function guard(){try{return latest.current.key===key&&!pending(latest.current.mode)&&!!child.current&&child.current()===true;}catch{return false;}}
 function register(fn,previous){if(fn)child.current=fn;else if(!previous||child.current===previous)child.current=null;}
 useEffect(()=>{props.onRegisterGuard?.(guard);const changed=()=>draw(n=>n+1);globalThis.addEventListener?.('storage',changed);const unload=e=>{if(!guard()){e.preventDefault();e.returnValue='';}};globalThis.addEventListener?.('beforeunload',unload);return()=>{latest.current.props.onRegisterGuard?.(null,guard);globalThis.removeEventListener?.('storage',changed);globalThis.removeEventListener?.('beforeunload',unload);};},[key]);
 function select(next){if(next===mode)return;if(!guard()){setError('当前编制稿尚未可靠保存，或原申请结果仍待核对；请先在当前模式恢复，未切换配方。');return;}try{globalThis.localStorage.setItem(preference,next);if(globalThis.localStorage.getItem(preference)!==next)throw Error('配方选择尚未可靠保存。');child.current=null;setError('');setChoice({key,mode:next});}catch(e){setError(e.message+' 原模式与原稿保留。');}}
 const otherPending=modes.some(name=>name!==mode&&pending(name)),canMutate=()=>!modes.some(name=>name!==mode&&pending(name));
 return <section aria-label="人工编制流程配方"><div role="group" aria-label="选择人工编制配方"><Btn aria-pressed={mode==='document'} onClick={()=>select('document')}>文档资料处理</Btn><Btn aria-pressed={mode==='image'} onClick={()=>select('image')}>图像原件技术复核</Btn><Btn aria-pressed={mode==='decision'} onClick={()=>select('decision')}>业务建议与补充观测</Btn><Btn aria-pressed={mode==='goal_data'} onClick={()=>select('goal_data')}>先准备资料再分析</Btn></div><p>在当前业务内选择明确支持的人工编制配方。各模式原稿和原申请分别保留；切换不会采用、生成实例或启用自动处理。</p>{error&&<p role="alert">{error}</p>}{otherPending&&<p role="alert">另一配方还有原申请待核对。当前稿可靠保存后可切换过去恢复；未结清前暂不在此模式提交新的设计。</p>}
 {mode==='goal_data'?<AuthoredDecisionWorkflowSetupV286 key={key+':goal_data'} {...props} profile="v288" onRegisterGuard={register} canMutate={canMutate} onLocalStateChange={()=>draw(n=>n+1)}/>:mode==='decision'?<AuthoredDecisionWorkflowSetupV286 key={key+':decision'} {...props} profile="v286" onRegisterGuard={register} canMutate={canMutate} onLocalStateChange={()=>draw(n=>n+1)}/>:mode==='image'?<AuthoredImageWorkflowSetupV243 key={key+':image'} {...props} onRegisterGuard={register} canMutate={canMutate} onLocalStateChange={()=>draw(n=>n+1)}/>:<fieldset style={{border:0,padding:0,margin:0}} disabled={otherPending&&!pending('document')}><AuthoredWorkflowSetupV208 key={key+':document'} {...props} onRegisterGuard={register}/></fieldset>}
 </section>;
}
