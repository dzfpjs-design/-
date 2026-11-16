// Explicit known-family dispatch only. Original wire values, drafts, requests
// and proofs are passed unchanged to their own closed validators.
import * as array from './claim-array-reading-v168.mjs';
import * as enumeration from './claim-enum-reading-v172.mjs';
export {CLAIM_ARRAY_DRAFT,OBJECT_REVIEW_FAMILY,NATIVE_CANDIDATE_OBJECTS} from './claim-array-reading-v168.mjs';
export const CLAIM_READING_DRAFTS=Object.freeze([array.CLAIM_ARRAY_DRAFT,enumeration.CLAIM_ENUM_DRAFT]);
const need=value=>{if(!value)throw Error('报告引用策略版本无法核对，原回答、私稿和请求保留，不能降级。');};
function protocol(preview){if(enumeration.claimEnumPreview(preview))return enumeration;if(array.claimArrayPreview(preview))return array;need(false);}
export const claimArrayPreview=preview=>enumeration.claimEnumPreview(preview)||array.claimArrayPreview(preview);
export const claimEnumPreview=enumeration.claimEnumPreview;
export const claimArrayUsesObjectReview=row=>row?.schema===enumeration.CLAIM_ENUM_DRAFT||enumeration.claimEnumPreview(row?.preview)||array.claimArrayUsesObjectReview(row);
export const legacyEvidenceReviewAllowed=row=>!!row&&!claimArrayUsesObjectReview(row);
export function checkClaimArrayGeneration(spec,...args){return (enumeration.claimEnumGeneration(spec)?enumeration:array).checkClaimArrayGeneration(spec,...args);}
export const checkClaimArrayPreparation=(preview,...args)=>protocol(preview).checkClaimArrayPreparation(preview,...args);
export const claimArrayPolicy=(cap,...args)=>(cap?.execution?.report_evidence_output?.schema==='report-evidence-output-v172'?enumeration:array).claimArrayPolicy(cap,...args);
export const claimArrayDispatch=(preview,...args)=>protocol(preview).claimArrayDispatch(preview,...args);
export const claimArrayResponseReady=(stage,preview,...args)=>protocol(preview).claimArrayResponseReady(stage,preview,...args);
export const checkClaimArrayReceipt=(receipt,preview,...args)=>protocol(preview).checkClaimArrayReceipt(receipt,preview,...args);
export const verifyClaimArrayMapProjection=(projection,row,...args)=>protocol(row.preview).verifyClaimArrayMapProjection(projection,row,...args);
export const verifyClaimArrayReductionPreparation=(preview,row,...args)=>protocol(row.preview).verifyClaimArrayReductionPreparation(preview,row,...args);
export const verifyClaimArrayDraft=(row,...args)=>protocol(row.preview).verifyClaimArrayDraft(row,...args);
export async function claimArrayStageSpec(preview,chunkId){const selected=protocol(preview);return selected===enumeration?enumeration.claimEnumStageSpec(preview.map_preparation,chunkId):preview.map_preparation.generation_contract;}
