export function stableStringify(value){
 const normalize=node=>Array.isArray(node)?node.map(normalize):node&&typeof node==='object'?Object.fromEntries(Object.keys(node).sort().map(key=>[key,normalize(node[key])])):node;
 return JSON.stringify(normalize(value));
}
export function sameSignature(a,b){try{return stableStringify(JSON.parse(a))===stableStringify(JSON.parse(b));}catch{return a===b;}}
