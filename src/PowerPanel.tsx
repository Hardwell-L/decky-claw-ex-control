import {localize,useLanguage} from './i18n';
import { BootToggle } from './BootToggle';
import {useEffect,useRef,useState} from 'react';
import {callable} from '@decky/api';
import {PanelSection,PanelSectionRow,ButtonItem,SliderField} from '@decky/ui';
type Pair={pl1:number;pl2:number};
type Status={ok:boolean;revision?:string;error?:string;message?:string;state?:string;gaming?:boolean;has_transaction?:boolean;draft?:Pair;target?:Pair;verification?:string;secondary_limited?:boolean;current?:Pair & {backend?:string;mmio?:Pair;msr?:Pair;firmware?:Pair;profile:string;pl1_min:number;pl1_max:number;pl2_min:number;pl2_max:number}};
const read=callable<[],Status>('get_power_status');
const mutate=callable<[Record<string,unknown>],Status>('mutate');
// Preserve ordering across panel close/reopen and overlapping requests.
let writes:Promise<unknown>=Promise.resolve();
let generation=0;
function submit(method:string, pair:Pair|null){
 const token=++generation;
 const task=writes.then(async()=>{
  const latest=await read();if(!latest.ok||!latest.revision)throw Error(latest.error||'Power service unavailable');
  if(method==='auto' && token!==generation)return latest;
  const operation=method==='auto'?(latest.gaming?'power_apply':'power_save'):method;
  const result=await mutate({method:operation,request_id:crypto.randomUUID(),expected_revision:latest.revision,...(operation==='power_restore'?{}:pair)});
  if(!result.ok)throw Error(result.error||'Operation failed');return result;
 });
 writes=task.catch(()=>undefined);return task;
}
export function PowerPanel(){
 useLanguage();
 const [s,setS]=useState<Status>({ok:false}),[draft,setDraft]=useState<Pair|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[pending,setPending]=useState(false);
 const alive=useRef(false),initialized=useRef(false),working=useRef(0);
 const timer=useRef<ReturnType<typeof setTimeout>|null>(null),queued=useRef<Pair|null>(null);
 function cancel(){if(timer.current)clearTimeout(timer.current);timer.current=null;queued.current=null;}
 function flush(){if(timer.current)clearTimeout(timer.current);timer.current=null;const pair=queued.current;queued.current=null;if(alive.current)setPending(false);if(pair)void run('auto',pair);}
 function edit(pair:Pair){
  generation++;setDraft(pair);cancel();setError('');
  const c=s.current;
  if(!c || pair.pl2<pair.pl1 || pair.pl1<c.pl1_min || pair.pl1>c.pl1_max || pair.pl2<c.pl2_min || pair.pl2>c.pl2_max){setPending(false);return;}
  queued.current=pair;setPending(true);timer.current=setTimeout(flush,400);
 }
 useEffect(()=>{alive.current=true;let fetching=false;
  const refresh=async()=>{if(fetching||working.current)return;fetching=true;try{await writes;const result=await read();if(!alive.current||working.current)return;setS(result);if(result.ok&&!initialized.current&&result.current){setDraft(result.draft||{pl1:result.current.pl1,pl2:result.current.pl2});initialized.current=true;}}catch(e){if(alive.current)setError(String(e));}finally{fetching=false;}};
  void refresh();const timer=setInterval(refresh,2000);return()=>{flush();alive.current=false;clearInterval(timer);};
 },[]);
 async function run(method:string,pair:Pair|null=draft){
  working.current++;if(alive.current){setBusy(true);setError('');}
  try{const result=await submit(method,pair);if(alive.current)setS(result);}
  catch(e){if(alive.current)setError(String(e));}
  finally{working.current--;if(alive.current)setBusy(working.current>0);}
 }
 const c=s.current,valid=!!(c&&draft&&draft.pl2>=draft.pl1&&draft.pl1>=c.pl1_min&&draft.pl1<=c.pl1_max&&draft.pl2>=c.pl2_min&&draft.pl2<=c.pl2_max);
 return localize(<PanelSection title="PL1 / PL2 Power">
  <PanelSectionRow><div style={{fontSize:13,lineHeight:1.6}}>{s.state==='active'?'Plugin control active':s.state==='recovery_required'?'Recovery required':'System managed'}<br/>
   Target: PL1 {s.target?.pl1??draft?.pl1??'—'} W · PL2 {s.target?.pl2??draft?.pl2??'—'} W<br/>
   {c?.backend==='msr'?'RAPL MSR':'RAPL MMIO'}: PL1 {c?.pl1??'—'} W · PL2 {c?.pl2??'—'} W<br/>
   MSR: PL1 {c?.msr?.pl1??'—'} W · PL2 {c?.msr?.pl2??'—'} W<br/>
   MSI: PL1 {c?.firmware?.pl1??'—'} W · PL2 {c?.firmware?.pl2??'—'} W<br/>
   {s.verification==='pending'?'Waiting for delayed readback':s.verification==='verified'?'Delayed readback verified':s.verification==='released'?'External changes detected; control released':''}<br/>
   {s.secondary_limited&&<>Limited by lower MSR limits; MSR was not changed.<br/></>}

   {s.message}<br/>{error||s.error}
  </div></PanelSectionRow>
  {c&&draft&&<>
   <PanelSectionRow><SliderField label="PL1 sustained limit" min={c.pl1_min} max={c.pl1_max} step={1} value={draft.pl1} showValue valueSuffix=" W" disabled={!s.ok||s.state==='recovery_required'} onChange={v=>edit({...draft,pl1:v})}/></PanelSectionRow>
   <PanelSectionRow><SliderField label="PL2 boost limit" min={c.pl2_min} max={c.pl2_max} step={1} value={draft.pl2} showValue valueSuffix=" W" disabled={!s.ok||s.state==='recovery_required'} onChange={v=>edit({...draft,pl2:v})}/></PanelSectionRow>
   {!valid&&<PanelSectionRow><div>PL2 must be at least PL1. Values must be within driver limits.</div></PanelSectionRow>}
  </>}
  <PanelSectionRow><ButtonItem layout="below" disabled={!s.ok||!valid||busy} onClick={()=>{cancel();setPending(false);void run('power_save');}}>Save settings</ButtonItem></PanelSectionRow>
  <PanelSectionRow><ButtonItem layout="below" disabled={!s.ok||!valid||busy||!s.gaming||s.state==='recovery_required'} onClick={()=>{cancel();setPending(false);void run('power_apply');}}>Apply power limits</ButtonItem></PanelSectionRow>
  <PanelSectionRow><ButtonItem layout="below" disabled={busy||(!s.has_transaction&&!pending)} onClick={()=>{cancel();setPending(false);void run('power_restore');}}>Restore system control</ButtonItem></PanelSectionRow>
  <BootToggle feature="power" label="Restore power at startup"/>
  <PanelSectionRow><div style={{fontSize:12,lineHeight:1.6,opacity:.8}}>
   {!s.gaming&&<>Desktop mode saves only. Apply in game mode.<br/></>}
   {pending?'Release to apply…':busy?'Updating…':'Changes save and apply automatically after 0.4 seconds.'}<br/>
   External RAPL changes release control; the plugin does not repeatedly overwrite them.<br/>
   Startup and resume restore are opt-in and wait for stable RAPL limits for 10 seconds. Readback is checked again after 1 and 5 seconds.<br/>
   These are power limits, not live power consumption.
  </div></PanelSectionRow>
 </PanelSection>);
}
