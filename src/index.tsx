import { BootToggle } from './BootToggle';
import { PowerPanel } from './PowerPanel';
import { FanPanel } from './FanPanel';
import { useEffect, useRef, useState } from 'react';
import { definePlugin, callable } from '@decky/api';
import { PanelSection, PanelSectionRow, ButtonItem, SliderField, ToggleField, DialogButton, Focusable } from '@decky/ui';

type Values = { p_max_khz: number; e_max_khz: number };
type Policy = {request_max?:number; group: 'P'|'E'; min: number; max: number; hw_min: number; hw_max: number};
type Status = {ok: boolean; error?: string; revision?: string; state?: string; message?: string;
  turbo_enabled?: boolean|null; gaming?: boolean; has_transaction?: boolean; policies?: Record<string, Policy>;
  presets?: Record<string, Values|null>; applied?: Values & {name: string}; ui?: {name:string; independent:boolean}};
const getStatus = callable<[], Status>('get_status');
const mutate = callable<[Record<string, unknown>], Status>('mutate');
const names: Record<string, string> = {default:'Default', balanced:'Balanced', powersave:'Power saving'};
const states: Record<string, string> = {idle:'CPU managed by system', active:'Frequency limits active', conflict:'External settings changed', recovery_required:'Recovery required', read_only:'Read-only', restoring:'Restoring'};
// Shared across panel mounts, so closing/reopening cannot reorder a final edit.
let writes: Promise<unknown> = Promise.resolve();
function submit(action: Record<string, unknown>): Promise<Status> {
  const result = writes.then(async () => {
    const latest = await getStatus();
    if (!latest.ok || !latest.revision) throw new Error(latest.error || 'Service unavailable');
    return mutate({...action,request_id:crypto.randomUUID(),expected_revision:latest.revision});
  });
  writes = result.catch(()=>undefined);
  return result;
}
function groupBounds(s: Status, group: 'P'|'E') {
  const ps = Object.values(s.policies || {}).filter(x=>x.group===group);
  return {min:ps.length ? Math.ceil(Math.max(...ps.map(x=>Math.max(x.min,x.hw_min)))/100000)*100 : 400,
    max:ps.length ? Math.floor(Math.max(...ps.map(x=>x.request_max ?? x.hw_max))/100000)*100 : (group==='P'?4700:3400)};
}
function presetValues(s: Status, name: string) {
  const saved = s.presets?.[name];
  if (saved) return {p:saved.p_max_khz/1000,e:saved.e_max_khz/1000};
  const fraction = name==='default' ? 1 : .7;
  const value = (g:'P'|'E') => {const b=groupBounds(s,g);return Math.max(b.min,Math.min(b.max,Math.round(b.max*fraction/100)*100));};
  return {p:value('P'),e:value('E')};
}
function Content() {
  const [status,setStatus] = useState<Status>({ok:false});
  const [name,setName] = useState('default'), [independent,setIndependent] = useState(false);
  const [ratio,setRatio] = useState(100), [p,setP] = useState(4700), [e,setE] = useState(3400);
  const [ready,setReady] = useState(false), [busy,setBusy] = useState(false), [pending,setPending] = useState(false);
  const [error,setError] = useState('');
  const alive = useRef(false), initialized = useRef(false), timer = useRef<ReturnType<typeof setTimeout>|null>(null);
  const draft = useRef<Record<string,unknown>|null>(null);
  function update(s: Status) { if (alive.current) setStatus(s); }
  function execute(action: Record<string,unknown>) {
    if (alive.current) {setBusy(true);setPending(false);setError('');}
    return submit(action).then(s=>{
      if (s.revision) update(s);
      if (!s.ok) throw new Error(s.error || 'Operation incomplete');
    }).catch(err=>{if(alive.current) setError(String(err));})
      .finally(()=>{if(alive.current)setBusy(false);});
  }
  function flush() {
    if(timer.current) clearTimeout(timer.current);
    timer.current=null;
    const action=draft.current; draft.current=null;
    if(action) void execute(action);
  }
  useEffect(()=>{
    alive.current=true;
    let fetching=false;
    const refresh=async()=>{
      if(fetching) return;
      fetching=true;
      try {
        await writes;
        const s=await getStatus();
        if(!alive.current)return;
        update(s);
        if(s.ok && s.revision && Object.keys(s.policies || {}).length===10 && !initialized.current) {
          const selected=s.ui?.name && names[s.ui.name] ? s.ui.name : 'default';
          const v=presetValues(s,selected);
          setName(selected);setIndependent(s.ui?.independent || false);setP(v.p);setE(v.e);
          setRatio(Math.round(v.p/groupBounds(s,'P').max*100));
          initialized.current=true;setReady(true);
        }
      } catch(err) {if(alive.current)setError(String(err));}
      finally{fetching=false;}
    };
    void refresh();const poll=setInterval(refresh,3000);
    return ()=>{alive.current=false;clearInterval(poll);flush();};
  },[]);
  const pb=groupBounds(status,'P'), eb=groupBounds(status,'E');
  const minimumPercent=Math.min(100,Math.ceil(Math.max(pb.min/pb.max,eb.min/eb.max)*100));
  const percent=Math.max(minimumPercent,Math.min(100,ratio));
  function linkedValues(value:number) {
    return {p:Math.max(pb.min,Math.min(pb.max,Math.round(pb.max*value/100/100)*100)),
      e:Math.max(eb.min,Math.min(eb.max,Math.round(eb.max*value/100/100)*100))};
  }
  function schedule(nextP:number,nextE:number) {
    setP(nextP);setE(nextE);setError('');setPending(true);
    draft.current={method:'configure',name,independent,p_max_khz:Math.round(nextP*1000),e_max_khz:Math.round(nextE*1000)};
    if(timer.current)clearTimeout(timer.current);
    timer.current=setTimeout(flush,600);
  }
  function select(next:string) {
    const v=presetValues(status,next);
    setName(next);setP(v.p);setE(v.e);setRatio(Math.round(v.p/pb.max*100));
    // An explicit selection applies an existing preset; first use only selects it.
    const action=status.presets?.[next]
      ? {method:'configure',name:next,independent,p_max_khz:Math.round(v.p*1000),e_max_khz:Math.round(v.e*1000)}
      : {method:'save_ui',name:next,independent};
    void execute(action);
  }
  function toggleMode() {
    setIndependent(!independent);setRatio(Math.round(p/pb.max*100));
    void execute({method:'save_ui',name,independent:!independent});
  }
  function restore() {
    if(timer.current)clearTimeout(timer.current);
    timer.current=null;draft.current=null;setPending(false);
    void execute({method:'restore'});
  }
  const editable=ready && Object.keys(status.policies || {}).length===10 && ['idle','active'].includes(status.state || '') && !busy;
  const linked=linkedValues(percent), pairDiffers=linked.p!==p || linked.e!==e;
  return <>
    <PanelSection title="CPU frequency limits">
      <PanelSectionRow><div style={{fontSize:13,lineHeight:1.6}}>
        {states[status.state || ''] || 'Connecting to service'}{status.applied ? ` · ${names[status.applied.name]}` : ''}<br/>
        {busy ? 'Saving / updating…' : pending ? 'Release to save and apply…' : 'Autosave enabled'}
        {!status.gaming && <div>Desktop mode saves only. Select or adjust a preset in game mode to apply.</div>}
        {status.message && <div>{status.message}</div>}
        {(error || status.error) && <div style={{color:'#ffbf80'}}>{error || status.error}</div>}
      </div></PanelSectionRow>
      <PanelSectionRow><ToggleField label="CPU Turbo" checked={status.turbo_enabled===true}
        disabled={!editable || pending || !status.gaming || status.turbo_enabled==null}
        onChange={enabled=>void execute({method:'set_turbo',enabled})}/></PanelSectionRow>
      <PanelSectionRow><div style={{fontSize:12,opacity:.7}}>With Turbo off, base frequency also limits the CPU. Startup restore is optional.</div></PanelSectionRow>
      <BootToggle feature="turbo" label="Restore Turbo at startup"/>
      {Object.entries(names).map(([key,label])=><PanelSectionRow key={key}>
        <ButtonItem layout="below" disabled={!editable || pending} onClick={()=>select(key)}>{name===key?'✓ ':''}{label}</ButtonItem>
      </PanelSectionRow>)}
      <PanelSectionRow><ButtonItem layout="below" disabled={!ready || busy || pending} onClick={toggleMode}>
        {independent?'P/E mode: Separate':'P/E mode: Linked'}
      </ButtonItem></PanelSectionRow>
      {independent ? <>
        <PanelSectionRow><SliderField label="P-core limit" value={p} min={pb.min} max={pb.max} step={100} showValue valueSuffix=" MHz" disabled={!editable} onChange={v=>schedule(v,e)}/></PanelSectionRow>
        <PanelSectionRow><SliderField label="E-core limit" value={e} min={eb.min} max={eb.max} step={100} showValue valueSuffix=" MHz" disabled={!editable} onChange={v=>schedule(p,v)}/></PanelSectionRow>
      </> : <PanelSectionRow><SliderField label="CPU limit ratio" value={percent} min={minimumPercent} max={100} step={1} showValue valueSuffix="%" disabled={!editable} onChange={v=>{setRatio(v);const next=linkedValues(v);schedule(next.p,next.e);}}/></PanelSectionRow>}
      <PanelSectionRow><div style={{fontSize:13,lineHeight:1.6}}>
        Target: P {p} MHz · E {e} MHz<br/>
        {!independent && pairDiffers && <>Separate values are retained until you move the linked slider.<br/></>}
        {!status.presets?.[name] && <>Preset not configured. Move a slider to save it.<br/></>}
        Applies 0.6 seconds after adjustment. Default is also editable.<br/>
        Reopening does not apply settings. The toggle below controls startup restore.
      </div></PanelSectionRow>
      <BootToggle feature="cpu" label="Restore CPU at startup"/>
      <PanelSectionRow><ButtonItem layout="below" disabled={!ready || busy || (!pending && !status.has_transaction)} onClick={restore}>Restore original settings</ButtonItem></PanelSectionRow>
      <PanelSectionRow><div style={{fontSize:12,opacity:.7}}>Restore original per-core limits and Turbo state. Presets are retained.</div></PanelSectionRow>
    </PanelSection>
    <PanelSection title="Frequency readback">
      {(['P','E'] as const).map(group=><PanelSectionRow key={group}><div style={{fontSize:13}}>{group} cores:{' '}
        {[...new Set(Object.values(status.policies || {}).filter(x=>x.group===group).map(x=>x.max/1000))].sort((a,b)=>a-b).join(' / ') || '—'} MHz
      </div></PanelSectionRow>)}
      <PanelSectionRow><div style={{fontSize:12,opacity:.7}}>CPU limits only. Adjust PL1/PL2 on the Power page. Actual frequency may be lower.</div></PanelSectionRow>
    </PanelSection>
  </>;
}
function Pages() {
  const [page,setPage]=useState('cpu');
  return <><PanelSection><PanelSectionRow><Focusable flow-children="horizontal" style={{display:'grid',gridTemplateColumns:'repeat(3, minmax(0, 1fr))',gap:8,width:'100%',minWidth:0}}>
    <DialogButton style={{minWidth:0,width:'100%',margin:0,padding:'10px 4px',boxSizing:'border-box'}} onClick={()=>setPage('cpu')}>{page==='cpu'?'✓ ':''}CPU</DialogButton>
    <DialogButton style={{minWidth:0,width:'100%',margin:0,padding:'10px 4px',boxSizing:'border-box'}} onClick={()=>setPage('fan')}>{page==='fan'?'✓ ':''}Fans</DialogButton>
    <DialogButton style={{minWidth:0,width:'100%',margin:0,padding:'10px 4px',boxSizing:'border-box'}} onClick={()=>setPage('power')}>{page==='power'?'✓ ':''}Power</DialogButton>
  </Focusable></PanelSectionRow></PanelSection>{page==='cpu'?<Content/>:page==='fan'?<FanPanel/>:<PowerPanel/>}</>;
}
export default definePlugin(()=>({name:'Claw EX Control',titleView:<div>Claw EX Control</div>,content:<Pages/>,icon:<span>CPU</span>}));
