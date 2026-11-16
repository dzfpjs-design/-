// Root-authorized empty-PG/API captures with synthetic documents + MockHTTP.
// Constructing an actor binding here is not proof of a new login or real AI.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import actual from './fixtures/report-evidence-v4-public.mjs';
import {id,memory,tabFor as legacyTab,missing,reseal,nodes,text} from './report-evidence-v3-fixture.mjs';
import {copy} from '../src/report-evidence-reading-contract.mjs';
export {actual,id,memory,missing,reseal,nodes,text};
export function tabFor(account){const tab=legacyTab();if(account){tab.setItem('ga-setup-csrf-user',account.user_id);tab.setItem('ga-setup-csrf-workspace',account.workspace_id);}return tab;}
export async function captured(){
 const root=new URL('./fixtures/report-evidence-v4-server/',import.meta.url),manifest=JSON.parse(await readFile(new URL('manifest.json',root),'utf8'));
 assert.equal(manifest.schema,'public-v4-server-dto-bridge-v1');assert.equal(manifest.synthetic_only,true);assert.equal(manifest.actual_provider,false);assert.equal(manifest.real_credentials,false);
 const out={};for(const name of ['preview','command','accepted','mapped']){const bytes=await readFile(new URL(name+'.json',root));assert.equal(createHash('sha256').update(bytes).digest('hex'),manifest.dto_pins[name+'.json']);out[name]=JSON.parse(bytes);}
 out.account={user_id:out.preview.actor_id,workspace_id:out.preview.workspace_id,role:'admin'};return out;
}
export function entryFor(f){return {schema:'report-evidence-reading-command-v2',scope:{actor_id:f.account.user_id,workspace_id:f.account.workspace_id},kind:'extract',draft_id:null,body:copy(f.command),command_hash:f.accepted.receipt.command_hash,binding:{material_id:f.preview.body.selection.original_ref.id,source_hash:f.preview.source_hash,preview_hash:f.preview.preview_hash,map_inputs_hash:null,map_plan_hash:f.preview.map_preparation.plan.plan_hash,remaining_chunks:[]},receipt:null,rejection:null,resolution:null};}
