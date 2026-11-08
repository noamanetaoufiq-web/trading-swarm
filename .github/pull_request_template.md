## What changed

## Safety checklist (see CLAUDE.md)
- [ ] Inspector hard rules not weakened (R:R >= 2, news blackout, daily/weekly/drawdown limits, sizing incl. costs)
- [ ] No look-ahead added to backtests / validator; final-test slice never used for selection
- [ ] Paper stays the default; live path still needs `--allow-live` + `LIVE_TRADING=YES` + "send to broker"
- [ ] No credential written to disk, logs or API responses
- [ ] Tests added / updated, `make test` passes
