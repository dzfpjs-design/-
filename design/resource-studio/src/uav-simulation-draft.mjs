/** Account-scoped, recoverable single-editor draft; locks fail closed. */
export const emptyFields=()=>({event:'',startLon:'',startLat:'',endLon:'',endLat:''});
export function readDraft(storage,key){
  const raw=storage.getItem(key);
  if(!raw)return {version:1,fields:emptyFields(),pending:null};
  const data=JSON.parse(raw);
  if(data?.version!==1||!data.fields||Object.keys(emptyFields()).some(k=>typeof data.fields[k]!=='string')||
      (data.pending!==null&&(!data.pending||typeof data.pending.request_id!=='string')))throw Error('草稿无法核对，请保留本机记录并联系管理员。');
  return data;
}
export function writeDraft(storage,key,value){
  const text=JSON.stringify(value);storage.setItem(key,text);
  if(storage.getItem(key)!==text)throw Error('草稿未能保存，已停止提交。');
}
export async function acquireDraft({storage,locks,key}){
  if(!locks?.request)throw Error('当前浏览器不支持安全的多标签编辑，无法修改草稿。');
  let release,finish;
  const held=new Promise(resolve=>{release=resolve});
  const ready=new Promise(resolve=>{finish=resolve});
  const task=locks.request(key,{mode:'exclusive',ifAvailable:true},async lock=>{
    if(!lock){finish(null);return}
    try{const value=readDraft(storage,key);finish({value,save(next){writeDraft(storage,key,next)},release});await held}
    catch(e){finish({error:e});}
  });
  task.catch(e=>finish({error:e}));
  const result=await ready;
  if(!result)throw Error('另一标签页正在编辑此草稿，请返回原标签页或关闭后重新进入。');
  if(result.error)throw result.error;
  return result;
}
