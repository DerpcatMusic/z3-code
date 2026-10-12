# Z3-code

Zeron’s native interface with T3 Code’s orchestration v2 backend. Linux x64.

Run `z3-code`. It reuses your local T3 backend or starts `~/.t3/bin/t3` headlessly with your shared T3 conversations. Install T3 Code first; the backend stays running when Z3 closes and can update independently.
For another environment, set `ZERON_T3_CONNECTION` to a private connection JSON (origin, environmentId, accessTokenFile).

Source: `native/zeron`. T3 and Zeron licenses are preserved. [Features and remaining work](FEATURES.md).

Daily GitHub checks track T3 nightly and Zeron releases, check the native adapter against the exact nightly API, and publish a `z3-upstream-report` artifact with changes and candidate feature gaps. The tracked import commits are in `upstreams.json`; advance them after verifying an integration. These checks do not replace your installed app.

For local feature maintenance, `prepare_maintenance.py` creates independent native/backend checkouts from your tested source, including pending source edits. Re-running it preserves existing maintenance work. A scheduled local agent can port features, run focused tests and benchmarks, and prepare matching app/backend builds while your installed version stays available.
