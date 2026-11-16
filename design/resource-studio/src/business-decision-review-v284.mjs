/** Display-only original V284 review context. No verdict, grant or dispatch. */
export const BUSINESS_DECISION_REVIEW_RESULT='business-decision-review-result-v284';
export const BUSINESS_DECISION_REVIEW_CONTEXT='business-decision-review-context-v284';
export const DECISION_PLAN_V285='workflow-decision-review-plan-v285';
export const REVIEW_STATES=Object.freeze({ready_for_review:'建议可供人工核对',needs_information:'仍需补充资料',conflicting_evidence:'原事实存在冲突',review_required:'需要人工核对'});
export const SUITABILITY=Object.freeze({suitable:'模型认为适合',unsuitable:'模型认为不适合',uncertain:'模型尚不能判断'});
export const APPLICABILITY=Object.freeze({applicable:'登记端口覆盖原需求',inapplicable:'未完整覆盖原需求或不适用',unknown:'适用条件尚不明确'});
export const ROUTES=Object.freeze({acquire:'原计划建议补充观测',continue:'原计划转入原人工步骤',no_action:'原计划不安排新动作，先由人员核对',review_required:'原计划要求人工核对'});
export const REASONS=Object.freeze({acquisition_ready:'原缺字段已有对应补采上下文；真正执行仍须原审批和收件链。',current_facts_ready:'原冻结资料充分且一致，未安排新的补采。',no_recommendation:'模型未推荐具体方案；请核对原资料和不行动的理由。',selection_needs_review:'所选方案的适用性、依据或前提仍需核查。',conflicting_evidence:'原事实存在冲突，不能用人工说明代替冲突核查。',facts_missing:'原资料仍有缺项或不能确认充分性。',source_review_incomplete:'部分原资料尚未完整核对。',unsupported_decision_intent:'此类决策尚不在本版本可执行范围。',unsupported_acquisition_scope:'所选方案不能由当前单一补采分支完整执行，未部分执行。'});
const flags=['can_dispatch','permissions_verified','source_authenticity_verified','current_outcome_verified','goal_completion_verified','data_reception_verified'];
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const sha=/^[0-9a-f]{64}$/;
const ref=/^[a-z][a-z0-9_]{0,63}$/;
const isRef=value=>typeof value==='string'&&ref.test(value);
const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const exact=(value,keys)=>object(value)&&Object.keys(value).length===keys.length&&keys.every(key=>Object.hasOwn(value,key));
const text=(value,max=4000)=>typeof value==='string'&&value.length>0&&value.length<=max&&/\S/u.test(value);
const list=(value,max)=>Array.isArray(value)&&value.length<=max;
const refs=(value,allowed,max=128)=>list(value,max)&&new Set(value).size===value.length&&value.every(key=>typeof key==='string'&&ref.test(key)&&(!allowed||allowed.has(key)));
const sameList=(left,right)=>Array.isArray(left)&&Array.isArray(right)&&left.length===right.length&&left.every((value,index)=>value===right[index]);
const need=(value)=>{if(!value)throw Error('原建议、资料引用或本次任务范围暂不能完整核对，请重新读取原记录。');};
const index=(rows,key,max)=>{need(list(rows,max)&&rows.every(object));const mapped=new Map(rows.map(row=>[row[key],row]));need(mapped.size===rows.length&&rows.every(row=>isRef(row[key])));return mapped;};
const window=value=>object(value)&&['start','end'].every(key=>value[key]===null||text(value[key],100));
const noAuthority=value=>flags.every(key=>value?.[key]===false);
const stable=value=>JSON.stringify(value,(_key,item)=>object(item)?Object.fromEntries(Object.keys(item).sort().map(key=>[key,item[key]])):item);
const comparableResult=value=>({...value,mechanical_review:{...value.mechanical_review,conflicts:value.mechanical_review.conflicts.map(row=>({...row,sides:row.sides.map(side=>({...side,value:null}))}))}});

export function isBusinessDecisionReviewResult(value){
 if(!exact(value,['schema','input_hash','decision_intent','scope','model_value','mechanical_review','review_state',...flags])||value.schema!==BUSINESS_DECISION_REVIEW_RESULT||!sha.test(value.input_hash)||!noAuthority(value)||!Object.hasOwn(REVIEW_STATES,value.review_state))return false;
 if(!['information_acquisition','operational_action','business_conclusion'].includes(value.decision_intent)||!object(value.scope)||!object(value.mechanical_review))return false;
 const model=value.model_value,review=value.mechanical_review;
 return exact(model,['schema','candidate_reviews','source_reviews','conflicts','recommendation'])&&model.schema==='business-decision-review-value-v284'&&list(model.candidate_reviews,16)&&model.candidate_reviews.length>0&&list(model.source_reviews,32)&&model.source_reviews.length>0&&list(model.conflicts,32)&&object(model.recommendation)
  &&exact(review,['candidates','sources','missing_requirement_refs','fact_consistency','current_facts_sufficient','conflicts'])&&list(review.candidates,16)&&list(review.sources,32)&&list(review.conflicts,32)&&refs(review.missing_requirement_refs,null,30)&&['consistent','contradictory','unknown'].includes(review.fact_consistency)&&typeof review.current_facts_sufficient==='boolean';
}

export function decisionReviewContextPath(taskId,activationId){
 need(uuid.test(taskId)&&text(activationId,200));
 return '/workspace/tasks/'+encodeURIComponent(taskId)+'/decision-review-context-v284?activation_id='+encodeURIComponent(activationId);
}

/** Cross-bind the GET to the exact graph output. The server authenticates and
 * hashes the original wire; JS-decoded numbers are never used to rehash it. */
export function checkedDecisionReviewContext(value,{taskId,activationId,result}={}){
 need(uuid.test(taskId)&&text(activationId,200)&&exact(value,['schema','task_id','activation_id','invocation_id','input_hash','result','decision_input','fields','fact_values','simulation_only']));
 need(value.schema===BUSINESS_DECISION_REVIEW_CONTEXT&&value.task_id===taskId&&value.activation_id===activationId&&uuid.test(value.invocation_id)&&sha.test(value.input_hash)&&typeof value.simulation_only==='boolean');
 const out=value.result,input=value.decision_input;
 need(isBusinessDecisionReviewResult(out)&&out.input_hash===value.input_hash&&object(input)&&input.schema==='business-decision-input-v274'&&input.decision_intent===out.decision_intent);
 need(exact(input,['schema','decision_intent','scope','requirements','sources','capabilities','candidates','facts','known_conflicts']));
 for(const scope of [input.scope,out.scope])need(exact(scope,['task_id','activation_id','goal_snapshot_hash','entity_ids'])&&scope.task_id===taskId&&scope.activation_id===activationId&&sha.test(scope.goal_snapshot_hash)&&list(scope.entity_ids,30)&&scope.entity_ids.length>0&&new Set(scope.entity_ids).size===scope.entity_ids.length&&scope.entity_ids.every(id=>uuid.test(id)));
 need(input.scope.goal_snapshot_hash===out.scope.goal_snapshot_hash&&sameList(input.scope.entity_ids,out.scope.entity_ids));
 if(result!==undefined){need(isBusinessDecisionReviewResult(result)&&result.input_hash===value.input_hash&&result.scope.task_id===taskId&&result.scope.activation_id===activationId&&result.scope.goal_snapshot_hash===out.scope.goal_snapshot_hash&&sameList(result.scope.entity_ids,out.scope.entity_ids));
  // Conflict numbers may be represented by the graph's lossless-number wrapper
  // and ordinary JSON differently. Compare every other original result field;
  // display numeric values only from this authenticated GET's exact spellings.
  need(stable(comparableResult(result))===stable(comparableResult(out)));}
 const candidates=index(input.candidates,'candidate_ref',16),requirements=index(input.requirements,'requirement_ref',30),sources=index(input.sources,'source_ref',32),facts=index(input.facts,'fact_ref',128),caps=index(input.capabilities,'capability_ref',16);
 need(candidates.size>0&&requirements.size>0&&sources.size>0&&caps.size>0);
 const entities=new Set(input.scope.entity_ids);
 for(const row of sources.values())need(['evidence','packet','observation','archive_record','capability'].includes(row.kind)&&uuid.test(row.source_id)&&sha.test(row.content_hash)&&(row.text===null||typeof row.text==='string'&&row.text.length<=20000)&&['readable','partial','unreadable'].includes(row.readability));
 for(const row of requirements.values())need(entities.has(row.entity_id)&&isRef(row.field)&&['number','integer','string','boolean'].includes(row.value_type)&&(row.unit===null||text(row.unit,100))&&window(row.time_scope));
 for(const row of caps.values())need(sources.get(row.source_ref)?.kind==='capability'&&sources.get(row.source_ref).source_id===row.capability_id&&uuid.test(row.capability_id)&&sha.test(row.capability_hash)&&sources.get(row.source_ref).content_hash===row.capability_hash&&list(row.output_ports,30)&&list(row.information_targets,30));
 const citation=(row)=>{need(exact(row,['source_ref','quote_start','quote_end','quote'])&&sources.has(row.source_ref));const source=sources.get(row.source_ref);if(source.text===null)need(row.quote===null&&row.quote_start===null&&row.quote_end===null);else need(Number.isSafeInteger(row.quote_start)&&Number.isSafeInteger(row.quote_end)&&row.quote_start>=0&&row.quote_end>row.quote_start&&row.quote_end<=Array.from(source.text).length&&text(row.quote,1200)&&Array.from(source.text).slice(row.quote_start,row.quote_end).join('')===row.quote);};
 for(const fact of facts.values()){need(entities.has(fact.entity_id)&&isRef(fact.field)&&['observation','document_statement','capability_declaration','inference'].includes(fact.kind)&&(fact.unit===null||text(fact.unit,100))&&window(fact.time_scope)&&list(fact.source_refs,16)&&fact.source_refs.length>0);fact.source_refs.forEach(citation);if(fact.kind==='observation')need(fact.source_refs.some(row=>sources.get(row.source_ref).kind==='observation'));if(fact.kind==='capability_declaration')need(fact.source_refs.some(row=>sources.get(row.source_ref).kind==='capability'));}
 for(const row of candidates.values())need(text(row.proposal,2000)&&refs(row.capability_refs,caps,8)&&row.capability_refs.length>0&&list(row.requirement_mappings,30));
 const model=out.model_value,review=out.mechanical_review;
 need(sameList(model.candidate_reviews.map(row=>row.candidate_ref),[...candidates.keys()])&&sameList(review.candidates.map(row=>row.candidate_ref),[...candidates.keys()]));
 need(sameList(model.source_reviews.map(row=>row.source_ref),[...sources.keys()])&&sameList(review.sources.map(row=>row.source_ref),[...sources.keys()]));
 for(const row of model.candidate_reviews)need(exact(row,['candidate_ref','suitability','reasoning','supporting_fact_refs','blocking_conditions'])&&Object.hasOwn(SUITABILITY,row.suitability)&&text(row.reasoning,2000)&&refs(row.supporting_fact_refs,facts)&&list(row.blocking_conditions,16)&&row.blocking_conditions.every(line=>text(line,2000)));
 for(const row of review.candidates){const candidate=candidates.get(row.candidate_ref);need(exact(row,['candidate_ref','capability_refs','requirement_refs','covered_requirement_refs','uncovered_requirement_refs','unknown_requirement_refs','applicability','support','blocking_conditions'])&&sameList(row.capability_refs,candidate.capability_refs)&&sameList(row.requirement_refs,[...requirements.keys()])&&['applicable','inapplicable','unknown'].includes(row.applicability)&&['supported','insufficient'].includes(row.support)&&sameList(row.blocking_conditions,model.candidate_reviews.find(item=>item.candidate_ref===row.candidate_ref).blocking_conditions));for(const key of ['covered_requirement_refs','uncovered_requirement_refs','unknown_requirement_refs'])need(refs(row[key],requirements,30));}
 for(let at=0;at<model.source_reviews.length;at++){const row=model.source_reviews[at],system=review.sources[at];need(exact(row,['source_ref','assessment','reason'])&&['reviewed','unreadable','incomparable'].includes(row.assessment)&&text(row.reason,2000)&&!(row.assessment==='reviewed'&&sources.get(row.source_ref).readability!=='readable'));need(exact(system,['source_ref','assigned_fact_refs','assessment'])&&system.assessment===row.assessment&&sameList(system.assigned_fact_refs,[...facts.values()].filter(fact=>fact.source_refs.some(item=>item.source_ref===row.source_ref)).map(fact=>fact.fact_ref)));}
 need(refs(review.missing_requirement_refs,requirements,30));
 const recommendation=model.recommendation;need(exact(recommendation,['candidate_ref','reason','supporting_fact_refs'])&&(recommendation.candidate_ref===null||candidates.has(recommendation.candidate_ref))&&text(recommendation.reason,2000)&&refs(recommendation.supporting_fact_refs,facts));
 const rawConflicts=index(model.conflicts,'conflict_ref',32),conflicts=index(review.conflicts,'conflict_ref',32);need(sameList([...rawConflicts.keys()],[...conflicts.keys()]));
 need(list(input.known_conflicts,32));for(const known of input.known_conflicts)need(rawConflicts.has(known.conflict_ref)&&sameList(rawConflicts.get(known.conflict_ref).fact_refs,known.fact_refs)&&rawConflicts.get(known.conflict_ref).reason===known.reason);
 for(const conflict of conflicts.values()){const original=rawConflicts.get(conflict.conflict_ref);need(entities.has(conflict.entity_id)&&isRef(conflict.field)&&(conflict.unit===null||text(conflict.unit,100))&&window(conflict.time_scope)&&refs(conflict.fact_refs,facts,8)&&conflict.fact_refs.length>=2&&sameList(conflict.fact_refs,original.fact_refs)&&text(conflict.reason,2000)&&list(conflict.sides,8)&&sameList(conflict.sides.map(row=>row.fact_ref),conflict.fact_refs));for(const side of conflict.sides){need(list(side.source_refs,16));side.source_refs.forEach(citation);need(sameList(side.source_refs.map(row=>row.source_ref),facts.get(side.fact_ref).source_refs.map(row=>row.source_ref)));}}
 need(list(value.fields,900));const fieldKeys=new Set();for(const row of value.fields){const key=row.entity_id+':'+row.field;need(exact(row,['entity_id','field','label','value_type','unit'])&&entities.has(row.entity_id)&&isRef(row.field)&&text(row.label,400)&&['number','integer','string','boolean'].includes(row.value_type)&&(row.unit===null||text(row.unit,100))&&!fieldKeys.has(key));fieldKeys.add(key);}
 for(const row of requirements.values()){const field=value.fields.find(item=>item.entity_id===row.entity_id&&item.field===row.field);need(field&&field.value_type===row.value_type&&field.unit===row.unit);}
 need(list(value.fact_values,128));const spellings=new Set();for(const row of value.fact_values){need(exact(row,['fact_ref','text'])&&facts.has(row.fact_ref)&&typeof row.text==='string'&&row.text.length<=12000&&!spellings.has(row.fact_ref));spellings.add(row.fact_ref);}
 return value;
}

export function factValueText(context,fact){
 if(typeof fact.value==='number'){const original=context?.fact_values?.find(row=>row.fact_ref===fact.fact_ref);return original?original.text:'数值原文暂不能核对，未显示近似值';}
 if(fact.value===null)return '原记录未提供值';
 if(typeof fact.value==='boolean')return fact.value?'是':'否';
 if(typeof fact.value==='string')return fact.value===''?'原记录为空文本':fact.value;
 return '原值类型暂不能核对';
}

export const unitText=value=>value===null?'无单位标注':value;
export const timeText=value=>value.start===null&&value.end===null?'原记录未附时间范围':value.start===value.end?'原记录时间：'+value.start:'原记录范围：'+(value.start??'未提供起始时间')+' 至 '+(value.end??'未提供结束时间');

export function isDecisionPlanV285(value){
 if(!exact(value,['schema','route','reason_code','acquisition_required','no_gap','selected_candidate_ref','missing_requirement_refs','conflict_refs','resense_context','resense_capability','producer','simulation_only','can_dispatch','permissions_verified','physical_state_verified','independent_acceptance'])||value.schema!==DECISION_PLAN_V285||!Object.hasOwn(ROUTES,value.route)||!Object.hasOwn(REASONS,value.reason_code)||['can_dispatch','permissions_verified','physical_state_verified','independent_acceptance'].some(key=>value[key]!==false)||typeof value.simulation_only!=='boolean'||typeof value.no_gap!=='boolean'||typeof value.acquisition_required!=='boolean'||!refs(value.missing_requirement_refs,null,30)||!refs(value.conflict_refs,null,32)||value.no_gap!==(value.missing_requirement_refs.length===0)||(value.selected_candidate_ref!==null&&!isRef(value.selected_candidate_ref)))return false;
 const producer=value.producer;
 if(!exact(producer,['schema','workspace_id','task_id','job_id','invocation_id','actor_id','capability_id','activation_id','node_id','capability_hash','input_hash','output_hash','request_key_hash','capture_hash','decision_input_hash','frame_hash','sources_hash'])||producer.schema!=='workflow-decision-review-producer-v285'||!['workspace_id','task_id','job_id','invocation_id','actor_id','capability_id'].every(key=>typeof producer[key]==='string'&&uuid.test(producer[key]))||!['activation_id','node_id'].every(key=>text(producer[key],200))||!['capability_hash','input_hash','output_hash','request_key_hash','capture_hash','decision_input_hash','frame_hash','sources_hash'].every(key=>typeof producer[key]==='string'&&sha.test(producer[key])))return false;
 if(value.route==='acquire')return value.reason_code==='acquisition_ready'&&value.acquisition_required===true&&!value.no_gap&&value.conflict_refs.length===0&&isRef(value.selected_candidate_ref)&&object(value.resense_context)&&value.resense_context.schema==='workflow-resense-context-v196'&&value.resense_context.can_dispatch===false&&value.resense_context.permissions_verified===false&&value.resense_context.scope?.task_id===producer.task_id&&exact(value.resense_capability,['capability_id','capability_hash','release_version'])&&typeof value.resense_capability.capability_id==='string'&&uuid.test(value.resense_capability.capability_id)&&typeof value.resense_capability.capability_hash==='string'&&sha.test(value.resense_capability.capability_hash)&&text(value.resense_capability.release_version,100);
 if(value.acquisition_required!==false||value.resense_context!==null||value.resense_capability!==null)return false;
 if(value.route==='continue')return value.reason_code==='current_facts_ready'&&value.no_gap&&value.conflict_refs.length===0&&isRef(value.selected_candidate_ref);
 if(value.route==='no_action')return value.reason_code==='no_recommendation'&&value.no_gap&&value.conflict_refs.length===0&&value.selected_candidate_ref===null;
 return !['acquisition_ready','current_facts_ready','no_recommendation'].includes(value.reason_code)&&(value.reason_code==='conflicting_evidence'?value.conflict_refs.length>0:value.conflict_refs.length===0);
}

/** No context GET or producer details are needed to explain this saved plan.
 * The summary does not authenticate a producer or derive a new route. */
export function decisionPlanSummaryView(value,{taskId}={}){
 need(isDecisionPlanV285(value)&&typeof taskId==='string'&&uuid.test(taskId)&&value.producer.task_id===taskId);
 const waits={acquire:'原计划拟进入补充观测分支。是否已经派发、处理或收件，请核对原采集进度；此计划本身不能证明已经发送。',continue:'原计划转入原人工步骤。实际待办及目标仍按原审批、输入和完成条件办理。',no_action:'等待指派人员核对不行动建议，再进入原人工步骤；不把暂不行动当作目标完成。',review_required:'等待指派人员核对原意见、缺项或冲突。人工记录不补写事实，也不解除执行限制。'};
 return {route:ROUTES[value.route],reason:REASONS[value.reason_code],wait:waits[value.route],simulation:value.simulation_only,missingCount:value.missing_requirement_refs.length,conflictCount:value.conflict_refs.length,noGap:value.no_gap,selection:value.selected_candidate_ref===null?'原建议暂不推荐具体方案。':'原建议保留了所选方案，具体提案及依据请在业务建议中核对。'};
}

export function decisionReviewView(context){
 const input=context.decision_input,result=context.result,model=result.model_value,system=result.mechanical_review;
 const fields=new Map(context.fields.map(row=>[row.entity_id+':'+row.field,row]));
 const fieldLabel=(entity,field)=>(input.scope.entity_ids.length>1?'对象'+(input.scope.entity_ids.indexOf(entity)+1)+' · ':'')+(fields.get(entity+':'+field)?.label||'原业务字段');
 const requirements=new Map(input.requirements.map(row=>[row.requirement_ref,row]));
 const requirementLabel=ref=>{const row=requirements.get(ref);return fieldLabel(row.entity_id,row.field);};
 const sources=new Map(input.sources.map((row,index)=>[row.source_ref,{...row,label:({'capability':'登记能力','observation':'原观测资料','archive_record':'档案原记录','packet':'本次资料包','evidence':'原件资料'})[row.kind]+(index+1)}]));
 const facts=new Map(input.facts.map(fact=>{const declarations=input.capabilities.filter(cap=>fact.source_refs.some(row=>row.source_ref===cap.source_ref));const targets=declarations.flatMap(cap=>[...cap.information_targets,...cap.output_ports]).map(port=>fieldLabel(fact.entity_id,port.field));return [fact.fact_ref,{...fact,label:fieldLabel(fact.entity_id,fact.field),kindLabel:({'observation':'原观测记录','document_statement':'原档案记录','capability_declaration':'登记能力声明','inference':'推断，不能当作观测'})[fact.kind],valueText:factValueText(context,fact),unitLabel:unitText(fact.unit),timeLabel:timeText(fact.time_scope),declaration:fact.kind==='capability_declaration',targetLabels:[...new Set(targets)],citations:fact.source_refs.map(row=>({...row,source:sources.get(row.source_ref)}))}];}));
 const factRows=refs=>refs.map(ref=>facts.get(ref));
 const candidates=input.candidates.map((candidate,index)=>{const opinion=model.candidate_reviews[index],check=system.candidates[index];return {label:'方案'+(index+1),proposal:candidate.proposal,suitability:SUITABILITY[opinion.suitability],reasoning:opinion.reasoning,blocking:[...opinion.blocking_conditions],applicability:APPLICABILITY[check.applicability],support:check.support==='supported'?'引用了本方案各能力的登记声明':'登记声明或所需依据引用仍不足',covered:check.covered_requirement_refs.map(requirementLabel),uncovered:check.uncovered_requirement_refs.map(requirementLabel),unknown:check.unknown_requirement_refs.map(requirementLabel),facts:factRows(opinion.supporting_fact_refs)};});
 const selected=model.recommendation.candidate_ref===null?-1:input.candidates.findIndex(row=>row.candidate_ref===model.recommendation.candidate_ref);
 return {state:REVIEW_STATES[result.review_state],simulation:context.simulation_only,candidates,missing:system.missing_requirement_refs.map(requirementLabel),consistency:({consistent:'原资料在本次范围内未见可比冲突',contradictory:'原资料存在可比冲突',unknown:'当前资料一致性仍不明确'})[system.fact_consistency],sufficient:system.current_facts_sufficient,recommendation:{label:selected<0?'暂不推荐具体方案':candidates[selected].label+'：'+candidates[selected].proposal,reason:model.recommendation.reason,facts:factRows(model.recommendation.supporting_fact_refs)},conflicts:system.conflicts.map(row=>({label:fieldLabel(row.entity_id,row.field),reason:row.reason,unit:unitText(row.unit),time:timeText(row.time_scope),sides:factRows(row.fact_refs)})),sources:[...sources.values()].map((row,index)=>({...row,assessment:({reviewed:'模型已核对',unreadable:'模型未能读取',incomparable:'模型认为暂不可比较'})[model.source_reviews[index].assessment],reason:model.source_reviews[index].reason,factCount:system.sources[index].assigned_fact_refs.length}))};
}

/** A UI routing hint using the plan's explicit producer ID, never equal-result
 * search or authority. The original-context GET independently verifies it. */
export function reviewDecisionBinding(task,human){
 if(human?.node_id!=='review_decision')return null;
 const state=task?.graph_runtime;if(!object(state)||!object(state.activations))return null;
 const plans=Object.values(state.activations).filter(row=>row?.node_id==='planner'&&row.status==='completed'&&row.epoch===state.cancel_epoch&&row.output?.schema===DECISION_PLAN_V285&&['no_action','review_required'].includes(row.output.route));
 if(plans.length!==1)return null;
 const plan=plans[0].output;if(!isDecisionPlanV285(plan))return null;
 const producer=plan.producer,original=state.activations[producer?.activation_id];
 if(!original||original.id!==producer.activation_id||original.node_id!==producer.node_id||original.status!=='completed'||producer.task_id!==task.id||!isBusinessDecisionReviewResult(original.output)||original.output.input_hash!==producer.decision_input_hash||original.output.scope.task_id!==task.id||original.output.scope.activation_id!==original.id)return null;
 if(['can_dispatch','permissions_verified','physical_state_verified','independent_acceptance'].some(key=>plan[key]!==false)||!Object.hasOwn(REASONS,plan.reason_code))return null;
 return {activationId:original.id,result:original.output,plan};
}

export function decisionPlanView(plan,context){
 if(!plan)return null;
 need(isDecisionPlanV285(plan)&&plan.producer?.task_id===context.task_id&&plan.producer?.activation_id===context.activation_id&&plan.producer?.invocation_id===context.invocation_id&&plan.producer?.decision_input_hash===context.input_hash);
 return {route:ROUTES[plan.route],reason:REASONS[plan.reason_code]};
}
