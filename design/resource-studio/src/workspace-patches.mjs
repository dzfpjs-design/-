// Merge only entries changed by this editor. A late receipt cannot restore stale sibling drafts.
const collections=['localRuleDrafts','mqttGatewayDrafts','mqttBindingDrafts'];
export function mergeWorkspacePatch(current,baseline,patch){
 const next={...current,...patch};
 for(const key of collections)if(Object.hasOwn(patch,key)){
  const changes=Object.fromEntries(Object.entries(patch[key]||{}).filter(([id,value])=>JSON.stringify(value)!==JSON.stringify(baseline?.[key]?.[id])));
  next[key]={...current?.[key],...changes};
 }
 return next;
}
