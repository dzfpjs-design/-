import {stableStringify} from './content-signature.js';
const uuid=x=>typeof x==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(x);
const sha=x=>typeof x==='string'&&/^[a-f0-9]{64}$/.test(x);
const name=x=>typeof x==='string'&&x.length>0&&x.length<=250&&!/[\u0000-\u001f\u007f/\\]/.test(x);
const version=x=>Number.isSafeInteger(x)&&x>=1;
const fail=()=>{throw Error('影像请求或回执无法核对；原记录已保留。');};
export function checkGeoAttempt(a){
 if(!a||typeof a!=='object'||Array.isArray(a)||Object.keys(a).length!==2||!['geo_listing','geo_import'].includes(a.kind)||!a.submission||typeof a.submission!=='object')fail();
 const s=a.submission,fields=a.kind==='geo_listing'?['request_id','version','connection_id']:['request_id','version','connection_id','listing_id','file_id'];
 if(Object.keys(s).length!==fields.length||!fields.every(x=>Object.hasOwn(s,x))||!uuid(s.request_id)||!uuid(s.connection_id)||!version(s.version))fail();
 if(a.kind==='geo_import'&&(!uuid(s.listing_id)||!sha(s.file_id)))fail();return a;
}
export function verifyGeoReceipt(r,a,protocol){
 checkGeoAttempt(a);if(!['ftp','smb'].includes(protocol)||!r||r.request_id!==a.submission.request_id||r.kind!==a.kind||stableStringify(r.submission)!==stableStringify(a.submission)||!['pending','failed','cancelled','succeeded'].includes(r.state))fail();
 if(r.state!=='succeeded')return r;const v=r.result;if(!v||typeof v!=='object')fail();
 if(a.kind==='geo_listing'){
  if(!uuid(v.listing_id)||!Array.isArray(v.files)||v.files.length>500||v.files.some(x=>!x||!sha(x.id)||!name(x.name)||!(/\.tiff?$/i.test(x.name))||!Number.isSafeInteger(x.size)||x.size<1||x.size>536870912||typeof x.modified!=='string'||!/^\d{14}(?:\.\d{1,6})?$/.test(x.modified)))fail();
 }else{
  const s=v.source;if(!uuid(v.scene_id)||!uuid(v.evidence_id)||typeof v.status!=='string'||!s||s.protocol!==protocol||s.connection_id!==a.submission.connection_id||s.listing_id!==a.submission.listing_id||s.file_id!==a.submission.file_id||!name(s.name)||!(/\.tiff?$/i.test(s.name))||!Number.isSafeInteger(s.size)||s.size<1||s.size>536870912||typeof s.modified!=='string'||!/^\d{14}(?:\.\d{1,6})?$/.test(s.modified)||!sha(s.sha256))fail();
 }
 return r;
}
export function geoPath(protocol,a){checkGeoAttempt(a);if(!['ftp','smb'].includes(protocol))fail();return '/'+protocol+'/geo/connections/'+encodeURIComponent(a.submission.connection_id)+(a.kind==='geo_listing'?'/listings':'/imports');}
export function geoBody(a){checkGeoAttempt(a);const {connection_id,...body}=a.submission;return body;}
