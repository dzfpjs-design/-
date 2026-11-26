import {sameSetupSession,setupSessionIdentity} from './setup-session-contract.mjs';

// Every async boundary checks the original generation and session. No credential
// or workspace binding changes until the cookie session confirms membership.
export async function verifyWorkspaceSwitch({account,target,storage,isCurrent,guard,waitDraft,readIdentity,lock=()=>{}}) {
 const original=setupSessionIdentity(account);
 const keys=['ga-setup-csrf','ga-setup-csrf-workspace','ga-setup-csrf-user','ga-setup-csrf-username'];
 const before=keys.map(key=>storage.getItem(key));
 const active=()=>{if(!isCurrent()||keys.some((key,index)=>storage.getItem(key)!==before[index]))throw Error('当前会话已变化，请重新核对。');};
 if(!before[0]||before[1]!==original.workspace_id||before[2]!==original.user_id||before[3]!==original.username)throw Error('当前会话需要重新核对。');
 if(!Array.isArray(account.workspaces)||!account.workspaces.some(w=>w.id===target)||target===original.workspace_id)throw Error('请选择可访问的工作区。');
 active();if(guard&&!await guard())return null;active();await waitDraft();active();lock();active();
 const current=await readIdentity(original.workspace_id);active();
 if(!sameSetupSession(original,current)||!Array.isArray(current.workspaces)||!current.workspaces.some(w=>w.id===target))throw Error('工作区权限已变化，请重新核对。');
 const selected=await readIdentity(target);active();
 if(selected?.user_id!==original.user_id||selected?.username!==original.username||selected?.workspace_id!==target||!Array.isArray(selected.workspaces)||!selected.workspaces.some(w=>w.id===target&&w.role===selected.role))throw Error('目标工作区身份无法核对。');
 if(guard&&!await guard())return null;active();await waitDraft();active();
 return {token:before[0],identity:setupSessionIdentity(selected)};
}
