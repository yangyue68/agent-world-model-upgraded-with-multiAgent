"""离线测试文件清单、全局校验和生成/修复/落盘流程，不调用模型。"""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from stages.spec.file_plan import build_files, build_spec_result, validate_files
from stages.spec.generate import (Config, _fix_group, _generate_groups, _load_resume_records,
                                  _normalize_spec_shapes, _plan_groups, _review,
                                  _remove_cross_group_duplicates, generate_spec_for_task, run)
from stages.spec.validators import validate_spec


def group(name="Orders", op="list_orders", path="/orders"):
    return {"group_name": name, "endpoints": [{
        "operation_id": op, "path": path, "method": "GET",
        "summary": "List orders", "description": "Complete business rules.",
        "tags": [name], "request_params": {}, "user_scope": "all", "example_policy": "illustrative",
        "response": {"type": "object", "properties": {"orders": {
            "type": "array", "items": {"type": "object", "properties": {
                "id": {"type": "integer"}}, "required": ["id"]}}}, "required": ["orders"]},
    }]}


class FilePlanTests(unittest.TestCase):
    def test_normalizes_only_unambiguous_path_wrappers_and_implicit_empty_required(self):
        candidate = group(path="/orders/{order_id}")
        ep = candidate["endpoints"][0]
        ep["request_params"] = {"path": {"order_id": {
            "type": "integer", "description": "Order identifier"}}}
        ep["response"] = {"type": "object", "properties": {
            "order": {"type": "object", "properties": {"id": {"type": "integer"}}}}
        }
        _normalize_spec_shapes([candidate])
        self.assertEqual(set(ep["request_params"]), {"order_id"})
        self.assertEqual(ep["request_params"]["order_id"]["param_type"], "path")
        self.assertTrue(ep["request_params"]["order_id"]["required"])
        self.assertEqual(ep["response"]["required"], [])
        self.assertEqual(ep["response"]["properties"]["order"]["required"], [])
        self.assertEqual(validate_spec([candidate])[1], [])

    def test_ambiguous_path_wrapper_is_left_for_fixer(self):
        candidate = group(path="/orders/{order_id}")
        candidate["endpoints"][0]["request_params"] = {
            "path": {"order_id": {"type": "integer"}, "other": {"type": "string"}}}
        _normalize_spec_shapes([candidate])
        self.assertIn("path", candidate["endpoints"][0]["request_params"])
        self.assertTrue(validate_spec([candidate])[1])

    def test_fixed_layout_and_one_router_per_group(self):
        spec = {"api_groups": [group("订单/中文"), group("Customers", "list_customers", "/customers")]}
        files = build_files(spec)
        self.assertEqual([f["path"] for f in files], [
            "main.py", "database.py", "models.py", "schemas.py", "routers/__init__.py",
            "routers/group_001.py", "routers/group_002.py",
        ])
        self.assertEqual(files[-2]["group_name"], "订单/中文")
        self.assertEqual(files[-1]["operation_ids"], ["list_customers"])
        self.assertEqual(validate_files(spec, files), [])

    def test_full_spec_preserved_without_aliasing(self):
        groups = [group()]
        groups[0]["endpoints"][0]["description"] = "必须保留的业务规则。" * 100
        original = copy.deepcopy(groups)
        result = build_spec_result("Shop", "task_2", "read orders", groups)
        self.assertEqual(result["api_spec"]["api_groups"], original)
        result["api_spec"]["api_groups"][0]["endpoints"][0]["description"] = "changed"
        self.assertEqual(groups, original)
        self.assertFalse(result["module_contract"]["router"]["cross_router_imports"])

    def test_invalid_bindings_and_paths_rejected(self):
        spec = {"api_groups": [group()]}
        for mutation in ("missing", "wrong_id", "duplicate", "unsafe"):
            with self.subTest(mutation=mutation):
                files = build_files(spec)
                if mutation == "missing":
                    files.pop()
                elif mutation == "wrong_id":
                    files[-1]["operation_ids"] = ["unknown"]
                elif mutation == "duplicate":
                    files.append(copy.deepcopy(files[-1]))
                else:
                    files[-1]["path"] = "../outside.py"
                self.assertTrue(validate_files(spec, files))

    def test_global_operation_collisions_drop_both_groups(self):
        kept, dropped = validate_spec([group(), group("Other", "list_orders", "/other")])
        self.assertEqual(kept, [])
        self.assertEqual({d["group"] for d in dropped}, {"Orders", "Other"})

    def test_parameterized_route_collision(self):
        kept, dropped = validate_spec([
            group("Orders", "get_order", "/orders/{id}"),
            group("Other", "get_other", "/orders/{order_id}"),
        ])
        self.assertEqual(kept, [])
        self.assertTrue(all(any("method/path" in r for r in d["reasons"]) for d in dropped))

    def test_malformed_groups_fail_validation(self):
        for groups in ([], [None], [{"group_name": "X", "endpoints": [None]}],
                       [{"group_name": "X", "endpoints": "text"}],
                       [group("X"), group("X", "other", "/other")]):
            with self.subTest(groups=groups):
                self.assertTrue(validate_spec(groups)[1])

    def test_response_requires_recursive_typed_contract(self):
        invalid = group()
        invalid["endpoints"][0]["response"] = {"results": {"type": "array"}}
        self.assertTrue(validate_spec([invalid])[1])
        nullable = group()
        nullable["endpoints"][0]["response"] = {
            "type": "object", "properties": {
                "value": {"type": ["string", "null"], "example": None},
            }, "required": ["value"],
        }
        self.assertEqual(validate_spec([nullable])[1], [])

    def test_empty_spec_cannot_be_resumed_with_a_fixed_skeleton(self):
        spec = {"api_groups": []}
        self.assertTrue(validate_files(spec, build_files(spec)))

    def test_path_parameters_match_route_for_dict_and_list(self):
        for as_list in (False, True):
            with self.subTest(as_list=as_list):
                candidate = group(path="/orders/{order_id}")
                ep = candidate["endpoints"][0]
                parameter = {"type": "integer", "param_type": "path", "required": True}
                ep["request_params"] = ([dict(parameter, name="order_id")] if as_list
                                        else {"order_id": parameter})
                self.assertEqual(validate_spec([candidate])[1], [])
                parameter = ep["request_params"][0] if as_list else parameter
                parameter["required"] = False
                self.assertIn("required=true", str(validate_spec([candidate])[1]))
                parameter["required"] = True
                parameter["type"] = ["integer", "null"]
                self.assertIn("不能为 null", str(validate_spec([candidate])[1]))

    def test_missing_and_extraneous_path_parameters_are_rejected(self):
        missing = group(path="/orders/{order_id}")
        self.assertIn("缺少同名", str(validate_spec([missing])[1]))
        extra = group()
        extra["endpoints"][0]["request_params"] = {
            "order_id": {"type": "integer", "param_type": "path", "required": True}}
        self.assertIn("没有对应占位符", str(validate_spec([extra])[1]))

    def test_parameter_list_names_and_tags_are_validated(self):
        for params in ([{"type": "integer", "required": True, "param_type": "query"}],
                       [{"name": "id", "type": "integer", "required": True, "param_type": "query"}] * 2):
            candidate = group()
            candidate["endpoints"][0]["request_params"] = params
            self.assertTrue(validate_spec([candidate])[1])
        candidate = group()
        candidate["endpoints"][0]["tags"] = []
        self.assertTrue(validate_spec([candidate])[1])

    def test_resume_requeues_invalid_records_with_full_backup(self):
        valid = build_spec_result("Shop", "task_1", "read orders", [group()])
        invalid = copy.deepcopy(valid)
        invalid["task_id"] = "task_2"
        invalid["api_spec"]["api_groups"][0]["endpoints"][0]["path"] = "/orders/{order_id}"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spec.jsonl"
            original = "".join(json.dumps(r) + "\n" for r in [valid, invalid])
            path.write_text(original, encoding="utf-8")
            self.assertEqual(_load_resume_records(str(path)), [valid])
            self.assertEqual(list(map(json.loads, path.read_text(encoding="utf-8").splitlines())), [valid])
            backups = list(Path(directory).glob("spec.resume_backup_*.jsonl"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), original)
            self.assertEqual(_load_resume_records(str(path)), [valid])
            self.assertEqual(len(list(Path(directory).glob("spec.resume_backup_*.jsonl"))), 1)


class PipelineTests(unittest.TestCase):
    def test_cross_group_duplicate_cleanup_preserves_unique_endpoints(self):
        broken = {"group_name": "Deals", "endpoints": [
            group("Deals", "create_deal_financing", "/deals/{deal_id}/financing")["endpoints"][0],
            group("Deals", "create_deal", "/deals")["endpoints"][0],
        ]}
        owner = group("Financing", "create_deal_financing", "/deals/{deal_id}/financing")
        cleaned = _remove_cross_group_duplicates(broken, [broken, owner])
        self.assertEqual([ep["operation_id"] for ep in cleaned["endpoints"]], ["create_deal"])

    def setUp(self):
        self.item = {"scenario": "Shop", "task_id": "task_1", "task": "read orders",
                     "db_item": {"db_schema": {"tables": []}}, "sample_item": {}}

    def test_group_generation_freezes_planned_name_and_preserves_description(self):
        generated = group("ModelRenamedGroup")
        generated["endpoints"][0]["description"] = "long business rule " * 100
        orch = Mock()
        orch.map_reduce.return_value = [json.dumps(generated)]
        result = _generate_groups("Shop", ["task"], {}, "", ["Orders"], "",
                                  {"spec_designer": Mock()}, orch)
        self.assertEqual(result[0]["group_name"], "Orders")
        self.assertEqual(result[0]["endpoints"], generated["endpoints"])

    def test_explicit_empty_designer_group_is_distinguished_from_missing_output(self):
        orch = Mock()
        orch.map_reduce.return_value = ['{"endpoints": []}', '{}']
        self.assertEqual(_generate_groups("Shop", ["task"], {}, "", ["Unused", "Missing"], "",
                                         {"spec_designer": Mock()}, orch),
                         [{"group_name": "Unused", "endpoints": []}])

    def test_regeneration_preserves_full_ordered_plan_and_existing_capabilities(self):
        orch = Mock()
        orch.map_reduce.return_value = [json.dumps(group("Customers", "find_customers", "/customers"))]
        _generate_groups("Shop", ["task"], {}, "", ["Customers"], "",
                         {"spec_designer": Mock()}, orch,
                         planned_groups=["Orders", "Customers"], existing_groups=[group()])
        prompt = orch.map_reduce.call_args.kwargs["to_user_content"]("Customers")
        self.assertIn('["Orders", "Customers"]', prompt)
        self.assertIn('"operation_id": "list_orders"', prompt)

    def test_all_request_parameter_errors_reach_fixer_together(self):
        broken = group()
        broken["endpoints"][0]["request_params"] = {
            "body": {"type": "object", "properties": {"note": {"type": "string"}}, "required": ["note"]}
        }
        reason = " ".join(validate_spec([broken])[1][0]["reasons"])
        self.assertIn("param_type", reason)
        self.assertIn("required 必须是布尔值", reason)
        self.assertIn("required_fields", reason)

    def test_removing_group_never_bypasses_task_coverage_review(self):
        second = group("Customers", "list_customers", "/customers")
        with patch("stages.spec.generate.build_agents", return_value={}), \
             patch("stages.spec.generate._plan_groups", return_value=["Orders", "Customers"]), \
             patch("stages.spec.generate._generate_groups", return_value=[group(), second]), \
             patch("stages.spec.generate._review", side_effect=[
                 [{"group": "Orders", "pass": True}, {"group": "Customers", "pass": False, "issue": "wrong"}],
                 [{"group": "Orders", "pass": False, "issue": "missing customer lookup"}]]), \
             patch("stages.spec.generate._fix_group", return_value={"group_name": "Customers", "endpoints": []}):
            result = generate_spec_for_task(self.item, Config(max_retry=1))
        self.assertIn("missing customer lookup", result["reason"])
        self.assertNotIn("api_spec", result)

    def test_fixer_sees_other_group_endpoint_contract(self):
        fixer = Mock()
        fixer.run.return_value = json.dumps(group(op="find_orders"))
        _fix_group(group(), "Shop", "task", "", "", "duplicate", {"spec_fixer": fixer},
                   api_groups=[group(), group("Customers", "list_customers", "/customers")])
        prompt = fixer.run.call_args.args[0]
        self.assertIn("list_customers", prompt)
        self.assertIn("/customers", prompt)

    def test_planner_valid_names_are_trimmed_and_deduplicated(self):
        planner = Mock()
        planner.run.return_value = '{"groups": [" Orders ", "Orders", "Customers"]}'
        self.assertEqual(_plan_groups("Shop", ["task"], {}, "", {"spec_planner": planner}),
                         ["Orders", "Customers"])

    def test_planner_invalid_contracts_report_specific_errors(self):
        planner = Mock()
        for raw, message in (
            ("", "空文本"), ('"Orders"', "必须为对象"),
            ('{"api_groups": ["Orders"]}', "缺少 groups"),
            ('{"groups": "Orders"}', "必须为数组"),
            ('{"groups": []}', "groups=\\[\\]"),
            ('{"groups": [{"name": "Orders"}]}', "非空字符串"),
        ):
            with self.subTest(raw=raw):
                planner.run.return_value = raw
                with self.assertRaisesRegex(ValueError, message):
                    _plan_groups("Shop", ["task"], {}, "", {"spec_planner": planner})

    def test_planner_name_array_is_accepted_without_retry(self):
        planner = Mock()
        planner.run.return_value = '["Animals", "Vaccinations"]'
        self.assertEqual(_plan_groups("Shop", ["task"], {}, "", {"spec_planner": planner}),
                         ["Animals", "Vaccinations"])
        planner.run.assert_called_once()

    def test_fixer_accepts_explicit_empty_list_but_not_missing_field(self):
        fixer = Mock()
        fixer.run.return_value = '{"group_name": "Orders", "endpoints": []}'
        self.assertEqual(_fix_group(group(), "Shop", "task", "", "", "redundant", {"spec_fixer": fixer})["endpoints"], [])
        fixer.run.return_value = '{"group_name": "Orders"}'
        self.assertEqual(_fix_group(group(), "Shop", "task", "", "", "error", {"spec_fixer": fixer}), group())

    def test_deleted_group_is_not_regenerated_and_remaining_coverage_is_reviewed(self):
        redundant = group("Customers", "duplicate_orders", "/customers")
        with patch("stages.spec.generate.build_agents", return_value={}), \
             patch("stages.spec.generate._plan_groups", return_value=["Orders", "Customers"]), \
             patch("stages.spec.generate._generate_groups", return_value=[group(), redundant]) as generate, \
             patch("stages.spec.generate._review", side_effect=[
                 [{"group": "Orders", "pass": True}, {"group": "Customers", "pass": False, "issue": "duplicate"}],
                 [{"group": "Orders", "pass": True}]]) as review, \
             patch("stages.spec.generate._fix_group", return_value={"group_name": "Customers", "endpoints": []}):
            result = generate_spec_for_task(self.item, Config(max_retry=1))
        generate.assert_called_once()
        self.assertEqual(review.call_count, 2)
        self.assertEqual([g["group_name"] for g in result["api_spec"]["api_groups"]], ["Orders"])

    def test_last_capability_cannot_be_deleted(self):
        with patch("stages.spec.generate.build_agents", return_value={}), \
             patch("stages.spec.generate._plan_groups", return_value=["Orders"]), \
             patch("stages.spec.generate._generate_groups", return_value=[group()]), \
             patch("stages.spec.generate._review", return_value=[{"group": "Orders", "pass": False, "issue": "missing"}]), \
             patch("stages.spec.generate._fix_group", return_value={"group_name": "Orders", "endpoints": []}):
            result = generate_spec_for_task(self.item, Config(max_retry=1))
        self.assertIn("reason", result)

    def test_reviewer_waits_until_all_groups_are_structurally_valid(self):
        broken = group("Customers", "get_customer", "/customers")
        broken["endpoints"][0]["method"] = "INVALID"
        with patch("stages.spec.generate.build_agents", return_value={}), \
             patch("stages.spec.generate._plan_groups", return_value=["Orders", "Customers"]), \
             patch("stages.spec.generate._generate_groups", return_value=[group(), broken]), \
             patch("stages.spec.generate._review", return_value=[{"group": "Orders", "pass": True}, {"group": "Customers", "pass": True}]) as review, \
             patch("stages.spec.generate._fix_group", return_value=group("Customers", "get_customer", "/customers")):
            result = generate_spec_for_task(self.item, Config(max_retry=1))
        self.assertIn("api_spec", result)
        review.assert_called_once()
        self.assertEqual(len(review.call_args.args[0]), 2)

    def test_later_fixer_sees_latest_specs(self):
        first, second = group(), group("Customers", "list_customers", "/customers")
        fixed = group(op="updated_orders")
        snapshots = []
        def fix(g, *args, **kwargs):
            snapshots.append(copy.deepcopy(kwargs["api_groups"]))
            return fixed if g["group_name"] == "Orders" else second
        with patch("stages.spec.generate.build_agents", return_value={}), \
             patch("stages.spec.generate._plan_groups", return_value=["Orders", "Customers"]), \
             patch("stages.spec.generate._generate_groups", return_value=[first, second]), \
             patch("stages.spec.generate._review", side_effect=[
                 [{"group": "Orders", "pass": False, "issue": "wrong"}, {"group": "Customers", "pass": False, "issue": "wrong"}],
                 [{"group": "Orders", "pass": True}, {"group": "Customers", "pass": True}]]), \
             patch("stages.spec.generate._fix_group", side_effect=fix):
            result = generate_spec_for_task(self.item, Config(max_retry=1))
        self.assertIn("api_spec", result)
        self.assertEqual(snapshots[1][0]["endpoints"][0]["operation_id"], "updated_orders")

    def test_planner_raw_reply_is_saved_before_rejection(self):
        planner = Mock()
        planner.run.return_value = '{"api_groups": ["Orders"]}'
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                _plan_groups("Shop", ["task"], {}, "", {"spec_planner": planner},
                             diagnostic_dir=Path(directory), attempt=2)
            self.assertEqual((Path(directory) / "planner_attempt_2.txt").read_text(encoding="utf-8"),
                             planner.run.return_value)

    def test_planner_retry_receives_failure_feedback(self):
        planner = Mock()
        planner.run.side_effect = ['{"api_groups": ["Orders"]}', '{"groups": ["Orders"]}']
        with tempfile.TemporaryDirectory() as directory, \
             patch("stages.spec.generate.build_agents", return_value={"spec_planner": planner}), \
             patch("stages.spec.generate._generate_groups", return_value=[group()]), \
             patch("stages.spec.generate._review", return_value=[{"group": "Orders", "pass": True}]):
            result = generate_spec_for_task(self.item, Config(output=str(Path(directory) / "spec.jsonl"), plan_retry=1))
        self.assertIn("api_spec", result)
        self.assertEqual(planner.run.call_count, 2)
        self.assertIn("缺少 groups", planner.run.call_args.args[0])

    def test_planner_service_error_is_kept_in_bad_task_reason(self):
        planner = Mock()
        planner.run.side_effect = RuntimeError("service unavailable")
        with tempfile.TemporaryDirectory() as directory, \
             patch("stages.spec.generate.build_agents", return_value={"spec_planner": planner}):
            result = generate_spec_for_task(self.item, Config(output=str(Path(directory) / "spec.jsonl"), plan_retry=0))
        self.assertIn("RuntimeError: service unavailable", result["reason"])

    def test_review_errors_and_missing_verdict_are_not_passes(self):
        reviewer = Mock()
        agents = {"spec_reviewer": reviewer}
        reviewer.run_json.side_effect = RuntimeError("offline")
        self.assertFalse(_review([group()], "Shop", ["task"], {}, "", agents)[0]["pass"])
        reviewer.run_json.side_effect = None
        for reply in ({"results": []}, {"results": [{"group": "Orders", "pass": "true"}]},
                      {"results": [{"group": "Other", "pass": True}]}):
            reviewer.run_json.return_value = reply
            self.assertFalse(_review([group()], "Shop", ["task"], {}, "", agents)[0]["pass"])
        self.assertIn("routers/group_001.py", reviewer.run_json.call_args.args[0])

    def test_fixer_changes_reflected_in_manifest(self):
        broken = group()
        broken["endpoints"][0]["method"] = "INVALID"
        fixed = group(op="find_orders")
        with patch("stages.spec.generate.build_agents", return_value={}), \
             patch("stages.spec.generate._plan_groups", return_value=["Orders"]), \
             patch("stages.spec.generate._generate_groups", return_value=[broken]), \
             patch("stages.spec.generate._review", return_value=[{"group": "Orders", "pass": True}]), \
             patch("stages.spec.generate._fix_group", return_value=fixed) as fixer:
            result = generate_spec_for_task(self.item, Config(max_retry=1))
        fixer.assert_called_once()
        self.assertEqual(result["files"][-1]["operation_ids"], ["find_orders"])
        self.assertEqual(validate_spec(result["api_spec"]["api_groups"])[1], [])

    def test_missing_group_regenerated_then_bound(self):
        with patch("stages.spec.generate.build_agents", return_value={}), \
             patch("stages.spec.generate._plan_groups", return_value=["Orders", "Customers"]), \
             patch("stages.spec.generate._generate_groups", side_effect=[
                 [group()], [group("Customers", "list_customers", "/customers")]]), \
             patch("stages.spec.generate._review", side_effect=[
                 [{"group": "Orders", "pass": True}],
                 [{"group": "Orders", "pass": True}, {"group": "Customers", "pass": True}]]):
            result = generate_spec_for_task(self.item, Config(max_retry=1))
        self.assertEqual(len(result["files"]), 7)
        self.assertEqual(result["files"][-1]["group_name"], "Customers")

    def test_no_unchecked_fix_on_last_round(self):
        broken = group()
        broken["endpoints"][0]["method"] = "INVALID"
        with patch("stages.spec.generate.build_agents", return_value={}), \
             patch("stages.spec.generate._plan_groups", return_value=["Orders"]), \
             patch("stages.spec.generate._generate_groups", return_value=[broken]), \
             patch("stages.spec.generate._fix_group") as fixer:
            result = generate_spec_for_task(self.item, Config(max_retry=0))
        self.assertIn("reason", result)
        self.assertNotIn("files", result)
        fixer.assert_not_called()

    def test_run_writes_and_resumes_task_level_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = {
                "tasks": [{"scenario": "Shop", "tasks": ["read orders"]}],
                "db": [{"scenario": "Shop", "task_id": "task_1", "db_schema": {}}],
                "samples": [{"scenario": "Shop", "task_id": "task_1"}],
            }
            for name, records in inputs.items():
                (root / f"{name}.jsonl").write_text(
                    "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
            config = Config(input_task=str(root / "tasks.jsonl"), input_db=str(root / "db.jsonl"),
                            input_sample=str(root / "samples.jsonl"), output=str(root / "out/spec.jsonl"),
                            bad_output=str(root / "bad/failures.jsonl"), max_concurrency=1)
            result = build_spec_result("Shop", "task_1", "read orders", [group()])
            with patch("stages.spec.generate.generate_spec_for_task", return_value=result) as worker:
                run(config)
                run(config)
                worker.assert_called_once()
            records = [json.loads(line) for line in Path(config.output).read_text(encoding="utf-8").splitlines()]
            self.assertEqual(records, [result])
            del result["files"]
            Path(config.output).write_text(json.dumps(result) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "resume false"):
                run(config)


if __name__ == "__main__":
    unittest.main()
