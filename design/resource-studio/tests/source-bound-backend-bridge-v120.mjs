// Root's offline integration bridge. Uses the production editor/input models;
// stdin contains only the actual synthetic API response for this test run.
import {readFileSync} from 'node:fs';
import {verifyGraphWire,graphWireRequestJSON} from '../src/full-semantic-graph-wire.mjs';
import {createGraphEditorDraft,buildGraphPreviewCommand} from '../src/full-semantic-graph-editor-model.mjs';
import {selectGraphEventSource} from '../src/full-semantic-graph-source-draft.mjs';
import {createTaskInputDraft,editTaskInput,buildTaskGraphInputs} from '../src/graph-task-inputs.mjs';
globalThis.fetch=()=>{throw Error('The offline model bridge cannot use network transport');};
const input=JSON.parse(readFileSync(0,'utf8'));
if(input.kind==='editor'){
 const context=await verifyGraphWire(input.wire),source=context.event_wait.sources.find(row=>row.label===input.source_label&&row.available);
 if(!source)throw Error('The actual selected registered source is unavailable');
 let draft=createGraphEditorDraft(context,context.design.result.businesses[0].name);
 for(const sub of draft.graph.subscriptions)draft=selectGraphEventSource(draft,context,sub.id,source.producer_id);
 process.stdout.write(graphWireRequestJSON(buildGraphPreviewCommand(draft,context,input.abilities)));
}else if(input.kind==='task'){
 let draft=createTaskInputDraft(input.metadata,input.scope);
 for(const field of input.metadata.entity_fields)draft=editTaskInput(draft,input.metadata,input.scope,field.field,{mode:'value',raw:input.chosen_entity});
 process.stdout.write(JSON.stringify(buildTaskGraphInputs(input.metadata,input.scope,draft,input.selected_entities)));
}else throw Error('Unknown explicit integration operation');
