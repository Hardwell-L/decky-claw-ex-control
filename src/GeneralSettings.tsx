import {useState} from 'react';
import {ButtonItem, PanelSectionRow} from '@decky/ui';
import {translate} from './i18n';
import {LanguageMode, setLanguageMode, useLanguage} from './languageSettings';

const options: {mode: LanguageMode; label: string}[] = [
  {mode: 'auto', label: 'Automatic'},
  {mode: 'zh', label: '简体中文'},
  {mode: 'en', label: 'English'},
];

export function GeneralSettings() {
  const [expanded, setExpanded] = useState(false);
  const selected = useLanguage();
  return <>
    <PanelSectionRow>
      <ButtonItem layout="below" onClick={() => setExpanded(!expanded)}>
        {translate('General settings')} {expanded ? '▴' : '▾'}
      </ButtonItem>
    </PanelSectionRow>
    {expanded && <>
      <PanelSectionRow><div>{translate('Language')}</div></PanelSectionRow>
      {options.map(option => <PanelSectionRow key={option.mode}>
        <ButtonItem layout="below" onClick={() => setLanguageMode(option.mode)}>
          {selected === option.mode ? '✓ ' : ''}{translate(option.label)}
        </ButtonItem>
      </PanelSectionRow>)}
    </>}
  </>;
}
