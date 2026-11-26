import {businessNames} from './business-schema.js';
import {stableStringify} from './content-signature.js';

const agents = new Set(['业务智能体', '多模感知智能体', '数据管理智能体', '人工确认']);
const unsafeNames = new Set(['__proto__', 'prototype', 'constructor']);
const reasons = new Set(['not_found', 'ambiguous', 'invalid_quote', 'material_changed', 'invalid_material']);
const problem = () => { throw Error('候选内容或资料状态已变化，请重新核对。'); };
const text = value => typeof value === 'string';
export const candidateNameKey = value => String(value).trim().normalize('NFKC').toUpperCase().toLowerCase();

function checkUnresolved(items, materialID) {
  if (!Array.isArray(items)) problem();
  return items.map(item => {
    if (!item || item.material_id !== materialID || !text(item.source_text) || !text(item.quote) || !reasons.has(item.reason)) problem();
    return {material_id: item.material_id, source_text: item.source_text, quote: item.quote, reason: item.reason};
  });
}

function checkCitations(items, materialID) {
  if (!Array.isArray(items)) problem();
  return items.map(item => {
    if (!item || item.material_id !== materialID || !text(item.block_id) || !item.block_id || !text(item.quote) || !item.quote) problem();
    return {...item};
  });
}

function sameInput(source, candidate, fields) {
  return fields.every(field => stableStringify(source[field]) === stableStringify(candidate[field]));
}

function checkedReferences(source, candidate, materialID) {
  const citations = checkCitations(candidate.citations, materialID);
  const unresolved = checkUnresolved(candidate.unresolved_sources, materialID);
  if (!Array.isArray(source.sources) || citations.length + unresolved.length !== source.sources.length) problem();
  const usedCitations = new Set(), usedUnresolved = new Set();
  for (const original of source.sources) {
    if (!original || !text(original.source_text) || !text(original.quote)) problem();
    let index = unresolved.findIndex((item, i) => !usedUnresolved.has(i)
      && item.source_text === original.source_text && item.quote === original.quote);
    if (index >= 0) { usedUnresolved.add(index); continue; }
    index = citations.findIndex((item, i) => !usedCitations.has(i) && item.quote === original.quote);
    if (index < 0) problem();
    usedCitations.add(index);
  }
  return {citations, candidateUnresolvedSources: unresolved};
}

// The preview must be obtained from the server for the current saved source.
// No client-side check in this module substitutes for server verification.
export function currentCandidateMaterial(bundle, materials, readStatus) {
  if (!bundle || bundle.schema !== 'setup-business-candidates-v1' || !['local_summary', 'model'].includes(bundle.origin)
      || !Array.isArray(bundle.candidates) || !bundle.material || !text(bundle.material.id)
      || !text(bundle.material.hash) || !Number.isInteger(bundle.material.version) || !text(bundle.material.engine)) return false;
  if (readStatus !== 'ready' || !Array.isArray(materials)) return false;
  const current = materials.find(item => item.id === bundle.material.id);
  return !!current && !current.archived && current.hash === bundle.material.hash
    && current.version === bundle.material.version && current.engine === bundle.material.engine;
}

export function addReportCandidate(workspace, bundle, preview, key, materials, readStatus, newID = () => crypto.randomUUID()) {
  if (!currentCandidateMaterial(bundle, materials, readStatus) || !preview?.material || !Array.isArray(preview.candidates)) problem();
  const source = bundle.candidates.find(item => item?.key === key);
  const candidate = preview.candidates.find(item => item?.key === key);
  if (!source || !candidate || preview.candidates.filter(item => item?.key === key).length !== 1
      || bundle.candidates.filter(item => item?.key === key).length !== 1) problem();
  const material = bundle.material;
  if (['id', 'hash', 'version', 'engine'].some(field => preview.material[field] !== material[field])) problem();
  if (!text(candidate.name) || !candidate.name.trim() || candidate.name !== candidate.name.trim()
      || unsafeNames.has(candidateNameKey(candidate.name)) || !text(candidate.trigger) || !text(candidate.output)
      || !text(candidate.inference_reason) || !Array.isArray(candidate.steps) || !candidate.steps.length) problem();
  if (!sameInput(source, candidate, ['key', 'name', 'trigger', 'output', 'inference_reason', 'sources'])
      || !Array.isArray(source.steps) || source.steps.length !== candidate.steps.length) problem();
  if (candidate.name !== source.name || businessNames(workspace).some(name => candidateNameKey(name) === candidateNameKey(candidate.name))
      || Object.hasOwn(workspace.flows || {}, candidate.name)) throw Error('该业务已存在，请在业务清单中修改。');
  const ids = new Map();
  for (const step of candidate.steps) {
    if (!step || !text(step.key) || !step.key || ids.has(step.key)) problem();
    ids.set(step.key, newID());
  }
  function destination(value) {
    if (value === '' || value === null || value === undefined || value === 'auto') return 'auto';
    if (value === 'end') return 'end';
    if (!ids.has(value)) problem();
    return ids.get(value);
  }
  const basis = checkedReferences(source, candidate, material.id);
  const steps = candidate.steps.map((step, index) => {
    if (!text(step.name) || !step.name.trim() || !text(step.purpose) || !agents.has(step.agent)
        || !text(step.role) || !text(step.output) || !text(step.condition) || !text(step.inference_reason)) problem();
    if (!sameInput(source.steps[index], step, ['key', 'name', 'purpose', 'agent', 'role', 'output', 'condition', 'next_key', 'else_key', 'inference_reason', 'sources'])) problem();
    const refs = checkedReferences(source.steps[index], step, material.id), first = refs.citations[0] || refs.candidateUnresolvedSources[0];
    return {id: ids.get(step.key), candidateKey: step.key, name: step.name, purpose: step.purpose,
      agent: step.agent, role: step.role, output: step.output, sourceId: first?.material_id || '',
      citation: '', citations: refs.citations, candidateUnresolvedSources: refs.candidateUnresolvedSources,
      inferenceReason: step.inference_reason, condition: step.condition, nextId: destination(step.next_key),
      elseId: destination(step.else_key)};
  });
  const flow = {candidateKey: candidate.key, trigger: candidate.trigger, output: candidate.output, steps,
    citations: basis.citations, candidateUnresolvedSources: basis.candidateUnresolvedSources,
    inferenceReason: candidate.inference_reason, confirmedAt: null, confirmedSignature: null};
  return {...workspace, businesses: [...(workspace.businesses || []), candidate.name],
    flows: {...(workspace.flows || {}), [candidate.name]: flow}};
}

// Apply a reviewed set in memory first. The caller commits exactly once only
// after every selected, non-conflicting entry has passed the same checks.
export function addReportCandidates(workspace, bundle, preview, keys, materials, readStatus, newID = () => crypto.randomUUID()) {
  if (!Array.isArray(keys) || !keys.length || new Set(keys).size !== keys.length || !Array.isArray(preview?.candidates)) problem();
  let next = workspace;
  const added = [], skipped = [];
  for (const key of keys) {
    const item = preview.candidates.find(candidate => candidate?.key === key);
    if (!item || !text(item.name)) problem();
    const existing = businessNames(next).some(name => candidateNameKey(name) === candidateNameKey(item.name));
    if (existing) { skipped.push(item.name); continue; }
    next = addReportCandidate(next, bundle, preview, key, materials, readStatus, newID);
    added.push(item.name);
  }
  return {workspace: next, added, skipped};
}
