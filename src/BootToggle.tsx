import {useEffect,useRef,useState} from 'react';
import {callable} from '@decky/api';
import {PanelSectionRow,ToggleField} from '@decky/ui';
type Status={ok:boolean;revision?:string;error?:string;features?:Record<string,{enabled:boolean;last:unknown;error:string}>};
const read=callable<[],Status>('get_boot_status');
const mutate=callable<[Record<string,unknown>],Status>('mutate');
// One polling stream for all visible toggles; serialize writes across features.
const listeners=new Set<(s:Status)=>void>();
let latest:Status|null=null;
let timer:ReturnType<typeof setInterval>|null=null;
let queue:Promise<unknown>=Promise.resolve();
let pendingRead:Promise<Status>|null=null;
function publish(s:Status){latest=s;listeners.forEach(fn=>fn(s));return s;}
function enqueue<T>(fn:()=>Promise<T>):Promise<T>{const task=queue.then(fn);queue=task.catch(()=>undefined);return task;}
function refreshShared():Promise<Status>{
 if(pendingRead)return pendingRead;
 pendingRead=enqueue(async()=>publish(await read())).catch(e=>publish({ok:false,error:String(e)})).finally(()=>{pendingRead=null;});
 return pendingRead;
}
function subscribe(fn:(s:Status)=>void){
 listeners.add(fn);if(latest)fn(latest);
 if(!timer){void refreshShared();timer=setInterval(()=>void refreshShared(),2000);}
 return ()=>{listeners.delete(fn);if(!listeners.size&&timer){clearInterval(timer);timer=null;}};
}
function changeBoot(feature:string,enabled:boolean){
 return enqueue(async()=>{
  const current=await read();if(!current.ok||!current.revision)throw Error(current.error||'Startup status unavailable');
  const result=await mutate({method:'boot_set',feature,enabled,request_id:crypto.randomUUID(),expected_revision:current.revision});
  if(!result.ok)throw Error(result.error||'Could not save settings');return publish(result);
 });
}
export function BootToggle({feature,label}:{feature:string;label:string}){
 const [s,setS]=useState<Status>({ok:false}),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const alive=useRef(false),working=useRef(false);
 useEffect(()=>{alive.current=true;const unsubscribe=subscribe(result=>{if(alive.current)setS(result);});return()=>{alive.current=false;unsubscribe();};},[]);
 async function toggle(enabled:boolean){if(working.current)return;working.current=true;setBusy(true);setError('');try{
  await changeBoot(feature,enabled);
 }catch(e){if(alive.current)setError(String(e));}finally{working.current=false;if(alive.current)setBusy(false);}}
 const entry=s.features?.[feature];
 return <><PanelSectionRow><ToggleField label={label} checked={entry?.enabled||false} disabled={busy||!s.ok||(!entry?.enabled&&entry?.last==null)} onChange={v=>void toggle(v)}/></PanelSectionRow>
 <PanelSectionRow><div style={{fontSize:12,opacity:.75,lineHeight:1.5}}>{entry?.last==null?'Apply settings successfully before enabling startup restore.':'Restore the last successfully applied settings when game mode starts.'}{(error||entry?.error||s.error)&&<div>{error||entry?.error||s.error}</div>}</div></PanelSectionRow></>;
}
