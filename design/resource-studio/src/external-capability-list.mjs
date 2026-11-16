// This is an inventory of saved manifests, never proof of a live connection,
// a successful model evaluation, or a binding to the current instance.
const TYPES = new Set(['tool', 'specialist_model']);
const ADAPTERS = new Set(['http', 'mcp']);
const STATUS = {draft:'草稿', validated:'契约已校验', published:'契约已发布', revoked:'已撤销'};
const safeText = (value, fallback) => {
  if (typeof value !== 'string' || !value.trim()) return fallback;
  return value.trim().slice(0, 500)
    .replace(/\b(?:https?|rtsp):\/\/[^\s<>]+/gi, '[地址已隐藏]')
    .replace(/\b(api[_-]?key|token|password|authorization)\s*[:=]\s*\S+/gi, '$1=[已隐藏]')
    .replace(/(密码|密钥|令牌|凭据)\s*[:：=]\s*\S+/g, '$1=[已隐藏]');
};

export function externalCapabilityList(value, kind = 'all') {
  if (!Array.isArray(value) || value.some(x => !x || typeof x !== 'object' || Array.isArray(x))) throw new Error('invalid capability list');
  if (!['all','specialist_model','tool'].includes(kind)) throw new Error('invalid category');
  const rows = value.filter(x => TYPES.has(x.kind) && ADAPTERS.has(x.data?.execution?.adapter)
    && (kind === 'all' || x.kind === kind))
    .map((x, index) => ({
      key: typeof x.id === 'string' && x.id ? x.id : `item-${index}`,
      identity: typeof x.id==='string' && /^[a-z][a-z0-9_]{0,63}$/.test(x.namespace||'') && /^[a-z][a-z0-9_]{0,63}$/.test(x.name||'') && /^\d+\.\d+\.\d+$/.test(x.release_version||'')
        ? {id:x.id,namespace:x.namespace,name:x.name,version:x.release_version} : null,
      name: safeText(x.data?.display_name, safeText(x.name, '未命名能力')),
      description: safeText(x.data?.description, '未填写能力说明'),
      category: x.kind === 'tool' ? '工具' : '专业模型',
      adapter: x.data.execution.adapter === 'http' ? 'HTTP JSON' : 'MCP',
      contractVersion: safeText(x.data?.contract_version, '未提供'),
      releaseVersion: safeText(x.release_version, '未提供'),
      status: STATUS[x.status] || '状态待核实',
    }));
  return {rows, truncated: value.length >= 100};
}
