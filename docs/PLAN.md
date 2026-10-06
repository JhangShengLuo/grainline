# Grainline — PLAN

> 從「前端頁面 → 埋點 → 解析 → DW → 報表」的端到端沙盒。埋點設計一改，報表立即重算，並用**假資料上的數字**證明指標之間的 grain / 母體 / 血緣衝突。

## 0. 背景

電商報表常在前端埋點之前就開始規劃。前端、行銷、BI、DE 只能各自猜「到時候會有什麼資料」，分頭開會，一拖就是好幾個月。更麻煩的是，只有 DE 知道資料怎麼處理：BI 拿到的是整理好的表，看不出兩個指標的 grain 或 filter 互相衝突。例如：

- 週 UV ≠ 日 UV 加總（count distinct 不可加）
- 轉換率的分子和分母不是同一個母體（分子含 App、分母只算 Web；或分子算訂單數、分母算人數）

### 參考：PostHog 怎麼做

- 只存一張不可變的 `events` 表（event, distinct_id, timestamp, properties JSON），另用 `persons` / `person_distinct_ids` 做身分合併。
- Action、Insight（trends / funnels / retention）、HogQL view 都在**查詢時**對原始事件計算（schema-on-read），所以新定義會**回溯生效**，不需要 backfill。

**我們借用的：** 原始事件不可變；L2–L4 全部是「定義」（YAML），編譯成 DuckDB view，查詢時才算。切換埋點設計就能秒級反映到報表。

**PostHog 沒做、我們要補的：** 它沒有 grain 契約（週 UV 和日 UV 加總可並排而不警告），Trends 公式（A/B）也不檢查分子分母母體。這就是下面三條相容性規則的動機。

## 1. 目標

- **G1 單一真相：** 一套宣告式規格（YAML，放在 git），描述 L1–L4 四層，是前端、行銷、BI、DE 共用的契約。
- **G2 埋點前就有數字：** 依 L1 規格產生合成事件（persona 驅動的 session 模擬），在 DuckDB 實際算出報表。
- **G3 衝突用數字呈現：** 不只丟錯誤碼，而是「週 UV = 1,203；日 UV 加總 = 4,870；差異來自 3,667 次跨日重複造訪」。
- **G4 埋點設計可快速切換：** tracking plan v1 → v2 時，所有下游指標自動重算，並列出受影響的指標與數字 diff。
- **G5 看得見的因果：** 一個 demo 商店頁面，人點擊產生的事件和模擬器資料走同一個 ingest，業務能看到「我做了這個動作 → 報表這一格變了」，並點開計算路徑。

## 2. 非目標

- 不碰真實資料或 PII，不串接真實 GA / PostHog / Segment。**只用假資料。**
- 不追求生產級吞吐、串流即時性、多租戶、權限系統。
- 不做通用拖拉式 BI，不做任意 SQL 編輯器。
- 不用 Cube.js 或其他外部語意層（未來可加「匯出 Cube schema」）。
- 不做歸因模型、ML、預測。

## 3. 四層架構

每一層都是一份**契約**（YAML），而不是一張表；編譯後成為 DuckDB view。

| 層 | 名稱 | 擁有者 | 內容 | 執行物 |
|---|---|---|---|---|
| L1 | 事件契約 Tracking Plan | 前端、PM | event 名稱、property 與型別、觸發時機、identity 欄位（anonymous_id / user_id）、版本 | demo 前端 SDK、合成事件產生器、ingest 驗證 |
| L2 | 解析 / Staging | DE | 原始事件依 L1 驗證、轉型；session 切分；身分合併 | `raw_events`（不可變）→ `stg_*` view |
| L3 | 實體與事實模型 DW | DE | 實體（user、session、order）、事實表、**宣告 grain** 與主鍵 | `dim_*` / `fct_*` view |
| L4 | 指標與報表 | BI、行銷 | measure（宣告可加性）、filter、時間 grain、比率 = 分子 / 分母；報表 = 指標 × 維度 × grain | 編譯後的 SQL、報表 JSON |

```
demo 前端 / 模擬器 ──POST /ingest──▶ raw_events (immutable)
                                        │
                     L2 stg_* ◀─────────┘   ← 全部是 view，
                     L3 dim_* / fct_*         改 YAML 只需重新編譯，
                     L4 metrics / reports     不需要 backfill
```

## 4. 三條相容性規則

每條規則都有**靜態檢查**（看規格）和**數字證據**（在假資料上實算）。

### R1 可加性 / grain

每個 measure 必須宣告 `additive` / `semi_additive` / `non_additive`。non-additive（count distinct、比率）不可以從細 grain 加總到粗 grain。

- 例：`weekly_uv` 被定義成 `sum(daily_uv)` → 違規。
- 證據：同時算「直接在粗 grain 計算」與「細 grain 加總」，列出差值與造成差值的重複實體數。

### R2 比率母體

比率的分子與分母必須：

1. 計數單位是同一個實體（人 / 人，不是 訂單 / 人）；
2. 時間窗口相同；
3. 分子的 filter 包含分母的全部 filter（分子母體 ⊆ 分母母體）。

- 證據：列出「在分子但不在分母」的列數，以及比率是否可能 > 1。

### R3 血緣可達

L4 引用的每個欄位，都必須能經過 L3、L2 追到 L1 中已宣告的 event property，且型別相符。

- 能抓到「BI 假設會有、前端根本沒埋」的欄位。
- L1 改版時，輸出受影響的指標清單與 v1 / v2 數字 diff。
- 證據：血緣圖，標出斷鏈的位置。

## 5. Tech stack

| 範圍 | 選擇 |
|---|---|
| 後端 | Python 3.12、FastAPI、DuckDB（嵌入式，檔案放在 volume）、Pydantic 驗證 YAML |
| 前端（M3 起） | Vite + React + TypeScript + ECharts；route `/shop`（demo 商店）、`/console`（規格、規則結果、報表） |
| 測試 | pytest（後端）；之後加 Vitest |
| 部署 | docker compose |

## 6. 里程碑

- **M0：** 本文件 + `/health` 骨架（docker compose + pytest）
- **M1：** L1–L4 YAML schema、合成事件產生器、編譯成 DuckDB view
- **M2：** R1–R3 檢查器與數字證據 API
- **M3：** demo 商店前端、埋點 SDK、`POST /ingest`
- **M4：** 報表頁、tracking plan 版本切換與 diff
- **M5：** 角色化檢視（業務看得懂的「行為 → 數字」解釋）

## 7. 驗收標準

### M0

- [ ] `docker compose up --build` 後，`curl localhost:8000/health` 回 200，body 為 `{"status":"ok","duckdb":"ok"}`（實際執行 DuckDB `select 1`）
- [ ] `docker compose ps` 顯示 api 為 `healthy`
- [ ] `docker compose run --rm api pytest` 全部通過
- [ ] repo 中沒有任何真實資料

### 整體（M1–M5 完成時）

- [ ] 內建範例規格故意放入三種衝突：週 / 日 UV、母體不一致的轉換率、引用不存在的 property。檢查器逐一抓到，並附數字證據
- [ ] tracking plan 從 v1 切到 v2，10 秒內報表重算完成並列出受影響指標
- [ ] 在 demo 商店點一次「加入購物車」，console 上對應指標格 +1，並能點開 L1 → L4 計算路徑
- [ ] 所有資料都來自產生器或 demo 前端
