# English release validation (0.6.3)

2026-10-22. Tests use simulated hardware; no sysfs writes, service changes, games or stress tests are performed.

- 110 Python tests cover CPU frequency and Turbo, EPP coexistence, fan transactions and suspend/resume recovery, power ownership, startup restore and session caching.
- Five frontend suites cover CPU, fan, power, startup toggles and shared startup polling.
- TypeScript checks, Rollup build and Python syntax validation are required for packaging.
- Release ZIP/tar entries and manifest hashes are checked; source and archives exclude local snapshots, backups, installed dependencies and credentials.
- English text and layout changes still require visual/controller validation inside Steam. Reboot and long-duration thermal behavior are not established by these tests.

Historical baseline: version 0.1.0 passed 21 simulated tests and read-only checks on MS-1T91 on 2026-10-14. That result is not evidence of later-version hardware validation.
