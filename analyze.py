# -*- coding: utf-8 -*-
"""
analyze.py — 把證交所原始資料轉成「資金流向報告」

產出內容：
  * 大盤三大法人今日買賣超 + 近數日趨勢
  * 大盤資金流向訊號燈(偏多/中性/偏空) + 透明的理由清單
  * 個股買賣超金額排行(外資/投信/三大法人合計、雙主力同買)
  * 連續買賣超天數
  * 重點觀察清單：每檔附「訊號 + 理由」

所有金額單位一律換算成「億元」，張數單位為「張」(1 張 = 1000 股)。
注意：本分析僅整理公開籌碼資料，為資訊參考，並非投資建議。
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional

import twse

YI = 1e8  # 一億


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def is_common_stock(code: str) -> bool:
    """判定是否為一般上市普通股(4 碼純數字、開頭 1-9)，藉此排除 ETF/權證/特別股等。"""
    return len(code) == 4 and code.isdigit() and code[0] != "0"


def lots(shares: float) -> int:
    """股 -> 張(四捨五入)。"""
    return int(round(shares / 1000.0))


def yi(amount_yuan: float) -> float:
    """元 -> 億元。"""
    return amount_yuan / YI


def change_pct(close: float, change: float) -> float:
    """以收盤價與漲跌價差回推漲跌幅(%)。"""
    prev = close - change
    if prev <= 0:
        return 0.0
    return change / prev * 100.0


def _streak(values: List[float]) -> int:
    """
    由舊到新的數列，計算「結尾連續同號天數」。
    回正數=連續買超天數；負數=連續賣超天數；0=最新一日持平或無資料。
    """
    if not values:
        return 0
    last = values[-1]
    if last > 0:
        s = 0
        for v in reversed(values):
            if v > 0:
                s += 1
            else:
                break
        return s
    if last < 0:
        s = 0
        for v in reversed(values):
            if v < 0:
                s += 1
            else:
                break
        return -s
    return 0


# ---------------------------------------------------------------------------
# 大盤訊號燈
# ---------------------------------------------------------------------------
def _market_signal(f: float, t: float, d: float,
                   f_streak: int, t_streak: int) -> dict:
    """
    依今日外資(f)、投信(t)、自營商(d) 買賣超金額(億)與連續天數，
    產生透明可解釋的訊號分數與理由。外資權重最高、自營商最低(多為避險部位)。
    """
    score = 0.0
    reasons: List[dict] = []

    def add(text: str, kind: str):
        reasons.append({"text": text, "kind": kind})  # kind: bull / bear / info

    # 外資
    if f >= 150:
        score += 2; add(f"外資大買超 {f:,.0f} 億，強力做多訊號", "bull")
    elif f >= 50:
        score += 1; add(f"外資買超 {f:,.0f} 億，資金偏流入", "bull")
    elif f <= -150:
        score -= 2; add(f"外資大賣超 {abs(f):,.0f} 億，重壓賣方", "bear")
    elif f <= -50:
        score -= 1; add(f"外資賣超 {abs(f):,.0f} 億，資金偏流出", "bear")
    else:
        add(f"外資買賣超 {f:,.0f} 億，動作不大", "info")

    # 投信
    if t >= 50:
        score += 1.5; add(f"投信大買超 {t:,.0f} 億，內資積極作帳", "bull")
    elif t >= 20:
        score += 1; add(f"投信買超 {t:,.0f} 億，內資偏多", "bull")
    elif t <= -50:
        score -= 1.5; add(f"投信大賣超 {abs(t):,.0f} 億，內資調節", "bear")
    elif t <= -20:
        score -= 1; add(f"投信賣超 {abs(t):,.0f} 億，內資轉弱", "bear")

    # 自營商(權重較低)
    if d >= 50:
        score += 0.5; add(f"自營商買超 {d:,.0f} 億", "bull")
    elif d <= -50:
        score -= 0.5; add(f"自營商賣超 {abs(d):,.0f} 億(含避險部位)", "bear")

    # 連續天數加成
    if f_streak >= 3:
        score += 1; add(f"外資已連續買超 {f_streak} 日，趨勢偏多", "bull")
    elif f_streak <= -3:
        score -= 1; add(f"外資已連續賣超 {abs(f_streak)} 日，趨勢偏空", "bear")
    if t_streak >= 3:
        score += 0.5; add(f"投信連續買超 {t_streak} 日", "bull")
    elif t_streak <= -3:
        score -= 0.5; add(f"投信連續賣超 {abs(t_streak)} 日", "bear")

    # 內外資分歧提醒
    if f * t < 0 and abs(f) >= 50 and abs(t) >= 20:
        side = "外資偏空、投信偏多(內資逆勢承接)" if f < 0 else "外資偏多、投信偏空"
        add(f"外資與投信方向分歧：{side}，宜留意角力結果", "info")

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


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def build_report(days: int = 6, top_n: int = 15, refresh: bool = False) -> dict:
    """組出完整報告 dict(可直接序列化成 JSON 給前端)。"""
    anchor, prices = twse.fetch_stock_day_all(refresh=refresh)
    if not anchor:
        return {"ok": False, "error": "無法取得證交所最新收盤資料(可能非交易日或網路問題)。"}

    trading_days = twse.recent_trading_days(anchor, days)
    if not trading_days:
        return {"ok": False, "error": "找不到可用的交易日資料。"}

    # 各交易日資料
    t86_by_day: Dict[str, Optional[dict]] = {}
    bfi_by_day: Dict[str, Optional[dict]] = {}
    for d in trading_days:
        do_refresh = refresh and (d == anchor)  # 只有最新一日需要強制更新
        t86_by_day[d] = twse.fetch_t86(d, refresh=do_refresh)
        cached_bfi = twse._load_cache(f"bfi82u_{d}.json") is not None
        bfi_by_day[d] = twse.fetch_bfi82u(d, refresh=do_refresh)
        if not cached_bfi:
            time.sleep(twse.REQUEST_GAP_SEC)

    # ---- 大盤趨勢與訊號 ----
    trend = []
    for d in trading_days:
        b = bfi_by_day.get(d)
        if not b:
            continue
        trend.append({
            "date": twse.fmt_date(d),
            "foreign": round(yi(b["foreign"]), 1),
            "trust": round(yi(b["trust"]), 1),
            "dealer": round(yi(b["dealer"]), 1),
            "total": round(yi(b["total"]), 1),
        })

    today_bfi = bfi_by_day.get(anchor) or {"foreign": 0, "trust": 0, "dealer": 0, "total": 0}
    f_today = yi(today_bfi["foreign"])
    t_today = yi(today_bfi["trust"])
    d_today = yi(today_bfi["dealer"])
    tot_today = yi(today_bfi["total"])

    f_streak = _streak([x["foreign"] for x in trend])
    t_streak = _streak([x["trust"] for x in trend])

    signal = _market_signal(f_today, t_today, d_today, f_streak, t_streak)

    market = {
        "today": {
            "foreign": round(f_today, 1),
            "trust": round(t_today, 1),
            "dealer": round(d_today, 1),
            "total": round(tot_today, 1),
        },
        "trend": trend,
        "foreign_streak": f_streak,
        "trust_streak": t_streak,
    }

    # ---- 個股層 ----
    anchor_t86 = t86_by_day.get(anchor) or {}

    # 每檔的多日淨買賣超(股)，用來算連續天數
    per_stock_series: Dict[str, Dict[str, List[float]]] = {}
    for d in trading_days:
        day_data = t86_by_day.get(d) or {}
        for code, v in day_data.items():
            s = per_stock_series.setdefault(code, {"foreign": [], "trust": []})
            s["foreign"].append(v["foreign"])
            s["trust"].append(v["trust"])

    def stock_row(code: str, v: dict) -> dict:
        px = prices.get(code, {})
        close = px.get("close", 0.0)
        chg = px.get("change", 0.0)
        fval = yi(v["foreign"] * close)
        tval = yi(v["trust"] * close)
        dval = yi(v["dealer"] * close)
        totval = yi(v["total"] * close)
        series = per_stock_series.get(code, {"foreign": [], "trust": []})
        return {
            "code": code,
            "name": v.get("name") or px.get("name", ""),
            "close": round(close, 2),
            "change_pct": round(change_pct(close, chg), 2),
            "foreign_val": round(fval, 2),
            "trust_val": round(tval, 2),
            "dealer_val": round(dval, 2),
            "total_val": round(totval, 2),
            "foreign_lots": lots(v["foreign"]),
            "trust_lots": lots(v["trust"]),
            "total_lots": lots(v["total"]),
            "foreign_streak": _streak(series["foreign"]),
            "trust_streak": _streak(series["trust"]),
        }

    rows = [stock_row(code, v) for code, v in anchor_t86.items()
            if is_common_stock(code) and prices.get(code, {}).get("close", 0) > 0]

    def top(key: str, reverse: bool, n: int = top_n):
        ordered = sorted(rows, key=lambda r: r[key], reverse=reverse)
        if reverse:
            ordered = [r for r in ordered if r[key] > 0]
        else:
            ordered = [r for r in ordered if r[key] < 0]
        return ordered[:n]

    dual = sorted(
        [r for r in rows if r["foreign_val"] > 0 and r["trust_val"] > 0],
        key=lambda r: r["foreign_val"] + r["trust_val"], reverse=True,
    )[:top_n]

    rankings = {
        "foreign_buy": top("foreign_val", True),
        "foreign_sell": top("foreign_val", False),
        "trust_buy": top("trust_val", True),
        "trust_sell": top("trust_val", False),
        "total_buy": top("total_val", True),
        "total_sell": top("total_val", False),
        "dual_buy": dual,
    }

    # ---- 重點觀察清單(訊號 + 理由) ----
    watchlist = _build_watchlist(rows)

    # ---- 推薦關注(偏多選股 + 操作立場) ----
    recommend = _build_recommend(rows, signal["level"])

    return {
        "ok": True,
        "as_of": twse.fmt_date(anchor),
        "as_of_raw": anchor,
        "trading_days": [twse.fmt_date(d) for d in trading_days],
        "market": market,
        "market_signal": signal,
        "rankings": rankings,
        "watchlist": watchlist,
        "recommend": recommend,
        "stock_count": len(rows),
        "disclaimer": (
            "本工具僅整理證交所公開的三大法人籌碼資料供研究參考，"
            "所有訊號為程式依規則自動產生，不構成任何投資建議或買賣邀約；"
            "投資決策請自行判斷並承擔風險。"
        ),
    }


def _build_watchlist(rows: List[dict]) -> List[dict]:
    """依籌碼規則挑出有明確訊號的個股，附上理由。"""
    candidates = []
    for r in rows:
        fv, tv, totv = r["foreign_val"], r["trust_val"], r["total_val"]
        fs, ts = r["foreign_streak"], r["trust_streak"]
        cp = r["change_pct"]
        bull, bear = [], []

        if fv >= 0.5 and tv >= 0.3:
            bull.append(f"外資買超 {fv:.1f} 億、投信買超 {tv:.1f} 億，雙主力同步進場")
        if ts >= 3:
            bull.append(f"投信連續 {ts} 日買超，具認養味道(中線偏多)")
        if fs >= 3:
            bull.append(f"外資連續 {fs} 日買超，持續布局")
        if totv >= 3 and cp > 0:
            bull.append(f"三大法人合計買超 {totv:.1f} 億且股價收紅 {cp:.1f}%，籌碼推升")

        if fv <= -0.5 and tv <= -0.3:
            bear.append(f"外資賣超 {abs(fv):.1f} 億、投信賣超 {abs(tv):.1f} 億，主力同步調節")
        if ts <= -3:
            bear.append(f"投信連續 {abs(ts)} 日賣超，內資調節")
        if fs <= -3:
            bear.append(f"外資連續 {abs(fs)} 日賣超，資金撤離")
        if totv <= -3 and cp < 0:
            bear.append(f"三大法人合計賣超 {abs(totv):.1f} 億且股價收黑 {cp:.1f}%，籌碼鬆動")

        if not bull and not bear:
            continue

        strength = abs(fv) + abs(tv) * 1.5
        if bull and not bear:
            direction = "bull"
            label = "強力偏多" if strength >= 8 else "偏多"
        elif bear and not bull:
            direction = "bear"
            label = "強力偏空" if strength >= 8 else "偏空"
        else:
            net = fv + tv
            direction = "mixed"
            label = "分歧偏多" if net > 0 else ("分歧偏空" if net < 0 else "方向分歧")

        candidates.append({
            **r,
            "direction": direction,
            "label": label,
            "strength": round(strength, 2),
            "reasons": bull + bear,
        })

    candidates.sort(key=lambda x: x["strength"], reverse=True)
    return candidates[:18]


def _build_recommend(rows: List[dict], market_level: str) -> dict:
    """依籌碼條件篩選偏多個股，並給隨大盤調整的操作立場。"""
    picks = []
    for r in rows:
        fv, tv, totv = r["foreign_val"], r["trust_val"], r["total_val"]
        fs, ts = r["foreign_streak"], r["trust_streak"]
        cp = r["change_pct"]
        reasons, tags, score = [], [], 0.0

        if fv >= 0.5 and tv >= 0.3:
            score += fv + tv * 1.5
            tags.append("雙主力同買")
            reasons.append(f"外資買超 {fv:.1f} 億、投信買超 {tv:.1f} 億，雙主力同步進場")
        if ts >= 3:
            score += ts * 0.8
            tags.append("投信認養")
            reasons.append(f"投信連續 {ts} 日買超，中線有認養味道")
        if fs >= 3:
            score += fs * 0.5
            tags.append("外資布局")
            reasons.append(f"外資連續 {fs} 日買超，持續布局")
        if totv >= 2 and cp > 0:
            score += totv * 0.3
            reasons.append(f"三大法人合計買超 {totv:.1f} 億且股價收紅 {cp:.1f}%")

        # 必須是法人站買方才入選
        if reasons and (fv > 0 or tv > 0):
            picks.append({
                "code": r["code"], "name": r["name"], "close": r["close"],
                "change_pct": cp, "foreign_val": fv, "trust_val": tv, "total_val": totv,
                "foreign_streak": fs, "trust_streak": ts,
                "tags": tags or ["法人買超"], "reasons": reasons,
                "score": round(score, 2),
            })

    picks.sort(key=lambda x: x["score"], reverse=True)
    picks = picks[:6]

    if "強力偏多" in market_level or market_level == "偏多":
        stance = "大盤資金偏多，可順勢留意下列法人作多、量價配合的個股，仍需設好停損。"
    elif market_level == "中性":
        stance = "大盤中性，建議挑法人持續站買方的個股、分批操作並嚴設停損。"
    else:
        stance = "大盤偏空，建議降低持股；下列為仍有法人逆勢買超、相對抗跌的標的，宜分批、控管風險，不宜追高。"

    return {"stance": stance, "picks": picks}


if __name__ == "__main__":
    import json
    rep = build_report(days=6, top_n=10)
    if not rep.get("ok"):
        print("ERROR:", rep.get("error"))
    else:
        print("資料日:", rep["as_of"], "| 交易日:", rep["trading_days"])
        print("大盤(億):", rep["market"]["today"])
        print("外資連續:", rep["market"]["foreign_streak"], "投信連續:", rep["market"]["trust_streak"])
        print("訊號:", rep["market_signal"]["level"], rep["market_signal"]["score"])
        for rs in rep["market_signal"]["reasons"]:
            print("   -", rs["kind"], rs["text"])
        print("\n外資買超前3:")
        for r in rep["rankings"]["foreign_buy"][:3]:
            print("  ", r["code"], r["name"], r["foreign_val"], "億", f'{r["change_pct"]}%')
        print("\n觀察清單前5:")
        for w in rep["watchlist"][:5]:
            print("  ", w["code"], w["name"], w["label"], "|", "；".join(w["reasons"]))
