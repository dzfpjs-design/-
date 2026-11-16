import React from 'react';
import {taskObjectState,taskObjectSelectionIssues} from './entity-release-contract.mjs';

export default function TaskObjectChoices({objects,selected,releaseID,onChange,disabled=false}){
 const missing=taskObjectSelectionIssues(objects,selected,releaseID).filter(item=>!objects.some(object=>object.id===item.id));
 function change(id,checked,usable){if(disabled||checked&&!usable)return;onChange(checked?[...new Set([...selected,id])]:selected.filter(item=>item!==id));}
 return <div aria-label="任务对象范围">
  {objects.map(object=>{const state=taskObjectState(object,releaseID),checked=selected.includes(object.id);return <label className="media-channel-choice" key={object.id}>
   <input type="checkbox" aria-label={'任务对象 '+state.label} checked={checked} disabled={disabled||!state.usable&&!checked} onChange={event=>change(object.id,event.target.checked,state.usable)}/>
   <span><strong>{state.label}</strong> · {state.type}<br/><small>{state.description}</small>{!state.usable&&<><br/><small>{state.reason}{checked?'；原选择保留，可取消此项选择':''}</small></>}</span>
  </label>;})}
  {missing.map(item=><label className="media-channel-choice" key={item.id}><input type="checkbox" aria-label={'取消未载入的'+item.label} checked disabled={disabled} onChange={event=>change(item.id,event.target.checked,false)}/><span>{item.label}<br/><small>{item.reason}</small></span></label>)}
 </div>;
}
