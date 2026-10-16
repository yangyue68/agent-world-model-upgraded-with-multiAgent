# -*- coding: utf-8 -*-
"""static_check 的离线测试：用日志里真实出现过的错误模式验证每条检查都有效。

运行：python -m unittest stages.env.test_static_check -v
只跑 AST 检查和本地 import，不请求 LLM。
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

_MA_ROOT = Path(__file__).resolve().parents[2]
if str(_MA_ROOT) not in sys.path:
    sys.path.insert(0, str(_MA_ROOT))

from stages.env.static_check import analyze_files, run_import_check
from stages.env.runner import (
    _patch_django_datetime,
    _patch_missing_imports,
    _patch_relative_imports,
)


DATABASE = """from sqlalchemy import Column, Integer, String, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

import os

Base = declarative_base()
engine = create_engine(os.getenv("DATABASE_PATH", "sqlite:///xxxx.db"))
SessionLocal = sessionmaker(bind=engine)
"""

SCHEMAS = """from pydantic import BaseModel


class UserOut(BaseModel):
    id: int
    name: str
"""


def _files(**overrides):
    base = {
        "main.py": (
            "from fastapi import FastAPI\n"
            "from routers import users\n"
            "app = FastAPI()\n"
            "app.include_router(users.router)\n"
            "if __name__ == '__main__':\n"
            "    import uvicorn\n"
            "    uvicorn.run(app, host='127.0.0.1', port=8000)\n"
        ),
        "database.py": DATABASE,
        "models.py": (
            "from sqlalchemy import Column, Integer, String, DateTime\n"
            "from database import Base\n"
            "\n"
            "class User(Base):\n"
            "    __tablename__ = 'users'\n"
            "    id = Column(Integer, primary_key=True)\n"
            "    name = Column(String)\n"
            "    created_at = Column(DateTime, default=datetime.utcnow)\n"
        ),
        "schemas.py": SCHEMAS,
        "routers/__init__.py": "",
        "routers/users.py": (
            "from fastapi import APIRouter\n"
            "from database import SessionLocal\n"
            "from models import User\n"
            "from schemas import UserOut\n"
            "\n"
            "router = APIRouter()\n"
            "\n"
            "@router.get('/users', response_model=list[UserOut])\n"
            "async def list_users():\n"
            "    session = SessionLocal()\n"
            "    try:\n"
            "        return session.query(User).all()\n"
            "    finally:\n"
            "        session.close()\n"
        ),
    }
    base.update(overrides)
    return [{"path": p, "content": c} for p, c in base.items()]


def _make_db(directory):
    path = os.path.join(directory, "check.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
    conn.commit()
    conn.close()
    return path


class AnalyzeFilesTest(unittest.TestCase):
    def test_clean_package_passes(self):
        files = _files(**{"models.py": (
            "from datetime import datetime\n"
            "from sqlalchemy import Column, Integer, String, DateTime\n"
            "from database import Base\n"
            "\n"
            "class User(Base):\n"
            "    __tablename__ = 'users'\n"
            "    id = Column(Integer, primary_key=True)\n"
            "    name = Column(String)\n"
            "    created_at = Column(DateTime, default=datetime.utcnow)\n"
        )})
        self.assertEqual(analyze_files(files), [])

    def test_detects_missing_datetime_import(self):
        problems = analyze_files(_files())
        self.assertTrue(
            any("未 import" in p and "datetime" in p and p.startswith("models.py") for p in problems),
            problems,
        )

    def test_detects_missing_top_level_module(self):
        files = [f for f in _files() if f["path"] != "database.py"]
        problems = analyze_files(files)
        self.assertTrue(any("缺少 database.py" in p for p in problems), problems)

    def test_detects_unknown_imported_name(self):
        # routers/users.py 里 import 了一个 schemas.py 根本没定义的名字
        files = _files(**{"routers/users.py": (
            "from fastapi import APIRouter\n"
            "from database import SessionLocal\n"
            "from models import User\n"
            "from schemas import UserCreate\n"
            "\n"
            "router = APIRouter()\n"
        ), "schemas.py": (
            "from pydantic import BaseModel\n"
            "\n"
            "class UserOut(BaseModel):\n"
            "    id: int\n"
        )})
        problems = analyze_files(files)
        self.assertTrue(
            any("from schemas import UserCreate" in p for p in problems),
            problems,
        )

    def test_detects_relative_import(self):
        files = _files(**{"routers/users.py": (
            "from fastapi import APIRouter\n"
            "from ..database import SessionLocal\n"
            "\n"
            "router = APIRouter()\n"
        )})
        problems = analyze_files(files)
        self.assertTrue(any("相对 import" in p for p in problems), problems)

    def test_detects_orm_class_as_response_model(self):
        files = _files(**{"routers/users.py": (
            "from fastapi import APIRouter\n"
            "from models import User\n"
            "\n"
            "router = APIRouter()\n"
            "\n"
            "@router.get('/users', response_model=User)\n"
            "async def list_users():\n"
            "    return []\n"
        )})
        problems = analyze_files(files)
        self.assertTrue(any("直接用了 ORM 类" in p for p in problems), problems)

    def test_detects_django_datetime_misuse(self):
        files = _files(**{"models.py": (
            "from datetime import date, datetime\n"
            "from sqlalchemy import Column, Integer, String, Date\n"
            "from database import Base\n"
            "\n"
            "class User(Base):\n"
            "    __tablename__ = 'users'\n"
            "    id = Column(Integer, primary_key=True)\n"
            "    name = Column(String)\n"
            "    created_at = Column(Date, default=Date.today())\n"
        )})
        problems = analyze_files(files)
        self.assertTrue(any("Django 写法" in p for p in problems), problems)

    def test_detects_module_not_found_at_import_time(self):
        files = [f for f in _files() if f["path"] != "database.py"]
        with tempfile.TemporaryDirectory() as directory:
            db_path = _make_db(directory)
            ok, output = run_import_check(files, db_path)
        self.assertFalse(ok)
        self.assertIn("database", output)

    def test_import_check_passes_for_valid_package(self):
        files = _files(**{"models.py": (
            "from datetime import datetime\n"
            "from sqlalchemy import Column, Integer, String, DateTime\n"
            "from database import Base\n"
            "\n"
            "class User(Base):\n"
            "    __tablename__ = 'users'\n"
            "    id = Column(Integer, primary_key=True)\n"
            "    name = Column(String)\n"
            "    created_at = Column(DateTime, default=datetime.utcnow)\n"
        )})
        with tempfile.TemporaryDirectory() as directory:
            db_path = _make_db(directory)
            ok, output = run_import_check(files, db_path)
        self.assertTrue(ok, output)


class DeterministicPatchTest(unittest.TestCase):
    """这些补丁必须把错误改对，改错比不改更糟，所以逐条断言替换后的完整结果。"""

    def test_patches_django_date_today(self):
        patched = _patch_django_datetime("created = Column(Date, default=Date.today())")
        self.assertIn("import datetime as _awm_datetime", patched)
        self.assertIn("default=_awm_datetime.date.today", patched)
        self.assertNotIn("default=_awm_datetime.date.today()", patched)
        self.assertNotIn("Date.today", patched)

    def test_patches_django_datetime_utcnow(self):
        patched = _patch_django_datetime("created = Column(DateTime, default=DateTime.utcnow())")
        self.assertIn("import datetime as _awm_datetime", patched)
        self.assertIn("default=_awm_datetime.datetime.utcnow", patched)
        self.assertNotIn("default=_awm_datetime.datetime.utcnow()", patched)

    def test_does_not_duplicate_existing_datetime_import(self):
        source = "from datetime import datetime\ncreated = Column(DateTime, default=DateTime.utcnow())"
        patched = _patch_django_datetime(source)
        self.assertEqual(patched.count("from datetime import"), 1)
        self.assertIn("default=_awm_datetime.datetime.utcnow", patched)

    def test_patches_relative_imports(self):
        self.assertEqual(
            _patch_relative_imports("from .database import SessionLocal"),
            "from database import SessionLocal",
        )
        self.assertEqual(
            _patch_relative_imports("def handler():\n    from ..models import User\n    return User"),
            "def handler():\n    from models import User\n    return User",
        )

    def test_patches_missing_import(self):
        patched = _patch_missing_imports(
            "from sqlalchemy import Column\ncreated = Column(DateTime, default=datetime.utcnow)",
            "models.py",
        )
        self.assertIn("from datetime import datetime", patched)

    def test_missing_import_patch_keeps_module_docstring_first(self):
        patched = _patch_missing_imports(
            '"""ORM models."""\nvalue = datetime.utcnow()',
            "models.py",
        )
        lines = [line for line in patched.split("\n") if line.strip()]
        self.assertEqual(lines[0], '"""ORM models."""')
        self.assertEqual(lines[1], "from datetime import datetime")

    def test_missing_import_patch_is_noop_when_already_imported(self):
        source = "from datetime import datetime\nvalue = datetime.now()"
        self.assertEqual(_patch_missing_imports(source, "models.py"), source)


class LogRegressionTest(unittest.TestCase):
    """把日志里真实出现过的失败代码喂给新管线，确认它在起服务器之前就被拦住 / 修好。"""

    def test_missing_os_import_is_caught(self):
        files = _files(**{"database.py": (
            "from sqlalchemy import create_engine\n"
            "from sqlalchemy.orm import declarative_base, sessionmaker\n"
            "\n"
            "Base = declarative_base()\n"
            "DATABASE_PATH = os.getenv('DATABASE_PATH', 'sqlite:///adoptpaws.db')\n"
            "engine = create_engine(DATABASE_PATH)\n"
            "SessionLocal = sessionmaker(bind=engine)\n"
        )})
        problems = analyze_files(files)
        self.assertTrue(
            any("database.py" in p and "'os'" in p for p in problems),
            problems,
        )

    def test_missing_os_import_is_auto_patched(self):
        from stages.env.runner import _patch_missing_imports
        source = (
            "from sqlalchemy import create_engine\n"
            "DATABASE_PATH = os.getenv('DATABASE_PATH', 'x.db')\n"
        )
        patched = _patch_missing_imports(source, "database.py")
        self.assertIn("import os", patched)
        compile(patched, "database.py", "exec")

    def test_missing_typing_list_is_caught(self):
        files = _files(**{"routers/users.py": (
            "from fastapi import APIRouter\n"
            "from models import User\n"
            "\n"
            "router = APIRouter()\n"
            "\n"
            "@router.get('/users', response_model=List[UserOut])\n"
            "async def list_users():\n"
            "    return []\n"
        )})
        problems = analyze_files(files)
        self.assertTrue(any("'List'" in p for p in problems), problems)

    def test_check_constraint_kwarg_is_caught(self):
        files = _files(**{"models.py": (
            "from datetime import datetime\n"
            "from sqlalchemy import Column, Integer, String, DateTime\n"
            "from database import Base\n"
            "\n"
            "class User(Base):\n"
            "    __tablename__ = 'users'\n"
            "    id = Column(Integer, primary_key=True)\n"
            "    name = Column(String)\n"
            "    created_at = Column(DateTime, default=datetime.utcnow)\n"
            "    status = Column(String, check_constraint=\"status IN ('a','b')\")\n"
        )})
        problems = analyze_files(files)
        self.assertTrue(any("check_constraint" in p for p in problems), problems)

    def test_check_constraint_kwarg_preserves_constraint(self):
        from stages.env.runner import _patch_bad_column_kwargs
        source = (
            "from sqlalchemy import Column, String\n"
            "status = Column(String, check_constraint=\"status IN ('a','b')\", nullable=False)\n"
        )
        patched = _patch_bad_column_kwargs(source)
        self.assertNotIn("check_constraint", patched)
        self.assertIn("CheckConstraint", patched)
        self.assertIn("status IN", patched)
        self.assertIn("nullable=False", patched)  # 不能误删合法参数

    def test_full_pipeline_catches_log_failure_before_server(self):
        """日志里的真实组合：database.py 漏 os + router 漏 List + 列名与 schema 不符。
        全部应该在 _rule_check 阶段被静态拦下，而不是等 uvicorn 崩。"""
        from stages.env.generate import _rule_check
        files = _files(**{
            "database.py": (
                "from sqlalchemy import create_engine\n"
                "from sqlalchemy.orm import declarative_base, sessionmaker\n"
                "Base = declarative_base()\n"
                "DATABASE_PATH = os.getenv('DATABASE_PATH', 'x.db')\n"
                "engine = create_engine(DATABASE_PATH)\n"
                "SessionLocal = sessionmaker(bind=engine)\n"
            ),
            "routers/users.py": (
                "from fastapi import APIRouter\n"
                "from database import SessionLocal\n"
                "from models import User\n"
                "from schemas import UserOut\n"
                "router = APIRouter()\n"
                "@router.get('/users', response_model=List[UserOut])\n"
                "async def list_users():\n"
                "    return []\n"
            ),
        })
        db_schema = {"tables": [{"name": "users", "ddl":
            "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, extra TEXT)"}]}
        problems = _rule_check(files, db_schema)
        self.assertTrue(any("'os'" in p for p in problems), problems)
        self.assertTrue(any("漏了 db 有的列" in p and "extra" in p for p in problems), problems)

    def test_no_progress_guard_detects_identical_state(self):
        from stages.env.generate import _fingerprint
        files = _files()
        same = [dict(f) for f in files]
        self.assertEqual(_fingerprint(files), _fingerprint(same))
        changed = [dict(f) for f in files]
        changed[0]["content"] += "\n# tweak"
        self.assertNotEqual(_fingerprint(files), _fingerprint(changed))


if __name__ == "__main__":
    unittest.main()
