"""离线验证覆盖证据、查询依赖和评审/修复的分工，不调用在线模型。"""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from stages.spec.coverage import ReviewFormatError, validate_review
from stages.spec.generate import Config, _review, generate_spec_for_task
from stages.spec.test_generate import group


def covered_reply(groups, operations=None, inputs=None):
    return {
        "results": [{"group": g["group_name"], "pass": True, "issue": ""} for g in groups],
        "coverage": [{
            "requirement": "Retrieve the requested orders",
            "group": groups[0]["group_name"], "covered": True,
            "operation_ids": operations or [groups[0]["endpoints"][0]["operation_id"]],
            "inputs": inputs or [], "evidence": "The endpoint returns orders from the database.",
            "issue": "",
        }],
    }


def input_source(op, parameter, source="task", **kwargs):
    return {"operation_id": op, "parameter": parameter, "source": source,
            "detail": "The value is supplied by the stated source.", **kwargs}


class CoverageTests(unittest.TestCase):
    def test_valid_read_only_evidence_and_reply_not_mutated(self):
        groups = [group()]
        reply = covered_reply(groups)
        original = copy.deepcopy(reply)
        verdicts = validate_review(groups, reply)
        self.assertTrue(verdicts[0]["pass"])
        self.assertEqual(verdicts[0]["coverage"], reply["coverage"])
        self.assertEqual(reply, original)

    def test_format_errors_are_not_business_failures(self):
        valid = covered_reply([group()])
        mutations = [None, {}, {"results": valid["results"]},
                     dict(valid, coverage=[]), dict(valid, results=[])]
        for key, value in (("group", []), ("covered", "true"), ("operation_ids", []),
                           ("evidence", ""), ("inputs", {})):
            reply = copy.deepcopy(valid)
            reply["coverage"][0][key] = value
            mutations.append(reply)
        reply = copy.deepcopy(valid)
        reply["results"][0]["pass"] = False
        mutations.append(reply)
        for reply in mutations:
            with self.subTest(reply=reply), self.assertRaises(ReviewFormatError):
                validate_review([group()], reply)

    def test_unsupported_input_source_cannot_use_examples_as_evidence(self):
        groups = [group()]
        groups[0]["endpoints"][0]["request_params"] = {
            "order_id": {"type": "integer", "param_type": "query", "required": True}}
        reply = covered_reply(groups, inputs=[input_source("list_orders", "order_id", "example")])
        with self.assertRaises(ReviewFormatError):
            validate_review(groups, reply)

    def test_missing_requirement_overrides_positive_group_verdict(self):
        groups = [group(), group("Addresses", "choose_address", "/addresses")]
        reply = covered_reply(groups)
        reply["coverage"].append({"requirement": "Enter a new shipping address",
            "group": "Addresses", "covered": False, "operation_ids": [], "inputs": [],
            "evidence": "Selecting an existing address cannot create the requested new address.",
            "issue": "Missing address-content fields or an address creation endpoint."})
        verdicts = validate_review(groups, reply)
        self.assertTrue(verdicts[0]["pass"])
        self.assertFalse(verdicts[1]["pass"])
        self.assertIn("Enter a new shipping address", verdicts[1]["issue"])
        self.assertIn("Missing address-content", verdicts[1]["issue"])

    def test_invented_endpoint_or_parameter_cannot_prove_coverage(self):
        groups = [group()]
        for ops, inputs, expected in (
            (["nonexistent"], [], "不存在的接口"),
            (["list_orders"], [input_source("list_orders", "address_line1")], "不存在的请求参数"),
        ):
            verdict = validate_review(groups, covered_reply(groups, ops, inputs))[0]
            self.assertFalse(verdict["pass"])
            self.assertIn(expected, verdict["issue"])

    def test_required_object_fields_cannot_hide_behind_body(self):
        groups = [group()]
        ep = groups[0]["endpoints"][0]
        ep["request_params"] = {"body": {"type": "object", "param_type": "body",
            "required": True, "properties": {"order_id": {"type": "integer"},
            "reason": {"type": "string"}}, "required_fields": ["order_id", "reason"]}}
        reply = covered_reply(groups, inputs=[input_source("list_orders", "body")])
        verdict = validate_review(groups, reply)[0]
        self.assertFalse(verdict["pass"])
        self.assertIn("body.order_id", verdict["issue"])
        self.assertIn("body.reason", verdict["issue"])
        reply["coverage"][0]["inputs"] = [input_source("list_orders", "body.order_id"),
                                            input_source("list_orders", "body.reason")]
        self.assertTrue(validate_review(groups, reply)[0]["pass"])

    def test_required_array_fields_and_list_form_parameters(self):
        groups = [group()]
        groups[0]["endpoints"][0]["request_params"] = [{"name": "animals", "type": "array",
            "param_type": "body", "required": True, "items": {"type": "object",
            "properties": {"tag": {"type": "string"}}, "required": ["tag"]}}]
        reply = covered_reply(groups, inputs=[input_source("list_orders", "animals[].tag")])
        self.assertTrue(validate_review(groups, reply)[0]["pass"])
        reply["coverage"][0]["inputs"][0]["parameter"] = "animals[].unknown"
        self.assertFalse(validate_review(groups, reply)[0]["pass"])

    def test_task_supplied_array_covers_required_item_fields(self):
        groups = [group()]
        groups[0]["endpoints"][0]["request_params"] = {
            "items": {"type": "array", "param_type": "body", "required": True,
                      "items": {"type": "object", "properties": {
                          "description": {"type": "string"},
                          "quantity": {"type": "integer"},
                      }, "required": ["description", "quantity"]}}
        }
        reply = covered_reply(groups, inputs=[input_source("list_orders", "items")])
        self.assertTrue(validate_review(groups, reply)[0]["pass"])

    def _lookup_case(self):
        target = group(op="create_return", path="/returns")
        target["endpoints"][0]["request_params"] = {
            "order_id": {"type": "integer", "param_type": "body", "required": True}}
        lookup = group("OrderLookup", "find_orders", "/orders")
        lookup["endpoints"][0]["request_params"] = {
            "order_number": {"type": "string", "param_type": "query", "required": True}}
        groups = [target, lookup]
        reply = covered_reply(groups, ["find_orders", "create_return"], [
            input_source("find_orders", "order_number"),
            input_source("create_return", "order_id", "lookup",
                         lookup_operation_id="find_orders", response_field="orders[].id"),
        ])
        return groups, reply

    def test_lookup_chain_resolves_required_ids(self):
        groups, reply = self._lookup_case()
        self.assertTrue(all(v["pass"] for v in validate_review(groups, reply)))

    def test_required_inputs_can_be_documented_across_requirement_rows(self):
        groups = [group()]
        endpoint = groups[0]["endpoints"][0]
        endpoint["request_params"] = {
            "user_id": {"type": "integer", "param_type": "query", "required": True},
            "note": {"type": "string", "param_type": "query", "required": True},
        }
        reply = covered_reply(groups, inputs=[input_source("list_orders", "user_id")])
        reply["coverage"][0]["requirement"] = "Find the user's orders"
        reply["coverage"].append({
            "requirement": "Add a note to the order request",
            "group": groups[0]["group_name"], "covered": True,
            "operation_ids": ["list_orders"],
            "inputs": [input_source("list_orders", "note")],
            "evidence": "The note is supplied by the task.", "issue": "",
        })
        self.assertTrue(validate_review(groups, reply)[0]["pass"])

    def test_invented_lookup_and_response_fields_are_rejected(self):
        for key, value, expected in (
            ("lookup_operation_id", "unknown_lookup", "查询来源接口 unknown_lookup 不存在"),
            ("response_field", "orders[].unknown", "未声明响应字段"),
            ("response_field", "$", "类型与参数"),
        ):
            groups, reply = self._lookup_case()
            reply["coverage"][0]["inputs"][1][key] = value
            verdict = validate_review(groups, reply)[0]
            self.assertFalse(verdict["pass"])
            self.assertIn(expected, verdict["issue"])

    def test_lookup_inputs_need_sources_and_cannot_be_cyclic(self):
        groups, reply = self._lookup_case()
        reply["coverage"][0]["inputs"].pop(0)
        self.assertIn("find_orders.order_number", validate_review(groups, reply)[0]["issue"])
        groups, reply = self._lookup_case()
        reply["coverage"][0]["operation_ids"].reverse()
        self.assertIn("先执行", validate_review(groups, reply)[0]["issue"])
        groups, reply = self._lookup_case()
        reply["coverage"][0]["inputs"][1].update(
            lookup_operation_id="create_return", response_field="orders[].id")
        self.assertIn("自引用", validate_review(groups, reply)[0]["issue"])

    def test_optional_parameters_not_forced_and_context_source_supported(self):
        groups = [group()]
        groups[0]["endpoints"][0]["request_params"] = {
            "user_id": {"type": "integer", "param_type": "query", "required": True},
            "limit": {"type": "integer", "param_type": "query", "required": False}}
        reply = covered_reply(groups, inputs=[input_source("list_orders", "user_id", "context")])
        self.assertTrue(validate_review(groups, reply)[0]["pass"])

    def test_duplicate_input_sources_and_false_ownership_are_rejected(self):
        groups, reply = self._lookup_case()
        reply["coverage"][0]["inputs"].append(copy.deepcopy(reply["coverage"][0]["inputs"][0]))
        self.assertIn("参数来源重复", validate_review(groups, reply)[0]["issue"])
        groups, reply = self._lookup_case()
        reply["coverage"][0]["group"] = "OrderLookup"
        reply["coverage"][0]["operation_ids"] = ["create_return"]
        self.assertIn("不属于负责分组", validate_review(groups, reply)[1]["issue"])


class ReviewFlowTests(unittest.TestCase):
    def test_malformed_review_retried_with_feedback_and_diagnostics(self):
        reviewer = Mock()
        groups = [group()]
        reviewer.run_json.side_effect = [{"results": [{"group": "Orders", "pass": True}]},
                                         covered_reply(groups)]
        with tempfile.TemporaryDirectory() as directory:
            verdicts = _review(groups, "Shop", ["read orders"], {}, "", {"spec_reviewer": reviewer},
                               diagnostic_dir=Path(directory))
            self.assertTrue(verdicts[0]["pass"])
            files = list(Path(directory).glob("review_*.json"))
            self.assertEqual(len(files), 2)
            first = json.loads((Path(directory) / "review_round_1_attempt_1.json").read_text(encoding="utf-8"))
            self.assertIn("coverage", first["error"])
        self.assertEqual(reviewer.run_json.call_count, 2)
        self.assertIn("coverage", reviewer.run_json.call_args.args[0])

    def test_reviewer_failure_never_invokes_fixer(self):
        reviewer = Mock()
        reviewer.run_json.return_value = {"results": [{"group": "Orders", "pass": True}]}
        item = {"scenario": "Shop", "task_id": "task_1", "task": "read orders",
                "db_item": {"db_schema": {}}, "sample_item": {}}
        with tempfile.TemporaryDirectory() as directory, \
             patch("stages.spec.generate.build_agents", return_value={"spec_reviewer": reviewer}), \
             patch("stages.spec.generate._plan_groups", return_value=["Orders"]), \
             patch("stages.spec.generate._generate_groups", return_value=[group()]), \
             patch("stages.spec.generate._fix_group") as fixer:
            result = generate_spec_for_task(item, Config(output=str(Path(directory) / "spec.jsonl")))
        fixer.assert_not_called()
        self.assertNotIn("api_spec", result)
        self.assertIn("Reviewer 重试耗尽", result["reason"])

    def test_missing_capability_reaches_fixer_and_final_evidence_is_saved(self):
        groups = [group()]
        missing = covered_reply(groups)
        missing["coverage"][0].update(covered=False, operation_ids=[],
            evidence="No lookup exists.", issue="Missing lookup for the task's order number.")
        reviewer = Mock()
        reviewer.run_json.side_effect = [missing, covered_reply(groups)]
        item = {"scenario": "Shop", "task_id": "task_1", "task": "read orders",
                "db_item": {"db_schema": {}}, "sample_item": {}}
        with tempfile.TemporaryDirectory() as directory, \
             patch("stages.spec.generate.build_agents", return_value={"spec_reviewer": reviewer}), \
             patch("stages.spec.generate._plan_groups", return_value=["Orders"]), \
             patch("stages.spec.generate._generate_groups", return_value=groups), \
             patch("stages.spec.generate._fix_group", return_value=group()) as fixer:
            result = generate_spec_for_task(item, Config(output=str(Path(directory) / "spec.jsonl"), max_retry=1))
        self.assertIn("Missing lookup", fixer.call_args.args[5])
        self.assertEqual(result["task_coverage"], covered_reply(groups)["coverage"])


if __name__ == "__main__":
    unittest.main()
