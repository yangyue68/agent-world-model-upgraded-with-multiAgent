import unittest

from stages.environment.validation import (
    check_openapi,
    generated_code_runtime_errors,
    normalize_code,
    repair_name_error_import,
    scan_trajectory,
    summarize_problems,
)


class CheckOpenApiTest(unittest.TestCase):
    def test_does_not_prefix_the_database_url_from_environment(self):
        code = 'DATABASE_PATH = os.environ.get("DATABASE_PATH")\nengine = create_engine(f"sqlite:///{DATABASE_PATH}")\n'

        self.assertIn("engine = create_engine(DATABASE_PATH)", normalize_code(code))

    def test_normalizes_database_url_alias_to_database_path(self):
        code = 'DATABASE_PATH = os.getenv("DATABASE_URL", "sqlite:///./test.db")\n'

        self.assertIn('DATABASE_PATH = os.environ.get("DATABASE_PATH")', normalize_code(code))

    def test_accepts_required_json_body_fields_from_referenced_schema(self):
        api_spec = {"api_groups": [{"endpoints": [{
            "method": "POST",
            "path": "/forms",
            "operation_id": "create_form",
            "request_params": {
                "name": {"param_type": "body", "required": True},
                "fee": {"param_type": "body", "required": True},
            },
        }]}]}
        document = {
            "paths": {"/forms": {"post": {
                "operationId": "create_form",
                "requestBody": {"required": True, "content": {"application/json": {
                    "schema": {"$ref": "#/components/schemas/FormInput"},
                }}},
            }}},
            "components": {"schemas": {"FormInput": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "fee": {"type": "integer"}},
                "required": ["name", "fee"],
            }}},
        }

        self.assertEqual(check_openapi(api_spec, document), [])

    def test_reports_missing_required_json_body_field(self):
        api_spec = {"api_groups": [{"endpoints": [{
            "method": "POST",
            "path": "/forms",
            "operation_id": "create_form",
            "request_params": {"name": {"param_type": "body", "required": True}},
        }]}]}
        document = {"paths": {"/forms": {"post": {
            "operationId": "create_form",
            "requestBody": {"content": {"application/json": {"schema": {
                "type": "object", "properties": {}, "required": [],
            }}}},
        }}}}

        self.assertEqual(
            check_openapi(api_spec, document),
            ["create_form: required body field name missing"],
        )

    def test_reports_missing_nested_request_schema_fields(self):
        api_spec = {"api_groups": [{"endpoints": [{
            "method": "POST",
            "path": "/forms",
            "operation_id": "create_form",
            "request_params": {"sections": {
                "param_type": "body",
                "required": True,
                "type": "array",
                "items": {"type": "object", "required": ["title"], "properties": {
                    "title": {"type": "string"},
                }},
            }},
        }]}]}
        document = {"paths": {"/forms": {"post": {
            "operationId": "create_form",
            "requestBody": {"content": {"application/json": {"schema": {
                "type": "object",
                "properties": {"sections": {"type": "array", "items": {"type": "object", "properties": {}}}},
                "required": ["sections"],
            }}}},
        }}}}

        self.assertEqual(
            check_openapi(api_spec, document),
            ["create_form.sections[]: required nested field title missing"],
        )

    def test_resolves_nullable_nested_array_schema(self):
        api_spec = {"api_groups": [{"endpoints": [{
            "method": "POST", "path": "/forms", "operation_id": "create_form",
            "request_params": {"requirements": {
                "param_type": "body", "required": False, "type": "array",
                "items": {"type": "object", "required": ["title"], "properties": {
                    "title": {"type": "string"},
                }},
            }},
        }]}]}
        document = {"paths": {"/forms": {"post": {
            "operationId": "create_form",
            "requestBody": {"content": {"application/json": {"schema": {
                "type": "object", "properties": {"requirements": {
                    "anyOf": [{"type": "array", "items": {"type": "object", "properties": {
                        "title": {"type": "string"},
                    }, "required": ["title"]}}, {"type": "null"}],
                }},
            }}}},
        }}}}

        self.assertEqual(check_openapi(api_spec, document), [])

    def test_repairs_missing_sqlalchemy_name_import_from_traceback(self):
        code = "from sqlalchemy import func\nvalue = distinct(1)\n"
        problems = ["NameError: name 'distinct' is not defined"]

        self.assertEqual(
            repair_name_error_import(code, problems),
            "from sqlalchemy import distinct\nfrom sqlalchemy import func\nvalue = distinct(1)\n",
        )

    def test_does_not_repair_names_not_exported_by_sqlalchemy(self):
        code = "value = unknown_name()\n"

        self.assertEqual(
            repair_name_error_import(code, ["NameError: name 'unknown_name' is not defined"]),
            code,
        )

    def test_summarizes_traceback_to_generated_frame_and_root_exception(self):
        problem = "Traceback (most recent call last):\n  File \"main.py\", line 20, in create\n    db.add(row)\nsqlite3.IntegrityError: CHECK constraint failed: field_type IN ('text','number')"

        self.assertEqual(
            summarize_problems([problem, problem]),
            ['File "main.py", line 20, in create: sqlite3.IntegrityError: CHECK constraint failed: field_type IN (\'text\',\'number\')'],
        )

    def test_identifies_exceptions_from_generated_main_as_runtime_errors(self):
        generated = 'Traceback (most recent call last):\n  File "env/main.py", line 12, in endpoint\nNameError: name \'distinct\' is not defined'
        framework = 'Traceback (most recent call last):\n  File "worker.py", line 12, in invoke\nRuntimeError: worker failed'

        self.assertEqual(
            generated_code_runtime_errors([generated, framework]),
            ['File "env/main.py", line 12, in endpoint: NameError: name \'distinct\' is not defined'],
        )

    def test_keeps_root_exception_when_scanning_long_traceback(self):
        result = "Error: Status code: 500, Internal Server Error:\nTraceback (most recent call last):\n" + ("  File \"worker.py\", line 1\n" * 400)
        result += '  File "main.py", line 42, in create\nsqlite3.IntegrityError: CHECK constraint failed: field_type IN (\'text\',\'number\')'

        errors = scan_trajectory({"http_events": [{"status": 500, "result": result}]})["environment_errors"]

        self.assertIn("sqlite3.IntegrityError: CHECK constraint failed", errors[0])
        self.assertIn('File "main.py", line 42, in create', errors[0])


if __name__ == "__main__":
    unittest.main()
