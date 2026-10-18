import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Path, Body
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI(title="CliniFlow Task & Workflow Hub")


# --- Pydantic Models for Request/Response Bodies ---

class CreateVersionRequest(BaseModel):
    new_version: str = Field(..., description="The new version number (e.g., '2.2')")

class VersionResponse(BaseModel):
    version_id: int
    version_number: str
    status: str

class UpdateStepRequest(BaseModel):
    requires_dual_nurse_verification: bool = Field(..., description="Whether dual nurse verification is required for this step")

class StepUpdateResponse(BaseModel):
    step_id: int
    requires_dual_nurse_verification: bool

class AddBranchRequest(BaseModel):
    step_id: int = Field(..., description="Internal ID of the protocol step to which the branch applies")
    condition_expression: str = Field(..., description="Expression defining when the branch should be taken (e.g., 'patient.age < 18')")
    target_step_id: Optional[int] = Field(None, description="Internal ID of the step to jump to if condition is met")
    branch_label: str = Field(..., description="Label describing the purpose of this branch")

class BranchResponse(BaseModel):
    branch_id: int
    condition_expression: str
    branch_label: str

class PublishResponse(BaseModel):
    version_id: int
    version_number: str
    status: str
    published_at: str


# --- Helper Functions ---

def get_current_user_id() -> int:
    """Reads the simulated logged-in actor from environment."""
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        # Fallback or error depending on strictness. Spec says read at request time.
        # If missing, we might default to a known admin or raise. 
        # Given "never hardcode", but env var is guaranteed by runtime per prompt instructions.
        # We assume it exists. If not, raise 401-like behavior via exception.
        raise HTTPException(status_code=401, detail="User context not found")
    try:
        return int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid User Context")


# --- Endpoints ---

@app.post("/protocol_templates/{template_id}/versions", response_model=VersionResponse, operation_id="create_new_protocol_version")
def create_new_protocol_version(
    template_id: int = Path(..., description="Internal ID of the protocol template"),
    body: CreateVersionRequest = Body(...)
):
    """
    Creates a new draft version of a protocol template, copying all steps and branches 
    from the latest published version. The new version will be marked as 'draft'.
    """
    db = SessionLocal()
    try:
        current_user_id = get_current_user_id()
        
        # 1. Find the latest published version for this template to copy from
        stmt_latest_pub = text("""
            SELECT id FROM protocol_versions 
            WHERE template_id = :tid AND status = 'published' 
            ORDER BY created_at DESC LIMIT 1
        """)
        res_latest = db.execute(stmt_latest_pub, {"tid": template_id})
        latest_published_row = res_latest.fetchone()
        
        source_version_id = None
        if latest_published_row:
            source_version_id = latest_published_row[0]

        # 2. Insert the new version row
        insert_ver_stmt = text("""
            INSERT INTO protocol_versions (template_id, version, status, created_at, updated_at)
            VALUES (:tid, :ver, 'draft', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            RETURNING id
        """)
        res_ins = db.execute(insert_ver_stmt, {
            "tid": template_id,
            "ver": body.new_version
        })
        new_version_id = res_ins.scalar_one()
        
        # 3. Copy Steps from source version if it exists
        copied_steps_map = {} # old_step_id -> new_step_id
        
        if source_version_id:
            select_steps_stmt = text("""
                SELECT id, step_order, title, description, step_type, requires_dual_nurse_verification
                FROM protocol_steps
                WHERE version_id = :sid
                ORDER BY step_order ASC
            """)
            res_steps = db.execute(select_steps_stmt, {"sid": source_version_id})
            steps_rows = res_steps.fetchall()
            
            for row in steps_rows:
                old_step_id, step_order, title, desc, stype, dual_verify = row
                
                ins_step_stmt = text("""
                    INSERT INTO protocol_steps (version_id, step_order, title, description, step_type, requires_dual_nurse_verification, created_at, updated_at)
                    VALUES (:vid, :so, :t, :d, :st, :dv, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    RETURNING id
                """)
                res_ins_step = db.execute(ins_step_stmt, {
                    "vid": new_version_id,
                    "so": step_order,
                    "t": title,
                    "d": desc,
                    "st": stype,
                    "dv": dual_verify
                })
                new_step_id = res_ins_step.scalar_one()
                copied_steps_map[old_step_id] = new_step_id
            
            # 4. Copy Conditional Branches associated with those steps
            if copied_steps_map:
                # Get all branches for the old steps
                placeholders = ",".join([f":k{i}" for i in range(len(copied_steps_map))])
                params = {f"k{i}": k for i, k in enumerate(copied_steps_map.keys())}
                
                select_branches_stmt = text(f"""
                    SELECT id, step_id, condition_expression, target_step_id, branch_label
                    FROM protocol_conditional_branches
                    WHERE step_id IN ({placeholders})
                """)
                res_branches = db.execute(select_branches_stmt, params)
                branch_rows = res_branches.fetchall()
                
                for b_row in branch_rows:
                    _, old_b_step_id, cond_expr, tgt_step_id, label = b_row
                    
                    # Map old step IDs to new ones
                    new_b_step_id = copied_steps_map.get(old_b_step_id)
                    new_tgt_step_id = None
                    if tgt_step_id is not None:
                        new_tgt_step_id = copied_steps_map.get(tgt_step_id)
                    
                    if new_b_step_id:
                        ins_branch_stmt = text("""
                            INSERT INTO protocol_conditional_branches (step_id, condition_expression, target_step_id, branch_label, created_at, updated_at)
                            VALUES (:sid, :ce, :tsid, :bl, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                        """)
                        db.execute(ins_branch_stmt, {
                            "sid": new_b_step_id,
                            "ce": cond_expr,
                            "tsid": new_tgt_step_id,
                            "bl": label
                        })

        db.commit()
        
        return VersionResponse(
            version_id=new_version_id,
            version_number=body.new_version,
            status="draft"
        )
    
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.put("/protocol_versions/{version_id}/steps/{step_id}", response_model=StepUpdateResponse, operation_id="update_protocol_step")
def update_protocol_step(
    version_id: int = Path(..., description="Internal ID of the protocol version"),
    step_id: int = Path(..., description="Internal ID of the protocol step"),
    body: UpdateStepRequest = Body(...)
):
    """
    Modifies properties of a protocol step, including enabling dual nurse verification.
    """
    db = SessionLocal()
    try:
        current_user_id = get_current_user_id()
        
        # Verify step belongs to version
        check_stmt = text("SELECT id FROM protocol_steps WHERE id = :sid AND version_id = :vid")
        res_check = db.execute(check_stmt, {"sid": step_id, "vid": version_id})
        if not res_check.fetchone():
            raise HTTPException(status_code=404, detail="Step not found in specified version")
        
        # Update the step
        dv_int = 1 if body.requires_dual_nurse_verification else 0
        upd_stmt = text("""
            UPDATE protocol_steps 
            SET requires_dual_nurse_verification = :dv, updated_at = CURRENT_TIMESTAMP
            WHERE id = :sid
        """)
        db.execute(upd_stmt, {"dv": dv_int, "sid": step_id})
        
        # Log change
        log_stmt = text("""
            INSERT INTO protocol_version_changes (version_id, change_type, entity_type, entity_id, description, changed_by, created_at)
            VALUES (:vid, 'modified', 'step', :eid, :desc, :cb, CURRENT_TIMESTAMP)
        """)
        db.execute(log_stmt, {
            "vid": version_id,
            "eid": step_id,
            "desc": f"Updated dual nurse verification to {body.requires_dual_nurse_verification}",
            "cb": current_user_id
        })
        
        db.commit()
        
        return StepUpdateResponse(
            step_id=step_id,
            requires_dual_nurse_verification=body.requires_dual_nurse_verification
        )
    
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.post("/protocol_versions/{version_id}/conditional_branches", response_model=BranchResponse, operation_id="add_conditional_branch")
def add_conditional_branch(
    version_id: int = Path(..., description="Internal ID of the protocol version"),
    body: AddBranchRequest = Body(...)
):
    """
    Adds a new conditional branch to a protocol step.
    """
    db = SessionLocal()
    try:
        current_user_id = get_current_user_id()
        
        # Verify step belongs to version
        check_stmt = text("SELECT id FROM protocol_steps WHERE id = :sid AND version_id = :vid")
        res_check = db.execute(check_stmt, {"sid": body.step_id, "vid": version_id})
        if not res_check.fetchone():
            raise HTTPException(status_code=404, detail="Step not found in specified version")
        
        # Validate target_step_id if provided
        if body.target_step_id is not None:
            chk_tgt = text("SELECT id FROM protocol_steps WHERE id = :tid AND version_id = :vid")
            res_tgt = db.execute(chk_tgt, {"tid": body.target_step_id, "vid": version_id})
            if not res_tgt.fetchone():
                raise HTTPException(status_code=400, detail="Target step does not belong to this version")

        # Insert branch
        ins_stmt = text("""
            INSERT INTO protocol_conditional_branches (step_id, condition_expression, target_step_id, branch_label, created_at, updated_at)
            VALUES (:sid, :ce, :tsid, :bl, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            RETURNING id
        """)
        res_ins = db.execute(ins_stmt, {
            "sid": body.step_id,
            "ce": body.condition_expression,
            "tsid": body.target_step_id,
            "bl": body.branch_label
        })
        new_branch_id = res_ins.scalar_one()
        
        # Log change
        log_stmt = text("""
            INSERT INTO protocol_version_changes (version_id, change_type, entity_type, entity_id, description, changed_by, created_at)
            VALUES (:vid, 'added', 'branch', :eid, :desc, :cb, CURRENT_TIMESTAMP)
        """)
        db.execute(log_stmt, {
            "vid": version_id,
            "eid": new_branch_id,
            "desc": f"Added branch '{body.branch_label}' to step {body.step_id}",
            "cb": current_user_id
        })
        
        db.commit()
        
        return BranchResponse(
            branch_id=new_branch_id,
            condition_expression=body.condition_expression,
            branch_label=body.branch_label
        )
    
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.post("/protocol_versions/{version_id}/publish", response_model=PublishResponse, operation_id="publish_protocol_version")
def publish_protocol_version(
    version_id: int = Path(..., description="Internal ID of the protocol version to publish")
):
    """
    Publishes the specified draft version of a protocol template.
    Updates status to 'published', sets published_at, and archives previous published versions.
    """
    db = SessionLocal()
    try:
        current_user_id = get_current_user_id()
        
        # Fetch version details
        ver_stmt = text("SELECT template_id, version, status FROM protocol_versions WHERE id = :vid")
        res_ver = db.execute(ver_stmt, {"vid": version_id})
        ver_row = res_ver.fetchone()
        
        if not ver_row:
            raise HTTPException(status_code=404, detail="Version not found")
        
        template_id, version_num, current_status = ver_row
        
        if current_status != 'draft':
            raise HTTPException(status_code=400, detail=f"Cannot publish version with status '{current_status}'. Must be 'draft'.")
        
        now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        
        # Archive other published versions of the same template
        arch_stmt = text("""
            UPDATE protocol_versions 
            SET status = 'archived', updated_at = CURRENT_TIMESTAMP
            WHERE template_id = :tid AND status = 'published' AND id != :vid
        """)
        db.execute(arch_stmt, {"tid": template_id, "vid": version_id})
        
        # Publish current version
        pub_stmt = text("""
            UPDATE protocol_versions 
            SET status = 'published', published_at = :pa, published_by = :pb, updated_at = CURRENT_TIMESTAMP
            WHERE id = :vid
        """)
        db.execute(pub_stmt, {
            "vid": version_id,
            "pa": now_ts,
            "pb": current_user_id
        })
        
        # Log change
        log_stmt = text("""
            INSERT INTO protocol_version_changes (version_id, change_type, entity_type, entity_id, description, changed_by, created_at)
            VALUES (:vid, 'modified', 'metadata', NULL, :desc, :cb, CURRENT_TIMESTAMP)
        """)
        db.execute(log_stmt, {
            "vid": version_id,
            "desc": f"Published version {version_num}",
            "cb": current_user_id
        })
        
        db.commit()
        
        return PublishResponse(
            version_id=version_id,
            version_number=version_num,
            status="published",
            published_at=now_ts
        )
    
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
