import os
from datetime import date

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH") or os.environ.get("DB_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class UserOut(BaseModel):
    id: int
    username: str
    full_name: str | None = None
    email: str | None = None


class ClauseTemplateCreateIn(BaseModel):
    title: str
    jurisdiction: str
    risk_level: str
    author_id: int
    body: str


class ClauseTemplateOut(BaseModel):
    id: int
    title: str
    jurisdiction: str
    risk_level: str
    author_id: int
    last_reviewed_date: str
    version: str


@app.get("/users/by_username/{username}", response_model=UserOut, operation_id="get_user_by_username")
def get_user_by_username(username: str):
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT id, username, full_name, email FROM users WHERE username = :username"),
            {"username": username},
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="User not found")
    return UserOut(
        id=row.id,
        username=row.username,
        full_name=row.full_name,
        email=row.email,
    )


@app.post("/clause_templates", response_model=ClauseTemplateOut, operation_id="create_clause_template")
def create_clause_template(payload: ClauseTemplateCreateIn):
    current_user_id_raw = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_raw:
        raise HTTPException(status_code=401, detail="Missing authenticated user context")
    try:
        current_user_id = int(current_user_id_raw)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid authenticated user context")

    today = date.today().isoformat()
    version = "1.0"

    with engine.begin() as conn:
        # Validate author exists
        author_row = conn.execute(
            text("SELECT id FROM users WHERE id = :author_id"),
            {"author_id": payload.author_id},
        ).fetchone()
        if author_row is None:
            raise HTTPException(status_code=404, detail="Author not found")

        result = conn.execute(
            text(
                """
                INSERT INTO clause_templates (title, jurisdiction, risk_level, author_id, body, last_reviewed_date)
                VALUES (:title, :jurisdiction, :risk_level, :author_id, :body, :last_reviewed_date)
                """
            ),
            {
                "title": payload.title,
                "jurisdiction": payload.jurisdiction,
                "risk_level": payload.risk_level,
                "author_id": payload.author_id,
                "body": payload.body,
                "last_reviewed_date": today,
            },
        )
        template_id = result.lastrowid

        conn.execute(
            text(
                """
                INSERT INTO clause_template_versions (clause_template_id, version, body, created_by)
                VALUES (:clause_template_id, :version, :body, :created_by)
                """
            ),
            {
                "clause_template_id": template_id,
                "version": version,
                "body": payload.body,
                "created_by": current_user_id,
            },
        )

    return ClauseTemplateOut(
        id=template_id,
        title=payload.title,
        jurisdiction=payload.jurisdiction,
        risk_level=payload.risk_level,
        author_id=payload.author_id,
        last_reviewed_date=today,
        version=version,
    )
