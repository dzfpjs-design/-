import {taskObjectState,taskObjectSelectionIssues} from './entity-release-contract.mjs';

export const usablePerceptionObjects=(objects,releaseID)=>objects.filter(object=>taskObjectState(object,releaseID).usable);
export function perceptionObjectState(objects,identity,releaseID){
 const object=objects.find(row=>row.id===identity);
 return object?taskObjectState(object,releaseID):{usable:false,label:'原选择对象',description:'原选择保留',reason:'对象暂未载入或已不再可见，请刷新记录或取消此项选择'};
}
export function assertPerceptionObjects(objects,identities,releaseID){
 if(!Array.isArray(identities)||!identities.length||new Set(identities).size!==identities.length)throw Error('请选择不重复的业务对象，原草稿保留');
 const issues=taskObjectSelectionIssues(objects,identities,releaseID);
 if(issues.length)throw Error(issues.map(issue=>issue.label+'：'+issue.reason).join('；')+'。原选择与输入保留，可刷新或取消失效选择。');
 return identities;
}
export function assertPerceptionRequestRelease(originalRelease,releaseID){
 if(!originalRelease||originalRelease!==releaseID)throw Error('原请求的实例版本已变化或尚未固定；原请求保留，可核对服务器历史，不能自动改签为当前版本的新操作');
}
export function retainPerceptionPending(storage,key,request,pendingKey='action'){
 const raw=storage.getItem(key),record=raw===null?null:JSON.parse(raw);
 if(!request||JSON.stringify(record?.[pendingKey])!==JSON.stringify(request))throw Error('另一页面已更新原请求，尚未取消等待，请先核对保存记录');
 const identity=request.request_id||request.source_record_id||'original',archive=key+':retained:'+encodeURIComponent(identity)+':'+Date.now();
 const value=JSON.stringify({schema:'retained-perception-request-v1',original_raw:raw,pending_key:pendingKey,request});
 if(storage.getItem(archive)!==null)throw Error('原请求归档位置已有记录，尚未取消等待');
 storage.setItem(archive,value);
 if(storage.getItem(archive)!==value||storage.getItem(key)!==raw)throw Error('原请求归档或当前草稿无法核对，原等待继续保留');
 return archive;
}
