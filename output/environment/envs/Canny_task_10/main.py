import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
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


class BoardResponse(BaseModel):
    id: int
    name: str


class TagResponse(BaseModel):
    id: int
    name: str


class ArchiveBoardResponse(BaseModel):
    id: int
    is_archived: int


class DeleteTagResponse(BaseModel):
    message: str


@app.get("/boards", response_model=List[BoardResponse], operation_id="find_boards")
def find_boards(name: str = Query(..., description="Board name")):
    """
    Filter boards.name by the supplied name and return matching IDs and names.
    """
    db = SessionLocal()
    try:
        # Using parameterized query to prevent SQL injection
        stmt = text("SELECT id, name FROM boards WHERE name = :name")
        result = db.execute(stmt, {"name": name})
        rows = result.fetchall()
        
        return [BoardResponse(id=row.id, name=row.name) for row in rows]
    finally:
        db.close()


@app.get("/boards/{board_id}/tags", response_model=List[TagResponse], operation_id="find_tags_on_board")
def find_tags_on_board(board_id: int, name: str = Query(..., description="Tag name")):
    """
    Filter tags.name by the supplied name for the given board_id and return matching IDs and names.
    """
    db = SessionLocal()
    try:
        stmt = text("SELECT id, name FROM tags WHERE board_id = :board_id AND name = :name")
        result = db.execute(stmt, {"board_id": board_id, "name": name})
        rows = result.fetchall()
        
        return [TagResponse(id=row.id, name=row.name) for row in rows]
    finally:
        db.close()


@app.put("/boards/{board_id}/archive", response_model=ArchiveBoardResponse, operation_id="archive_board")
def archive_board(board_id: int):
    """
    Archives the board with the specified ID by setting its is_archived flag to 1.
    """
    db = SessionLocal()
    try:
        # Check if board exists first to provide better error handling if needed, 
        # though spec implies direct update. We'll perform the update.
        stmt_update = text("UPDATE boards SET is_archived = 1 WHERE id = :board_id")
        result = db.execute(stmt_update, {"board_id": board_id})
        
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Board not found")
            
        db.commit()
        
        # Fetch back the state to confirm
        stmt_select = text("SELECT id, is_archived FROM boards WHERE id = :board_id")
        res = db.execute(stmt_select, {"board_id": board_id}).fetchone()
        
        if not res:
             raise HTTPException(status_code=404, detail="Board not found after update")

        return ArchiveBoardResponse(id=res.id, is_archived=res.is_archived)
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


@app.delete("/boards/{board_id}/tags/{tag_id}", response_model=DeleteTagResponse, operation_id="delete_tag_from_board")
def delete_tag_from_board(board_id: int, tag_id: int):
    """
    Deletes the tag with the given tag_id from the board identified by board_id.
    """
    db = SessionLocal()
    try:
        # Ensure the tag belongs to the specific board before deleting
        stmt_check = text("SELECT id FROM tags WHERE id = :tag_id AND board_id = :board_id")
        check_res = db.execute(stmt_check, {"tag_id": tag_id, "board_id": board_id}).fetchone()
        
        if not check_res:
            raise HTTPException(status_code=404, detail="Tag not found on this board")

        stmt_delete = text("DELETE FROM tags WHERE id = :tag_id AND board_id = :board_id")
        db.execute(stmt_delete, {"tag_id": tag_id, "board_id": board_id})
        
        db.commit()
        
        return DeleteTagResponse(message=f"Tag {tag_id} deleted from board {board_id}")
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()
