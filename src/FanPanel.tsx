import { Fragment, useEffect, useRef, useState } from 'react';
import { callable } from '@decky/api';
import { PanelSection, PanelSectionRow, ButtonItem, SliderField, ToggleField, DialogButton, Focusable } from '@decky/ui';
type Draft = {mode:'fixed'|'curve';fixed:number;points:{temp:number;output:number}[]};
type Status = {ok:boolean;error?:string;message?:string;revision?:string;state?:string;mode?:number;gaming?:boolean;
  autostart?:boolean;boot_plan?:{slot:number};has_transaction?:boolean;temperature?:number;rpm?:number[];initial?:Draft;
  settings?:{selected:number;presets:Record<string,Draft|null>};applied?:{slot:number;draft:Draft}};
const read = callable<[],Status>('get_fan_status');
const mutate = callable<[Record<string,unknown>],Status>('mutate');
let cachedStatus:Status|null=null;
let queue:Promise<unknown> = Promise.resolve();
function send(action:Record<string,unknown>) {
  const task=queue.then(async()=>{
    const s=await read();
    if(!s.revision) throw new Error(s.error || 'Fan service unavailable');
    const result=await mutate({...action,request_id:crypto.randomUUID(),expected_revision:s.revision});
    if(!result.ok) throw new Error(result.error || 'Fan operation failed');
    return result;
  });
  queue=task.catch(()=>undefined);return task;
}
export function FanPanel() {
  const cachedSlot=cachedStatus?.settings?.selected===2?2:1;
  const [s,setS]=useState<Status>(cachedStatus || {ok:false}),[slot,setSlot]=useState(cachedSlot),[draft,setDraft]=useState<Draft|null>(cachedStatus?.settings?.presets[String(cachedSlot)] || cachedStatus?.initial || null);
  const [enabled,setEnabled]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[edit,setEdit]=useState<number|null>(null);
  const alive=useRef(false),initialized=useRef(false),timer=useRef<ReturnType<typeof setTimeout>|null>(null);
  const pending=useRef<{slot:number;draft:Draft}|null>(null);
  const drafts=useRef<Record<string,Draft>>({});
  function run(action:Record<string,unknown>) {
    const blocking=action.method!=='fan_save';
    if(blocking)setBusy(true);setError('');
    return send(action).then(result=>{cachedStatus=result;if(alive.current)setS(result);return result;})
      .catch(e=>{if(alive.current)setError(String(e));throw e;})
      .finally(()=>{if(alive.current && blocking)setBusy(false);});
  }
  function flush() {
    if(timer.current)clearTimeout(timer.current);timer.current=null;
    const value=pending.current;pending.current=null;
    if(value)void run({method:'fan_save',...value}).catch(()=>undefined);
  }
  useEffect(()=>{
    alive.current=true;let fetching=false;
    const refresh=async()=>{
      if(fetching)return;fetching=true;
      try {
        await queue;const result=await read();if(!alive.current)return;cachedStatus=result;setS(result);
        if(result.ok && result.settings && result.initial && !initialized.current){
          for(const [key,value] of Object.entries(result.settings.presets)){if(value)drafts.current[key]=value;}
          const selected=[1,2].includes(result.settings.selected)?result.settings.selected:1;
          setSlot(selected);setDraft(result.settings.presets[String(selected)] || result.initial);
          setEnabled(result.state==='active');initialized.current=true;
        }
      }catch(e){if(alive.current)setError(String(e));}finally{fetching=false;}
    };
    void refresh();const id=setInterval(refresh,2000);
    return ()=>{flush();alive.current=false;clearInterval(id);};
  },[]);
  function change(next:Draft) {
    drafts.current[String(slot)]=next;
    setDraft(next);pending.current={slot,draft:next};
    if(timer.current)clearTimeout(timer.current);timer.current=setTimeout(flush,600);
  }
  function select(value:number){
    if(value===slot)return;
    flush();const next=drafts.current[String(value)] || s.initial;
    if(!next)return;setSlot(value);setDraft(next);setEdit(null);
    void run({method:'fan_save',slot:value,draft:next}).catch(()=>undefined);
  }
  function toggle(value:boolean){
    if(value){setEnabled(true);return;}
    flush();void run({method:'fan_restore'}).then(()=>setEnabled(false)).catch(()=>undefined);
  }
  const dirty=!s.applied || s.applied.slot!==slot || JSON.stringify(s.applied.draft)!==JSON.stringify(draft);
  const disabled=busy || !draft;
  const states:Record<string,string>={idle:s.mode===1?'External custom control':'System automatic',active:'Custom settings active',conflict:'External settings changed',restoring:'Restoring',recovery_required:'Recovery required'};
  return <PanelSection title="Dual-fan control">
    <PanelSectionRow><ToggleField label="Enable fan control" checked={enabled || !!s.has_transaction} disabled={busy || !s.revision} onChange={toggle}/></PanelSectionRow>
    <PanelSectionRow><ToggleField label="Restore fans at startup" checked={s.autostart===true}
      disabled={busy || !s.revision} onChange={value=>{flush();void run({method:'fan_autostart',enabled:value}).catch(()=>undefined);}}/></PanelSectionRow>
    <PanelSectionRow><div style={{fontSize:12,lineHeight:1.5,opacity:.8}}>
      {s.autostart?`Next boot, restore preset ${s.boot_plan?.slot ?? '—'}.`:'Off: keep automatic control at boot. Apply a preset before enabling.'}
    </div></PanelSectionRow>
    <PanelSectionRow><div style={{fontSize:13,lineHeight:1.7}}>
      Current: {s.ok ? states[s.state || 'idle'] : 'Unavailable'}<br/>
      Temperature {s.ok ? s.temperature : '—'}°C<br/>
      Fan 1: {s.ok ? s.rpm?.[0] : '—'} RPM · Fan 2: {s.ok ? s.rpm?.[1] : '—'} RPM
    </div></PanelSectionRow>
    <PanelSectionRow><Focusable noFocusRing flow-children="horizontal" style={{display:'grid',gridTemplateColumns:'repeat(2, minmax(0, 1fr))',gap:8,width:'100%',minWidth:0,padding:'8px 0'}}>
      {[1,2].map(i=><DialogButton style={{minWidth:0,width:'100%',margin:0,padding:'10px 4px',boxSizing:'border-box',whiteSpace:'nowrap'}} key={i} disabled={disabled} onClick={()=>select(i)}>{slot===i?'✓ ':''}Preset {i}</DialogButton>)}
    </Focusable></PanelSectionRow>
    <PanelSectionRow><Focusable noFocusRing flow-children="horizontal" style={{display:'grid',gridTemplateColumns:'repeat(2, minmax(0, 1fr))',gap:8,width:'100%',minWidth:0,padding:'8px 0'}}>
      {(['fixed','curve'] as const).map(mode=><DialogButton key={mode} style={{minWidth:0,width:'100%',margin:0,padding:'10px 4px',boxSizing:'border-box',whiteSpace:'nowrap',fontSize:13}} disabled={disabled}
        onClick={()=>{if(draft && draft.mode!==mode){setEdit(null);change({...draft,mode});}}}>
        {draft?.mode===mode?'● ':'○ '}{mode==='fixed'?'Fixed output':'Fan curve'}
      </DialogButton>)}
    </Focusable></PanelSectionRow>
    {draft?.mode==='fixed' ? <>
      <PanelSectionRow><SliderField label="Fan output" min={0} max={100} step={1} value={draft.fixed} showValue valueSuffix="%" disabled={busy} onChange={v=>change({...draft,fixed:v})}/></PanelSectionRow>
      <PanelSectionRow><Focusable noFocusRing flow-children="horizontal" style={{display:'grid',gridTemplateColumns:'repeat(2, minmax(0, 1fr))',gap:8,width:'100%',minWidth:0}}>{[-5,5].map(v=><DialogButton style={{minWidth:0,width:'100%',margin:0,padding:'10px 4px',boxSizing:'border-box'}} key={v} disabled={busy} onClick={()=>change({...draft,fixed:Math.max(0,Math.min(100,draft.fixed+v))})}>{v>0?'+5%':'−5%'}</DialogButton>)}</Focusable></PanelSectionRow>
    </> : draft?.points.map((point,i)=><Fragment key={i}><PanelSectionRow>
      <ButtonItem layout="below" disabled={busy} onClick={()=>setEdit(edit===i?null:i)}><span style={{display:'flex',justifyContent:'space-between',gap:8,width:'100%'}}><span>{point.temp}°C</span><span>{point.output}%</span><span>{edit===i?'Done':'Edit'}</span></span></ButtonItem></PanelSectionRow>
      {edit===i && <>
        <PanelSectionRow><SliderField label="Temperature" min={i===0?0:draft.points[i-1].temp+1} max={i===5?100:draft.points[i+1].temp-1} step={1} value={point.temp} showValue valueSuffix="°C" disabled={busy} onChange={v=>change({...draft,points:draft.points.map((p,j)=>j===i?{...p,temp:v}:p)})}/></PanelSectionRow>
        <PanelSectionRow><SliderField label="Output" min={i===0?0:draft.points[i-1].output} max={i===5?100:draft.points[i+1].output} step={1} value={point.output} showValue valueSuffix="%" disabled={busy} onChange={v=>change({...draft,points:draft.points.map((p,j)=>j===i?{...p,output:v}:p)})}/></PanelSectionRow>
      </>}
    </Fragment>)}
    <PanelSectionRow><div style={{fontSize:12,lineHeight:1.6}}>
      Editing preset {slot}; active: {s.applied && s.state==='active'?`Preset ${s.applied.slot}`:'None'}<br/>
      {busy?'Saving / updating…':dirty?'Unapplied changes':'Settings applied'}<br/>
      Select Apply to use custom output. Missing RPM feedback restores automatic control. The toggle above controls startup behavior.
      {(error || s.error || s.message) && <div style={{color:'#ffbf80'}}>{error || s.error || s.message}</div>}
    </div></PanelSectionRow>
    <PanelSectionRow><ButtonItem layout="below" disabled={disabled || !enabled || !s.gaming || !s.ok} onClick={()=>{flush();void run({method:'fan_apply',slot,draft}).catch(()=>undefined);}}>Apply settings</ButtonItem></PanelSectionRow>
  </PanelSection>;
}
