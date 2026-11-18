import {stableStringify} from './content-signature.js';

// Names are presentation keys. Refuse to attribute a shared/cyclic alias to a business.
export function voiceEvidenceFor(config, business, names) {
 const aliases=config.businessAliases??{};
 if(!aliases||typeof aliases!=='object'||Array.isArray(aliases)||!Array.isArray(names)||!names.includes(business))return {refs:[],ambiguous:true};
 function chain(name){const seen=new Set();while(true){if(typeof name!=='string'||!name||seen.has(name))return null;seen.add(name);if(!Object.hasOwn(aliases,name))return seen;name=aliases[name];}}
 const target=chain(business);if(!target)return {refs:[],ambiguous:true};
 for(const name of names){if(name===business)continue;const other=chain(name);if(!other||[...other].some(x=>target.has(x)))return {refs:[],ambiguous:true};}
 if(config.voiceInputs!=null&&!Array.isArray(config.voiceInputs))return {refs:[],ambiguous:true};
 return {refs:(config.voiceInputs||[]).filter(r=>r&&r.target==='automation_instruction'&&target.has(r.business)&&typeof r.audio_run_id==='string'&&typeof r.request_id==='string'),ambiguous:false};
}
export async function readVoiceEvidence(ref,{readReceipt,readAudio,hashBytes}) {
 const result=await readReceipt(ref);
 if(!result?.receipt?.reference||stableStringify(result.receipt.reference)!==stableStringify(ref))throw Error('语音收据与当前配置中的依据不一致，已停止读取。');
 const blob=await readAudio(ref);
 if(await hashBytes(await blob.arrayBuffer())!==ref.input_sha256)throw Error('原音频摘要不一致，已停止播放。');
 return blob;
}
