import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text

DATABASE_PATH = os.environ.get("DATABASE_PATH")
if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class PostResponse(BaseModel):
    id: int
    title: str
    description: str
    status: str
    vote_count: int
    created_at: str


class CommentResponse(BaseModel):
    id: int
    body: str
    is_public: bool
    created_at: str


class VoteResponse(BaseModel):
    post_id: int
    user_id: int
    created_at: str


def get_current_user_id() -> int:
    raw = os.environ.get("AWM_CURRENT_USER_ID")
    if not raw:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        return int(raw)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID")


@app.post("/posts", response_model=PostResponse, operation_id="create_feature_request_post")
def create_feature_request_post(
    board_name: str = Query(...),
    category_name: str = Query(...),
    title: str = Query(...),
    description: str = Query(...),
):
    user_id = get_current_user_id()

    with engine.begin() as conn:
        # Verify user exists
        res = conn.execute(text("SELECT id FROM users WHERE id = :uid"), {"uid": user_id})
        if res.fetchone() is None:
            raise HTTPException(status_code=404, detail="User not found")

        # Find board by name
        res = conn.execute(text("SELECT id FROM boards WHERE name = :bname"), {"bname": board_name})
        row = res.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"Board '{board_name}' not found")
        board_id = row[0]

        # Find category within that board
        res = conn.execute(
            text("SELECT id FROM categories WHERE board_id = :bid AND name = :cname"),
            {"bid": board_id, "cname": category_name},
        )
        row = res.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"Category '{category_name}' not found on board '{board_name}'")
        category_id = row[0]

        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        # Insert post
        res = conn.execute(
            text("""
                INSERT INTO posts (board_id, category_id, author_id, title, description, status, vote_count, created_at, updated_at)
                VALUES (:board_id, :category_id, :author_id, :title, :description, 'open', 0, :now, :now)
            """),
            {
                "board_id": board_id,
                "category_id": category_id,
                "author_id": user_id,
                "title": title,
                "description": description,
                "now": now_str,
            },
        )
        post_id = res.lastrowid

        # Fetch the inserted post to return accurate data
        res = conn.execute(
            text("SELECT id, title, description, status, vote_count, created_at FROM posts WHERE id = :pid"),
            {"pid": post_id},
        )
        p = res.fetchone()

    return PostResponse(
        id=p[0],
        title=p[1],
        description=p[2],
        status=p[3],
        vote_count=p[4],
        created_at=str(p[5]),
    )


@app.post("/posts/{post_id}/comments", response_model=CommentResponse, operation_id="add_public_comment")
def add_public_comment(
    post_id: int,
    body: str = Query(...),
):
    user_id = get_current_user_id()

    with engine.begin() as conn:
        # Verify user exists
        res = conn.execute(text("SELECT id FROM users WHERE id = :uid"), {"uid": user_id})
        if res.fetchone() is None:
            raise HTTPException(status_code=404, detail="User not found")

        # Verify post exists
        res = conn.execute(text("SELECT id FROM posts WHERE id = :pid"), {"pid": post_id})
        if res.fetchone() is None:
            raise HTTPException(status_code=404, detail="Post not found")

        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        # Insert comment as public (is_public = 1)
        res = conn.execute(
            text("""
                INSERT INTO comments (post_id, author_id, body, is_public, created_at, updated_at)
                VALUES (:post_id, :author_id, :body, 1, :now, :now)
            """),
            {
                "post_id": post_id,
                "author_id": user_id,
                "body": body,
                "now": now_str,
            },
        )
        comment_id = res.lastrowid

        # Fetch the inserted comment
        res = conn.execute(
            text("SELECT id, body, is_public, created_at FROM comments WHERE id = :cid"),
            {"cid": comment_id},
        )
        c = res.fetchone()

    return CommentResponse(
        id=c[0],
        body=c[1],
        is_public=bool(c[2]),
        created_at=str(c[3]),
    )


@app.post("/posts/{post_id}/votes", response_model=VoteResponse, operation_id="vote_on_post")
def vote_on_post(post_id: int):
    user_id = get_current_user_id()

    with engine.begin() as conn:
        # Verify user exists
        res = conn.execute(text("SELECT id FROM users WHERE id = :uid"), {"uid": user_id})
        if res.fetchone() is None:
            raise HTTPException(status_code=404, detail="User not found")

        # Verify post exists
        res = conn.execute(text("SELECT id FROM posts WHERE id = :pid"), {"pid": post_id})
        if res.fetchone() is None:
            raise HTTPException(status_code=404, detail="Post not found")

        # Check if user already voted
        res = conn.execute(
            text("SELECT id FROM votes WHERE post_id = :pid AND user_id = :uid"),
            {"pid": post_id, "uid": user_id},
        )
        if res.fetchone() is not None:
            raise HTTPException(status_code=409, detail="User has already voted on this post")

        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        # Insert vote
        res = conn.execute(
            text("""
                INSERT INTO votes (post_id, user_id, created_at)
                VALUES (:post_id, :user_id, :now)
            """),
            {
                "post_id": post_id,
                "user_id": user_id,
                "now": now_str,
            },
        )

        # Increment vote_count on the post
        conn.execute(
            text("UPDATE posts SET vote_count = vote_count + 1, updated_at = :now WHERE id = :pid"),
            {"pid": post_id, "now": now_str},
        )

    return VoteResponse(
        post_id=post_id,
        user_id=user_id,
        created_at=now_str,
    )
