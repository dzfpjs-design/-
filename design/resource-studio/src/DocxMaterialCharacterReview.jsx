import React,{useState} from 'react';
import DocxVisibleTextComparison from './DocxVisibleTextComparison';

function Review({account,material,requestJSON,onSessionInvalid,Btn}){
 const [open,setOpen]=useState(false);
 async function identity(){
  const value=await requestJSON('/me',{headers:{'X-Workspace-ID':account.workspace_id},cache:'no-store'});
  if(value?.user_id!==account.user_id||value?.workspace_id!==account.workspace_id||value?.role!==account.role||
      (typeof account.username==='string'&&value?.username!==account.username)){
   const error=Error('账号或工作区已变化，请重新核对当前会话。');error.code='SESSION_SCOPE_CHANGED';throw error;
  }
 }
 async function read(path,options){await identity();if(options?.signal?.aborted)throw Object.assign(Error('已停止核对。'),{name:'AbortError'});return requestJSON(path,{...options,headers:{...(options?.headers||{}),'X-Workspace-ID':account.workspace_id}});}
 return <details onToggle={event=>setOpen(event.currentTarget.open)}><summary>核对 Word 原文特殊字符</summary>
  {open&&<DocxVisibleTextComparison account={account} material={material} row={null} request={read} identity={identity} Btn={Btn} onSessionInvalid={onSessionInvalid}/>}
 </details>;
}

export default function DocxMaterialCharacterReview({account,material,requestJSON,onSessionInvalid,Btn='button'}){
 if(!account?.user_id||!account?.workspace_id||!['admin','operator'].includes(account.role)||
    !material?.id||!material?.hash||!Number.isSafeInteger(material.version)||material.version<1||
    material.archived||typeof requestJSON!=='function')return null;
 const scope=JSON.stringify([account.user_id,account.workspace_id,account.username,account.role,material.id,material.version,material.hash]);
 return <Review key={scope} {...{account,material,requestJSON,onSessionInvalid,Btn}}/>;
}
