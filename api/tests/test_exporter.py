"""Tests for dbt-style SQL exporter."""

import pytest
from pathlib import Path

from app.compiler import compile_project
from app.exporter import export_dbt_sql, export_golden_file
from app.lineage import resolve
from app.spec import load_project


REPO_ROOT = Path(__file__).resolve().parents[2]
SPECS_DIR = REPO_ROOT / "specs" / "shop"


@pytest.fixture
def compiled_views():
    """Load and compile the shop project."""
    project = load_project(SPECS_DIR)
    lineage = resolve(project)
    return compile_project(project, lineage)


def test_export_dbt_sql_creates_files(compiled_views, tmp_path):
    """Export should create SQL files in staging/models/reports directories."""
    results = export_dbt_sql(compiled_views, tmp_path)
    
    assert len(results) > 0
    
    staging_files = list((tmp_path / "staging").glob("*.sql"))
    models_files = list((tmp_path / "models").glob("*.sql"))
    reports_files = list((tmp_path / "reports").glob("*.sql"))
    
    assert len(staging_files) > 0, "Expected staging SQL files"
    assert len(models_files) > 0, "Expected model SQL files"
    assert len(reports_files) > 0, "Expected report SQL files"


def test_export_dbt_sql_content_has_header(compiled_views, tmp_path):
    """Exported SQL files should have dbt-style headers."""
    export_dbt_sql(compiled_views, tmp_path)
    
    stg_events = tmp_path / "staging" / "stg_events.sql"
    assert stg_events.exists()
    
    content = stg_events.read_text()
    assert "Generated from Grainline spec" in content
    assert "Layer: L2" in content
    assert "{{ config(" in content


def test_export_golden_file_creates_single_file(compiled_views, tmp_path):
    """Golden file export should create a single SQL file with all views."""
    golden_path = tmp_path / "golden.sql"
    export_golden_file(compiled_views, golden_path)
    
    assert golden_path.exists()
    content = golden_path.read_text()
    
    assert "Grainline Golden File" in content
    assert "stg_events" in content
    assert "fct_orders" in content
    assert "rpt_daily_overview" in content


def test_export_golden_file_is_deterministic(compiled_views, tmp_path):
    """Same input should produce same golden file."""
    path1 = tmp_path / "golden1.sql"
    path2 = tmp_path / "golden2.sql"
    
    export_golden_file(compiled_views, path1)
    export_golden_file(compiled_views, path2)
    
    assert path1.read_text() == path2.read_text()
