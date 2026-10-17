import os
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set.")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


class UpdatePostStatusRequest(BaseModel):
    status: str


class AddCommentRequest(BaseModel):
    body: str


@app.put("/posts/{post_id}", operation_id="update_post_status")
def update_post_status(post_id: int, payload: UpdatePostStatusRequest):
    """
    Close a post by setting its status to 'closed'.
    """
    if payload.status != "closed":
        raise HTTPException(status_code=400, detail="Status must be 'closed'")

    db = SessionLocal()
    try:
        # Check if post exists and get current data for response
        result = db.execute(
            text("SELECT id, title, status FROM posts WHERE id = :id"),
            {"id": post_id}
        ).fetchone()

        if not result:
            raise HTTPException(status_code=404, detail="Post not found")

        # Update the status
        db.execute(
            text("UPDATE posts SET status = :status, updated_at = CURRENT_TIMESTAMP WHERE id = :id"),
            {"status": "closed", "id": post_id}
        )
        db.commit()

        return {
            "id": result.id,
            "title": result.title,
            "status": "closed"
        }
    finally:
        db.close()


@app.post("/posts/{post_id}/comments", operation_id="add_internal_comment")
def add_internal_comment(post_id: int, payload: AddCommentRequest):
    """
    Add an internal comment to a specified post.
    """
    # Get the current user ID from the environment
    author_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not author_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        author_id = int(author_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format")

    db = SessionLocal()
    try:
        # Verify post exists
        post_exists = db.execute(
            text("SELECT 1 FROM posts WHERE id = :id"),
            {"id": post_id}
        ).fetchone()

        if not post_exists:
            raise HTTPException(status_code=404, detail="Post not found")

        # Insert the new comment with is_internal = 1
        db.execute(
            text("""
                INSERT INTO comments (post_id, author_id, body, is_internal, created_at, updated_at)
                VALUES (:post_id, :author_id, :body, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """),
            {
                "post_id": post_id,
                "author_id": author_id,
                "body": payload.body
            }
        )
        db.commit()

        # Fetch the newly inserted comment to return full details including generated ID
        new_comment = db.execute(
            text("""
                SELECT id, post_id, author_id, body, is_internal 
                FROM comments 
                ORDER BY id DESC LIMIT 1
            """)
        ).fetchone()

        return {
            "id": new_comment.id,
            "post_id": new_comment.post_id,
            "author_id": new_comment.author_id,
            "body": new_comment.body,
            "is_internal": new_comment.is_internal
        }
    finally:
        db.close()
