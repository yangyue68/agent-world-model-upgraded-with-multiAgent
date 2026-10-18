import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class ClauseInsertItem(BaseModel):
    clause_title: str
    insert_after: Optional[int] = None


class InsertedClauseResponse(BaseModel):
    clause_id: int
    usage_event_id: int
    position: int


class InsertClausesResponse(BaseModel):
    contract_id: int
    inserted_clauses: List[InsertedClauseResponse]


@app.post(
    "/contracts/{contract_ref}/clauses",
    response_model=InsertClausesResponse,
    operation_id="insert_clauses_into_contract",
)
def insert_clauses_into_contract(
    contract_ref: str = Path(..., description="Unique reference identifier of the draft contract"),
    clauses: List[ClauseInsertItem] = ...,
):
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="Missing current user ID")
    try:
        user_id = int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid current user ID")

    with engine.connect() as conn:
        # Verify contract exists and is in draft status
        result = conn.execute(
            text("SELECT id, status FROM contracts WHERE contract_ref = :ref"),
            {"ref": contract_ref},
        ).fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="Contract not found")
        contract_id, status = result
        if status != "draft":
            raise HTTPException(status_code=400, detail="Contract must be in draft status")

        # Get current max position for this contract
        max_pos_result = conn.execute(
            text("SELECT COALESCE(MAX(position), 0) FROM contract_clauses WHERE contract_id = :cid"),
            {"cid": contract_id},
        ).fetchone()
        current_max_position = max_pos_result[0] if max_pos_result else 0

        inserted_clauses = []
        last_usage_event_id = None

        for item in clauses:
            # Find clause by title
            clause_result = conn.execute(
                text("SELECT id FROM clauses WHERE title = :title"),
                {"title": item.clause_title},
            ).fetchone()
            if not clause_result:
                raise HTTPException(status_code=404, detail=f"Clause '{item.clause_title}' not found")
            clause_id = clause_result[0]

            # Determine insert_after value
            insert_after_val = item.insert_after
            if insert_after_val is None and last_usage_event_id is not None:
                # If no explicit insert_after provided but we have a previous usage event, use it
                insert_after_val = last_usage_event_id

            # Calculate next position
            new_position = current_max_position + len(inserted_clauses) + 1

            # Insert into contract_clauses
            cc_result = conn.execute(
                text("""
                    INSERT INTO contract_clauses (contract_id, clause_id, position, insert_after_usage_event_id)
                    VALUES (:cid, :clid, :pos, :iaueid)
                """),
                {
                    "cid": contract_id,
                    "clid": clause_id,
                    "pos": new_position,
                    "iaueid": insert_after_val,
                },
            )
            contract_clause_id = cc_result.lastrowid

            # Insert into usage_events
            ue_result = conn.execute(
                text("""
                    INSERT INTO usage_events (user_id, contract_id, clause_id, event_type, metadata)
                    VALUES (:uid, :cid, :clid, 'insert_clause', :meta)
                """),
                {
                    "uid": user_id,
                    "cid": contract_id,
                    "clid": clause_id,
                    "meta": f'{{"position": {new_position}, "insert_after": {insert_after_val}}}' if insert_after_val is not None else f'{{"position": {new_position}, "source": "library"}}',
                },
            )
            usage_event_id = ue_result.lastrowid

            inserted_clauses.append(
                InsertedClauseResponse(
                    clause_id=clause_id,
                    usage_event_id=usage_event_id,
                    position=new_position,
                )
            )
            last_usage_event_id = usage_event_id

        conn.commit()

        return InsertClausesResponse(
            contract_id=contract_id,
            inserted_clauses=inserted_clauses,
        )
