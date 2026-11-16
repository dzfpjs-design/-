import React,{forwardRef,useCallback,useEffect,useRef,useState} from 'react';

// First expansion creates the child. Later closing only changes the native
// details visibility, preserving controllers, unsaved memory and leave guards.
export default forwardRef(function RetainedWorkspaceSectionV142({scopeKey,summary,children,forceOpen=false,className='local-model-card'},ref){
 const node=useRef(null),[visited,setVisited]=useState(null);
 const attach=useCallback(value=>{node.current=value;if(typeof ref==='function')ref(value);else if(ref)ref.current=value;},[ref]);
 useEffect(()=>{if(forceOpen&&node.current){node.current.open=true;setVisited(scopeKey);}},[forceOpen,scopeKey]);
 return <details key={scopeKey} ref={attach} className={className} onToggle={event=>{if(event.target===event.currentTarget&&event.currentTarget.open)setVisited(scopeKey);}}>
  <summary>{summary}</summary>{visited===scopeKey?children:null}
 </details>;
});
