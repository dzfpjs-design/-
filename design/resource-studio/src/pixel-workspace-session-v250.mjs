import {json,api as binary} from './runtime-api.js';
import {pixelNeed as need,pixelSHA} from './pixel-change-v249-protocol.mjs';
// The same scope is checked before/after transport and binary decoding. An old
// response cannot authorize display or a new write after a workspace switch.
export class PixelWorkspaceSessionV250{
 constructor({account,session=globalThis.sessionStorage,request=json,readBinary=binary,current=()=>true,onDenied=()=>{}}){Object.assign(this,{account,session,request,readBinary,current,onDenied});this.token=session.getItem('ga-setup-csrf');}
 check(){need(this.current(),'页面范围已变化；原输入与原操作保留。');if(!this.token||this.session.getItem('ga-setup-csrf')!==this.token||this.session.getItem('ga-setup-csrf-user')!==this.account.user_id||this.session.getItem('ga-setup-csrf-workspace')!==this.account.workspace_id){this.onDenied();throw Error('登录范围已变化，原记录已隐藏并保留。');}}
 headers(){return {'X-Workspace-ID':this.account.workspace_id,'X-CSRF-Token':this.token};}
 async identity(){this.check();try{const value=await this.request('/me',{cache:'no-store',headers:this.headers()});this.check();need(value.user_id===this.account.user_id&&value.workspace_id===this.account.workspace_id&&value.role===this.account.role,'当前账号、工作区或权限已变化，原记录保留。');return value;}catch(e){if([401,403].includes(e.status)||/权限已变化/.test(e.message))this.onDenied();throw e;}}
 async read(path,options={}){await this.identity();try{const value=await this.request(path,{...options,cache:'no-store',headers:{...options.headers,...this.headers()}});this.check();await this.identity();return value;}catch(e){if([401,403].includes(e.status))this.onDenied();throw e;}}
 async bytes(path,ref){await this.identity();const response=await this.readBinary(path,{cache:'no-store',headers:this.headers()});this.check();need(response.headers.get('X-Content-SHA256')===ref.sha256&&response.headers.get('Content-Type')?.split(';')[0]===ref.media_type,'原件或结果文件的来源声明不匹配，尚未作为核对依据。');const raw=await response.arrayBuffer();this.check();need(raw.byteLength===ref.size_bytes&&await pixelSHA(raw)===ref.sha256,'原件或结果内容与保存记录不一致，未显示为核对依据。');await this.identity();return raw;}
}
