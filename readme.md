# Grainline

An end-to-end sandbox from product to data: storefront → tracking → parsing → DW → reports, using fake data only.

See [docs/PLAN.md](docs/PLAN.md) for goals, architecture and acceptance criteria. Specs live in [specs/shop/](specs/shop/): one YAML file per layer, with tracking plan versions under `tracking_plans/`.

```bash
docker compose up --build
open http://localhost:8080/shop                         # demo shop with live tracking log
open http://localhost:8080/console                      # reports, checks, tracking plan diff
curl localhost:8000/health
docker compose run --rm api pytest
docker compose run --rm api python -m app.cli build     # fake events → data/shop.duckdb
docker compose run --rm api python -m app.cli compile   # print compiled SQL
docker compose run --rm api python -m app.cli check     # R1–R3 compatibility checks
docker compose run --rm api python -m app.cli check --plan 2
```

Frontend development (needs the API on :8000):

```bash
cd web && npm install && npm run dev
```
