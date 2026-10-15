# Claw EX Control

A Decky Loader plugin for CPU frequency and linked dual-fan control on MSI Claw EX handhelds.

**Hardware tested:** MSI Claw 8 EX, board `MS-1T91`, CachyOS with the enhanced `msi_wmi_platform` fan-curve interface. Other models, kernels and operating systems are not verified. This is a community project, not an official MSI product.

## Features

- CPU default / balanced / power-saving presets; linked or independent P/E frequency caps.
- CPU turbo toggle and restoration of original settings. TDP is managed separately.
- Linked dual-fan control: two visible presets, fixed output or six temperature/output points.
- Fan drafts save without applying; explicit Apply and restoration to firmware automatic mode.
- Optional boot restoration of the last successfully applied fan plan, after entering game mode and receiving a Decky heartbeat. Disabled by default.
- Readback validation, transaction backups, heartbeat monitoring and recovery. No games or stress tests are launched by the plugin.

The interface is currently in Chinese. Output percentages are firmware commands, not percentages of measured RPM. A flat curve implements fixed output; there is no direct target-RPM interface. Fan telemetry failure or missing RPM after a ten-second grace period causes a restoration attempt. Intentional zero-RPM operation is therefore not guaranteed. Low-output and sustained-load cooling suitability must be evaluated for your hardware; a successful sysfs write does not establish thermal performance.

## Installation

Download `decky-claw-ex-control-0.4.1-install.tar.gz` from Releases, extract it, then run:

```sh
sudo python3 scripts/install.py
```

This installs both the Decky frontend and a root systemd controller. The current installer targets user `deck` and `/home/deck/homebrew/plugins`. It validates hashes, backs up a read-only hardware snapshot and starts idle. It refuses an existing installation or recovery state rather than overwriting it. The Decky ZIP alone does **not** install the controller.

Reload Decky after installation. Hardware application requires an independent Steam gaming session; desktop mode supports configuration editing. The internal service/socket remain named `claw-cpu-control` for continuity.

### Existing local versions

Do not install alongside `Claw CPU Control`. Restore CPU and fan settings first and use the old version's uninstall script. Keep `/var/lib/claw-cpu-control` backups; the installer intentionally refuses to overwrite retained state. Migration of existing local installations requires inspecting and archiving that state first. No automatic upgrade or deletion of recovery records is provided in this release.

## Fan boot restoration

Apply a fan plan successfully, then enable **开机恢复风扇方案**. On the next boot, the controller waits for game mode and a live Decky heartbeat before applying that successful plan. Unapplied drafts do not change the boot plan. Each boot allows one automatic attempt; failure or manual restoration does not cause repeated application. Turning the option off cancels future automatic application without changing the current curve.

Closing the panel retains active control. Exiting game mode, loss of backend heartbeat, plugin unload or normal service shutdown attempts restoration. A crash or power loss cannot guarantee immediate restoration. Recovery records are retained on ambiguous state or external changes; the plugin does not continuously fight another controller.

## Restore / uninstall

```sh
python3 /usr/local/lib/claw-cpu-control/control.py status
python3 /usr/local/lib/claw-cpu-control/control.py fan-status
python3 /usr/local/lib/claw-cpu-control/control.py restore
python3 /usr/local/lib/claw-cpu-control/control.py fan-restore
sudo python3 scripts/uninstall.py
```

Uninstall refuses to proceed if recovery is incomplete. Settings and recovery history are retained. Do not delete transaction files to bypass a recovery error.

## Development

```sh
pnpm install --frozen-lockfile --ignore-scripts
pnpm typecheck
pnpm test:ui
python3 -m unittest discover -s tests -v
pnpm build
python3 scripts/package.py
```

Python uses the standard library. Node dependencies are locked. Release archives and checksums are created under `release/`. The backend only exposes fixed operations through a restricted Unix socket, not arbitrary shell commands or filesystem writes. The systemd unit restricts writes to CPU controls and the discovered MSI hwmon subtree.

Validation includes simulated backend recovery and frontend behavior tests. These do not replace Steam controller-navigation checks, physical reboot testing or thermal testing. Boot restoration has simulated coverage but has not yet been verified through an actual device reboot.

## License

MIT; see [LICENSE](LICENSE).
