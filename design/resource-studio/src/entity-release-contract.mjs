const text=value=>typeof value==='string'&&value.trim()?value.trim():null;

export function taskObjectState(object,releaseID){
 const proof=object?.object_compatibility,knownStatus=['current','compatible','incompatible'].includes(proof?.status);
 const matched=typeof releaseID==='string'&&releaseID.length>0&&proof?.target_release_id===releaseID;
 const usable=matched&&knownStatus&&proof.usable===true&&['current','compatible'].includes(proof.status);
 const reason=usable?'':!matched?'对象可用范围尚未按当前实例版本核实，请刷新记录':text(proof?.reason)||'此对象尚不能用于当前业务版本，请核对对象定义与来源';
 const label=text(proof?.source_label)||'已保存对象',type=text(proof?.source_type)||'对象类型待核实';
 const source=Number.isInteger(proof?.source_release_number)&&proof.source_release_number>0?'来源第 '+proof.source_release_number+' 版':'来源版本待核实';
 const version=Number.isInteger(object?.version)&&object.version>0?'对象第 '+object.version+' 版':'对象版本待核实';
 return {usable,status:usable?proof.status:'incompatible',reason,label,type,
  description:(usable?(proof.status==='compatible'?'可沿用':'当前对象'):'暂不可用于当前任务')+' · '+source+' · '+version};
}

export function taskObjectSelectionIssues(objects,selected,releaseID){
 return selected.flatMap((id,index)=>{
  const object=objects.find(item=>item.id===id);
  if(!object)return [{id,label:'已选对象 '+(index+1),reason:'记录暂未载入或已不再可见；原选择保留，请刷新或取消此项选择'}];
  const state=taskObjectState(object,releaseID);
  return state.usable?[]:[{id,label:state.label,reason:state.reason}];
 });
}

export function assertTaskObjectSelection(objects,selected,releaseID){
 if(!Array.isArray(selected)||!selected.length)throw Error('请选择任务对象');
 if(new Set(selected).size!==selected.length)throw Error('任务对象选择重复，请核对原选择');
 const issues=taskObjectSelectionIssues(objects,selected,releaseID);
 if(issues.length)throw Error('已选对象当前不能发起任务：'+issues.map(item=>item.label+'（'+item.reason+'）').join('；')+'。原输入和选择已保留，可刷新记录或取消失效选择。');
 return selected;
}
