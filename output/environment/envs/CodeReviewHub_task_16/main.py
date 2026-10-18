import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class ClaimResponse(BaseModel):
    id: int
    status: str
    claimed_by: int
    claimed_at: str


class LineCommentRequest(BaseModel):
    line_number: int = Field(..., ge=1)
    comment: str
    severity: str = Field(..., pattern="^(Minor|Major|Critical)$")


class LineCommentResponse(BaseModel):
    id: int
    line_number: int
    comment: str
    severity: str


class RubricScoreRequest(BaseModel):
    criterion_id: int
    score: int = Field(..., ge=0)


class RubricScoreResponse(BaseModel):
    id: int
    criterion_id: int
    score: int


def get_current_user_id() -> int:
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="Current user not authenticated")
    try:
        return int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid current user ID")


@app.put("/submissions/{submission_id}/claim", response_model=ClaimResponse, operation_id="claim_submission")
def claim_submission(submission_id: int = Path(...)):
    current_user_id = get_current_user_id()
    
    with engine.begin() as conn:
        # Check if submission exists and is pending
        result = conn.execute(
            text("""
                SELECT id, status FROM submissions WHERE id = :sid
            """),
            {"sid": submission_id}
        ).fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Submission not found")
        
        sub_id, status = result
        
        if status != 'pending':
            raise HTTPException(status_code=409, detail=f"Submission cannot be claimed. Current status: {status}")
        
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        
        conn.execute(
            text("""
                UPDATE submissions 
                SET status = 'claimed', claimed_by = :uid, claimed_at = :now, updated_at = :now
                WHERE id = :sid AND status = 'pending'
            """),
            {"uid": current_user_id, "now": now, "sid": submission_id}
        )
        
        # Verify update happened (race condition check)
        verify_result = conn.execute(
            text("""
                SELECT id, status, claimed_by, claimed_at FROM submissions WHERE id = :sid
            """),
            {"sid": submission_id}
        ).fetchone()
        
        if not verify_result or verify_result[2] != current_user_id:
             raise HTTPException(status_code=409, detail="Failed to claim submission due to concurrent modification")
             
        final_id, final_status, final_claimed_by, final_claimed_at = verify_result
        
        return ClaimResponse(
            id=final_id,
            status=final_status,
            claimed_by=final_claimed_by,
            claimed_at=str(final_claimed_at)
        )


@app.post("/submissions/{submission_id}/comments", response_model=LineCommentResponse, operation_id="add_line_comment")
def add_line_comment(request: LineCommentRequest, submission_id: int = Path(...)):
    current_user_id = get_current_user_id()
    
    with engine.begin() as conn:
        # Check if submission exists and is claimed by current user
        result = conn.execute(
            text("""
                SELECT id, status, claimed_by FROM submissions WHERE id = :sid
            """),
            {"sid": submission_id}
        ).fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Submission not found")
            
        sub_id, status, claimed_by = result
        
        if status != 'claimed':
            raise HTTPException(status_code=403, detail="Submission must be claimed before adding comments")
            
        if claimed_by != current_user_id:
            raise HTTPException(status_code=403, detail="Only the reviewer who claimed the submission can add comments")
            
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        
        res_insert = conn.execute(
            text("""
                INSERT INTO line_comments (submission_id, reviewer_id, line_number, comment, severity, created_at, updated_at)
                VALUES (:sid, :rid, :ln, :cmt, :sev, :now, :now)
            """),
            {
                "sid": submission_id,
                "rid": current_user_id,
                "ln": request.line_number,
                "cmt": request.comment,
                "sev": request.severity,
                "now": now
            }
        )
        
        new_id = res_insert.lastrowid
        
        return LineCommentResponse(
            id=new_id,
            line_number=request.line_number,
            comment=request.comment,
            severity=request.severity
        )


@app.post("/submissions/{submission_id}/scores", response_model=RubricScoreResponse, operation_id="submit_rubric_score")
def submit_rubric_score(request: RubricScoreRequest, submission_id: int = Path(...)):
    current_user_id = get_current_user_id()
    
    with engine.begin() as conn:
        # Check if submission exists and is claimed by current user
        result = conn.execute(
            text("""
                SELECT id, status, claimed_by FROM submissions WHERE id = :sid
            """),
            {"sid": submission_id}
        ).fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Submission not found")
            
        sub_id, status, claimed_by = result
        
        if status != 'claimed':
            raise HTTPException(status_code=403, detail="Submission must be claimed before scoring")
            
        if claimed_by != current_user_id:
            raise HTTPException(status_code=403, detail="Only the reviewer who claimed the submission can score it")
            
        # Validate criterion belongs to the assignment of this submission
        crit_check = conn.execute(
            text("""
                SELECT rc.id 
                FROM rubric_criteria rc
                JOIN submissions s ON s.assignment_id = rc.assignment_id
                WHERE rc.id = :cid AND s.id = :sid
            """),
            {"cid": request.criterion_id, "sid": submission_id}
        ).fetchone()
        
        if not crit_check:
            raise HTTPException(status_code=404, detail="Rubric criterion not found for this submission's assignment")
            
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        
        # Insert or Update based on unique constraint (submission_id, criterion_id, reviewer_id)
        existing = conn.execute(
            text("""
                SELECT id FROM rubric_scores 
                WHERE submission_id = :sid AND criterion_id = :cid AND reviewer_id = :rid
            """),
            {"sid": submission_id, "cid": request.criterion_id, "rid": current_user_id}
        ).fetchone()
        
        if existing:
            score_id = existing[0]
            conn.execute(
                text("""
                    UPDATE rubric_scores 
                    SET score = :sc, updated_at = :now
                    WHERE id = :id
                """),
                {"sc": request.score, "now": now, "id": score_id}
            )
        else:
            res_insert = conn.execute(
                text("""
                    INSERT INTO rubric_scores (submission_id, criterion_id, reviewer_id, score, created_at, updated_at)
                    VALUES (:sid, :cid, :rid, :sc, :now, :now)
                """),
                {
                    "sid": submission_id,
                    "cid": request.criterion_id,
                    "rid": current_user_id,
                    "sc": request.score,
                    "now": now
                }
            )
            score_id = res_insert.lastrowid
            
        return RubricScoreResponse(
            id=score_id,
            criterion_id=request.criterion_id,
            score=request.score
        )
