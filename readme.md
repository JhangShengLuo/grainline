# Grainline — 埋點契約沙盒

> 讓前端、行銷、BI、DE 在同一份規格上對齊，在真實資料進來之前就能看到報表數字。

---

## 問題

電商團隊要做報表，通常流程是：行銷開需求 → BI 畫欄位 → DE 拉資料 → 前端埋點 → 等資料上線。這中間的等待通常是好幾週，而且一旦上線才發現埋點和報表對不起來，又要再跑一輪。

更難抓的問題是**指標之間的衝突**，例如：

- **週 UV ≠ 日 UV 加總**：同一個人在一週內來好幾天，每天都被算進日 UV，但週 UV 只算一次
- **轉換率的分子分母不一致**：分母只算 Web 訪客、分子忘了加條件，把 App 的買家也算進來，轉換率可能超過 100%
- **報表引用了還沒埋的欄位**：BI 以為訂單會帶折扣碼，但前端根本沒埋

這些錯誤只有 DE 看得出來，而且通常是資料上線、數字開始吵架之後才會發現。

---

## 解法

**Grainline** 是一個端到端的沙盒：從埋點規格到 DW 模型到報表，全部用 YAML 宣告。系統自動產生假資料、編譯成 DuckDB view、跑出報表數字。

改一行規格，報表 1 秒內重算——**不用等資料上線**。

三條相容性規則在規格層就先擋：

| 規則 | 檢查 |
|------|------|
| **R1 可加性** | count distinct / 比率不能從細 grain 加總到粗 grain |
| **R2 母體一致** | 比率的分子分母必須同單位、同窗口、分子 ⊆ 分母 |
| **R3 血緣可達** | L4 用到的每個欄位都能追回 L1 的 tracking plan |

---

## 為什麼需要人工簽核

規則可以機械化檢查，但**哪些衝突是可接受的取捨**需要人決定。

例如 v2 tracking plan 把「加入購物車」從 API 成功才送改成點擊就送，cart_adds 會變高、轉換率會變低。這不是錯誤，是設計取捨——但必須有人簽核這個取捨，否則下游報表就會悄悄變數字。

Grainline 把這些取捨列出來，讓 DE、BI、PM 在同一張表上 review，不是讓機器自動放行。

---

## Demo 展示內容（M1–M5，僅使用合成資料）

這個 repo 實作了 M0–M5，可以在本機跑起來：

| 功能 | 說明 |
|------|------|
| **L1–L4 規格** | 事件契約、解析規則、DW 模型、指標報表，全部寫在 `specs/shop/` |
| **合成資料** | 1,500 人 × 28 天，約 40,000 筆事件，跑一次約 1 秒 |
| **R1–R3 檢查** | API `/checks` 回傳每個問題的數字證據 |
| **Demo 商店** | `/shop` 可以走完首頁 → 分類 → 商品 → 購物車 → 結帳 → 付款 → 完成 |
| **即時更新** | 在商店下一張單，console 的報表當天訂單立即 +1 |
| **版本比較** | v1 / v2 tracking plan 同一群人同樣行為，列出每個指標的數字差異 |
| **角色化檢視** | 業務看白話說明、前端看埋點清單、DE 看資料模型與 SQL |

---

## 如何執行

```bash
docker compose up --build
```

啟動後：

- Demo 商店：http://localhost:8080/shop
- Console：http://localhost:8080/console
- API 健康檢查：http://localhost:8000/health

其他指令：

```bash
# 跑 API 測試
docker compose run --rm api pytest

# 手動重建假資料與倉儲
docker compose run --rm api python -m app.cli build

# 印出編譯後的 SQL
docker compose run --rm api python -m app.cli compile

# 執行 R1–R3 檢查
docker compose run --rm api python -m app.cli check
docker compose run --rm api python -m app.cli check --plan 2
```

前端開發（需要先啟動 API）：

```bash
cd web && npm install && npm run dev
```

---

## 下一步：YC 申請

這個沙盒證明：

1. **四層規格可以讓跨團隊在同一份文件上對齊**，不用再各自猜資料長什麼樣
2. **埋點設計的取捨可以用數字呈現**，而不是上線後才發現報表數字變了
3. **相容性規則在規格層就能擋住大部分問題**，不用等 DE 人工 review 每一張表

下一步：

- 找一個真實電商團隊做 pilot，驗證四層規格在實際開發流程中的可行性
- 補上 dbt / Cube export，讓規格直接輸出到現有工具
- 建立 hosted 版本，讓團隊不用自己架環境

---

## 專案結構

```
specs/shop/           # L1–L4 規格
  tracking_plans/     # v1 (current) / v2 (proposal)
  l2_staging.yaml
  l3_models.yaml
  l4_metrics.yaml
  simulation.yaml
api/                  # FastAPI + DuckDB
web/                  # Vite + React + TypeScript
docs/                 # 計畫書、設計決定
data/                 # DuckDB 檔案（gitignore）
```

詳細架構與驗收標準請見 [docs/PLAN.md](docs/PLAN.md)。
