// A refreshed window is rebuilt from one new server head and only that head's opaque cursor chain.
// The API validates cursor authority; contiguous indices here detect incomplete display data.
const PAGE_STEP=20,API_LIMIT=100,MAX_UNITS=500;
const sameScope=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
const invalid=()=>{throw Error('页清单缺失、重复或分页范围已变化，已展开清单与人工稿保留；请重试刷新。');};

export class OCRPageWindow {
 constructor(){this.state=null;}
 isCurrent(value){return !!value&&this.state?.value===value;}
 async load({scope,version,readPage,isCurrent,more=false}){
  if(!isCurrent())return null;
  if(!Number.isSafeInteger(scope.total)||scope.total<0||scope.total>MAX_UNITS||!Number.isSafeInteger(version)||version<1)invalid();
  if(!this.state||!sameScope(this.state.scope,scope))this.state={scope:structuredClone(scope),wanted:PAGE_STEP,value:null,flight:null};
  const state=this.state,current=()=>this.state===state&&isCurrent();
  if(!current())return null;
  if(more)state.wanted=Math.min(scope.total,Math.max(state.wanted,state.value?.items.length||0)+PAGE_STEP);
  for(;;){
   if(!current())return null;
   if(!state.flight){
    const flight=this.readWindow(state,version,readPage,current).then(value=>{if(value&&current())state.value=value;return value;});
    state.flight=flight;
    flight.then(()=>{if(state.flight===flight)state.flight=null;},()=>{if(state.flight===flight)state.flight=null;});
   }
   const value=await state.flight;
   if(!value||!current())return null;
   if(value.read_version<version||value.items.length<Math.min(state.wanted,value.total))continue;
   return state.value;
  }
 }
 async readWindow(state,version,readPage,current){
  const items=[],ids=new Set(),cursors=new Set(),prior=state.value;let after=null,upper=null,next=null,calls=0;
  do{
   if(!current())return null;
   const limit=Math.min(API_LIMIT,Math.max(1,state.wanted-items.length));
   if(++calls>Math.ceil(MAX_UNITS/PAGE_STEP))invalid();
   const response=await readPage({after,limit});
   if(!current())return null;
   if(response?.material_id!==state.scope.material_id||response.run_id!==state.scope.run_id||response.generation!==state.scope.generation||!Array.isArray(response.items)||!Number.isSafeInteger(response.total)||response.total<0||response.total>state.scope.total)invalid();
   if(upper===null){upper=response.total;if(prior&&upper<prior.total)invalid();}else if(response.total!==upper)invalid();
   if(response.items.length!==Math.min(limit,upper-items.length))invalid();
   for(const row of response.items){
    const index=items.length+1,old=prior?.items[index-1];
    if(!row||typeof row.id!=='string'||!row.id||row.index!==index||!Number.isSafeInteger(row.version)||row.version<1||ids.has(row.id)||old&&(old.id!==row.id||old.version>row.version))invalid();
    ids.add(row.id);items.push(row);
   }
   next=response.next_cursor;
   if(items.length<upper){if(typeof next!=='string'||!next||cursors.has(next))invalid();cursors.add(next);}else if(next!==null)invalid();
   after=next;
  }while(items.length<Math.min(state.wanted,upper));
  return {items,next_cursor:next,total:upper,read_version:version};
 }
}
