const validCsrf=value=>typeof value==='string'&&value.length>=20&&value.length<=512&&!/[^\x21-\x7e]/.test(value);
export const SESSION_FIELDS=['user_id','username','workspace_id','role'];
function sessionError(message,code='SESSION_SCOPE_CHANGED'){const error=Error(message+' 原草稿和请求保留，未重放写入。');error.code=code;return error;}
export function setupSessionIdentity(user){const identity={};for(const field of SESSION_FIELDS){if(typeof user?.[field]!=='string'||!user[field]||user[field].trim()!==user[field]||/[\r\n]/.test(user[field]))throw sessionError('当前账号身份暂不能核对。','SESSION_INVALID');identity[field]=user[field];}return identity;}
export function sameSetupSession(left,right){return !!left&&!!right&&SESSION_FIELDS.every(field=>left[field]===right[field]);}
export function checkSessionRestore(user,restored){const expected=setupSessionIdentity(user);if(!restored||Object.keys(restored).sort().join(',')!=='csrf_token,role,user_id,username,workspace_id'||!sameSetupSession(expected,restored)||!validCsrf(restored.csrf_token))throw sessionError('恢复回执与当前账号或工作区不一致，未采用凭据。');return expected;}
export async function requestSessionRestore(workspaceID,fetcher=globalThis.fetch){
 let response;try{response=await fetcher('/api/session/restore',{method:'GET',credentials:'same-origin',cache:'no-store',headers:{'X-Session-Restore':'1','X-Workspace-ID':workspaceID}});}catch{throw sessionError('暂时无法核对当前会话，请检查连接后重试。','SESSION_RESTORE_NETWORK');}
 if(!response.ok){let detail;try{detail=(await response.json()).detail;}catch{}const error=Error(typeof detail==='string'?detail:'当前会话暂不能恢复，请核对权限或连接。');error.status=response.status;throw error;}
 try{return await response.json();}catch{throw sessionError('会话恢复响应暂不能核对。','SESSION_INVALID');}
}
export async function prepareSetupSession(user,{storage,isCurrent,commitSession,readIdentity,restore=requestSessionRestore}){
 const expected=setupSessionIdentity(user),keys=['ga-setup-csrf','ga-setup-csrf-workspace','ga-setup-csrf-user','ga-setup-csrf-username'],before=keys.map(key=>storage.getItem(key));
 const active=()=>{if(!isCurrent())throw sessionError('上次会话读取已停止，未采用迟到回执。','SESSION_BOOT_STALE');};
 active();if(before[1]&&before[1]!==expected.workspace_id||before[2]&&before[2]!==expected.user_id||before[3]&&before[3]!==expected.username)throw sessionError('登录账号或工作区已改变，请核对当前会话。');
 if(before[0]&&!validCsrf(before[0]))throw sessionError('本页会话凭据暂不能核对。','SESSION_INVALID');
 if(before[0]&&before[1]===expected.workspace_id&&before[2]===expected.user_id&&before[3]===expected.username){commitSession(before[0],expected.workspace_id,expected.user_id,expected.username);return expected;}
 const restored=await restore(expected.workspace_id);active();checkSessionRestore(expected,restored);
 const latest=setupSessionIdentity(await readIdentity(expected.workspace_id));active();
 if(!sameSetupSession(expected,latest)||keys.some((key,index)=>storage.getItem(key)!==before[index]))throw sessionError('读取期间账号或页面凭据发生变化，未采用迟到回执。');
 commitSession(restored.csrf_token,expected.workspace_id,expected.user_id,expected.username);return expected;
}
