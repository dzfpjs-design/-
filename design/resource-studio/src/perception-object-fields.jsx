import React from 'react';
import {taskObjectState,taskObjectSelectionIssues} from './entity-release-contract.mjs';
import {perceptionObjectState} from './perception-entity-contract.mjs';

export function perceptionObjectOptions(objects,selected,releaseID){
 const values=objects.map(object=>{const state=taskObjectState(object,releaseID);return <option key={object.id} value={object.id} disabled={!state.usable}>{state.label} · {state.description}{state.usable?'':' · '+state.reason}</option>;});
 if(selected&&!objects.some(object=>object.id===selected))values.push(<option key="retained-selection" value={selected} disabled>原选择对象（暂未载入，选择保留）</option>);
 return values;
}
export function perceptionSelectionNote(objects,selected,releaseID){
 if(!selected)return null;const state=perceptionObjectState(objects,selected,releaseID);
 return <p role={state.usable?'status':'alert'}>{state.description}{state.usable?'':'。'+state.reason+'。原选择与草稿保留，可选择空项取消此项选择。'}</p>;
}
export function perceptionObjectChecks(objects,selected,releaseID,onChange,prefix='核查对象 '){
 const choices=objects.map(object=>{const state=taskObjectState(object,releaseID),checked=selected.includes(object.id);return <label className="cap-option" key={object.id}><input type="checkbox" aria-label={prefix+state.label} checked={checked} disabled={!state.usable&&!checked} onChange={event=>{if(event.target.checked&&!state.usable)return;onChange(event.target.checked?[...new Set([...selected,object.id])]:selected.filter(id=>id!==object.id));}}/><span>{state.label} · {state.description}{!state.usable&&<> · {state.reason}；原选择可取消</>}</span></label>;});
 for(const issue of taskObjectSelectionIssues(objects,selected,releaseID).filter(item=>!objects.some(object=>object.id===item.id)))choices.push(<label className="cap-option" key={issue.id}><input type="checkbox" aria-label={'取消未载入的'+issue.label} checked onChange={event=>{if(!event.target.checked)onChange(selected.filter(id=>id!==issue.id));}}/>{issue.label} · {issue.reason}</label>);
 return choices;
}
