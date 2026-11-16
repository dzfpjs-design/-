import {existsSync} from 'node:fs';
import {dirname,join} from 'node:path';
import {fileURLToPath} from 'node:url';

// Read the same durable evidence in an isolated checkout or a frozen release;
// never synthesize a fallback when a real acceptance record is unavailable.
export function evidencePath(relative){
 let root=dirname(fileURLToPath(import.meta.url));
 for(;;){
  const candidate=join(root,'docs','evidence',relative);
  if(existsSync(candidate))return candidate;
  const parent=dirname(root);
  if(parent===root)throw Error('Required saved acceptance evidence is missing: '+relative);
  root=parent;
 }
}
