export const A='w:p1/s1',B='w:p2/s1';
export const DECISION_PATH='/business/flows/0/phases/0/decision';
export const STEP_PATH=['business','flows',0,'phases',0,'branches',0,'phases',0,'branches',0,'phases',0,'steps'];
export const attr=()=>({citations:[A],inferred:true,reason:'公开合成原文，只用于界面与持久化测试，业务语义仍待核对。'});
export const action=(name,actor='human',role='业务核验人员')=>({name,actor,role,responsibility:'核对原资料并保留处理记录',inputs:['当前业务资料'],outputs:[name+'记录'],attribution:attr()});
export function nestedPlan(){return {schema:'report-business-flow-nested-plan-v192',binding_hash:'a'.repeat(64),business:{name:'递归业务核对',flows:[{
 name:'修复建议审批与并行补核',trigger:{kind:'event',description:'收到需要复核的修复建议',attribution:attr()},phase_order:attr(),phases:[{
  kind:'decision',attribution:attr(),decision:action('确认处置建议','human','值班负责人'),branches:[{
   condition:'负责人同意继续处置',attribution:attr(),phases:[{kind:'parallel',attribution:attr(),dispatch:action('组织通知与补查','business_agent','业务协调岗位'),branches:[
    {phases:[{kind:'steps',attribution:attr(),steps:[action('通知责任人员','business_agent'),action('记录通知回执','data_agent')]}]},
    {phases:[{kind:'repeat',attribution:attr(),steps:[action('补充现场观察','perception_agent')],check:action('核对资料是否充分','business_agent'),repeat_if:'仍有缺项且可继续补查',finish_if:'资料充分或明确转交人工处理'}]},
   ],wait_mode:'all',join_attribution:attr()}]},
   {condition:'负责人不同意继续处置',attribution:attr(),phases:[]},
  ]}],completion:action('保存处置记录','data_agent')
 }],questions:[{question:'仍需核对补查能力',reason:'设备与能力绑定尚未接入',citations:[A,B]}]}};}
export const evidence=()=>[{span_ref:A,source_text:'公开原文A：负责人同意后，通知与补查并行；资料不足时再次核查。',text_origin:'platform_resolved_source'},{span_ref:B,source_text:'公开原文B：保留不同意分支及全部处置记录。',text_origin:'platform_resolved_source'}];
export function causality(){return {schema:'business-flow-nested-causality-view-v193',original_candidate_only:true,runtime_entry_binding_verified:false,current_draft_matches_original:true,rows:[{
 flow_index:0,flow_name:'修复建议审批与并行补核',action_path:DECISION_PATH,action_name:'确认处置建议',step_name:'确认处置建议',actor:'human',role:'值班负责人',control_ancestors:[],input_path:DECISION_PATH+'/inputs/0',input_ordinal:0,input_name:'当前业务资料',sources:[{kind:'entry',entry_path:'/entry_ports/0',label:'流程触发或配置需提供的资料（待绑定）'}],citations:[A],
 }]};}
export function diagnostics(value){return {schema:'business-flow-review-diagnostics-v1',available:true,value:structuredClone(value),issues:[{code:'semantic_pending',path:'/business/flows/0/phases/0/decision/role',message:'请核对负责人及条件依据',span_ref:A}],source_evidence:evidence(),structural_valid:false,source_valid:false,semantic_quality_verified:false,can_apply:false,can_publish:false,can_execute:false};}
