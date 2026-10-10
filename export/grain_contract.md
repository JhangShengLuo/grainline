# Grain Contract Summary — Shop Demo

> 本文件是 `specs/shop/` 規格的摘要，用於團隊交接與 code review。
> 
> 產生日期：2026-10-11

---

## L1：事件契約（Tracking Plan）

事件由前端送出，帶有系統自動填入的信封欄位：

| 欄位 | 說明 |
|------|------|
| `event_id` | 唯一識別碼 |
| `event_name` | 事件名稱 |
| `timestamp` | 台北時間，不帶時區 |
| `anonymous_id` | 裝置識別碼（登入前後不變） |
| `user_id` | 使用者 ID（登入後才有） |
| `tracking_plan_version` | 規格版本 |

### 共用屬性

| 名稱 | 型別 | 來源 |
|------|------|------|
| `platform` | string (web / app) | session.platform |

### 事件清單

| 事件 | 觸發時機 | 主要屬性 |
|------|----------|----------|
| `page_view` | 頁面載入完成 | page_type |
| `product_view` | 商品詳情頁載入 | product_id, price |
| `add_to_cart` | 點擊加入購物車（v1: API 成功；v2: 點擊） | product_id, price, quantity |
| `checkout_start` | 結帳頁載入 | cart_value, item_count |
| `order_completed` | 訂單成立（v1: 付款 API 成功；v2: 成功頁載入） | order_id, revenue, item_count, coupon_code (v2) |
| `login` | 登入 API 成功 | method |

---

## L2：解析規則（Staging）

| 設定 | 值 |
|------|-----|
| Session 逾時 | 30 分鐘 |
| 身分合併 | `stitch_to_user`（登入前的匿名事件回溯歸到同一人） |

### 產出 View

- `stg_events`：所有 L1 宣告的事件，加上 `person_id`、`session_id`
- `stg_<event>`：每個事件一個 view，屬性已轉型
- `stg_unknown_events`：L1 沒宣告的事件（不流到下游）

---

## L3：實體與事實模型

### Fact Tables（一個事件一列）

| Model | Grain | 來源事件 |
|-------|-------|----------|
| `fct_page_views` | event_id | page_view |
| `fct_product_views` | event_id | product_view |
| `fct_cart_adds` | event_id | add_to_cart |
| `fct_orders` | order_id | order_completed |

### Entity Tables（聚合後一個實體一列）

| Model | Grain | 來源 |
|-------|-------|------|
| `dim_sessions` | session_id | 全部事件 |
| `dim_persons` | person_id | 全部事件 |

---

## L4：指標與報表

### Simple Metrics

| 指標 | Model | 聚合 | 可加性 |
|------|-------|------|--------|
| `uv` | fct_page_views | count_distinct(person_id) | ❌ non_additive |
| `page_views` | fct_page_views | count | ✅ additive |
| `sessions` | dim_sessions | count | ✅ additive |
| `product_views` | fct_product_views | count | ✅ additive |
| `cart_adds` | fct_cart_adds | count | ✅ additive |
| `cart_adders` | fct_cart_adds | count_distinct(person_id) | ❌ non_additive |
| `orders` | fct_orders | count | ✅ additive |
| `revenue` | fct_orders | sum(revenue) | ✅ additive |
| `buyers` | fct_orders | count_distinct(person_id) | ❌ non_additive |
| `web_uv` | fct_page_views | count_distinct(person_id) where platform=web | ❌ non_additive |

### Ratio Metrics

| 指標 | 分子 | 分母 | 類型 |
|------|------|------|------|
| `conversion_rate` | buyers | uv | rate |
| `aov` | revenue | orders | per_unit |
| `web_conversion_rate` | buyers | web_uv | rate |

### Rollup Metrics

| 指標 | 來源 | 來源 grain | 聚合 |
|------|------|------------|------|
| `weekly_uv_from_daily` | uv | day | sum |

### Reports

| 報表 | 時間粒度 | 維度 | 指標 |
|------|----------|------|------|
| `daily_overview` | day | — | uv, sessions, page_views, cart_adds, orders, revenue, conversion_rate, aov |
| `weekly_by_platform` | week | platform | uv, cart_adders, buyers, conversion_rate, revenue |
| `marketing_weekly` | week | — | uv, weekly_uv_from_daily, web_uv, web_conversion_rate |
| `promotion_weekly` | week | coupon_code | orders, revenue |

---

## 刻意埋入的衝突（R1–R3）

這些是為了示範檢查器而刻意放入的衝突，正式環境應修正。

### R1 可加性衝突

**`weekly_uv_from_daily`**

- 問題：把每日 UV 加總當週 UV，同一個人在一週內來好幾天會被重複計算
- 數字證據：週 UV 直接算 = 1,203；日 UV 加總 = 4,870；差異來自跨日重複造訪
- 正確做法：週 UV 應該直接在週的 grain 上算 count_distinct

### R2 母體不一致

**`web_conversion_rate`**

- 問題：分母只篩選 platform = web，分子忘了加條件
- 結果：App 的買家也被算進分子，轉換率可能 > 100%
- 正確做法：分子也應加上 platform = web 的篩選

### R3 血緣斷裂

**`fct_orders.coupon_code`**

- 問題：L3 引用 `coupon_code`，但 v1 的 `order_completed` 沒有宣告這個屬性
- 結果：v1 下這個欄位永遠是 NULL，`promotion_weekly` 報表無法建立
- 正確做法：v2 已補上 `coupon_code`，或從後端 join 訂單表

---

## v1 vs v2 Tracking Plan 取捨

| 改動 | v1 | v2 | 影響 |
|------|----|----|------|
| **product_view** | 獨立事件 | 併入 page_view | v2 的 `product_views` 指標要改成從 page_view where page_type=product 計算 |
| **add_to_cart 觸發時機** | API 成功才送 | 點擊就送 | v2 會多記 API 失敗的點擊，cart_adds 會變高 |
| **order_completed 觸發時機** | 付款 API 成功 | 成功頁載入 | v2 會漏掉沒等到頁面的人，成功頁重新整理會重複計算，違反 fct_orders 的 grain |
| **coupon_code** | ❌ 沒有 | ✅ 有 | v2 才能做折扣碼成效報表 |

### 取捨決策

- v2 的「點擊就送」對前端來說比較好埋，但會讓 cart_adds 包含 API 失敗的操作
- v2 的「成功頁載入」避免後端埋點的複雜度，但重新整理會產生重複，R1 grain 檢查會抓到
- 團隊必須決定：接受這些取捨，還是維持 v1 的設計

---

## 相關檔案

| 路徑 | 內容 |
|------|------|
| `specs/shop/tracking_plans/v1.yaml` | 現行 tracking plan |
| `specs/shop/tracking_plans/v2.yaml` | 前端提案 |
| `specs/shop/l2_staging.yaml` | 解析規則 |
| `specs/shop/l3_models.yaml` | 實體與事實模型 |
| `specs/shop/l4_metrics.yaml` | 指標與報表 |
| `specs/shop/simulation.yaml` | 合成資料設定 |

---

## 驗證指令

```bash
# 建立假資料與倉儲
docker compose run --rm api python -m app.cli build

# 執行 R1–R3 檢查
docker compose run --rm api python -m app.cli check

# 比較 v1 vs v2
docker compose run --rm api python -m app.cli check --plan 2
```
