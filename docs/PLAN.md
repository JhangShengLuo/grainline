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

### M1 設計決定

- **一層一個 YAML 檔**，放在 `specs/<project>/`：`l1_tracking_plan.yaml`、`l2_staging.yaml`、`l3_models.yaml`、`l4_metrics.yaml`，加上 `simulation.yaml`。每個檔案開頭寫明擁有者與語法。
- **信封欄位由系統固定**：`event_id, event_name, timestamp, anonymous_id, user_id, tracking_plan_version` 不在 L1 宣告；L1 只宣告 property。時間一律是台北時間、不帶時區。
- **L2 是設定，不是 SQL**：只有 session 逾時與身分合併策略（`stitch_to_user` / `anonymous_only`）。每個事件自動產生一個依 L1 型別轉好的 `stg_<event>`；L1 沒宣告的事件進 `stg_unknown_events`，不流到下游。
- **L3 不允許寫自由 SQL**：欄位只能 `from` 來源 stg view 的欄位（fact），或加上 `agg`（entity）。代價是表達力有限，換來的是每個欄位都能機械地追回 L1，R3 才做得到。
- **比率在報表 grain 上由分子、分母的彙總值相除**，不對比率本身加總。
- **合成資料可重現**：同一個 `seed` 產生完全相同的事件。property 值來源依序為 funnel 固定值 → bindings 情境值（商品、購物車、訂單）→ 依 L1 型別隨機。
- **名稱一律小寫 snake_case**，在 spec 層就擋掉，所以 compiler 可以直接把名稱放進 SQL，只需要跳脫值。

### M2 設計決定

- **L4 新增兩個可以「寫錯」的語法**，檢查器才有東西可抓：
  - `type: rollup`：先在 `from_grain` 算出某指標，再用 `agg` 彙總到報表 grain（例：週 UV = 日 UV 加總）。
  - ratio 的 `kind`：`rate`（分子 ⊆ 分母，例：轉換率）或 `per_unit`（每單位平均，例：客單價），兩者的 R2 檢查不同。
- **實際可加性由 measure 推導，不只看宣告**：`count_distinct / avg / min / max` 一律不可加；宣告成 additive 本身就是 R1 error。
- **R1 也檢查 grain 能否整齊切分**：日可以切進週和月，週不能切進月。
- **R2 的計數單位**：`count_distinct x` 的單位是 x 代表的實體；`count` 的單位是「該 model 的列」。分母的每個篩選都必須出現在分子。
- **R2 也做實測**：規格正確的 rate，仍會在假資料上量「分子不在同期分母裡」的實體數，有的話給 warning（範例中抓到跨午夜的 session）。
- **新增 `lineage` 模組作為單一真相**：每個 L3 欄位、L4 引用都追到 L1，帶型別與 enum。compiler 用它決定能建什麼，R3 用它產生 finding。
- **編譯改為容錯**：斷鏈的欄位從 model view 移除，用到它的指標、報表被跳過並列在 finding 的「影響」中；其他部分照常建立。`strict=True` 時直接報錯。
- **篩選值不在 L1 enum 裡算 R3 error 但不斷鏈**：SQL 跑得動，只是數字永遠是 0。
- **API 會偵測規格檔變動**：下一個請求自動重新產生假資料、重建倉儲（約 1 秒）。

### M3 設計決定

- **`POST /ingest` 只接收，不拒收**：只驗證信封格式（缺欄位才回 422），和 L1 不符的地方回傳 warning，事件照樣保存，由 L2 隔離或轉型（schema-on-read）。依 `event_id` 去重，SDK 重送是安全的。
- **`raw_events.source`** 區分 `sim`（模擬器）與 `live`（demo 商店）。live 事件另存在 `data/live_events_<project>.ndjson`，規格改動、倉儲重建時一併載入。
- **時間**：SDK 送 UTC ISO 字串，ingest 轉成台北時間、不帶時區（L1 約定）。
- **SDK 不綁定事件**：`track(name, props)` 由呼叫端決定；啟動時讀 `GET /tracking-plan`，送出前在瀏覽器端比對 L1。每個平台各有一個 `anonymous_id`（像不同裝置）；`identify` 後才帶 `user_id`。批次送出、失敗重送、頁面關閉時用 `sendBeacon`。
- **觸發時機照 L1 寫**：`add_to_cart` 在 API 成功後才送；`order_completed` 在付款成功頁載入時送，且同一張訂單重新整理不會再送（否則違反 `fct_orders` 的 grain）。
- **商店右側的埋點紀錄面板**：每個事件旁邊顯示 L1 的觸發時機、properties、瀏覽器端與伺服器的 warning；「伺服器解析（L2）」分頁顯示 person_id、session_id、是否被隔離。
- **刻意留一個未宣告的事件**：商品頁的「收藏」送出 `wishlist_add`，示範前端沒照契約埋點時會發生什麼。
- **前端部署**：Vite build 後由 nginx 提供，`/api` 轉發到 FastAPI；開發時用 Vite proxy。

## 7. 驗收標準

### M0

- [ ] `docker compose up --build` 後，`curl localhost:8000/health` 回 200，body 為 `{"status":"ok","duckdb":"ok"}`（實際執行 DuckDB `select 1`）
- [ ] `docker compose ps` 顯示 api 為 `healthy`
- [ ] `docker compose run --rm api pytest` 全部通過
- [ ] repo 中沒有任何真實資料

### M1

- [x] `specs/shop/` 內 L1–L4 與模擬設定皆通過 schema 驗證；壞掉的規格會列出所有錯誤，且指出是哪一層、哪個名稱
- [x] 同一個 seed 產生完全相同的事件，且每個事件的 property 名稱、型別、enum 都符合 L1
- [x] `python -m app.cli build` 產生約 4 萬筆假事件並建立 L2–L4 view，1 秒內完成
- [x] 手工事件精確驗證：30 分鐘 session 切分、身分合併回溯生效、未宣告事件隔離、型別轉換失敗為 NULL、比率由期間彙總值相除
- [x] 假資料上確實出現「週 UV < 日 UV 加總」，留給 M2 的 R1 抓

### M2

- [x] `GET /checks` 回傳 R1–R3 finding，每個都附 `evidence.summary` 與明細列；`?rule=R1` 可篩選
- [x] `GET /metrics`（含實際可加性）、`GET /metrics/{name}/lineage`（L4 → L1 路徑）、`GET /reports`、`GET /reports/{name}`（斷鏈報表回 409 與原因）
- [x] `python -m app.cli check` 印出 finding 與證據，有 error 時 exit code 為 1
- [x] 每種違規都有改壞規格的測試；精確數字用手工事件驗證
- [x] 修改規格檔後，下一個 API 請求就反映新結果（測試：補上 coupon_code 埋點後 R3 消失、報表可建立）

### M3

- [x] `docker compose up` 後 `http://localhost:8080/shop` 可以完整走過：首頁 → 分類 → 商品 → 加入購物車 → 結帳 → 付款 → 完成 → 登入
- [x] 每個事件都符合 L1；「收藏」的 `wishlist_add` 會被標示並隔離
- [x] 在商店下一張單，`GET /reports/daily_overview` 當天的 orders 立即 +1
- [x] 登入後，同一裝置登入前的事件回溯歸到同一個 person
- [x] 付款成功頁重新整理不會重複送出 `order_completed`
- [x] SDK 有 Vitest 單元測試（批次、重送、身分、L1 比對）；`/ingest` 有 pytest（去重、隔離、重建後保留、即時進報表、證據更新）

### 整體（M1–M5 完成時）

- [x] 內建範例規格故意放入三種衝突：週 / 日 UV、母體不一致的轉換率、引用不存在的 property。檢查器逐一抓到，並附數字證據
- [ ] tracking plan 從 v1 切到 v2，10 秒內報表重算完成並列出受影響指標
- [ ] 在 demo 商店點一次「加入購物車」，console 上對應指標格 +1，並能點開 L1 → L4 計算路徑
- [ ] 所有資料都來自產生器或 demo 前端
