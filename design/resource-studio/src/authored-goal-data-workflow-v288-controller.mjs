import {createAuthoredWorkflowControllerClass} from './authored-workflow-controller-engine.mjs';
import {canonical,clone,need,INTENTS,freezePreparationPolicy,preparationPolicyChoice,checkedPreparationPolicyChoice} from './authored-goal-data-workflow-v288-protocol.mjs';

export class AuthoredGoalDataWorkflowControllerV288 extends createAuthoredWorkflowControllerClass('v288'){
 async setPreparationPolicy(value){
  this.check();need(!this.busy&&!this.pending&&!this.configurationReadbackPending&&!this.readbackExited,'原操作或采用配置仍待核对；未替换资料准备规则。');
  const epoch=this.epoch,before=canonical(this.draft),policy=await freezePreparationPolicy(clone(value),this.sha);
  this.check(epoch);need(canonical(this.draft)===before,'规则计算期间选项已变化，请核对现有选项后重新确认。');
  this.update({preparation_policy:policy,preparation_policy_choice:preparationPolicyChoice(policy)});return clone(policy);
 }
 editPreparationPolicyChoice(patch){const value=checkedPreparationPolicyChoice({...this.draft.preparation_policy_choice,...clone(patch)});this.update({preparation_policy_choice:value,preparation_policy:null});}
 confirmPreparationPolicy(){const value=this.draft.preparation_policy_choice;return this.setPreparationPolicy({time_basis:value.time_basis,allow_acquisition:value.allow_acquisition,max_age_seconds:value.max_age_seconds===''?null:Number(value.max_age_seconds)});}
 setDecisionIntent(value){need(INTENTS.includes(value),'请选择资料收集建议、业务行动建议或业务结论。');this.update({decision_intent:value});}
}
