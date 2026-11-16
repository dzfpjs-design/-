import {reviewJournalKey,readReviewJournal} from './automation-review-contract.mjs';
import {stableStringify} from './content-signature.js';
export function shouldPreventSetupUnload({draftSync,draftKey,account,review,storage,otherUnsaved=false,evidenceModal=null}){
 if(otherUnsaved||draftSync?.busy||draftSync?.next||draftSync?.error||draftSync?.suspended||review?.error)return true;
 if(evidenceModal){try{const registration=evidenceModal.registration();if(evidenceModal.entry?.mode!=='evidence'||evidenceModal.current()!==true||registration?.entry!==evidenceModal.entry||typeof registration?.guard!=='function'||registration.guard!==evidenceModal.guard()||typeof registration.canUnload!=='function'||registration.canUnload()!==true)return true;}catch{return true;}}
 if(!review?.pending)return !!review?.busy;
 try{
  if(!account||['workspace_id','username','user_id'].some(field=>typeof account[field]!=='string'||!account[field])||!['admin','operator','viewer'].includes(account.role))return true;
  const expected='ga-setup:'+account.username+':'+account.workspace_id;
  if(!draftSync||draftKey!==expected||draftSync.key!==expected)return true;
  const original=readReviewJournal(storage,reviewJournalKey(account));
  return !original||stableStringify(original)!==stableStringify(review.pending);
 }catch{return true;}
}
