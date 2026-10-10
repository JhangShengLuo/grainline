# Design Decisions

> 本文件記錄 Grainline 各里程碑的設計決定，摘錄自 [PLAN.md](PLAN.md)。

---

## M1：L1–L4 YAML Schema 與合成事件產生器

**日期**：2026-09-30（專案啟動）

### DEC-M1-01：一層一個 YAML 檔

- **決定**：每層規格獨立一個檔案，放在 `specs/<project>/`
- **檔案結構**：
  - `tracking_plans/v*.yaml`（L1）
  - `l2_staging.yaml`
  - `l3_models.yaml`
  - `l4_metrics.yaml`
  - `simulation.yaml`
- **理由**：每層擁有者不同（前端、DE、BI），分開檔案讓 code review 更清楚

### DEC-M1-02：信封欄位由系統固定

- **決定**：`event_id, event_name, timestamp, anonymous_id, user_id, tracking_plan_version` 不在 L1 宣告，由系統自動填入
- **理由**：減少重複宣告，確保每個事件都有一致的基礎結構

### DEC-M1-03：L2 是設定，不是 SQL

- **決定**：L2 只寫 session 逾時和身分合併策略，不寫自由 SQL
- **理由**：機械式產生 `stg_<event>` view，確保每個欄位都能追回 L1

### DEC-M1-04：L3 不允許自由 SQL

- **決定**：L3 欄位只能用 `from` 引用來源 stg view 的欄位，或加上 `agg` 聚合
- **代價**：表達力有限
- **換來**：每個欄位都能機械地追回 L1，R3 血緣檢查才做得到

### DEC-M1-05：比率在報表 grain 上相除

- **決定**：比率由分子、分母的彙總值相除，不對比率本身加總
- **理由**：避免 ratio 被錯誤加總

### DEC-M1-06：合成資料可重現

- **決定**：同一個 `seed` 產生完全相同的事件
- **值來源順序**：funnel 固定值 → bindings 情境值 → 依 L1 型別隨機
- **理由**：測試需要 deterministic 資料

### DEC-M1-07：名稱一律小寫 snake_case

- **決定**：在 spec 層就擋掉非 snake_case 的名稱
- **理由**：compiler 可以直接把名稱放進 SQL，只需要跳脫值

---

## M2：R1–R3 檢查器與數字證據

**日期**：2026-10-02

### DEC-M2-01：刻意可以「寫錯」的語法

- **決定**：L4 新增 `type: rollup`（先在細 grain 算，再彙總到粗 grain）和 ratio 的 `kind`（rate / per_unit）
- **理由**：檢查器才有東西可抓；這些語法讓錯誤明確化

### DEC-M2-02：實際可加性由 measure 推導

- **決定**：`count_distinct / avg / min / max` 一律 non_additive，宣告成 additive 本身就是 R1 error
- **理由**：不能只靠宣告，要機械檢查

### DEC-M2-03：R1 也檢查 grain 切分

- **決定**：日可以切進週和月，週不能切進月
- **理由**：7 天一週和月的天數對不上

### DEC-M2-04：R2 的計數單位

- **決定**：`count_distinct x` 的單位是 x 代表的實體；`count` 的單位是該 model 的列
- **理由**：分子分母單位必須一致

### DEC-M2-05：R2 也做實測

- **決定**：規格正確的 rate 仍在假資料上實測「分子不在同期分母裡」的實體數
- **結果**：抓到跨午夜的 session 問題

### DEC-M2-06：lineage 模組作為單一真相

- **決定**：每個 L3 欄位、L4 引用都追到 L1，帶型別與 enum
- **理由**：compiler 用它決定能建什麼，R3 用它產生 finding

### DEC-M2-07：編譯改為容錯

- **決定**：斷鏈的欄位從 model view 移除，用到它的指標、報表被跳過並列在 finding 的「影響」中
- **例外**：`strict=True` 時直接報錯
- **理由**：部分錯誤不應該讓整個系統無法運作

### DEC-M2-08：篩選值不在 enum 裡算 R3 error

- **決定**：SQL 跑得動，只是數字永遠是 0
- **理由**：不斷鏈，但要警告

### DEC-M2-09：API 偵測規格檔變動

- **決定**：下一個請求自動重新產生假資料、重建倉儲
- **效能**：約 1 秒
- **理由**：開發時改 YAML 不用手動 rebuild

---

## M3：Demo 商店與 Ingest

**日期**：2026-10-04

### DEC-M3-01：POST /ingest 只接收，不拒收

- **決定**：只驗證信封格式，和 L1 不符的地方回傳 warning，事件照樣保存
- **理由**：schema-on-read，由 L2 隔離或轉型

### DEC-M3-02：raw_events.source 區分來源

- **決定**：`sim`（模擬器）與 `live`（demo 商店）分開
- **保存**：live 事件另存在 `data/live_events_<project>.ndjson`，倉儲重建時載入
- **理由**：規格改動不應該丟失已收集的事件

### DEC-M3-03：時間處理

- **決定**：SDK 送 UTC ISO 字串，ingest 轉成台北時間、不帶時區
- **理由**：統一時區，和 L1 約定一致

### DEC-M3-04：SDK 不綁定事件

- **決定**：`track(name, props)` 由呼叫端決定；啟動時讀 `GET /tracking-plan`，送出前在瀏覽器端比對 L1
- **理由**：SDK 可以通用，L1 比對在前端做能提早發現問題

### DEC-M3-05：刻意留一個未宣告的事件

- **決定**：商品頁的「收藏」送出 `wishlist_add`
- **理由**：示範前端沒照契約埋點時會發生什麼

---

## M4：版本切換與 Diff

**日期**：2026-10-06

### DEC-M4-01：行為和埋點分開

- **決定**：模擬器與 demo 商店只回報「行為時刻」（moment），L1 用 `fires_on` 決定送哪些事件
- **理由**：換一份 tracking plan，商店程式碼和模擬的使用者行為都不用改

### DEC-M4-02：真實世界的行為放在 simulation.yaml

- **設定**：加入購物車 API 失敗率、折扣碼使用率、成功頁未載入率、重新整理率
- **理由**：不同的埋點設計「看到」這些行為的不同部分

### DEC-M4-03：兩個版本同一群人、同樣行為

- **決定**：行為用一組亂數、事件 id 與隨機 property 用另一組
- **理由**：送出的事件數不同也不會讓行為分岔；`/diff` 差異全部來自 tracking plan

### DEC-M4-04：模擬資料截在觀察期結束的午夜

- **理由**：最後一天不會只剩跨午夜的零星 session

### DEC-M4-05：L1 多版本支援

- **結構**：`specs/<project>/tracking_plans/v*.yaml`，有 `status`（current / proposal / retired）
- **API**：所有 API 接受 `?plan=`，省略時用 current
- **效能**：每個版本一個倉儲，第一次用到時建立（約 1 秒）

### DEC-M4-06：缺少來源事件改為 R3 問題

- **決定**：model 的來源事件不在 L1 不再是格式錯誤，而是 R3 `missing_event`
- **理由**：換版本時很常見，應該列出影響範圍而不是整個規格無法載入

### DEC-M4-07：新增 grain 資料檢查

- **決定**：L3 宣告的 grain 在資料上要成立（歸在 R1）
- **範例**：v2 在成功頁送訂單，重新整理造成重複 order_id

### DEC-M4-08：property 可以 required: false

- **決定**：情境裡沒有值時不帶（例如沒用折扣碼），SDK 與 ingest 不會因此發 warning

---

## M5：角色化檢視

**日期**：2026-10-07

### DEC-M5-01：依角色調整導覽與預設頁

- **角色**：業務/行銷、BI、前端/PM、DE
- **實作**：右上「我是」，記在瀏覽器；所有頁面都看得到，只是排序不同

### DEC-M5-02：指標說明自動產生

- **內容**：一句話摘要、L1→L4 步驟、要注意的地方
- **理由**：不另外維護文件，減少不一致

### DEC-M5-03：「舉個例子」用實際模擬使用者

- **選人邏輯**：優先挑能看出重點的人
  - 不重複人數：挑有多筆事件的人
  - rollup：挑一週來好幾天的人
  - grain 重複：挑有重複的人

### DEC-M5-04：埋點清單內容

- **顯示**：時機、欄位來源、下游用途
- **警示**：下游需要但還沒埋的、demo 商店送了但 L1 沒宣告的、不符 L1 的事件統計

### DEC-M5-05：資料模型頁內容

- **顯示**：L2 規則、每個 model 的 grain 檢查、欄位血緣、編譯後的 SQL

---

## 整體設計原則

### 借鏡 PostHog

- 原始事件不可變
- L2–L4 全部是「定義」（YAML），編譯成 DuckDB view，查詢時才算
- 切換埋點設計就能秒級反映到報表

### PostHog 沒做、我們補的

- Grain 契約（週 UV 和日 UV 加總可並排而不警告 → 我們會抓）
- Trends 公式（A/B）的分子分母母體檢查
