import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

# DATABASE_PATH is injected by the runtime environment
DATABASE_PATH = os.environ.get("DATABASE_PATH")

if not DATABASE_PATH:
    raise RuntimeError("DATABASE_PATH environment variable is not set.")

engine = create_engine(DATABASE_PATH)

app = FastAPI()


class MessageResponse(BaseModel):
    message: str


@app.delete("/posts/{post_id}/votes", response_model=MessageResponse, operation_id="delete_post_vote")
def delete_post_vote(post_id: int):
    """
    Delete the current user's upvote on a post.
    """
    # Get the current user ID from the environment variable injected by the runtime
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format")

    with engine.connect() as conn:
        # Check if the vote exists for this user and post
        result = conn.execute(
            text("SELECT id FROM votes WHERE user_id = :user_id AND post_id = :post_id"),
            {"user_id": current_user_id, "post_id": post_id}
        )
        row = result.fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="Vote not found")
        
        # Delete the vote
        conn.execute(
            text("DELETE FROM votes WHERE user_id = :user_id AND post_id = :post_id"),
            {"user_id": current_user_id, "post_id": post_id}
        )
        conn.commit()

    return MessageResponse(message="Vote deleted successfully")


@app.delete("/posts/{post_id}/comments/{comment_id}", response_model=MessageResponse, operation_id="delete_comment")
def delete_comment(post_id: int, comment_id: int):
    """
    Delete a public comment on a post made by the current user.
    """
    # Get the current user ID from the environment variable injected by the runtime
    current_user_id_str = os.environ.get("AWM_CURRENT_USER_ID")
    if not current_user_id_str:
        raise HTTPException(status_code=401, detail="User not authenticated")
    
    try:
        current_user_id = int(current_user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid user ID format")

    with engine.connect() as conn:
        # Check if the comment exists, belongs to the current user, is associated with the correct post, and is public
        result = conn.execute(
            text("""
                SELECT id 
                FROM comments 
                WHERE id = :comment_id 
                  AND post_id = :post_id 
                  AND user_id = :user_id 
                  AND is_public = 1
            """),
            {
                "comment_id": comment_id,
                "post_id": post_id,
                "user_id": current_user_id
            }
        )
        row = result.fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="Comment not found or access denied")
        
        # Delete the comment
        conn.execute(
            text("DELETE FROM comments WHERE id = :comment_id"),
            {"comment_id": comment_id}
        )
        conn.commit()

    return MessageResponse(message="Comment deleted successfully")
