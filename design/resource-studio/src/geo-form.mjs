// Drafts are explicitly unsaved; confirmed configuration remains on the server.
export function savedForm(scene){
 const c=scene.configuration;
 return {version:scene.version,bands:c?.rgb_bands||scene.metadata.suggested_rgb_bands||['','',''],ranges:c?.ranges||[['',''],['',''],['','']],map:!!c?.map_image_confirmed,observed:c?.observed_at?new Date(new Date(c.observed_at).getTime()+8*3600000).toISOString().slice(0,16):'',originalTime:c?.observed_at||null,dirty:false};
}
export function draftKey(workspace,scene){return 'geo-rgb-draft:'+workspace+':'+scene;}
export function readDraft(storage,key,scene){
 try{const d=JSON.parse(storage.getItem(key));if(d?.dirty&&Number.isInteger(d.version)&&d.version>0&&Array.isArray(d.bands)&&d.bands.length===3&&Array.isArray(d.ranges)&&d.ranges.length===3&&d.ranges.every(r=>Array.isArray(r)&&r.length===2)&&typeof d.observed==='string'&&d.observed.length<=32)return d;}catch{}
 return savedForm(scene);
}
export function formPayload(form,needsRange){
 const displayed=form.originalTime?new Date(new Date(form.originalTime).getTime()+8*3600000).toISOString().slice(0,16):'';
 return {version:form.version,rgb_bands:form.bands.map(Number),ranges:needsRange?form.ranges.map(r=>r.map(Number)):null,map_image_confirmed:form.map,observed_at:form.observed===displayed?form.originalTime:form.observed?new Date(form.observed+'+08:00').toISOString():null};
}
