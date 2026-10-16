"""进程内工具适配回归：真实 FastAPI、ORM、任务数据库副本，不调用在线模型。"""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI, Query
from typing import Annotated
from pydantic import BaseModel

from lib.runtime import parse_tool_calls
from stages.rl import env as runtime
from stages.rl.rollout import _get_system_prompt


class RolloutProtocolTest(unittest.TestCase):
    def test_qwen_safe_tool_tag_is_used_and_legacy_tag_still_parses(self):
        prompt = _get_system_prompt(10)
        self.assertIn("<awm_tool_call>", prompt)
        self.assertNotIn("<tool_call>", prompt)

        for tag in ("awm_tool_call", "tool_call"):
            with self.subTest(tag=tag):
                calls = parse_tool_calls(
                    f'<{tag}>{{"name":"list_tools","arguments":null}}</{tag}>'
                )
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0]["name"], "list_tools")


class ToolSchemaTest(unittest.TestCase):
    def test_argument_validation_matches_fastapi_constraints_and_aliases(self):
        app = FastAPI()

        @app.get("/search")
        def search(limit: Annotated[int, Query(ge=1)], value: Annotated[str, Query(alias="class")]):
            return {"limit": limit, "value": value}

        route = next(r for r in app.routes if getattr(r, "path", None) == "/search")
        kwargs, error = runtime._validate_route_arguments(route, {"limit": "2", "class": "x"})
        self.assertIsNone(error)
        self.assertEqual(kwargs, {"limit": 2, "value": "x"})
        kwargs, error = runtime._validate_route_arguments(route, {"limit": "0", "class": "x"})
        self.assertEqual(kwargs, {})
        self.assertIn("greater_than_equal", error)

    def test_automatic_operation_id_matches_tools_and_resolves_body_refs(self):
        class Payload(BaseModel):
            name: str
            count: int = 1
            tags: list[str] = []
            note: str | None = None

        app = FastAPI()

        @app.post("/items")
        def create_item(payload: Payload, limit: int | None = None):
            return payload.model_dump()

        functions, routes = runtime._route_maps(app)
        tools = runtime._build_tools_from_openapi(app, functions)
        self.assertEqual(len(tools), 1)
        tool = tools[0]
        self.assertEqual(tool["name"], app.openapi()["paths"]["/items"]["post"]["operationId"])
        self.assertIs(functions[tool["name"]], create_item)
        props = tool["inputSchema"]["properties"]
        self.assertEqual(props["count"]["type"], "integer")
        self.assertEqual(props["tags"]["items"]["type"], "string")
        self.assertIn({"type": "null"}, props["limit"]["anyOf"])
        self.assertEqual(tool["inputSchema"]["required"], ["name"])

    def test_duplicate_explicit_operation_id_is_rejected(self):
        app = FastAPI()
        app.get("/a", operation_id="same")(lambda: [])
        app.get("/b", operation_id="same")(lambda: [])
        with self.assertRaisesRegex(ValueError, "same"):
            runtime._route_maps(app)


class InProcessToolTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.code_dir = self.directory / "env"
        self.code_dir.mkdir()
        database = (
            "from sqlalchemy import create_engine\n"
            "from sqlalchemy.orm import declarative_base, sessionmaker\n"
            "engine = create_engine('sqlite:///:memory:')\n"
            "SessionLocal = sessionmaker(bind=engine)\nBase = declarative_base()\n"
        )
        main = '''from __future__ import annotations
from fastapi import FastAPI, Depends
from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import Session
from pydantic import BaseModel, ConfigDict
from database import Base
app = FastAPI()
class Item(Base):
    __tablename__ = "items"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
class Payload(BaseModel):
    name: str
class ItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
def get_db():
    raise AssertionError("runtime must inject its own session")
@app.get("/items", response_model=list[ItemOut])
def list_items(db: Session = Depends(get_db)):
    return db.query(Item).all()
@app.post("/items", response_model=ItemOut)
async def create_item(payload: Payload, db: Session = Depends(get_db)):
    item = Item(name=payload.name)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item
@app.get("/bad", response_model=ItemOut)
async def bad():
    return {"id": 1, "name": None}
'''
        (self.code_dir / "database.py").write_text(database, encoding="utf-8")
        (self.code_dir / "main.py").write_text(main, encoding="utf-8")
        self.source_db = self.directory / "source.db"
        with sqlite3.connect(self.source_db) as connection:
            connection.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
            connection.execute("INSERT INTO items VALUES (1, 'seed')")
        self.env = runtime.InProcessEnv("test", str(self.code_dir), "unused", "unused",
                                        str(self.directory / "run"), initial_db_source=str(self.source_db))
        self.env.start()

    def tearDown(self):
        self.env.close()
        runtime._clear_loaded_env_modules()
        runtime._scenario_cache.clear()
        self.temp.cleanup()

    def operation(self, prefix):
        return next(tool["name"] for tool in self.env.list_tools() if tool["name"].startswith(prefix))

    def test_sync_orm_read_and_async_model_write_use_final_database(self):
        self.assertEqual(json.loads(self.env.call_tool(self.operation("list_items"), {})), [{"id": 1, "name": "seed"}])
        result = json.loads(self.env.call_tool(self.operation("create_item"), {"name": "new"}))
        self.assertEqual(result, {"id": 2, "name": "new"})
        with sqlite3.connect(self.env.db_file_path) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM items").fetchone()[0], 2)
        with sqlite3.connect(self.source_db) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM items").fetchone()[0], 1)

    def test_response_validation_error_is_reported_as_environment_500(self):
        result = self.env.call_tool(self.operation("bad"), {})
        self.assertIn("Status code: 500", result)
        self.assertIn("ResponseValidationError", result)


if __name__ == "__main__":
    unittest.main()
