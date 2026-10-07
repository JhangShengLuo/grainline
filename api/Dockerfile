FROM python:3.12-slim

# 容器內的目錄結構和 repo 一致：/repo/api、/repo/specs、/repo/data
WORKDIR /repo/api
COPY api/pyproject.toml ./
COPY api/app ./app
RUN pip install --no-cache-dir ".[dev]"
COPY api/tests ./tests
COPY specs /repo/specs

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
