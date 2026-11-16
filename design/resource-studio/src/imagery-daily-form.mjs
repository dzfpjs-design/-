export function confirmImageryDetail(record,value){
 const saved=value?.record;
 if(!saved||saved.key!==record.key||saved.kind!==record.kind||saved.id!==record.id||JSON.stringify(saved.sources.map(s=>s.sha256))!==JSON.stringify(record.sources.map(s=>s.sha256)))throw Error('当前影像依据与选中的归集记录不一致，未显示。');
 for(const image of value.images||[]){
  const prefix=record.kind==='scene'?'/geo/scenes/'+record.id+'/preview':record.kind==='segmentation'?'/geo/runs/'+record.id+'/rgb':'/geo/pairs/'+record.id+'/artifacts/';
  if(!image.url.startsWith(prefix)||!/^([a-f0-9]{64})$/.test(image.sha256)||record.kind!=='comparison'&&image.url!==prefix||record.kind==='comparison'&&!['before','after','valid','candidate'].includes(image.url.slice(prefix.length)))throw Error('影像证据下载地址或摘要无效，未显示。');
 }
 return value;
}
