# Grainline 重構 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 一頁式 demo：顧客旅程時間軸 → 原始 URL hit → 中繼表的列 → 三個行銷問題的儀表板，加上顧問立場、乘開與缺欄位檢查、定案卡。

**Architecture:** 精簡的新核心取代舊的 L1–L5 編譯器。模擬器依埋點規格（v1/v2）輸出真實格式的 URL hit 與後端訂單 → DuckDB 的 `raw_hits` 與 `src_*` 表 → `models.py` 裡宣告層級與粒度的 SQL view → 問題、檢查、旅程 API → React 單頁畫布。

**Tech Stack:** Python 3.12、FastAPI、DuckDB、Pydantic、PyYAML、pytest；React 19、Vite、TypeScript、Vitest；docker compose。

**規格：** `docs/superpowers/specs/2026-10-15-grainline-refactor-design.md`

**執行方式：** 在 `refactor` 分支上 inline 執行，每個 Task 完成就 commit，每天結束（或每 2–3 個 Task）push。測試在本機用 `uv run --python 3.12`，交付前在 docker 裡再跑一次。

---

## 檔案結構

```
api/app/
  main.py         FastAPI：/health /plans /questions /journeys /models /decision /live
  tracking.py     埋點規格（TrackingPlan）讀取
  hits.py         hit ⇄ URL 編碼與解碼
  sim.py          模擬器：顧客、session、活動、行為 → hits + 後端訂單
  warehouse.py    建立 DuckDB：raw_hits、src_*，套用 models
  models.py       分層 view：名稱、層級、粒度、上游、SQL
  questions.py    問題庫讀取 + 三個問題的儀表板查詢
  checks.py       乘開與缺欄位檢查（含證據）
  journey.py      單一顧客的流動軌跡
  decision.py     定案卡 Markdown
specs/flow/
  tracking/v1.yaml   現行埋點（purchase 沒帶 order）
  tracking/v2.yaml   提案（purchase 補上 order）
  questions.yaml     三個問題
api/tests/        test_hits, test_sim, test_models, test_questions, test_checks, test_journey, test_decision, test_api
web/src/
  App.tsx, main.tsx, styles.css, api.ts, types.ts
  flow/FlowPage.tsx      畫布（縮放）＋頂端列
  flow/QuestionBar.tsx   三個問題與常見說法
  flow/AdvisorCard.tsx   立場、歸因切換、明細 join 切換、埋點版本切換
  flow/Dashboard.tsx     各問題的儀表板
  flow/Timeline.tsx      旅程時間軸（StepCard：縮圖、hit、列、格子）
  flow/HitView.tsx       URL 參數上色、缺的參數標紅
  flow/RowsView.tsx      中繼表的列＋粒度標籤
  flow/ShopPage.tsx      商店頁面（縮圖／放大可操作）
  flow/DecisionCard.tsx  定案卡
```

刪除：舊的 `api/app/*`（spec、generator、compiler、lineage、checks、diff、explain、library、decisions、execution、story、assistant、shop_backend、standards、ingest、catalog、cli）、所有舊測試、`specs/shop`、`web/src/console`、`web/src/shop`、`web/src/tracking`、`web/src/MeetingPage.tsx`。

## 共用介面（後面的 Task 都照這個）

```python
# hits.py
def encode(params: dict[str, Any]) -> str           # "/collect?ev=page_view&aid=...": 依插入順序，值用 quote(safe="")；list/dict 先 json.dumps
def decode(url: str) -> dict[str, str]              # 反向；值保持字串

# tracking.py
class EventSpec(BaseModel): params: list[str]; when: str
class TrackingPlan(BaseModel): version: int; name: str; events: dict[str, EventSpec]
BASE_PARAMS = ["ev", "aid", "uid", "sid", "ts"]      # 每個 hit 都有（uid 只在登入後）
def load_plans(dir: Path) -> dict[int, TrackingPlan]

# sim.py
@dataclass class SimConfig: seed=7, visitors=1500, days=14, start=date(2026,12,1)
@dataclass class World: hits: list[dict] (hit_id:int, url:str, ts:str); orders; order_lines; products
def simulate(plan: TrackingPlan, config: SimConfig) -> World
CAMPAIGNS = {"xmas_fb": ("fb","paid_social"), "xmas_line": ("line","message"), "xmas_gg": ("google","cpc"), "xmas_edm": ("edm","email")}

# models.py
@dataclass class Model: name; layer ("raw"|"staging"|"mart"); grain: str; upstream: list[str]; sql: str; columns_doc: dict[str,str]
MODELS: list[Model]
def apply(con) -> None

# warehouse.py
def build(world: World) -> duckdb.DuckDBPyConnection   # 記憶體資料庫

# questions.py
def load_questions(path) -> dict[str, Question]
def dashboard(con, qid, attribution="last") -> dict

# checks.py
def fanout(con, qid) -> list[dict]; def missing(con, plan, qid) -> list[dict]

# journey.py
def sample_people(con) -> list[dict]; def journey(con, person_id) -> dict

# decision.py
def decision_card(question, plan, attribution, checks) -> str
```

---

## 第 1 天：清場與資料核心

### Task 1：砍掉舊程式碼，留下可以跑的骨架
**Files:** 刪除上面列的檔案；修改 `api/pyproject.toml`（移除 anthropic、jsonschema、apache-ossie-cube、package-data、scripts；packages=["app"]）、`docker-compose.yml`（移除 AI 環境變數，specs 掛載改 `./specs`→`/repo/specs` 不變）、`.env.example`（刪除）、`api/app/main.py`（只剩 /health）、`web/src/App.tsx`（暫時顯示「重建中」）、`readme.md`。
- [ ] `git rm` 舊檔案；main.py 只留 `/health`（DuckDB select 1）
- [ ] `api/tests/test_api.py` 只測 /health
- [ ] `uv run --python 3.12 --with-editable 'api[dev]' pytest -q api/tests` → 1 passed
- [ ] web：`npm run build` 通過
- [ ] commit：「Strip old shell for the journey-first rebuild」

### Task 2：hits 編碼與埋點規格
**Files:** `api/app/hits.py`、`api/app/tracking.py`、`specs/flow/tracking/v1.yaml`、`v2.yaml`、`api/tests/test_hits.py`
- [ ] 測試：`decode(encode(p)) == {k: str 或 json 字串}`；中文與 `&`、`=` 會被跳脫；`items` list 會變成 JSON 字串；`load_plans` 讀到 v1、v2，v1 purchase 沒有 `order`、v2 有
- [ ] 實作（`urllib.parse.quote(str, safe="")`、`parse_qsl` 的 unquote）
- [ ] v1.yaml：
  ```yaml
  version: 1
  name: 現行埋點
  events:
    page_view: {params: [page, utm_source, utm_medium, utm_campaign], when: 每次頁面載入；utm 只在從廣告進站的第一頁}
    list_view: {params: [page, list, items], when: 商品列表顯示；items 是這次看到的商品陣列}
    product_view: {params: [page, sku, cat, price], when: 商品頁載入}
    add_to_cart: {params: [sku, qty, price], when: 加入購物車成功}
    purchase: {params: [value], when: 付款完成頁}
  ```
  v2 同上，但 `purchase.params: [order, value]`，name: 提案：purchase 帶訂單編號
- [ ] 測試通過 → commit

### Task 3：模擬器
**Files:** `api/app/sim.py`、`api/tests/test_sim.py`
行為（確定性，`random.Random(seed)`）：
- 24 個 SKU（6 品類 × 4：耳機、吸塵器、保溫瓶、外套、保養、配件），價格、成本、吸引力 appeal、轉換 conv（刻意讓 3 個 SKU 高吸引力、低轉換）
- 每位訪客：一個 `aid`；60% 是會員（有 `uid`，結帳時登入）；1–4 個 session，平均分散在 14 天內並排序
- 每個 session 的進站來源：第 1 個 session 55% 來自活動，權重 fb .45、line .30、gg .15、edm .10；之後的 session 50% 來自活動，權重 gg .40、edm .35、fb .15、line .10；其餘是直接進站（沒有 utm）
- session 流程：page_view（有活動時 `/campaign/xmas`，否則 `/`）→ list_view（某品類 6 個商品，items 帶 sku、cat、price、pos）→ 依 appeal 看 1–3 個 product_view → 每個依 conv 決定 add_to_cart → 有購物車時，購買機率 = 0.25 + 0.15 ×（第幾個 session − 1），上限 0.7
- 購買：會員在購買前登入（之後的 hit 帶 uid）；非會員是訪客結帳（後端訂單 `uid` 為 null）。後端寫入 `orders(order_id, uid, created_at, total, item_count)`、`order_lines(order_id, line_no, sku, qty, unit_price, line_revenue)`；前端送出 `purchase` hit，帶什麼參數照埋點規格
- hit 的參數只輸出規格裡有的（加上 BASE_PARAMS），所以換規格時行為完全相同，只有 hit 不同
- [ ] 測試：同 seed 兩次結果相同；v1 與 v2 的訂單完全相同、hit 數相同；v1 purchase hit 沒有 `order`、v2 有；至少有一位顧客 ≥3 個 session 且跨 2 個以上活動；有訪客訂單（uid null）；有多 SKU 訂單；orders.total = 明細加總
- [ ] 實作、測試通過 → commit

### Task 4：倉儲與分層 view
**Files:** `api/app/warehouse.py`、`api/app/models.py`、`api/tests/test_models.py`
- [ ] raw：`raw_hits(hit_id BIGINT, url VARCHAR, source VARCHAR)`、`src_orders`、`src_order_lines`、`src_products`
- [ ] views（每個都宣告 layer、grain、upstream）：
  - `stg_hit_params`（1 列 = 1 個 hit 的 1 個參數）：`from raw_hits h, unnest(string_split(split_part(h.url,'?',2),'&')) t(kv)`，key=`split_part(kv,'=',1)`、value=`url_decode(split_part(kv,'=',2))`
  - `stg_hits`（1 列 = 1 個 hit）：從 params pivot：ev、aid、uid、sid、ts::timestamp、page、utm_*、list、items、sku、cat、price::double、qty::int、order_id（param `order`）、value::double
  - `stg_hit_items`（1 列 = hit 裡的 1 個商品）：`unnest(from_json(items, '[{"sku":"VARCHAR","cat":"VARCHAR","price":"DOUBLE","pos":"INTEGER"}]'))`
  - `stg_identity`（1 列 = 1 個 aid）：有 uid 的 hit 中，aid → uid
  - `fct_sessions`（1 列 = 1 個 session）：每個 sid 的第一個 hit 的 utm，start_ts，aid，`person_id = coalesce(identity.uid, aid)`
  - `fct_touches`（1 列 = 1 次活動進站）：fct_sessions 中 utm_campaign 不為 null 的列
  - `fct_orders`（1 列 = 1 張訂單）：src_orders 加上 `person_id = coalesce(uid, 用 purchase hit 的 order_id 找到的 aid 經 identity)`；找不到時為 null
  - `fct_order_lines`（1 列 = 訂單 × SKU）：src_order_lines join src_products（cat）
  - `fct_attributed_orders`（1 列 = 訂單 × 活動 × 歸因方式）：30 天回溯；last/first 權重 1，linear 權重 1/n；沒有觸點的訂單歸給 `(直接進站)`；person_id 為 null 的歸給 `(無法歸因)`
  - `fct_sku_funnel`（1 列 = 1 個 SKU）：曝光（stg_hit_items）、瀏覽、加購、訂單數、件數、銷售額
- [ ] 測試：stg_hits 列數 = raw_hits；stg_hit_items 列數 = 所有 items 長度加總；三種歸因下 `sum(revenue)` 都等於 `sum(fct_orders.total)`（誤差 < 0.01）；每張訂單的 linear 權重加總 = 1；v1 有 `(無法歸因)`、v2 沒有；fct_sku_funnel 的件數加總 = order_lines qty 加總
- [ ] 實作、測試通過 → commit → **push（第 1 天結束）**

## 第 2 天：問題、檢查

### Task 5：問題庫與儀表板
**Files:** `specs/flow/questions.yaml`、`api/app/questions.py`、`api/tests/test_questions.py`
- [ ] questions.yaml：`budget`、`assists`、`category`，每題有：`title`、`phrasings`（行銷常見說法 2–3 句）、`stance`（一行立場）、`attribution: {default, options}`（category 沒有）、`requires`（[{event, param, why}]）、`fanout`（要示範的錯誤 join：`lines_x_touches` 或 `items_x_lines`）
- [ ] `dashboard(con, "budget", attribution)` → 各活動的 revenue、orders（sum weight）、占比，以及三種歸因並列；`assists` → 各活動：被碰過的訂單數、最後點擊訂單數、助攻訂單數＝前兩者相減、線性與最後點擊營收差；`category` → 各 SKU 的漏斗，以及 `flag`（瀏覽數 ≥ 中位數，且瀏覽→下單轉換 ≤ 第 25 百分位）
- [ ] 測試：budget 合計＝總營收；assists 的助攻訂單數 ≥ 0，且至少有一個活動 > 0；category 至少標出 1 個 SKU
- [ ] commit

### Task 6：檢查（乘開、缺欄位）
**Files:** `api/app/checks.py`、`api/tests/test_checks.py`
- [ ] `fanout(con, qid)`：
  - `lines_x_touches`：錯誤做法是 `fct_order_lines join fct_touches on person`，比較 `count(*)` 與 `count(distinct order_id)`、`sum(total)` 與真實營收，並附上一張範例訂單（明細數 × 觸點數 → 列數）
  - `items_x_lines`：錯誤做法是 `stg_hit_items join fct_order_lines on sku`，比較件數加總與真實件數，並附上範例 SKU
  - 回傳 `{kind, wrong_sql, right_sql, wrong, right, ratio, example: {key, rows_before, rows_after}}`
- [ ] `missing(con, plan, qid)`：對每個 requires，回傳該事件的 hit 數、帶這個參數的 hit 數、出現率、status（ok ≥ 99%／partial／missing＝0%），以及 `impact`（`purchase.order` 時＝無法歸因的銷售額與占比）
- [ ] 測試：v1 budget 的 `purchase.order` 是 missing、impact > 0；v2 是 ok；fanout ratio > 1，且範例的列數等於明細數 × 觸點數
- [ ] commit → **push（第 2 天結束）**

## 第 3 天：旅程、定案、API

### Task 7：旅程
**Files:** `api/app/journey.py`、`api/tests/test_journey.py`
- [ ] `sample_people(con)`：挑出最多 6 位代表性的顧客，類型包括多活動觸點加多 SKU 訂單、訪客結帳、只逛不買；回傳 `{person_id, label, touches, orders}`
- [ ] `journey(con, person_id)` →
  ```
  {person_id, steps: [{hit_id, ts, sid, ev, url, params: {k: v}, page: {kind, sku?, items?},
     rows: [{model, grain, values}], session_start: bool}],
   orders: [{order_id, total, after_hit_id, lines: [...], attribution: {last: [...], first: [...], linear: [...]}}]}
  ```
  rows：stg_hits 一列、list_view 的 stg_hit_items N 列、session 第一個 hit 的 fct_sessions 和 fct_touches 列
- [ ] 測試：steps 依時間排序；list_view 的列數 = items 數；訂單的 attribution linear 權重加總 = 1；訪客訂單在 v1 是 `(無法歸因)`
- [ ] commit

### Task 8：定案卡
**Files:** `api/app/decision.py`、`api/tests/test_decision.py`
- [ ] Markdown 內容：問題、埋點版本、選定的歸因、儀表板指標與粒度、乘開注意事項（錯誤與正確的 SQL 各一行）、前端待辦（每個 missing 或 partial：「在 `purchase` 事件補上 `order`：原因、影響金額」）、日期
- [ ] 測試：v1 budget 的卡片包含「`purchase`」和「`order`」；v2 沒有前端待辦
- [ ] commit

### Task 9：API
**Files:** `api/app/main.py`、`api/tests/test_api.py`
- [ ] `create_app(spec_dir, config)`：每個埋點版本建一個倉儲，lazy 建立並快取
- [ ] 端點：`GET /plans`、`GET /questions`、`GET /questions/{id}?plan=&attribution=`（dashboard + checks）、`GET /journeys?plan=`（sample_people）、`GET /journeys/{person}?plan=`、`GET /models?plan=`（名稱、層級、粒度、上游、列數）、`GET /questions/{id}/decision?plan=&attribution=`（text/markdown）
- [ ] 測試：用 300 位訪客的設定，每個端點都回 200，結構正確；未知的問題或版本回 404
- [ ] commit → **push（第 3 天結束）**

## 第 4–5 天：前端

### Task 10：前端骨架與資料
**Files:** `web/src/App.tsx`、`web/src/api.ts`、`web/src/types.ts`、`web/src/flow/FlowPage.tsx`、`QuestionBar.tsx`、`AdvisorCard.tsx`
- [ ] `useApi<T>(path)` hook；types 對應 API
- [ ] FlowPage 狀態：qid、plan、attribution、fanout、personId；頂端是 QuestionBar（三張卡，每張列出 phrasings），下面是 AdvisorCard（stance、歸因切換、「用明細直接 join」切換、埋點 v1/v2 切換）
- [ ] build 通過 → commit

### Task 11：儀表板
**Files:** `web/src/flow/Dashboard.tsx`
- [ ] budget：各活動橫條（同一色，選中的歸因），切換歸因時寬度用 CSS transition 移動；fanout 開啟時，數字換成錯誤值並標紅，顯示「×ratio」與正確值；`(無法歸因)` 是灰色列
- [ ] assists：每個活動「最後點擊訂單」與「助攻訂單」兩段堆疊條
- [ ] category：SKU 漏斗表（曝光 → 瀏覽 → 加購 → 下單），被標出的列以警示色呈現
- [ ] 缺欄位警示列：「v1 的 purchase 沒有 order → NT$ X（Y%）無法歸因」，可以點，點了會捲到時間軸上對應的 hit
- [ ] commit

### Task 12：旅程時間軸與 hit、列
**Files:** `web/src/flow/Timeline.tsx`、`HitView.tsx`、`RowsView.tsx`
- [ ] 顧客選擇（sample_people 的 chips）；時間軸橫向捲動，每一步是一欄：縮圖 → hit → 列；session 開頭有分隔線與進站來源
- [ ] HitView：`/collect?` 後的每個參數是一個 chip（key 是主色，value 是 mono 字型）；規格要求但沒有的參數用紅色虛線 chip 顯示（例如 `order=?`）；items 可以展開成 JSON 陣列
- [ ] RowsView：每張表一個小表格，表頭有粒度標籤（「1 列 = 1 個商品」）
- [ ] 訂單區塊：接在 purchase 之後，顯示後端訂單、明細，以及依目前歸因分配到各活動的金額；fanout 開啟時展示明細 × 觸點的列，標紅
- [ ] commit

### Task 13：商店頁面縮圖與放大
**Files:** `web/src/flow/ShopPage.tsx`、`web/src/flow/FlowPage.tsx`
- [ ] `ShopPage({page, scale, interactive, onAction})`：依 page.kind 畫出活動頁、首頁、列表（items 卡片）、商品頁（加入購物車按鈕）、完成頁
- [ ] 縮圖是 scale 0.35 的 ShopPage；點擊後在畫布上方放大（in-flow 的展開區），interactive 為 true
- [ ] 畫布縮放：右上角 −／100%／＋，作用在 Timeline 容器的 `transform: scale()`，ctrl+滾輪也可以縮放
- [ ] commit → **push（第 5 天結束）**

## 第 6–7 天：互動、定案、收尾

### Task 14：即時操作（放大頁面上點擊 → 時間軸多一步）
**Files:** `api/app/sim.py`（`LiveVisitor`）、`api/app/main.py`（`POST /live/actions`、`DELETE /live`）、`web/src/flow/ShopPage.tsx`
- [ ] 後端：`POST /live/actions {plan, action: land|list|product|add|purchase, campaign?, sku?}`，用同一套 hit 產生規則，為 person `live-you` 追加 hit（source=live）；purchase 會寫入後端訂單。回傳新的旅程
- [ ] 測試：land（fb）→ list → product → add → purchase 之後，旅程有 5 步，且有 1 張訂單、最後點擊歸給 xmas_fb
- [ ] 前端：放大頁的按鈕呼叫 actions；「你的旅程」出現在顧客選擇的第一個
- [ ] commit

### Task 15：定案卡畫面
**Files:** `web/src/flow/DecisionCard.tsx`
- [ ] 「定案」按鈕 → 取得 Markdown → 顯示卡片，提供複製和下載 .md
- [ ] commit

### Task 16：收尾
- [ ] web 的 Vitest：HitView 會把缺的參數標成 missing（1 個測試）；api.ts 的格式化
- [ ] docker：`docker compose up -d --build`，用瀏覽器走過 demo；`docker compose run --rm api pytest -q`
- [ ] 更新 `readme.md`（demo 腳本：3 分鐘怎麼講）、`docs/PLAN.md` 開頭加註「已由 refactor 取代，見 specs」
- [ ] commit → **push**

## 自我檢查（對照規格）
- 三個問題 → Task 5、11；首次點擊對比 → Task 4、11
- 旅程時間軸、縮圖放大、畫布縮放 → Task 12、13
- 原始 hit 真實格式、缺參數標紅 → Task 2、3、12
- 乘開示範（1 列變 N 列）→ Task 6、11、12
- 定案卡 → Task 8、15
- 埋點版本切換（v1→v2 無法歸因變 0）→ Task 2、4、10
- 規模（N 位顧客）→ SimConfig.visitors；畫面以 sample_people 挑人
- 沒有大段文字 → 每個元件都以資料呈現；stance 只有一行
