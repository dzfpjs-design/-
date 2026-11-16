// A unit is a declaration with provenance, never proof of calibration.
export function measurementUnit(info, value){
 if(typeof value!=='number'||!Number.isFinite(value))return '—';
 if(!info||typeof info!=='object'||typeof info.unit!=='string'||!info.unit.trim())return '单位未核对';
 if(info.basis==='message')return `${info.unit}（原报文提供）`;
 if(info.basis==='confirmed_source_mapping')return `${info.unit}（按已确认接入映射）`;
 return '单位未核对';
}
