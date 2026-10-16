"""离线回归测试：使用可记录输入的模型替身，无网络调用。"""

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from .config import Config
from .generate import run
from .llm import SpecAgent, ReplyError, decode_reply
from .pipeline import generate_spec_for_task
from .storage import load_items, read_jsonl, resume_keys
from .validators import normalize_groups, parse_review, validate_spec


def group(name="Products", op="find_products", path="/products"):
    return {"group_name": name, "endpoints": [{"method": "GET", "path": path,
        "operation_id": op, "summary": "Find products", "description": "Query products by name.",
        "user_scope": "all", "request_params": {}, "response": {"type": "array", "items": {
            "type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}}}]}


def review(*names, issue=""):
    return {"results": [{"group": name, "pass": not bool(issue), "issue": issue} for name in names]}


class FakeAgent:
    def __init__(self, *responses):
        self.responses, self.prompts = list(responses), []

    def run(self, prompt):
        self.prompts.append(prompt)
        if not self.responses:
            raise AssertionError("意外的额外模型调用")
        value = self.responses.pop(0)
        return value if isinstance(value, str) else json.dumps(value)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Config(output=str(Path(self.temp.name) / "ok.jsonl"),
                             bad_output=str(Path(self.temp.name) / "bad.jsonl"),
                             max_retry=1, plan_retry=0, generation_retry=0, review_retry=1)
        self.item = {"scenario": "Shop", "task_id": "task_1", "task": "Find products",
                     "db_item": {"db_schema": {"tables": [{"name": "products", "ddl": "CREATE TABLE products(id INTEGER);"}]}},
                     "sample_item": {"initial_snapshot": {"products": {"columns": ["id"], "rows": [[1], [2], [3]]}}}}

    def agents(self, designer=None, reviewer=None, fixer=None, planner=None):
        return {"planner": planner or FakeAgent({"groups": ["Products"]}),
                "designer": designer or FakeAgent(group()),
                "reviewer": reviewer or FakeAgent(review("Products")),
                "fixer": fixer or FakeAgent()}

    def test_success_has_no_file_contract(self):
        agents = self.agents()
        result = generate_spec_for_task(self.item, self.config, agents)
        self.assertEqual(set(result), {"scenario", "task_id", "task", "api_spec", "spec_version"})
        self.assertEqual(result["api_spec"]["api_groups"][0]["endpoints"][0]["example_policy"], "illustrative")
        self.assertEqual(len(agents["fixer"].prompts), 0)

    def test_later_group_sees_previous_group(self):
        agents = self.agents(planner=FakeAgent({"groups": ["Products", "Orders"]}),
                             designer=FakeAgent(group(), group("Orders", "find_orders", "/orders")),
                             reviewer=FakeAgent(review("Products", "Orders")))
        result = generate_spec_for_task(self.item, self.config, agents)
        self.assertNotIn("reason", result)
        self.assertIn('"operation_id": "find_products"', agents["designer"].prompts[1])

    def test_fix_receives_candidate_and_is_reviewed_after_last_round(self):
        fixed = group()
        fixed["endpoints"][0]["description"] = "Return names and IDs, filtered by products.name."
        agents = self.agents(reviewer=FakeAgent(review("Products", issue="Missing names"), review("Products")),
                             fixer=FakeAgent(fixed))
        result = generate_spec_for_task(self.item, self.config, agents)
        self.assertNotIn("reason", result)
        self.assertIn("Missing names", agents["fixer"].prompts[0])
        self.assertIn("current_api_spec", agents["fixer"].prompts[0])
        self.assertIn(fixed["endpoints"][0]["description"], agents["reviewer"].prompts[1])

    def test_invalid_final_fix_is_not_written_as_success(self):
        broken = group()
        broken["endpoints"][0]["response"] = {"type": "array"}
        agents = self.agents(reviewer=FakeAgent(review("Products", issue="Missing names")), fixer=FakeAgent(broken))
        result = generate_spec_for_task(self.item, self.config, agents)
        self.assertEqual(result["stage"], "validation")

    def test_empty_fixer_does_not_delete_group(self):
        agents = self.agents(reviewer=FakeAgent(review("Products", issue="Missing names"), review("Products", issue="Missing names")),
                             fixer=FakeAgent({"endpoints": []}))
        result = generate_spec_for_task(self.item, self.config, agents)
        self.assertIn("reason", result)
        self.assertIn("find_products", agents["reviewer"].prompts[-1])

    def test_bad_review_retries_only_reviewer(self):
        agents = self.agents(reviewer=FakeAgent({"results": []}, review("Products")))
        self.assertNotIn("reason", generate_spec_for_task(self.item, self.config, agents))
        self.assertEqual(len(agents["fixer"].prompts), 0)
        self.assertEqual(len(agents["designer"].prompts), 1)

    def test_review_failure_does_not_invoke_fixer(self):
        agents = self.agents(reviewer=FakeAgent({"results": []}, {"results": []}))
        self.assertEqual(generate_spec_for_task(self.item, self.config, agents)["stage"], "reviewer")
        self.assertFalse(agents["fixer"].prompts)

    def test_rule_failure_repaired_before_semantic_review(self):
        broken = group()
        broken["endpoints"][0]["path"] = "/products/{id}"
        agents = self.agents(designer=FakeAgent(broken), fixer=FakeAgent(group()))
        self.assertNotIn("reason", generate_spec_for_task(self.item, self.config, agents))
        self.assertEqual(len(agents["reviewer"].prompts), 1)

    def test_json_decoding_never_repairs_truncation(self):
        self.assertEqual(decode_reply('<think>thinking</think>\n```json\n{"groups":["A"]}\n```'), {"groups": ["A"]})
        with self.assertRaises(ValueError):
            decode_reply('{"groups":["A"]')

    def test_missing_or_string_verdict_is_not_pass(self):
        for data in ({"results": []}, {"results": [{"group": "Products", "pass": "true", "issue": ""}]}):
            with self.assertRaises(ValueError):
                parse_review(data, [group()])

    def test_truncated_model_response_is_rejected_even_when_json_is_complete(self):
        self.config.save_raw_responses = False
        response = SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content='{"groups":["Products"]}'), finish_reason="length")],
            usage=SimpleNamespace(completion_tokens=50))
        with patch("stages.specific.llm.GPTClient") as client:
            client.return_value.batch_requests.return_value = [response]
            agent = SpecAgent("planner", "plan", self.config, "Shop/task_1", self.temp.name)
            with self.assertRaises(ReplyError):
                agent.run("task")

    def test_empty_model_content_cannot_be_a_successful_plan(self):
        agents = self.agents(planner=FakeAgent(""))
        self.assertEqual(generate_spec_for_task(self.item, self.config, agents)["stage"], "planner")

    def test_duplicate_routes_and_nullable_path_rejected(self):
        a, b = group(), group("Orders", "other", "/products")
        self.assertTrue(validate_spec([a, b]))
        a["endpoints"][0].update(path="/products/{id}", request_params={"id": {
            "type": ["integer", "null"], "required": True, "param_type": "path"}})
        self.assertTrue(validate_spec([a]))

    def test_normalization_does_not_mutate_or_invent_semantics(self):
        original = group()
        del original["endpoints"][0]["user_scope"]
        result = normalize_groups([original])
        self.assertNotIn("tags", original["endpoints"][0])
        self.assertTrue(validate_spec(result))

    def write_inputs(self, mismatch=False):
        root = Path(self.temp.name)
        inputs = {
            "input_task": [{"scenario": "Shop", "tasks": ["Find products", "Another task"]}],
            "input_db": [{**self.item["db_item"], "scenario": "Shop", "task_id": "task_1", "task": "Find products"}],
            "input_sample": [{**self.item["sample_item"], "scenario": "Shop", "task_id": "task_1",
                              "task": "Wrong task" if mismatch else "Find products"}],
        }
        for name, rows in inputs.items():
            path = root / (name + ".jsonl")
            path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            setattr(self.config, name, str(path))

    def test_input_selection_uses_sample_and_rejects_text_mismatch(self):
        self.write_inputs()
        items, errors = load_items(self.config)
        self.assertEqual(len(items), 1)
        self.assertFalse(errors)
        self.write_inputs(mismatch=True)
        items, errors = load_items(self.config)
        self.assertFalse(items)
        self.assertEqual(errors[0]["stage"], "input")

    def test_run_persists_and_resumes_without_model_calls(self):
        self.write_inputs()
        with patch("stages.specific.generate.generate_spec_for_task", side_effect=lambda item, config:
                   generate_spec_for_task(item, config, self.agents())) as worker:
            self.assertEqual(run(self.config)["success"], 1)
            self.assertEqual(run(self.config)["attempted"], 0)
            self.assertEqual(worker.call_count, 1)
        self.assertEqual(len(read_jsonl(self.config.output)), 1)
        self.assertEqual(resume_keys(self.config.output), {("Shop", "task_1")})

    def test_run_reports_discarded_input_and_stage_summary(self):
        self.write_inputs(mismatch=True)
        result = run(self.config)
        self.assertEqual(result["success"], 0)
        self.assertEqual(result["attempted"], 0)
        self.assertEqual(result["input_errors"], 1)
        self.assertEqual(result["discarded"], 0)
        self.assertEqual(result["discarded_by_stage"], {"input": 1})

    def test_resume_rejects_old_file_plan_artifacts(self):
        Path(self.config.output).write_text(json.dumps({"scenario": "Shop", "task_id": "task_1", "api_spec": {"api_groups": [group()]}}), encoding="utf-8")
        with self.assertRaises(ValueError):
            resume_keys(self.config.output)


if __name__ == "__main__":
    unittest.main()
