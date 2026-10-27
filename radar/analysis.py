"""Frozen-methodology analysis (TZ 4): which reaction to a spike is profitable.

Rules (frozen before data collection, never relaxed retroactively):
- PnL = sign * (exit/entry - 1) * 100%.
- Strategies: FOLLOW (pump->long, dump->short), FADE (pump->short, dump->long).
  DO-NOTHING = 0 by construction (baseline).
- Entries: alert print, pe30, pe60. Horizons: 1..1440 min.
- Costs: gross and net at 0.10% / 0.25% / 0.50% round-trip.
- PASS bar (on net 0.25%, all conditions at once):
  n >= 100, mean > +0.30%, median > 0, winrate >= 55%,
  mean of 2nd half > 0, mean ex-top-5% > 0.
  Everything else = "no edge". n < 100 = "insufficient data".
"""
from __future__ import annotations

import math
import sqlite3
import statistics
import time
from dataclasses import dataclass, field

COSTS: dict[str, float] = {
    "gross": 0.0,
    "net010": 0.10,
    "net025": 0.25,
    "net050": 0.50,
}
ENTRIES: tuple[tuple[str, str], ...] = (
    ("print", "price"),
    ("pe30", "pe30"),
    ("pe60", "pe60"),
)
HORIZONS: tuple[int, ...] = (1, 5, 10, 15, 30, 60, 240, 480, 720, 1440)
HORIZON_COL = {
    1: "p1", 5: "p5", 10: "p10", 15: "p15", 30: "p30",
    60: "p60", 240: "p240", 480: "p480", 720: "p720", 1440: "p1440",
}
STRATEGIES = ("FOLLOW", "FADE")
BUCKETS = (("<8", 0.0, 8.0), ("8-12", 8.0, 12.0), ("12-20", 12.0, 20.0), (">=20", 20.0, float("inf")))

PASS_N = 100
PASS_MEAN = 0.30
PASS_WR = 55.0


def _sign(strategy: str, direction: str) -> int:
    # FOLLOW: pump->long(+1), dump->short(-1); FADE: opposite
    return 1 if (strategy == "FOLLOW") == (direction == "pump") else -1


@dataclass
class CellStats:
    n: int = 0
    mean: float = 0.0
    median: float = 0.0
    p10: float = 0.0
    p90: float = 0.0
    winrate: float = 0.0
    mean_2nd_half: float = 0.0
    mean_ex_top5: float = 0.0
    mfe: float | None = None
    mae: float | None = None
    verdict: str = "N/A"  # PASS | NO | N/A (insufficient data)


def _cell(trades: list[dict], cost_pct: float, horizon_min: int, apply_bar: bool) -> CellStats:
    n = len(trades)
    if n == 0:
        return CellStats()
    pnls = [t["gross"] - cost_pct for t in trades]
    mean = statistics.fmean(pnls)
    median = statistics.median(pnls)
    cuts = statistics.quantiles(pnls, n=10)
    p10, p90 = cuts[0], cuts[8]
    winrate = sum(1 for p in pnls if p > 0) / n * 100.0
    by_ts = sorted(trades, key=lambda t: t["ts"])
    second = by_ts[n // 2 :]
    mean_2nd = statistics.fmean([t["gross"] - cost_pct for t in second]) if second else 0.0
    drop = max(1, math.ceil(n * 0.05))
    rest = sorted(pnls)[: n - drop]
    mean_ex = statistics.fmean(rest) if rest else 0.0

    mfe_vals, mae_vals = [], []
    for t in trades:
        lo, hi = (t["min60"], t["max60"]) if horizon_min <= 60 else (t["min1440"], t["max1440"])
        if lo is None or hi is None or t["entry"] <= 0:
            continue
        if t["sign"] == 1:
            mfe_vals.append((hi / t["entry"] - 1) * 100.0)
            mae_vals.append((lo / t["entry"] - 1) * 100.0)
        else:
            mfe_vals.append((1 - lo / t["entry"]) * 100.0)
            mae_vals.append((1 - hi / t["entry"]) * 100.0)

    verdict = "N/A"
    if apply_bar and n >= PASS_N:
        verdict = (
            "PASS"
            if (
                mean > PASS_MEAN
                and median > 0
                and winrate >= PASS_WR
                and mean_2nd > 0
                and mean_ex > 0
            )
            else "NO"
        )
    return CellStats(
        n=n, mean=mean, median=median, p10=p10, p90=p90, winrate=winrate,
        mean_2nd_half=mean_2nd, mean_ex_top5=mean_ex,
        mfe=statistics.fmean(mfe_vals) if mfe_vals else None,
        mae=statistics.fmean(mae_vals) if mae_vals else None,
        verdict=verdict,
    )


def _trades(rows: list[dict], strategy: str, entry_col: str, horizon_col: str) -> list[dict]:
    out = []
    for r in rows:
        entry, exit_ = r.get(entry_col), r.get(horizon_col)
        if entry is None or exit_ is None or entry <= 0:
            continue
        sign = _sign(strategy, r["direction"])
        out.append(
            {
                "ts": r["ts"],
                "sign": sign,
                "entry": entry,
                "gross": sign * (exit_ / entry - 1) * 100.0,
                "min60": r.get("min60"), "max60": r.get("max60"),
                "min1440": r.get("min1440"), "max1440": r.get("max1440"),
            }
        )
    return out


def _cuts(rows: list[dict]) -> dict[str, list[dict]]:
    cuts: dict[str, list[dict]] = {"all": rows}
    for d in ("pump", "dump"):
        cuts[d] = [r for r in rows if r["direction"] == d]
    for v in sorted({r["origin"] for r in rows}):
        cuts[f"venue:{v}"] = [r for r in rows if r["origin"] == v]
    for w in (60, 300, 900):
        cuts[f"window:{w}"] = [r for r in rows if r["window_s"] == w]
    for name, lo, hi in BUCKETS:
        cuts[f"pct:{name}"] = [r for r in rows if lo <= abs(r["pct"]) < hi]
    for c in sorted({r["cohort"] for r in rows}):
        cuts[f"cohort:{c}"] = [r for r in rows if r["cohort"] == c]
        for d in ("pump", "dump"):
            cuts[f"cohort:{c}:{d}"] = [r for r in rows if r["cohort"] == c and r["direction"] == d]
    return cuts


def _analyze_rows(rows: list[dict]) -> dict:
    """results[cut][strategy][entry][horizon][cost] = CellStats"""
    rows = sorted(rows, key=lambda r: r["ts"])
    results: dict = {}
    for cut_name, cut_rows in _cuts(rows).items():
        results[cut_name] = {}
        for strategy in STRATEGIES:
            results[cut_name][strategy] = {}
            for entry_name, entry_col in ENTRIES:
                results[cut_name][strategy][entry_name] = {}
                for h in HORIZONS:
                    trades = _trades(cut_rows, strategy, entry_col, HORIZON_COL[h])
                    results[cut_name][strategy][entry_name][h] = {
                        cost_name: _cell(trades, cost, h, apply_bar=(cost_name == "net025"))
                        for cost_name, cost in COSTS.items()
                    }
    return results


def _load_rows(db_path: str, table: str) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY ts")]
    except sqlite3.OperationalError:
        rows = []
    conn.close()
    return rows


def _normalize_dex(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        d = dict(r)
        d["origin"] = "dex:" + d.get("network", "?")
        d["price"] = d.get("price_usd")
        d["asset"] = d.get("token_name", "?")
        d["cohort"] = "dex"
        out.append(d)
    return out


# -- report --------------------------------------------------------------------


RED_TEAM_CAVEATS = [
    "Вход по принту алерта оптимистичен: на тонких книгах bid/ask bounce завышает FADE (поэтому есть входы +30/+60 с).",
    "Выборка смещена к бирже с самым мягким фильтром объёма: выводы описывают прежде всего этот сегмент.",
    "Повторные алерты по одному активу коррелированы — эффективное n меньше номинального.",
    "Выживаемость: умершие/делистнутые токены могут не дать поздних горизонтов — поздние горизонты смещены к выжившим.",
    "KuCoin: снимок раз в ~30 с — его алерты и дозаписи грубее остальных.",
    "Квирк данных: колонки pe60 и p1 покрывают один и тот же горизонт (+60 с) — вариант входа +60 с на горизонте 1 мин вырожден.",
    "Режим рынка за период сбора (2–3 суток) — один; перенос выводов на другой режим не гарантирован.",
]

DEVIATIONS = [
    "Binance опрашивается через /ticker/24hr вместо /ticker/price (нужен quoteVolume для фильтра объёма).",
    "При срабатывании нескольких окон у пары отчитывается самое короткое (в ТЗ порядок не задан).",
    "DEX: один алерт на токен (пара с max |Δ|), кулдаун по пулу — в ТЗ агрегация не детализирована.",
]


@dataclass
class AnalysisReport:
    n_cex: int
    n_dex: int
    n_news: int
    period: str
    cohorts: str
    results: dict  # CEX analysis
    dex_results: dict
    markdown: str = ""

    def telegram_summary(self) -> str:
        lines = [
            "📊 <b>Анализ завершён: n=%d CEX-сигналов</b>" % self.n_cex,
            "Период: %s. Когорты: %s." % (self.period, self.cohorts),
            "",
        ]
        verdict = self._verdict_lines()
        lines.extend(verdict if verdict else ["Эджей по PASS-бару не найдено."])
        lines += [
            "",
            "DEX: n=%d%s. Новостей: %d." % (
                self.n_dex,
                " — недостаточно данных" if self.n_dex < 100 else "",
                self.n_news,
            ),
            "Оговорки: вход по принту оптимистичен; фандинг на 8–24 ч не учтён;",
            "выборка смещена к бирже с мягким фильтром объёма.",
            "Полный отчёт: ANALYSIS-500.md",
        ]
        return "\n".join(lines)

    def _verdict_lines(self) -> list[str]:
        """PASS segments for the main cut (direction x strategy, entry=print)."""
        lines = []
        for direction, strategy, label in (
            ("dump", "FADE", "FADE по дампам (лонг)"),
            ("pump", "FADE", "FADE по пампам (шорт)"),
            ("dump", "FOLLOW", "FOLLOW по дампам (шорт)"),
            ("pump", "FOLLOW", "FOLLOW по пампам (лонг)"),
        ):
            cells = self.results.get(direction, {}).get(strategy, {}).get("print", {})
            passed = [h for h in HORIZONS if cells.get(h, {}).get("net025", CellStats()).verdict == "PASS"]
            if passed:
                detail = []
                for h in passed:
                    c = cells[h]["net025"]
                    detail.append(f"{h} мин (n={c.n}, mean {c.mean:+.2f}%, WR {c.winrate:.0f}%)")
                lines.append(f"✅ {label}: PASS — " + "; ".join(detail))
            else:
                lines.append(f"❌ {label}: PASS нет")
        return lines

    def write_markdown(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(self.markdown)


def _fmt_cell(c: CellStats) -> str:
    if c.n == 0:
        return "—"
    mfe = f"{c.mfe:+.1f}" if c.mfe is not None else "—"
    mae = f"{c.mae:+.1f}" if c.mae is not None else "—"
    return (
        f"{c.n} | {c.mean:+.2f} | {c.median:+.2f} | {c.p10:+.2f} | {c.p90:+.2f} | "
        f"{c.winrate:.0f} | {c.mean_2nd_half:+.2f} | {c.mean_ex_top5:+.2f} | "
        f"{mfe} | {mae} | {c.verdict}"
    )


def _table(title: str, results: dict, cut: str, strategy: str, entry: str) -> list[str]:
    lines = [f"### {title}", "",
             "| горизонт | n | mean | median | p10 | p90 | WR% | mean 2nd | mean ex5% | MFE | MAE | вердикт |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for h in HORIZONS:
        cell = results[cut][strategy][entry][h]["net025"]
        lines.append(f"| {h} мин | {_fmt_cell(cell)} |")
    lines.append("")
    return lines


def _build_markdown(report: AnalysisReport) -> str:
    r = report.results
    lines = [
        "# ANALYSIS-500 — какая торговая реакция на сигнал выгодна",
        "",
        f"Сгенерировано: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}",
        f"Выборка: n={report.n_cex} CEX-сигналов ({report.cohorts}), период {report.period}.",
        f"DEX: n={report.n_dex}. Новостей: {report.n_news}.",
        "Методика заморожена до сбора данных (раздел 4 ТЗ); PASS-бар применён дословно к net 0.25%.",
        "DO-NOTHING = 0 по построению (базовая линия).",
        "",
        "## Вердикт",
        "",
    ]
    verdict = report._verdict_lines()
    lines.extend(("- " + v for v in verdict) if verdict else ["- Эджей по PASS-бару не найдено."])
    lines += ["", "## Главные разрезы (вход по принту, net 0.25%)", ""]
    for cut, cut_title in (("all", "Все алерты"), ("dump", "Дампы"), ("pump", "Пампы")):
        for strategy in STRATEGIES:
            lines += _table(f"{cut_title} — {strategy}, вход по принту", r, cut, strategy, "print")
    lines += ["## Входы +30/+60 с (дампы и пампы, FADE)", ""]
    for cut, cut_title in (("dump", "Дампы"), ("pump", "Пампы")):
        for entry in ("pe30", "pe60"):
            lines += _table(f"{cut_title} — FADE, вход {entry}", r, cut, "FADE", entry)
    lines += ["## Матрица PASS (вход по принту, net 0.25%, FADE)", ""]
    lines.append("| разрез | " + " | ".join(f"{h}" for h in HORIZONS) + " |")
    lines.append("|---|" + "---|" * len(HORIZONS))
    for cut in sorted(r.keys()):
        if cut in ("all", "dump", "pump") or cut.startswith("cohort:"):
            continue
        row = []
        for h in HORIZONS:
            v_fade_dump = r[cut]["FADE"]["print"][h]["net025"].verdict
            row.append("✅" if v_fade_dump == "PASS" else ("·" if v_fade_dump == "NO" else "?"))
        lines.append(f"| {cut} FADE | " + " | ".join(row) + " |")
    lines.append("")
    lines += ["## Когорты", ""]
    for cut in sorted(k for k in r.keys() if k.startswith("cohort:")):
        for strategy in STRATEGIES:
            lines += _table(f"{cut} — {strategy}, вход по принту", r, cut, strategy, "print")
    lines += ["## DEX (отдельная секция)", ""]
    if report.n_dex < 100:
        lines.append(f"n={report.n_dex} < 100 — недостаточно данных, вердикт не выносится.")
        lines.append("")
    else:
        for strategy in STRATEGIES:
            lines += _table(f"DEX все — {strategy}, вход по принту", report.dex_results, "all", strategy, "print")
    lines += ["## Red-team оговорки (обязательные)", ""]
    lines += [f"{i+1}. {c}" for i, c in enumerate(RED_TEAM_CAVEATS)]
    lines += ["", "## Отклонения от ТЗ", ""]
    lines += [f"- {d}" for d in DEVIATIONS]
    lines += [
        "",
        "## Методика (кратко)",
        "- PnL = sign × (выход/вход − 1) × 100%; FOLLOW: памп→лонг, дамп→шорт; FADE: наоборот.",
        "- Входы: принт алерта, pe30, pe60. Выходы: 1, 5, 10, 15, 30, 60, 240, 480, 720, 1440 мин.",
        "- net = gross − издержки (0.10% / 0.25% / 0.50% круговые). Фандинг ≤60 мин игнорируется; 8–24 ч — не учтён (оговорка).",
        "- PASS ⇔ n ≥ 100, mean > +0.30%, median > 0, winrate ≥ 55%, mean 2-й половины > 0, mean без топ-5% > 0 (net 0.25%).",
        "- Бакеты |pct|: <8, 8–12, 12–20, ≥20. MFE/MAE из min/max колонок (≤60 мин — min60/max60, иначе min1440/max1440).",
    ]
    return "\n".join(lines) + "\n"


def run_analysis(db_path: str) -> AnalysisReport:
    cex_rows = _load_rows(db_path, "alerts")
    dex_rows = _normalize_dex(_load_rows(db_path, "dex_alerts"))
    try:
        n_news = _load_rows(db_path, "news_alerts").__len__()
    except Exception:
        n_news = 0

    results = _analyze_rows(cex_rows)
    dex_results = _analyze_rows(dex_rows)

    if cex_rows:
        t0 = time.strftime("%Y-%m-%d %H:%M", time.gmtime(min(r["ts"] for r in cex_rows)))
        t1 = time.strftime("%Y-%m-%d %H:%M", time.gmtime(max(r["ts"] for r in cex_rows)))
        period = f"{t0} — {t1} UTC"
    else:
        period = "—"
    cohorts = ", ".join(
        f"{c}: {sum(1 for r in cex_rows if r['cohort'] == c)}"
        for c in sorted({r["cohort"] for r in cex_rows})
    ) or "—"

    report = AnalysisReport(
        n_cex=len(cex_rows), n_dex=len(dex_rows), n_news=n_news,
        period=period, cohorts=cohorts, results=results, dex_results=dex_results,
    )
    report.markdown = _build_markdown(report)
    return report
