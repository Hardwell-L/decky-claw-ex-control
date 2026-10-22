import { BootToggle } from './BootToggle';
import {useEffect,useRef,useState} from 'react';
import {callable} from '@decky/api';
import {PanelSection,PanelSectionRow,ButtonItem,SliderField} from '@decky/ui';
type Pair={pl1:number;pl2:number};
type Status={ok:boolean;revision?:string;error?:string;message?:string;state?:string;gaming?:boolean;has_transaction?:boolean;draft?:Pair;current?:Pair & {profile:string;pl1_min:number;pl1_max:number;pl2_min:number;pl2_max:number}};
const read=callable<[],Status>('get_power_status');
const mutate=callable<[Record<string,unknown>],Status>('mutate');
export function PowerPanel(){
 const [s,setS]=useState<Status>({ok:false}),[draft,setDraft]=useState<Pair|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const alive=useRef(false),initialized=useRef(false),working=useRef(false);
 useEffect(()=>{alive.current=true;let fetching=false;
  const refresh=async()=>{if(fetching||working.current)return;fetching=true;try{const result=await read();if(!alive.current||working.current)return;setS(result);if(result.ok&&!initialized.current&&result.current){setDraft(result.draft||{pl1:result.current.pl1,pl2:result.current.pl2});initialized.current=true;}}catch(e){if(alive.current)setError(String(e));}finally{fetching=false;}};
  void refresh();const timer=setInterval(refresh,2000);return()=>{alive.current=false;clearInterval(timer);};
 },[]);
 async function run(method:string){
  if(working.current)return;working.current=true;setBusy(true);setError('');
  try{const latest=await read();if(!latest.ok||!latest.revision)throw Error(latest.error||'Power service unavailable');
   const result=await mutate({method,request_id:crypto.randomUUID(),expected_revision:latest.revision,...(method==='power_restore'?{}:draft)});
   if(alive.current)setS(result);if(!result.ok)throw Error(result.error||'Operation failed');
  }catch(e){if(alive.current)setError(String(e));}finally{working.current=false;if(alive.current)setBusy(false);}
 }
 const c=s.current,valid=!!(c&&draft&&draft.pl2>=draft.pl1&&draft.pl1>=c.pl1_min&&draft.pl1<=c.pl1_max&&draft.pl2>=c.pl2_min&&draft.pl2<=c.pl2_max);
 return <PanelSection title="PL1 / PL2 Power">
  <PanelSectionRow><div style={{fontSize:13,lineHeight:1.6}}>{s.state==='active'?'Plugin control active':s.state==='recovery_required'?'Recovery required':'System managed'}<br/>
   Readback: PL1 {c?.pl1??'—'} W · PL2 {c?.pl2??'—'} W<br/>
   {s.message}<br/>{error||s.error}
  </div></PanelSectionRow>
  {c&&draft&&<>
   <PanelSectionRow><SliderField label="PL1 sustained limit" min={c.pl1_min} max={c.pl1_max} step={1} value={draft.pl1} showValue valueSuffix=" W" disabled={busy} onChange={v=>setDraft({...draft,pl1:v})}/></PanelSectionRow>
   <PanelSectionRow><SliderField label="PL2 boost limit" min={c.pl2_min} max={c.pl2_max} step={1} value={draft.pl2} showValue valueSuffix=" W" disabled={busy} onChange={v=>setDraft({...draft,pl2:v})}/></PanelSectionRow>
   {!valid&&<PanelSectionRow><div>PL2 must be at least PL1. Values must be within driver limits.</div></PanelSectionRow>}
  </>}
  <PanelSectionRow><ButtonItem layout="below" disabled={!s.ok||!valid||busy} onClick={()=>void run('power_save')}>Save settings</ButtonItem></PanelSectionRow>
  <PanelSectionRow><ButtonItem layout="below" disabled={!s.ok||!valid||busy||!s.gaming||c?.profile!=='custom'||s.state==='recovery_required'} onClick={()=>void run('power_apply')}>Apply power limits</ButtonItem></PanelSectionRow>
  <PanelSectionRow><ButtonItem layout="below" disabled={busy||!s.has_transaction} onClick={()=>void run('power_restore')}>Restore system control</ButtonItem></PanelSectionRow>
  <BootToggle feature="power" label="Restore power at startup"/>
  <PanelSectionRow><div style={{fontSize:12,lineHeight:1.6,opacity:.8}}>
   {!s.gaming&&<>Desktop mode saves only. Apply in game mode.<br/></>}
   {c?.profile!=='custom'&&<>Enable custom TDP in Steam Performance settings first.<br/></>}
   Sliders edit a draft. Select Apply to write settings.<br/>
   The plugin yields control when Steam changes power limits or the performance profile.<br/>
   Control returns to the system on game-mode exit, connection loss or resume. Optional startup restore waits for Steam custom TDP.<br/>
   These are firmware limits, not live power consumption.
  </div></PanelSectionRow>
 </PanelSection>;
}
