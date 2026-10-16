"""env 修复闭环回归测试：离线模拟 LLM，实际运行本地导入及服务检查。"""

import ast
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from lib.gpt import ChatCompletionFallback
from stages.env import generate, runner
from stages.env.llm import EnvAgent, parse_agent_output, decode_json_reply
from stages.env.static_check import analyze_files, run_import_check
from stages.env.test_static_check import _files, _make_db
from stages.env.validators import validate_codegen_output


def fake_agent(name, raw):
    return SimpleNamespace(name=name, run=Mock(return_value=raw), last_finish_reason="stop", last_validation_error="")


class RepairLoopTest(unittest.TestCase):
    def test_fixer_reads_source_with_line_numbers_and_error_file_first(self):
        files = [{"path": "main.py", "content": "value = 1\n"},
                 {"path": "schemas.py", "content": "class UserOut:\n    id: int\n"}]
        agent = fake_agent("fixer", '{"files": []}')
        generate._fix_files(files, "schemas.py missing AnimalCreate", "", "", "", "", {}, {},
                            {"fixer": agent}, "test")
        prompt = agent.run.call_args.args[0]
        self.assertIn("   1: class UserOut:\n   2:     id: int", prompt)
        self.assertLess(prompt.index("### schemas.py"), prompt.index("### main.py"))

    def validate(self, files, config=None):
        return generate._validate_test_fix(files, "test/task_1", "business", "task", "",
                                           {}, {}, "unused.db", {}, config or generate.Config())

    def test_normalize_before_rules_avoids_unnecessary_llm_repair(self):
        agents = {"code_generator": fake_agent("generator", json.dumps({"files": _files()})),
                  "fixer": fake_agent("fixer", "")}
        result = generate._generate_files("test", "business", "task", "", {}, {}, [], "", agents, generate.Config())
        self.assertIsNotNone(result)
        self.assertEqual(analyze_files(result), [])
        agents["fixer"].run.assert_not_called()
        self.assertEqual(runner.prepare_files(result), result)

    def test_noop_stops_import_startup_and_smoke_repairs(self):
        for mode in ("import", "startup", "smoke"):
            with self.subTest(mode=mode):
                files = runner.prepare_files(_files())
                import_result = (False, "temporary_path/NameError") if mode == "import" else (True, "")
                server_result = (False, "temporary_path/NameError", []) if mode == "startup" else (True, "", ["GET /users -> 500"])
                with patch.object(generate, "run_import_check", return_value=import_result) as imp, \
                     patch.object(generate, "test_multifile_server", return_value=server_result) as server, \
                     patch.object(generate, "_fix_files", return_value=files) as fixer:
                    _, state = self.validate(files)
                self.assertFalse(state["success"])
                self.assertEqual(fixer.call_count, 1)
                self.assertEqual(imp.call_count, 1)
                self.assertEqual(server.call_count, 0 if mode == "import" else 1)

    def test_runtime_fix_rechecks_static_contract_before_import(self):
        files = runner.prepare_files(_files())
        bad = [{"path": "routers/users.py", "content": (
            "from fastapi import APIRouter\nfrom schemas import MissingSchema\n"
            "router = APIRouter()\n") }]
        with patch.object(generate, "run_import_check", return_value=(True, "")) as imp, \
             patch.object(generate, "test_multifile_server", return_value=(True, "", ["GET /users -> 500"])) as server, \
             patch.object(generate, "_fix_files", side_effect=[bad, None]) as fixer:
            _, state = self.validate(files)
        self.assertFalse(state["success"])
        self.assertIn("MissingSchema", state["feedback"])
        self.assertEqual(imp.call_count, 1)
        self.assertEqual(server.call_count, 1)
        self.assertEqual(fixer.call_count, 2)

    def test_repair_budget_is_independent_of_regeneration_budget(self):
        files = runner.prepare_files(_files())
        original_main = next(f["content"] for f in files if f["path"] == "main.py")
        fixes = [[{"path": "main.py", "content": original_main + f"\nvalue = {i}\n"}] for i in range(4)]
        with patch.object(generate, "run_import_check", side_effect=[(False, "error")] * 4 + [(True, "")]), \
             patch.object(generate, "test_multifile_server", return_value=(True, "", [])), \
             patch.object(generate, "_fix_files", side_effect=fixes) as fixer:
            _, state = self.validate(files, generate.Config(max_retry=0, max_fix_retry=4))
        self.assertTrue(state["success"])
        self.assertEqual(fixer.call_count, 4)

    def test_cycles_and_comment_only_changes_are_rejected(self):
        files = runner.prepare_files(_files())
        seen = {generate._fingerprint(files)}
        main = next(f for f in files if f["path"] == "main.py")
        comments = [{**main, "content": main["content"] + "\n# still broken"}]
        self.assertIsNone(generate._merge_repair(files, comments, seen, "test"))
        changed = generate._merge_repair(files, [{**main, "content": main["content"] + "\nvalue = 1"}], seen, "test")
        self.assertIsNotNone(changed)
        self.assertIsNone(generate._merge_repair(changed, [main], seen, "test"))

    def test_invalid_fixer_patch_is_rejected_before_merge(self):
        for entry in ({"path": "../bad.py", "content": "value = 1"},
                      {"path": "router.py", "content": "def broken(\n"}, "not a file"):
            with self.subTest(entry=entry):
                agents = {"fixer": fake_agent("fixer", json.dumps({"files": [entry]}))}
                result = generate._fix_files(_files(), "error", "", "business", "task", "", {}, {}, agents, "test")
                self.assertIsNone(result)


class PatchContractTest(unittest.TestCase):
    def test_imports_preserve_multiline_docstring_future_and_import(self):
        source = ('"""module\nwith multiple lines\n"""\n'
                  'from __future__ import annotations\nfrom sqlalchemy import (\n    Column,\n)\n'
                  'value = datetime.now()\n')
        patched = runner._patch_missing_imports(source, "models.py")
        compile(patched, "models.py", "exec")
        tree = ast.parse(patched)
        self.assertEqual(ast.get_docstring(tree), "module\nwith multiple lines")
        self.assertEqual(tree.body[1].module, "__future__")

    def test_datetime_patch_preserves_literal_and_datetime_module_binding(self):
        source = ('"""doc"""\nfrom __future__ import annotations\nimport datetime\n'
                  'example = "Date.today()"\nvalue = Date.today()\n')
        patched = runner._patch_django_datetime(source)
        compile(patched, "models.py", "exec")
        scope = {}
        exec(patched, scope)
        import datetime
        self.assertEqual(scope["example"], "Date.today()")
        self.assertEqual(scope["value"], datetime.date.today())
        self.assertIs(scope["datetime"], datetime)

    def test_column_date_default_is_callable(self):
        source = "from sqlalchemy import Column, Date\nvalue = Column(Date, default=Date.today())"
        patched = runner._patch_django_datetime(source)
        scope = {}
        exec(patched, scope)
        self.assertTrue(scope["value"].default.is_callable)

    def test_model_config_respects_import_aliases_and_inheritance(self):
        for source in (
            "from pydantic import BaseModel as BM, ConfigDict as CD\n"
            "class Shared(BM):\n    id: int\nclass UserOut(Shared):\n    pass",
            "import pydantic as pd\nclass UserOut(pd.BaseModel):\n    id: int",
            "from pydantic import BaseModel\nConfigDict = 42\nclass UserOut(BaseModel):\n    id: int",
        ):
            with self.subTest(source=source):
                patched = runner._patch_model_config(source)
                scope = {"__name__": "env_model_test"}
                exec(patched, scope)
                model = scope["UserOut"]
                self.assertTrue(model.model_config["from_attributes"])
                self.assertEqual(model.model_validate(SimpleNamespace(id=1)).id, 1)
                self.assertEqual(runner._patch_model_config(patched), patched)

    def test_model_config_preserves_explicit_configs_and_non_models(self):
        sources = (
            "from typing import ClassVar\nfrom pydantic import BaseModel, ConfigDict\n"
            "class UserOut(BaseModel):\n    model_config: ClassVar[ConfigDict] = ConfigDict(from_attributes=True)\n    id: int",
            "from pydantic import BaseModel\nclass UserOut(BaseModel):\n"
            "    class Config:\n        from_attributes = True\n    id: int",
            "class FileOut:\n    pass",
        )
        for source in sources:
            with self.subTest(source=source):
                self.assertEqual(runner._patch_model_config(source), source)

    def test_real_check_constraint_remains_enforced(self):
        source = ('from sqlalchemy import Column, String\n'
                  'value = Column("status", String, check_constraint="status IN (\'a\',\'b\')", nullable=False)')
        patched = runner._patch_missing_imports(runner._patch_bad_column_kwargs(source), "models.py")
        scope = {}
        exec(patched, scope)
        from sqlalchemy import MetaData, Table, create_engine
        from sqlalchemy.exc import IntegrityError
        table = Table("items", MetaData(), scope["value"])
        engine = create_engine("sqlite://")
        try:
            table.metadata.create_all(engine)
            with engine.begin() as connection:
                connection.execute(table.insert().values(status="a"))
                with self.assertRaises(IntegrityError):
                    connection.execute(table.insert().values(status="invalid"))
        finally:
            engine.dispose()

    def test_response_model_orm_alias_and_generic_are_rejected(self):
        files = _files(**{"routers/users.py": (
            "from models import User as UserORM\nfrom fastapi import APIRouter\nrouter=APIRouter()\n"
            "@router.get('/users', response_model=list[UserORM])\ndef users():\n    return []")})
        prepared = runner.prepare_files(files)
        self.assertTrue(any("直接用了 ORM 类" in p for p in analyze_files(prepared)))
        self.assertNotIn("response_model=None", next(f["content"] for f in prepared if f["path"] == "routers/users.py"))

    def test_same_name_pydantic_model_is_not_misidentified_as_orm(self):
        files = runner.prepare_files(_files(**{
            "schemas.py": "from pydantic import BaseModel\nclass User(BaseModel):\n    id: int",
            "routers/users.py": "from schemas import User\nfrom fastapi import APIRouter\nrouter = APIRouter()\n"
                                  "@router.get('/users', response_model=list[User])\ndef users():\n    return []",
        }))
        self.assertEqual(analyze_files(files), [])

    def test_real_package_relative_import_is_preserved(self):
        source = "from .models import User"
        self.assertEqual(runner._patch_relative_imports(source, "package/router.py", {"package/models.py"}), source)

    def test_damaged_json_file_shapes_do_not_crash_rules(self):
        for files in (["bad"], [{"path": 1, "content": "value = 1"}], [{"path": "main.py", "content": None}]):
            with self.subTest(files=files):
                self.assertTrue(generate._rule_check(files, {}))

    def test_parent_and_drive_paths_are_rejected(self):
        for path in ("routers/../../outside.py", "E:/outside.py"):
            self.assertTrue(validate_codegen_output({"files": [{"path": path, "content": "value = 1"}]}, require_main=False))


class DiagnosticsTest(unittest.TestCase):
    def test_env_judge_accepts_negative_and_requires_evidence_for_bugs(self):
        for raw in ('环境代码没有问题。', 'OK',
                    '{"has_env_bug": false, "issues": []}',
                    '{"has_env_bug": "false", "issues": ["wrong"]}',
                    '{"has_env_bug": true, "issues": [{"description": "empty response"}]}'):
            with self.subTest(raw=raw):
                self.assertEqual(generate._parse_env_judgment(raw), [])
        problems = generate._parse_env_judgment(
            '{"has_env_bug": true, "issues": [{"description": "field missing", "evidence": "GET /x returned {}"}]}')
        self.assertEqual(len(problems), 1)
        self.assertIn("GET /x", problems[0])

    def test_json_reply_preserves_python_source_and_fences(self):
        raw = '''```json
{"files":[{"path":"models.py","content":"value = 'a\\'b'\\n"}]}
```'''
        data = decode_json_reply(raw)
        self.assertEqual(data["files"][0]["content"], "value = 'a\\'b'\n")
        compile(data["files"][0]["content"], "models.py", "exec")

    def test_json_reply_rejects_ambiguous_truncation(self):
        with self.assertRaises(ValueError):
            decode_json_reply('{"files": [{"path": "main.py", "content": "x"}')

    def test_logs_actual_usage_and_saves_raw_response(self):
        response = ChatCompletionFallback({
            "choices": [{"message": {"content": '{"files": []}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 8},
        })
        client = SimpleNamespace(batch_requests=Mock(return_value=[response]))
        with tempfile.TemporaryDirectory() as directory:
            agent = EnvAgent("fixer", "system", client=client, label="test/task_1", diagnostics_dir=directory, max_tokens=50000)
            raw = agent.run("task")
            metadata = json.loads(next(Path(directory).rglob("*.json")).read_text(encoding="utf-8"))
            self.assertEqual(next(Path(directory).rglob("*.txt")).read_text(encoding="utf-8"), raw)
        self.assertEqual(metadata["completion_tokens"], 8)
        self.assertEqual(metadata["chars"], len(raw))
        self.assertEqual(metadata["finish_reason"], "stop")
        self.assertEqual(client.batch_requests.call_args.args[0][0]["max_tokens"], 50000)

    def test_truncated_response_is_not_accepted_even_when_json_parses(self):
        agent = fake_agent("generator", "")
        agent.last_finish_reason = "length"
        self.assertIsNone(parse_agent_output(agent, '{"files": []}', "test"))


class RunnerTest(unittest.TestCase):
    def test_timeout_retry_preserves_definite_failures(self):
        for errors, expected_calls in ((["GET /a -> ReadTimeout"], 2),
                                       (["GET /a -> 500", "GET /b -> ReadTimeout"], 1)):
            with self.subTest(errors=errors), patch.object(runner, "_test_multifile_server_once",
                side_effect=[(True, "", errors), (True, "", [])]) as once:
                result = runner.test_multifile_server([], "unused.db", "test", 0)
            self.assertEqual(once.call_count, expected_calls)
            self.assertEqual(result[2], [] if expected_calls == 2 else errors)

    def test_large_stdout_does_not_block_startup_or_requests(self):
        main = (
            "import os\nfrom fastapi import FastAPI\nimport uvicorn\n"
            "print('startup-' + 'x' * 200000, flush=True)\napp = FastAPI()\n"
            "@app.get('/ping')\ndef ping():\n"
            "    print('request-' + 'y' * 200000, flush=True)\n    return {'ok': True}\n"
            "if __name__ == '__main__':\n"
            "    uvicorn.run(app, host=os.getenv('HOST', '127.0.0.1'), port=int(os.getenv('PORT', '8000')))\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            db_path = _make_db(directory)
            before = Path(db_path).read_bytes()
            result = runner.test_multifile_server([{"path": "main.py", "content": main}], db_path, "noisy", 0,
                                                  startup_timeout=15, smoke_timeout=3, smoke_retry=0)
            self.assertEqual(Path(db_path).read_bytes(), before)
        self.assertEqual(result, (True, "", []))

    def test_server_traceback_is_returned_to_fixer(self):
        main = (
            "import os\nfrom fastapi import FastAPI\nimport uvicorn\napp=FastAPI()\n"
            "@app.get('/broken')\ndef broken():\n    raise ValueError('intentional regression failure')\n"
            "if __name__ == '__main__':\n"
            "    uvicorn.run(app, host='127.0.0.1', port=int(os.getenv('PORT', '8000')))\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            result = runner.test_multifile_server([{"path": "main.py", "content": main}], _make_db(directory), "broken", 0,
                                                  startup_timeout=15, smoke_retry=0)
        self.assertTrue(result[0])
        self.assertTrue(any("GET /broken -> 500" in e for e in result[2]))
        self.assertTrue(any("intentional regression failure" in e for e in result[2]))

    def test_import_check_timeout_terminates_process(self):
        files = [{"path": "main.py", "content": "import time\nprint('before-block', flush=True)\ntime.sleep(60)"}]
        ok, output = run_import_check(files, None, timeout=1)
        self.assertFalse(ok)
        self.assertIn("超时", output)
        self.assertIn("before-block", output)


if __name__ == "__main__":
    unittest.main()
