import {businesses as seedBusinesses,withoutSecrets} from './catalog.js';
import {initializeBusinessPolicies} from './automation-policy-contract.mjs';
function invalid(message){const error=Error(message+' 原业务、资源与输入保留，请核对已保存配置后重试。');error.code='BUSINESS_CONFIG_INVALID';throw error;}
function list(object,key,label){if(!Object.hasOwn(object,key)||object[key]===undefined)return [];if(!Array.isArray(object[key]))invalid(label+'不是可核对的列表。');return object[key];}
export function businessNames(w){const items=Array.isArray(w.businesses)?w.businesses:seedBusinesses;return [...new Set(items.map(x=>typeof x==='string'?x:x?.name).filter(x=>typeof x==='string'&&x.trim()).map(x=>x.trim()))];}
export function changeBusinesses(w,rows,{allowProtectedManualRename=false}={}){
 if(!Array.isArray(rows)||rows.some(row=>!row||typeof row.name!=='string'||row.original!==undefined&&typeof row.original!=='string'))invalid('待保存的业务名称无法核对。');
 if(Object.hasOwn(w,'businesses')&&w.businesses!==undefined&&!Array.isArray(w.businesses))invalid('原业务清单不是可核对的列表。');if(Array.isArray(w.businesses)&&w.businesses.some(item=>typeof item!=='string'&&(!item||typeof item!=='object'||Array.isArray(item)||typeof item.name!=='string')))invalid('原业务清单含有无法核对的名称。');if(w.businessAliases!=null&&(typeof w.businessAliases!=='object'||Array.isArray(w.businessAliases)||Object.values(w.businessAliases).some(value=>typeof value!=='string')))invalid('业务改名关联无法核对。');
 const names=rows.map(x=>x.name.trim());if(names.some(x=>!x))throw Error('请填写业务名称，或移除空白业务。');if(new Set(names).size!==names.length)throw Error('业务名称不能重复。');if(names.some(x=>withoutSecrets({value:x}).value!==x))throw Error('业务名称中不能包含凭证。');
 const instructions=w.automationInstructions||{};if(typeof instructions!=='object'||Array.isArray(instructions))invalid('自动化修改要求无法核对。');const beforeNames=businessNames(w);
 const changed=beforeNames.filter(old=>!names.includes(old)&&typeof instructions[old]==='string'&&instructions[old].trim());let protectedRename=null;
 if(changed.length&&allowProtectedManualRename){
  const old=changed[0], matches=rows.filter(row=>row.original===old), target=matches[0]?.name.trim(), oldAliases=w.businessAliases||{}, policies=w.automation?.policies||{};
  const unchanged=beforeNames.filter(name=>name!==old), nextOthers=names.filter(name=>name!==target);
  if(changed.length!==1||matches.length!==1||!target||beforeNames.includes(target)||unchanged.length!==nextOthers.length||unchanged.some(name=>!nextOthers.includes(name))
    ||Object.hasOwn(instructions,target)||Object.hasOwn(oldAliases,target)||Object.values(oldAliases).includes(target)
    ||Object.hasOwn(w.flows||{},target)||Object.hasOwn(w.setupNotes||{},target)||Object.hasOwn(policies,target))
   throw Error('请单独改名一个业务，并使用没有历史说明或规则的新名称；原记录已保留。');
  protectedRename={from:old,to:target};
 }
 for(const old of beforeNames){const row=rows.find(item=>item.original===old||!item.original&&item.name.trim()===old);if((!row||row.name.trim()!==old)&&typeof instructions[old]==='string'&&instructions[old].trim()&&protectedRename?.from!==old)throw Error('“'+old+'”有已保存的自动化修改要求。请先复制或清空该要求并保存，再改名或移除；语音依据和历史版本保留。');}for(const row of rows){const name=row.name.trim();if(!beforeNames.includes(name)&&typeof instructions[name]==='string'&&instructions[name].trim())throw Error('“'+name+'”存在旧的自动化修改要求，暂不能复用此业务名称；请先核对历史记录。');}
 const resources=list(w,'resources','资源清单'),gaps=list(w,'gaps','待补充事项清单');
 for(const resource of resources){if(!resource||typeof resource!=='object'||Array.isArray(resource))invalid('资源清单含有无法核对的记录。');const bound=list(resource,'businesses','资源关联业务');if(bound.some(name=>typeof name!=='string'))invalid('资源关联业务含有无法核对的名称。');}
 if(gaps.some(gap=>!gap||typeof gap!=='object'||Array.isArray(gap)||gap.business!==undefined&&typeof gap.business!=='string'))invalid('待补充事项含有无法核对的业务记录。');
 const map=new Map(rows.filter(x=>x.original).map(x=>[x.original,x.name.trim()])),before=businessNames(w),aliases={...w.businessAliases};for(const row of rows)if(row.original&&row.original!==row.name.trim())aliases[row.name.trim()]=row.original;
 const automation=initializeBusinessPolicies(w.automation,rows.filter(row=>!row.original&&!before.includes(row.name.trim())).map(row=>row.name.trim()),aliases);
 return {...w,...(protectedRename?{automationInstructions:{...instructions,[protectedRename.to]:instructions[protectedRename.from]},_businessRenameIntent:protectedRename}:{}),...(automation===undefined?{}:{automation}),businesses:names,businessAliases:aliases,resources:resources.map(resource=>({...resource,businesses:[...new Set(list(resource,'businesses','资源关联业务').flatMap(name=>map.has(name)?[map.get(name)]:before.includes(name)&&!names.includes(name)?[]:[name]))]})),gaps:gaps.map(gap=>({...gap,business:map.get(gap.business)||gap.business}))};
}
