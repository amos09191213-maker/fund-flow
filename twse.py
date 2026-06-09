# -*- coding: utf-8 -*-
"""
twse.py — 台灣證券交易所(上市)資料抓取與本機快取

資料來源(免金鑰、合法的證交所開放資料 rwd API)：
  1. T86           個股三大法人買賣超日報 (股數)
  2. BFI82U        大盤三大法人買賣金額統計表 (金額)
  3. STOCK_DAY_ALL 全市場個股當日收盤行情 (用來把股數換算成金額)

所有抓回來的資料都會正規化後存到 cache/ 目錄，
非當日的歷史資料一旦快取就不再重抓，對證交所伺服器友善、重開也秒載。
"""

from __future__ import annotations

import json
import os
import time
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

import requests

# ---------------------------------------------------------------------------
# 基本設定
# ---------------------------------------------------------------------------
BASE = "https://www.twse.com.tw/rwd/zh"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
REQUEST_GAP_SEC = 0.8  # 連續打 API 之間的禮貌性間隔


def _ensure_cache_dir() -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)


def _cache_path(name: str) -> str:
    return os.path.join(CACHE_DIR, name)


def _today_str() -> str:
    return date.today().strftime("%Y%m%d")


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def parse_num(s) -> float:
    """把證交所回傳的字串(可能含逗號、'--'、空白)轉成 float，無法解析回 0。"""
    if s is None:
        return 0.0
    if isinstance(s, (int, float)):
        return float(s)
    t = str(s).replace(",", "").replace(" ", "").strip()
    if t in ("", "--", "---", "X", "x", "N/A", "null", "None"):
        return 0.0
    # 處理括號表示負數的情況(少見)
    neg = t.startswith("(") and t.endswith(")")
    if neg:
        t = t[1:-1]
    try:
        v = float(t)
        return -v if neg else v
    except ValueError:
        return 0.0


def _find_idx(fields: List[str], *keywords: str, exclude: Tuple[str, ...] = ()) -> Optional[int]:
    """在 fields 中找出同時包含所有 keywords、且不含 exclude 任一字串的欄位索引。"""
    for i, f in enumerate(fields):
        name = str(f)
        if all(k in name for k in keywords) and not any(e in name for e in exclude):
            return i
    return None


def _http_get_json(url: str) -> Optional[dict]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=25)
        if r.status_code != 200:
            return None
        try:
            return r.json()
        except ValueError:
            return None
    except requests.RequestException:
        return None


# ---------------------------------------------------------------------------
# 快取讀寫
# ---------------------------------------------------------------------------
def _load_cache(name: str) -> Optional[dict]:
    p = _cache_path(name)
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None
    return None


def _save_cache(name: str, data: dict) -> None:
    _ensure_cache_dir()
    try:
        with open(_cache_path(name), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 1) STOCK_DAY_ALL — 全市場最新收盤行情
# ---------------------------------------------------------------------------
def fetch_stock_day_all(refresh: bool = False) -> Tuple[Optional[str], Dict[str, dict]]:
    """
    回傳 (交易日字串YYYYMMDD, {code: {name, close, change, volume, turnover}})。
    這支 API 只提供「最新一個交易日」的資料，用來當作整份報告的錨定日與收盤價來源。
    """
    url = f"{BASE}/afterTrading/STOCK_DAY_ALL?response=json"
    raw = _http_get_json(url)
    if not raw or raw.get("stat") != "OK":
        return None, {}

    the_date = raw.get("date") or _today_str()
    fields = raw.get("fields", [])
    i_code = _find_idx(fields, "證券代號") or 0
    i_name = _find_idx(fields, "證券名稱") or 1
    i_vol = _find_idx(fields, "成交股數")
    i_turn = _find_idx(fields, "成交金額")
    i_close = _find_idx(fields, "收盤價")
    i_chg = _find_idx(fields, "漲跌價差")

    result: Dict[str, dict] = {}
    for row in raw.get("data", []):
        code = str(row[i_code]).strip()
        close = parse_num(row[i_close]) if i_close is not None else 0.0
        result[code] = {
            "name": str(row[i_name]).strip(),
            "close": close,
            "change": parse_num(row[i_chg]) if i_chg is not None else 0.0,
            "volume": parse_num(row[i_vol]) if i_vol is not None else 0.0,
            "turnover": parse_num(row[i_turn]) if i_turn is not None else 0.0,
        }

    # 快取(當日資料盤後才穩定，這裡仍存檔，refresh=True 可強制更新)
    _save_cache(f"stockday_{the_date}.json", {"date": the_date, "data": result})
    return the_date, result


# ---------------------------------------------------------------------------
# 2) T86 — 個股三大法人買賣超 (股數)
# ---------------------------------------------------------------------------
def fetch_t86(ymd: str, refresh: bool = False) -> Optional[Dict[str, dict]]:
    """
    回傳指定日期 {code: {name, foreign, trust, dealer, total}} (單位：股，買超為正)。
    若該日非交易日或無資料回 None。會做磁碟快取。
    """
    cache_name = f"t86_{ymd}.json"
    if not refresh:
        cached = _load_cache(cache_name)
        if cached is not None:
            return cached.get("data")

    url = f"{BASE}/fund/T86?date={ymd}&selectType=ALLBUT0999&response=json"
    raw = _http_get_json(url)
    if not raw or raw.get("stat") != "OK":
        return None

    fields = raw.get("fields", [])
    i_code = _find_idx(fields, "證券代號") or 0
    i_name = _find_idx(fields, "證券名稱") or 1
    # 外資 = 外陸資(不含外資自營商) + 外資自營商
    i_foreign_main = _find_idx(fields, "外陸資", "買賣超")
    i_foreign_dealer = _find_idx(fields, "外資自營商", "買賣超")
    i_trust = _find_idx(fields, "投信", "買賣超")
    # 自營商合計：欄名為「自營商買賣超股數」(不帶括號)，需排除(自行買賣)/(避險)
    i_dealer = _find_idx(fields, "自營商買賣超股數", exclude=("(", "（"))
    i_total = _find_idx(fields, "三大法人買賣超")

    # 取用到的最大欄位索引，藉此過濾掉長度不足的列(例如尾端的合計/附註列)
    used = [x for x in (i_code, i_name, i_foreign_main, i_foreign_dealer,
                        i_trust, i_dealer, i_total) if x is not None]
    need_len = (max(used) + 1) if used else 2

    result: Dict[str, dict] = {}
    for row in raw.get("data", []):
        if not isinstance(row, list) or len(row) < need_len:
            continue
        code = str(row[i_code]).strip()
        if not code:
            continue
        fm = parse_num(row[i_foreign_main]) if i_foreign_main is not None else 0.0
        fd = parse_num(row[i_foreign_dealer]) if i_foreign_dealer is not None else 0.0
        trust = parse_num(row[i_trust]) if i_trust is not None else 0.0
        dealer = parse_num(row[i_dealer]) if i_dealer is not None else 0.0
        total = parse_num(row[i_total]) if i_total is not None else (fm + fd + trust + dealer)
        result[code] = {
            "name": str(row[i_name]).strip(),
            "foreign": fm + fd,
            "trust": trust,
            "dealer": dealer,
            "total": total,
        }

    _save_cache(cache_name, {"date": ymd, "data": result})
    return result


# ---------------------------------------------------------------------------
# 3) BFI82U — 大盤三大法人買賣金額 (元)
# ---------------------------------------------------------------------------
def fetch_bfi82u(ymd: str, refresh: bool = False) -> Optional[dict]:
    """
    回傳指定日期大盤三大法人買賣超金額(單位：元，買超為正)：
      {foreign, trust, dealer, total}
    若該日無資料回 None。會做磁碟快取。
    """
    cache_name = f"bfi82u_{ymd}.json"
    if not refresh:
        cached = _load_cache(cache_name)
        if cached is not None:
            return cached.get("data")

    url = f"{BASE}/fund/BFI82U?dayDate={ymd}&type=day&response=json"
    raw = _http_get_json(url)
    if not raw or raw.get("stat") != "OK":
        return None

    foreign = trust = dealer = total = 0.0
    for row in raw.get("data", []):
        unit = str(row[0])
        diff = parse_num(row[-1])  # 買賣差額在最後一欄
        # 注意比對順序：「外資及陸資(不含外資自營商)」字串裡含有「自營商」，
        # 故必須先判斷「外資」，避免被誤歸到自營商。
        if "合計" in unit:
            total = diff
        elif "投信" in unit:
            trust += diff
        elif "外資" in unit:    # 外資及陸資 + 外資自營商
            foreign += diff
        elif "自營商" in unit:  # 自營商(自行買賣) + 自營商(避險)
            dealer += diff

    if total == 0.0:
        total = foreign + trust + dealer

    data = {"foreign": foreign, "trust": trust, "dealer": dealer, "total": total}
    _save_cache(cache_name, {"date": ymd, "data": data})
    return data


# ---------------------------------------------------------------------------
# 交易日序列
# ---------------------------------------------------------------------------
def recent_trading_days(anchor_ymd: str, n: int, max_lookback: int = 30) -> List[str]:
    """
    從 anchor 日(含)往回找出最近 n 個交易日(以 T86 是否有資料判定)，回傳由舊到新。
    會用到 fetch_t86 的快取，所以不會重複打 API。
    """
    anchor = datetime.strptime(anchor_ymd, "%Y%m%d").date()
    days: List[str] = []
    fetched = 0
    cur = anchor
    while len(days) < n and fetched < max_lookback:
        ymd = cur.strftime("%Y%m%d")
        cache_name = f"t86_{ymd}.json"
        had_cache = _load_cache(cache_name) is not None
        data = fetch_t86(ymd)
        if not had_cache:
            fetched += 1
            time.sleep(REQUEST_GAP_SEC)  # 只有真的打 API 才禮貌等待
        if data:
            days.append(ymd)
        cur = cur - timedelta(days=1)
    return list(reversed(days))


def fmt_date(ymd: str) -> str:
    """YYYYMMDD -> YYYY-MM-DD"""
    try:
        return datetime.strptime(ymd, "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return ymd


if __name__ == "__main__":
    # 簡易自我測試
    d, prices = fetch_stock_day_all()
    print("最新交易日:", d, "收盤檔數:", len(prices))
    days = recent_trading_days(d, 5)
    print("最近交易日:", days)
    bfi = fetch_bfi82u(d)
    print("大盤(億):", {k: round(v / 1e8, 1) for k, v in bfi.items()})
    t86 = fetch_t86(d)
    print("T86 檔數:", len(t86) if t86 else 0)
