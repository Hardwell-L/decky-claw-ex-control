import {cloneElement,isValidElement,ReactNode,useEffect,useState} from 'react';
import {callable} from '@decky/api';
import {PanelSectionRow,ButtonItem} from '@decky/ui';
type Mode='auto'|'zh'|'en';
let mode:Mode='auto',automatic='en';
try{const value=localStorage.getItem('claw-ex-language');if(value==='zh'||value==='en')mode=value;}catch{}
const listeners=new Set<()=>void>();
let timer:ReturnType<typeof setInterval>|null=null;
const read=callable<[],{language:string}>('get_ui_language');
const notify=()=>listeners.forEach(fn=>fn());
export function useLanguage(){
 const [,setRevision]=useState(0);
 useEffect(()=>{const update=()=>setRevision(v=>v+1);listeners.add(update);
  if(!timer){const refresh=async()=>{try{const result=await read();const next=result.language==='zh'?'zh':'en';if(next!==automatic){automatic=next;notify();}}catch{}};void refresh();timer=setInterval(refresh,10000);}
  return()=>{listeners.delete(update);if(!listeners.size&&timer){clearInterval(timer);timer=null;}};
 },[]);
}
const dictionary:Record<string,string>={
'Target: PL1':'目标：PL1',
'Waiting for delayed readback':'等待延迟复查',
'Delayed readback verified':'延迟复查通过',
'External changes detected; control released':'检测到外部改写，已让出控制',
'Limited by lower MSR limits; MSR was not changed.':'受到更低的 MSR 上限限制；未改写 MSR。',
'External RAPL changes release control; the plugin does not repeatedly overwrite them.':'外部改动 RAPL 时让出控制，不循环争抢设置。',
'Startup and resume restore are opt-in and wait for stable RAPL limits for 10 seconds. Readback is checked again after 1 and 5 seconds.':'启用自动恢复后，开机和唤醒等待 RAPL 稳定 10 秒再应用；写入后约 1 秒和 5 秒复查。',
'These are power limits, not live power consumption.':'此处是功耗上限，不是实时耗电量。',
'RAPL limits or interface changed; plugin control stopped':'RAPL 上限或接口已变化，插件停止控制',
'RAPL limits or interface changed; control released':'RAPL 上限或接口已变化，已让出控制',
'Readback matches; limited by MSR':'回读一致，但受到 MSR 上限限制',
'RAPL readback matches; actual consumption may be lower':'RAPL 回读一致，实际耗电量可能更低',

'Temperature nodes are set by the firmware. Edit output only; percentage is not RPM.':'温度节点由固件设定，只修改输出；百分比不代表固定转速。',
'Closing this panel keeps the applied curve. Disable control to return both fans to system automatic mode. Missing RPM feedback restores automatic control.':'关闭面板保持已应用曲线；关闭控制则双风扇交还系统自动管理。缺少转速反馈会恢复自动。',
'CPU':'CPU','Fans':'风扇','Power':'功耗','Default':'默认','Balanced':'平衡','Power saving':'省电',
'CPU frequency limits':'CPU 最高频率','CPU managed by system':'系统管理 CPU','Frequency limits active':'限频生效','External settings changed':'外部配置变化','Recovery required':'需要恢复','Read-only':'只读','Restoring':'恢复中','Connecting to service':'连接服务中',
'Saving / updating…':'正在保存／更新…','Release to save and apply…':'停止调节后保存并应用…','Autosave enabled':'已开启自动保存',
'Desktop mode saves only. Select or adjust a preset in game mode to apply.':'桌面模式只保存；游戏模式中选择或调整预设才生效。',
'CPU Turbo':'CPU 睿频','With Turbo off, base frequency also limits the CPU. Startup restore is optional.':'关闭睿频后还受基准频率限制，可单独选择开机恢复。',
'Restore Turbo at startup':'开机恢复睿频','P/E mode: Separate':'P/E 分别调整','P/E mode: Linked':'P/E 联动调整','P-core limit':'P 核最高频率','E-core limit':'E 核最高频率','CPU limit ratio':'CPU 频率上限比例',
'Separate values are retained until you move the linked slider.':'拖动联动滑块前保留独立数值。','Preset not configured. Move a slider to save it.':'预设未配置，移动滑块后保存。','Applies 0.6 seconds after adjustment. Default is also editable.':'停止调节约 0.6 秒后生效，默认预设也可编辑。','Reopening does not apply settings. The toggle below controls startup restore.':'重新打开页面不自动应用；下方开关控制开机恢复。',
'Restore CPU at startup':'开机恢复 CPU 频率','Restore original settings':'恢复系统原始配置','Restore original per-core limits and Turbo state. Presets are retained.':'恢复接管前的核心上限和睿频状态，保留预设。','Frequency readback':'当前回读上限','CPU limits only. Adjust PL1/PL2 on the Power page. Actual frequency may be lower.':'此页只控制 CPU 上限，PL1/PL2 在功耗页调整；实际频率可能更低。',
'Dual-fan control':'双风扇控制','Enable fan control':'启用双风扇控制','Restore fans at startup':'开机恢复风扇方案','Off: keep automatic control at boot. Apply a preset before enabling.':'关闭时开机保持自动；先应用方案再开启。','External custom control':'外部自定义控制','System automatic':'系统自动','Custom settings active':'自定义已应用','Unavailable':'读取不可用','Fixed output':'固定输出','Fan curve':'温控曲线','Fan output':'双风扇输出','Done':'收起','Edit':'编辑','Temperature':'温度','Output':'输出','None':'无','Unapplied changes':'有未应用的修改','Settings applied':'设置已应用','Apply settings':'应用设置',
'Select Apply to use custom output. Missing RPM feedback restores automatic control. The toggle above controls startup behavior.':'点击应用执行自定义输出；缺少转速反馈会恢复自动。上方开关控制开机恢复。',
'PL1 / PL2 Power':'PL1／PL2 功耗','Plugin control active':'插件控制中','System managed':'系统管理','PL1 sustained limit':'PL1 长期功耗','PL2 boost limit':'PL2 短期功耗','PL2 must be at least PL1. Values must be within driver limits.':'PL2 不得低于 PL1，数值须在驱动范围内。','Save settings':'保存配置','Apply power limits':'应用功耗','Restore system control':'恢复系统控制','Restore power at startup':'自动恢复功耗',
'Desktop mode saves only. Apply in game mode.':'桌面模式只保存，在游戏模式中应用。','Enable custom TDP in Steam Performance settings first.':'请先在 Steam 性能菜单启用自定义 TDP。','Release to apply…':'停止调节后应用…','Updating…':'正在更新…','Changes save and apply automatically after 0.4 seconds.':'停止调节约 0.4 秒后自动保存并应用。','The plugin yields control when Steam changes power limits or the performance profile.':'Steam 改动功耗或性能模式时，插件让出控制。',
'Control returns to the system on game-mode exit, connection loss or resume. Startup restore waits for Steam custom TDP to remain stable for 10 seconds.':'退出游戏模式或连接中断时交还系统；启用自动恢复后，唤醒或重进游戏模式会等待设置稳定 10 秒再应用。','These are firmware limits, not live power consumption.':'此处是固件功耗限制，不是实时耗电量。',
'Apply settings successfully before enabling startup restore.':'先成功应用一次，再启用自动恢复。','Restore the last successfully applied settings when game mode starts.':'进入游戏模式后恢复上次成功应用的设置。',
'Power service unavailable':'功耗服务不可用','Fan service unavailable':'风扇服务不可用','Service unavailable':'服务不可用','Operation failed':'操作失败','Operation incomplete':'操作未完成','Fan operation failed':'风扇操作失败','Startup status unavailable':'自动恢复状态不可用','Could not save settings':'保存失败',
'Applied; control will be released if the system changes power limits':'已应用；系统改动功耗时让出控制','System power or performance profile changed; plugin control stopped':'系统功耗或性能模式变化，已让出控制','Original power limits restored; control returned to system':'已恢复原功耗，交还系统管理','System automatic control detected; previous transaction archived. You can apply a preset again.':'系统已恢复自动控制，旧记录已归档，可重新应用方案。',
};
export function translate(text:string):string{
 if((mode==='auto'?automatic:mode)!=='zh')return text;
 const clean=text.trim();if(dictionary[clean])return text.replace(clean,dictionary[clean]);
 const marked=clean.match(/^([✓●○]\s*)(.*)$/);if(marked)return marked[1]+translate(marked[2]);
 if(/^Preset \d+$/.test(clean))return text.replace('Preset','方案');
 if(/^Next boot, restore preset /.test(clean))return text.replace('Next boot, restore preset ','下次进入游戏模式后恢复方案 ');
 const fragments:Record<string,string>={'Current:':'当前：','Temperature':'温度','Fan 1:':'风扇①','RPM · Fan 2:':'RPM · 风扇②','Editing preset':'正在编辑方案','; active:':'；当前生效：','Target: P':'配置：P 核','MHz · E':'MHz · E 核','cores:':'核：','Readback: PL1':'回读：PL1'};
 if(fragments[clean])return text.replace(clean,fragments[clean]);return text;
}
export function localize(node:ReactNode):ReactNode{
 if(typeof node==='string')return translate(node);
 if(Array.isArray(node))return node.map(localize);
 if(!isValidElement<Record<string,unknown>>(node))return node;
 const props:Record<string,unknown>={};
 for(const key of ['label','title','description','children'])if(key in node.props)props[key]=localize(node.props[key] as ReactNode);
 return cloneElement(node,props);
}
export function LanguageSelector(){useLanguage();return <PanelSectionRow><ButtonItem layout="below" onClick={()=>{mode=mode==='auto'?'zh':mode==='zh'?'en':'auto';try{localStorage.setItem('claw-ex-language',mode);}catch{}notify();}}>{mode==='auto'?'Language / 语言: Auto':mode==='zh'?'语言：简体中文':'Language: English'}</ButtonItem></PanelSectionRow>;}
