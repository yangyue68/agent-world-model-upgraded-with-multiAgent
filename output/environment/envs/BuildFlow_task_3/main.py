import os
from datetime import date, timedelta
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class ClonePhaseStructureResponse(BaseModel):
    message: str
    cloned_phase_count: int


@app.post(
    "/projects/{target_project_id}/clone-phase-structure-from/{source_project_id}",
    response_model=ClonePhaseStructureResponse,
    operation_id="clone_phase_structure",
    tags=["PrimaryResource"],
)
def clone_phase_structure(
    target_project_id: int,
    source_project_id: int,
    new_start_date: str = Query(..., description="The new base start date for all cloned phases in YYYY-MM-DD format"),
):
    # Validate new_start_date format
    try:
        base_date = date.fromisoformat(new_start_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Expected YYYY-MM-DD.")

    with engine.connect() as conn:
        # Verify source project exists
        source_proj = conn.execute(
            text("SELECT id FROM projects WHERE id = :pid"),
            {"pid": source_project_id}
        ).fetchone()
        if not source_proj:
            raise HTTPException(status_code=404, detail=f"Source project {source_project_id} not found.")

        # Verify target project exists
        target_proj = conn.execute(
            text("SELECT id FROM projects WHERE id = :pid"),
            {"pid": target_project_id}
        ).fetchone()
        if not target_proj:
            raise HTTPException(status_code=404, detail=f"Target project {target_project_id} not found.")

        # Fetch all phases from the source project ordered by sort_order
        source_phases = conn.execute(
            text("""
                SELECT id, name, description, duration_days, default_cost_code_id, sort_order
                FROM phases
                WHERE project_id = :pid
                ORDER BY sort_order ASC
            """),
            {"pid": source_project_id}
        ).fetchall()

        if not source_phases:
            return ClonePhaseStructureResponse(
                message="No phases found in source project to clone.",
                cloned_phase_count=0
            )

        # Map old phase ID -> new phase info (for dependency remapping)
        # We will insert phases first, then handle dependencies
        
        inserted_phases = []  # List of dicts with keys: old_id, new_id, name, etc.
        
        current_start = base_date
        
        for phase_row in source_phases:
            old_phase_id = phase_row.id
            name = phase_row.name
            description = phase_row.description
            duration_days = phase_row.duration_days
            default_cost_code_id = phase_row.default_cost_code_id
            sort_order = phase_row.sort_order
            
            end_date = current_start + timedelta(days=duration_days - 1)
            
            # Insert the new phase
            result = conn.execute(
                text("""
                    INSERT INTO phases (project_id, name, description, duration_days, start_date, end_date, default_cost_code_id, sort_order)
                    VALUES (:project_id, :name, :description, :duration_days, :start_date, :end_date, :default_cost_code_id, :sort_order)
                """),
                {
                    "project_id": target_project_id,
                    "name": name,
                    "description": description,
                    "duration_days": duration_days,
                    "start_date": current_start.isoformat(),
                    "end_date": end_date.isoformat(),
                    "default_cost_code_id": default_cost_code_id,
                    "sort_order": sort_order
                }
            )
            new_phase_id = result.lastrowid
            
            inserted_phases.append({
                "old_id": old_phase_id,
                "new_id": new_phase_id
            })
            
            # Next phase starts after this one ends? 
            # The spec says "adjusting all start dates to begin on 2024-07-10".
            # Usually, cloning a template implies preserving relative offsets or sequential chaining.
            # Looking at the source data:
            # Phase 1: 2024-03-01 to 2024-03-05 (5 days)
            # Phase 2: 2024-03-06 to 2024-03-15 (10 days) -> Starts day after previous ends.
            # It seems they are strictly sequential based on duration.
            # So we chain them: next_start = prev_end + 1 day.
            current_start = end_date + timedelta(days=1)

        # Now handle dependencies
        # Fetch dependencies for the source project's phases
        source_phase_ids = [p["old_id"] for p in inserted_phases]
        placeholders = ", ".join([f":sid_{i}" for i in range(len(source_phase_ids))])
        params = {f"sid_{i}": pid for i, pid in enumerate(source_phase_ids)}
        
        deps_rows = conn.execute(
            text(f"""
                SELECT phase_id, depends_on_phase_id
                FROM phase_dependencies
                WHERE phase_id IN ({placeholders})
            """),
            params
        ).fetchall()
        
        # Create mapping old_id -> new_id
        old_to_new_map = {item["old_id"]: item["new_id"] for item in inserted_phases}
        
        for dep_row in deps_rows:
            old_phase_id = dep_row.phase_id
            old_depends_on_id = dep_row.depends_on_phase_id
            
            new_phase_id = old_to_new_map.get(old_phase_id)
            new_depends_on_id = old_to_new_map.get(old_depends_on_id)
            
            if new_phase_id and new_depends_on_id:
                conn.execute(
                    text("""
                        INSERT OR IGNORE INTO phase_dependencies (phase_id, depends_on_phase_id)
                        VALUES (:phase_id, :depends_on_phase_id)
                    """),
                    {
                        "phase_id": new_phase_id,
                        "depends_on_phase_id": new_depends_on_id
                    }
                )

        conn.commit()

        return ClonePhaseStructureResponse(
            message=f"Successfully cloned {len(inserted_phases)} phases from project {source_project_id} to project {target_project_id}.",
            cloned_phase_count=len(inserted_phases)
        )
