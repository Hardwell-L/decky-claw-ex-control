const fs=require('fs'),vm=require('vm'),ts=require('typescript'),assert=require('assert/strict');
const effects=[];let steam='zh';const store={};
const jsx=(type,props)=>({type,props});
const c={exports:{},localStorage:{getItem:k=>store[k],setItem:(k,v)=>store[k]=v},setInterval:()=>1,clearInterval:()=>{},require:n=>n==='react'?{useState:()=>[0,()=>{}],useEffect:f=>effects.push(f),isValidElement:x=>!!x?.props,cloneElement:(x,p)=>({...x,props:{...x.props,...p}})}:n==='react/jsx-runtime'?{jsx,jsxs:jsx}:n==='@decky/api'?{callable:()=>async()=>({language:steam})}:{PanelSectionRow:'row',ButtonItem:'button'}};
vm.runInNewContext(ts.transpileModule(fs.readFileSync('src/i18n.tsx','utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText,c);
(async()=>{
 c.exports.useLanguage();effects.splice(0).forEach(f=>f());await new Promise(r=>setImmediate(r));
 assert.equal(c.exports.translate('Fans'),'风扇');assert.equal(c.exports.translate('Preset 2'),'方案 2');
 const event=()=>{};const node=c.exports.localize({type:'slider',props:{label:'Output',onChange:event,value:35}});
 assert.equal(node.props.label,'输出');assert.equal(node.props.onChange,event);assert.equal(node.props.value,35);
 c.exports.LanguageSelector().props.children.props.onClick();assert.equal(store['claw-ex-language'],'zh');
 c.exports.LanguageSelector().props.children.props.onClick();assert.equal(c.exports.translate('Fans'),'Fans');
 assert.equal(c.exports.translate('unknown backend error'),'unknown backend error');
 console.log('Locale tests passed: automatic Chinese, manual override, translated labels, unchanged handlers/values.');
})().catch(e=>{console.error(e);process.exitCode=1;});
