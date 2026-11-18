import React, {useEffect, useRef, useState} from 'react';
import {json} from './runtime-api';

const SHA = /^[0-9a-f]{64}$/;
const STATES = new Set(['different', 'no_difference', 'native_unavailable', 'ocr_unavailable', 'unsupported', 'too_large']);
const MAX_TEXT = 20000;
const isText = value => typeof value === 'string' && value.length > 0 && [...value].length <= MAX_TEXT;
const notText = value => value === null;

function expectedScope(materialID, materialHash, account, detail) {
  const u = detail?.unit, raw = detail?.raw;
  if (!account?.user_id || !account?.workspace_id || !account?.role || !materialID ||
      !SHA.test(materialHash || '') || detail?.material_id !== materialID || !detail?.run_id ||
      !Number.isInteger(detail?.generation) || !Number.isInteger(detail?.material_version) ||
      !u?.id || !Number.isInteger(u.version) || u.kind !== 'pdf_page' ||
      !Number.isInteger(u.page) || u.page < 1 ||
      (raw && (!raw.id || !SHA.test(raw.hash || '')))) return null;
  return {material_id:materialID, material_version:detail.material_version, source_sha256:materialHash,
          run_id:detail.run_id, generation:detail.generation, unit_id:u.id, unit_version:u.version,
          page:u.page, raw_revision_id:raw?.id || null, raw_revision_hash:raw?.hash || null};
}

function checkedResult(value, expected) {
  if (value?.schema !== 'ocr-native-comparison-v1' || !STATES.has(value.status) ||
      !value.scope || Object.keys(expected).some(key => value.scope[key] !== expected[key])) throw Error('scope');
  const {status, native_text:native, ocr_text:ocr} = value;
  const valid = status === 'different' || status === 'no_difference' ? isText(native) && isText(ocr) :
    notText(native) && notText(ocr);
  if (!valid) throw Error('shape');
  return {status, native_text:native, ocr_text:ocr};
}

const statusText = {
  different:'两份文字存在差异，请对照原页面核查。',
  no_difference:'忽略空白后未发现差异，仍需对照原页面核查。',
  native_unavailable:'本页没有可供对照的 PDF 可提取文字，请直接核对原页面。',
  ocr_unavailable:'本页的原始机器识别结果尚不可用，请稍后重试。',
  unsupported:'当前资料不支持此项文字对照，请直接核对原页面。',
  too_large:'本页文字超出对照处理范围，未截断显示；请直接核对原页面。'
};

/** Read-only, explicitly requested comparison. Text never enters browser storage. */
export default function OCRNativeComparison({materialID, materialHash, account, detail, Btn='button', request=json}) {
  const expected = expectedScope(materialID, materialHash, account, detail);
  const scope = JSON.stringify([account?.user_id, account?.workspace_id, account?.role, expected]);
  const live = useRef({scope, generation:0, serial:0, controller:null});
  if (live.current.scope !== scope) {
    // This synchronous fence hides the previous owner's contents even before effects run.
    live.current.scope = scope;
    live.current.generation++;
    live.current.serial++;
  }
  const generation = live.current.generation;
  const [view, setView] = useState(null);
  const current = view?.scope === scope && view?.generation === generation ? view : null;
  useEffect(() => {
    return () => {live.current.serial++; live.current.controller?.abort(); live.current.controller = null;};
  }, [scope]);

  async function load() {
    if (!expected || current?.phase === 'loading') return;
    const stamp = ++live.current.serial, ownGeneration = live.current.generation;
    const controller = new AbortController();
    live.current.controller?.abort();
    live.current.controller = controller;
    setView({scope, generation:ownGeneration, phase:'loading'});
    const active = () => live.current.scope === scope && live.current.generation === ownGeneration && live.current.serial === stamp && !controller.signal.aborted;
    try {
      const url = '/setup/materials/' + encodeURIComponent(materialID) + '/ocr/units/' + encodeURIComponent(expected.unit_id) +
                  '/native-comparison?run_id=' + encodeURIComponent(expected.run_id);
      const reply = await request(url, {signal:controller.signal, cache:'no-store', headers:{'X-Workspace-ID':account.workspace_id}});
      const result = checkedResult(reply, expected);
      if (active()) setView({scope, generation:ownGeneration, phase:'ready', result});
    } catch (error) {
      if (active()) setView({scope, generation:ownGeneration, phase:'error',
        message:error?.message === 'scope' || error?.message === 'shape' ? '对照范围或结果发生变化，请刷新本页后重试。' :
          error?.status === 401 || error?.status === 403 ? '当前账号无法读取本页对照，请重新核对权限。' : '本页文字对照暂时无法读取，请重试。'});
    } finally {
      if (live.current.controller === controller) live.current.controller = null;
    }
  }

  if (!expected) return null;
  const result = current?.result;
  return <section aria-label="本页 PDF 文字与原始识别对照">
    <h4>对照本页文字</h4>
    <p>仅对照 PDF 可提取文字与原始机器识别；两者都可能有误。请以原页面为依据，此操作不会修改或确认任何文字。</p>
    <Btn type="button" disabled={current?.phase === 'loading'} onClick={load}>
      {current?.phase === 'loading' ? '正在读取本页文字…' : current?.phase === 'error' ? '重试读取本页文字' : result ? '重新读取本页文字' : '读取本页文字对照'}
    </Btn>
    {current?.phase === 'error' && <p role="alert">{current.message}</p>}
    {result && <div role="status"><p>{statusText[result.status]}</p>
      {(result.native_text !== null || result.ocr_text !== null) && <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(min(100%,18rem),1fr))',gap:'1rem'}}>
        <section aria-label="PDF 可提取文字"><strong>PDF 可提取文字</strong><pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere',maxHeight:'18rem',overflow:'auto'}}>{result.native_text ?? '本页没有可供对照的文字。'}</pre></section>
        <section aria-label="原始机器识别"><strong>原始机器识别</strong><pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere',maxHeight:'18rem',overflow:'auto'}}>{result.ocr_text ?? '本页尚无原始识别文字。'}</pre></section>
      </div>}
    </div>}
  </section>;
}
