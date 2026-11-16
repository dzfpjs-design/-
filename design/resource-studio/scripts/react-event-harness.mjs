import assert from 'node:assert/strict';
import React from 'react';
import {JSDOM,ResourceLoader} from 'jsdom';
// Browser dependencies for the event/effect harness below. This does not turn
// the manual hook harness into React DOM or browser acceptance evidence.
export function installEventBrowser({account,storage}){
 class NoResources extends ResourceLoader{fetch(){throw Error('External resources forbidden in event harness');}}
 const dom=new JSDOM('<!doctype html><html><body></body></html>',{url:'https://react-event.invalid/',pretendToBeVisual:true,resources:new NoResources()}),tab=dom.window.sessionStorage;
 tab.setItem('ga-setup-csrf','synthetic-event-csrf');tab.setItem('ga-setup-csrf-user',account.user_id);tab.setItem('ga-setup-csrf-workspace',account.workspace_id);tab.setItem('ga-setup-csrf-username',account.username);
 const tails=new Map(),locks={request(name,options,callback){assert.equal(options.mode,'exclusive');const previous=tails.get(name)||Promise.resolve(),next=previous.then(callback);tails.set(name,next.catch(()=>{}));return next;}};
 Object.defineProperty(dom.window.navigator,'locks',{configurable:true,value:locks});Object.defineProperty(dom.window,'localStorage',{configurable:true,value:storage});
 const values={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,localStorage:storage,sessionStorage:tab},previous=Object.fromEntries(Object.keys(values).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 for(const [key,value]of Object.entries(values))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});
 return {window:dom.window,document:dom.window.document,sessionStorage:tab,restore(){dom.window.close();for(const [key,descriptor]of Object.entries(previous)){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}}};
}
// Execute actual component handlers/effects through a manual hook dispatcher.
// This is not a React DOM mount or a real browser.
export function mount(Component,props){
 const hooks=[],effects=[];let index=0,tree,disposed=false;
 const dispatcher={useState(initial){const slot=index++;if(!(slot in hooks))hooks[slot]=typeof initial==='function'?initial():initial;return [hooks[slot],value=>{if(!disposed)hooks[slot]=typeof value==='function'?value(hooks[slot]):value;}];},useRef(initial){const slot=index++;if(!(slot in hooks))hooks[slot]={current:initial};return hooks[slot];},useCallback(callback,deps){const slot=index++,old=hooks[slot];assert.ok(deps==null||Array.isArray(deps),'useCallback dependencies must be an array when provided');if(old&&Array.isArray(deps)&&Array.isArray(old.deps)&&old.deps.length===deps.length&&deps.every((value,i)=>Object.is(value,old.deps[i])))return old.callback;hooks[slot]={callback,deps:deps==null?null:[...deps]};return callback;},useEffect(effect,deps){const slot=index++,old=hooks[slot];if(!old||!deps||deps.some((value,i)=>value!==old.deps?.[i])){hooks[slot]={deps,cleanup:old?.cleanup};effects.push(()=>{hooks[slot].cleanup?.();hooks[slot].cleanup=effect();});}}};
 function render(){index=0;const internals=React.__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE,previous=internals.H;internals.H=dispatcher;try{tree=Component(props);}finally{internals.H=previous;}while(effects.length)effects.shift()();return tree;}
 function nodes(value,result=[]){if(Array.isArray(value))for(const item of value)nodes(item,result);else if(value&&typeof value==='object'&&value.props){result.push(value);nodes(value.props.children,result);}return result;}
 const label=value=>Array.isArray(value)?value.map(label).join(''):value&&typeof value==='object'?label(value.props?.children):['string','number'].includes(typeof value)?String(value):'';
 function byAria(name){const result=nodes(tree).find(node=>node.props['aria-label']===name);assert.ok(result,'control: '+name);return result;}
 function button(name){const result=nodes(tree).find(node=>typeof node.props.onClick==='function'&&label(node.props.children)===name);assert.ok(result,'button: '+name);return result;}
 async function flush(){for(let i=0;i<5;i++){await new Promise(resolve=>setImmediate(resolve));render();}}
 render();return {render,flush,byAria,button,async input(name,value){byAria(name).props.onChange({target:{value,checked:!!value}});render();},async click(name){await button(name).props.onClick();await flush();},unmount(){if(disposed)return;disposed=true;for(const value of hooks)value?.cleanup?.();}};
}
