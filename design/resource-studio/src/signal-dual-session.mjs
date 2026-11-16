import {useEffect,useRef} from 'react';
import {scopedPreparationRequester} from './understanding-preparation-contract.mjs';
import {dualScope} from './signal-dual-contract.mjs';

// Every read, proof hash, and final send retains the initiating UI scope. The
// requester also captures the real session tags/CSRF rather than global state.
export function useDualSession({account,instanceId,releaseID='',enabled=true,request,tabStorage=globalThis.sessionStorage}){
 const scope=dualScope(account,instanceId),key=JSON.stringify([account?.user_id,account?.workspace_id,account?.role,instanceId,releaseID,enabled]);
 const current=useRef(key),life=useRef(0),context=useRef(null);current.current=key;
 if(context.current?.key!==key)context.current={key,send:scope?scopedPreparationRequester(account,request,tabStorage):null};
 useEffect(()=>()=>{life.current++;},[key]);
 const writable=!!scope&&enabled&&['admin','operator'].includes(account?.role);
 function snapshot(){const epoch=life.current,original=key,send=context.current?.send;return {send,check(){if(epoch!==life.current||original!==current.current||!send)throw Error('账号、工作区或实例已变化，原输入和请求仍保留。');}};}
 async function identity(snap,write=false){const me=await snap.send('/me');snap.check();if(me.user_id!==scope.actor_id||me.workspace_id!==scope.workspace_id||me.role!==account.role||write&&!['admin','operator'].includes(me.role))throw Error('当前会话或权限已变化，未提交新操作。');}
 return {scope,key,writable,snapshot,identity};
}
