export function candidateWatchBody(form,profiles,objects,requestId,releaseID){
 const profile=profiles.find(p=>p.key===form.profile),object=objects.find(o=>o.id===form.object);
 if(!profile||!object)throw Error('请选择业务对象和本地处理结果。');
 if(!form.confirmed)throw Error('请确认此规则只报告待核查线索。');
 const body={request_id:requestId,...(releaseID?{release_id:releaseID}:{}),entity_id:object.id,profile:profile.key,name:(form.name?.trim()||object.name+' · '+profile.name).slice(0,120),confirm_candidate_only:true};
 if(profile.value_kind==='text'){
  const needle=form.needle?.trim();if(!needle||needle.length>100)throw Error('请填写不超过100字的待核查内容。');
  return {...body,operator:'contains',needle};
 }
 if(!/^\d+$/.test(String(form.threshold)))throw Error('请填写有限、非负的整数候选数量。');
 const threshold=Number(form.threshold);if(!Number.isSafeInteger(threshold)||threshold>1e9||!['gt','gte'].includes(form.operator)||(form.operator==='gte'&&threshold<1))throw Error('至少匹配时应为1或更多，且不超过十亿。');
 const min_score=Number(form.minScore||0);if(!Number.isFinite(min_score)||min_score<0||min_score>1)throw Error('模型分数应在0至1之间。');
 const label=form.label?.trim()||null;
 return {...body,operator:form.operator,threshold,min_score,label};
}
export const watchCondition=d=>d.operator==='contains'?'文字包含“'+d.needle+'”':(d.operator==='gte'?'至少':'多于')+d.threshold+'项候选'+(d.label?'，目标类别：'+d.label:'')+(d.min_score?'，模型分数至少'+d.min_score:'');
