export const vadPackID='silero-vad-onnx-v1';
export const audioPackID='sensevoice-small-v1';
export function packRuntimeReady(id,catalog){return id===vadPackID?catalog?.vad_runtime_installed===true:id===audioPackID?catalog?.audio_runtime_installed===true:catalog?.runtime_installed===true}
export function installBody(id,source,backend,version){
 if([audioPackID,vadPackID].includes(id)&&(source!=='official'||backend!=='cpu'))throw Error('当前语音包仅核验官方固定版本与CPU，不能切换来源或后端。');
 return {pack_id:id,source,backend,...(version?{version}:{})};
}
export function audioUploadReady(catalog){return catalog?.node_prepared===true&&catalog?.workspace_prepared===true}
