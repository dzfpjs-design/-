// Root executes this static React rendering check. All records are synthetic.
import test, {after} from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {mkdtemp, writeFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join, resolve} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
import {build} from 'vite';
import {blankDeviceGatewayFormV268} from '../src/device-gateway-ui-v268.mjs';
const project = resolve(fileURLToPath(new URL('..', import.meta.url))), require = createRequire(join(project, 'package.json')), reactPath = require.resolve('react'), reactURL = pathToFileURL(reactPath).href;
const id = n => '00000000-0000-4000-8000-' + String(n).padStart(12, '0');
let folder, bundle;
after(async () => { if (folder) await rm(folder, {recursive: true, force: true}); });
async function production() {
  if (!bundle) bundle = (async () => {
    folder = await mkdtemp(join(tmpdir(), 'device-gateway-v268-')); const entry = join(folder, 'entry.jsx');
    await writeFile(entry, 'export {DeviceGatewayViewV268 as View} from ' + JSON.stringify(join(project, 'src/DeviceGatewayStudioV268.jsx')) + ';');
    const result = await build({root: project, configFile: false, envDir: folder, cacheDir: join(folder, 'cache'), logLevel: 'silent', define: {'process.env.NODE_ENV': '"development"'}, build: {ssr: entry, write: false, minify: false, rollupOptions: {external: name => ['react', reactPath, reactURL].includes(name), output: {format: 'es', inlineDynamicImports: true, paths: name => ['react', reactPath, reactURL].includes(name) ? reactURL : name}}}});
    const chunk = result.output.find(row => row.type === 'chunk' && row.isEntry); assert.ok(chunk); const path = join(folder, 'production.mjs'); await writeFile(path, chunk.code); return import(pathToFileURL(path).href);
  })();
  return bundle;
}
function snapshot() {
  const connection = {id: id(10), name: '合成设备连接', origin: 'http://127.0.0.1:19680', status: 'enabled', version: 1, credential_revision: 1, credential_configured: true, created_at: '2026-11-01T00:00:00Z', grants: []};
  const families = [['weather_station', '气象测试站', ['get_status', 'read_telemetry']], ['ptz_camera', '相机测试点', ['get_status', 'set_ptz']], ['drone_dock', '无人机测试机库', ['get_status', 'start_mission', 'return_to_dock', 'cancel_mission']]];
  const discovery = {id: id(20), connection_id: connection.id, status: 'succeeded', connection_version: 1, credential_revision: 1, effective: true, integrity_error: null, catalogue_digest: 'a'.repeat(64), devices: families.map(([kind, name, actions], i) => ({external_id: 'test-' + i, kind, name, actions, simulation: true, origin_kind: 'simulated_equipment', field_verified: false, physical_device_connected: false})), created_at: connection.created_at};
  return {connections: [connection], selectedID: connection.id, detail: connection, discovery, form: {...blankDeviceGatewayFormV268(), reason: '合成核对说明', device_external_id: 'test-1', actions: []}, pending: null, writable: true, busy: false, loading: false, error: '', notice: '', formSaved: true};
}
async function markup(state, props = {}) { const {View} = await production(); return renderToStaticMarkup(React.createElement(View, {state, ...props})); }
test('V268 natural-device UI keeps simulated identity, distinct family boundaries and separated action groups', async () => {
  const html = await markup(snapshot()); for (const text of ['采购参数测试设备', '现场身份未核验', '物理设备未接通', '只读取测量数据', '不要求执行时段', '已确认航线', '允许读取', '允许控制能力', '云台转向', '只保存网关授权']) assert.ok(html.includes(text), text);
  assert.equal(html.includes('<pre'), false); assert.equal(html.includes('JSON'), false); assert.equal(html.includes('立即执行'), false); assert.equal(html.includes('开始飞行'), false);
});
test('V268 stale discovery and history never offer effective device authorization', async () => {
  const state = snapshot(); state.discovery.effective = false; const html = await markup(state); assert.ok(html.includes('历史发现已过期')); assert.match(html, /<select[^>]*disabled=""/); assert.match(html, /<button[^>]*disabled=""[^>]*>保存此设备的网关授权<\/button>/);
});
test('V268 invalid discovery displays failed evidence and no device authorization', async () => {
  const state = snapshot(); state.discovery = {...state.discovery, status: 'invalid', effective: false, integrity_error: 'discovery_integrity_invalid', devices: []}; const html = await markup(state); assert.ok(html.includes('发现依据核对失败')); assert.ok(html.includes('不能据此授权')); assert.match(html, /<button[^>]*disabled=""[^>]*>保存此设备的网关授权<\/button>/);
});
test('V268 original GET remains available in readonly mode while secret retry is disabled', async () => {
  const state = snapshot(); state.writable = false; state.pending = {operation: 'create', request_id: id(40), meta: {name: '合成原连接', origin: 'http://127.0.0.1:19680'}};
  const html = await markup(state); assert.match(html, /<button(?![^>]*disabled)[^>]*>核对原申请<\/button>/); assert.match(html, /<button[^>]*disabled=""[^>]*>明确按原申请重试<\/button>/); assert.ok(html.includes('不会自动重发')); assert.ok(html.includes('重新输入原申请使用的访问令牌'));
});
test('V268 device names and original reasons are escaped; credentials are password fields', async () => {
  const state = snapshot(); state.discovery.devices[0].name = '<img src=x onerror=alert(1)>'; const html = await markup(state); assert.equal(html.includes('<img'), false); assert.ok(html.includes('&lt;img')); assert.match(html, /type="password"/); assert.equal(html.includes('token_hash'), false); assert.equal(html.includes('ciphertext'), false);
});
