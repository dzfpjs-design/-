// Current evidence and historical suggestions must never be interchangeable.
import {stableStringify} from './content-signature.js';
const STEP_FACTS=['name','purpose','agent','role','output','condition','nextId','elseId'];
const FLOW_FACTS=['trigger','output'];
const LIMIT=20;
const clone=value=>JSON.parse(JSON.stringify(value));
const same=(a,b)=>stableStringify(a)===stableStringify(b);
const refs=item=>Array.isArray(item?.citations)?item.citations:[];
const referenceKeys=item=>[...new Set(refs(item).map(ref=>stableStringify(['material_id','block_id','quote'].map(key=>ref?.[key]))))].sort();
const onlyOldRefs=(previous,item)=>{const old=new Set(referenceKeys(previous));return referenceKeys(item).every(key=>old.has(key));};
const explanation=item=>typeof item?.inferenceReason==='string'?item.inferenceReason:'';
const text=(item,key)=>typeof item?.[key]==='string'?item[key]:'';
const protectedBasis=item=>refs(item).length>0||!!explanation(item);
function snapshot(item,scope){return {scope,reason:'人工修改前的依据，仅供回查，不证明当前值',values:Object.fromEntries((scope==='step'?STEP_FACTS:FLOW_FACTS).map(key=>[key,text(item,key)])),sourceId:text(item,'sourceId'),citation:text(item,'citation'),citations:clone(refs(item)),inferenceReason:explanation(item)};}
function history(previous,item,extra){
 const rows=[];
 for(const row of [...(Array.isArray(previous?.basisHistory)?previous.basisHistory:[]),...(Array.isArray(item.basisHistory)?item.basisHistory:[]),...extra])if(row&&typeof row==='object'&&!Array.isArray(row)&&!rows.some(old=>same(old,row)))rows.push(clone(row));
 const omitted=Math.max(Number.isSafeInteger(previous?.basisHistoryOmitted)?previous.basisHistoryOmitted:0,Number.isSafeInteger(item.basisHistoryOmitted)?item.basisHistoryOmitted:0,0)+Math.max(0,rows.length-LIMIT);
 if(rows.length)item.basisHistory=rows.length>LIMIT?[rows[0],...rows.slice(-(LIMIT-1))]:rows;
 if(omitted)item.basisHistoryOmitted=omitted;
}
function synchronizeItem(previous,item,scope){
 if(!previous)return item;
 const facts=scope==='step'?STEP_FACTS:FLOW_FACTS;
 const sourceChanged=scope==='step'&&text(previous,'sourceId')!==text(item,'sourceId');
 const manualChanged=sourceChanged||scope==='step'&&text(previous,'citation')!==text(item,'citation');
 const factsChanged=facts.some(key=>text(previous,key)!==text(item,key));
 // Fresh references supplied by a reviewed model edit are retained. Repeating
 // the old references after a human edit does not rebind them to the new facts.
 const staleRefs=(manualChanged||factsChanged)&&refs(item).length>0&&onlyOldRefs(previous,item);
 const staleExplanation=(manualChanged||factsChanged)&&!!explanation(item)&&explanation(previous)===explanation(item);
 const withdrew=protectedBasis(previous)&&!protectedBasis(item);
 const archive=staleRefs||staleExplanation||withdrew||sourceChanged&&(text(previous,'citation')||protectedBasis(previous));
 history(previous,item,archive?[snapshot(previous,scope)]:[]);
 if(staleRefs)item.citations=[];
 if(staleExplanation)item.inferenceReason='';
 if(scope==='step'&&text(previous,'citation')===text(item,'citation')&&(sourceChanged||staleRefs||staleExplanation))item.citation=refs(item).length?refs(item).map(ref=>ref.block_id+'：'+ref.quote).join('；'):'';
 if(scope==='step'&&factsChanged&&!manualChanged&&(staleRefs||staleExplanation)&&!refs(item).length)item.sourceId='';
 if((staleRefs||staleExplanation||withdrew)&&!protectedBasis(item))item.basisKind='manual';
 if(protectedBasis(item)&&item.basisKind==='manual')delete item.basisKind;
 return item;
}
export function synchronizeFlowBasis(previous,proposed){
 const value=clone(proposed);
 synchronizeItem(previous,value,'business');
 const oldSteps=new Map((previous?.steps||[]).map(item=>[item.id,item]));
 value.steps=(value.steps||[]).map(item=>synchronizeItem(oldSteps.get(item.id),item,'step'));
 return value;
}
export function retireFlowBasis(item,scope='step'){
 const value=clone(item);history(item,value,[snapshot(item,scope)]);
 value.citations=[];value.inferenceReason='';value.basisKind='manual';
 if(scope==='step'){value.sourceId='';value.citation='';}
 return value;
}
