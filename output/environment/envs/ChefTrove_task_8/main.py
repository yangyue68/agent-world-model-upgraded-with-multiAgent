import os
from typing import List

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    ForeignKey,
    create_engine,
    select,
    insert,
    MetaData,
)
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

app = FastAPI(title="ChefTrove API", version="1.0.0")


# --- Pydantic Models ---

class PrepTaskCreate(BaseModel):
    description: str


class AssignPrepTasksRequest(BaseModel):
    tasks: List[PrepTaskCreate]


class LogDeviationRequest(BaseModel):
    description: str


class PrepTaskResponse(BaseModel):
    id: int
    description: str
    status: str

    class Config:
        from_attributes = True


class DeviationResponse(BaseModel):
    id: int
    prep_task_id: int
    description: str

    class Config:
        from_attributes = True


# --- Endpoints ---

@app.post(
    "/shift_assignments/{shift_assignment_id}/prep_tasks",
    operation_id="assign_prep_tasks",
    summary="Assign prep tasks to a shift assignment",
    response_model=List[PrepTaskResponse],
    tags=["PrimaryResource"],
)
def assign_prep_tasks(
    shift_assignment_id: int = Path(..., ge=1),
    body: AssignPrepTasksRequest = ...,
):
    """
    Assign multiple prep tasks to a specific shift assignment.
    """
    db = SessionLocal()
    try:
        # Verify shift assignment exists
        sa_check = db.execute(
            select(shift_assignments.c.id).where(
                shift_assignments.c.id == shift_assignment_id
            )
        ).fetchone()
        if not sa_check:
            raise HTTPException(status_code=404, detail="Shift assignment not found")

        created_tasks = []
        for task_item in body.tasks:
            stmt = (
                insert(prep_tasks)
                .values(
                    shift_assignment_id=shift_assignment_id,
                    description=task_item.description,
                    status="assigned",
                )
                .returning(
                    prep_tasks.c.id,
                    prep_tasks.c.description,
                    prep_tasks.c.status,
                )
            )
            result = db.execute(stmt)
            row = result.fetchone()
            if row:
                created_tasks.append(
                    PrepTaskResponse(
                        id=row[0],
                        description=row[1],
                        status=row[2],
                    )
                )
        
        db.commit()
        return created_tasks

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.post(
    "/prep_tasks/{prep_task_id}/deviations",
    operation_id="log_deviation",
    summary="Log a deviation for a prep task",
    response_model=DeviationResponse,
    tags=["PrimaryResource"],
)
def log_deviation(
    prep_task_id: int = Path(..., ge=1),
    body: LogDeviationRequest = ...,
):
    """
    Record a deviation for a given prep task with a description.
    The logged_by_user_id is inferred from the authenticated user.
    """
    db = SessionLocal()
    try:
        # Verify prep task exists
        pt_check = db.execute(
            select(prep_tasks.c.id).where(prep_tasks.c.id == prep_task_id)
        ).fetchone()
        if not pt_check:
            raise HTTPException(status_code=404, detail="Prep task not found")

        # Get current user ID from environment
        logged_by_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
        if not logged_by_user_id_str:
            raise HTTPException(
                status_code=400, detail="Current user ID not provided"
            )
        
        try:
            logged_by_user_id = int(logged_by_user_id_str)
        except ValueError:
            raise HTTPException(
                status_code=400, detail="Invalid current user ID format"
            )

        stmt = (
            insert(deviations)
            .values(
                prep_task_id=prep_task_id,
                description=body.description,
                logged_by_user_id=logged_by_user_id,
            )
            .returning(
                deviations.c.id,
                deviations.c.prep_task_id,
                deviations.c.description,
            )
        )
        result = db.execute(stmt)
        row = result.fetchone()
        
        if not row:
            raise HTTPException(status_code=500, detail="Failed to log deviation")

        db.commit()
        
        return DeviationResponse(
            id=row[0],
            prep_task_id=row[1],
            description=row[2],
        )

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


# --- SQLAlchemy Table Definitions (for reference/metadata only, schema already exists) ---

class Shifts(Base):
    __tablename__ = "shifts"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    start_time = Column(String, nullable=False)
    end_time = Column(String, nullable=False)

class Locations(Base):
    __tablename__ = "locations"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    address = Column(String)

class Users(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    full_name = Column(String)
    role = Column(String)

class ShiftAssignments(Base):
    __tablename__ = "shift_assignments"
    id = Column(Integer, primary_key=True)
    shift_id = Column(Integer, ForeignKey("shifts.id"), nullable=False)
    location_id = Column(Integer, ForeignKey("locations.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

class PrepTasks(Base):
    __tablename__ = "prep_tasks"
    id = Column(Integer, primary_key=True)
    shift_assignment_id = Column(Integer, ForeignKey("shift_assignments.id"), nullable=False)
    description = Column(Text, nullable=False)
    status = Column(String, nullable=False, default="assigned")

class Deviations(Base):
    __tablename__ = "deviations"
    id = Column(Integer, primary_key=True)
    prep_task_id = Column(Integer, ForeignKey("prep_tasks.id"), nullable=False)
    description = Column(Text, nullable=False)
    logged_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

# Use raw table objects for direct SQL execution to avoid ORM overhead/complexity
metadata = MetaData()
metadata.reflect(bind=engine)
shifts = metadata.tables['shifts']
locations = metadata.tables['locations']
users = metadata.tables['users']
shift_assignments = metadata.tables['shift_assignments']
prep_tasks = metadata.tables['prep_tasks']
deviations = metadata.tables['deviations']
