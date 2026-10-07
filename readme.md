# Grainline

An end-to-end sandbox from product to data: storefront → tracking → parsing → DW → reports, using fake data only.

See [docs/PLAN.md](docs/PLAN.md) for goals, architecture and acceptance criteria. Specs live in [specs/shop/](specs/shop/), one YAML file per layer.

```bash
docker compose up --build
curl localhost:8000/health
docker compose run --rm api pytest
docker compose run --rm api python -m app.cli build     # fake events → data/shop.duckdb
docker compose run --rm api python -m app.cli compile   # print compiled SQL
```
