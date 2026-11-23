import test, {after} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, writeFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join, resolve, dirname} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {webcrypto} from 'node:crypto';
import React, {act, useEffect} from 'react';
import {JSDOM, ResourceLoader, VirtualConsole} from 'jsdom';
import {build} from 'vite';

// Real React DOM, StrictMode, component, runtime-api and local journal. Only the
// HTTP transport and public response data are synthetic; this is not a browser,
// model-quality, backend-permission or persistence-across-server-restart test.
const project = resolve(dirname(fileURLToPath(import.meta.url)), '..');
let temp, bundle;
after(async () => { if (temp) await rm(temp, {recursive:true, force:true}); });
async function component() {
  if (!bundle) bundle = (async () => {
    temp = await mkdtemp(join(tmpdir(), 'native-suggestion-v130-real-react-'));
    const output = await build({
      root:project, configFile:false, envDir:temp, cacheDir:join(temp, 'cache'),
      mode:'test', logLevel:'silent',
      build:{ssr:join(project, 'src/NativeMethodSuggestions.jsx'), write:false,
        minify:false, rollupOptions:{output:{format:'es', inlineDynamicImports:true,
          paths:id => id === 'react' ? pathToFileURL(join(project, 'node_modules/react/index.js')).href : id}}},
    });
    const chunk = output.output.find(item => item.type === 'chunk' && item.isEntry);
    assert.ok(chunk, 'bundle the actual NativeMethodSuggestions entry');
    const file = join(temp, 'NativeMethodSuggestions.mjs');
    await writeFile(file, chunk.code);
    return (await import(pathToFileURL(file).href)).default;
  })();
  return bundle;
}

const copy = value => JSON.parse(JSON.stringify(value));
const account = {user_id:'30000000-0000-4000-8000-000000000001',
  workspace_id:'30000000-0000-4000-8000-000000000002', username:'synthetic-operator', role:'operator'};
const otherAccount = {...account, user_id:'30000000-0000-4000-8000-000000000099', username:'another-operator'};
const task = {id:'30000000-0000-4000-8000-000000000003', version:4};
const suggestionID = '30000000-0000-4000-8000-000000000004';
const hash = 'a'.repeat(64), savedHash = 'b'.repeat(64);
const csrf = 'synthetic-native-suggestion-csrf';
const privateTitle = '只属于原账号的本机核对稿';
const privateReason = '原账号尚未提交的私有操作说明';
const prefix = '/api/workspace/tasks/' + task.id + '/experience/suggestions';
const legacyKey = 'ga-native-suggestion:' + JSON.stringify([account.workspace_id, account.username, task.id]);
const form = {title:'人工核对方法', summary:'合成的核查方式', limits:'需核对当前资料',
  steps:[{id:'step-a', field:'reading', kind:'units', instruction:'人工核对单位', pattern_ids:[]}]};
function detail() {
  return {id:suggestionID, task_id:task.id, task_version:4, version:2, record_hash:hash,
    status:'needs_review', source_kind:'cloud_model',
    permissions:{save:{allowed:true}, decline:{allowed:true}, cancel:{allowed:true},
      retry:{allowed:false}, import:{allowed:false}, local_recover:{allowed:false}},
    history:[], cloud_input:{contract_version:'native-method-projection-v1',
      fields:[{alias:'F1', type:'number', templates:['source_time','units'],
        flags:{source_identity:'known', declared_time:'unknown', units:'unknown', comparability:'unknown'}, records_bucket:'2+'}],
      source:'observations', criteria:['evidence_sufficient','field_value'], consumed_templates:[],
      allowed_pairs:[{alias:'F1', template:'source_time', pattern_id:'declared_time_v1'},
        {alias:'F1', template:'units', pattern_id:'declared_units_v1'}],
      limitations:['proposal_only','unknown_is_not_verified','no_old_values','no_device_or_model_execution','no_automatic_review']},
    generation_contract:{version:'native-method-generation-v1', prompt_version:'native-method-prompt-v1',
      prompt_hash:null, output_contract_hash:null, generation_contract_hash:null,
      rules:['仅按原合同选择已有核查方式。'], max_steps:5},
    dispatch_messages_hash:null, dispatch_options:null, capacity:null, raw_quality:null,
    error_code:null, missing_required_pairs:[], field_mapping:[{alias:'F1', field:'reading', field_label:'登记值'}],
    patterns:[], ...copy(form), saved_edit:{...copy(form), hash:savedHash, saved_at:'2026-10-12T00:00:00+00:00'},
    has_saved_response:true, provider:'synthetic-provider', model:'synthetic-model',
    call:{status:'received', usage:{total_tokens:91}, cost:{status:'unknown', reason:'合成回执，实付仍待核对'}},
    source_error:null};
}
function oldDraft() {
  return {selected:suggestionID, edits:{[suggestionID]:{form:{...copy(form), title:privateTitle}, dirty:true, baseHash:hash}},
    reason:privateReason, modelID:'', dailyLimit:1, validDays:7, pending:null};
}
const experience = {schema:'native-method-experience-v1', task_id:task.id, version:4,
  field_options:[{key:'reading', label:'登记值', type:'number', templates:['source_time','units']}],
  available_cases:[], has_more:false, permissions:{},
  source:{accepted:true, acceptance_hash:hash}, draft:null};

class NoResources extends ResourceLoader {
  fetch() { throw Error('External resources are forbidden in this synthetic DOM harness'); }
}
function serializedLocks() {
  const tails = new Map();
  return {request:async (name, options, callback) => {
    assert.equal(options.mode, 'exclusive');
    const previous = tails.get(name) || Promise.resolve();
    let release;
    const current = new Promise(resolve => { release = resolve; });
    tails.set(name, current);
    await previous;
    try { return await callback(); }
    finally { release(); if (tails.get(name) === current) tails.delete(name); }
  }};
}
async function harness(run) {
  const errors = [], transportErrors = [], virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', error => errors.push(error));
  const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',
    {url:'https://synthetic-native.invalid/', pretendToBeVisual:true, resources:new NoResources(), virtualConsole});
  const backing = dom.window.localStorage, session = dom.window.sessionStorage;
  const state = {me:copy(account), detail:detail(), models:[], startAllowed:false, allMeGate:null, nextMeGate:null,
    failWorkingWrites:false, parentGuard:false, guardHistory:[], visibility:'visible'};
  const storage = {
    get length() { return backing.length; }, key:index => backing.key(index),
    getItem:key => backing.getItem(key), removeItem:key => backing.removeItem(key),
    setItem(key, value) {
      if (state.failWorkingWrites && key.includes(':working:')) throw new dom.window.DOMException('Synthetic quota full', 'QuotaExceededError');
      backing.setItem(key, value);
    },
  };
  const originalRaw = JSON.stringify(oldDraft());
  storage.setItem(legacyKey, originalRaw);
  session.setItem('ga-setup-csrf', csrf);
  session.setItem('ga-setup-csrf-user', account.user_id);
  session.setItem('ga-setup-csrf-workspace', account.workspace_id);
  Object.defineProperty(dom.window, 'localStorage', {configurable:true, value:storage});
  Object.defineProperty(dom.window.document, 'visibilityState', {configurable:true, get:() => state.visibility});
  Object.defineProperty(dom.window.navigator, 'locks', {configurable:true, value:serializedLocks()});
  const names = ['window','document','navigator','HTMLElement','Event','localStorage','sessionStorage','fetch','crypto','IS_REACT_ACT_ENVIRONMENT'];
  const descriptors = Object.fromEntries(names.map(name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  const calls = [], gates = [];
  const fetch = async (raw, options = {}) => {
    const url = new URL(String(raw), dom.window.location.href);
    const method = options.method || 'GET';
    const call = {path:url.pathname + url.search, method, body:options.body};
    calls.push(call);
    try {
      assert.equal(url.origin, dom.window.location.origin, 'only local synthetic API calls are allowed');
      assert.equal(method, 'GET', 'privacy and editing tests never authorize a mutation');
      const headers = new Headers(options.headers);
      assert.equal(headers.get('X-Workspace-ID'), account.workspace_id);
      assert.equal(headers.get('X-CSRF-Token'), csrf);
      let value;
      if (url.pathname === '/api/me' && !url.search) {
        const identity = copy(state.me), held = state.nextMeGate || state.allMeGate;
        state.nextMeGate = null;
        if (held) await held.promise;
        value = identity;
      } else if (url.pathname === prefix + '/models' && !url.search) value = copy(state.models);
      else if (url.pathname === prefix && url.search === '?limit=20') {
        value = {items:[copy(state.detail)], version:4, next_cursor:null, policy:null,
          permissions:{start:{allowed:state.startAllowed}, local_start:{allowed:false}}};
      } else if (url.pathname === prefix + '/' + suggestionID && !url.search) value = copy(state.detail);
      else throw Error('Unexpected synthetic API route: ' + call.path);
      return new Response(JSON.stringify(value), {status:200, headers:{'Content-Type':'application/json'}});
    } catch (error) { transportErrors.push(error.message); throw error; }
  };
  for (const [name, value] of Object.entries({window:dom.window, document:dom.window.document,
    navigator:dom.window.navigator, HTMLElement:dom.window.HTMLElement, Event:dom.window.Event,
    localStorage:storage, sessionStorage:session, fetch, crypto:webcrypto, IS_REACT_ACT_ENVIRONMENT:true})) {
    Object.defineProperty(globalThis, name, {configurable:true, writable:true, value});
  }
  dom.window.fetch = fetch;
  const container = dom.window.document.getElementById('root');
  let root, probe = {setup:0, cleanup:0};
  const settle = async () => { for (let i=0; i<16; i++) await act(async () => { await new Promise(resolve => setImmediate(resolve)); }); };
  const makeGate = () => {
    let release;
    const gate = {promise:new Promise(resolve => { release = resolve; }), release:() => release()};
    gates.push(gate); return gate;
  };
  const snapshots = () => Array.from({length:storage.length}, (_, i) => storage.key(i)).sort().map(key => [key, storage.getItem(key)]);
  const input = label => container.querySelector('[aria-label="' + label + '"]');
  const contains = value => container.textContent.includes(value) || [...container.querySelectorAll('input,textarea')].some(node => node.value.includes(value));
  const onBusy = value => { state.parentGuard = value; state.guardHistory.push(value); };
  try {
    const {createRoot} = await import('react-dom/client'), Component = await component();
    function Btn({children, primary, ghost, icon, ...props}) { return React.createElement('button', props, children); }
    function Probe() { useEffect(() => { probe.setup++; return () => { probe.cleanup++; }; }, []); return null; }
    const mount = async () => {
      assert.equal(root, undefined);
      root = createRoot(container, {onUncaughtError:error => errors.push(error), onCaughtError:error => errors.push(error), onRecoverableError:error => errors.push(error)});
      await act(async () => root.render(React.createElement(React.StrictMode, null,
        React.createElement(Probe), React.createElement(Component, {task, account, Btn, experience,
          methodDirty:false, methodPending:null, locked:false, onBusy}))));
      await settle();
    };
    const unmount = async () => { if (root) { const old = root; root = undefined; await act(async () => old.unmount()); await settle(); } };
    const button = label => { const node = [...container.querySelectorAll('button')].find(value => value.textContent === label); assert.ok(node, 'actual DOM button: ' + label); return node; };
    const click = async label => { const node = button(label); assert.equal(node.disabled, false, label + ' is enabled'); await act(async () => node.click()); await settle(); };
    const edit = async (label, value) => {
      const node = input(label); assert.ok(node, 'actual DOM input: ' + label); assert.equal(node.matches(':disabled'), false);
      const prototype = node.tagName === 'TEXTAREA' ? dom.window.HTMLTextAreaElement.prototype : dom.window.HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(prototype, 'value').set;
      await act(async () => { setter.call(node, value); node.dispatchEvent(new dom.window.Event('input', {bubbles:true})); });
      await settle();
    };
    const focus = async () => { await act(async () => dom.window.dispatchEvent(new dom.window.Event('focus'))); await settle(); };
    const visibility = async value => { state.visibility = value; await act(async () => dom.window.document.dispatchEvent(new dom.window.Event('visibilitychange'))); await settle(); };
    await run({dom, state, storage, session, calls, container, originalRaw, mount, unmount, settle, input,
      click, edit, focus, visibility, contains, snapshots, text:() => container.textContent,
      blockIdentity:() => { const gate=makeGate(); state.allMeGate=gate; return gate; },
      blockNextIdentity:() => { const gate=makeGate(); state.nextMeGate=gate; return gate; },
      get probe() { return probe; }});
    await unmount();
    assert.deepEqual(transportErrors, [], 'all calls use exact scoped synthetic API routes');
    assert.deepEqual(errors, [], 'no React or jsdom runtime errors');
  } finally {
    if (root) { const old=root; root=undefined; await act(async () => old.unmount()); }
    for (const gate of gates) gate.release();
    await settle(); dom.window.close();
    for (const name of names) { if (descriptors[name]) Object.defineProperty(globalThis, name, descriptors[name]); else delete globalThis[name]; }
  }
}

test('automatic daily setting follows the selected provider without changing old saved policies', async () => harness(async h => {
  h.state.models=[{id:'deepseek-model',name:'DeepSeek',provider:'deepseek',model:'synthetic',version:1},{id:'qwen-model',name:'Qwen',provider:'qwen',model:'synthetic',version:1}];
  h.state.startAllowed=true;
  await h.mount();
  const model=h.input('方法建议云模型');assert.ok(model);
  await act(async()=>{model.value='deepseek-model';model.dispatchEvent(new h.dom.window.Event('change',{bubbles:true}));});await h.settle();
  let daily=h.input('自动方法建议每日限额');assert.ok(daily);assert.equal(daily.value,'unlimited');
  await act(async()=>{model.value='qwen-model';model.dispatchEvent(new h.dom.window.Event('change',{bubbles:true}));});await h.settle();
  daily=h.input('自动方法建议每日限额');assert.equal(daily.value,'1');assert.equal([...daily.options].some(option=>option.value==='unlimited'),false);
}));

test('actual StrictMode mount conceals a local private draft until the real account checks complete', async () => harness(async h => {
  const gate = h.blockIdentity(), before = h.snapshots();
  await h.mount();
  assert.ok(h.probe.setup >= 2 && h.probe.cleanup >= 1, 'StrictMode really replays effects');
  assert.ok(h.calls.some(call => call.path === '/api/me'));
  assert.equal(h.contains(privateTitle), false); assert.equal(h.contains(privateReason), false);
  assert.equal(h.input('建议方法名称'), null); assert.match(h.text(), /本机稿暂不展示/);
  assert.deepEqual(h.snapshots(), before, 'unverified startup has not opened or migrated private storage');
  await act(async () => gate.release()); await h.settle();
  assert.equal(h.input('建议方法名称').value, privateTitle);
  assert.equal(h.input('方法建议操作说明').value, privateReason);
  assert.equal(h.storage.getItem(legacyKey), h.originalRaw);
  assert.equal(h.calls.filter(call => call.method !== 'GET').length, 0);
}));

test('initial cookie-account mismatch keeps the old draft hidden and untouched, and explicit identity retry can restore it', async () => harness(async h => {
  h.state.me = copy(otherAccount); const before = h.snapshots();
  await h.mount();
  assert.equal(h.contains(privateTitle), false); assert.equal(h.contains(privateReason), false);
  assert.equal(h.input('建议方法名称'), null); assert.ok(h.container.querySelector('[role=alert]'));
  assert.deepEqual(h.snapshots(), before);
  assert.equal(h.calls.some(call => call.path.startsWith(prefix)), false, 'no private detail loading under the wrong cookie account');
  h.state.me = copy(account); await h.click('重新核对当前账号');
  assert.equal(h.input('建议方法名称').value, privateTitle);
  assert.equal(h.storage.getItem(legacyKey), h.originalRaw);
  assert.equal(h.calls.filter(call => call.method !== 'GET').length, 0);
}));

test('focus and visibility immediately conceal the draft and a late old-account response cannot reopen it after a switch', async () => harness(async h => {
  await h.mount(); assert.equal(h.input('建议方法名称').value, privateTitle);
  const before = h.snapshots(), oldIdentity = h.blockNextIdentity();
  await h.focus();
  assert.equal(h.contains(privateTitle), false); assert.equal(h.contains(privateReason), false);
  h.state.me = copy(otherAccount);
  await h.visibility('hidden'); await h.visibility('visible');
  assert.equal(h.input('建议方法名称'), null); assert.ok(h.container.querySelector('[role=alert]'));
  await act(async () => oldIdentity.release()); await h.settle();
  assert.equal(h.input('建议方法名称'), null); assert.equal(h.contains(privateTitle), false);
  assert.deepEqual(h.snapshots(), before, 'identity changes preserve the exact original local records');
  assert.equal(h.calls.filter(call => call.method !== 'GET').length, 0);
}));

test('actual input survives localStorage quota failure with parent and beforeunload protection until saved readback succeeds', async () => harness(async h => {
  await h.mount(); assert.equal(h.state.parentGuard, false);
  const before = h.snapshots(), nextTitle = '存储恢复前必须保留在本页的新核对稿';
  h.state.failWorkingWrites = true; await h.edit('建议方法名称', nextTitle);
  assert.equal(h.input('建议方法名称').value, nextTitle);
  assert.match(h.text(), /有输入尚未持久保存/); assert.equal(h.state.parentGuard, true);
  assert.deepEqual(h.snapshots(), before, 'failed storage writes do not pretend to persist the new input');
  const leave = new h.dom.window.Event('beforeunload', {cancelable:true});
  h.dom.window.dispatchEvent(leave); assert.equal(leave.defaultPrevented, true);
  assert.equal(h.calls.filter(call => call.method !== 'GET').length, 0);
  h.state.failWorkingWrites = false; await h.click('另存本页稿并核对原请求');
  assert.equal(h.input('建议方法名称').value, nextTitle); assert.equal(h.state.parentGuard, false);
  assert.ok(h.snapshots().some(([key, raw]) => key.includes(':working:') && raw.includes(nextTitle)));
  const savedLeave = new h.dom.window.Event('beforeunload', {cancelable:true});
  h.dom.window.dispatchEvent(savedLeave); assert.equal(savedLeave.defaultPrevented, false);
  await h.unmount(); await h.mount();
  assert.equal(h.input('建议方法名称').value, nextTitle, 'real journal rehydrates the successfully read-back input');
  assert.equal(h.storage.getItem(legacyKey), h.originalRaw);
  assert.equal(h.calls.filter(call => call.method !== 'GET').length, 0);
}));

test('cancelled suggestion retains its stopped main status while separate not-sent evidence explains reservation uncertainty', async () => harness(async h => {
  h.state.detail = {...detail(), status:'cancelled', title:'已停止的合成建议', steps:[], saved_edit:null,
    has_saved_response:false, permissions:Object.fromEntries(['save','decline','cancel','retry','import','local_recover'].map(key => [key,{allowed:false}])),
    call:{status:'not_started', usage:{}, cost:{status:'unknown'},
      dispatch:{status:'not_sent', reservation_state:'confirmed', proof_hash:hash}}};
  await h.mount();
  const card = h.container.querySelector('article.suggestion-card'); assert.ok(card);
  assert.equal(card.querySelector('h4').textContent, '已停止的合成建议 · 已停止后续处理');
  assert.match(card.textContent, /调用：本次未发送/);
  assert.ok([...card.querySelectorAll('[role=status]')].some(node => node.textContent === '本次未发送，已有预算预留记录；预留不等于实际费用。'));
  assert.match(card.textContent, /实际费用尚未核实/);
  assert.equal(h.input('建议方法名称'), null, 'no fabricated generated method is offered for the cancelled request');
  assert.equal(h.calls.filter(call => call.method !== 'GET').length, 0);
}));
