# Grainline

讓行銷、BI、DE、前端在埋點定版之前，就看到「顧客的操作 → 真實長相的埋點資料 → 中繼表 → 儀表板」怎麼流動；乘開和缺欄位直接在資料上看得到。只用假資料。

設計：[docs/superpowers/specs/2026-10-15-grainline-refactor-design.md](docs/superpowers/specs/2026-10-15-grainline-refactor-design.md)

```bash
docker compose up --build
open http://localhost:8080
docker compose run --rm api pytest
```
