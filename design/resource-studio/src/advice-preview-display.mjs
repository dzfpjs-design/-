// Accept only the server-verified, fully resolved display contract.
export function presentPreview(value){
 const exact=(obj,keys)=>obj&&typeof obj==='object'&&!Array.isArray(obj)&&Object.keys(obj).sort().join('|')===[...keys].sort().join('|');
 if(!exact(value,['question','instruction','lines','documents'])||typeof value.question!=='string'||typeof value.instruction!=='string'||
  !Array.isArray(value.lines)||value.lines.some(line=>!exact(line,['label','value'])||typeof line.label!=='string'||typeof line.value!=='string')||
  !Array.isArray(value.documents)||!value.documents.length||value.documents.some(text=>typeof text!=='string'))throw Error('unsupported preview');
 return value;
}
