# -*- coding: utf-8 -*-
"""
usstock.py — 美股大盤與類股資金輪動

資料來源：Yahoo Finance chart API(免金鑰)
  https://query1.finance.yahoo.com/v8/finance/chart/<symbol>?range=3mo&interval=1d

美股沒有免費的「三大法人」資料，故「資金流向」改以下列方式表達：
  * 三大指數 + VIX 的漲跌與趨勢
  * 11 大類股 SPDR ETF 相對大盤(SPY)的強弱 → 看資金輪動到哪個族群
  * 綜合大盤多空訊號(指數趨勢 + 市場廣度 + VIX) 並附理由
"""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from statistics import mean
from typing import Dict, List, Optional

import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

# 指數(VIX 為恐慌指數，越高越恐慌)
INDICES = [
    ("^GSPC", "S&P 500"),
    ("^IXIC", "那斯達克"),
    ("^DJI", "道瓊工業"),
    ("^VIX", "VIX 恐慌指數"),
]
BENCH = "SPY"  # 類股相對強弱的比較基準

# 11 大 SPDR 類股 ETF
SECTORS = [
    ("XLK", "科技"),
    ("XLC", "通訊服務"),
    ("XLY", "非必需消費"),
    ("XLP", "必需消費"),
    ("XLE", "能源"),
    ("XLF", "金融"),
    ("XLV", "醫療保健"),
    ("XLI", "工業"),
    ("XLB", "原物料"),
    ("XLRE", "房地產"),
    ("XLU", "公用事業"),
]

# 推薦選股的熱門大型股池(跨產業)
US_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "AVGO", "TSLA",
    "AMD", "NFLX", "CRM", "ORCL", "JPM", "V", "MA", "LLY",
    "UNH", "JNJ", "XOM", "CVX", "HD", "COST", "WMT", "KO",
]
US_NAMES = {
    "AAPL": "蘋果", "MSFT": "微軟", "NVDA": "輝達", "GOOGL": "Alphabet",
    "AMZN": "亞馬遜", "META": "Meta", "AVGO": "博通", "TSLA": "特斯拉",
    "AMD": "超微", "NFLX": "Netflix", "CRM": "Salesforce", "ORCL": "甲骨文",
    "JPM": "摩根大通", "V": "Visa", "MA": "萬事達", "LLY": "禮來",
    "UNH": "聯合健康", "JNJ": "嬌生", "XOM": "埃克森美孚", "CVX": "雪佛龍",
    "HD": "家得寶", "COST": "好市多", "WMT": "沃爾瑪", "KO": "可口可樂",
}

_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=3mo&interval=1d"


def _fetch_chart(symbol: str) -> Optional[dict]:
    """抓單一標的近 3 個月日線，回傳 {symbol, price, closes:[...]}。"""
    try:
        r = requests.get(_CHART_URL.format(sym=symbol), headers=HEADERS, timeout=20)
        if r.status_code != 200:
            return None
        res = r.json()["chart"]["result"][0]
        meta = res.get("meta", {})
        raw_closes = res["indicators"]["quote"][0].get("close", [])
        closes = [c for c in raw_closes if c is not None]
        if len(closes) < 2:
            return None
        price = meta.get("regularMarketPrice") or closes[-1]
        return {"symbol": symbol, "price": float(price), "closes": [float(c) for c in closes]}
    except (requests.RequestException, KeyError, ValueError, IndexError, TypeError):
        return None


def _ret(closes: List[float], n: int) -> Optional[float]:
    """近 n 個交易日報酬率(%)。"""
    if len(closes) <= n:
        return None
    base = closes[-1 - n]
    if base == 0:
        return None
    return (closes[-1] / base - 1) * 100


def _metrics(name: str, symbol: str, data: dict) -> dict:
    closes = data["closes"]
    day = _ret(closes, 1) or 0.0
    r5 = _ret(closes, 5)
    r20 = _ret(closes, 20)
    ma20 = mean(closes[-20:]) if len(closes) >= 20 else mean(closes)
    return {
        "symbol": symbol,
        "name": name,
        "price": round(data["price"], 2),
        "day": round(day, 2),
        "ret5": round(r5, 2) if r5 is not None else None,
        "ret20": round(r20, 2) if r20 is not None else None,
        "above_ma20": data["price"] > ma20,
    }


def _market_signal(spx: Optional[dict], vix: Optional[dict], sector_rows: List[dict]) -> dict:
    score = 0.0
    reasons: List[dict] = []

    def add(text, kind):
        reasons.append({"text": text, "kind": kind})

    if spx:
        if spx["above_ma20"]:
            score += 1; add(f"S&P 500 站上 20 日均線，中期趨勢偏多", "bull")
        else:
            score -= 1; add(f"S&P 500 跌破 20 日均線，中期轉弱", "bear")
        if spx.get("ret5") is not None:
            if spx["ret5"] > 0:
                score += 1; add(f"S&P 500 近 5 日 +{spx['ret5']:.1f}%，短線偏多", "bull")
            else:
                score -= 1; add(f"S&P 500 近 5 日 {spx['ret5']:.1f}%，短線偏弱", "bear")

    up = sum(1 for s in sector_rows if s["day"] > 0)
    total = len(sector_rows)
    if total:
        if up >= 8:
            score += 1; add(f"類股廣度強：{up}/{total} 個類股上漲，資金面廣泛流入", "bull")
        elif up <= 3:
            score -= 1; add(f"類股廣度弱：僅 {up}/{total} 個類股上漲，資金面退潮", "bear")
        else:
            add(f"類股漲跌互見：{up}/{total} 個類股上漲", "info")

    if vix:
        v = vix["price"]
        if v < 15:
            score += 1; add(f"VIX {v:.1f} 偏低，市場情緒樂觀", "bull")
        elif v > 30:
            score -= 2; add(f"VIX {v:.1f} 飆高，市場恐慌", "bear")
        elif v > 25:
            score -= 1; add(f"VIX {v:.1f} 升高，避險情緒升溫", "bear")
        else:
            add(f"VIX {v:.1f} 中性區間", "info")

    if score >= 3:
        level, emoji = "強力偏多", "🟢"
    elif score >= 1:
        level, emoji = "偏多", "🟢"
    elif score > -1:
        level, emoji = "中性", "🟡"
    elif score > -3:
        level, emoji = "偏空", "🔴"
    else:
        level, emoji = "強力偏空", "🔴"

    return {"level": level, "emoji": emoji, "score": round(score, 1), "reasons": reasons}


def _build_us_recommend(charts: Dict[str, Optional[dict]], level: str) -> dict:
    """從大型股池篩選站上 20 日均線且具動能的個股。"""
    picks = []
    for sym in US_UNIVERSE:
        c = charts.get(sym)
        if not c:
            continue
        m = _metrics(US_NAMES.get(sym, sym), sym, c)
        r5 = m["ret5"] or 0.0
        r20 = m["ret20"] or 0.0
        if m["above_ma20"] and r20 > 0:
            m["score"] = round(r20 * 0.6 + r5 * 0.3 + 3, 2)
            m["reasons"] = ["站上 20 日均線", f"近 20 日 {r20:+.1f}%", f"近 5 日 {r5:+.1f}%"]
            picks.append(m)
    picks.sort(key=lambda x: x["score"], reverse=True)
    picks = picks[:6]

    if "強力偏多" in level or level == "偏多":
        stance = "美股偏多，可順勢留意站上均線、相對強勢的權值股，仍需設好停損。"
    elif level == "中性":
        stance = "美股中性，挑趨勢向上、動能延續的個股，控管部位。"
    else:
        stance = "美股偏空，建議保守；下列為逆勢仍站上均線、相對抗跌的個股，宜謹慎、不追高。"
    return {"stance": stance, "picks": picks}


def build_us_report() -> dict:
    """組出美股報告 dict。"""
    index_syms = [s for s, _ in INDICES]
    sector_syms = [s for s, _ in SECTORS]
    all_syms = list(dict.fromkeys(index_syms + [BENCH] + sector_syms + US_UNIVERSE))

    charts: Dict[str, Optional[dict]] = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {ex.submit(_fetch_chart, sym): sym for sym in all_syms}
        for fut in futs:
            charts[futs[fut]] = fut.result()

    # 指數
    index_rows = []
    spx = vix = None
    for sym, nm in INDICES:
        c = charts.get(sym)
        if not c:
            continue
        m = _metrics(nm, sym, c)
        index_rows.append(m)
        if sym == "^GSPC":
            spx = m
        if sym == "^VIX":
            vix = m

    # 類股相對強弱(以 SPY 近 5 日報酬為基準)
    spy_c = charts.get(BENCH)
    spy_r5 = (_ret(spy_c["closes"], 5) if spy_c else None) or 0.0

    sector_rows = []
    for sym, nm in SECTORS:
        c = charts.get(sym)
        if not c:
            continue
        m = _metrics(nm, sym, c)
        m["rs5"] = round((m["ret5"] - spy_r5), 2) if m["ret5"] is not None else None  # 相對 SPY 強弱
        sector_rows.append(m)

    # 依近 5 日報酬排序：上方=資金流入族群，下方=流出
    ranked = sorted(sector_rows, key=lambda x: (x["ret5"] if x["ret5"] is not None else -999),
                    reverse=True)
    inflow = ranked[:3]
    outflow = ranked[-3:][::-1]

    signal = _market_signal(spx, vix, sector_rows)
    recommend = _build_us_recommend(charts, signal["level"])

    if not index_rows and not sector_rows:
        return {"ok": False, "error": "無法取得美股資料(可能網路問題或 Yahoo 暫時無回應)。"}

    return {
        "ok": True,
        "as_of": time.strftime("%Y-%m-%d %H:%M"),
        "spy_ret5": round(spy_r5, 2),
        "indices": index_rows,
        "sectors": ranked,
        "rotation": {"inflow": inflow, "outflow": outflow},
        "signal": signal,
        "recommend": recommend,
        "disclaimer": (
            "美股資料為 Yahoo Finance 延遲報價，類股強弱以 SPDR 類股 ETF 相對 SPY 計算，"
            "訊號為程式自動研判，僅供參考，非投資建議。"
        ),
    }


# ---------------------------------------------------------------------------
# 自訂觀察股(任意美股代號)
# ---------------------------------------------------------------------------
_SYM_RE = re.compile(r"^[A-Z0-9.\-^]{1,12}$")


def _stock_signal(m: dict) -> tuple:
    """以是否站上 20 日均線 + 近 5 日報酬，給單檔簡易訊號。"""
    r5 = m.get("ret5") or 0.0
    if m["above_ma20"] and r5 > 0:
        return "偏多", "bull"
    if (not m["above_ma20"]) and r5 < 0:
        return "偏空", "bear"
    return "中性", "info"


def quote_symbols(symbols: List[str]) -> List[dict]:
    """抓任意美股代號的報價與訊號(最多 25 檔)。"""
    syms, seen = [], set()
    for s in symbols:
        s = (s or "").strip().upper()
        if s and s not in seen and _SYM_RE.match(s):
            seen.add(s)
            syms.append(s)
    syms = syms[:25]
    if not syms:
        return []

    charts: Dict[str, Optional[dict]] = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(_fetch_chart, s): s for s in syms}
        for fut in futs:
            charts[futs[fut]] = fut.result()

    out = []
    for s in syms:
        c = charts.get(s)
        if not c:
            out.append({"symbol": s, "ok": False})
            continue
        m = _metrics(s, s, c)
        label, kind = _stock_signal(m)
        m.update({"ok": True, "signal": label, "kind": kind})
        out.append(m)
    return out


def build_quotes_report(symbols: List[str]) -> dict:
    return {
        "ok": True,
        "as_of": time.strftime("%Y-%m-%d %H:%M"),
        "quotes": quote_symbols(symbols),
    }


if __name__ == "__main__":
    rep = build_us_report()
    if not rep.get("ok"):
        print("ERR:", rep.get("error"))
    else:
        print("as_of:", rep["as_of"], "| 訊號:", rep["signal"]["level"], rep["signal"]["score"])
        print("\n指數:")
        for i in rep["indices"]:
            print(f"  {i['name']:<12} {i['price']:>10} 日{i['day']:+.2f}% 5日{i['ret5']}% MA20上方={i['above_ma20']}")
        print("\n資金流入族群(近5日強):")
        for s in rep["rotation"]["inflow"]:
            print(f"  {s['name']}({s['symbol']}) 5日{s['ret5']:+.2f}% 相對SPY{s['rs5']:+.2f}%")
        print("資金流出族群(近5日弱):")
        for s in rep["rotation"]["outflow"]:
            print(f"  {s['name']}({s['symbol']}) 5日{s['ret5']:+.2f}% 相對SPY{s['rs5']:+.2f}%")
        print("\n訊號理由:")
        for r in rep["signal"]["reasons"]:
            print("  -", r["kind"], r["text"])
