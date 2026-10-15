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
const names: Record<string, string> = {default:'默认', balanced:'平衡', powersave:'省电'};
const states: Record<string, string> = {idle:'未接管 CPU', active:'限频生效', conflict:'外部配置变化', recovery_required:'需要恢复', read_only:'只读', restoring:'恢复中'};
// Shared across panel mounts, so closing/reopening cannot reorder a final edit.
let writes: Promise<unknown> = Promise.resolve();
function submit(action: Record<string, unknown>): Promise<Status> {
  const result = writes.then(async () => {
    const latest = await getStatus();
    if (!latest.ok || !latest.revision) throw new Error(latest.error || '服务不可用');
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
      if (!s.ok) throw new Error(s.error || '操作未完成');
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
    <PanelSection title="CPU 最高频率">
      <PanelSectionRow><div style={{fontSize:13,lineHeight:1.6}}>
        {states[status.state || ''] || '连接服务中'}{status.applied ? ` · ${names[status.applied.name]}` : ''}<br/>
        {busy ? '正在保存 / 更新…' : pending ? '停止调节后自动保存并应用…' : '已开启自动保存'}
        {!status.gaming && <div>桌面模式只保存配置；游戏模式中主动选择或调整才生效。</div>}
        {status.message && <div>{status.message}</div>}
        {(error || status.error) && <div style={{color:'#ffbf80'}}>{error || status.error}</div>}
      </div></PanelSectionRow>
      <PanelSectionRow><ToggleField label="CPU 睿频" checked={status.turbo_enabled===true}
        disabled={!editable || pending || !status.gaming || status.turbo_enabled==null}
        onChange={enabled=>void execute({method:'set_turbo',enabled})}/></PanelSectionRow>
      <PanelSectionRow><div style={{fontSize:12,opacity:.7}}>关闭睿频后，实际频率上限还受基准频率限制。开关不随预设保存或开机自动应用。</div></PanelSectionRow>
      {Object.entries(names).map(([key,label])=><PanelSectionRow key={key}>
        <ButtonItem layout="below" disabled={!editable || pending} onClick={()=>select(key)}>{name===key?'✓ ':''}{label}</ButtonItem>
      </PanelSectionRow>)}
      <PanelSectionRow><ButtonItem layout="below" disabled={!ready || busy || pending} onClick={toggleMode}>
        {independent?'调整方式：P/E 独立':'调整方式：P/E 联动'}
      </ButtonItem></PanelSectionRow>
      {independent ? <>
        <PanelSectionRow><SliderField label="P 核最高频率" value={p} min={pb.min} max={pb.max} step={100} showValue valueSuffix=" MHz" disabled={!editable} onChange={v=>schedule(v,e)}/></PanelSectionRow>
        <PanelSectionRow><SliderField label="E 核最高频率" value={e} min={eb.min} max={eb.max} step={100} showValue valueSuffix=" MHz" disabled={!editable} onChange={v=>schedule(p,v)}/></PanelSectionRow>
      </> : <PanelSectionRow><SliderField label="CPU 频率上限比例" value={percent} min={minimumPercent} max={100} step={1} showValue valueSuffix="%" disabled={!editable} onChange={v=>{setRatio(v);const next=linkedValues(v);schedule(next.p,next.e);}}/></PanelSectionRow>}
      <PanelSectionRow><div style={{fontSize:13,lineHeight:1.6}}>
        配置：P 核 {p} MHz · E 核 {e} MHz<br/>
        {!independent && pairDiffers && <>当前保留独立数值，拖动后按比例联动。<br/></>}
        {!status.presets?.[name] && <>此预设尚未配置，移动滑块后保存。<br/></>}
        停止调节约 0.6 秒后生效。“默认”也可自定义。<br/>
        重新打开页面只记住选择；开机不自动限频。
      </div></PanelSectionRow>
      <PanelSectionRow><ButtonItem layout="below" disabled={!ready || busy || (!pending && !status.has_transaction)} onClick={restore}>恢复系统原始配置</ButtonItem></PanelSectionRow>
      <PanelSectionRow><div style={{fontSize:12,opacity:.7}}>恢复本次接管前各核心上限，以及本插件修改前的睿频状态；不删除预设。</div></PanelSectionRow>
    </PanelSection>
    <PanelSection title="当前回读上限">
      {(['P','E'] as const).map(group=><PanelSectionRow key={group}><div style={{fontSize:13}}>{group} 核：
        {[...new Set(Object.values(status.policies || {}).filter(x=>x.group===group).map(x=>x.max/1000))].sort((a,b)=>a-b).join(' / ') || '—'} MHz
      </div></PanelSectionRow>)}
      <PanelSectionRow><div style={{fontSize:12,opacity:.7}}>只控制 CPU 频率上限；TDP 仍由原有工具管理。实际运行频率可能更低。</div></PanelSectionRow>
    </PanelSection>
  </>;
}
function Pages() {
  const [page,setPage]=useState('cpu');
  return <><PanelSection><PanelSectionRow><Focusable flow-children="horizontal" style={{display:'grid',gridTemplateColumns:'repeat(2, minmax(0, 1fr))',gap:8,width:'100%',minWidth:0}}>
    <DialogButton style={{minWidth:0,width:'100%',margin:0,padding:'10px 4px',boxSizing:'border-box'}} onClick={()=>setPage('cpu')}>{page==='cpu'?'✓ ':''}CPU</DialogButton>
    <DialogButton style={{minWidth:0,width:'100%',margin:0,padding:'10px 4px',boxSizing:'border-box'}} onClick={()=>setPage('fan')}>{page==='fan'?'✓ ':''}风扇</DialogButton>
  </Focusable></PanelSectionRow></PanelSection>{page==='cpu'?<Content/>:<FanPanel/>}</>;
}
export default definePlugin(()=>({name:'Claw EX Control',titleView:<div>Claw EX Control</div>,content:<Pages/>,icon:<span>CPU</span>}));
