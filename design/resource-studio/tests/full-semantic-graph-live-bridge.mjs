// Root-only API JSON -> pure authoring contract bridge. No browser, network or providers.
import fs from 'node:fs';
import {verifyGraphWire} from '../src/full-semantic-graph-wire.mjs';
import {verifyGraphEditorContext,verifyGraphAdoptionPreview} from '../src/full-semantic-graph-adoption-controller.mjs';
import {createGraphEditorDraft,buildGraphPreviewCommand,collectGraphEditorIssues} from '../src/full-semantic-graph-editor-model.mjs';
const payload=JSON.parse(fs.readFileSync(0,'utf8')),context=verifyGraphWire(payload.context);
await verifyGraphEditorContext(context,payload.scope);
let draft=createGraphEditorDraft(context,payload.business_name);
if(payload.command){for(const field of ['proof_id','graph','owner_bindings','role_bindings','external_node_ids','node_actions'])draft[field]=payload.command[field];}
if(payload.threshold!==undefined){draft.graph.predicates.p_after.right.value=payload.threshold;draft.owner_bindings[0].reason+=' 明确本次业务条件的数值门槛。';}
const issues=collectGraphEditorIssues(draft,context,payload.abilities,payload.members);
if(issues.length)throw Error('authoring issues: '+JSON.stringify(issues));
const command=buildGraphPreviewCommand(draft,context,payload.abilities);
let pin=null;if(payload.preview)pin=await verifyGraphAdoptionPreview(verifyGraphWire(payload.preview),command,command.parent?'revision':'root',{scope:payload.scope,context,members:payload.members});
process.stdout.write(JSON.stringify({command,pin}));
