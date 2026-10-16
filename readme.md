# Grainline

讓行銷、BI、DE、前端在埋點定版之前，就看到「顧客的操作 → 真實長相的埋點資料 → 中繼表 → 儀表板」怎麼流動；乘開和缺欄位直接在資料上看得到。只用假資料。

設計：[docs/superpowers/specs/2026-10-15-grainline-refactor-design.md](docs/superpowers/specs/2026-10-15-grainline-refactor-design.md)

```bash
docker compose up --build
open http://localhost:8080
docker compose run --rm api pytest
```

## 3 分鐘 demo 腳本

1. **行銷提需求**：點「預算分配」，卡片下方列出行銷常說的話。顧問卡用一行話給出立場：預設最後點擊。
2. **數字對不上的原因**：埋點選 v1，儀表板最下面一列是「無法歸因」（約 47% 的銷售額），旁邊亮紅燈 `缺 purchase.order`。點它，時間軸會跳到「訪客結帳」那位顧客：purchase 這一步的 URL 裡 `order=?` 標成紅色，下面寫著「後端訂單接不回來」。
3. **改埋點**：切到 v2，無法歸因變成 0，同一位訪客的訂單接回來了，可以看到這筆錢算給哪個活動。
4. **歸因之爭**：切換最後點擊、首次點擊、線性，FB 和 Google 的長條互換位置。再到「助攻」，可以看到 FB 碰過最多訂單，但大多是助攻。
5. **乘開**：打開「錯誤示範：直接 join」，儀表板變紅、出現 ×2～3；時間軸上的訂單卡會展示「明細 × 觸點 = N 列」。
6. **自己操作**：選「你自己逛」，在放大的頁面上選進站來源、點商品、加入購物車、付款，每點一下，時間軸就多一步。
7. **定案**：按「定案」，產生可以簽核的 Markdown，裡面寫明前端要在哪個事件補哪個參數。

本機開發：API 用 `cd api && uv run --python 3.12 --with-editable '.[dev]' uvicorn app.main:app --port 8001`，前端用 `cd web && API_URL=http://localhost:8001 npm run dev`。
