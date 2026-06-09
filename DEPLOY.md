# 部署教學：把 Dashboard 變成公開網站（Render 免費方案）

成果：一個公開網址（`https://你的名稱.onrender.com`），手機電腦都能開，**需帳號密碼登入**。
全程免費。Render 免費方案閒置約 15 分鐘會休眠，下次開啟需等 30～50 秒喚醒（解法見最後）。

> 已幫你準備好 `render.yaml`、`Procfile`、`.gitignore`，程式也已支援雲端（自動讀 `PORT`、密碼保護）。
> 你只需要：① 推上 GitHub ② 在 Render 點幾下。

---

## 步驟一：推到 GitHub

1. 先安裝 Git（若還沒）：<https://git-scm.com/download/win>，一路下一步即可。
2. 我已在 `tw_fund_flow/` 幫你 `git init` 並建立第一個 commit。確認一下：
   ```powershell
   cd "C:\Users\user\Desktop\claude code\tw_fund_flow"
   git log --oneline    # 應看到一筆 "初始版本" commit
   ```
3. 到 <https://github.com/new> 建立一個**空的** repo（不要勾 Add README），取名例如 `fund-flow`。
4. 把本地專案連到該 repo 並推上去（把網址換成你的）：
   ```powershell
   git remote add origin https://github.com/你的帳號/fund-flow.git
   git branch -M main
   git push -u origin main
   ```
   > 第一次 push 會要你登入 GitHub（瀏覽器授權即可）。

---

## 步驟二：在 Render 部署

1. 到 <https://render.com> 用 GitHub 帳號註冊／登入（免費）。
2. 右上 **New +** → **Blueprint**。
3. 選剛剛的 GitHub repo → Render 會自動偵測到 `render.yaml` → 按 **Apply / Create**。
   - 它會自動設定：新加坡機房、免費方案、健康檢查 `/healthz`、並**自動產生一組登入密碼**。
4. 等待 build & deploy（約 2～4 分鐘）完成，狀態變綠色 **Live**。

### 查看 / 修改登入密碼
- 進入該服務 → 左側 **Environment** → 可看到 `APP_USER`（預設 `admin`）與 `APP_PASSWORD`。
- `APP_PASSWORD` 是 Render 自動產生的；點開可看到值，或直接改成你好記的密碼後 **Save**（會自動重新部署）。

---

## 步驟三：開來看

1. 服務頁面上方會有網址：`https://fund-flow-dashboard-xxxx.onrender.com`
2. 手機或電腦瀏覽器開啟 → 跳出登入框 → 輸入 `admin` 與你的密碼。
3. 完成！三個分頁（台股 / 美股 / BTC）都能用。

> 想加到手機主畫面：用 Safari/Chrome 開網址 → 分享 →「加入主畫面」，就像 App 一樣。

---

## 常見問題

**Q：免費方案會休眠，第一次開很慢？**
正常。閒置 15 分鐘後休眠，下次請求需喚醒 30～50 秒。若想保持喚醒：
到 <https://cron-job.org>（免費）建立一個排程，每 10 分鐘 GET 一次
`https://你的網址/healthz`（此路徑不需密碼）即可。

**Q：BTC 抓不到？**
本程式已內建 `data-api.binance.vision` 鏡像與 CoinGecko 備援，
且 `render.yaml` 指定新加坡機房，正常不會有問題。若仍異常多半是該時段 API 維護。

**Q：之後改了程式怎麼更新？**
```powershell
git add -A; git commit -m "更新"; git push
```
Render 會自動重新部署（`autoDeploy: true`）。

**Q：想換平台？**
`Procfile` 也相容 Railway / Heroku 類平台；只要記得：
① 設環境變數 `APP_PASSWORD` ② 機房選**非美國**（避免 Binance 主站被擋，雖已有備援）。
