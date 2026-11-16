import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {mount} from '../scripts/react-event-harness.mjs';
import {evidenceManualValidationView} from '../src/report-evidence-manual-v5.mjs';

// Explicitly synthetic HUMAN inputs. The callbacks below model local draft
// persistence, never a model answer, server save, permission or source proof.
const copy=value=>structuredClone(value),H='a'.repeat(64),reason='合成测试尚需核对';
const evidence=()=>({status:'unknown',citations:[],explanation:reason});
const step=name=>({name,purpose:'合成步骤目的',agent:'业务智能体',role:'',output:'合成输出',branch_condition:'',citations:[],inference_reason:reason,next_index:null,else_index:null});
function manual(){return {schema:'report-evidence-manual-review-v5',note:'保留原私人说明',result:{schema:'report-evidence-candidate-wire-v5',source_hash:H,catalog_hash:H,domain:'公开合成测试',global_claims:[],objects:[],relationships:[],businesses:[{operation:'upsert',original_name:'',name:'合成业务',trigger:'合成人工启动',output:'合成核对结果',steps:[step('先前核查'),step('后续整理')],citations:[],inference_reason:reason,participants:[{agent:'合成参与甲',responsibility:'合成甲职责',evidence:evidence()},{agent:'合成参与乙',responsibility:'合成乙职责',evidence:evidence()}],interactions:[],conditions:[],coordination:[]}],questions:[reason]}};}
const text=value=>Array.isArray(value)?value.map(text).join(''):value&&typeof value==='object'?text(value.props?.children):['string','number'].includes(typeof value)?String(value):'';
function nodes(value,result=[]){if(Array.isArray(value))value.forEach(item=>nodes(item,result));else if(value&&typeof value==='object'&&value.props){result.push(value);nodes(value.props.children,result);}return result;}
const memory=()=>{const map=new Map();return {getItem:key=>map.get(key)??null,setItem:(key,value)=>map.set(key,value),removeItem:key=>map.delete(key)};};
const require=createRequire(import.meta.url),{build}=createRequire(import.meta.resolve('vite'))('esbuild'),modules=new Map();
async function load(name){if(!modules.has(name)){const built=await build({entryPoints:[fileURLToPath(new URL('../src/'+name+'.jsx',import.meta.url))],bundle:true,write:false,platform:'node',format:'esm',loader:{'.css':'empty'},logLevel:'silent',plugins:[{name:'react',setup(b){b.onResolve({filter:/^react(?:\/.*)?$/},args=>({path:pathToFileURL(require.resolve(args.path)).href,external:true}));}}]});modules.set(name,await import('data:text/javascript;base64,'+Buffer.from(built.outputFiles[0].text).toString('base64')));}return modules.get(name);}
async function environment(run){const old=Object.fromEntries(['fetch','localStorage','sessionStorage'].map(key=>[key,globalThis[key]])),mounted=[],writes=[];globalThis.localStorage=memory();globalThis.sessionStorage=memory();globalThis.fetch=async()=>assert.fail('manual structure edits must never perform HTTP');try{await run({mounted,writes});}finally{mounted.forEach(ui=>ui.unmount());for(const [key,value] of Object.entries(old)){if(value===undefined)delete globalThis[key];else globalThis[key]=value;}}}
async function fixture(env,kind='business',extra={}){
 const state={persisted:null,joined:[],failDraft:false,failJoin:false},props={value:manual(),kind,businessIndex:kind==='business'?undefined:0,scopeKey:'synthetic-owner:workspace:draft:model',writable:true,busy:false,formDraft:null,Btn:'button',...extra};
 props.onFormDraft=next=>{if(state.failDraft)throw Error('合成本机保存失败');state.persisted=copy(next);props.formDraft=copy(next);env.writes.push(copy(next));return true;};
 props.onStructure=next=>{if(state.failJoin)throw Error('合成加入失败，输入保留');evidenceManualValidationView(next);state.joined.push(copy(next));props.value=next;props.formDraft=null;state.persisted=null;return true;};
 const Form=(await load('EvidenceStructureAddForm')).default,ui=mount(Form,props);env.mounted.push(ui);return {ui,props,state,Form};
}
const label=(title,field)=>title+'：'+field;
async function input(ui,title,field,value){await ui.input(label(title,field),value);}
async function explain(ui,title,value='  合成人工补充需核对  '){await input(ui,title,'补充依据与待核事项',value);}

test('human business form persists partial input and appends a whole manual value with pending reason and empty objects',()=>environment(async env=>{
 const {ui,props,state}=await fixture(env),original=copy(props.value);await ui.click('补充业务');assert.equal(state.persisted.key,'business:new');assert.equal(state.persisted.business_index,null);assert.deepEqual(state.persisted.base_value,original);
 await input(ui,'补充业务','业务名称','另一项合成业务');await explain(ui,'补充业务');assert.equal(state.persisted.fields.name,'另一项合成业务');await ui.click('加入人工稿，仍待核对');
 assert.equal(state.joined.length,1);const next=state.joined[0];assert.equal(next.result.businesses.at(-1).name,'另一项合成业务');assert.equal(next.result.businesses.at(-1).trigger,'');assert.deepEqual(next.result.businesses.at(-1).steps,[]);assert.deepEqual(next.result.objects,[]);assert.equal(next.result.questions.at(-1),'  合成人工补充需核对  ');assert.equal(next.note,original.note);assert.deepEqual(next.result.businesses[0],original.result.businesses[0]);assert.equal(props.formDraft,null);assert.match(text(ui.render()),/仍需核对并保存服务器回执/);
}));

test('step destination is initially unselected and explicit end or existing named step never rewires old steps',async()=>{
 for(const [chosen,expected] of [['end',null],['0',0]])await environment(async env=>{
  const {ui,props,state}=await fixture(env,'step'),oldSteps=copy(props.value.result.businesses[0].steps);await ui.click('补充步骤');assert.equal(ui.byAria(label('补充步骤','完成后去向')).props.value,'');
  for(const [key,value] of [['步骤名称','合成新增步骤'],['步骤目的','合成目的'],['步骤产出','合成待核结果'],['负责智能体','人工确认'],['负责角色','合成核查人']])await input(ui,'补充步骤',key,value);await explain(ui,'补充步骤');
  await ui.click('加入人工稿，仍待核对');assert.equal(state.joined.length,0);assert.match(text(ui.render()),/请明确选择完成后的去向/);assert.equal(state.persisted.fields.next_index,'');assert.match(text(ui.byAria(label('补充步骤','完成后去向'))),/先前核查/);
  await input(ui,'补充步骤','完成后去向',chosen);await ui.click('加入人工稿，仍待核对');const steps=state.joined[0].result.businesses[0].steps;assert.deepEqual(steps.slice(0,2),oldSteps);assert.equal(steps[2].next_index,expected);assert.equal(steps[2].role,'合成核查人');assert.deepEqual(steps[2].citations,[]);assert.equal(steps[2].inference_reason,'  合成人工补充需核对  ');
 });
});

test('participant and directional interaction use human names, selected local steps and unknown evidence without generated identities',()=>environment(async env=>{
 const person=await fixture(env,'participant');await person.ui.click('补充参与方');await input(person.ui,'补充参与方','参与方称谓','合成补充参与方');await input(person.ui,'补充参与方','参与职责','合成人工补充职责');await explain(person.ui,'补充参与方');await person.ui.click('加入人工稿，仍待核对');const added=person.state.joined[0].result.businesses[0].participants.at(-1);assert.equal(added.agent,'合成补充参与方');assert.deepEqual(added.evidence,{status:'unknown',citations:[],explanation:'  合成人工补充需核对  '});assert.equal(Object.hasOwn(added,'key'),false);
 const interaction=await fixture(env,'interaction');await interaction.ui.click('补充互动');assert.match(text(interaction.ui.byAria(label('补充互动','由谁发起'))),/合成参与甲.*合成参与乙/);await input(interaction.ui,'补充互动','由谁发起','0');await input(interaction.ui,'补充互动','交给谁','1');await input(interaction.ui,'补充互动','互动目的','合成信息交接目的');await interaction.ui.input('补充互动：涉及步骤 2 后续整理',true);await explain(interaction.ui,'补充互动');await interaction.ui.click('加入人工稿，仍待核对');const item=interaction.state.joined[0].result.businesses[0].interactions[0];assert.equal(item.from_participant_index,0);assert.equal(item.to_participant_index,1);assert.deepEqual(item.step_indices,[1]);assert.equal(item.condition,'');assert.equal(item.evidence.status,'unknown');
}));

test('conditions and parallel coordination require explicit categories and retain the shared pending reason',async()=>{
 for(const kind of ['condition','coordination'])await environment(async env=>{
  const title=kind==='condition'?'补充条件':'补充协同',{ui,state}=await fixture(env,kind);await ui.click(title);await explain(ui,title);await ui.input(title+'：涉及步骤 1 先前核查',true);
  if(kind==='condition'){await input(ui,title,'条件类别','stop');await input(ui,title,'条件内容','合成停止条件');}
  else{await input(ui,title,'协同关系','parallel');await ui.click('加入人工稿，仍待核对');assert.equal(state.joined.length,0);assert.match(text(ui.render()),/至少两个不同已有步骤/);await ui.input(title+'：涉及步骤 2 后续整理',true);}
  await ui.click('加入人工稿，仍待核对');const result=state.joined[0].result,item=result.businesses[0][kind==='condition'?'conditions':'coordination'][0];assert.equal(item.kind,kind==='condition'?'stop':'parallel');assert.equal(item.evidence.status,'unknown');assert.deepEqual(item.evidence.citations,[]);assert.equal(item.evidence.explanation,result.questions.at(-1));
 });
});

test('invalid input remains persisted across remount and cancellation needs an explicit inline discard',()=>environment(async env=>{
 const {ui,props,state,Form}=await fixture(env);await ui.click('补充业务');await input(ui,'补充业务','业务名称','保留这份补充');await ui.click('加入人工稿，仍待核对');assert.equal(state.joined.length,0);assert.match(text(ui.render()),/补充理由/);const saved=copy(state.persisted);ui.unmount();props.formDraft=copy(saved);const restored=mount(Form,props);env.mounted.push(restored);assert.equal(restored.byAria(label('补充业务','业务名称')).props.value,'保留这份补充');await restored.click('取消本次补充');assert.ok(restored.byAria('确认放弃补充业务'));assert.deepEqual(state.persisted,saved);await restored.click('继续填写');assert.equal(restored.byAria(label('补充业务','业务名称')).props.value,'保留这份补充');await restored.click('取消本次补充');await restored.click('确认放弃本次填写');assert.equal(state.persisted,null);assert.equal(state.joined.length,0);
}));

test('draft persistence and append errors preserve all fields, and failed cancel cannot clear the stored draft',()=>environment(async env=>{
 const {ui,props,state}=await fixture(env);await ui.click('补充业务');state.failDraft=true;await input(ui,'补充业务','业务名称','末次输入不丢');assert.equal(ui.byAria(label('补充业务','业务名称')).props.value,'末次输入不丢');assert.match(text(ui.render()),/末次填写尚未可靠保存/);state.failDraft=false;await ui.click('重试保护补充输入');assert.equal(state.persisted.fields.name,'末次输入不丢');await explain(ui,'补充业务');state.failJoin=true;await ui.click('加入人工稿，仍待核对');assert.equal(state.joined.length,0);assert.equal(state.persisted.fields.name,'末次输入不丢');assert.equal(props.formDraft.fields.reason,'  合成人工补充需核对  ');state.failDraft=true;await ui.click('取消本次补充');await ui.click('确认放弃本次填写');assert.equal(ui.byAria(label('补充业务','业务名称')).props.value,'末次输入不丢');assert.equal(state.persisted.fields.name,'末次输入不丢');
}));

test('a changed manual base blocks append until explicit persisted rebase, retaining fields and newer human edits',()=>environment(async env=>{
 const {ui,props,state}=await fixture(env);await ui.click('补充业务');await input(ui,'补充业务','业务名称','追加合成业务');await explain(ui,'补充业务');const oldBase=copy(state.persisted.base_value);props.value={...copy(props.value),note:'后来修改的私人说明'};ui.render();assert.equal(ui.button('加入人工稿，仍待核对').props.disabled,true);await ui.click('加入人工稿，仍待核对');assert.equal(state.joined.length,0);assert.deepEqual(state.persisted.base_value,oldBase);await ui.click('按当前人工稿重新核对补充');assert.equal(state.persisted.fields.name,'追加合成业务');assert.equal(state.persisted.base_value.note,'后来修改的私人说明');await ui.click('加入人工稿，仍待核对');assert.equal(state.joined[0].note,'后来修改的私人说明');
}));

test('viewer, busy, raw-original and active-other-form gates cannot append; question capacity keeps the original value',async()=>{
 for(const patch of [{writable:false},{busy:true},{value:{...manual(),schema:'unverified-original'}},{formDraft:{key:'participant:0',kind:'participant',business_index:0,base_value:manual(),fields:{agent:'保留参与方',responsibility:'保留职责',reason}}}])await environment(async env=>{const {ui,state}=await fixture(env,'business',patch);assert.equal(ui.button('补充业务').props.disabled,true);await ui.click('补充业务');assert.equal(state.joined.length,0);if(patch.formDraft)assert.match(text(ui.render()),/先完成或明确放弃当前补充/);});
 await environment(async env=>{const value=manual();value.result.questions=Array.from({length:30},(_,i)=>'合成待核问题'+i);const original=copy(value),{ui,props,state}=await fixture(env,'business',{value});assert.equal(ui.button('补充业务').props.disabled,true);assert.match(text(ui.render()),/待核问题已达30条/);await ui.click('补充业务');assert.equal(state.joined.length,0);assert.deepEqual(props.value,original);});
});

test('a saved stale handler cannot write after scope change and retains the original controlled draft',()=>environment(async env=>{
 const {ui,props,state}=await fixture(env);await ui.click('补充业务');await input(ui,'补充业务','业务名称','原账号填写');await explain(ui,'补充业务');const apply=ui.button('加入人工稿，仍待核对').props.onClick,saved=copy(state.persisted),writes=env.writes.length;props.scopeKey='another-owner:another-draft';assert.equal(ui.render(),null);await apply();assert.equal(state.joined.length,0);assert.deepEqual(state.persisted,saved);assert.equal(env.writes.length,writes);
}));

test('actual editor exposes all six typed actions through onStructure and keeps raw original action controls disabled',()=>environment(async env=>{
 const {EvidenceCandidateEditor}=await load('ReportEvidenceReadingDialog'),value=manual(),account={user_id:'synthetic-actor',workspace_id:'synthetic-workspace',role:'operator'},row={id:'synthetic-draft',model_result_hash:H,preview:{},reduction:{preview:{reduction_preparation:{payload:{spans:[]}}}}};
 const props={value,row,catalog:{index:{}},account,Btn:'button',writable:true,structureWritable:true,formDraft:null,onFormDraft(){},onStructure(){},onEdit(){assert.fail('append must not use the old scalar edit path');}},tree=EvidenceCandidateEditor(props),forms=nodes(tree).filter(node=>node.type?.name==='EvidenceStructureAddForm');assert.deepEqual(forms.map(node=>node.props.kind),['business','step','participant','interaction','condition','coordination']);assert.ok(forms.every(node=>node.props.writable&&node.props.onStructure===props.onStructure));
 const raw=EvidenceCandidateEditor({...props,value:{...value,schema:'immutable-original'},writable:false}),rawForms=nodes(raw).filter(node=>node.type?.name==='EvidenceStructureAddForm');assert.equal(rawForms.length,6);assert.ok(rawForms.every(node=>!node.props.writable&&node.props.formDraft===null&&node.props.onStructure===undefined));
 const formDraft={key:'step:0',kind:'step',business_index:0,base_value:copy(value),fields:{name:'尚未加入的原步骤',purpose:'合成保留目的',output:'合成保留产出',agent:'业务智能体',role:'',next_index:'',reason}},changed=copy(value);changed.result.businesses=[];
 const orphanTree=EvidenceCandidateEditor({...props,value:changed,formDraft}),orphan=nodes(orphanTree).find(node=>node.type?.name==='EvidenceStructureAddForm'&&node.props.kind==='step');assert.ok(orphan,'retained input stays visible when its old business is absent');const ui=mount(orphan.type,orphan.props);env.mounted.push(ui);assert.equal(ui.byAria('补充步骤：步骤名称').props.value,'尚未加入的原步骤');assert.match(text(ui.render()),/人工稿已有变化/);assert.match(text(ui.render()),/本次补充原属业务：合成业务/);assert.equal(ui.button('加入人工稿，仍待核对').props.disabled,true);
}));

function pathTo(value,predicate,ancestors=[]){
 if(Array.isArray(value)){for(const child of value){const found=pathTo(child,predicate,ancestors);if(found)return found;}return null;}
 if(value&&typeof value==='object'&&value.props){const path=[...ancestors,value];return predicate(value)?path:pathTo(value.props.children,predicate,path);}
 return null;
}
test('restored forms in the second business have every actual editor details ancestor open, including all four collections',()=>environment(async()=>{
 const {EvidenceCandidateEditor}=await load('ReportEvidenceReadingDialog'),value=manual();value.result.businesses.push({...copy(value.result.businesses[0]),name:'第二项合成业务'});
 const fields={step:{name:'保留步骤',purpose:'',output:'',agent:'',role:'',next_index:'',reason},participant:{agent:'保留参与方',responsibility:'',reason},interaction:{from_participant_index:'',to_participant_index:'',purpose:'',condition:'',step_indices:[],reason},condition:{kind:'',statement:'保留条件',step_indices:[],reason},coordination:{kind:'',start_rule:'保留开始说明',wait_rule:'',join_rule:'',step_indices:[],reason}};
 const collections={participant:'参与方与职责',interaction:'有方向的互动',condition:'开始、停止与例外条件',coordination:'顺序、分支、并行及汇合'};
 const account={user_id:'synthetic-actor',workspace_id:'synthetic-workspace',role:'operator'},row={id:'synthetic-draft',model_result_hash:H,preview:{}};
 for(const kind of ['step','participant','interaction','condition','coordination']){
  const formDraft={key:kind+':1',kind,business_index:1,base_value:copy(value),fields:copy(fields[kind])};
  // A new editor render uses only restored controlled data, without browser
  // details state from before refresh or mounting the child outside its parent.
  const tree=EvidenceCandidateEditor({value:copy(value),row,catalog:{index:{}},account,Btn:'button',writable:true,structureWritable:true,formDraft:copy(formDraft),onFormDraft(){},onStructure(){},onEdit(){}});
  const path=pathTo(tree,node=>node.type?.name==='EvidenceStructureAddForm'&&node.props.kind===kind&&node.props.businessIndex===1);assert.ok(path,kind+' retained form exists');
  const details=path.filter(node=>node.type==='details');assert.equal(details.length,kind==='step'?1:2);assert.ok(details.every(node=>node.props.open===true),kind+' is not hidden behind a closed ancestor');assert.match(text(details[0].props.children[0]),/业务2：第二项合成业务/);
  if(collections[kind])assert.ok(text(details[1].props.children[0]).startsWith(collections[kind]));
  for(const other of Object.values(collections).filter(title=>title!==collections[kind])){const section=nodes(details[0]).find(node=>node.type==='details'&&text(node.props.children?.[0]).startsWith(other));assert.ok(section);assert.equal(section.props.open,false,'unrelated collection stays folded');}
  assert.deepEqual(path.at(-1).props.formDraft,formDraft);
 }
}));

test('the status used by the panel never claims local protection when persistence failed',()=>environment(async()=>{
 const {evidenceManualSaveStatus}=await load('ReportEvidenceReadingDialog');
 for(const dirty of [true,false]){const status=evidenceManualSaveStatus({dirty,localSaved:false});assert.match(status,/仅保留在本页内存.*尚未可靠保存.*请勿刷新或退出/);assert.doesNotMatch(status,/已在本机保护|已读取服务器保存稿/);}
 assert.match(evidenceManualSaveStatus({dirty:true,localSaved:true}),/已在本机保护.*尚未全部保存到服务器/);assert.match(evidenceManualSaveStatus({dirty:false,localSaved:true}),/已读取服务器保存稿，仍需人工核对/);
}));

test('recovery rows distinguish each retained structure by kind, original business and entered human content without owner IDs',()=>environment(async()=>{
 const {evidenceWorkingDraftLabel}=await load('ReportEvidenceReadingDialog'),base=manual(),current=manual();current.result.businesses[0].name='后来更改的业务';
 const cases=[['business','补充业务',{name:'新增合成业务',reason:''},'新增合成业务'],['step','补充步骤',{name:'合成新增步骤',purpose:'',output:'',agent:'',role:'',next_index:'',reason:''},'合成新增步骤'],['participant','补充参与方',{agent:'合成补充参与方',responsibility:'',reason:''},'合成补充参与方'],['interaction','补充互动',{from_participant_index:'',to_participant_index:'',purpose:'合成资料交接目的',condition:'',step_indices:[],reason:''},'合成资料交接目的'],['condition','补充条件',{kind:'',statement:'合成停止条件待核',step_indices:[],reason:''},'合成停止条件待核'],['coordination','补充协同',{kind:'parallel',start_rule:'合成并行开始说明',wait_rule:'',join_rule:'',step_indices:[],reason:''},'合成并行开始说明']],labels=[];
 for(const [kind,title,fields,entered] of cases){const saved={owner:'internal-owner-secret',journal_key:'internal-storage-key',dirty:true,pending:null,value:current,structure_form:{key:kind+':'+(kind==='business'?'new':0),kind,business_index:kind==='business'?null:0,base_value:base,fields}},before=copy(saved),label=evidenceWorkingDraftLabel(saved);labels.push(label);assert.ok(label.startsWith(title+' · '));assert.ok(label.includes(kind==='business'?'新增业务':'合成业务'));assert.ok(label.includes(entered));assert.doesNotMatch(label,/internal-owner-secret|internal-storage-key|后来更改的业务|source_hash|schema/);assert.deepEqual(saved,before);}
 assert.equal(new Set(labels).size,6);
 const partial={dirty:true,structure_form:{key:'step:0',kind:'step',business_index:0,base_value:base,fields:{name:'',purpose:'已填写的合成目的',output:'',agent:'',role:'',next_index:'',reason:''}}};assert.match(evidenceWorkingDraftLabel(partial),/补充步骤 · 合成业务 · 已填写的合成目的/);
 const categoryOnly={dirty:true,structure_form:{key:'coordination:0',kind:'coordination',business_index:0,base_value:base,fields:{kind:'parallel',start_rule:'',wait_rule:'',join_rule:'',step_indices:[],reason:''}}};assert.match(evidenceWorkingDraftLabel(categoryOnly),/补充协同 · 合成业务 · 并行（内容尚未填完）/);
 assert.equal(evidenceWorkingDraftLabel({dirty:true,structure_form:null,pending:null}),'人工稿待保存');assert.equal(evidenceWorkingDraftLabel({dirty:true,structure_form:null,pending:{kind:'review',request_id:'do-not-display'}}),'原请求待核对');
}));
