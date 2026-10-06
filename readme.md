# Grainline

An end-to-end sandbox from product to data: storefront → tracking → parsing → DW → reports, using fake data only.

See [docs/PLAN.md](docs/PLAN.md) for goals, architecture and acceptance criteria.

```bash
docker compose up --build
curl localhost:8000/health
docker compose run --rm api pytest
```
