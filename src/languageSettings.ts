import {useEffect, useState} from 'react';
import {callable} from '@decky/api';

export type LanguageMode = 'auto' | 'zh' | 'en';
const storageKey = 'claw-ex-language';
let mode: LanguageMode = 'auto';
let detected: 'zh' | 'en' = 'en';
try {
  const saved = localStorage.getItem(storageKey);
  if (saved === 'zh' || saved === 'en') mode = saved;
} catch { /* Use the session preference when storage is unavailable. */ }

const listeners = new Set<() => void>();
const readLanguage = callable<[], {language: string}>('get_ui_language');
let timer: ReturnType<typeof setInterval> | undefined;
const notify = () => listeners.forEach(listener => listener());

export function currentLanguage() {
  return mode === 'auto' ? detected : mode;
}

export function setLanguageMode(next: LanguageMode) {
  if (next === mode) return;
  mode = next;
  try { localStorage.setItem(storageKey, next); } catch { /* Keep the session preference. */ }
  notify();
}

async function refresh() {
  try {
    const {language} = await readLanguage();
    if ((language === 'zh' || language === 'en') && detected !== language) {
      detected = language;
      if (mode === 'auto') notify();
    }
  } catch { /* Retain the last detected language until the next poll. */ }
}

export function useLanguage() {
  const [, setRevision] = useState(0);
  useEffect(() => {
    const update = () => setRevision(value => value + 1);
    listeners.add(update);
    if (timer === undefined) {
      void refresh();
      timer = setInterval(refresh, 10000);
    }
    return () => {
      listeners.delete(update);
      if (!listeners.size) {
        clearInterval(timer);
        timer = undefined;
      }
    };
  }, []);
  return mode;
}
