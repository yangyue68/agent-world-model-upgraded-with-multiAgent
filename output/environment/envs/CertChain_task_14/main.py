import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text, Column, Integer, String, Float, Date, DateTime, ForeignKey, UniqueConstraint, CheckConstraint
from sqlalchemy.orm import sessionmaker, declarative_base
DATABASE_PATH = os.environ.get("DATABASE_PATH")
engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

# --- Pydantic Models ---

class CreditItem(BaseModel):
    course_title: str
    provider_name: str
    category_name: str
    credits: float
    completion_date: str  # ISO-8601 date string

class BatchImportRequest(BaseModel):
    user_id: int
    credits_batch: List[CreditItem]

class FailedRecord(BaseModel):
    index: int
    error: str

class BatchImportResponse(BaseModel):
    imported_count: int
    failed_records: List[FailedRecord]

# --- SQLAlchemy Models (for reference/metadata if needed, but we use raw SQL mostly for control) ---
# Note: We don't need to define full ORM models if we use raw SQL with parameterized queries,
# but defining them helps with type hinting or if we switch. However, the prompt says "Use existing tables and parameterized SQL".
# Let's stick to raw SQL for the logic to ensure we handle the specific constraints and lookups correctly.

app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/ceu_credits/batch_import", response_model=BatchImportResponse, operation_id="batch_import_ceu_credits")
def batch_import_ceu_credits(request: BatchImportRequest):
    db = SessionLocal()
    try:
        imported_count = 0
        failed_records = []

        # Pre-fetch providers and categories to avoid repeated queries
        # Providers
        providers_query = text("SELECT id, name FROM providers")
        providers_rows = db.execute(providers_query).fetchall()
        provider_map = {row[1]: row[0] for row in providers_rows}

        # Categories
        categories_query = text("SELECT id, name FROM categories")
        categories_rows = db.execute(categories_query).fetchall()
        category_map = {row[1]: row[0] for row in categories_rows}

        # Check if user exists
        user_check = text("SELECT id FROM users WHERE id = :user_id")
        user_result = db.execute(user_check, {"user_id": request.user_id}).fetchone()
        if not user_result:
            raise HTTPException(status_code=404, detail=f"User with ID {request.user_id} not found")

        for idx, item in enumerate(request.credits_batch):
            try:
                # Resolve Provider ID
                provider_id = provider_map.get(item.provider_name)
                if provider_id is None:
                    raise ValueError(f"Provider '{item.provider_name}' not found")

                # Resolve Category ID
                category_id = category_map.get(item.category_name)
                if category_id is None:
                    raise ValueError(f"Category '{item.category_name}' not found")

                # Upsert Course: Get ID if exists, else Insert
                # UNIQUE (title, provider_id)
                course_lookup = text("""
                    SELECT id FROM courses 
                    WHERE title = :title AND provider_id = :provider_id
                """)
                course_row = db.execute(course_lookup, {
                    "title": item.course_title,
                    "provider_id": provider_id
                }).fetchone()

                if course_row:
                    course_id = course_row[0]
                else:
                    # Insert new course
                    insert_course_sql = text("""
                        INSERT INTO courses (title, provider_id, category_id, created_at, updated_at)
                        VALUES (:title, :provider_id, :category_id, datetime('now'), datetime('now'))
                    """)
                    db.execute(insert_course_sql, {
                        "title": item.course_title,
                        "provider_id": provider_id,
                        "category_id": category_id
                    })
                    # Select again after insert to get the new ID
                    course_row = db.execute(course_lookup, {
                        "title": item.course_title,
                        "provider_id": provider_id
                    }).fetchone()
                    if not course_row:
                         raise RuntimeError("Failed to retrieve newly inserted course ID")
                    course_id = course_row[0]

                # Upsert CEU Credit: UNIQUE (user_id, course_id, completion_date)
                # Check if already exists
                credit_check = text("""
                    SELECT id FROM ceu_credits 
                    WHERE user_id = :user_id AND course_id = :course_id AND completion_date = :completion_date
                """)
                existing_credit = db.execute(credit_check, {
                    "user_id": request.user_id,
                    "course_id": course_id,
                    "completion_date": item.completion_date
                }).fetchone()

                if existing_credit:
                    # Already exists, skip or update? Spec says "Imports... ensures course uniqueness".
                    # Usually implies idempotency. If it exists, do nothing or update credits?
                    # The spec doesn't specify update behavior for duplicates. 
                    # Given "failed_records" structure, maybe duplicate is considered a failure?
                    # Or maybe it's just skipped. Let's assume skipping is fine, but since we need to count imported,
                    # let's treat duplicate as a failure to be explicit about state changes.
                    raise ValueError(f"CEU credit for user {request.user_id}, course '{item.course_title}', date {item.completion_date} already exists")

                # Insert new credit
                insert_credit_sql = text("""
                    INSERT INTO ceu_credits (user_id, course_id, credits, completion_date, source, created_at, updated_at)
                    VALUES (:user_id, :course_id, :credits, :completion_date, :source, datetime('now'), datetime('now'))
                """)
                db.execute(insert_credit_sql, {
                    "user_id": request.user_id,
                    "course_id": course_id,
                    "credits": item.credits,
                    "completion_date": item.completion_date,
                    "source": "TechEd LMS"
                })
                
                imported_count += 1
                
            except Exception as e:
                failed_records.append(FailedRecord(index=idx, error=str(e)))

        # Commit only once after all records are processed successfully
        db.commit()
        
        return BatchImportResponse(
            imported_count=imported_count,
            failed_records=failed_records
        )

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
