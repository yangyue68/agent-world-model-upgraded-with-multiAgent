"""Isolated startup checks and rollout execution."""
from pathlib import Path
from loguru import logger
from lib.gpt import GPTClient
from lib.single_file_env import SingleFileEnv
from lib.rollout_runner import run_single_rollout
from .validation import check_database, check_openapi, check_source


def validate_candidate(code, directory, item, config):
    problems = check_source(code)
    if problems:
        return problems
    env = SingleFileEnv(item["scenario"], directory, Path(directory) / "_check", item["db_path"], config.runtime_timeout)
    try:
        env.start()
        tools = env.list_tools() or []
        logger.info(f"[{item['scenario']}/{item['task_id']}] exposed_tools={len(tools)}")
        if not tools:
            return ["environment started but exposed no business tools"]
        problems.extend(check_openapi(item.get("api_spec", {}), env.openapi))
        problems.extend(check_database(env.initial_db_path, env.db_file_path, startup=True))
    except Exception as exc:
        problems.append(f"environment startup/OpenAPI failed: {type(exc).__name__}: {exc}")
    finally:
        env.close()
    return problems


def run_probe(directory, run_dir, item, config):
    env = SingleFileEnv(item["scenario"], directory, run_dir, item["db_path"], config.runtime_timeout)
    try:
        env.start()
        result = run_single_rollout(GPTClient(timeout=config.request_timeout), item["task"], env,
                                    max_steps=config.max_steps, temperature=config.probe_temperature,
                                    max_new_tokens=config.max_tokens, model=config.probe_model,
                                    verbose=config.verbose, strict_format=True)
        result["http_events"] = list(env.events)
        result["database_errors"] = check_database(env.initial_db_path, env.db_file_path)
        return result
    finally:
        env.close()
