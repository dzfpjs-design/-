// Pure, explicit V6 -> independent manual-v5 adapter. The caller MUST verify
// the complete original projection, journal, source and scope before calling.
// The old tag below is only a private structural-validation view, never an
// original model answer. Nothing here adopts, saves, authorizes or executes.
import {copy,same} from './report-full-reading-contract.mjs';
import {evidenceManualFromProjection} from './report-evidence-manual-v5.mjs';

const CANDIDATE='report-evidence-candidate-projection-v6';
const MAX_REVIEW_BYTES=512*1024;
const need=(value,message='新候选与人工稿无法逐字段对应；原记录未修改。')=>{if(!value)throw Error(message);};

function originalShape(value,original){
 const restored=copy(value.result);restored.schema=CANDIDATE;
 for(const [index,claim] of restored.global_claims.entries())claim.key=original.global_claims[index].key;
 for(const [bi,business] of restored.businesses.entries()){
  const source=original.businesses[bi],steps=source.steps.map(item=>item.key),people=source.participants.map(item=>item.key);
  for(const [si,step] of business.steps.entries()){
   step.key=steps[si];step.next_key=step.next_index===null?'end':steps[step.next_index];step.else_key=step.else_index===null?'end':steps[step.else_index];delete step.next_index;delete step.else_index;
  }
  for(const [pi,person] of business.participants.entries())person.key=people[pi];
  for(const collection of ['interactions','conditions','coordination'])for(const [index,item] of business[collection].entries()){
   item.key=source[collection][index].key;item.step_keys=item.step_indices.map(si=>steps[si]);delete item.step_indices;
   if(collection==='interactions'){
    item.from_participant=people[item.from_participant_index];item.to_participant=people[item.to_participant_index];delete item.from_participant_index;delete item.to_participant_index;
   }
  }
 }
 return restored;
}

export function evidenceManualFromProjectionV6(result,note=''){
 need(result?.schema===CANDIDATE,'需要已由外层完整核验的新候选，未转换旧模型原答。');
 const original=copy(result),internalValidationView=copy(original);
 internalValidationView.schema='report-evidence-candidate-projection-v5';
 const value=evidenceManualFromProjection(internalValidationView,note);
 need(new TextEncoder().encode(JSON.stringify(value)).byteLength<=MAX_REVIEW_BYTES,'人工稿超过512 KiB保存上限；原候选保留，未截断或采用。');
 need(same(originalShape(value,original),original)&&same(result,original));
 return value;
}
