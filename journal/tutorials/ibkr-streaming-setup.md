---
tags: [tutorial, ibkr, ib-gateway, setup, streaming]
---
# IBKR Streaming Setup: From Paper Account to Live Data

This guide walks through everything you need to get IB Gateway running locally so that `run_live_ibkr.py` can stream real-time SYMBOL bars from Interactive Brokers and route paper trades back to your paper account. By the end, the IBKR runner will be your sole data + execution layer — no Databento or Alpaca needed.

---

## Prerequisites

- An IBKR Paper Account (you already have this)
- IB Gateway installed on your machine (see Step 2 below — prefer this over TWS for automation)
- The Python venv already has `ib_insync` — it's in `requirements.txt` and was added in journal 021

---

## A Note on API Keys

You mentioned retrieving API keys from IBKR's portal. This is worth clarifying upfront, because IBKR has **two separate APIs** and they work differently:

**IBKR Web API (REST)** — This is what the "API Keys" section in the Client Portal refers to. It's a modern REST API accessed with bearer tokens/keys. We do **not** use this.

**TWS API (Socket API)** — This is what `ib_insync` uses. It connects via a local TCP socket to IB Gateway or TWS running on your machine. There are no API keys involved — authentication happens when you log in to IB Gateway with your account credentials. Our code connects to `127.0.0.1:4002` and IB Gateway handles the rest.

So you don't need to generate or manage any API keys for our setup. If you see them in the portal, that's for a different use case.

---

## Step 1 — Confirm Your Paper Account Is Active

Log in to the IBKR Client Portal at `clientportal.ibkr.com`. Under **Account → Account Type**, you should see a paper trading account listed. Paper accounts are separate from live accounts — make sure you're logging in with the paper account credentials (IBKR typically issues a separate username like `DU1234567` for paper accounts).

If your paper account has trading permissions for US equities and options, you're set. No special setup is needed in the portal for the socket API.

---

## Step 2 — Download and Install IB Gateway

Go to `interactivebrokers.com`, navigate to **Trading → Trading Platforms**, and download **IB Gateway** (not Trader Workstation / TWS). IB Gateway is a lighter headless version — no charts, no order entry UI — that's better suited for automated trading. TWS works too, but it's heavier and needs more manual attention.

Install it like any other app. When you launch it:

1. At the login screen, select **Paper Trading** from the mode dropdown
2. Log in with your paper account credentials
3. Leave it running — it needs to stay open while our script is running

IB Gateway auto-disconnects after approximately 24 hours. You'll need to manually restart and re-login each trading day, or set up a scheduled task to do it.

---

## Step 3 — Configure IB Gateway for API Access

Once logged in, open the settings:

**Configure → Settings → API → Settings**

Make these changes:

| Setting | Value |
|---|---|
| Enable ActiveX and Socket Clients | Checked ✓ |
| Socket port | `4002` |
| Read-Only API | Unchecked (critical — if checked, orders are silently dropped) |
| Allow connections from localhost only | Checked ✓ |
| Trusted IPs | Add `127.0.0.1` |
| Master Client ID | Leave blank or set to `0` |

The port `4002` is the paper trading port for IB Gateway and matches what's in `config/strategy_params.yaml`. The live trading port is `4001` — do not mix these up.

Click **OK** and restart IB Gateway for the settings to take effect.

---

## Step 4 — Data Subscriptions and Cost

This is the part that trips people up. IBKR's socket API gives you access to market data, but **what data you receive depends on your subscriptions**.

**Without a subscription:** You get 15-minute delayed data for free on most US symbols. The streaming code (`keepUpToDate=True` in `ibkr_streamer.py`) will still work — bars will arrive, but they'll be 15 minutes behind real time. This is fine for testing the plumbing but defeats the purpose of a live strategy.

**With a subscription:** Real-time L1 data (bid/ask/last) for US stocks and ETFs costs approximately **$4.50/month** through IBKR. The specific subscription is called something like **"US Securities Snapshot and Futures Value Bundle"** or similar — the exact name varies, but it covers equities/ETFs on US exchanges, which includes SYMBOL.

To subscribe:
1. Log in to the Client Portal
2. Go to **Account Management → Market Data Subscriptions**
3. Search for US equity data and add the relevant bundle

IBKR's data fees are on the low end compared to alternatives (Databento runs around $40–80/month for the same coverage). See `decisions/006-data-provider-comparison.md` for the full analysis.

**Note:** Even with delayed data, you can validate the whole pipeline end-to-end. Subscribe when you're ready to trade seriously with real-time prices.

---

## Step 5 — Verify the Config

Open `config/strategy_params.yaml` and confirm the IBKR section looks like this:

```yaml
live:
  warmup_bars: 200
  ibkr_host: "127.0.0.1"
  ibkr_port: 4002              # IB Gateway paper
  ibkr_streamer_client_id: 1   # data connection
  ibkr_trader_client_id: 2     # order connection
```

The two client IDs **must be different**. IB Gateway rejects connections that reuse an already-connected client ID. Our streamer uses `1` and our trader uses `2` — they connect to the same IB Gateway instance simultaneously as separate clients.

Port reference:
- `4002` — IB Gateway paper trading (use this)
- `4001` — IB Gateway live trading
- `7497` — TWS paper trading (if you switch to TWS instead of IB Gateway)
- `7496` — TWS live trading

---

## Step 6 — Run the IBKR Live Runner

With IB Gateway running and logged in, start the runner:

```bash
bash scripts_bash/run_live_ibkr.sh
```

Or directly:

```bash
/path/to/venv/python live_runner/run_live_ibkr.py
```

What you'll see in the logs on a healthy startup:

1. `IBKRTrader connected (client_id=2)` — order client connected
2. `Loaded 200 warmup bars from Databento cache` — historical data loaded for indicator priming
3. `IBKRStreamer connected (client_id=1)` — data client connected
4. `Subscribed to SYMBOL 1-min bars` — streaming started
5. First bar arrives → `on_bar called` → engine processes it

The runner waits for bars during market hours and shuts down cleanly at 15:55 EST with an EOD position close. Stop it any time with Ctrl+C — it logs a trade summary on exit.

---

## Common Gotchas

**Read-Only API not unchecked** — Orders appear to submit in the logs but are silently rejected by IB Gateway. If you're placing paper trades and see no fills, check this setting first.

**Duplicate client ID** — If another process is connected to IB Gateway with the same client ID, the new connection is rejected. Restart IB Gateway or kill the other process.

**IB Gateway session expiry** — After ~24 hours, IB Gateway automatically logs out. The streamer will hit its reconnection limit and exit. You need to log back in to IB Gateway before restarting the runner.

**Delayed data vs real-time** — If bars are arriving but your paper trades look off-price, check whether you have a real-time data subscription. Log messages show the bar timestamps — if they're 15 minutes behind wall clock, you're on delayed data.

**Port mismatch** — The most common config error. Paper IB Gateway is `4002`, live is `4001`. If the connection is refused, double-check the port in both IB Gateway settings and `strategy_params.yaml`.

**Warmup from Databento cache** — The runner loads 200 warmup bars from the local Databento cache (not from IBKR) to prime the indicators. If the cache is empty or stale, warmup fails loudly at startup. Run `scripts_py/download_and_aggregate_databento.py` to refresh it if needed.

---

## Next Steps

Once the runner is streaming and placing paper trades, refer to:

- `runbooks/run-live-trading.md` — day-to-day operating procedures (when to start, what to watch for, stopping cleanly)
- `docs/_state.md` — current config values, armed mode status, active TODOs
- `decisions/005-live-runner-architecture.md` — why the architecture is designed the way it is (warmup from cache, two-client IB Gateway connection, etc.)
