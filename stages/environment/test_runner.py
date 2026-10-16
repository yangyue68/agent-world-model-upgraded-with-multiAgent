import unittest
from unittest.mock import patch

from stages.environment import runner


class RunProbeTest(unittest.TestCase):
    @patch("stages.environment.runner.check_database", return_value=[])
    @patch("stages.environment.runner.run_single_rollout")
    @patch("stages.environment.runner.GPTClient")
    @patch("stages.environment.runner.SingleFileEnv")
    def test_starts_environment_before_rollout(self, env_factory, client_factory, rollout, check_database):
        env = env_factory.return_value

        def verify_started_before_rollout(*args, **kwargs):
            env.start.assert_called_once_with()
            return {"http_events": []}

        rollout.side_effect = verify_started_before_rollout
        result = runner.run_probe("env", "run", {"scenario": "s", "db_path": "db", "task": "task"},
                                  type("Config", (), {"runtime_timeout": 1, "request_timeout": 1,
                                                       "max_steps": 1, "probe_temperature": 0,
                                                       "max_tokens": 1, "probe_model": "model",
                                                       "verbose": False})())

        self.assertEqual(result["http_events"], [])
        env.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
