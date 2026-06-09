# -*- coding: utf-8 -*-
"""
crypto.py — BTC 即時價格與多週期走勢訊號

資料來源(免金鑰)：
  * Binance  /api/v3/ticker/24hr  → 現價、24h 漲跌/高低/成交額
  * Binance  /api/v3/klines       → 各週期 K 線(算趨勢)
  * CoinGecko /simple/price        → 市值(輔助)

多週期訊號：對 15分 / 1小時 / 4小時 / 日線 各算快慢均線(7 / 25)判斷多空，
再依週期權重(越長權重越高)加權，得出綜合多空研判，並逐週期附理由。
"""

from __future__ import annotations

from statistics import mean
from typing import List, Optional

import requests

HEADERS = {"User-Agent": "Mozilla/5.0"}
# 先用 Binance 公開資料鏡像(data-api.binance.vision，不受美國地區封鎖)，
# 再退回主站；兩者皆失敗時改用 CoinGecko 取價，確保任何機房都能抓到 BTC。
BINANCE_HOSTS = ["https://data-api.binance.vision", "https://api.binance.com"]
SYMBOL = "BTCUSDT"

# (Binance 週期代碼, 抓取根數, 中文名稱, 權重)
TIMEFRAMES = [
    ("15m", 96, "15 分", 1.0),
    ("1h", 72, "1 小時", 1.5),
    ("4h", 90, "4 小時", 2.0),
    ("1d", 60, "日線", 3.0),
]
MA_FAST, MA_SLOW = 7, 25


def _get(url: str, params: dict = None) -> Optional[object]:
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=20)
        if r.status_code != 200:
            return None
        return r.json()
    except (requests.RequestException, ValueError):
        return None


def _binance_get(path: str, params: dict) -> Optional[object]:
    """依序嘗試各 Binance 主機，回傳第一個成功的 JSON。"""
    for host in BINANCE_HOSTS:
        d = _get(host + path, params)
        if d is not None:
            return d
    return None


def _coingecko_price() -> Optional[dict]:
    d = _get("https://api.coingecko.com/api/v3/simple/price",
             {"ids": "bitcoin", "vs_currencies": "usd", "include_24hr_change": "true"})
    try:
        b = d["bitcoin"]
        return {
            "price": float(b["usd"]),
            "chg24": float(b.get("usd_24h_change", 0.0)),
            "high24": None, "low24": None, "vol24_usdt": None,
            "source": "CoinGecko",
        }
    except (TypeError, KeyError, ValueError):
        return None


def _ticker24() -> Optional[dict]:
    d = _binance_get("/api/v3/ticker/24hr", {"symbol": SYMBOL})
    if d:
        try:
            return {
                "price": float(d["lastPrice"]),
                "chg24": float(d["priceChangePercent"]),
                "high24": float(d["highPrice"]),
                "low24": float(d["lowPrice"]),
                "vol24_usdt": float(d["quoteVolume"]),
                "source": "Binance",
            }
        except (KeyError, ValueError):
            pass
    return _coingecko_price()  # Binance 不可用時退回 CoinGecko


def _klines_closes(interval: str, limit: int) -> List[float]:
    d = _binance_get("/api/v3/klines", {"symbol": SYMBOL, "interval": interval, "limit": limit})
    if not isinstance(d, list):
        return []
    try:
        return [float(k[4]) for k in d]  # k[4] = 收盤價
    except (IndexError, ValueError):
        return []


def _market_cap() -> Optional[float]:
    d = _get("https://api.coingecko.com/api/v3/simple/price",
             {"ids": "bitcoin", "vs_currencies": "usd", "include_market_cap": "true"})
    try:
        return float(d["bitcoin"]["usd_market_cap"])
    except (TypeError, KeyError, ValueError):
        return None


def _tf_trend(closes: List[float]) -> Optional[dict]:
    if len(closes) < MA_SLOW + 1:
        return None
    last = closes[-1]
    ma_f = mean(closes[-MA_FAST:])
    ma_s = mean(closes[-MA_SLOW:])
    dist = (last / ma_s - 1) * 100  # 距慢均線%(動能)
    if last > ma_f and ma_f > ma_s:
        trend, kind = "偏多", "bull"
    elif last < ma_f and ma_f < ma_s:
        trend, kind = "偏空", "bear"
    else:
        trend, kind = "中性", "info"
    return {"trend": trend, "kind": kind, "dist_ma": round(dist, 2)}


def build_btc_report() -> dict:
    tk = _ticker24()

    tf_rows = []
    score = 0.0
    reasons = []
    weight_sum = 0.0
    for code, limit, label, w in TIMEFRAMES:
        closes = _klines_closes(code, limit)
        t = _tf_trend(closes)
        if not t:
            continue
        weight_sum += w
        if t["kind"] == "bull":
            score += w
        elif t["kind"] == "bear":
            score -= w
        tf_rows.append({"tf": label, **t, "weight": w})
        side = "上方" if t["dist_ma"] >= 0 else "下方"
        reasons.append({
            "kind": t["kind"],
            "text": f"{label}走勢{t['trend']}：價格位於 25 均線{side} {abs(t['dist_ma']):.1f}%",
        })

    # 綜合研判(依權重)
    if weight_sum == 0:
        signal = {"level": "資料不足", "emoji": "⚪", "score": 0, "reasons": []}
    else:
        # 門檻偏保守：「強力」需要含日線在內的多週期一致(約 7 成權重)才成立
        if score >= 5.5:
            level, emoji = "強力偏多", "🟢"
        elif score >= 1.5:
            level, emoji = "偏多", "🟢"
        elif score > -1.5:
            level, emoji = "中性", "🟡"
        elif score > -5.5:
            level, emoji = "偏空", "🔴"
        else:
            level, emoji = "強力偏空", "🔴"
        signal = {"level": level, "emoji": emoji, "score": round(score, 1),
                  "max": round(weight_sum, 1), "reasons": reasons}

    if not tk and not tf_rows:
        return {"ok": False, "error": "無法取得 BTC 資料(Binance/CoinGecko 可能暫時無回應或被封鎖)。"}

    mcap = _market_cap()

    import time as _t
    return {
        "ok": True,
        "as_of": _t.strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": "BTC / USDT",
        "ticker": tk or {},
        "market_cap": mcap,
        "timeframes": tf_rows,
        "signal": signal,
        "disclaimer": (
            "BTC 報價來自 Binance、市值來自 CoinGecko；多週期訊號以快慢均線(7/25)"
            "程式自動研判，僅供參考，非投資建議，加密貨幣波動極大請審慎評估。"
        ),
    }


if __name__ == "__main__":
    rep = build_btc_report()
    if not rep.get("ok"):
        print("ERR:", rep.get("error"))
    else:
        tk = rep["ticker"]
        print("as_of:", rep["as_of"])
        print(f"BTC ${tk.get('price'):,.0f}  24h {tk.get('chg24'):+.2f}%  "
              f"高 ${tk.get('high24'):,.0f} 低 ${tk.get('low24'):,.0f}")
        if rep["market_cap"]:
            print(f"市值 ${rep['market_cap']/1e9:,.0f}B")
        print("綜合訊號:", rep["signal"]["emoji"], rep["signal"]["level"],
              rep["signal"]["score"], "/", rep["signal"].get("max"))
        print("多週期:")
        for t in rep["timeframes"]:
            print(f"  {t['tf']:<6} {t['trend']:<4} 距25MA {t['dist_ma']:+.2f}%")
