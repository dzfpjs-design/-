const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const hash=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
const bad={applicable:true,valid:false,reviewAllowed:false,grounding:null};
const counts=items=>{const found=new Map();for(const item of items)found.set(item,(found.get(item)||0)+1);return found;};

// The server owns tokenization and authorization. Here we bind its explanation
// to this saved question, exact parameters, and source spans for human review.
export function queryGroundingState(row){
 if(row?.wire_contract_version!==6)return {applicable:false,valid:true,reviewAllowed:true,grounding:null};
 const value=row.source_grounding,question=row.original_question;
 if(!object(value)||value.schema!=='native-query-grounding-v1'||value.tokenizer_version!=='zh_character_bigram_en_word_v1'||value.semantic_equivalence_verified!==false||!hash(value.raw_hash)||!hash(value.question_hash)||value.question_hash!==row.question_hash||typeof question!=='string'||!question.trim()||!Array.isArray(value.steps)||!Array.isArray(value.diagnostics)||!Number.isSafeInteger(value.blocker_count)||value.blocker_count<0||value.blocker_count!==value.diagnostics.length)return bad;
 const candidate=row.candidate,steps=candidate?.tool_steps,spans=row.claim_spans;
 if(!object(candidate)||!['candidate','needs_clarification'].includes(candidate.status)||!Array.isArray(steps)||!steps.every(step=>object(step)&&typeof step.id==='string'&&step.id&&typeof step.capability_id==='string'&&object(step.parameters))||!Array.isArray(spans)||!Array.isArray(row.catalog?.query_tools)||!row.catalog.query_tools.every(tool=>object(tool)&&typeof tool.id==='string'&&['keyword','fulltext','aggregate'].includes(tool.method)))return bad;
 const ids=new Set(steps.map(step=>step?.id));
 if(ids.size!==steps.length)return bad;
 if(value.diagnostics.some(item=>!object(item)||typeof item.code!=='string'||!item.code||typeof item.message!=='string'||!item.message.trim()||item.step_id!==null&&!ids.has(item.step_id)))return bad;
 const fulltext=steps.filter(step=>row.catalog.query_tools.find(tool=>tool.id===step.capability_id)?.method==='fulltext');
 if(value.steps.length!==fulltext.length)return bad;
 const characters=Array.from(question);
 for(let index=0;index<fulltext.length;index++){
  const actual=fulltext[index],proof=value.steps[index];
  if(typeof actual.parameters.field!=='string'||!actual.parameters.field||!['all_terms','any_terms'].includes(actual.parameters.match_mode)||!Number.isSafeInteger(actual.parameters.top_k)||actual.parameters.top_k<1||actual.parameters.top_k>20||!Number.isFinite(actual.parameters.min_score)||actual.parameters.min_score<0)return bad;
  if(!object(proof)||proof.id!==actual.id||proof.method!=='fulltext'||proof.text!==actual.parameters?.text||typeof proof.text!=='string'||typeof proof.quote!=='string'||!['supported','blocked'].includes(proof.status)||!Array.isArray(proof.query_tokens)||!proof.query_tokens.every(token=>typeof token==='string'&&token.length>0)||!Array.isArray(proof.tokens)||!Array.isArray(proof.diagnostic_codes)||!proof.diagnostic_codes.every(code=>typeof code==='string'&&code.length>0))return bad;
  const expected=counts(proof.query_tokens),seen=new Set();
  for(const item of proof.tokens){
   if(!object(item)||typeof item.token!=='string'||seen.has(item.token)||!Number.isSafeInteger(item.required_count)||item.required_count<=0||expected.get(item.token)!==item.required_count||!Number.isSafeInteger(item.source_count)||item.source_count<0)return bad;
   seen.add(item.token);
  }
  if(seen.size!==expected.size)return bad;
  if(proof.status==='supported'){
   const claim=spans.filter(item=>item?.kind==='filter'&&item.key===actual.id);
   if(!proof.query_tokens.length||proof.diagnostic_codes.length||!Number.isSafeInteger(proof.start)||!Number.isSafeInteger(proof.end)||proof.start<0||proof.end<=proof.start||proof.end>characters.length||characters.slice(proof.start,proof.end).join('')!==proof.quote||question.indexOf(proof.quote)!==question.lastIndexOf(proof.quote)||claim.length!==1||claim[0].quote!==proof.quote||claim[0].start!==proof.start||claim[0].end!==proof.end||proof.tokens.some(item=>item.source_count<item.required_count))return bad;
  }else if(!proof.diagnostic_codes.length||!value.blocker_count)return bad;
 }
 return {applicable:true,valid:true,reviewAllowed:value.blocker_count===0&&value.steps.every(step=>step.status==='supported'),grounding:value};
}
