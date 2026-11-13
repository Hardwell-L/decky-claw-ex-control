const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),ts=require('typescript');
let hooks=[],cursor=0,effects=[],tree,requests=[],poll,timers=new Map(),nextTimer=0,readGate=null;
const snapshot={ok:true,revision:'rev',state:'idle',gaming:true,has_transaction:false,current:{profile:'custom',pl1:17,pl2:17,pl1_min:8,pl1_max:35,pl2_min:9,pl2_max:45}};
const react={useState(v){let i=cursor++;if(!(i in hooks))hooks[i]=v;return[hooks[i],x=>hooks[i]=x];},useRef(v){let i=cursor++;if(!(i in hooks))hooks[i]={current:v};return hooks[i];},useEffect(f){let i=cursor++;if(!(i in hooks)){hooks[i]={};effects.push(()=>{hooks[i].cleanup=f();});}}};
const jsx=(type,props)=>({type,props});
const context={exports:{},require:n=>n==='./i18n'?{localize:x=>x,useLanguage:()=>{}}:n==='react'?react:n==='react/jsx-runtime'?{jsx,jsxs:jsx}:n==='@decky/api'?{callable:route=>async req=>{if(route==='mutate'){requests.push(req);if(req.method==='power_apply'){snapshot.state='active';snapshot.has_transaction=true;}if(req.method==='power_restore'){snapshot.state='idle';snapshot.has_transaction=false;}}if(route!=='mutate'&&readGate)await readGate;return structuredClone(snapshot);}}:{PanelSection:'section',PanelSectionRow:'row',ButtonItem:'button',SliderField:'slider'},setTimeout:(fn)=>{const id=++nextTimer;timers.set(id,fn);return id;},clearTimeout:id=>timers.delete(id),setInterval:f=>{poll=f;return 1;},clearInterval:()=>{},crypto:require('node:crypto')};
vm.runInNewContext(ts.transpileModule(fs.readFileSync('src/PowerPanel.tsx','utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2020}}).outputText,context);
function render(){cursor=0;tree=context.exports.PowerPanel();effects.splice(0).forEach(f=>f());}
async function settle(){for(let i=0;i<5;i++)await new Promise(r=>setImmediate(r));render();}
function all(n){return !n||typeof n!=='object'?[]:Array.isArray(n)?n.flatMap(all):[n,...all(n.props?.children)];}
const button=t=>all(tree).find(n=>n.type==='button'&&n.props.children===t);
const slider=t=>all(tree).find(n=>n.type==='slider'&&n.props.label===t);
(async()=>{
 render();await settle();assert.equal(requests.length,0);
 slider('PL1 sustained limit').props.onChange(20);render();assert.equal(requests.length,0);
 assert.equal(button('Apply power limits').props.disabled,true);
 slider('PL2 boost limit').props.onChange(30);render();assert.equal(requests.length,0);
 for(const fn of timers.values())fn();timers.clear();await settle();assert.equal(requests.at(-1).method,'power_apply');
 assert.equal(requests.length,1,'slider changes coalesced into one application');
 button('Save settings').props.onClick();await settle();assert.equal(requests.at(-1).method,'power_save');
 button('Apply power limits').props.onClick();await settle();assert.equal(requests.at(-1).method,'power_apply');assert.equal(requests.at(-1).pl2,30);
 button('Restore system control').props.onClick();await settle();assert.equal(requests.at(-1).method,'power_restore');
 snapshot.gaming=false;await poll();render();assert.equal(button('Apply power limits').props.disabled,true);
 snapshot.gaming=true;snapshot.current.profile='performance';await poll();render();assert.equal(button('Apply power limits').props.disabled,false);
 slider('PL2 boost limit').props.onChange(31);render();
 for(const fn of timers.values())fn();timers.clear();await settle();assert.equal(requests.at(-1).method,'power_apply','RAPL does not require custom profile');
 snapshot.current.profile='custom';await poll();render();
 slider('PL2 boost limit').props.onChange(32);render();
 button('Restore system control').props.onClick();await settle();
 for(const fn of timers.values())fn();timers.clear();await settle();assert.equal(requests.at(-1).method,'power_restore','restore cancels pending apply');
 let releaseRead;readGate=new Promise(resolve=>releaseRead=resolve);
 const before=requests.length;
 slider('PL2 boost limit').props.onChange(33);render();
 for(const fn of timers.values())fn();timers.clear();await settle();
 slider('PL2 boost limit').props.onChange(34);render();
 for(const fn of timers.values())fn();timers.clear();
 releaseRead();readGate=null;await settle();
 assert.equal(requests.length,before+1,'obsolete queued automatic request is discarded');
 assert.equal(requests.at(-1).pl2,34,'only newest queued value is applied');
 slider('PL2 boost limit').props.onChange(33);render();
 for(const h of hooks)h?.cleanup?.();await settle();assert.equal(requests.at(-1).pl2,33,'panel close flushes final edit');
 console.log('Power UI smoke passed: debounced apply, profile-independent apply, latest queued value, restore cancellation, close flush');
})().catch(e=>{console.error(e);process.exitCode=1;});
