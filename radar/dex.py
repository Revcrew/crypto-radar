"""DEX spike monitor (TZ 1.5).

Source: DexScreener (token-profiles/latest + token-boosts/top -> pairs),
poll 60 s, networks solana / bsc / base / ethereum.
(GeckoTerminal tends to 429 from server IPs, so DexScreener is used;
choice documented per TZ.)

Alert: |d| >= 40% over 5 min OR >= 80% over 1 h, pool liquidity >= $50K.
One alert per token per cycle (the pair with max |d|); cooldown per pool
60 min, persistent in the state file. The first poll after start only
seeds state — no alerts on already-existing pools.
"""
from __future__ import annotations

import html
import logging
from collections import deque
from dataclasses import dataclass

import requests

from .state import StateStore

log = logging.getLogger("radar.dex")

CHAINS = ("solana", "bsc", "base", "ethereum")
CHAIN_DISPLAY = {"solana": "Solana", "bsc": "BSC", "base": "Base", "ethereum": "Ethereum"}

MIN_LIQUIDITY_USD = 50_000.0
COOLDOWN_S = 60 * 60
HISTORY_S = 3700  # a bit more than the 1 h window

# (window_s, pct_threshold, reference tolerance_s); shortest window first
RULES: tuple[tuple[int, float, int], ...] = (
    (300, 40.0, 90),
    (3600, 80.0, 300),
)

_PROFILES_URL = "https://api.dexscreener.com/token-profiles/latest/v1"
_BOOSTS_URL = "https://api.dexscreener.com/token-boosts/top/v1"
_TOKENS_URL = "https://api.dexscreener.com/tokens/v1/{chain}/{addresses}"


@dataclass(frozen=True)
class DexPair:
    chain: str
    pair_address: str
    token_address: str
    token_symbol: str
    token_name: str
    price_usd: float
    liquidity_usd: float
    url: str

    @property
    def pool_key(self) -> str:
        return f"{self.chain}:{self.pair_address}"


@dataclass(frozen=True)
class DexTrigger:
    pair: DexPair
    pct: float
    window_s: int
    price_now: float
    price_then: float


@dataclass(frozen=True)
class DexAlert:
    chain: str
    token_symbol: str
    token_name: str
    contract: str
    pool_address: str  # pair that fired (pool_ref in the journal)
    direction: str  # "pump" | "dump"
    pct: float
    window_s: int
    price_usd: float
    liquidity_usd: float
    url: str
    ts: float


def _f(value) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result


class DexScreenerClient:
    def __init__(self, timeout: int = 20):
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "crypto-crime-radar/0.1"})
        self._timeout = timeout

    def _get_json(self, url: str):
        resp = self._session.get(url, timeout=self._timeout)
        resp.raise_for_status()
        return resp.json()

    def fetch_pairs(self) -> list[DexPair]:
        profiles = self._get_json(_PROFILES_URL) or []
        boosts = self._get_json(_BOOSTS_URL) or []
        tokens: dict[tuple[str, str], None] = {}
        for entry in profiles + boosts:
            chain = entry.get("chainId")
            address = entry.get("tokenAddress")
            if chain in CHAINS and address:
                tokens[(chain, address)] = None

        by_chain: dict[str, list[str]] = {}
        for chain, address in tokens:
            by_chain.setdefault(chain, []).append(address)

        pairs: list[DexPair] = []
        for chain, addresses in by_chain.items():
            for i in range(0, len(addresses), 30):
                batch = addresses[i : i + 30]
                url = _TOKENS_URL.format(chain=chain, addresses=",".join(batch))
                try:
                    raw_pairs = self._get_json(url) or []
                except Exception as exc:
                    # one slow batch must not kill the whole DEX round
                    log.warning("dex batch %s failed: %s", chain, type(exc).__name__)
                    continue
                for raw in raw_pairs:
                    pair = self._parse_pair(chain, raw)
                    if pair is not None:
                        pairs.append(pair)
        return pairs

    @staticmethod
    def _parse_pair(chain: str, raw: dict) -> DexPair | None:
        price = _f(raw.get("priceUsd"))
        liquidity = _f((raw.get("liquidity") or {}).get("usd"))
        pair_address = raw.get("pairAddress") or ""
        base_token = raw.get("baseToken") or {}
        token_address = base_token.get("address") or ""
        if not pair_address or not token_address or price is None or price <= 0:
            return None
        return DexPair(
            chain=chain,
            pair_address=pair_address,
            token_address=token_address,
            token_symbol=(base_token.get("symbol") or "?")[:24],
            token_name=(base_token.get("name") or "?")[:64],
            price_usd=price,
            liquidity_usd=liquidity if liquidity is not None else 0.0,
            url=raw.get("url") or "https://dexscreener.com",
        )


class DexMonitor:
    def __init__(self, state: StateStore, client: DexScreenerClient | None = None):
        self._state = state
        self._client = client or DexScreenerClient()
        self._buffers: dict[str, deque] = {}
        self._latest: dict[str, DexPair] = {}
        self.last_pairs: list[DexPair] = []

    def poll(self, now: float) -> list[DexAlert]:
        pairs = self._client.fetch_pairs()
        self.last_pairs = pairs
        for pair in pairs:
            buf = self._buffers.setdefault(pair.pool_key, deque())
            buf.append((now, pair.price_usd))
            cutoff = now - HISTORY_S
            while buf and buf[0][0] < cutoff:
                buf.popleft()
            self._latest[pair.pool_key] = pair

        section = self._state.get_section("dex")
        if not section.get("seeded"):
            section["seeded"] = True
            self._state.save()
            log.info("dex: seeded %d pools, no alerts on first poll", len(pairs))
            return []

        alerts = self._aggregate(self._detect(now), now, section)
        # prune expired cooldowns
        cooldowns = section.setdefault("cooldowns", {})
        for key in [k for k, ts in cooldowns.items() if now - ts > 2 * COOLDOWN_S]:
            del cooldowns[key]
        self._state.save()
        return alerts

    def _detect(self, now: float) -> list[DexTrigger]:
        triggers: list[DexTrigger] = []
        for key, pair in self._latest.items():
            if pair.liquidity_usd < MIN_LIQUIDITY_USD:
                continue
            buf = self._buffers.get(key) or deque()
            if not buf:
                continue
            for window_s, pct_needed, tolerance in RULES:
                ref = self._reference(buf, now - window_s, tolerance)
                if ref is None or ref <= 0:
                    continue
                pct = (pair.price_usd - ref) / ref * 100.0
                if abs(pct) >= pct_needed:
                    triggers.append(
                        DexTrigger(pair, pct, window_s, pair.price_usd, ref)
                    )
                    break  # shortest window wins
        return triggers

    @staticmethod
    def _reference(buf: deque, target: float, tolerance: float) -> float | None:
        best: tuple[float, float] | None = None
        for ts, price in buf:
            dist = abs(ts - target)
            if dist <= tolerance and (best is None or dist < best[0]):
                best = (dist, price)
        return best[1] if best else None

    def _aggregate(
        self, triggers: list[DexTrigger], now: float, section: dict
    ) -> list[DexAlert]:
        by_token: dict[tuple[str, str], list[DexTrigger]] = {}
        for t in triggers:
            by_token.setdefault((t.pair.chain, t.pair.token_address), []).append(t)
        cooldowns = section.setdefault("cooldowns", {})
        alerts: list[DexAlert] = []
        for (_chain, _token), group in by_token.items():
            trigger = max(group, key=lambda t: abs(t.pct))
            pair = trigger.pair
            last = cooldowns.get(pair.pool_key)
            if last is not None and now - last < COOLDOWN_S:
                continue
            cooldowns[pair.pool_key] = now
            alerts.append(
                DexAlert(
                    chain=pair.chain,
                    token_symbol=pair.token_symbol,
                    token_name=pair.token_name,
                    contract=pair.token_address,
                    pool_address=pair.pair_address,
                    direction="pump" if trigger.pct > 0 else "dump",
                    pct=trigger.pct,
                    window_s=trigger.window_s,
                    price_usd=pair.price_usd,
                    liquidity_usd=pair.liquidity_usd,
                    url=pair.url,
                    ts=now,
                )
            )
        return alerts


# -- formatting ------------------------------------------------------------


def _fmt_price(price: float) -> str:
    if price >= 1000:
        return f"${price:,.2f}"
    if price >= 1:
        return f"${price:,.4f}"
    text = f"${price:.8f}".rstrip("0").rstrip(".")
    return text if text != "$" else "$0"


def _fmt_liquidity(usd: float) -> str:
    if usd >= 1_000_000:
        return f"${usd / 1_000_000:.1f}M"
    return f"${usd / 1_000:.1f}K"


def _short_contract(address: str) -> str:
    if len(address) <= 12:
        return address
    return f"{address[:6]}…{address[-4:]}"


def format_dex_alert(alert: DexAlert) -> tuple[str, list[list[dict]]]:
    emoji = "🟢" if alert.direction == "pump" else "🔴"
    word = "ПАМП" if alert.direction == "pump" else "ДАМП"
    window_label = {300: "5 мин", 3600: "1 час"}.get(alert.window_s, f"{alert.window_s}с")
    chain = CHAIN_DISPLAY.get(alert.chain, alert.chain)
    text = "\n".join(
        [
            f"{emoji} <b>DEX-{word} {html.escape(alert.token_symbol)}</b> · {chain}",
            f"<b>{alert.pct:+.2f}%</b> за {window_label}",
            f"💧 Ликвидность: {_fmt_liquidity(alert.liquidity_usd)}",
            f"Цена: {_fmt_price(alert.price_usd)}",
            f"Контракт: <code>{html.escape(_short_contract(alert.contract))}</code>",
        ]
    )
    keyboard = [[{"text": "DexScreener", "url": alert.url}]]
    return text, keyboard
