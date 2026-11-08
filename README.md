# Trading Swarm - 12 AI agents, one pipeline + command-center dashboard

Kol task fiha agent dyalo (prompt + schema + fallback). Architecture 3la hsab l'diagram dyalek:
`Orchestrator -> (Data | News | Technical | Macro | Scanner | Social/On-chain) -> Inspector -> Backtest/Monte-Carlo gate (agent 11) -> Trade Validator (agent 12) -> Execution -> Critic`.

> **Paper trading by default.** Analysis tool, not financial advice. Live order sending is triple-gated and UNTESTED - use a demo account first.

## Quick start (Darija)

```bash
cd trading-swarm
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt                        # MT5 on Windows: requirements-mt5.txt
cp .env.example .env                                   # 7t f ANTHROPIC_API_KEY

# 1) test offline (data synthetic + agents rule-based, bla API key)
python main.py analyze XAUUSD BTCUSD NASDAQ --demo

# 2) l'vrai: data live + Claude agents
python main.py analyze XAUUSD BTCUSD NASDAQ

# 3) data live bla LLM (bla tkalfa API)
python main.py analyze XAUUSD BTCUSD NAS100 --no-llm

# 4) mn ba3d ma trades ytsalaw: Critic kay-t3alam
python main.py review
python main.py journal

# 5) Backtest / Monte Carlo / optimisation (agent 11) 3la symbols
python main.py backtest XAUUSD BTCUSD NAS100        # --demo bach t-testi offline

# 6) DASHBOARD (l'awal screen howa setup dyal l'account: Demo wla Real + broker)
python main.py serve                                 # -> http://127.0.0.1:8000/?token=...  (l'URL kay-tban f terminal)
python main.py serve --allow-live                    # ONLY if you really want the dashboard able to send real orders
```
Report kay-t-sauvegarda f `reports/report_<date>.md` (add `--json` l JSON kamel).
Symbols aliases: `NASDAQ/NDX/US100 -> NAS100`, `GOLD -> XAUUSD`, `BTC -> BTCUSD`.

## Repository layout (what goes on GitHub)
```
trading-swarm/
|- .github/
|   |- workflows/ci.yml            tests on Python 3.10 + 3.12, secret scan, headless dashboard smoke (incl. stubbed MT5 linking)
|   |- workflows/claude.yml        the repo bot: write @claude in an issue / PR comment
|   |- workflows/claude-review.yml automatic PR review against CLAUDE.md
|   |- ISSUE_TEMPLATE/  pull_request_template.md  dependabot.yml
|- CLAUDE.md  AGENTS.md            rules + commands for bots and contributors (safety invariants)
|- README.md  docs/ARCHITECTURE.md
|- config.yaml  .env.example       all thresholds / instruments; example env (the real .env is git-ignored)
|- main.py  server.py              CLI and dashboard server
|- swarm/  (pipeline, agents/, tools/)   dashboard/   tests/   scripts/check_secrets.py
|- requirements.txt  requirements-dev.txt  requirements-mt5.txt (Windows)  pytest.ini  Makefile  .gitignore
```
**Never commit**: `.env`, `data/` (journals, equity history = your balance, saved account), `reports/`. They are git-ignored and `scripts/check_secrets.py` fails CI if a key or password shows up.

### Put it on GitHub
```bash
cd trading-swarm
git init -b main
git add .
git status                      # check: no .env, no data/, no reports/
git commit -m "Trading Swarm v3"
git remote add origin https://github.com/<you>/trading-swarm.git      # create the repo as PRIVATE
git push -u origin main
```
### Turn on the bot
1. Install the Claude GitHub App on the repo (or run `/install-github-app` inside Claude Code), 2. add the repository secret `ANTHROPIC_API_KEY` (Settings > Secrets and variables > Actions), 3. in an issue or PR write `@claude <task>`. Every PR is also reviewed automatically (`claude-review.yml`); delete that file if you do not want it. The bot runs on GitHub's runners with no broker and no MT5: it can edit code and run the offline tests, it cannot trade. **Do not run the trading swarm itself on GitHub Actions with real credentials.**

## The 12 agents

| # | Agent (key) | File | Deterministic part (code) | LLM part |
|---|---|---|---|---|
| 1 | Orchestrator - CIO (`orchestrator`) | `agents/orchestrator.py` | routing plan, guardrails (never overrides news freeze) | resolves conflicts -> `TradeProposal`, executive memo |
| 2 | Data Fetcher (`data_fetcher`) | `agents/data_fetcher.py` | OHLCV 1m/15m/1h/4h/1d (ccxt/yfinance), cleaning (NaN, dupes, glitch spikes), ATR, spread | audits the quality report |
| 3 | News & Sentiment (`news_sentiment`) | `agents/news_sentiment.py` | RSS headlines, economic calendar, blackout window | score -10..+10, freeze flag (+ web search) |
| 4 | Technical / SMC (`technical`) | `agents/technical.py` + `tools/smc.py` | swings, BOS/CHoCH, FVG, Order Blocks, EQH/EQL, sweeps, premium/discount, RSI div, EMA | builds entry/SL/TP plan |
| 5 | Macro (`macro`) | `agents/macro.py` | DXY, US10Y, VIX, SPX/NDX, gold, oil | regime Risk-On/Off + asset bias (+ web search) |
| 6 | Opportunity Scanner (`scanner`) | `agents/scanner.py` | vol compression, volume spikes, breakouts, sweeps | ranks candidates |
| 7 | Inspector / Risk (`inspector`) | `agents/inspector.py` + `tools/risk.py` | **HARD rules**: R:R>=1:2, news window, ATR stop sanity, spread, sizing, exposure & daily-loss caps, data health | false-signal audit (**can only veto**) |
| 8 | Social & On-chain (`social_onchain`) | `agents/social_onchain.py` | Fear&Greed, Binance funding/OI/long-short, optional Whale Alert | contrarian read |
| 9 | Execution (`execution`) | `agents/execution.py` + `tools/brokers.py` | MT5 / Binance USD-M / IBKR payloads, validation, gated submit | chooses LIMIT/STOP/MARKET |
| 11 | **Backtest / Monte Carlo / Optimisation gate** (`backtester`) | `agents/backtester.py` + `tools/backtest.py` + `strategy.py` | no-look-ahead replay on ~4000 x 1h bars, win rate / realised R:R / expectancy / PF / max DD (after spread), Monte Carlo (1000 paths x 100 trades: P(loss), DD distribution, risk of ruin), walk-forward optimisation (in-sample vs out-of-sample), threshold gate | sceptical review (**veto only**) |
| 12 | **Trade Validator - per-trade Monte Carlo** (`trade_validator`) | `agents/trade_validator.py` + `tools/trade_sim.py` | re-simulates the exact confirmed entry/SL/TP on thousands of synthetic 1h paths at **10 / 100 / 1000 / 5000 runs**, compares the strategy's historical win rate with the luck baseline and the break-even win rate, cost-stress, verdict GOOD / MARGINAL / BAD | sceptical review (**veto only**) |
| 10 | Critic / Learning (`critic`) | `agents/critic.py` + `review.py` | paper-trade simulation (fills, SL/TP, MFE/MAE) | root cause + lessons fed back into prompts |

Pipeline order and parallelism: see `docs/ARCHITECTURE.md`.

## Agent 11 - the probability gate (before Execution)
Flow: Inspector approves -> **Backtester** validates statistically -> only `PASS` / `PASS_REOPTIMIZED` reaches Execution.
- **Historical simulation**: the rule-based SMC strategy (`swarm/strategy.py`, the same code the rule-based Technical planner uses) is replayed bar-by-bar on `history_bars` of 1h data. Each evaluation only sees data up to that bar (unit-tested for look-ahead). Limit/stop fills on touch, spread charged in R, same-candle SL+TP counts as SL.
- **Probability & win-rate check**: win rate (+95% CI), realised R:R, expectancy, profit factor, max drawdown. The sample used is the *similar setups* (same side, R:R +/- 1) when enough exist, otherwise same-direction trades.
- **Monte Carlo**: bootstrap of the trade R-multiples, `mc_runs` paths (10 / 100 / 1000 / 5000 - config) x `mc_horizon_trades` trades -> probability of loss, drawdown distribution (p50/p95), risk of ruin, equity fan.
- **Conditional gate** (all thresholds in `config.yaml -> backtest`): win-rate floor, win rate must beat the break-even win rate of the setup's R:R, expectancy, profit factor, P(loss), DD p95, P(ruin), minimum sample.
  - all pass -> `PASS`
  - fail -> a **walk-forward optimised** parameter set is tried (24 combos, chosen in-sample, must also earn out-of-sample). If it passes the gate the plan is re-derived with those parameters and re-checked by the Inspector -> `PASS_REOPTIMIZED`
  - otherwise -> `REJECT` (or `INSUFFICIENT_DATA`): the trade is refused and the reasons are shown.
- The LLM part can only veto (overfitting / regime / sample-quality concerns).

### What the backtest does and does not prove
It validates the **strategy family** (rule-based SMC: 4h bias + 1h zones) and the **setup geometry** on this instrument. The discretionary LLM plan cannot be replayed bar-by-bar, so a statistically sound strategy does not prove that one specific LLM idea is good - that is why the Inspector + Critic loop still exist. Backtests are in-sample estimates of the past; the optimiser is protected by an out-of-sample split but overfitting is never impossible. Free data (Yahoo/Binance) limits history to ~2 years of 1h bars.

## Agent 12 - the per-trade validator (after the gate, before Execution)
Flow: Inspector approves -> Backtest gate passes -> **Trade Validator** simulates *this* trade -> only an approved verdict reaches Execution.
- **Simulation**: the confirmed `side / entry / SL / TP` is played on N synthetic 1h price paths = a **block bootstrap of the instrument's real 1h bars** (24-bar blocks keep volatility clustering and fat tails; volatility rescaled to today's ATR). Fills on touch (limit or stop), same-candle SL+TP = SL, spread + slippage + commission + swap charged in R, time-out at `max_hold_bars`.
- **Ladder**: the same trade at **10, 100, 1000, 5000 runs** (`validator.runs_ladder`), evaluated on nested prefixes of the same paths, so you see the estimate converge. Per step: fill %, TP / SL / time-out / unfilled counts, win rate with 95% CI, expectancy, R percentiles. Everything is stored in the journal for **every trade** and shown in the dashboard (Symbols card 12, Journal drawer, Pipeline node, Lab simulator).
- **What the win rate means**: the paths have no directional edge (drift removed), so the simulated win rate is what this entry/SL/TP wins by **luck** (about 1 / (1 + R:R) minus costs). A second lens that keeps the historical drift is shown for information only.
- **Verdict (code)**: `GOOD` = the strategy's historical win rate (agent 11, similar setups) beats the luck baseline **and** the cost-adjusted break-even win rate with 95% confidence, the edge survives costs x2, the entry fills often enough and the ladder is stable -> full risk. `MARGINAL` = point estimates pass but the confidence interval (or the evidence) does not -> trade only at `marginal_risk_mult` x risk, refused if the minimum lot makes that impossible (or if `min_verdict: GOOD`). `BAD` -> refused. The LLM can only veto.
- **Trade simulator** (Backtest lab): type any entry/SL/TP and run the same ladder (`POST /api/validate`).
It cannot predict the future: it shows whether the geometry + the historical evidence is better than chance after costs.

## Link ANY MetaTrader 5 account (demo, real, funded / prop-firm)
In the wizard choose **MetaTrader 5** and enter what your broker gave you: **account number (login) + password + server**, exactly as in your terminal (Exness example: `Exness-MT5Trial15` for a demo, `Exness-MT5Real…` for real). Optional: the full path to `terminal64.exe`.
- **Requirements**: Windows, the MT5 terminal installed, `pip install MetaTrader5` (the package only exists for Windows), and this server running on that same PC. 64-bit python + 64-bit terminal.
- **Everything comes from the account**: balance / equity / free margin / floating P&L / leverage / currency / demo-or-real, **and** per instrument the real symbol name, lot min / step / max, tick value, and live spread. Nothing about contract sizes is typed in: `usd_per_point_per_lot`, `min_lot`, `lot_step`, `max_lot`, `typical_spread` and `mt5_symbol` are overwritten at the start of every run from the broker.
- **Symbol names differ per broker / account type** (Exness Standard `XAUUSDm`, `USTECm`; Pro `XAUUSD`, `USTEC`; others `GOLD`, `NAS100.cash`...). The swarm auto-detects from a list of aliases + the common suffixes (`m z c r .m .pro .raw ...`), never guesses the amplified `_x100` contracts, reports ambiguity, and lets you **override** a symbol on the Account page.
- **Cent / non-USD accounts**: lot values are read in the account currency from the broker's own tick value, so equity and risk are always in the same unit (USC is shown divided by 100); no FX guessing.
- **Prices from the broker**: with an MT5 account the live candles, the backtest history, the validator paths and the spreads come from the terminal (`feed: mt5`), so the `GC=F` proxy disappears. If the terminal cannot be read the run falls back to the public feed and the Inspector flags it as a proxy.
- **One login per run** (the terminal session is kept open for the whole run, then closed). The password lives in the server's memory only; the MT5 terminal itself may remember the login on its side.
- **Funded / prop-firm accounts**: they are just MT5 logins. The firm's own daily / total-loss rules are *not* known to the swarm: set the limits in the wizard tighter than theirs. An investor (read-only) password can analyse but not trade (the wizard warns).
- **Errors are explained**: `-6` authorization failed (wrong login/password or server name not identical to the terminal's), `-10005` IPC timeout (give `terminal64.exe` path, close other MT5 windows, log in manually once), `-10003` terminal could not start.
- Status: all of this is unit-tested against a **stub** of the MetaTrader5 module that behaves like an Exness server; it has **not** been run against a real terminal or broker.

## Account size is read, never hard-coded
- A linked broker account (MT5 / Binance / IBKR, demo **or** real) is **read at the start of every run**: equity, balance, free margin, floating P&L, currency, kind. Every agent sizes on that number (`ctx.equity`, `ctx.account`). If the broker cannot be read the run **stops** - there is no silent fallback to a typed amount.
- Only the built-in **Paper simulator** has a typed starting capital (default **$100**); its equity = start + realised paper P&L.
- **Equity-based protection**: daily loss, weekly loss (since Monday UTC) and drawdown-from-peak are computed from equity snapshots (`data/equity/*.jsonl`) so they include **floating** losses. Snapshots start the first time the swarm sees the account (a loss made before that cannot be known).
- Sizing assumes a USD account; a non-USD account is refused unless `account.allow_non_usd` + `fx_to_usd` are set.
- **$100 reality check** (Account page shows it per market): on XAUUSD the smallest lot (0.01) moves $1 per $1, so a normal ~$4-10 stop already risks $4-10 = 4-10% of $100. With 0.5% risk ($0.50) the Inspector will refuse every gold trade - correctly. Options: a cent/micro account, a larger balance, or consciously higher risk %. The code never lowers the protection to find trades.

## Backtest hardening (v2)
- **No data leakage**: walk-forward uses three slices - parameters are chosen on TRAIN (50%), must also earn money on VALIDATION (20%), and the optimised set is judged **only on the untouched FINAL TEST (30%)**. A bar embargo (entry expiry + max hold) at every boundary stops a trade from resolving on the next slice's prices. Unit tests: the selected parameters do not move when the final slice is altered, and slices are ordered and embargoed.
- **Costs**: spread + slippage (stop entries and every stop-loss exit) + commission + swap, all in R (`config.yaml -> costs`, per-instrument `typical_slippage`, `swap_per_lot_per_day_usd`). Gaps through a stop are **not** modelled.
- **Data feed**: with a linked MT5 account the broker's own history is used automatically. Without one, `GC=F` is gold *futures*, not your broker's XAUUSD. Per instrument `feed: yahoo | csv | mt5` (history for backtest + validator): `csv` loads a broker export (MT5 export or `time,open,high,low,close,volume`, M1..H1, resampled to 1h); `mt5` reads the logged-in terminal (**untested**). While `feed_is_proxy: true` the Inspector shows a warning (`risk.require_broker_feed: true` makes it a refusal). Live analysis candles still come from Yahoo/ccxt.

## v3 review fixes
- **One definition of a win** in the backtester and the validator: a trade is a win when **R > 0 after costs** (a profitable time-out counts). The validator compares like with like (luck baseline vs strategy), uses the strategy's realised avg win/loss for the break-even, and adds a confidence check that the strategy's expectancy (95% lower bound) is positive.
- **Optimizer** no longer stops at the first parameter set: it walks down the train ranking until one **also passes validation**; if none does, nothing is selected (the best candidate is only displayed).
- **Position sizing includes spread, stop-exit slippage and commission**: lots are chosen so the loss at the stop *including those costs* stays inside the risk budget (`risk_usd` is all-in, `cost_buffer_usd` is shown).

## Dashboard (`python main.py serve`)
Local FastAPI server + dependency-free dashboard (`dashboard/`). **First screen = account setup**: choose **Demo** or **Real**, pick a broker (Paper simulator / Binance USD-M (testnet toggle) / MetaTrader 5 / Interactive Brokers), enter credentials, set risk limits (account size is read from the broker; only the paper simulator takes a starting capital), test the connection (the broker's own account type is checked against what you selected), save.
Then: **Overview** (KPIs, run controls, executive summary, decision funnel per symbol), **Agent pipeline** (the 12-agent flow with status/timing, click a node to inspect its raw output), **Symbols** (candlestick chart with FVG / order blocks / liquidity / entry-SL-TP shading + a card for every agent incl. the gate, the per-trade validator ladder and broker payloads), **Backtest lab** (equity curve, Monte Carlo fan, drawdown histogram, train/validation/final-test optimisation table, thresholds, **trade simulator**), **Journal & Critic** (click a trade for its full validation).
Security model:
- binds to `127.0.0.1`; every `/api` call needs the random start-up token printed in the terminal;
- broker credentials live **in server memory only** (never written to disk, never returned by the API); restart = re-enter, or use `.env`;
- Demo account => orders are simulated in the paper journal, nothing goes to a broker;
- Linked broker account (demo or real) => equity is read from the broker at every run; orders are sent **only if** the server runs with `--allow-live` **and** you tick "send to broker" on that run **and** the run uses live data + Claude agents; everything still has to pass the Inspector and the Backtest gate;
- the live order code (MT5 / Binance) is **untested** - rehearse on a demo/testnet account first.
The dashboard logic is covered by an automated headless-DOM smoke test (all pages render, wizard flow works, no JS errors), but it has not been visually reviewed in a real browser - tell me what to polish.

## Safety design (important)
- LLMs **never** do the arithmetic that matters: R:R, sizing, exposure, blackout windows are code.
- The Inspector LLM can only **veto**. A failed hard rule can't be overridden by any agent.
- If any agent's LLM call fails, it degrades to its rule-based fallback and the Inspector **rejects** the trade (`agents_healthy`).
- Execution is paper unless: `execution.mode: live` in `config.yaml` **and** `--live` **and** `LIVE_TRADING=YES`.
- The Critic's `parameter_feedback` is advisory only - it never edits risk limits.

## Configuration (`config.yaml`)
Account/risk limits, per-agent models (cheap Haiku for simple agents, Sonnet/Opus for judgment), timeframes, instruments (add EURUSD etc. by copying a block), calendar + manual events (FOMC example included), RSS feeds.
Check `usd_per_point_per_lot`, `lot_step`, `mt5_symbol` against **your broker's** contract specs before trusting lot sizes.

## Add a new agent
1. Create `swarm/agents/my_agent.py` extending `BaseAgent` (`key`, `system_prompt`, `schema`, `user_prompt`, `heuristic`).
2. Add its schema in `schemas.py`, register it in `agents/__init__.py`.
3. Call it in `pipeline.py` at the right stage.

## Tests
`python -m pytest -q tests` (offline, ~60s). Dashboard smoke (headless DOM): `npm i jsdom`, start `python tests/serve_smoke.py`, then `PHASE=1 node tests/dashboard_smoke.js`, `PHASE=2 ...`, `PHASE=3 ...`. Covers JSON extraction, risk maths, data cleaning, SMC output, full demo pipeline, hard rules, news blackout, **no look-ahead in the backtester, leak-free train/validation/final-test selection, costs in R, Monte Carlo behaviour, gate refusal / insufficient-sample / re-optimisation paths, the per-trade validator (ladder convergence to break-even, verdicts, cost stress, MARGINAL sizing, blocking Execution), account size read from the broker (no fallback), equity-based daily/weekly/drawdown limits, csv feed**, and the server API (auth, account validation, secrets never returned, live sending refused without the server flag, end-to-end run).

## Known limitations
- SMC detection is rule-based approximation (see `tools/smc.py`); tune to your own definitions.
- Yahoo/RSS/ForexFactory are free and can be delayed, rate-limited or change format. For production use a proper data vendor and verify prices against your broker.
- yfinance 1m data is limited to ~5 days; `^TNX` units should be verified.
- Whale flows need a Whale Alert key; on-chain/funding applies to crypto only.
- Paper review assumes fills at the planned entry on touch (no slippage) and SL-first when SL and TP share a candle. The backtester/validator charge slippage but do not model gaps through stops.
- The validator's simulated win rate is a luck baseline (random-walk paths), not a forecast; the evidence for an edge is the historical strategy sample (small samples => wide confidence intervals => MARGINAL).
- Multi-strategy selection (trend / breakout / mean-reversion with independent backtests) is **not built yet**: the replayed strategy is still the SMC family.
- Broker reading code (MT5 / Binance / IBKR account info, MT5 candles and history) is untested against real servers; MT5 is covered by stub tests only.
- Live execution code is untested.
- First backtest per symbol takes ~10-30 s (cached for `cache_hours`); `python main.py backtest ...` pre-computes it.
