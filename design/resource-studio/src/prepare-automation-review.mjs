import {stableStringify} from './content-signature.js';
import {materializeEmptyReviewPolicy} from './automation-policy-contract.mjs';
import {withoutSecrets} from './catalog.js';

// The preparation only saves rules; the caller separately journals and sends
// the confirmation. Each await checks the original account and business.
export async function prepareCurrentAutomationReview({context,flush,readDraft,commit}){
 const initial=context(),scope=stableStringify([initial.workspaceId,initial.userId,initial.username,initial.business]);
 const check=()=>{const now=context();if(stableStringify([now.workspaceId,now.userId,now.username,now.business])!==scope)throw Error('账号、工作区或业务已变化；本次未确认，请重新核对。');return now;};
 if(!initial.workspaceId||!initial.userId||!initial.business)throw Error('账号或当前业务尚未确定，未启动确认。');
 const revision=await flush(true);check();
 const saved=await readDraft();const current=check();
 if(saved?.revision!==revision||!saved.configuration)throw Error('服务器配置已变化，请重新读取并核对当前规则。');
 const proposal=materializeEmptyReviewPolicy(saved.configuration.automation,current.visible,current.names,current.aliases,initial.business);
 if(!proposal.changed)return revision;
 const accepted=commit(value=>{check();if(stableStringify(value)!==stableStringify(saved.configuration))throw Error('本页配置已变化，请先保存并重新核对。');const candidate={...value,automation:proposal.automation};if(stableStringify(withoutSecrets(candidate))!==stableStringify(candidate))throw Error('已有配置包含无法安全保存的敏感字段；原记录保留，请联系管理员处理。');return candidate;});
 if(!accepted)throw Error('自动化规则尚未可靠保存，未启动确认；本页输入保留。');
 const nextRevision=await flush(false);check();
 const readback=await readDraft();check();
 if(readback?.revision!==nextRevision)throw Error('自动化规则的保存版本已变化，请重新核对。');
 const expected={...proposal.automation.policies[initial.business]},actual={...readback.configuration?.automation?.policies?.[initial.business]};
 delete expected.flow_review;delete actual.flow_review;
 if(stableStringify(expected)!==stableStringify(actual))throw Error('服务器保存的规则与本次确认内容不同，请重新核对。');
 return nextRevision;
}
