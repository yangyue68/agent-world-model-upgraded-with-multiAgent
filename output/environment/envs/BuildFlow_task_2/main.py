import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)

app = FastAPI()


class PhaseUpdateRequest(BaseModel):
    new_name: str


class AttachmentResponse(BaseModel):
    attachment_id: int
    task_id: int
    file_name: str


class DependencyResponse(BaseModel):
    dependency_id: int
    task_id: int
    depends_on_task_id: int


class PhaseUpdateResponse(BaseModel):
    message: str
    updated_phase_id: int
    reassigned_task_count: int
    affected_attachments: List[AttachmentResponse]
    affected_dependencies: List[DependencyResponse]


class TaskAttachmentItem(BaseModel):
    attachment_id: int
    file_name: str
    file_path: str


class TaskDependencyItem(BaseModel):
    dependency_id: int
    depends_on_task_id: int
    dependency_type: str
    status: str


class TaskDetailResponse(BaseModel):
    task_id: int
    task_name: str
    status: str
    start_date: str
    due_date: str
    attachments: List[TaskAttachmentItem]
    dependencies: List[TaskDependencyItem]


@app.put(
    "/projects/{project_id}/phases/{phase_id}",
    response_model=PhaseUpdateResponse,
    operation_id="update_phase_name_and_reassign_tasks",
)
def update_phase_name_and_reassign_tasks(
    project_id: int, phase_id: int, body: PhaseUpdateRequest
):
    with engine.begin() as conn:
        # Verify the phase exists and belongs to the specified project
        row = conn.execute(
            text("SELECT id, name FROM phases WHERE id = :pid AND project_id = :proj_id"),
            {"pid": phase_id, "proj_id": project_id},
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Phase not found in project")

        old_name = row.name
        new_name = body.new_name

        # Check for uniqueness constraint violation (UNIQUE(project_id, name))
        conflict = conn.execute(
            text(
                "SELECT id FROM phases WHERE project_id = :proj_id AND name = :new_name AND id != :phase_id"
            ),
            {"proj_id": project_id, "new_name": new_name, "phase_id": phase_id},
        ).fetchone()

        if conflict:
            raise HTTPException(
                status_code=409,
                detail=f"A phase named '{new_name}' already exists in this project.",
            )

        # Get all tasks associated with this phase BEFORE updating
        tasks_rows = conn.execute(
            text("SELECT id FROM tasks WHERE phase_id = :phase_id"),
            {"phase_id": phase_id},
        ).fetchall()
        
        task_ids = [t.id for t in tasks_rows]
        reassigned_task_count = len(task_ids)

        affected_attachments = []
        affected_dependencies = []

        if task_ids:
            placeholders = ", ".join([f":tid_{i}" for i in range(len(task_ids))])
            params = {f"tid_{i}": tid for i, tid in enumerate(task_ids)}

            # Fetch attachments for these tasks
            att_rows = conn.execute(
                text(
                    f"SELECT id, task_id, file_name FROM attachments WHERE task_id IN ({placeholders})"
                ),
                params,
            ).fetchall()
            
            for r in att_rows:
                affected_attachments.append(
                    AttachmentResponse(
                        attachment_id=r.id,
                        task_id=r.task_id,
                        file_name=r.file_name,
                    )
                )

            # Fetch dependencies where either side involves these tasks
            # The spec says "reassign all associated tasks... including those with ... pending dependencies"
            # We need to report dependencies that are affected by the move. 
            # Since dependencies link tasks, and we are moving tasks between phases (conceptually renaming the phase),
            # the dependencies themselves don't change their IDs or links, but they are "associated" with the moved tasks.
            # Let's fetch dependencies involving any of the task_ids.
            dep_rows = conn.execute(
                text(
                    f"""
                    SELECT id, task_id, depends_on_task_id 
                    FROM dependencies 
                    WHERE task_id IN ({placeholders}) OR depends_on_task_id IN ({placeholders})
                    """
                ),
                params,
            ).fetchall()

            seen_dep_ids = set()
            for r in dep_rows:
                if r.id not in seen_dep_ids:
                    seen_dep_ids.add(r.id)
                    affected_dependencies.append(
                        DependencyResponse(
                            dependency_id=r.id,
                            task_id=r.task_id,
                            depends_on_task_id=r.depends_on_task_id,
                        )
                    )

        # Perform the rename
        conn.execute(
            text("UPDATE phases SET name = :new_name, updated_at = CURRENT_TIMESTAMP WHERE id = :phase_id"),
            {"new_name": new_name, "phase_id": phase_id},
        )

        # Note: Tasks reference phase_id, not phase name. So strictly speaking, no UPDATE on tasks is needed 
        # unless there was a denormalized column. Looking at schema: tasks has phase_id FK. 
        # Renaming the phase record automatically updates the logical association for all tasks pointing to it.
        # However, the prompt implies "reassign... to the updated phase name". In normalized SQL, changing the parent name 
        # doesn't require touching children if children only store FK ID. 
        # But wait, does the system rely on string matching? No, standard SQL uses IDs.
        # The "reassignment" is implicit via the FK relationship remaining intact while the referenced entity changes its label.
        
        return PhaseUpdateResponse(
            message=f"Phase renamed from '{old_name}' to '{new_name}'.",
            updated_phase_id=phase_id,
            reassigned_task_count=reassigned_task_count,
            affected_attachments=affected_attachments,
            affected_dependencies=affected_dependencies,
        )


@app.get(
    "/projects/{project_id}/phases/{phase_id}/tasks",
    response_model=List[TaskDetailResponse],
    operation_id="list_phase_tasks_with_details",
)
def list_phase_tasks_with_details(project_id: int, phase_id: int):
    with engine.connect() as conn:
        # Verify phase exists and belongs to project
        phase_row = conn.execute(
            text("SELECT id FROM phases WHERE id = :pid AND project_id = :proj_id"),
            {"pid": phase_id, "proj_id": project_id},
        ).fetchone()

        if not phase_row:
            raise HTTPException(status_code=404, detail="Phase not found in project")

        # Get all tasks for this phase
        tasks_rows = conn.execute(
            text("""
                SELECT id, name, status, start_date, due_date 
                FROM tasks 
                WHERE phase_id = :phase_id
            """),
            {"phase_id": phase_id},
        ).fetchall()

        result = []
        for t in tasks_rows:
            # Get attachments for this task
            atts = conn.execute(
                text("""
                    SELECT id, file_name, file_path 
                    FROM attachments 
                    WHERE task_id = :task_id
                """),
                {"task_id": t.id},
            ).fetchall()
            
            attachment_list = [
                TaskAttachmentItem(
                    attachment_id=a.id,
                    file_name=a.file_name,
                    file_path=a.file_path,
                )
                for a in atts
            ]

            # Get dependencies for this task (where this task is the dependent one)
            deps = conn.execute(
                text("""
                    SELECT id, depends_on_task_id, dependency_type, status 
                    FROM dependencies 
                    WHERE task_id = :task_id
                """),
                {"task_id": t.id},
            ).fetchall()

            dependency_list = [
                TaskDependencyItem(
                    dependency_id=d.id,
                    depends_on_task_id=d.depends_on_task_id,
                    dependency_type=d.dependency_type,
                    status=d.status,
                )
                for d in deps
            ]

            result.append(
                TaskDetailResponse(
                    task_id=t.id,
                    task_name=t.name,
                    status=t.status,
                    start_date=str(t.start_date) if t.start_date else "",
                    due_date=str(t.due_date) if t.due_date else "",
                    attachments=attachment_list,
                    dependencies=dependency_list,
                )
            )

        return result
