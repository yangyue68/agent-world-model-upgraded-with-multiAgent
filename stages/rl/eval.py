"""评估脚本：在 held-out 环境集上评测模型的任务完成率。

用法（对比 base 模型 vs 训练后模型，在同一个 eval 环境集上比完成率）：
  python -m ma_upgrade.rl.eval --model_name Qwen/Qwen3-4B --split eval
  python -m ma_upgrade.rl.eval --model_name ma_upgrade/outputs/rl/trained_model --split eval
"""

import json
# os：路径拼接
import os
# shutil：清理评估临时目录
import shutil
# sys：把项目根目录加进模块搜索路径
import sys
# threading：并发写坏任务清单时加锁
import threading
# time：坏任务清单时间戳
import time
# dataclass：声明配置类
from dataclasses import dataclass
# Path：定位项目根目录
from pathlib import Path

# torch：把模型搬到 GPU
import torch
# loguru：结构化日志
from loguru import logger

# 定位 ma_upgrade 目录（eval.py 上溯三级：rl → stages → ma_upgrade）
_MA_ROOT = Path(__file__).resolve().parent.parent.parent
# 项目根（部署目录）= ma_upgrade 的上级，用于 import ma_upgrade 包
_PROJECT_ROOT = _MA_ROOT.parent
# 保证能 import awm（在 ma_upgrade/awm/ 下）和 ma_upgrade
for _p in (str(_MA_ROOT), str(_PROJECT_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# tools_jsonl_load：读 jsonl；normalize_scenario_name：规范化场景名
from lib.interrupt import run_cli, check_cancelled
from lib.interrupt import InterruptibleThreadPoolExecutor as ThreadPoolExecutor, as_completed
from lib.tools import tools_jsonl_load, normalize_scenario_name
from lib.gpt import GPTClient
from lib.db_diff import snapshot_db

# 本包三个模块：环境封装 / reward / rollout（评估复用同一套，只是不做反向传播）
from stages.rl.env import InProcessEnv
from stages.rl.reward import compute_reward, extract_final_answer
from stages.rl.rollout import rollout


@dataclass
class Config:
    """评估配置。"""
    model_name: str = "Qwen/Qwen3-4B"            # 要评估的模型（base=原始模型，或训练后的 trained_model 路径）
    use_modelscope: bool = False                  # True 时从魔搭(ModelScope)下载，False 走 HuggingFace/本地路径
    tasks_path: str = str(_MA_ROOT / "output" / "task" / "gen_tasks_ma.jsonl")     # 任务文件
    envs_path: str = str(_MA_ROOT / "output" / "environment" / "gen_envs_ma.jsonl")      # environment 环境清单（含 env_dir）
    db_path: str = str(_MA_ROOT / "output" / "db" / "gen_db_ma.jsonl")           # 表结构 schema
    sample_path: str = str(_MA_ROOT / "output" / "sample" / "gen_sample_ma.jsonl")   # 样本数据
    verifier_path: str = str(_MA_ROOT / "output" / "verifier" / "gen_verifier_ma.jsonl")  # 兼容旧命令；评估不读取 verifier 代码
    output_dir: str = str(_MA_ROOT / "stages" / "rl" / "output_eval")        # 评估产物目录
    max_steps: int = 10                # 每条 rollout 最大步数
    temperature: float = 1.0           # 采样温度
    max_new_tokens: int = 512          # 单次生成最大 token
    device: str = "cuda"               # 设备
    limit_scenarios: int | None = None  # 只评前 N 个场景
    max_tasks: int | None = None        # 每场景只评前 N 个任务
    num_samples: int = 1                # 每个任务采样几次取平均（评估稳定性）
    start_scenario: int = 0             # eval 集起始下标（跳过的 = 训练用，实现 train/eval 划分）
    allowed_scenarios_path: str | None = None  # 可选：passed 场景名单，只评估名单里 start_scenario 之后的场景
    allowed_tasks_path: str | None = None      # 可选：任务级白名单（gen_rl_ready.jsonl），只评估名单里的 (scenario, task_id)
    start_task: int = 0                        # 任务级切分：起始任务索引（0-based，含）
    end_task: int | None = None                # 任务级切分：结束任务索引（0-based，不含），None=到末尾
    num_workers: int = 1                # 并发评估线程数（=1 串行；>1 并行，受显存限制）
    # ---- LLM 裁判（sql 模式判分）----
    judge_model: str | None = None   # LLM 裁判模型名（如 deepseek-chat），None = 不启用裁判（会全部判 judge_error）
    judge_provider: str = ""         # 裁判 provider（azure/openai），空 = 跟随环境变量 AWM_SYN_LLM_PROVIDER


_write_lock = threading.Lock()


def _build_bad_tasks_path(config) -> str:
    """生成坏任务清单文件名：bad_tasks_{start}_{end}_in_{数据源文件名}.jsonl。"""
    src_name = ""
    if config.allowed_tasks_path:
        src_name = os.path.splitext(os.path.basename(config.allowed_tasks_path))[0]
    start = config.start_task
    end = config.end_task if config.end_task is not None else "end"
    fname = f"bad_tasks_{start}_{end}_in_{src_name}.jsonl" if src_name else f"bad_tasks_{start}_{end}.jsonl"
    return os.path.join(config.output_dir, fname)


def _append_bad_task(path: str, scenario: str, task_id: int, reason: str, type_: str, tool: str | None = None):
    """记录 eval 阶段遇到的 env 5xx / env 启动失败 / judge_error（线程安全）。"""
    entry = {
        "scenario": scenario,
        "task_id": task_id,
        "reason": reason,
        "type": type_,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if tool:
        entry["tool"] = tool
    with _write_lock:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def evaluate(config: Config):
    # 延迟导入 transformers
    from transformers import AutoModelForCausalLM, AutoTokenizer

    logger.info(f"加载模型 {config.model_name} ...")
    # 解析模型实际路径：支持从魔搭(ModelScope)下载，避免直连 HuggingFace
    model_path = config.model_name
    # 注意：model_name 可能是「本地路径」（训练产物 lora_adapter / trained_model），
    # 此时不能当成 ModelScope repo id 去下载（否则 404），直接原地加载即可。
    if config.use_modelscope and not os.path.isdir(model_path):
        from modelscope import snapshot_download  # 魔搭官方下载 SDK
        model_path = snapshot_download(config.model_name)  # 下载到本地缓存，返回本地路径
        logger.info(f"已从 ModelScope 下载模型到 {model_path}")

    # CPU 上 bf16 支持有限（易报错/很慢），自动降级 fp32；GPU 上用 bf16 省显存
    dtype = torch.bfloat16 if config.device.startswith("cuda") else torch.float32

    # 判断 model_path 是「完整模型」还是「LoRA adapter」：
    # 完整模型目录里有 config.json；adapter 目录里只有 adapter_config.json（无 config.json）。
    is_adapter = os.path.exists(os.path.join(model_path, "adapter_config.json")) and \
                 not os.path.exists(os.path.join(model_path, "config.json"))
    if is_adapter:
        # LoRA adapter：先加载 base 模型，再挂 adapter（base 路径记录在 adapter_config.json 里）
        from peft import PeftConfig, PeftModel
        base_path = PeftConfig.from_pretrained(model_path).base_model_name_or_path
        if config.use_modelscope and not os.path.exists(base_path):
            from modelscope import snapshot_download
            base_path = snapshot_download(base_path)
        model = AutoModelForCausalLM.from_pretrained(base_path, dtype=dtype)
        model = PeftModel.from_pretrained(model, model_path)  # 挂 adapter
        logger.info(f"加载 LoRA adapter（base={base_path}）")
    else:
        model = AutoModelForCausalLM.from_pretrained(model_path, dtype=dtype)

    model.to(config.device)  # 搬到目标设备（CPU/GPU）
    model.eval()             # 切到评估模式（关 dropout / 不记梯度）
    tokenizer = AutoTokenizer.from_pretrained(model_path)  # 加载分词器（adapter 目录里也保存了一份）
    if tokenizer.pad_token_id is None:  # 补 pad_token
        tokenizer.pad_token_id = tokenizer.eos_token_id

    # ---- 加载 env 代码目录映射（(scenario, task_id) -> env_dir，任务级）----
    envs_data = tools_jsonl_load(config.envs_path)          # 读 env 环境清单（每行含 env_dir 指向多文件代码包）
    env_dir_map = {}
    for e in envs_data:
        sc = normalize_scenario_name(e.get("scenario", ""))
        env_dir = e.get("env_dir")
        if not env_dir:
            continue
        if not os.path.isabs(env_dir):
            env_dir = os.path.join(str(_MA_ROOT), env_dir)
        tid = e.get("task_id", "")
        try:
            idx = int(tid.split("_", 1)[1]) - 1  # "task_1" -> 0（0-based）
        except (ValueError, IndexError):
            idx = 0
        env_dir_map[(sc, idx)] = env_dir
    scenario_set = {sc for (sc, _) in env_dir_map}  # 有 env 代码的场景集合

    # ---- 加载任务 + sql 模式 database diff ----
    tasks_data = tools_jsonl_load(config.tasks_path)
    sample_data = tools_jsonl_load(config.sample_path)
    sample_map = {
        (normalize_scenario_name(s.get("scenario", "")), s.get("task_id", "")): s
        for s in sample_data
    }
    for sample_entry in sample_map.values():
        if sample_entry.get("initial_snapshot") is None:
            source = sample_entry.get("db_path", "")
            if source and not os.path.isabs(source):
                source = os.path.join(str(_MA_ROOT), source)
            if source and os.path.exists(source):
                sample_entry["initial_snapshot"] = snapshot_db(source)

    # ---- LLM 裁判客户端（sql 模式判分）：每条轨迹都过一次 LLM 裁判 ----
    judge_client = None
    if config.judge_model:
        judge_client = GPTClient(provider=config.judge_provider or "azure", timeout=120)
        logger.info(f"LLM 裁判已启用：model={config.judge_model}")

    # ---- 任务级白名单（gen_rl_ready.jsonl），支持 start_task 切分 ----
    allowed_tasks = None
    if config.allowed_tasks_path:
        ready_data = tools_jsonl_load(config.allowed_tasks_path)
        if config.start_task or config.end_task is not None:
            ready_data = ready_data[config.start_task:config.end_task]  # 本次评估的任务切片 [start, end)
        allowed_tasks = {(normalize_scenario_name(e["scenario"]), e["task_id"]) for e in ready_data}
        logger.info(f"从 {config.allowed_tasks_path} 加载 {len(allowed_tasks)} 个评估任务（切片 [{config.start_task}:{config.end_task}]）")

    # ---- 构建评估样本（从 start_scenario 开始 = held-out 环境集）----
    # 如果指定了 passed 名单，按名单顺序取「start_scenario 之后」的场景（eval = held-out 集），
    # 这样和训练（取名单前 start_scenario 个）不重叠，实现 train/eval 划分。
    allowed_scenarios = None
    if config.allowed_scenarios_path:
        passed_data = tools_jsonl_load(config.allowed_scenarios_path)
        allowed_names = [normalize_scenario_name(e["scenario"]) for e in passed_data]  # 保持名单顺序
        allowed_names = allowed_names[config.start_scenario:]  # 跳过前 start_scenario 个（训练用）
        if config.limit_scenarios is not None:
            allowed_names = allowed_names[: config.limit_scenarios]  # 再截取前 N 个
        allowed_scenarios = set(allowed_names)
        logger.info(f"从 {config.allowed_scenarios_path} 加载 {len(allowed_scenarios)} 个评估场景（跳过前 {config.start_scenario} 个训练场景）")

    eval_items = []
    if allowed_scenarios is None:
        # 没有 allowed 名单时：先过滤出有 env 环境代码的场景，再按 start/limit 切分
        scenarios = [e for e in tasks_data if normalize_scenario_name(e["scenario"]) in scenario_set]
        scenarios = scenarios[config.start_scenario:]
        if config.limit_scenarios is not None:
            scenarios = scenarios[: config.limit_scenarios]
    else:
        scenarios = tasks_data
    for entry in scenarios:
        scenario = normalize_scenario_name(entry["scenario"])
        if allowed_scenarios is not None and scenario not in allowed_scenarios:  # 只评估名单里的场景
            continue
        for task_id, task in enumerate(entry.get("tasks", [])):  # 遍历任务
            if config.max_tasks is not None and task_id >= config.max_tasks:
                break
            if allowed_tasks is not None and (scenario, task_id) not in allowed_tasks:  # 任务级白名单过滤
                continue
            verifier = sample_map.get((scenario, f"task_{task_id + 1}"))
            if verifier is None:
                continue
            eval_items.append((scenario, task_id, task, verifier))
    logger.info(f"评估样本数：{len(eval_items)}")

    # 坏任务清单：eval 阶段遇到 env 5xx / env 启动失败 / judge_error 时记录到这里（供后续针对性修复）
    bad_tasks_path = _build_bad_tasks_path(config)  # bad_tasks_{start}_{end}_in_{数据源}.jsonl

    # ---- 跑评估（支持多线程并行，用 num_workers 控制并发数，受显存限制）----
    # 单条 rollout 的 worker：每个线程独立 tokenizer（避免并发访问 tokenizer 的问题），
    # 共享 model（eval 模式下 model.generate 是线程安全的只读前向）
    def _run_one(scenario, task_id, task, verifier, s):
        check_cancelled()
        # 每个线程独立 tokenizer（线程安全起见，tokenizer 加载很快、内存开销小）
        tk = AutoTokenizer.from_pretrained(model_path)
        if tk.pad_token_id is None:
            tk.pad_token_id = tk.eos_token_id
        run_dir = os.path.join(config.output_dir, f"{scenario}_task_{task_id}_s{s}")
        env = InProcessEnv(
            scenario, env_dir_map.get((scenario, task_id), ""),
            config.db_path, config.sample_path, run_dir,
            task_id=task_id,
            initial_db_source=verifier.get("db_path"),
        )
        try:
            try:
                env.start()
            except Exception as e:
                # 启动失败或中断也必须进入外层 finally 清理。
                _append_bad_task(bad_tasks_path, scenario, task_id, str(e), "env_start_error")
                logger.warning(f"[eval] {scenario} task {task_id} 样本 {s} env 启动失败：{e}")
                return 0
            traj = rollout(model, tk, task, env, config.max_steps, config.temperature,
                    config.max_new_tokens, config.device)

            # env 5xx（运行时环境 bug）→ 记录 env_5xx
            if traj.get("has_5xx"):
                err_details = traj.get("error_details", []) or []
                tool = "|".join(list(dict.fromkeys(t for t, _ in err_details))) if err_details else None
                reason = err_details[-1][1] if err_details else "env_5xx"
                _append_bad_task(bad_tasks_path, scenario, task_id, reason, "env_5xx", tool=tool)

            reward_type, judge_detail = compute_reward(
                env.initial_db_path,
                env.db_file_path,
                initial_snapshot=verifier.get("initial_snapshot"),
                tool_call_names=traj.get("tool_call_names"),
                task=task,
                messages=traj.get("messages"),
                judge_client=judge_client,
                judge_model=config.judge_model,
            )

            # verifier 坏 → 记录 judge_error
            if reward_type == "judge_error":
                _append_bad_task(bad_tasks_path, scenario, task_id, judge_detail or "judge_error", "judge_error")
            # LLM 裁判判定环境错误 → 记录 env_error（同 env_5xx，不怪 agent）
            elif reward_type == "env_error":
                _append_bad_task(bad_tasks_path, scenario, task_id, judge_detail or "env_error", "env_error")

            return 1 if reward_type == "complete" else 0
        except Exception as e:
            # 其他异常（rollout 等）→ 记 0 分继续，保证 eval 对坏环境有容错
            logger.warning(f"[eval] {scenario} task {task_id} 样本 {s} 异常，跳过：{e}")
            return 0
        finally:
            env.close()
            shutil.rmtree(run_dir, ignore_errors=True)  # 清理评估临时目录
            torch.cuda.empty_cache()  # 释放本条 rollout 残留的 KV cache 等空闲显存（第七重治本：防逐条累积 OOM）

    tasks = [(scenario, task_id, task, verifier, s)
             for scenario, task_id, task, verifier in eval_items
             for s in range(config.num_samples)]

    total, complete = 0, 0  # total=总样本数，complete=完成任务数
    if config.num_workers <= 1:
        # 串行（num_workers=1）
        for scenario, task_id, task, verifier, s in tasks:
            complete += _run_one(scenario, task_id, task, verifier, s)
            total += 1
            logger.info(f"评估进度 {total}/{len(tasks)}，累计完成 {complete}")
    else:
        # 并行（num_workers>1）
        with ThreadPoolExecutor(max_workers=config.num_workers) as executor:
            futures = [executor.submit(_run_one, scenario, task_id, task, verifier, s)
                       for scenario, task_id, task, verifier, s in tasks]
            for future in as_completed(futures):  # 哪个先完成先处理哪个
                complete += future.result()
                total += 1
                logger.info(f"评估进度 {total}/{len(tasks)}，累计完成 {complete}")

    # 算最终完成率
    rate = complete / total if total else 0.0
    logger.success("=" * 60)
    logger.success(f"任务完成率：{complete}/{total} = {rate:.2%}")
    logger.success("=" * 60)


def main():
    # 作为脚本直接运行时：解析命令行参数 → 评估
    from simpleArgParser import parse_args
    config = parse_args(Config)
    evaluate(config)


if __name__ == "__main__":
    run_cli(main)

