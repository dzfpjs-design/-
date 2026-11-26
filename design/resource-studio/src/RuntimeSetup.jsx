import {materialReadScope} from './material-list-merge.mjs';
import {savedConfigurationWire} from './configuration-numeric-wire.mjs';
import {adoptExecutionConfiguration} from './execution-configuration-adoption.mjs';
import ReportDraftDialog from './ReportDraftDialog';
import ReportFullReadingDialog from './ReportFullReadingDialog';
import ReportEvidenceReadingDialog from './ReportEvidenceReadingDialog';
import {evidenceReadingPending,verifyEvidenceReadingJournals} from './report-evidence-reading-session.mjs';
import {verifyEvidenceDraft} from './report-evidence-reading-contract.mjs';
import {fullReadingPending,verifyFullReadingJournals} from './report-full-reading-session.mjs';
import {verifyFullDraft} from './report-full-reading-contract.mjs';
import {reportPending} from './report-draft-paid-adapter.mjs';
import {verifyReportDraft} from './report-draft-contract.mjs';
import {ocrMaterialPresentation,readOCRMaterialStatus,newerOCRMaterials,materialStatusErrorMessage} from './material-ocr-contract.mjs';
import MaterialOCRReview from './MaterialOCRReview';
import DocumentCopyReview from './DocumentCopyReview';
import DocxStructureReview from './DocxStructureReview';
import DocxMaterialCharacterReview from './DocxMaterialCharacterReview';
import React,{useState,useEffect,useRef} from 'react';
import {checkedAuthoredConfigurationResult,imageReadbackFromSaved} from './authored-image-workflow-v243-protocol.mjs';
import {checkedDecisionConfigurationResult,decisionReadbackFromSaved} from './authored-decision-workflow-v286-protocol.mjs';
import {checkedGoalDataConfigurationResult,goalDataReadbackFromSaved} from './authored-goal-data-workflow-v288-protocol.mjs';
import SetupStudio from './SetupStudio';
import SetupVoiceDialog from './SetupVoiceDialog';
import UnderstandingDialog from './UnderstandingDialog';
import {emptyPreparation,normalizePreparation} from './understanding-preparation-contract.mjs';
import ConfigHistoryDialog from './ConfigHistoryDialog';
import RuntimeResources from './RuntimeResources';
import AutomationStudio from './AutomationStudio';
import AutomationReviewPanel from './AutomationReviewPanel';
import {setupSessionIdentity,sameSetupSession,prepareSetupSession} from './setup-session-contract.mjs';
import {verifyWorkspaceSwitch} from './workspace-switch-contract.mjs';
import {readDraftRecovery,adoptSavedDraft,resolveDraftRecovery} from './draft-cache-contract.mjs';
import {reviewJournalKey,legacyReviewJournalKey,readScopedReviewJournal,checkReviewView} from './automation-review-contract.mjs';
import {shouldPreventSetupUnload} from './setup-unload-contract.mjs';
import RuntimeReviewStudio from './RuntimeReviewStudio';
import BusinessWorkspace from './BusinessWorkspace';
import {businessNames} from './business-schema';
import {businessListBasis} from './business-list-contract.mjs';
import {flowFor} from './setup-schema';
import {initialWorkspace,withoutSecrets} from './catalog';
import {configureMaterialBackend,getMaterial} from './material-store';
import {json,api,setSession,clearSession,uploadRemote,readRemote,remoteURL,DraftSync,readRuntimeDraft} from './runtime-api';
import {requestLocalTransfer} from './local-transfer';
import './runtime.css';
import './styles.css';

function Icon(){return null;}
function Btn({children,primary,ghost,icon,...props}){return <button type="button" className={'btn '+(primary?'primary':ghost?'ghost':'')} {...props}>{children}</button>;}
function Field({label,note,children}){return <label className="field"><span>{label}</span>{children}{note&&<small>{note}</small>}</label>;}
function Dialog({title,onClose,children}){const ref=useRef();useEffect(()=>{ref.current.showModal();},[]);return <dialog ref={ref} onCancel={e=>{e.preventDefault();onClose();}}><div className="modal-head"><h2>{title}</h2><Btn onClick={onClose}>关闭</Btn></div>{children}</dialog>;}
const fresh=()=>({...initialWorkspace(),connections:[],resources:[],materials:[],businesses:[],flows:{},instanceProfile:{name:'',domain:'',goal:'',objectCandidates:[]}});
function stamp(materials){return JSON.stringify(materials.map(m=>[m.id,m.version,m.status,m.ocr,m.block_count,m.error,m.warnings]));}
function RecoverySummary({value}){return <><p>目标：{value.instanceProfile?.goal||'未填写'}</p><p>资料：{value.materials?.map(m=>m.name).join('、')||'无'}</p><p>资源：{value.resources?.map(r=>r.name).join('、')||'无'}</p>{businessNames(value).map(name=><div key={name}><strong>{name}</strong><p>{flowFor(value,name).steps.map(s=>[s.name,s.role,s.output].filter(Boolean).join(' / ')).join(' → ')||'未配置步骤'}</p></div>)}</>;}
export default function RuntimeSetup(){
 const [documentCopyMaterial,setDocumentCopyMaterial]=useState(null),[ocrMaterial,setOCRMaterial]=useState(null),[materialReads,setMaterialReads]=useState(0),[materialRefreshing,setMaterialRefreshing]=useState(false),[materialStatusRead,setMaterialStatusRead]=useState("loading");
 const [account,setAccount]=useState(null),[loading,setLoading]=useState(true),[error,setError]=useState(''),[username,setUsername]=useState(''),[password,setPassword]=useState(''),[w,setW]=useState(null),[step,setStep]=useState(()=>({business:1,resources:2,automation:3,review:4,workspace:5}[new URLSearchParams(location.search).get('view')]||0)),[save,setSave]=useState({state:'saved',text:''}),[materials,setMaterials]=useState([]),[recovery,setRecovery]=useState(null),[importing,setImporting]=useState(false),[historyRows,setHistoryRows]=useState(null),[understanding,setUnderstanding]=useState(null),[voice,setVoice]=useState(null);
 const [workspaceInstance,setWorkspaceInstance]=useState(()=>new URLSearchParams(location.search).get('instance')||'');
 const [reauth,setReauth]=useState(false),[switchingWorkspace,setSwitchingWorkspace]=useState(false);
 const workspaceSwitchPending=useRef(false);
 const [reviewPending,setReviewPending]=useState(null),[reviewBusy,setReviewBusy]=useState(false),[reviewStorageError,setReviewStorageError]=useState(''),[draftView,setDraftView]=useState(null);
 const reviewLock=useRef({pending:null,busy:false,error:''});reviewLock.current={pending:reviewPending,busy:reviewBusy,error:reviewStorageError};
 function pendingReview(value){reviewLock.current.pending=value;setReviewPending(value);}
 function busyReview(value){reviewLock.current.busy=value;setReviewBusy(value);}
 function reviewBlocked(){return !!(reviewLock.current.pending||reviewLock.current.busy||reviewLock.current.error);}
 const bootEpoch=useRef(0),bootMounted=useRef(true);
 const reportCloseGuard=useRef(null),reportCaptureWorking=useRef(null),reportMemoryDrafts=useRef([]);
 const fullOpenEpoch=useRef(0),fullDialogEpoch=useRef(0),fullCloseGuard=useRef(null),fullCaptureWorking=useRef(null),fullMemoryDrafts=useRef([]);
 const [fullReading,setFullReading]=useState(null),[fullMemoryVersion,setFullMemoryVersion]=useState(0);
 const ActiveReportReadingDialog=fullReading?.mode==='legacy'?ReportFullReadingDialog:ReportEvidenceReadingDialog;
 function fullEntryCurrent(entry){return reportEntryCurrent(entry)&&entry.dialog_epoch===fullDialogEpoch.current;}
 function captureFullMemory(){const draft=fullCaptureWorking.current?.(),user=accountRef.current;if(!draft||!user||draft.scope.actor_id!==user.user_id||draft.scope.workspace_id!==user.workspace_id)return null;const old=fullMemoryDrafts.current.find(x=>x.owner===draft.owner);if(!old||old.generation<=draft.generation){fullMemoryDrafts.current=[...fullMemoryDrafts.current.filter(x=>x.owner!==draft.owner),draft];setFullMemoryVersion(n=>n+1);}return draft;}
 function availableFullMemory(){const user=accountRef.current;return user?fullMemoryDrafts.current.filter(x=>x.scope.actor_id===user.user_id&&x.scope.workspace_id===user.workspace_id):[];}
 function openFullReading(materialID,draftID=null,memory=null,requestedMode=null){
  const user=accountRef.current,material=materials.find(m=>m.id===materialID),epoch=bootEpoch.current,fromDialog=fullDialogEpoch.current;
  if(!user||!material)return;
  const mode=requestedMode||(memory?.schema==='report-full-reading-working-v1'?'legacy':'evidence');
  if(!['legacy','evidence'].includes(mode)||memory&&!(mode==='legacy'?['report-full-reading-working-v1']:['report-evidence-reading-working-v1','report-evidence-reading-working-v2']).includes(memory.schema)){setError('这份暂存稿属于另一种读取记录，原输入保留。');return;}
  const proceed=()=>{if(epoch!==bootEpoch.current||fromDialog!==fullDialogEpoch.current||!sameSetupSession(user,accountRef.current))return;captureFullMemory();fullOpenEpoch.current++;fullDialogEpoch.current++;fullCloseGuard.current=null;fullCaptureWorking.current=null;setFullReading({material,draftID,memory,mode,identity:setupSessionIdentity(user),epoch,dialog_epoch:fullDialogEpoch.current});};
  if(fullReading&&fullEntryCurrent(fullReading)&&fullCloseGuard.current&&!fullCloseGuard.current({proceed}))return;
  proceed();
 }
 async function openFullReadingRecord(id){const user=accountRef.current,epoch=bootEpoch.current,entryEpoch=++fullOpenEpoch.current;const current=()=>entryEpoch===fullOpenEpoch.current&&epoch===bootEpoch.current&&sameSetupSession(user,accountRef.current);try{const row=await json('/setup/report-full-readings/'+id,{headers:{'X-Workspace-ID':user.workspace_id}});if(!current())return;await verifyFullDraft(row,user);if(!current())return;openFullReading(row.preview.body.material_refs[0].id,id,null,'legacy');}catch(e){if(current())setError(e.message);}}
 async function openEvidenceReadingRecord(id){const user=accountRef.current,epoch=bootEpoch.current,entryEpoch=++fullOpenEpoch.current;const current=()=>entryEpoch===fullOpenEpoch.current&&epoch===bootEpoch.current&&sameSetupSession(user,accountRef.current);try{const row=await json('/setup/report-evidence-readings/'+id,{headers:{'X-Workspace-ID':user.workspace_id}});if(!current())return;await verifyEvidenceDraft(row,user);if(!current())return;openFullReading(row.preview.body.material_refs[0].id,id,null,'evidence');}catch(e){if(current())setError(e.message);}}
 function closeFullReading(force=false,entry=fullReading){if(!fullEntryCurrent(entry))return;if(!force&&fullCloseGuard.current&&!fullCloseGuard.current({proceed:()=>closeFullReading(true,entry)}))return;captureFullMemory();fullOpenEpoch.current++;fullDialogEpoch.current++;setFullReading(null);fullCloseGuard.current=null;fullCaptureWorking.current=null;}
 const quoteCaptureWorking=useRef(null),quoteMemoryDrafts=useRef([]);const [quoteMemoryVersion,setQuoteMemoryVersion]=useState(0);
 function captureQuoteMemory(){const draft=quoteCaptureWorking.current?.(),user=accountRef.current;if(!draft||!user||draft.scope.actor_id!==user.user_id||draft.scope.workspace_id!==user.workspace_id)return null;const old=quoteMemoryDrafts.current.find(item=>item.token===draft.token);if(!old||old.generation<=draft.generation){quoteMemoryDrafts.current=[...quoteMemoryDrafts.current.filter(item=>item.token!==draft.token),draft];setQuoteMemoryVersion(n=>n+1);}return draft;}
 function availableQuoteMemory(){const user=accountRef.current;return user?quoteMemoryDrafts.current.filter(item=>item.scope.actor_id===user.user_id&&item.scope.workspace_id===user.workspace_id):[];}
 function settleQuoteMemory(token){const user=accountRef.current;if(!user)return;quoteMemoryDrafts.current=quoteMemoryDrafts.current.filter(item=>item.token!==token||item.scope.actor_id!==user.user_id||item.scope.workspace_id!==user.workspace_id);setQuoteMemoryVersion(n=>n+1);}
 const [reportDraft,setReportDraft]=useState(null),[reportMemoryVersion,setReportMemoryVersion]=useState(0),[reportNotice,setReportNotice]=useState(null);
 function captureReportMemory(){const draft=reportCaptureWorking.current?.();if(draft){const old=reportMemoryDrafts.current.find(x=>x.owner===draft.owner);if(!old||old.generation<=draft.generation){reportMemoryDrafts.current=[...reportMemoryDrafts.current.filter(x=>x.owner!==draft.owner),draft];setReportMemoryVersion(n=>n+1);}}return draft;}
 function availableReportMemory(){const user=accountRef.current;return user?reportMemoryDrafts.current.filter(x=>x.scope.actor_id===user.user_id&&x.scope.workspace_id===user.workspace_id):[];}
 function openReportDraft(materialID,draftID=null,memory=null){const user=accountRef.current,material=materials.find(m=>m.id===materialID);if(!user||!material)return;setReportDraft({material,draftID,memory,identity:setupSessionIdentity(user),epoch:bootEpoch.current});}
 async function openReportRecord(id){const user=accountRef.current,epoch=bootEpoch.current;try{const row=await json('/setup/report-drafts/'+id,{headers:{'X-Workspace-ID':user.workspace_id}});await verifyReportDraft(row,user);if(epoch!==bootEpoch.current||!sameSetupSession(user,accountRef.current))return;openReportDraft(row.preview.body.material_refs[0].id,id);}catch(e){if(epoch===bootEpoch.current)setError(e.message);}}
 function reportEntryCurrent(entry){return !!entry&&entry.epoch===bootEpoch.current&&sameSetupSession(entry.identity,accountRef.current);}
 function closeReportDraft(force=false,entry=reportDraft){if(!reportEntryCurrent(entry))return;if(!force&&reportCloseGuard.current&&!reportCloseGuard.current({proceed:()=>closeReportDraft(true,entry)}))return;setReportDraft(null);reportCloseGuard.current=null;reportCaptureWorking.current=null;}
 const ocrCloseGuard=useRef(null);
 const ocrCaptureWorking=useRef(null),ocrMemoryDrafts=useRef([]);const [ocrMemoryVersion,setOCRMemoryVersion]=useState(0),[ocrMemoryToken,setOCRMemoryToken]=useState('');
 function captureOCRMemory(){captureQuoteMemory();captureReportMemory();captureFullMemory();const draft=ocrCaptureWorking.current?.();if(!draft)return null;const old=ocrMemoryDrafts.current.find(item=>item.token===draft.token);if(!old||old.generation<=draft.generation){ocrMemoryDrafts.current=[...ocrMemoryDrafts.current.filter(item=>item.token!==draft.token),draft];setOCRMemoryVersion(n=>n+1);}return draft;}
 function availableOCRMemory(){const user=accountRef.current;return user?ocrMemoryDrafts.current.filter(item=>item.actor_id===user.user_id&&item.workspace_id===user.workspace_id):[];}
 function settleOCRMemory(token){const user=accountRef.current;if(!user)return;ocrMemoryDrafts.current=ocrMemoryDrafts.current.filter(item=>item.token!==token||item.actor_id!==user.user_id||item.workspace_id!==user.workspace_id);setOCRMemoryVersion(n=>n+1);}
 function requestOCRLeave(proceed){const identity=accountRef.current&&setupSessionIdentity(accountRef.current),epoch=bootEpoch.current,resume=()=>{if(epoch!==bootEpoch.current||!sameSetupSession(identity,accountRef.current))return;proceed();};if(fullCloseGuard.current&&!fullCloseGuard.current({proceed:resume}))return false;if(reportCloseGuard.current&&!reportCloseGuard.current({proceed:resume}))return false;return !ocrCloseGuard.current||ocrCloseGuard.current({proceed:resume});}
 async function restoreOCRMemory(draft){const epoch=bootEpoch.current,user=accountRef.current;if(!user||draft.actor_id!==user.user_id||draft.workspace_id!==user.workspace_id)return;try{if(sessionStorage.getItem('ga-setup-csrf-user')!==user.user_id||sessionStorage.getItem('ga-setup-csrf-workspace')!==user.workspace_id)throw Error('当前会话已变化');const fresh=await json('/me',{headers:{'X-Workspace-ID':user.workspace_id}});if(epoch!==bootEpoch.current||!sameSetupSession(user,accountRef.current))return;if(!sameSetupSession(user,fresh))throw Error('当前会话已变化');const current=ocrMemoryDrafts.current.find(item=>item.token===draft.token);if(current!==draft)throw Error('暂存稿已更新，请重新核对');setOCRMemoryToken(draft.token);setOCRMaterial(draft.material_id);}catch(e){if(epoch!==bootEpoch.current)return;bootEpoch.current++;detachSetupView();setReauth(true);setError('当前登录身份需要重新核对，原核页稿仅暂留当前应用内存。');}}
 const sync=useRef(),key=useRef(),guard=useRef(null),live=useRef(),accountRef=useRef(null),materialRequests=useRef(0),foregroundMaterialRequests=useRef(0),foregroundMaterialActive=useRef(false);live.current=w;accountRef.current=account;
 async function boot(){
  captureOCRMemory();setOCRMaterial(null);const epoch=++bootEpoch.current,isCurrent=()=>bootMounted.current&&epoch===bootEpoch.current;let sawIdentity=false;setLoading(true);setError('');
  try{
   const selected=sessionStorage.getItem('ga-setup-csrf-workspace'),user=await json('/me',{headers:selected?{'X-Workspace-ID':selected}:{}});if(!isCurrent())return;sawIdentity=true;
   const identity=setupSessionIdentity(user);if(accountRef.current&&!sameSetupSession(accountRef.current,identity)){const changed=Error('登录账号或工作区已改变，请核对当前会话。原人稿与请求保留，未重放写入。');changed.code='SESSION_SCOPE_CHANGED';throw changed;}
   await prepareSetupSession(user,{storage:sessionStorage,isCurrent,commitSession:setSession,readIdentity:workspaceID=>json('/me',{headers:{'X-Workspace-ID':workspaceID}})});if(!isCurrent())return;
   const [record,rows]=await Promise.all([readRuntimeDraft(),json('/setup/materials?limit=500')]);if(!isCurrent())return;
   const finalIdentity=setupSessionIdentity(await json('/me',{headers:{'X-Workspace-ID':identity.workspace_id}}));if(!isCurrent())return;if(!sameSetupSession(identity,finalIdentity)){const changed=Error('读取配置期间登录身份已改变，原人稿与请求保留，未采用迟到配置。');changed.code='SESSION_SCOPE_CHANGED';throw changed;}
   let navigation=null,canRestoreNavigation=false;try{canRestoreNavigation=!readScopedReviewJournal(localStorage,user);}catch{}
   if(canRestoreNavigation){
    const search=location.search,query=new URLSearchParams(search),view=query.get('view')||'domain',views={domain:0,business:1,resources:2,automation:3,review:4,workspace:5};navigation={search,step:Object.hasOwn(views,view)?views[view]:0,instance:'',error:''};
    if(view==='workspace'){
     const target=(query.get('instance')||'').toLowerCase();let visible=false;
     if(/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(target)){
      try{const instances=await json('/instances',{headers:{'X-Workspace-ID':identity.workspace_id}});if(!isCurrent())return;visible=Array.isArray(instances)&&instances.some(item=>item.id===target);}catch(e){if(!isCurrent())return;navigation.error='暂时无法核对链接中的实例，请在检查与生成中重新选择。';}
      const navigationIdentity=setupSessionIdentity(await json('/me',{headers:{'X-Workspace-ID':identity.workspace_id}}));if(!isCurrent())return;if(!sameSetupSession(identity,navigationIdentity)){const changed=Error('核对实例期间登录身份已改变，原人稿与请求保留，未打开旧实例。');changed.code='SESSION_SCOPE_CHANGED';throw changed;}
     }
     if(visible)navigation.instance=target;else{navigation.step=4;navigation.error||='链接中的实例缺失、无效或不在当前工作区可见清单中，已返回检查与生成，请重新选择实例。';}
    }
   }
   key.current='ga-setup:'+user.username+':'+user.workspace_id;
   const localRecovery=readDraftRecovery(localStorage,key.current,sessionStorage),local=localRecovery.local;
   const base=Object.keys(record.configuration).length?record.configuration:fresh();
   const config={...base,materials:rows};
   let originalReview=null,reviewError='';try{originalReview=readScopedReviewJournal(localStorage,user);}catch(e){reviewError=e.message;}
   pendingReview(originalReview);busyReview(false);reviewLock.current.error=reviewError;setReviewStorageError(reviewError);setDraftView(checkReviewView(record));
   if(originalReview||reviewError){guard.current=null;setStep(3);const query=new URLSearchParams({view:'automation'});if(originalReview)query.set('business',originalReview.business_name);history.replaceState(null,'','?'+query.toString());}
   else if(navigation&&location.search===navigation.search){setWorkspaceInstance(navigation.instance);setStep(navigation.step);if(navigation.error){setError(navigation.error);const query=new URLSearchParams(navigation.search);query.set('view','review');query.delete('instance');query.delete('task');history.replaceState(null,'','?'+query.toString());}else if(navigation.instance&&new URLSearchParams(navigation.search).get('instance')!==navigation.instance){const query=new URLSearchParams(navigation.search);query.set('instance',navigation.instance);history.replaceState(null,'','?'+query.toString());}}
   configureMaterialBackend({put:uploadRemote,read:readRemote,url:remoteURL});
   sync.current?.suspend();sync.current=new DraftSync(localStorage,key.current,record.revision,setSave,{configurationWire:savedConfigurationWire(record)});
   if(!originalReview&&!reviewError){const copyID=new URLSearchParams(location.search).get('document_copy');if(copyID)setDocumentCopyMaterial(copyID);}setMaterials(rows);setMaterialStatusRead("ready");setMaterialReads(n=>n+1);setAccount(user);accountRef.current=user;setReauth(false);setW(config);live.current=config;
   if(local?.pending){setRecovery({local,source:localRecovery.source,candidates:localRecovery.candidates,own:localRecovery.own,server:config,revision:record.revision});setSave({state:'error',text:'发现尚未送达服务器的修改，原输入已保留。请选择恢复方式。'});}
   else {setRecovery(null);setSave({state:'saved',text:'已读取服务器保存的配置与原件。'});}
  }catch(e){if(!isCurrent())return;let storageUnavailable=false,storedSession=false;try{storedSession=!!sessionStorage.getItem('ga-setup-csrf')||!!sessionStorage.getItem('ga-setup-csrf-user');}catch{storageUnavailable=true;}const hadSession=sawIdentity||!!accountRef.current||storedSession;detachSetupView();setReauth(e.status!==401);setError(storageUnavailable?'浏览器会话存储暂不可用，无法核对登录状态。请恢复浏览器存储后重试；原人稿与请求保留。':e.status===401?(hadSession?'当前登录会话已失效，请重新登录。原人稿与请求保留。':''):e.message);}
  finally{if(isCurrent())setLoading(false);}
 }
 useEffect(()=>{bootMounted.current=true;boot();return()=>{bootMounted.current=false;bootEpoch.current++;sync.current?.suspend();configureMaterialBackend(null);};},[]);
 useEffect(()=>{
  if(!account)return;
  setMaterialRefreshing(false);foregroundMaterialActive.current=false;let active=true;
  const timer=setInterval(()=>{if(active)refreshMaterials(account,false);},2500);
  return()=>{active=false;materialRequests.current++;foregroundMaterialRequests.current++;foregroundMaterialActive.current=false;clearInterval(timer);};
 },[account]);
 useEffect(()=>{if(!account){setReportDraft(null);reportCloseGuard.current=null;reportCaptureWorking.current=null;setFullReading(null);fullCloseGuard.current=null;fullCaptureWorking.current=null;}},[account?.user_id,account?.workspace_id]);
 useEffect(()=>{if(!account)return;let active=true;const user=account,epoch=bootEpoch.current;const read=async()=>{try{await verifyFullReadingJournals(user);await verifyEvidenceReadingJournals(user);const pending=reportPending(user),fullPending=fullReadingPending(user),evidencePending=evidenceReadingPending(user),status=await json('/setup/report-drafts/active',{headers:{'X-Workspace-ID':user.workspace_id}});if(active&&epoch===bootEpoch.current&&sameSetupSession(user,accountRef.current))setReportNotice(pending.length||fullPending.length||evidencePending.length||status.report_draft_active?{...status,pending,fullPending,evidencePending}:null);}catch(e){if(active&&epoch===bootEpoch.current)setReportNotice({error:e.message});}};read();const timer=setInterval(read,2500);return()=>{active=false;clearInterval(timer);};},[account?.user_id,account?.workspace_id]);
 useEffect(()=>{if(!account)return;const scopeKey=reviewJournalKey(account),legacyKey=legacyReviewJournalKey(account);const changed=e=>{if(e.key!==scopeKey&&e.key!==legacyKey)return;try{const original=readScopedReviewJournal(localStorage,account);pendingReview(original);reviewLock.current.error='';setReviewStorageError('');if(original){setStep(3);const query=new URLSearchParams({view:'automation',business:original.business_name});history.replaceState(null,'','?'+query.toString());}}catch(error){reviewLock.current.error=error.message;setReviewStorageError(error.message);}};window.addEventListener('storage',changed);return()=>window.removeEventListener('storage',changed);},[account?.workspace_id,account?.username]);
 useEffect(()=>{const warn=e=>{if(ocrMemoryDrafts.current.length||reportMemoryDrafts.current.length||fullMemoryDrafts.current.length){e.preventDefault();e.returnValue='';}};window.addEventListener('beforeunload',warn);return()=>window.removeEventListener('beforeunload',warn);},[]);
 const unloadDraftError=useRef(false),unloadExtras=useRef({voice:false,modal:false,importing:false});unloadExtras.current={voice:!!voice,modal:!!(understanding||ocrMaterial||documentCopyMaterial||historyRows||reportDraft||fullReading&&fullReading.mode!=='evidence'),evidenceModal:fullReading?.mode==='evidence'?{entry:fullReading,current:()=>fullEntryCurrent(fullReading),registration:()=>fullCloseGuard.evidenceUnload,guard:()=>fullCloseGuard.current}:null,importing};
 useEffect(()=>{const fn=e=>{if(shouldPreventSetupUnload({draftSync:sync.current,draftKey:key.current,account:accountRef.current,review:reviewLock.current,storage:localStorage,evidenceModal:unloadExtras.current.evidenceModal,otherUnsaved:unloadDraftError.current||unloadExtras.current.voice||unloadExtras.current.modal||unloadExtras.current.importing||new URLSearchParams(location.search).get('business_list_edit')==='1'})){e.preventDefault();e.returnValue='';}};window.addEventListener('beforeunload',fn);return()=>window.removeEventListener('beforeunload',fn);},[]);
 function commit(next){if(recovery||reviewLock.current.pending||reviewLock.current.error||!['admin','operator'].includes(accountRef.current?.role)){unloadDraftError.current=true;return false;}try{const clean=withoutSecrets(typeof next==='function'?next(live.current):next);sync.current.save(clean);unloadDraftError.current=false;live.current=clean;setW(clean);return true;}catch(e){unloadDraftError.current=true;setError(e.message);return false;}}
 async function go(next){try{if(reviewBlocked())throw Error('请先核对原自动化确认请求，编辑和导航暂时锁定；刷新可恢复原请求。');if(guard.current&&!await guard.current())return;await sync.current.wait();guard.current=null;setStep(next);history.pushState(null,'','?view='+(['domain','business','resources','automation','review','workspace'][next])+(next===5?'&instance='+encodeURIComponent(workspaceInstance):''));}catch(e){setError(e.message);}}
 function detachSetupView(){captureOCRMemory();quoteCaptureWorking.current=null;fullOpenEpoch.current++;setFullReading(null);fullCloseGuard.current=null;fullCaptureWorking.current=null;setReportDraft(null);reportCloseGuard.current=null;reportCaptureWorking.current=null;setReportNotice(null);ocrCloseGuard.current=null;ocrCaptureWorking.current=null;setOCRMemoryToken('');sync.current?.suspend();sync.current=null;key.current=null;guard.current=null;accountRef.current=null;live.current=null;unloadDraftError.current=false;setAccount(null);setW(null);setMaterials([]);setOCRMaterial(null);setDocumentCopyMaterial(null);setMaterialRefreshing(false);materialRequests.current++;foregroundMaterialRequests.current++;setRecovery(null);setHistoryRows(null);setUnderstanding(null);setVoice(null);setWorkspaceInstance('');pendingReview(null);busyReview(false);setReviewStorageError('');reviewLock.current.error='';setDraftView(null);configureMaterialBackend(null);}
 async function switchWorkspace(target){
  const user=accountRef.current,epoch=bootEpoch.current;
  if(!user||workspaceSwitchPending.current||target===user.workspace_id)return;
  if(step===5||recovery||reviewBlocked()||importing||voice||understanding||historyRows||reportDraft||fullReading||ocrMaterial||documentCopyMaterial||document.querySelector('dialog[open]')){setError('请先完成或关闭当前操作，再切换工作区。');return;}
  workspaceSwitchPending.current=true;setSwitchingWorkspace(true);setError('');let unlock=()=>{};
  const current=()=>bootMounted.current&&epoch===bootEpoch.current&&sameSetupSession(user,accountRef.current);
  try{
   const verified=await verifyWorkspaceSwitch({account:user,target,storage:sessionStorage,isCurrent:current,guard:()=>guard.current?guard.current():true,waitDraft:()=>sync.current.wait(),lock:()=>{const root=document.getElementById('root');if(!root)throw Error('页面状态无法核对。');const notice=document.createElement('div');notice.className='workspace-switch-progress';notice.setAttribute('role','status');notice.textContent='正在切换工作区，请稍候…';document.body.appendChild(notice);root.inert=true;unlock=()=>{root.inert=false;notice.remove();};},readIdentity:id=>json('/me',{cache:'no-store',headers:{'X-Workspace-ID':id}})});
   if(!verified||!current())return;
   bootEpoch.current++;detachSetupView();history.replaceState(null,'','?view=domain');setStep(0);
   try{setSession(verified.token,verified.identity.workspace_id,verified.identity.user_id,verified.identity.username);}catch{clearSession();setReauth(true);setError('无法保存工作区选择，请重新核对登录状态；原草稿保留。');return;}
   await boot();
  }catch(e){if(current()){
   if(e.status===401||e.status===403){bootEpoch.current++;detachSetupView();setReauth(true);setError('工作区或登录权限已变化，请重新核对；原草稿保留。');}
   else setError('暂时无法切换工作区：'+e.message);
  }}finally{unlock();workspaceSwitchPending.current=false;setSwitchingWorkspace(false);}
 }
 async function reauthenticate(){const identity=accountRef.current&&setupSessionIdentity(accountRef.current),epoch=bootEpoch.current;if(!requestOCRLeave(reauthenticate))return;if(guard.current){const decision=guard.current(),allowed=decision&&typeof decision.then==='function'?await decision:decision;if(!allowed)return;}if(epoch!==bootEpoch.current||(identity?!sameSetupSession(identity,accountRef.current):!!accountRef.current))return;bootEpoch.current++;detachSetupView();clearSession();setReauth(false);setError('正在核对当前登录会话，原人稿与请求保留；不会重新登录或重放写入。');boot();}

 async function login(e){e.preventDefault();bootEpoch.current++;setError('');setLoading(true);try{const r=await json('/login',{method:'POST',body:JSON.stringify({username,password})});setSession(r.csrf_token,'',undefined,r.username);setPassword('');await boot();}catch(e){setError(e.message);setLoading(false);}}
 async function logout(){
  const identity=accountRef.current&&setupSessionIdentity(accountRef.current),epoch=bootEpoch.current,current=()=>epoch===bootEpoch.current&&sameSetupSession(identity,accountRef.current);let attempted=false,csrf='';
  const credentialsCurrent=()=>sessionStorage.getItem('ga-setup-csrf-user')===identity.user_id&&sessionStorage.getItem('ga-setup-csrf-workspace')===identity.workspace_id&&!!csrf&&sessionStorage.getItem('ga-setup-csrf')===csrf;
  async function verifyRevocation(){
   bootEpoch.current++;detachSetupView();setReauth(true);setError('退出结果尚未确认，原人稿与请求保留，正在只读核对当前会话；不会再次发送退出。');
   const checkEpoch=bootEpoch.current,checking=()=>bootMounted.current&&checkEpoch===bootEpoch.current&&!accountRef.current,statuses=[];
   for(const path of ['/me','/session/restore']){
    if(!checking())return;let status;
    try{await json(path,{cache:'no-store',headers:{'X-Workspace-ID':identity.workspace_id,...(path==='/session/restore'?{'X-Session-Restore':'1'}:{})}});status=200;}catch(error){status=error.status||0;}
    if(!checking())return;statuses.push(status);
   }
   const invalid=statuses.length===2&&statuses.every(status=>status===401);setReauth(!invalid);setError(invalid?'当前登录会话已失效，原人稿与请求保留；本次退出是否完成未另作推断。':'退出结果尚未确认。原人稿与请求保留，请只读核对当前会话后再决定；未自动再次发送退出。');
  }
  try{
   if(!current())return;if(!requestOCRLeave(logout))return;if(reviewBlocked())throw Error('请先核对原自动化确认请求，原请求恢复记录保留。');
   csrf=sessionStorage.getItem('ga-setup-csrf')||'';
   if(guard.current&&!await guard.current())return;if(!current())return;await sync.current?.wait();if(!current())return;
   if(!credentialsCurrent()){const changed=Error('当前会话凭据已变化');changed.code='SESSION_CREDENTIAL_CHANGED';throw changed;}
   attempted=true;const response=await api('/setup/session/revoke',{method:'POST',headers:{'X-Workspace-ID':identity.workspace_id,'X-CSRF-Token':csrf}});if(!current())return;
   if(response.status!==200)throw Error('安全退出回执暂不能核对。');const result=await response.json();if(!current())return;
   if(!credentialsCurrent()){const changed=Error('当前会话凭据已变化');changed.code='SESSION_CREDENTIAL_CHANGED';throw changed;}
   if(result?.revoked!==true)throw Error('安全退出回执暂不能核对。');
   captureOCRMemory();bootEpoch.current++;ocrCloseGuard.current=null;ocrCaptureWorking.current=null;clearSession();accountRef.current=null;setAccount(null);setW(null);setOCRMaterial(null);setDocumentCopyMaterial(null);setMaterialRefreshing(false);materialRequests.current++;foregroundMaterialRequests.current++;configureMaterialBackend(null);setReauth(false);setError('');
  }catch(e){
   if(!current())return;
   if(e.status===403||e.code==='SESSION_CREDENTIAL_CHANGED'){bootEpoch.current++;detachSetupView();setReauth(true);setError('当前会话凭据需要重新核对，退出尚未确认。旧账号内容已隐藏，原人稿与请求保留；未自动再次发送退出。');}
   else if(e.status===404){bootEpoch.current++;detachSetupView();setReauth(true);setError('安全退出服务当前不可用，退出尚未确认。旧账号内容已隐藏，原人稿与请求保留，请核对当前会话与服务后再试。');}
   else if(attempted||e.status===401)await verifyRevocation();
   else setError('暂不能退出：'+e.message);
  }
 }
 async function recover(useLocal){
  try{if(!['admin','operator'].includes(accountRef.current?.role))throw Error('当前账号只读，可查看对照；原人稿保留。');if(reviewBlocked())throw Error('请先核对原自动化确认请求，再处理草稿恢复。');
   const original=recovery,scope=accountRef.current?.workspace_id+':'+accountRef.current?.username,target=key.current;
   const saved=checkReviewView(await readRuntimeDraft());
   if(scope!==accountRef.current?.workspace_id+':'+accountRef.current?.username||target!==key.current)throw Error('账号已变化，原人稿保留。');
   if(saved.revision!==original.revision){setRecovery(old=>({...old,server:saved.configuration,revision:saved.revision}));throw Error('服务器版本已变化，请先核对更新后的服务器版本；原人稿保留，尚未恢复提交。');}
   const value=useLocal?{...original.local.configuration,materials:original.local.configuration.materials||materials}:saved.configuration;
   const recovered=new DraftSync(localStorage,target,saved.revision,setSave,{configurationWire:useLocal?original.local.configuration_wire??savedConfigurationWire(saved):savedConfigurationWire(saved)});if(useLocal)recovered.stage(value);
   await resolveDraftRecovery(localStorage,sessionStorage,target,original.local,saved,useLocal);
   if(scope!==accountRef.current?.workspace_id+':'+accountRef.current?.username||target!==key.current)throw Error('账号已变化，原人稿保留。');
   sync.current=recovered;setRecovery(null);setW(value);live.current=value;setDraftView(saved);
   if(useLocal){recovered.flush();await recovered.wait();}else setSave({state:'saved',text:'已继续查看服务器版本，原人稿留在恢复备份；其他页面人稿保留。'});setError('');
  }catch(e){setError(e.message);}
 }

 async function importOld(){
  if(reviewBlocked()||!['admin','operator'].includes(accountRef.current?.role))return;
  if(!confirm('将导入此浏览器原来的配置与资料。先上传并逐项核对原件摘要，再保存到当前工作区；原记录不会删除。'))return;
  setImporting(true);setError('');
  try{
   await sync.current.wait();let source=JSON.parse(localStorage.getItem('three-agent-resource-design-v1')||'null'),transferred=null;
   if(!source){transferred=await requestLocalTransfer();source=transferred.draft;}
   const originals=[];for(const m of source.materials||[]){let blob;
    if(transferred){const file=transferred.files.find(f=>f.id===m.id);if(!file)throw Error('原件缺失：'+m.name);blob=new Blob([file.bytes],{type:m.type});}
    else {configureMaterialBackend(null);let rec;try{rec=await getMaterial(m.id);}finally{configureMaterialBackend({put:uploadRemote,read:readRemote,url:remoteURL});}blob=rec.blob;}
    const saved=await uploadRemote(new File([blob],m.name,{type:m.type}));originals.push({...saved,archived:!!m.archived,note:m.note||'',kind:m.kind||'业务资料'});}
   const idMap=new Map((source.materials||[]).map((m,i)=>[m.id,originals[i].id]));
   const replace=node=>Array.isArray(node)?node.map(replace):node&&typeof node==='object'?Object.fromEntries(Object.entries(node).map(([k,v])=>[k,k==='sourceId'&&idMap.has(v)?idMap.get(v):replace(v)])):node;
   const imported=replace({...source,materials:originals,flows:Object.fromEntries(Object.entries(source.flows||{}).map(([name,flow])=>[name,{...flow,confirmedAt:null,confirmedSignature:null}]))});
   commit(imported);await sync.current.wait();setMaterials(await json('/setup/materials?limit=500'));
  }catch(e){setError('导入未完成：'+e.message+' 已上传原件保留，可重试，原浏览器记录未删除。');}
  finally{setImporting(false);}
 }
 async function prepareUnderstanding(flush=true){if(flush&&guard.current&&!await guard.current())throw Error('当前输入还未保存，请先保存。');await sync.current.wait();return sync.current.revision;}
 async function prepareSemanticMutation(scope,{requireActive=true}={}){
  const user=accountRef.current,epoch=bootEpoch.current,prior=sync.current,target=key.current;
  const check=()=>{if(!scope||scope.actor_id!==user?.user_id||scope.workspace_id!==user?.workspace_id||!sameSetupSession(user,accountRef.current)||bootEpoch.current!==epoch||sync.current!==prior||key.current!==target)throw Error('账号、工作区或本页草稿已变化，原采用选择与请求保留。');};
  check();if(reviewBlocked())throw Error('请先核对原自动化确认请求；采用选择保留。');await prepareUnderstanding();check();const generation=prior.generation;
  const saved=checkReviewView(await readRuntimeDraft());check();
  if(prior.generation!==generation||prior.busy||prior.next||prior.error||saved.revision<prior.revision)throw Error('读取期间本页有较新输入或保存未完成，原稿保留，请重新核对。');
  if(requireActive&&!saved.configuration.fullSemanticDesigns?.some(d=>d.handoff_id===scope.handoff_id))throw Error('服务器当前完整设计已有变化，原选择与回执保留。');
  return saved;
 }
 async function readCurrentSemanticConfiguration(scope,settle){
  const user=accountRef.current,epoch=bootEpoch.current,saved=await prepareSemanticMutation(scope,{requireActive:false});
  if(!sameSetupSession(user,accountRef.current)||epoch!==bootEpoch.current)throw Error('账号已变化，未读入旧配置；原回执保留。');
  const result=await adoptServerDraft(saved);
  if(!sameSetupSession(user,accountRef.current)||epoch!==bootEpoch.current)throw Error('账号已变化，未结束原采用请求。');
  await settle?.(saved);setSave({state:'saved',text:'采用回执已核对并读取服务器当前配置；实际可用业务仍需检查生成和单独启用。'+(result.preservedPending?'其他页面尚未提交的人稿继续保留。':'')});
  return saved;
 }
 async function verifyBusinessListSave(submitted){const expected=accountRef.current?.workspace_id+':'+accountRef.current?.username,target=key.current,prior=sync.current;await prior.wait();const saved=await readRuntimeDraft();if(expected!==accountRef.current?.workspace_id+':'+accountRef.current?.username||target!==key.current||prior!==sync.current)throw Error('账号或本页草稿已变化，原编辑稿保留。');if(saved.revision<prior.revision)throw Error('服务器当前版本暂不能核对，原编辑稿保留。');if(!submitted||JSON.stringify(businessListBasis(saved.configuration))!==JSON.stringify(businessListBasis(submitted)))throw Error('服务器当前业务清单与本次保存不同，原编辑稿保留，请重新核对。');await adoptServerDraft(saved);return saved;}
 async function refreshMaterials(expectedAccount=accountRef.current,foreground=true){if(!expectedAccount||!foreground&&foregroundMaterialActive.current)return;const expected=materialReadScope(expectedAccount,bootEpoch.current),request=++materialRequests.current,foregroundRequest=foreground?++foregroundMaterialRequests.current:null,sameIdentity=()=>expected===materialReadScope(accountRef.current,bootEpoch.current),isCurrent=()=>request===materialRequests.current&&sameIdentity();if(foreground){foregroundMaterialActive.current=true;setMaterialRefreshing(true);}setMaterialStatusRead("loading");try{const rows=await readOCRMaterialStatus(json,isCurrent);if(!rows)return;setMaterials(old=>{const next=newerOCRMaterials(old,rows);return stamp(old)===stamp(next)?old:next;});setMaterialStatusRead("ready");setMaterialReads(n=>n+1);setError(old=>old.startsWith('资料状态读取失败：')?'':old);}catch(e){if(isCurrent()){setMaterialStatusRead('failed');setError('资料状态读取失败：'+materialStatusErrorMessage(e)+' 已有资料与核对结果保留。');}}finally{if(foregroundRequest===foregroundMaterialRequests.current){foregroundMaterialActive.current=false;if(sameIdentity())setMaterialRefreshing(false);}}}
 function closeDocumentCopy(){setDocumentCopyMaterial(null);const query=new URLSearchParams(location.search);query.delete('document_copy');history.replaceState(null,'','?'+query.toString());refreshMaterials(account,true);}
 async function openDocumentCopy(materialID){try{if(reviewBlocked())throw Error('请先核对原自动化确认请求。');await prepareUnderstanding();setDocumentCopyMaterial(materialID);const query=new URLSearchParams(location.search);query.set('document_copy',materialID);history.replaceState(null,'','?'+query.toString());}catch(e){setError(e.message);}}
 function closeOCR(force=false){if(force!==true&&!requestOCRLeave(()=>closeOCR(true)))return false;setOCRMaterial(null);ocrCloseGuard.current=null;ocrCaptureWorking.current=null;setOCRMemoryToken('');refreshMaterials(account,true);return true;}
 async function openOCR(materialID){try{await prepareUnderstanding();setOCRMaterial(materialID);}catch(e){setError(e.message);}}
 async function openReportSource(materialID,checkEntry){
  const validateEntry=typeof checkEntry==='function'?checkEntry:()=>{};
  const user=accountRef.current,epoch=bootEpoch.current,prior=sync.current;
  const current=()=>bootMounted.current&&epoch===bootEpoch.current&&prior===sync.current&&sameSetupSession(user,accountRef.current)&&sameSetupSession(account,user);
  if(!user||!current())return;
  const material=materials.find(item=>item.id===materialID);
  if(!material||material.archived){setError('来源资料当前不可用，请保留业务草稿并刷新资料。');return;}
  try{
   validateEntry();if(reviewBlocked())throw Error('请先核对原自动化确认请求。');
   if(guard.current&&!await guard.current())throw Error('当前输入还未保存，请先保存。');
   if(!current())return;validateEntry();await prior.wait();
   if(!current())return;validateEntry();
   if(/\.docx$/i.test(material.name)){
    setDocumentCopyMaterial(materialID);const query=new URLSearchParams(location.search);query.set('document_copy',materialID);history.replaceState(null,'','?'+query.toString());
   }else setOCRMaterial(materialID);
  }catch(e){if(current())setError(e.message);}
 }
 async function openUnderstanding(instruction='',mode='understand',intent='resume'){try{if(typeof instruction==='string'&&Array.from(instruction).length>4000)throw Error('修改要求超过4000字，原输入保留；请缩短后再预览。');normalizePreparation({...emptyPreparation(),note:instruction,mode});if(reviewBlocked())throw Error('请先核对原自动化确认请求。');const identity=accountRef.current;if(!identity)throw Error('请先核对当前登录身份。');setUnderstanding({instruction,mode,intent,entryId:crypto.randomUUID(),scope:identity.workspace_id+':'+identity.user_id});return true;}catch(e){setError(e.message);return false;}}
 async function adoptServerDraft(saved){
  const expected=accountRef.current?.workspace_id+':'+accountRef.current?.username,target=key.current,prior=sync.current;
  await prior.wait();if(expected!==accountRef.current?.workspace_id+':'+accountRef.current?.username||target!==key.current||prior!==sync.current)throw Error('账号或本页草稿已变化，原稿保留。');
  if(saved.revision<prior.revision)throw Error('服务器回执早于当前草稿，原人稿保留，请重新核对。');
  const adoptionGeneration=prior.generation;
  const result=await adoptSavedDraft(localStorage,target,saved);
  if(expected!==accountRef.current?.workspace_id+':'+accountRef.current?.username||target!==key.current||prior!==sync.current)throw Error('账号或本页草稿已变化，原稿保留。');
  if(prior.generation!==adoptionGeneration||saved.revision<prior.revision||prior.busy||prior.next||prior.error)throw Error('本页在核对期间又修改或保存了人稿，较新内容已保留；请先处理本页草稿，再核对原回执。');
  sync.current=new DraftSync(localStorage,target,saved.revision,setSave,{configurationWire:savedConfigurationWire(saved)});setW(saved.configuration);live.current=saved.configuration;if(saved.automation_review)setDraftView(checkReviewView(saved));return result;
 }
 async function acceptAuthoredConfiguration(result){
  const user=accountRef.current,epoch=bootEpoch.current,prior=sync.current,target=key.current;
  const check=()=>{if(!user||!sameSetupSession(user,accountRef.current)||epoch!==bootEpoch.current||prior!==sync.current||target!==key.current)throw Error('账号或配置入口已变化，人工采用的原回执保留。');};
  check();if(reviewBlocked()||recovery)throw Error('先核对原配置恢复或自动化确认，前置处理采用回执已保留。');
  const image=result?.schema==='authored-image-workflow-adoption-result-v243',decision=result?.schema==='authored-decision-workflow-adoption-result-v286',goalData=result?.schema==='authored-goal-data-workflow-adoption-result-v288';if(goalData)checkedGoalDataConfigurationResult(result);else if(decision)checkedDecisionConfigurationResult(result);else if(image)checkedAuthoredConfigurationResult(result);else if(!result||result.schema!=='authored-workflow-adoption-result-v208')throw Error('人工采用的原结果尚不能核对。');
  const verifyImageIdentity=async()=>{const actual=await json('/me',{cache:'no-store',headers:{'X-Workspace-ID':user.workspace_id}});check();if(!sameSetupSession(user,actual))throw Error('当前实际账号或工作区已变化，原采用回执保留，未替换页面配置。');};
  if(image||decision||goalData)await verifyImageIdentity();
  await prior.wait();check();const generation=prior.generation;
  const saved=checkReviewView(await readRuntimeDraft());check();
  if(prior.generation!==generation||prior.busy||prior.next||prior.error||saved.revision<result.accepted_revision)throw Error('读取期间又有配置输入，原人稿和采用回执保留；请先核对当前草稿。');
  if(image||decision||goalData){await verifyImageIdentity();if(prior.generation!==generation||prior.busy||prior.next||prior.error)throw Error('身份核对期间又有配置输入，原人稿和成功回执保留，未替换页面。');}const imageReceipt=image?imageReadbackFromSaved(saved,result,{actor_id:user.user_id,workspace_id:user.workspace_id,business_name:result.business_name}):decision?decisionReadbackFromSaved(saved,result,{actor_id:user.user_id,workspace_id:user.workspace_id,business_name:result.business_name}):goalData?goalDataReadbackFromSaved(saved,result,{actor_id:user.user_id,workspace_id:user.workspace_id,business_name:result.business_name}):null;
  const adopted=await adoptServerDraft(saved);
  if(!sameSetupSession(user,accountRef.current)||epoch!==bootEpoch.current||target!==key.current)throw Error('账号已变化，原采用结果仍保留。');
  setSave({state:'saved',text:(goalData?'资料准备与业务建议流程已另存并读回；仍需检查生成、审批和单独启用。':decision?'业务建议流程已另存并读回；仍需检查生成、审批和单独启用。':image?'人工编制的图像技术复核设计已另存并读回；内部技术报告与人工记录分别保留，仍需检查生成、审批和单独启用。':'人工配置的运行期资料前置已另存并读回；仍需检查生成、审批和单独启用。')+(adopted.preservedPending?'另一页面的人稿继续保留。':'')});
  return image||decision||goalData?imageReceipt:saved;
 }
 async function acceptUnderstanding(saved,goBusiness=true){const result=await adoptServerDraft(saved);setSave({state:'saved',text:(saved.analysis?.contract_version===6?(saved.analysis.edit_noop?'本次操作内容相同，未更改配置版本或流程确认。已读取服务器当前配置。':saved.analysis.status==='undone'?'字段修改已撤销并保存新版本，请核对受影响流程。':'选中的字段修改已保存，请核对受影响流程与能力配置。'):'建议已保存为新配置版本，流程仍需逐项确认。')+(result.preservedPending?'另一页面未提交的人稿已原样保留，请在原页面处理。':'')});if(goBusiness){setStep(1);history.pushState(null,'','?view=business');}}

 function reportConfigurationAdopter(goBusiness=true,saveMessage='报告候选已保存到配置，来源与人工流程仍需分别核对；尚未确认或发布。'){const expected={actor_id:account.user_id,workspace_id:account.workspace_id},epoch=bootEpoch.current,prior=sync.current;return async(saved,scope,onAccepted)=>{if(!scope||scope.actor_id!==expected.actor_id||scope.workspace_id!==expected.workspace_id||accountRef.current?.user_id!==expected.actor_id||accountRef.current?.workspace_id!==expected.workspace_id||bootEpoch.current!==epoch||sync.current!==prior)throw Error('账号、入口或本页草稿已变化，原交接回执保留。');await adoptServerDraft(saved);if(accountRef.current?.user_id!==expected.actor_id||accountRef.current?.workspace_id!==expected.workspace_id||bootEpoch.current!==epoch)throw Error('账号已变化，未打开旧配置。');onAccepted?.();setSave({state:'saved',text:saveMessage});if(goBusiness){setReportDraft(null);reportCloseGuard.current=null;reportCaptureWorking.current=null;setStep(1);history.pushState(null,'','?view=business');}};}
 function flowConfigurationAdopter(entry){const user=account,epoch=bootEpoch.current;return async(saved,scope,receipt,wire)=>adoptExecutionConfiguration({saved,scope,receipt,wire,account:user,checkEntry:()=>{if(!sameSetupSession(user,accountRef.current)||bootEpoch.current!==epoch||!fullEntryCurrent(entry))throw Error('账号或报告入口已变化，原流程采用回执保留。');},adoptServerDraft,onStatus:setSave});}
 function fullConfigurationAdopter(entry){const user=account,epoch=bootEpoch.current,prior=sync.current;return async(saved,scope,onAccepted)=>{const check=()=>{if(!scope||scope.actor_id!==user.user_id||scope.workspace_id!==user.workspace_id||!sameSetupSession(user,accountRef.current)||bootEpoch.current!==epoch||!fullEntryCurrent(entry))throw Error('账号或整份报告入口已变化，原加入回执保留。');};check();if(sync.current!==prior)throw Error('本页配置已变化，请先核对服务器当前配置，原加入回执保留。');await adoptServerDraft(saved);check();onAccepted?.();setSave({state:'saved',text:'完整业务设计已加入配置，仍待来源与执行条件核对；未确认或启用业务。'});closeFullReading(true,entry);setStep(1);history.pushState(null,'','?view=business');};}

 async function openHistory(){try{if(reviewBlocked())throw Error('请先核对原自动化确认请求。');await prepareUnderstanding();setHistoryRows(await json('/setup/draft/history'));}catch(e){setError(e.message);}}
 async function acceptRestore(saved){await acceptUnderstanding(saved,false);await refreshMaterials(accountRef.current,true);setSave({state:'saved',text:'旧配置已另存为新版本。请核对资料变化及需要重新确认的流程。'});}
 async function acceptAutomationReview(saved){
  checkReviewView(saved);
  if(recovery){const expected=accountRef.current?.workspace_id+':'+accountRef.current?.username;await sync.current.wait();if(expected!==accountRef.current?.workspace_id+':'+accountRef.current?.username)throw Error('账号已变化，原稿保留。');setRecovery(old=>({...old,server:saved.configuration,revision:saved.revision}));setDraftView(saved);sync.current=new DraftSync(localStorage,key.current,saved.revision,setSave);return;}
  const result=await adoptServerDraft(saved);setSave({state:'saved',text:'自动化确认回执已保存；当前规则状态以服务器当前配置为准。'+(result.preservedPending?'另一页面未提交的人稿已原样保留，请在原页面处理。':'')});
 }

 async function materialAction(row,action){try{if(!['admin','operator'].includes(account?.role))throw Error('当前账号只读，可查看资料与进度。');const current=await json('/setup/materials/'+row.id);await json(`/setup/materials/${row.id}/${action}`,{method:'POST',body:JSON.stringify({version:current.version})});setMaterials(await json('/setup/materials?limit=500'));}catch(e){setError(e.message);}}
 if(loading)return <main className="runtime-login"><h1>三智能体配置中心</h1><p>正在读取账户与服务器记录…</p></main>;
 if(!account&&reauth)return <main className="runtime-login"><h1>核对当前会话与配置</h1><p className="error" role="alert">{error}</p><p>原人稿和请求保持原样。核对只读取当前会话，不会重新登录或重复提交。</p><Btn onClick={boot}>重试读取</Btn><Btn onClick={reauthenticate}>核对当前会话，保留原稿</Btn></main>;
 if(!account)return <main className="runtime-login"><h1>登录三智能体配置中心</h1><form onSubmit={login}><Field label="账号"><input autoComplete="username" value={username} onChange={e=>setUsername(e.target.value)} required/></Field><Field label="密码"><input type="password" autoComplete="current-password" value={password} onChange={e=>setPassword(e.target.value)} required/></Field><button className="btn primary" type="submit">登录</button></form>{error&&<p className="error" role="alert">{error}<Btn onClick={boot}>重试连接</Btn></p>}</main>;
 return <><header className="runtime-header"><strong>{step===5?'业务工作区':'三智能体配置中心'}</strong><nav aria-label="智能体配置"><b>业务智能体</b><span>多模感知智能体</span><span>数据管理智能体</span></nav><label className="workspace-switcher"><span>工作区</span>{account.workspaces?.length>1?<select aria-label="切换工作区" value={account.workspace_id} disabled={switchingWorkspace} onChange={e=>switchWorkspace(e.target.value)}>{account.workspaces.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select>:<span>{account.workspaces?.find(item=>item.id===account.workspace_id)?.name||'当前工作区'}</span>}</label><span>{account.username}</span><Btn disabled={switchingWorkspace||reviewBusy||!!reviewPending||!!reviewStorageError} onClick={logout}>退出</Btn></header>{step<5&&<div className="runtime-steps">{['领域与资料','业务与流程','资源与能力','自动化范围','检查与生成'].map((name,i)=><Btn key={name} primary={step===i} disabled={importing||!!recovery||!!voice||reviewBusy||!!reviewPending||!!reviewStorageError} onClick={()=>go(i)}>{i+1} {name}</Btn>)}</div>}
 {availableReportMemory().map(memory=><section className="notice" key={memory.owner}><p>原业务草案末次输入仅暂留当前应用内存；刷新或退出应用会丢失。仅当前账号和工作区可明确恢复。</p><Btn onClick={()=>openReportDraft(memory.material_id,memory.draft_id,memory)}>恢复本账号暂存业务草案</Btn></section>)}
 {reportNotice&&<section className="notice"><p>{reportNotice.error||'有业务草案正在处理或原启动待核对；请先恢复原任务，不另起付费理解。'}</p>{reportNotice.own?.map(item=><Btn key={item.id} onClick={()=>(item.request_id?.startsWith('fe_')||item.request_id?.startsWith('fl_'))?openEvidenceReadingRecord(item.id):item.request_id?.startsWith('fr_')?openFullReadingRecord(item.id):openReportRecord(item.id)}>{(item.request_id?.startsWith('fe_')||item.request_id?.startsWith('fl_'))?'查看报告读取进度':item.request_id?.startsWith('fr_')?'查看旧版整份报告读取':'查看原正文业务草案'}</Btn>)}{reportNotice.pending?.map(entry=><Btn key={entry.body.request_id} onClick={()=>openReportDraft(entry.body.material_refs[0].id)}>核对原正文草案启动</Btn>)}</section>}
 {availableFullMemory().map(memory=><section className="notice" key={memory.owner}><p>整份报告的末次输入仅暂留本应用内存；刷新或退出会丢失。</p><Btn onClick={()=>openFullReading(memory.material_id,memory.draft_id,memory)}>恢复本账号整份报告稿</Btn></section>)}
 {reportNotice?.fullPending?.map(entry=><section className="notice" key={entry.body.request_id}><p>整份报告原调用结果尚待核对，请从原编号恢复。</p><Btn onClick={()=>entry.kind==='extract'?openFullReading(entry.body.material_refs[0].id,null,null,'legacy'):openFullReadingRecord(entry.draft_id)}>核对整份报告原调用</Btn></section>)}
 {reportNotice?.evidencePending?.map(entry=><section className="notice" key={entry.body.request_id}><p>报告读取的原请求尚待核对，已保存内容会保留。</p><Btn onClick={()=>entry.kind==='extract'?openFullReading(entry.body.material_refs[0].id):openEvidenceReadingRecord(entry.draft_id)}>恢复报告读取</Btn></section>)}
 {fullReading&&<ActiveReportReadingDialog key={[account.user_id,account.workspace_id,fullReading.mode,fullReading.dialog_epoch,fullReading.material.id,fullReading.draftID||'',fullReading.memory?.owner||''].join(':')} account={account} material={fullReading.material} initialDraftID={fullReading.draftID} memoryDraft={fullReading.memory} {...{Dialog,Btn}} prepareDraft={prepareUnderstanding} onConfigurationAdopt={fullConfigurationAdopter(fullReading)} onFlowConfigurationAdopt={flowConfigurationAdopter(fullReading)} onClose={()=>closeFullReading(true,fullReading)} onMemorySettled={owner=>{fullMemoryDrafts.current=fullMemoryDrafts.current.filter(x=>x.owner!==owner);setFullMemoryVersion(n=>n+1);}} onSessionInvalid={()=>{if(!fullEntryCurrent(fullReading))return;bootEpoch.current++;detachSetupView();setReauth(true);setError('当前账号需要重新核对，整份报告稿已隐藏，原输入保留。');}} onRegisterCloseGuard={(fn,capture,canUnload)=>{if(!fullEntryCurrent(fullReading))return;fullCloseGuard.current=fn;fullCloseGuard.evidenceUnload=fn&&fullReading.mode==='evidence'&&typeof canUnload==='function'?{entry:fullReading,guard:fn,canUnload}:null;fullCaptureWorking.current=capture;}}/>}
 {reportDraft&&<ReportDraftDialog key={[account.user_id,account.workspace_id,reportDraft.material.id,reportDraft.draftID||'',reportDraft.memory?.owner||''].join(':')} account={account} material={reportDraft.material} initialDraftID={reportDraft.draftID} memoryDraft={reportDraft.memory} {...{Dialog,Btn}} prepareDraft={prepareUnderstanding} onConfigurationAdopt={reportConfigurationAdopter()} onClose={()=>closeReportDraft(true,reportDraft)} onMemorySettled={owner=>{reportMemoryDrafts.current=reportMemoryDrafts.current.filter(x=>x.owner!==owner);setReportMemoryVersion(n=>n+1);}} onSessionInvalid={()=>{if(!reportEntryCurrent(reportDraft))return;bootEpoch.current++;detachSetupView();setReauth(true);setError('当前账号需要重新核对，旧业务草案已隐藏，人工稿保留。');}} onRegisterCloseGuard={(fn,capture)=>{reportCloseGuard.current=fn;reportCaptureWorking.current=capture;}}/>}
 {availableOCRMemory().length>0&&<section role="alert" aria-label="当前会话暂存核页稿"><p>核页稿仅暂存在当前应用内存，尚未完整保存；刷新或退出应用会丢失。可在原工作区重新打开核对。</p>{availableOCRMemory().map(item=><Btn key={item.token} onClick={()=>restoreOCRMemory(item)}>恢复会话暂存核页稿：{item.material_name||'原资料'}</Btn>)}</section>}
 {step<5&&<div className={'runtime-save '+(save.state==='error'?'error':'')} role="status">{save.text}{save.state==='error'&&!recovery&&<><Btn disabled={reviewBusy||!!reviewPending||!!reviewStorageError} onClick={()=>sync.current.retry()}>重试保存</Btn><Btn disabled={reviewBusy||!!reviewPending||!!reviewStorageError} onClick={boot}>查看服务器版本并处理冲突</Btn></>}<Btn onClick={()=>openUnderstanding()} disabled={importing||!!recovery||reviewBusy||!!reviewPending||!!reviewStorageError}>理解资料与修改记录</Btn><Btn onClick={openHistory} disabled={importing||!!recovery||reviewBusy||!!reviewPending||!!reviewStorageError}>配置历史</Btn><Btn disabled={importing||!!recovery||reviewBusy||!!reviewPending||!!reviewStorageError} onClick={importOld}>{importing?'正在核对并导入…':'导入原浏览器资料与配置'}</Btn></div>}
 {reviewStorageError&&<div className="runtime-error error" role="alert">{reviewStorageError} 配置编辑与导航已锁定，原存储记录保留。<Btn onClick={boot}>重新核对浏览器恢复记录</Btn></div>}
 {error&&<div className="runtime-error error" role="alert">{error}{reauth&&<Btn onClick={reauthenticate}>核对当前会话，保留草稿</Btn>}<Btn onClick={()=>setError('')}>关闭提示</Btn></div>}
 {recovery?<main><h1>恢复尚未保存的修改</h1><p>当前工作区和服务器记录均保留，请核对再选择。恢复本地修改将作为新的配置版本保存，已有原件不删除。</p>{recovery.candidates?.length>1&&<Field label="选择要核对的未提交稿"><select value={Math.max(0,recovery.candidates.indexOf(recovery.local))} onChange={e=>{const local=recovery.candidates[Number(e.target.value)];setRecovery(old=>({...old,local,source:local===old.own?'own':'shared'}));}}>{recovery.candidates.map((local,index)=><option key={index} value={index}>未提交稿 {index+1} · {local.configuration.instanceProfile?.name||'未命名实例'} · {(local.configuration.instanceProfile?.goal||'未填写目标').slice(0,70)}</option>)}</select></Field>}<div className="runtime-recovery"><div><h2>{recovery.source==='own'?'本页面未提交的修改':'另一页面或原版本未提交的修改'}</h2><p>实例：{recovery.local.configuration.instanceProfile?.name||'未填写'}</p><p>领域：{recovery.local.configuration.instanceProfile?.domain||'未填写'}</p><p>{recovery.local.configuration.instanceProfile?.goal}</p><RecoverySummary value={recovery.local.configuration}/></div><div><h2>服务器版本</h2><p>实例：{recovery.server.instanceProfile?.name||'未填写'}</p><p>领域：{recovery.server.instanceProfile?.domain||'未填写'}</p><p>{recovery.server.instanceProfile?.goal}</p><RecoverySummary value={recovery.server}/></div></div>{reviewPending&&<AutomationReviewPanel key={JSON.stringify([account.workspace_id,account.username,account.user_id])} account={account} business={reviewPending.business_name} refreshKey={'draft-recovery'} prepareDraft={()=>prepareUnderstanding(false)} onAdopt={acceptAutomationReview} onPending={pendingReview} onBusy={busyReview} onReload={boot} pending={reviewPending} initialView={draftView} Btn={Btn}/>}<Btn primary disabled={reviewBlocked()||!['admin','operator'].includes(account.role)} onClick={()=>recover(true)}>恢复本地修改并保存新版本</Btn><Btn disabled={reviewBlocked()||!['admin','operator'].includes(account.role)} onClick={()=>recover(false)}>继续使用服务器版本</Btn></main>:<fieldset style={{border:0,padding:0,margin:0}} disabled={importing||!!voice}>{step===5?<BusinessWorkspace key={workspaceInstance} instanceId={workspaceInstance} account={account} onBack={()=>go(4)} onRegisterGuard={fn=>{guard.current=fn;}} {...{Btn,Field}}/>:step===4?<RuntimeReviewStudio initialInstanceId={workspaceInstance} workspace={w} onCommit={commit} onSave={()=>prepareUnderstanding(false)} onEdit={go} account={account} onRegisterGuard={(fn,previous)=>{if(fn)guard.current=fn;else if(!previous||guard.current===previous)guard.current=null;}} onWorkspace={async(identity,taskId=null,entryCheck=null)=>{const original=accountRef.current,epoch=bootEpoch.current;const check=()=>{if(epoch!==bootEpoch.current||!sameSetupSession(original,accountRef.current))throw Error('账号或工作区已变化，未跳转旧任务。');if(entryCheck)entryCheck();};try{check();if(taskId!==null&&!/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(taskId))throw Error('原任务入口尚未核对，未拼接任务地址。');await prepareUnderstanding();check();guard.current=null;setWorkspaceInstance(identity);setStep(5);history.pushState(null,'','?view=workspace&instance='+encodeURIComponent(identity)+(taskId?'&task='+encodeURIComponent(taskId):''));}catch(e){setError(e.message);}}} {...{Btn,Field}}/>:step===3?<AutomationStudio runtime account={account} onSessionInvalid={identity=>{if(!sameSetupSession(identity,accountRef.current))return;bootEpoch.current++;detachSetupView();setReauth(true);setError('当前账号需要重新核对，配置与语音依据已隐藏，原记录保留。');}} onBusinessListSave={verifyBusinessListSave} onVoice={async(target,business)=>{try{await prepareUnderstanding();setVoice({target,business});}catch(e){setError(e.message);}}} reviewPending={reviewPending} reviewBusy={reviewBusy} reviewLocked={!!reviewStorageError} reviewView={draftView} prepareReview={prepareUnderstanding} onReviewAdopt={acceptAutomationReview} onReviewPending={pendingReview} onReviewBusy={busyReview} onReviewReload={boot} workspace={w} onCommitWorkspace={commit} onBack={()=>go(2)} onNext={()=>go(4)} onRegisterGuard={fn=>{guard.current=fn;}} {...{Icon,Btn,Field,Dialog}}/>:step===2?<RuntimeResources account={account} workspace={w} onCommit={commit} onSave={()=>prepareUnderstanding(false)} onImported={()=>refreshMaterials(account,true)} onGo={go} onRegisterGuard={fn=>{guard.current=fn;}} {...{Btn,Field}}/>:<SetupStudio key={step} runtime account={account} authoredWorkflowAPI={json} onAuthoredAdopted={acceptAuthoredConfiguration} authoredConfigurationSaved={save.state==='saved'&&!sync.current?.busy&&!sync.current?.next&&!sync.current?.error&&!recovery&&!reviewBlocked()} onRegisterCapture={fn=>{quoteCaptureWorking.current=fn;}} memoryDrafts={availableQuoteMemory()} onSettleMemory={settleQuoteMemory} canonicalMaterials={materials} materialStatusRead={materialStatusRead} onRefreshMaterials={()=>refreshMaterials(account,true)} onSave={verifyBusinessListSave} onOCR={openOCR} onOpenReportSource={openReportSource} onOpenFullSemanticReport={openEvidenceReadingRecord} onReportDraft={openReportDraft} onFullReading={openFullReading} onLegacyFullReading={id=>openFullReading(id,null,null,'legacy')} onUnderstand={openUnderstanding} prepareReportSource={prepareUnderstanding} onReportSourceAdopt={reportConfigurationAdopter(false)} onContentReviewAdopt={reportConfigurationAdopter(false,'草案内容确认已保存；正式启用仍需核对依据。')} {...{prepareSemanticMutation,readCurrentSemanticConfiguration}} onVoice={async(target,business,audioId='')=>{try{await prepareUnderstanding();setVoice({target,business,audioId});}catch(e){setError(e.message);}}} step={step} workspace={w} onCommit={commit} onGo={go} onRegisterGuard={fn=>{guard.current=fn;}} {...{Icon,Btn,Field,Dialog}}/>}
 {step===0&&<section className="runtime-material-status"><h2>资料处理进度</h2><Btn disabled={materialRefreshing} onClick={()=>refreshMaterials(account,true)}>刷新资料处理状态</Btn>{materialRefreshing&&<p role="status">正在重新读取服务器资料状态；已保存文字与历史保留。</p>}{!materials.length?<p>上传资料后，原件和解析进度会保存在服务器。</p>:materials.map(m=>{const current=ocrMaterialPresentation(m);return <div className="runtime-material" key={m.id}><div><strong>{m.name}</strong><span>{current.statusLabel}{m.resource_kind==='geospatial_raster'?' · 影像元数据':' · 上传时解析：'+m.block_count+'个文本块'}</span>{/\.docx$/i.test(m.name)&&<small>Word表格按行合并计数，与接受修订后的逐段读取口径不同。</small>}{current.currentError&&<p className="error">{current.currentError}</p>}{current.currentWarnings.length>0&&<p>{current.currentWarnings.join('；')}</p>}{current.historyMessages.length>0&&<details><summary>查看原生解析阶段的历史说明</summary>{current.historyMessages.map((message,i)=><p key={i}>{message}</p>)}</details>}{m.document_copy&&<p className="document-copy-provenance">PDF 副本来源：{m.document_copy.source_name||'原 Word'} · 原件与历史单独保留。{current.confirmed?'副本文字已逐页确认。':'此副本必须逐页识别与核对。'}<Btn onClick={()=>openDocumentCopy(m.document_copy.source_material_id)}>查看原件与副本来源</Btn></p>}{m.ocr&&<><p>{/\.docx$/i.test(m.name)?<>Word内嵌图片引用：{m.ocr.total}处 · 已确认{m.ocr.confirmed}处（与PDF页面单独统计）</>:/\.pdf$/i.test(m.name)?<>{m.ocr.force_all_pages?'PDF全页文字核对':'PDF需识别页面核对'}：{m.ocr.total}页 · 已确认{m.ocr.confirmed}页</>:<>图片文字核对：{m.ocr.total}项 · 已确认{m.ocr.confirmed}项</>}{current.currentOCRReason&&' · 仍有内容待核对'}</p>{current.currentOCRReason&&<details><summary>查看未覆盖内容</summary><p>{current.currentOCRReason}</p></details>}</>}{/\.docx$/i.test(m.name)&&<><DocxStructureReview materialID={m.id} account={account} requestJSON={json}/><DocxMaterialCharacterReview account={account} material={m} requestJSON={json} Btn={Btn} onSessionInvalid={()=>{if(!sameSetupSession(account,accountRef.current))return;bootEpoch.current++;detachSetupView();setReauth(true);setError('当前账号需要重新核对，原文对照与配置已隐藏，已保存内容保留。');}}/></>}</div>{(/\.docx$/i.test(m.name))&&<Btn primary={m.ocr?.coverage_status==='blocked'} onClick={()=>openDocumentCopy(m.id)}>本地 PDF 副本</Btn>}{(/\.(pdf|docx)$/i.test(m.name)||m.ocr)&&<Btn onClick={()=>openOCR(m.id)}>识别与核对图片文字</Btn>}{['failed','needs_input','cancelled'].includes(m.status)&&<Btn disabled={account.role==='viewer'||materialRefreshing} onClick={()=>materialAction(m,'retry')}>重试解析</Btn>}{['queued','parsing'].includes(m.status)&&<Btn disabled={account.role==='viewer'||materialRefreshing} onClick={()=>materialAction(m,'cancel')}>取消解析</Btn>}</div>;})}</section>}
 </fieldset>}{documentCopyMaterial&&<Dialog title="本地 PDF 副本与来源核对" onClose={closeDocumentCopy}><div className="modal-body"><DocumentCopyReview key={[account.workspace_id,account.user_id,documentCopyMaterial].join(':')} materialID={documentCopyMaterial} account={account} Btn={Btn} Field={Field} onClose={closeDocumentCopy} onChanged={()=>refreshMaterials(account,false)} onReview={copyID=>{closeDocumentCopy();openOCR(copyID);}}/></div></Dialog>}{ocrMaterial&&<Dialog title="本地识别与文字核对" onClose={closeOCR}><div className="modal-body"><MaterialOCRReview key={[account.workspace_id,account.user_id,account.username,ocrMaterial,ocrMemoryToken].join(':')} materialID={ocrMaterial} account={account} refreshToken={materialReads} Btn={Btn} Field={Field} onClose={()=>closeOCR(true)} memoryDraft={availableOCRMemory().find(item=>item.token===ocrMemoryToken&&item.material_id===ocrMaterial)} onMemorySettled={settleOCRMemory} onRegisterCloseGuard={(fn,capture)=>{ocrCloseGuard.current=fn;ocrCaptureWorking.current=capture||null;}} onChanged={()=>refreshMaterials(account,false)} onInstall={()=>{if(closeOCR()){refreshMaterials(account,false);go(2);}}}/></div></Dialog>}{historyRows&&<ConfigHistoryDialog {...{Dialog,Btn}} rows={historyRows} workspace={w} onClose={()=>setHistoryRows(null)} onRestored={acceptRestore} prepareDraft={()=>prepareUnderstanding(false)}/>}{voice&&<SetupVoiceDialog {...{account,Btn,Field,Dialog}} {...voice} currentText={voice.target==='goal'?(w.instanceProfile?.goal||''):voice.target==='automation_instruction'?(w.automationInstructions?.[voice.business]||''):(w.setupNotes?.[voice.business]||'')} prepareDraft={prepareUnderstanding} onClose={()=>setVoice(null)} onResources={()=>{setVoice(null);go(2);}} onApplied={async saved=>{await acceptUnderstanding(saved,false);setSave({state:'saved',text:'核对文字与语音依据已保存为新配置版本。'});}}/>}{understanding&&<UnderstandingDialog key={[account.workspace_id,account.user_id,understanding.entryId].join(':')} account={account} {...{Dialog,Btn,Field,materials}} workspace={w} instruction={understanding.instruction} mode={understanding.mode} intent={understanding.intent} entryId={understanding.entryId} onClose={()=>setUnderstanding(null)} prepareDraft={prepareUnderstanding} onApplied={acceptUnderstanding}/>}</>;
}
