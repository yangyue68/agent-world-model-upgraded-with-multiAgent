import os
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()


class VoteRequest(BaseModel):
    post_id: int
    mrr: float


class CommentRequest(BaseModel):
    post_id: int
    content: str


@app.get("/posts", operation_id="find_canny_post")
def find_canny_post(
    title: str = Query(..., description="Title of the post to find"),
    board_name: str = Query(..., description="Name of the board where the post resides"),
):
    db = SessionLocal()
    try:
        # Join posts with boards to filter by board name and post title
        query = text("""
            SELECT p.id, p.title, p.board_id
            FROM posts p
            JOIN boards b ON p.board_id = b.id
            WHERE p.title = :title AND b.name = :board_name
        """)
        result = db.execute(query, {"title": title, "board_name": board_name})
        rows = result.fetchall()
        
        response_data = []
        for row in rows:
            response_data.append({
                "id": row[0],
                "title": row[1],
                "board_id": row[2]
            })
            
        return response_data
    finally:
        db.close()


@app.post("/votes", operation_id="add_vote_to_post")
def add_vote_to_post(payload: VoteRequest):
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        user_id = int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID format")

    db = SessionLocal()
    try:
        # Check if post exists
        check_post_query = text("SELECT id FROM posts WHERE id = :post_id")
        post_exists = db.execute(check_post_query, {"post_id": payload.post_id}).fetchone()
        if not post_exists:
            raise HTTPException(status_code=404, detail="Post not found")

        # Insert vote
        insert_vote_query = text("""
            INSERT INTO votes (post_id, user_id, mrr)
            VALUES (:post_id, :user_id, :mrr)
        """)
        db.execute(insert_vote_query, {
            "post_id": payload.post_id,
            "user_id": user_id,
            "mrr": payload.mrr
        })
        db.commit()

        # Retrieve the inserted vote to get its ID
        select_vote_query = text("""
            SELECT id, post_id, user_id, mrr
            FROM votes
            WHERE post_id = :post_id AND user_id = :user_id
        """)
        vote_row = db.execute(select_vote_query, {
            "post_id": payload.post_id,
            "user_id": user_id
        }).fetchone()

        if not vote_row:
            raise HTTPException(status_code=500, detail="Failed to retrieve vote after insertion")

        return {
            "id": vote_row[0],
            "post_id": vote_row[1],
            "user_id": vote_row[2],
            "mrr": vote_row[3]
        }
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


@app.post("/comments", operation_id="add_comment_to_post")
def add_comment_to_post(payload: CommentRequest):
    user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        user_id = int(user_id_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID format")

    db = SessionLocal()
    try:
        # Check if post exists
        check_post_query = text("SELECT id FROM posts WHERE id = :post_id")
        post_exists = db.execute(check_post_query, {"post_id": payload.post_id}).fetchone()
        if not post_exists:
            raise HTTPException(status_code=404, detail="Post not found")

        # Insert comment (is_public defaults to 1 in schema, but we can explicitly set it or rely on default)
        # The spec says "public comment", so is_public should be 1.
        insert_comment_query = text("""
            INSERT INTO comments (post_id, user_id, content, is_public)
            VALUES (:post_id, :user_id, :content, 1)
        """)
        db.execute(insert_comment_query, {
            "post_id": payload.post_id,
            "user_id": user_id,
            "content": payload.content
        })
        db.commit()

        # Retrieve the inserted comment to get its ID
        select_comment_query = text("""
            SELECT id, post_id, user_id, content, is_public
            FROM comments
            WHERE post_id = :post_id AND user_id = :user_id AND content = :content
            ORDER BY id DESC LIMIT 1
        """)
        comment_row = db.execute(select_comment_query, {
            "post_id": payload.post_id,
            "user_id": user_id,
            "content": payload.content
        }).fetchone()

        if not comment_row:
            raise HTTPException(status_code=500, detail="Failed to retrieve comment after insertion")

        return {
            "id": comment_row[0],
            "post_id": comment_row[1],
            "user_id": comment_row[2],
            "content": comment_row[3],
            "is_public": comment_row[4]
        }
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()
