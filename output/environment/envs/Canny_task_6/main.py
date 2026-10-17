import os
from typing import Optional, List

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


# --- Pydantic Models for Request/Response ---

class VoteCreateRequest(BaseModel):
    post_id: int
    mrr: Optional[float] = None


class CommentCreateRequest(BaseModel):
    post_id: int
    body: str


class PostResponse(BaseModel):
    id: int
    title: str
    board_id: int


class VoteResponse(BaseModel):
    id: int
    post_id: int
    mrr: float


class CommentResponse(BaseModel):
    id: int
    post_id: int
    body: str
    is_public: bool


class MessageResponse(BaseModel):
    message: str


# --- Helper Functions ---

def get_current_user_id() -> int:
    """Retrieve current user ID from environment."""
    uid_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not uid_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    try:
        return int(uid_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format")


# --- Endpoints ---

@app.get("/posts", response_model=List[PostResponse], operation_id="find_posts")
def find_posts(
    title: str = Query(..., description="Title of the post to find"),
    board_name: str = Query(..., description="Name of the board where the post resides")
):
    """Filter posts by title and board name to locate specific posts for voting or commenting."""
    db = SessionLocal()
    try:
        # Join posts with boards to filter by board name
        query = text("""
            SELECT p.id, p.title, p.board_id 
            FROM posts p
            JOIN boards b ON p.board_id = b.id
            WHERE p.title = :title AND b.name = :board_name
        """)
        result = db.execute(query, {"title": title, "board_name": board_name})
        rows = result.fetchall()
        
        posts = []
        for row in rows:
            posts.append(PostResponse(id=row[0], title=row[1], board_id=row[2]))
        return posts
    finally:
        db.close()


@app.delete("/votes/{vote_id}", response_model=MessageResponse, operation_id="remove_vote")
def remove_vote(vote_id: int):
    """Delete a specific vote identified by vote_id. This operation requires the user to own the vote."""
    current_user_id = get_current_user_id()
    db = SessionLocal()
    try:
        # Check if vote exists and belongs to the current user
        check_query = text("SELECT user_id FROM votes WHERE id = :vote_id")
        res = db.execute(check_query, {"vote_id": vote_id})
        row = res.fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="Vote not found")
        
        if row[0] != current_user_id:
            raise HTTPException(status_code=403, detail="You can only delete your own votes")
        
        # Delete the vote
        delete_query = text("DELETE FROM votes WHERE id = :vote_id")
        db.execute(delete_query, {"vote_id": vote_id})
        db.commit()
        
        return MessageResponse(message="Vote removed successfully")
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


@app.post("/votes", response_model=VoteResponse, operation_id="create_vote")
def create_vote(request: VoteCreateRequest):
    """Cast a vote on a specified post with associated MRR value. The user must be authenticated and have permission to vote on the post."""
    current_user_id = get_current_user_id()
    db = SessionLocal()
    try:
        # Verify post exists
        post_check = text("SELECT id FROM posts WHERE id = :post_id")
        res = db.execute(post_check, {"post_id": request.post_id})
        if not res.fetchone():
            raise HTTPException(status_code=404, detail="Post not found")
        
        # Check if user already voted on this post (UNIQUE constraint on user_id, post_id)
        existing_vote = text("SELECT id FROM votes WHERE user_id = :user_id AND post_id = :post_id")
        res = db.execute(existing_vote, {"user_id": current_user_id, "post_id": request.post_id})
        if res.fetchone():
            raise HTTPException(status_code=409, detail="You have already voted on this post")
        
        # Insert new vote
        insert_query = text("""
            INSERT INTO votes (user_id, post_id, mrr) 
            VALUES (:user_id, :post_id, :mrr)
        """)
        db.execute(insert_query, {
            "user_id": current_user_id,
            "post_id": request.post_id,
            "mrr": request.mrr if request.mrr is not None else 0.0
        })
        db.commit()
        
        # Retrieve the inserted vote to return full details including generated ID
        select_query = text("SELECT id, post_id, mrr FROM votes WHERE user_id = :user_id AND post_id = :post_id")
        res = db.execute(select_query, {"user_id": current_user_id, "post_id": request.post_id})
        row = res.fetchone()
        
        return VoteResponse(id=row[0], post_id=row[1], mrr=row[2])
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


@app.post("/comments", response_model=CommentResponse, operation_id="create_comment")
def create_comment(request: CommentCreateRequest):
    """Add a new public comment to a specified post. The comment must be marked as public."""
    current_user_id = get_current_user_id()
    db = SessionLocal()
    try:
        # Verify post exists
        post_check = text("SELECT id FROM posts WHERE id = :post_id")
        res = db.execute(post_check, {"post_id": request.post_id})
        if not res.fetchone():
            raise HTTPException(status_code=404, detail="Post not found")
        
        # Insert new comment (is_public defaults to 1 per schema, but we explicitly set it to ensure compliance with spec "must be marked as public")
        insert_query = text("""
            INSERT INTO comments (user_id, post_id, body, is_public) 
            VALUES (:user_id, :post_id, :body, 1)
        """)
        db.execute(insert_query, {
            "user_id": current_user_id,
            "post_id": request.post_id,
            "body": request.body
        })
        db.commit()
        
        # Retrieve the inserted comment
        select_query = text("SELECT id, post_id, body, is_public FROM comments WHERE user_id = :user_id AND post_id = :post_id ORDER BY id DESC LIMIT 1")
        res = db.execute(select_query, {"user_id": current_user_id, "post_id": request.post_id})
        row = res.fetchone()
        
        return CommentResponse(id=row[0], post_id=row[1], body=row[2], is_public=bool(row[3]))
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()
