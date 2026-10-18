import os
from typing import Optional

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


class InsertClauseRequest(BaseModel):
    clause_version_id: str
    section_anchor: str


class DraftClauseResponse(BaseModel):
    id: int
    draft_id: str
    clause_version_id: str
    section_anchor: str
    position: int
    inserted_by: int


@app.post("/drafts/{draft_id}/clauses", response_model=DraftClauseResponse, operation_id="insert_clause_into_draft")
def insert_clause_into_draft(
    draft_id: str = Path(...),
    body: InsertClauseRequest = ...,
):
    db = SessionLocal()
    try:
        # 1. Verify the draft exists
        draft_exists = db.execute(
            text("SELECT 1 FROM drafts WHERE id = :draft_id"),
            {"draft_id": draft_id}
        ).fetchone()
        
        if not draft_exists:
            raise HTTPException(status_code=404, detail="Draft not found")

        # 2. Verify the clause version exists and is reviewed
        clause_version_row = db.execute(
            text("""
                SELECT cv.id 
                FROM clause_versions cv
                JOIN clauses c ON cv.clause_id = c.id
                WHERE cv.id = :clause_version_id AND cv.reviewed_at IS NOT NULL
            """),
            {"clause_version_id": body.clause_version_id}
        ).fetchone()

        if not clause_version_row:
            raise HTTPException(status_code=400, detail="Clause version not found or not reviewed")

        # 3. Determine new position for "beginning" of the section
        # We need to insert at the start, which implies shifting existing items down.
        # Find the minimum current position for this draft and anchor.
        min_pos_result = db.execute(
            text("""
                SELECT MIN(position) 
                FROM draft_clauses 
                WHERE draft_id = :draft_id AND section_anchor = :section_anchor
            """),
            {"draft_id": draft_id, "section_anchor": body.section_anchor}
        ).scalar()

        if min_pos_result is None:
            # No existing clauses in this section/anchor, so position starts at 1
            new_position = 1
        else:
            # There are existing clauses. To put the new one at the beginning,
            # we shift all existing positions up by 1 starting from the max downwards?
            # Or simply increment all >= min_pos?
            # The safest way to avoid unique constraint violations during update is to 
            # shift them in descending order if moving up, or ascending if moving down.
            # Here we want to make room at `min_pos_result`. So everything currently at 
            # `min_pos_result` and above needs to move +1.
            
            # Get all affected rows ordered by position DESC to avoid collision when updating
            affected_rows = db.execute(
                text("""
                    SELECT id, position 
                    FROM draft_clauses 
                    WHERE draft_id = :draft_id AND section_anchor = :section_anchor
                    ORDER BY position DESC
                """),
                {"draft_id": draft_id, "section_anchor": body.section_anchor}
            ).fetchall()

            for row in affected_rows:
                db.execute(
                    text("""
                        UPDATE draft_clauses 
                        SET position = :new_pos 
                        WHERE id = :row_id
                    """),
                    {"new_pos": row.position + 1, "row_id": row.id}
                )
            
            new_position = min_pos_result

        # 4. Get the current user ID from environment
        actor_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
        if not actor_user_id_str:
             raise HTTPException(status_code=401, detail="User not authenticated")
        
        try:
            actor_user_id = int(actor_user_id_str)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid user ID format")

        # Verify user exists
        user_exists = db.execute(
            text("SELECT 1 FROM users WHERE id = :user_id"),
            {"user_id": actor_user_id}
        ).fetchone()
        
        if not user_exists:
            raise HTTPException(status_code=400, detail="Authenticated user does not exist")

        # 5. Insert the new draft clause
        db.execute(
            text("""
                INSERT INTO draft_clauses (draft_id, clause_version_id, section_anchor, position, inserted_by)
                VALUES (:draft_id, :clause_version_id, :section_anchor, :position, :inserted_by)
            """),
            {
                "draft_id": draft_id,
                "clause_version_id": body.clause_version_id,
                "section_anchor": body.section_anchor,
                "position": new_position,
                "inserted_by": actor_user_id
            }
        )
        db.commit()

        # 6. Fetch the inserted row to return full details including generated ID
        inserted_row = db.execute(
            text("""
                SELECT id, draft_id, clause_version_id, section_anchor, position, inserted_by
                FROM draft_clauses
                WHERE draft_id = :draft_id AND clause_version_id = :clause_version_id AND section_anchor = :section_anchor AND position = :position
            """),
            {
                "draft_id": draft_id,
                "clause_version_id": body.clause_version_id,
                "section_anchor": body.section_anchor,
                "position": new_position
            }
        ).mappings().fetchone()

        if not inserted_row:
            raise HTTPException(status_code=500, detail="Failed to retrieve inserted record")

        return DraftClauseResponse(**dict(inserted_row))

    finally:
        db.close()
