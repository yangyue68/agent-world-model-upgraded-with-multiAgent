"""Verifier 阶段的离线测试：不调用 LLM，只检查选择、证据准备和任务分组。"""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from stages.verifier import generate


_ROOT = Path(__file__).resolve().parents[2]
_ENV_PATH = _ROOT / "output" / "env" / "gen_envs_ma.jsonl"
_SAMPLE_PATH = _ROOT / "output" / "sample" / "gen_sample_ma.jsonl"


def _read_jsonl(path: Path | str) -> list[dict]:
    path = Path(path)
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@unittest.skipUnless(_ENV_PATH.is_file() and _SAMPLE_PATH.is_file(), "需要 env 与 sample 产物")
class VerifierGenerateTest(unittest.TestCase):
    def setUp(self):
        self.config = replace(
            generate.Config(),
            input_env=str(_ENV_PATH),
            input_sample=str(_SAMPLE_PATH),
            model="test-judge",
            limit=2,
            max_concurrency=1,
        )
        self.config.pre_process()

    def test_selects_only_env_tasks_with_environment_and_trajectory(self):
        items = generate._select_env_tasks(self.config)
        self.assertTrue(items)
        env_keys = {
            (generate.normalize_scenario_name(row["scenario"]), str(row["task_id"]))
            for row in _read_jsonl(_ENV_PATH)
        }
        sample_keys = {
            (generate.normalize_scenario_name(row["scenario"]), str(row["task_id"]))
            for row in _read_jsonl(_SAMPLE_PATH)
        }
        self.assertTrue({item["key"] for item in items} <= env_keys)
        # sample 数量远多于 env；选择结果不能由 sample 驱动。
        self.assertLess(len(items), len(sample_keys))
        for item in items:
            self.assertTrue(item["env_dir"].is_dir())
            self.assertTrue(item["probes"])

    def test_prepare_probe_contains_messages_snapshot_and_exact_diff(self):
        item = generate._select_env_tasks(self.config)[0]
        samples = {
            generate._key(row): row
            for row in _read_jsonl(_SAMPLE_PATH)
        }
        payload, metadata = generate._prepare_probe(
            item["env"]["task"], item["probes"][0], samples.get(item["key"], {})
        )
        self.assertEqual(payload["task"], item["env"]["task"])
        self.assertIn("messages", payload["trajectory"])
        self.assertNotIn("final_answer", payload["trajectory"])
        self.assertIn("initial_snapshot", payload["database_state"])
        self.assertIn("diff_summary", metadata)
        if payload["database_state"]["diff"] is not None:
            self.assertIn("summary", payload["database_state"]["diff"])
            self.assertIn("tables", payload["database_state"]["diff"])

    def test_run_groups_tasks_by_judge_classification_without_llm(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            config = replace(
                self.config,
                output=str(temp / "verifier.jsonl"),
                bad_output=str(temp / "bad.jsonl"),
                ready_output=str(temp / "ready.jsonl"),
                solvable_output=str(temp / "solvable.jsonl"),
                unsolvable_output=str(temp / "unsolvable.jsonl"),
                evidence_dir=str(temp / "evidence"),
                resume=False,
            )
            counter = {"value": 0}

            def fake_judge(item, path, sample, task, cfg, client_holder):
                index = counter["value"]
                counter["value"] += 1
                classification = "complete" if index == 0 else "incomplete"
                return {
                    "probe": path.parent.name,
                    "trajectory_path": str(path),
                    "classification": classification,
                    "completed": classification == "complete",
                    "judge_result": {"reasoning": "offline test"},
                }

            with mock.patch.object(generate, "_judge_probe", side_effect=fake_judge):
                results = generate.run(config)

            solvable = _read_jsonl(temp / "solvable.jsonl")
            unsolvable = _read_jsonl(temp / "unsolvable.jsonl")
            ready = _read_jsonl(temp / "ready.jsonl")
            self.assertEqual(len(results), 2)
            self.assertEqual(len(solvable), 1)
            self.assertEqual(len(unsolvable), 1)
            self.assertEqual(len(ready), 2)
            self.assertTrue(solvable[0]["completed"])
            self.assertFalse(unsolvable[0]["completed"])
            self.assertEqual(_read_jsonl(temp / "bad.jsonl"), [])
            self.assertTrue(all(isinstance(row["task_id"], int) for row in ready))

    def test_judge_error_is_not_reported_as_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            config = replace(
                self.config,
                output=str(temp / "verifier.jsonl"),
                bad_output=str(temp / "bad.jsonl"),
                ready_output=str(temp / "ready.jsonl"),
                solvable_output=str(temp / "solvable.jsonl"),
                unsolvable_output=str(temp / "unsolvable.jsonl"),
                evidence_dir=str(temp / "evidence"),
                resume=False,
            )

            def fake_error(item, path, sample, task, cfg, client_holder):
                return {
                    "probe": path.parent.name, "classification": "judge_error", "completed": None,
                    "error_stage": "judge", "error": "offline simulated failure",
                }

            with mock.patch.object(generate, "_judge_probe", side_effect=fake_error):
                results = generate.run(config)

            self.assertTrue(all(row["status"] == "judge_error" for row in results))
            self.assertEqual(_read_jsonl(Path(config.ready_output)), [])
            self.assertEqual(_read_jsonl(Path(config.unsolvable_output)), [])
            self.assertEqual(
                len(_read_jsonl(config.bad_output)),
                sum(row["probe_count"] for row in results),
            )


class LlmJudgeParsingTest(unittest.TestCase):
    class FakeClient:
        def __init__(self, content):
            self.content = content

        def chat_completion(self, **kwargs):
            return self.content

    def test_invalid_classification_becomes_judge_error(self):
        client = self.FakeClient(json.dumps({"classification": "probably fine"}))
        classification, result = generate.llm_judge(client, "test", "task", [], {})
        self.assertEqual(classification, "judge_error")
        self.assertIn("Invalid judge classification", result["error"])

    def test_valid_classification_is_returned(self):
        client = self.FakeClient(json.dumps({"classification": "COMPLETE", "reasoning": "ok"}))
        classification, result = generate.llm_judge(client, "test", "task", [], {})
        self.assertEqual(classification, "complete")
        self.assertEqual(result["reasoning"], "ok")


if __name__ == "__main__":
    unittest.main()
