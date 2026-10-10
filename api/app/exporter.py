"""Export compiled views as dbt-style SQL files.

Generates staging and model SQL files that can be used with dbt or similar tools.
This is a convenience export for teams that want to use the spec-compiled SQL
in their existing dbt projects.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .compiler import View


@dataclass
class ExportResult:
    path: Path
    layer: str
    model_name: str


def _dbt_header(view: View) -> str:
    """Generate dbt-style header comment."""
    return f"""-- Generated from Grainline spec
-- Layer: {view.layer}
-- Model: {view.name}
--
-- DO NOT EDIT DIRECTLY. Regenerate from specs with:
--   python -m app.cli export
"""


def _dbt_config(view: View) -> str:
    """Generate dbt config block based on layer."""
    if view.layer == "L2":
        return "{{ config(materialized='view', schema='staging') }}\n\n"
    elif view.layer == "L3":
        return "{{ config(materialized='view', schema='marts') }}\n\n"
    elif view.layer == "L4":
        return "{{ config(materialized='view', schema='reports') }}\n\n"
    return ""


def export_dbt_sql(views: Sequence[View], output_dir: Path) -> list[ExportResult]:
    """Export compiled views as dbt-style SQL files.
    
    Directory structure:
        output_dir/
            staging/
                stg_*.sql
            models/
                dim_*.sql
                fct_*.sql
            reports/
                rpt_*.sql
    """
    results = []
    
    staging_dir = output_dir / "staging"
    models_dir = output_dir / "models"
    reports_dir = output_dir / "reports"
    
    for d in (staging_dir, models_dir, reports_dir):
        d.mkdir(parents=True, exist_ok=True)
    
    for view in views:
        if view.layer == "L2":
            target_dir = staging_dir
        elif view.layer == "L3":
            target_dir = models_dir
        elif view.layer == "L4":
            target_dir = reports_dir
        else:
            continue
        
        sql_content = _dbt_header(view) + _dbt_config(view) + view.sql
        file_path = target_dir / f"{view.name}.sql"
        file_path.write_text(sql_content)
        
        results.append(ExportResult(
            path=file_path,
            layer=view.layer,
            model_name=view.name,
        ))
    
    return results


def export_golden_file(views: Sequence[View], output_path: Path) -> None:
    """Export all views as a single golden file for testing.
    
    The golden file contains all SQL statements separated by markers,
    making it easy to diff changes to the compiled output.
    """
    lines = ["-- Grainline Golden File", "-- Generated from specs", ""]
    
    for view in sorted(views, key=lambda v: (v.layer, v.name)):
        lines.append(f"-- === [{view.layer}] {view.name} ===")
        lines.append(f"create or replace view {view.name} as")
        lines.append(view.sql + ";")
        lines.append("")
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines))
