# Changelog

## 0.6.7

- Choose Automatic, 简体中文 or English under General settings. CPU, Fans and Power share the selection and preserve the existing language preference.
- On return from desktop to game mode, wait for a fresh Decky heartbeat and restore the last applied CPU limits, Turbo setting and dual-fan preset once when their individual restore switches are enabled.

## 0.6.6

### RAPL power backend

- Discover package PL1/PL2 by constraint name, preferring MMIO; read MSR and MSI separately without writing secondary limits or stopping services.
- Remove the Steam custom-profile prerequisite, retain conservative board limits, and expose target/readback, secondary-limit and delayed-verification status in English and Chinese.
- Recheck after approximately 1 and 5 seconds, release on external changes, and wait for stable RAPL readings before opt-in startup/resume application.
- Preserve exact original limits and interface identity in versioned transactions; archive legacy MSI journals without replaying them into RAPL.
- Serialize slider changes and discard obsolete queued automatic requests. Prepare installer permissions for selected RAPL constraint files only; installer was not run.
- Validate 134 backend tests, six frontend suites, TypeScript and Rollup. Hardware validation remains pending.

### Dual-fan design

- Keep both firmware temperature axes unchanged when applying fixed output or six-point curves; show differing fan temperatures and edit only output percentages.
- Accept two editable presets, preserving legacy preset data and mapping an old selected slot to the first visible slot.
- Keep an active curve across panel heartbeat loss while backend telemetry monitoring continues; game-mode exit and telemetry failures still restore automatic control.
- Retain explicit Apply, last-successful-plan startup restore, suspend recovery and transactional rollback. No automatic low-output snapping.
- Validate 122 backend tests, six frontend suites, TypeScript checks and Rollup build. Hardware deployment and Steam layout validation are pending.

## 0.6.4 — 2026-11-05

- Release stale fan ownership when firmware returns to automatic control; archive evidence without rewriting the firmware curve.
- Wait for 10 seconds of stable custom TDP settings before boot power restoration.
- Automatically save/apply valid power slider edits after 400 ms, with serialized requests and restore cancellation.
- Validate 114 backend tests and power frontend behavior.

## 0.6.3 — 2026-10-22

- Provide English UI text, backend errors, status messages and installer diagnostics.
- Keep compact CPU/Fans/Power navigation and two fan presets; use structured columns for fan-point buttons.
- Include previously local PL1/PL2 control, CPU/Turbo/power startup restore, fan resume recovery, EPP coexistence and shared polling improvements.
- Align the frontend title and public install/uninstall paths with Claw EX Control.
- Preserve configuration keys, internal service/socket names and hardware control semantics.
- Verify 110 backend tests, five frontend suites, TypeScript checks and Rollup build.

No system installation or hardware writes were performed for this release. In-Steam English layout/controller navigation and real reboot behavior still require device validation.
