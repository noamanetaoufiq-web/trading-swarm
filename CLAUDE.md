# CLAUDE.md - rules for any AI bot / contributor working on this repo

Trading Swarm = a **12-agent trading RESEARCH system** (paper by default, human supervised) + a local web command center.
It is not financial advice and no strategy here is proven profitable. Read `README.md` (usage) and `docs/ARCHITECTURE.md` (flow) first.

## Commands
- `make install` - dependencies (`requirements-dev.txt`; MT5 users also `requirements-mt5.txt`, Windows only)
- `make test` - secret scan + the offline suite (~1 min). **Must pass before any commit.** No network, broker or API key needed.
- `make demo` - end-to-end run on synthetic data with rule-based agents
- `make serve` - dashboard on http://127.0.0.1:8000 (prints the URL with the start-up token)
- `make smoke` - headless dashboard check (`npm i --no-save jsdom`; CI runs it too)

## Layout
- `main.py` CLI · `server.py` FastAPI + token auth · `config.yaml` every threshold, instrument, cost and gate
- `swarm/pipeline.py` orchestration order: account -> plan -> data -> scanner -> news/technical/macro/social -> orchestrator -> Inspector -> Backtest gate (11) -> Trade Validator (12) -> Execution -> journal
- `swarm/agents/<agent>.py` one class per agent: role prompt, pydantic schema, `heuristic()` fallback. `swarm/agents/__init__.py` registers them
- `swarm/tools/` pure logic: `risk.py` (sizing, R:R), `smc.py`, `backtest.py` (replay, costs, leak-free walk-forward), `trade_sim.py` (per-trade Monte Carlo), `accounts.py` (broker / MT5 plumbing, equity log), `brokers.py` (order payloads), `market_data.py`
- `dashboard/` dependency-free HTML/CSS/JS · `tests/` pytest + jsdom smoke · `scripts/` repo tooling

## Invariants - never break these (a PR that does must be rejected)
1. **Code decides money; LLMs only veto.** R:R, sizing, exposure, blackout, loss limits and every gate are deterministic code. An LLM can never approve what code refused.
2. **Inspector hard rules are not weakened** to "find trades": R:R >= 2, confidence floor, news blackout, SL 0.5-4 ATR, spread/stop ratio, macro alignment, daily / weekly / drawdown limits (equity based, incl. floating), account currency, sizing **including spread + slippage + commission**. If the minimum lot exceeds the risk budget the answer is NO TRADE.
3. **No look-ahead / no leakage.** Walk-forward = TRAIN picks, VALIDATION must also earn, FINAL TEST is never used to choose anything (embargo at boundaries). Same definition of a win everywhere: **R > 0 after costs**.
4. **Paper by default.** A live order needs `--allow-live` on the server AND `LIVE_TRADING=YES` AND the per-run "send to broker" tick AND live data + LLM agents. Live code paths are UNTESTED against real brokers: say so, never claim otherwise.
5. **Account size is read from the linked broker** (MT5 login + password + server, demo or real) at the start of every run; symbol names, lot limits, tick values and spreads too. No silent fallback to a typed number. Only the paper simulator has a typed capital.
6. **Credentials live in server memory only**: never written to disk, logs, journal, reports or API responses; `scripts/check_secrets.py` guards the tree. `data/` and `.env` are git-ignored - keep them so.
7. Every behaviour change ships with a test. Be honest in docs about what is untested (real terminal / broker, browser look, free-data quality).

## Conventions
- Python 3.10+ (CI runs 3.10 and 3.12): no 3.12-only syntax (e.g. reusing the same quote inside an f-string).
- Add a threshold -> `config.yaml` (with a comment), not a magic number. Add an agent -> new file in `swarm/agents/`, register it, add to `pipeline.py`, `dashboard/app.js` (node + page card), README table, tests.
- MT5 is Windows-only: anything touching `MetaTrader5` must import lazily and be testable with the stub in `tests/test_mt5.py`.
- Keep the dashboard dependency-free (no build step, no CDN).

## Typical tasks for the bot
- "Add test for X" / "fix failing test" - run `make test`, keep fixes minimal.
- "Review this PR" - check the invariants above first, then maths, then style.
- "Add an instrument" - `config.yaml` (aliases for MT5 symbol detection, typical ATR / slippage / swap), then run `make demo`.
