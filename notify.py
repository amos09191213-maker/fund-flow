# -*- coding: utf-8 -*-
"""
notify.py — 組三市場摘要並推播到 Telegram

需要環境變數：
    TELEGRAM_BOT_TOKEN   你的 Telegram Bot Token(向 @BotFather 申請)
    TELEGRAM_CHAT_ID     接收訊息的 chat id(可向 @userinfobot 查)

由 server 的 /api/push 觸發(再由 GitHub Actions 定時呼叫)，
未設定上述變數時會回傳友善訊息而不會出錯。
"""

from __future__ import annotations

import os
import time

import requests

import analyze
import usstock
import crypto


def build_text_summary() -> str:
    """組合台股 / 美股 / BTC 的精簡文字摘要(純文字，避免 Markdown 轉義問題)。"""
    lines = ["📊 資金流向摘要  " + time.strftime("%m/%d %H:%M")]

    # 台股
    try:
        tw = analyze.build_report(days=6)
        if tw.get("ok"):
            m, s = tw["market"]["today"], tw["market_signal"]
            lines.append("")
            lines.append(f"🇹🇼 台股 {s['emoji']}{s['level']}")
            lines.append(f"  外資 {m['foreign']:+.0f} / 投信 {m['trust']:+.0f} / 合計 {m['total']:+.0f} 億")
            fb = tw["rankings"]["foreign_buy"][:3]
            if fb:
                lines.append("  外資買超: " + "、".join(f"{r['name']} {r['foreign_val']:+.1f}億" for r in fb))
    except Exception as e:  # noqa: BLE001
        lines.append(f"🇹🇼 台股: 取得失敗({e})")

    # 美股
    try:
        us = usstock.build_us_report()
        if us.get("ok"):
            s = us["signal"]
            lines.append("")
            lines.append(f"🇺🇸 美股 {s['emoji']}{s['level']}")
            spx = next((i for i in us["indices"] if i["symbol"] == "^GSPC"), None)
            if spx:
                lines.append(f"  S&P500 {spx['price']} ({spx['day']:+.2f}%)")
            inflow = us["rotation"]["inflow"]
            if inflow:
                lines.append("  資金流入: " + "、".join(f"{x['name']}({x['ret5']:+.1f}%)" for x in inflow))
    except Exception as e:  # noqa: BLE001
        lines.append(f"🇺🇸 美股: 取得失敗({e})")

    # BTC
    try:
        b = crypto.build_btc_report()
        if b.get("ok"):
            t, s = b["ticker"], b["signal"]
            price = t.get("price")
            chg = t.get("chg24")
            lines.append("")
            ps = f"${price:,.0f}" if price else "—"
            cs = f"({chg:+.1f}%)" if chg is not None else ""
            lines.append(f"₿ BTC {s['emoji']}{s['level']}  {ps} {cs}")
    except Exception as e:  # noqa: BLE001
        lines.append(f"₿ BTC: 取得失敗({e})")

    lines.append("")
    lines.append("⚠️ 程式自動產生，僅供參考，非投資建議")
    return "\n".join(lines)


def send_telegram(text: str) -> dict:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        return {"ok": False, "error": "未設定 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID"}
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": text, "disable_web_page_preview": True},
            timeout=20,
        )
        ok = r.status_code == 200
        try:
            ok = ok and bool(r.json().get("ok"))
        except ValueError:
            pass
        return {"ok": ok, "status": r.status_code}
    except requests.RequestException as e:
        return {"ok": False, "error": str(e)}


def push() -> dict:
    """組摘要並推播；回傳結果(含預覽文字，方便除錯)。"""
    text = build_text_summary()
    res = send_telegram(text)
    return {"ok": bool(res.get("ok")), "preview": text, "detail": res}


if __name__ == "__main__":
    print(build_text_summary())
    print("\n--- 送出結果 ---")
    print(push()["detail"])
