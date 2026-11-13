const fs=require('fs'),vm=require('vm'),ts=require('typescript'),assert=require('assert/strict');
const effects=[],store={'claw-ex-language':'en'};let polls=0, ticks, steam='zh';
const react={useState:()=>[0,()=>{}],useEffect:f=>effects.push(f),isValidElement:x=>!!x?.props,cloneElement:(x,p)=>({...x,props:{...x.props,...p}})};
const modules={'react':react,'@decky/api':{callable:()=>async()=>({language:steam})}};
function load(file) {
 const context={exports:{},localStorage:{getItem:k=>store[k],setItem:(k,v)=>store[k]=v},
  setInterval:f=>{polls++;ticks=f;return 1},clearInterval:()=>{polls--},require:n=>modules[n]};
 vm.runInNewContext(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText,context);
 return context.exports;
}
(async()=>{
 const settings=modules['./languageSettings']=load('src/languageSettings.ts'),i18n=load('src/i18n.tsx');
 settings.useLanguage();settings.useLanguage();const cleanup=effects.splice(0).map(f=>f());await new Promise(r=>setImmediate(r));
 assert.equal(polls,1);assert.equal(i18n.translate('Fans'),'Fans');
 settings.setLanguageMode('auto');assert.equal(i18n.translate('General settings'),'通用设置');
 const event=()=>{};const node=i18n.localize({type:'slider',props:{label:'Output',onChange:event,value:35}});
 assert.equal(node.props.label,'输出');assert.equal(node.props.onChange,event);assert.equal(node.props.value,35);
 settings.setLanguageMode('zh');steam='en';await ticks();assert.equal(i18n.translate('Fans'),'风扇');
 assert.equal(store['claw-ex-language'],'zh');settings.setLanguageMode('auto');assert.equal(i18n.translate('Fans'),'Fans');
 cleanup.forEach(f=>f());assert.equal(polls,0);console.log('Shared language settings passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
