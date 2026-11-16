// Preserve the original single-source sequence (including duplicates) so old
// confirmations retain their exact signature. Additional citations extend it.
export function flowSourceIds(flow){
 const steps=flow.steps||[],ids=steps.map(step=>step.sourceId).filter(id=>typeof id==='string'&&id.length>0),seen=new Set(ids);
 function append(citations){for(const citation of citations||[]){const id=citation.material_id;if(typeof id==='string'&&id.length>0&&!seen.has(id)){ids.push(id);seen.add(id);}}}
 for(const step of steps)append(step.citations);
 append(flow.citations);
 return ids;
}
