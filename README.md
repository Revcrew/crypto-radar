# Crypto Crime Radar

By **Alexander Khlystun** — a QA engineer with an AI focus from Lviv, Ukraine.
Seven years in QA taught me one habit: I don't trust a demo, I check it with
data. This project is that habit applied to crypto: detect sharp price moves,
write everything down, and only then ask whether reacting to them works.

## What it does

A single lightweight Python process (stdlib + `requests`) that:

1. Polls perpetual tickers on 7 exchanges — MEXC, BingX, OKX, Hyperliquid,
   Binance, Bybit, KuCoin — about every 5 seconds.
2. Detects sharp moves per base asset: **|change| ≥ 5% in 1 min, ≥ 8% in 5 min,
   or ≥ 12% in 15 min**, with a 24h-volume noise filter ($200k for 5/15-min
   windows, $500k for 1-min; BingX uses native units).
3. Sends **one** Telegram alert per asset (even if several exchanges fire),
   sourced from the exchange with the largest move, with a 30-minute cooldown.
4. Watches DEX tokens (DexScreener, solana/bsc/base/ethereum): ≥ 40% in 5 min
   or ≥ 80% in 1 hour, pool liquidity ≥ $50k.
5. Watches news (CoinDesk + Cointelegraph RSS) for hack / exploit / delist /
   halted withdrawals / SEC charges / rug / insolvency keywords.
6. Keeps a **paper journal** (SQLite): every alert, plus price follow-ups at
   fixed horizons (+30s, +60s, 1, 5, 10, 15, 30, 60, 240, 480, 720, 1440 min).
   A missed horizon stays NULL forever — never backfilled with a later price.
7. At **n ≥ 500 CEX alerts**, runs a statistical analysis with rules frozen
   *before* data collection: FOLLOW vs FADE vs DO-NOTHING, net of 0.10% /
   0.25% / 0.50% round-trip costs. A segment is an edge only if **all** hold
   at net 0.25%: n ≥ 100, mean > +0.30%, median > 0, winrate ≥ 55%,
   second-half mean > 0, mean without the top 5% of trades > 0.
   Anything else = "no edge".

## What it does NOT do

**No real trading. No orders. No exchange API keys.** This is detection,
alerting and honest measurement only.

## Secrets

The Telegram bot token and any proxy credentials live **only** in a local
`.env` / `proxy.env` with mode 600, never in the repo, logs or reports.
Copy `.env.example` to `.env` and fill in your own values. If the bot config
is missing, the service logs an error and sends nothing anywhere — no
fallback bot, ever.

## Run

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in your own bot token and chat IDs
python -m radar --check-config
python -m radar
# tests
python -m unittest discover -s tests -v
```

See `PLAN.md` for the phased build plan and `deploy/` for a systemd unit.
