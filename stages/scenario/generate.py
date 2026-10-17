"""多 agent 版场景生成（轻量改造 awm/core/scenario.py，流水线第 1 步）。

定位（轻量改造）：
  - 保留原版的核心机制：Self-Instruct 主循环、embedding 去重、few-shot、类别上限、stall 放宽。
  - 补齐两块（对齐 ma_upgrade「两层验证」）：
      ① 确定性规则校验（validators.py）：名称合法 / 描述长度 / 无 GUI 关键词 / 精确去重 / 类别归一化；
      ② 评审重生成闭环（reviewer）：对通过规则+embedding 去重的场景做可仿真性/具体性/多样性评审，
         不合格的带 issue 重生成（不再"丢弃式过滤"）。

流程：
  加载种子 → classifier 给种子打分(得到 few-shot 池) → embedding 初始化
  → Self-Instruct 主循环（凑够 target_count）:
      生成(generator, few-shot + 类别引导) → 规则校验(validators)
      → embedding 去重(确定性) → 评审(reviewer) → 不合格带意见重生成
      → 合格加入池子

运行方式（在项目根目录 ma_upgrade/ 下）：
  python -m stages.scenario.generate --target_count 150
"""

# ---- 标准库 ----
import json                # 写统计报告（report.json）
import os                  # 读环境变量（模型名 / embedding 凭证）
import random              # few-shot 抽样、焦点类别打乱、评审抽样
import sys                 # 把项目根目录加到 sys.path
from dataclasses import dataclass   # Config 配置类
from pathlib import Path   # 路径处理（输出文件、report 路径）

# ---- 第三方库 ----
import numpy as np         # embedding 向量矩阵运算
from loguru import logger  # 日志

# 确保项目根目录在 sys.path 上，便于 import awm / ma_upgrade
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# 默认输出文件用「绝对路径」，与 CWD 无关
_DEFAULT_INPUT = str(_PROJECT_ROOT / "stages" / "scenario" / "seed_scenario.jsonl")
_DEFAULT_OUTPUT = str(_PROJECT_ROOT / "output" / "scenario" / "gen_scenario_ma.jsonl")

# ---- 复用原版 awm 的组件 ----
from lib.interrupt import run_cli, check_cancelled
from lib.gpt import GPTClient          # LLM 客户端（generator/classifier 用）
from stages.scenario.prompts import (              # 生成 / 分类用的提示词与类别表
    SCENARIO_GENERATION_SYSTEM_PROMPT,
    SCENARIO_GENERATION_USER_PROMPT_TEMPLATE,
    SCENARIO_FOCUSED_GENERATION_USER_PROMPT_TEMPLATE,
    SCENARIO_FOCUS_CATEGORIES,
    SCENARIO_CLASSIFICATION_SYSTEM_PROMPT,
    SCENARIO_CLASSIFICATION_USER_PROMPT_TEMPLATE,
)
from lib.tools import tools_jsonl_load, tools_jsonl_save, tools_robust_json_loads   # jsonl 读写 + 容错 JSON 解析
from openai import OpenAI              # embedding 客户端（去重用）

# ---- ma_upgrade 自身的组件 ----
from lib.orchestrator import Agent   # 多 agent 封装（name + system prompt + client）
from stages.scenario.prompts import (   # 评审提示词
    SCENARIO_REVIEW_SYSTEM_PROMPT,
    SCENARIO_REVIEW_USER_PROMPT,
)
from stages.scenario.validators import (   # 确定性规则校验
    validate_scenarios, dedup_scenarios, normalize_categories,
)


@dataclass
class Config:
    """场景生成的配置项（对应原 scenario.py 的 Config）。"""
    input_path: str = _DEFAULT_INPUT                       # 种子场景文件（seed_scenario.jsonl）
    output_path: str = _DEFAULT_OUTPUT          # 输出文件（默认绝对路径）
    target_count: int = 500                    # 目标场景总数
    num_parallel_requests: int = 2              # 每轮并行请求数
    scenarios_per_request: int = 5             # 每个请求生成的场景数
    num_few_shot: int = 8                       # few-shot 示例数
    model: str = "your-llm-model-name"          # 模型名（占位，必须被覆盖）
    temperature: float = 1.0                    # 生成温度
    max_retry: int = 1                          # 评审重生成循环最大轮次
    diversity_check_batch: int = 5             # 去重/评审的批次大小，配合pending缓存区使用
    skip_classification: bool = False           # 是否跳过种子分类
    embedding_similarity_threshold: float = 0.9   # embedding 相似度阈值（超过视为重复），值越高，代表卡的越松
    embedding_warning_threshold: float = 0.85      # embedding 相似度警告阈值
    embedding_model: str = "text-embedding-v4"  # embedding 模型
    embedding_batch_size: int = 5              # embedding 批大小
    max_per_category: int = 80                  # 每个类别的最大场景数
    max_stall_iterations: int = 1              # 连续无进展的最大迭代数
    max_total_iterations: int = 30            # 最大总迭代数
    resume: bool = True                         # 是否断点续跑（默认续跑）
    max_tokens= 50000

    def pre_process(self):
        """开工前检查：处理 resume、输入文件、输出目录、模型名。"""
        # resume 默认 True：output 有产物 → 真断点续跑；无产物 → 读种子（input）重新生成
        need_input = not (self.resume and os.path.exists(self.output_path))
        if need_input and not os.path.exists(self.input_path):
            raise FileNotFoundError(f"种子文件不存在: {self.input_path}")
        out_dir = os.path.dirname(self.output_path)
        if out_dir and not os.path.exists(out_dir):
            os.makedirs(out_dir, exist_ok=True)
        if os.environ.get("AWM_SYN_OVERRIDE_MODEL"):
            self.model = os.environ["AWM_SYN_OVERRIDE_MODEL"]
        if os.environ.get("EMBEDDING_OPENAI_MODEL"):  # 环境变量优先指定 embedding 模型名（否则用 --embedding_model / 默认值）
            self.embedding_model = os.environ["EMBEDDING_OPENAI_MODEL"]
        assert self.model != "your-llm-model-name", "请设置模型名（--model 或 AWM_SYN_OVERRIDE_MODEL）"


def build_agents(config: Config) -> dict:
    """构建 3 个 agent：生成 / 分类 / 评审。

    - generator 温度 1.0（config.temperature）：生成场景，偏多样；生成慢，给更长超时。
    - classifier 温度 0.0：给种子打分，要稳定。
    - reviewer 温度 0.0：评审可仿真性/具体性/多样性，要稳定。
    """
    return {
        # 生成 agent：温度高（偏多样），单次生成量大，给最长超时（600s）
        "generator": Agent(
            "scenario_generator", SCENARIO_GENERATION_SYSTEM_PROMPT,
            client=GPTClient(timeout=600), model=config.model,
            temperature=config.temperature, max_tokens=config.max_tokens,
        ),
        # 分类 agent：温度 0（稳定），输出短
        "classifier": Agent(
            "scenario_classifier", SCENARIO_CLASSIFICATION_SYSTEM_PROMPT,
            model=config.model, temperature=0.0, max_tokens=config.max_tokens,
        ),
        # 评审 agent：温度 0（稳定），输出中等
        "reviewer": Agent(
            "scenario_reviewer", SCENARIO_REVIEW_SYSTEM_PROMPT,
            model=config.model, temperature=0.0, max_tokens=config.max_tokens,
        ),
    }


class ScenarioSynthesizer:
    """Self-Instruct 场景合成器（保留原版骨架 + 两层验证）。"""

    def __init__(self, config: Config, agents: dict):
        self.config = config
        self.agents = agents
        self._is_resuming = False                     # 是否真断点续跑（output 已有产物）
        self.existing_scenarios: list[dict] = []     # 已有（种子）场景
        self.generated_scenarios: list[dict] = []    # 新生成的场景
        self.all_names: set[str] = set()             # 所有场景名（去重用）
        self.high_suitability: list[dict] = []       # 高适合度场景（few-shot 抽样池）
        # embedding 客户端：优先用专用环境变量，否则回退 OpenAI 默认地址
        self.embedding_client = OpenAI(
            api_key=os.environ.get("EMBEDDING_OPENAI_API_KEY"),
            base_url=os.environ.get("EMBEDDING_OPENAI_BASE_URL", "https://api.openai.com/v1"),
        )
        self.embeddings: np.ndarray | None = None    # 场景 embedding 矩阵
        self.embedding_dim: int = 3072               # embedding 维度
        self.category_counts: dict[str, int] = {}    # 各类别场景数量统计
        # 统计报告（吸收 Codex 的 report.json 优点）
        self.stats = {
            "generated": 0,              # 生成总数
            "rule_rejected": 0,          # 规则校验剔除
            "classification_rejected": 0,  # 分类剔除（medium/low）
            "duplicate_rejected": 0,     # embedding 去重剔除
            "review_rejected": 0,        # 评审剔除
            "review_failures": 0,        # 评审失败次数
            "regenerated": 0,            # 重生成数量
        }

    # ---------------- 加载 / 分类 / 过滤 ----------------
    def load_existing(self):
        # resume 默认 True：output 有产物 → 真断点续跑；无产物 → 读种子重新生成
        self._is_resuming = self.config.resume and os.path.exists(self.config.output_path)
        if self._is_resuming:
            # 真断点续跑：output 已有产物，从 output 加载
            load_path = self.config.output_path
            logger.info(f"断点续跑：从 output 加载 {load_path}")
        else:
            # 全新生成（或 resume 但 output 无产物）：读种子
            load_path = self.config.input_path
            if self.config.resume:
                logger.warning(f"output 无产物，改为读取种子重新生成：{load_path}")
        # 读取 jsonl 文件，得到已有场景列表
        self.existing_scenarios = tools_jsonl_load(load_path)
        logger.info(f"Loaded {len(self.existing_scenarios)} existing scenarios from {load_path}")
        # 把已有场景名（小写去空格）登记进 all_names，供后续去重
        for s in self.existing_scenarios:
            self.all_names.add(s["name"].lower().strip())
        return self.existing_scenarios

    def classify_scenarios(self, scenarios: list[dict]) -> list[dict]:
        """用 classifier agent 给场景打分，打分后直接删除 low/medium，只返回 high 场景。"""
        to_classify = [s for s in scenarios if "suitability_level" not in s]
        already_classified = [s for s in scenarios if "suitability_level" in s]
        classified = []
        if to_classify:
            logger.info(f"Classifying {len(to_classify)} scenarios...")
            requests = []
            for s in to_classify:
                user = SCENARIO_CLASSIFICATION_USER_PROMPT_TEMPLATE.format(
                    name=s["name"], description=s.get("description", "No description")
                )
                requests.append(self.agents["classifier"].build_request(user))

            responses = self.agents["classifier"].client.batch_chat_completion(requests, progress_bar=True)

            for scenario, response in zip(to_classify, responses):
                try:
                    result = tools_robust_json_loads(response)
                    if isinstance(result, dict):
                        scenario["categories"] = normalize_categories(result.get("categories", []))
                        scenario["suitability_score"] = result.get("suitability_score", 5)
                        scenario["suitability_level"] = result.get("suitability_level", "medium")
                        scenario["suitability_reasoning"] = result.get("reasoning", "")
                        scenario["simulatable_features"] = result.get("simulatable_features", [])
                        scenario["non_simulatable_features"] = result.get("non_simulatable_features", [])
                    else:
                        scenario["categories"] = []
                        scenario["suitability_score"] = 5
                        scenario["suitability_level"] = "medium"
                        scenario["suitability_reasoning"] = "Classification failed"
                except Exception as e:
                    logger.warning(f"Failed to parse classification for {scenario['name']}: {e}")
                    scenario["categories"] = []
                    scenario["suitability_score"] = 5
                    scenario["suitability_level"] = "medium"
                    scenario["suitability_reasoning"] = f"Classification error: {str(e)}"
                classified.append(scenario)
        else:
            logger.info("All scenarios already classified")

        all_classified = already_classified + classified
        # 打分后直接删除 low/medium 种子，只返回 high（不合格种子不参与后续任何流程，也不占 target 名额）
        high_scenarios = [s for s in all_classified if s.get("suitability_level") == "high"]
        removed = len(all_classified) - len(high_scenarios)
        self.stats["classification_rejected"] = removed
        logger.success(f"Classification complete: 保留 High={len(high_scenarios)} 个，删除 Low/Medium={removed} 个")
        return high_scenarios

    def filter_by_suitability(self):
        # low/medium 已在 classify_scenarios 里删除，这里只需设置 few-shot 池 + 同步去重名单
        self.high_suitability = [s for s in self.existing_scenarios if s.get("suitability_level") == "high"]
        self.all_names = {s["name"].lower().strip() for s in self.existing_scenarios}
        logger.info(f"种子筛选: 保留 High={len(self.high_suitability)} 个")

    # ---------------- embedding 去重（保留原版确定性逻辑） ----------------
    def _scenario_to_text(self, scenario: dict) -> str:
        # 把一个场景拼成一段文本，供 embedding 模型编码（去重时用文本语义判断相似度）
        # 拼接顺序：名称 + 描述（截断 500 字符）+ 类别 + 可仿真特性（前 5 个）
        name = scenario.get("name", "")
        desc = scenario.get("description", "")[:500]
        cats = ", ".join(scenario.get("categories", []))
        features = ", ".join(scenario.get("simulatable_features", [])[:5])
        return f"{name}: {desc} Categories: {cats}. Features: {features}"

    def compute_embeddings(self, scenarios: list[dict]) -> np.ndarray:
        # 空列表返回 0×dim 的空矩阵（避免后续 numpy 运算报错）
        if not scenarios:
            return np.array([]).reshape(0, self.embedding_dim)
        # 先把每个场景拼成文本，再分小批调用 embedding 模型（控制单次请求大小）
        texts = [self._scenario_to_text(s) for s in scenarios]
        all_embeddings = []
        batch_size = self.config.embedding_batch_size
        for i in range(0, len(texts), batch_size):
            check_cancelled()
            batch_texts = texts[i:i + batch_size]
            response = self.embedding_client.embeddings.create(
                input=batch_texts, model=self.config.embedding_model
            )
            # 收集每个文本对应的向量
            all_embeddings.extend([item.embedding for item in response.data])
        # 转成二维 numpy 矩阵（形状 [N, dim]），方便后续批量算相似度
        return np.array(all_embeddings)

    def initialize_embeddings(self):
        # 初始化 embedding 矩阵：把「种子 + 已生成」全部编码，作为去重的比对基准
        all_scenarios = self.existing_scenarios + self.generated_scenarios
        if not all_scenarios:
            # 没有任何场景时，初始化为空矩阵
            self.embeddings = np.array([]).reshape(0, self.embedding_dim)
            return
        logger.info(f"Computing embeddings for {len(all_scenarios)} existing scenarios...")
        self.embeddings = self.compute_embeddings(all_scenarios)
        logger.success(f"Initialized embedding matrix: {self.embeddings.shape}")
        # 同时初始化各类别的数量统计（供后续 max_per_category 上限判断用）
        self._update_category_counts(all_scenarios)

    def _update_category_counts(self, scenarios: list[dict]):
        # 累计每个类别出现的次数（category_counts 用于类别上限判断）
        for s in scenarios:
            for cat in s.get("categories", []):
                self.category_counts[cat] = self.category_counts.get(cat, 0) + 1

    def cosine_similarity(self, vec1: np.ndarray, vec2: np.ndarray) -> float:
        # 两个向量的余弦相似度 = 内积 / (模长之积)，值域 [-1, 1]，越接近 1 越相似
        norm1 = np.linalg.norm(vec1)
        norm2 = np.linalg.norm(vec2)
        # 零向量保护：任一模长为 0 则相似度直接算 0，避免除零报错
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return float(np.dot(vec1, vec2) / (norm1 * norm2))

    def max_similarity_to_pool(self, embedding: np.ndarray) -> tuple[float, int]:
        # 计算单个向量与「整个已有池子」的最大相似度及其下标
        # 返回 (最大相似度, 最相似向量的下标)；池子为空时返回 (0.0, -1)
        if self.embeddings is None or len(self.embeddings) == 0:
            return 0.0, -1
        # 先把矩阵按行归一化（模长为 0 的行替换成极小值，避免除零）
        norms = np.linalg.norm(self.embeddings, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1e-10, norms)
        # 向量化点积：一次性算出与池子里所有向量的余弦相似度
        sims = np.dot(self.embeddings / norms, embedding / (np.linalg.norm(embedding) or 1e-10))
        idx = int(np.argmax(sims))   # 找最相似向量的下标
        return float(sims[idx]), idx

    def check_embedding_diversity(self, candidates: list[dict]) -> list[dict]:
        # ② embedding 语义去重（确定性、免费）：判断候选是否与已有/批内场景重复
        if not candidates:
            return []
        logger.info(f"Checking embedding diversity for {len(candidates)} candidates...")
        # 先把所有候选编码成向量
        candidate_embeddings = self.compute_embeddings(candidates)
        kept = []                 # 最终保留的候选
        kept_embeddings = []      # 已保留候选的向量（用于批内互相比对）
        all_scenarios = self.existing_scenarios + self.generated_scenarios
        for candidate, emb in zip(candidates, candidate_embeddings):
            # 与「已有池子」的最大相似度
            max_sim, most_similar_idx = self.max_similarity_to_pool(emb)
            # 与「本批已保留候选」的最大相似度（防止同一批里互相重复）
            batch_max_sim = 0.0
            for kept_emb in kept_embeddings:
                batch_max_sim = max(batch_max_sim, self.cosine_similarity(emb, kept_emb))
            # 取两者中更大的那个作为最终相似度
            overall_max_sim = max(max_sim, batch_max_sim)
            # 超过「硬阈值」：视为重复，直接丢弃
            if overall_max_sim > self.config.embedding_similarity_threshold:
                self.stats["duplicate_rejected"] += 1
                similar_name = "batch candidate"
                if 0 <= most_similar_idx < len(all_scenarios):
                    similar_name = all_scenarios[most_similar_idx].get("name", "Unknown")
                logger.debug(f"REJECT (sim={overall_max_sim:.3f}): {candidate['name']} ~= {similar_name}")
                continue
            # 介于「警告阈值」和「硬阈值」之间：相似但不至于重复，再查类别是否已满
            if overall_max_sim > self.config.embedding_warning_threshold:
                over_limit = False
                for cat in candidate.get("categories", []):
                    # 某个类别已达到 max_per_category 上限 → 丢弃（避免类别失衡）
                    if self.category_counts.get(cat, 0) >= self.config.max_per_category:
                        logger.debug(f"REJECT (category limit): {candidate['name']} - {cat}")
                        over_limit = True
                        break
                if over_limit:
                    continue
            # 通过：保留，并把其向量加入批内比对池
            kept.append(candidate)
            kept_embeddings.append(emb)
        logger.info(f"Embedding diversity check: kept {len(kept)}/{len(candidates)}")
        return kept

    def add_to_embedding_pool(self, scenarios: list[dict]):
        # 把新入库的场景向量追加到 embedding 矩阵（保持去重基准最新）
        if not scenarios:
            return
        new_embeddings = self.compute_embeddings(scenarios)
        if self.embeddings is None or len(self.embeddings) == 0:
            # 池子还是空的：直接赋值
            self.embeddings = new_embeddings
        else:
            # 池子已有内容：按行堆叠追加
            self.embeddings = np.vstack([self.embeddings, new_embeddings])
        # 同步更新类别计数（新增场景的类别也要计入 max_per_category 判断）
        self._update_category_counts(scenarios)

    def run_global_diversity_check(self) -> dict:
        # 全局多样性体检：抽样计算所有场景两两相似度，判断整体是否过密
        # 场景太少（<10）时不做，返回 not_enough_data
        if self.embeddings is None or len(self.embeddings) < 10:
            return {"status": "not_enough_data"}
        n = len(self.embeddings)
        # 最多抽 200 个样本，避免全量两两比对太慢
        sample_size = min(200, n)
        indices = random.sample(range(n), sample_size)
        sample_embeddings = self.embeddings[indices]
        # 归一化后点积得到两两余弦相似度矩阵
        norms = np.linalg.norm(sample_embeddings, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1e-10, norms)
        normalized = sample_embeddings / norms
        similarity_matrix = np.dot(normalized, normalized.T)
        # 取上三角（去掉自相似 = 对角线），统计全局相似度指标
        upper_tri = similarity_matrix[np.triu_indices(sample_size, k=1)]
        stats = {
            "num_scenarios": n,                          # 场景总数
            "sample_size": sample_size,                  # 抽样数
            "mean_similarity": float(np.mean(upper_tri)),   # 平均相似度
            "max_similarity": float(np.max(upper_tri)),     # 最大相似度
            "high_similarity_pairs": int(np.sum(upper_tri > self.config.embedding_similarity_threshold)),  # 高相似对数
            "category_distribution": dict(sorted(self.category_counts.items(), key=lambda x: -x[1])),       # 类别分布（按数量降序）
        }
        # 找出已超过类别上限的类别（失衡预警）
        over_rep = [c for c, cnt in self.category_counts.items() if cnt > self.config.max_per_category]
        stats["over_represented_categories"] = over_rep
        logger.info(
            f"Global diversity: mean_sim={stats['mean_similarity']:.3f}, "
            f"high_pairs={stats['high_similarity_pairs']}"
        )
        return stats

    # ---------------- few-shot（保留原版逻辑） ----------------
    def get_few_shot_examples(self, num_examples: int, prioritize_diverse: bool = True) -> list[dict]:
        # 从 high_suitability 池里挑 few-shot 示例（默认优先保证类别多样性）
        pool = self.high_suitability
        if not pool:
            # high 池为空时兜底：用全部已有场景
            logger.warning("No suitable scenarios found, using all existing scenarios")
            pool = self.existing_scenarios
        # 不要求多样性 / 池子不够大时：直接随机抽
        if not prioritize_diverse or len(pool) <= num_examples:
            return random.sample(pool, min(num_examples, len(pool)))
        # 要求多样性：按第一个类别把场景分组
        categorized: dict[str, list[dict]] = {}
        uncategorized = []
        for scenario in pool:
            categories = scenario.get("categories", [])
            if categories:
                categorized.setdefault(categories[0], []).append(scenario)
            else:
                uncategorized.append(scenario)
        selected = []
        if categorized:
            # 每个类别配额 = 目标数 / 类别数，尽量每个类别都抽一点（避免示例全是一个类别）
            per_category = max(1, num_examples // len(categorized))
            for scenarios in categorized.values():
                if scenarios and len(selected) < num_examples:
                    to_take = min(per_category, len(scenarios), num_examples - len(selected))
                    selected.extend(random.sample(scenarios, to_take))
        # 配额没抽满：从「未分类 + 未被选中的」里补足
        remaining = num_examples - len(selected)
        if remaining > 0:
            remaining_pool = uncategorized + [s for s in pool if s not in selected]
            if remaining_pool:
                selected.extend(random.sample(remaining_pool, min(remaining, len(remaining_pool))))
        # 打乱顺序，避免示例总是固定顺序
        random.shuffle(selected)
        return selected[:num_examples]

    def format_examples(self, examples: list[dict]) -> str:
        # 把 few-shot 示例格式化成编号文本（每个只保留 name + 截断的 description）
        formatted = []
        for i, ex in enumerate(examples, 1):
            formatted.append(f'{i}. {{"name": "{ex["name"]}", "description": "{ex["description"][:500]}..."}}')
        return "\n\n".join(formatted)

    # ---------------- 生成（generator agent） ----------------
    def _build_generation_prompt(
        self,
        examples: list[dict],
        num_to_generate: int,
        focus_categories: list[str] | None = None,
        avoid_scenarios: list[dict] | None = None,
    ) -> str:
        # 组装生成 prompt：few-shot 示例 + 可选「避开清单」+ 可选「焦点类别」
        examples_text = self.format_examples(examples)
        # 如果有要避开的场景（如上一批被评审拒绝的），追加到 prompt 里
        if avoid_scenarios:
            avoid_text = "\n".join(
                f"- {s['name']}: {s.get('description', '')[:150]}"
                for s in avoid_scenarios
            )
            examples_text += "\n\n## Also avoid generating scenarios similar to these:\n" + avoid_text
        # 指定了焦点类别 → 用「聚焦生成」模板（引导 LLM 往这些类别生成）
        if focus_categories:
            return SCENARIO_FOCUSED_GENERATION_USER_PROMPT_TEMPLATE.format(
                num_examples=len(examples),
                examples=examples_text,
                focus_categories=", ".join(focus_categories),
                num_to_generate=num_to_generate,
            )
        # 没指定焦点类别 → 用通用生成模板
        return SCENARIO_GENERATION_USER_PROMPT_TEMPLATE.format(
            num_examples=len(examples),
            examples=examples_text,
            num_to_generate=num_to_generate,
        )

    def _parse_generation_response(self, response: str) -> list[dict]:
        # 解析 generator 返回的 JSON：只提取 name + description 两个字段
        try:
            parsed = tools_robust_json_loads(response)
            # 期望返回 JSON 数组；不是数组则视为失败
            if not isinstance(parsed, list):
                return []
            valid = []
            for item in parsed:
                # 只保留同时含 name 和 description 的 dict，并做字符串规整
                if isinstance(item, dict) and "name" in item and "description" in item:
                    valid.append({"name": str(item["name"]).strip(), "description": str(item["description"]).strip()})
            return valid
        except Exception as e:
            # 解析失败返回空列表（由上层决定重试或跳过）
            logger.warning(f"Failed to parse generation response: {e}")
            return []

    def _generate_batch(self) -> list[dict]:
        # 主循环每轮的核心：并行生成一批候选场景
        num_requests = self.config.num_parallel_requests#每轮并发发送的请求数
        per_request = self.config.scenarios_per_request#每个请求要求生产的场景数
        # 取最近 20 个已生成场景作为「避开清单」，防止新生成的和刚生成的重样
        avoid_scenarios = self.generated_scenarios[-20:] if self.generated_scenarios else []
        # 复制并打乱焦点类别表（后续按轮次轮转，避免每轮都聚焦同一批类别）
        shuffled_categories = SCENARIO_FOCUS_CATEGORIES.copy()
        random.shuffle(shuffled_categories)
        requests = []
        for i in range(num_requests):
            # 每个请求独立抽 few-shot 示例（保证各请求之间也有差异）
            examples = self.get_few_shot_examples(self.config.num_few_shot, prioritize_diverse=True)
            random.shuffle(examples)
            # 轮转式挑选焦点类别：从 shuffled_categories 里连续取 3 组
            focus_idx = i % len(shuffled_categories)
            focus_cats = []
            for j in range(3):
                cat_idx = (focus_idx + j) % len(shuffled_categories)
                focus_cats.extend(shuffled_categories[cat_idx])
            # 70% 概率使用焦点类别（引导多样性），30% 概率自由生成
            use_focus = random.random() > 0.3
            prompt = self._build_generation_prompt(
                examples, per_request,
                focus_categories=focus_cats if use_focus else None,
                avoid_scenarios=avoid_scenarios,
            )
            requests.append(self.agents["generator"].build_request(prompt))
        logger.info(f"Sending {num_requests} parallel requests, each generating {per_request} scenarios...")
        # 并行调用 generator（温度 1.0，偏多样）
        responses = self.agents["generator"].client.batch_chat_completion(requests, progress_bar=True)
        all_valid = []
        seen = set()
        # 解析每个响应，按 name 做「批内去重 + 与已有去重」
        for response in responses:
            for item in self._parse_generation_response(response):
                name_lower = item["name"].lower().strip()
                if name_lower not in seen and name_lower not in self.all_names:
                    seen.add(name_lower)
                    all_valid.append(item)
        # 累计生成总数（含后续可能被规则/去重/评审剔除的）
        self.stats["generated"] += len(all_valid)
        logger.success(f"Generated {len(all_valid)} unique valid scenarios")
        return all_valid

    # ---------------- 评审重生成（reviewer + 带意见重生成） ----------------
    def _review(self, candidates: list[dict], include_existing: bool = True) -> tuple[list[dict], list[dict]]:
        """用 reviewer 评审候选场景，返回 (保留, 被拒带 issue)。
        include_existing：是否把已有场景作为评审的「去重参照」。种子评审时传 False（种子是最早的场景，
        没有「已有场景」可对照，去重已由 embedding 去重完成）。"""
        if not candidates:
            return [], []
        # 抽样已有场景（最多 100 个）作为评审的「去重参照」
        if include_existing:
            existing_sample = self.existing_scenarios + self.generated_scenarios
        else:
            existing_sample = []   # 种子评审：无已有场景参照，reviewer 只判可仿真性 + 具体性
        if len(existing_sample) > 100:
            existing_sample = random.sample(existing_sample, 100)
        existing_text = "\n".join(
            f"- {s['name']}: {s.get('description', '')}"
            for s in existing_sample
        )
        # 候选场景文本（带编号，便于 reviewer 回填 index/name）
        candidates_text = "\n".join(
            f"{i}. {c['name']}: {c.get('description', '')}"
            for i, c in enumerate(candidates)
        )
        prompt = SCENARIO_REVIEW_USER_PROMPT.format(
            existing_scenarios=existing_text, candidates=candidates_text
        )
        try:
            # 调用 reviewer（温度 0.0，输出稳定），返回 JSON 评审结果
            result = self.agents["reviewer"].run_json(prompt)
            if not isinstance(result, dict):
                # 结果格式不对：评审失败，全部丢弃（未确认合格的场景不进入产物）
                self.stats["review_failures"] += 1
                return [], []
            results = result.get("results", [])
            if not isinstance(results, list):
                self.stats["review_failures"] += 1
                return [], []
        except Exception as e:
            # 评审异常：同样全部丢弃（未确认合格的场景不进入产物）
            logger.error(f"评审失败（全部丢弃）: {e}")
            self.stats["review_failures"] += 1
            return [], []

        kept: list[dict] = []
        rejected: list[dict] = []
        reviewed_names = set()
        # 按 reviewer 给的回执逐条匹配：pass=true 保留，pass=false 带 issue 进被拒列表
        for v in results:
            name = (v.get("name") or "").strip().lower()
            reviewed_names.add(name)
            matched = next((c for c in candidates if c["name"].strip().lower() == name), None)
            if matched is None:
                continue
            if v.get("pass"):
                kept.append(matched)
            else:
                # 把评审给的具体 issue 存到场景上，供后续带意见重生成使用
                matched["_reject_reason"] = v.get("issue", "未说明原因")
                rejected.append(matched)
        # reviewer 没提到名字的候选：视为漏评，默认保留（宽容处理）
        for c in candidates:
            if c["name"].strip().lower() not in reviewed_names:
                kept.append(c)
        self.stats["review_rejected"] += len(rejected)
        logger.info(f"评审: kept {len(kept)}/{len(candidates)}, rejected {len(rejected)}")
        return kept, rejected

    def _regenerate(self, rejected: list[dict]) -> list[dict]:
        """对评审被拒的场景，带 issue 重新生成替代场景。"""
        if not rejected:
            return []
        num = len(rejected)   # 需要生成几个替代场景（数量 = 被拒数量）
        examples = self.get_few_shot_examples(self.config.num_few_shot, prioritize_diverse=True)
        # 把被拒场景的 name + reject 原因组成「避开清单」，塞进 prompt
        avoid = [{"name": r["name"], "description": r.get("_reject_reason", "")} for r in rejected]
        prompt = self._build_generation_prompt(examples, num, focus_categories=None, avoid_scenarios=avoid)
        # 额外追加一段「带意见」的反馈，明确告诉 generator 上一批为什么被拒
        feedback = "\n\n【上一批场景被评审拒绝，请生成替代场景，避开以下问题】\n" + "\n".join(
            f"- {r['name']}: {r.get('_reject_reason', '')}" for r in rejected
        )
        prompt += feedback
        # 单请求生成替代场景（量小，不用并行）
        responses = self.agents["generator"].client.batch_chat_completion(
            [self.agents["generator"].build_request(prompt)], progress_bar=False
        )
        out = []
        seen = set()
        # 解析重生成结果，按 name 去重
        for response in responses:
            for item in self._parse_generation_response(response):
                name_lower = item["name"].lower().strip()
                if name_lower not in seen and name_lower not in self.all_names:
                    seen.add(name_lower)
                    out.append(item)
        self.stats["regenerated"] += len(out)
        logger.info(f"重生成 {len(out)} 个替代场景（替换 {num} 个被拒场景）")
        return dedup_scenarios(out)

    def _gate_prefix(self, scenarios: list[dict]) -> list[dict]:
        """前三道质量把关：① 规则校验 → ② suitability 打分 → ③ embedding 去重。返回通过的场景。"""
        if not scenarios:
            return []
        # ① 规则校验（确定性，免费）：名称/描述长度/GUI 关键词
        kept, dropped = validate_scenarios(scenarios)
        self.stats["rule_rejected"] += len(dropped)
        if not kept:
            return []
        # ② suitability 打分（classifier），删除 low/medium，只保留 high
        kept = self.classify_scenarios(kept)
        if not kept:
            return []
        # ③ embedding 去重（确定性，免费）
        kept = self.check_embedding_diversity(kept)
        return kept

    def _quality_gate(self, scenarios: list[dict], allow_regenerate: bool = True) -> list[dict]:
        """四道质量把关：① 规则校验 → ② suitability 打分 → ③ embedding 去重 → ④ LLM 评审。
        allow_regenerate：评审 reject 后是否重生成（种子不重生成、直接丢弃；新场景带 issue 重生成）。"""
        # 前 3 道关：规则 + 打分 + 去重
        kept = self._gate_prefix(scenarios)
        if not kept:
            return []
        # ④ LLM 评审（reject 的：新场景带 issue 重生成；种子直接丢弃）
        if allow_regenerate:
            kept = self._review_and_regenerate(kept)
        else:
            kept, _ = self._review(kept, include_existing=False)
        return kept

    def _review_and_regenerate(self, candidates: list[dict]) -> list[dict]:
        # ④ 评审 + 带意见重生成闭环：先评审 → 被拒的带 issue 重生成 → 重生成的重新过前 3 道关 + 再评审
        kept, rejected = self._review(candidates)
        for _ in range(self.config.max_retry):
            if not rejected:
                break   # 没有需要重生成的，提前结束
            regen = self._regenerate(rejected)   # 带意见重生成替代场景
            if not regen:
                break   # 重生成失败/没产出，结束
            # 重生成的替代场景重新过「规则 + 打分 + 去重」前三道关（保证质量），再评审
            regen_kept = self._gate_prefix(regen)
            kept2, rejected = self._review(regen_kept)   # 对替代场景再评审
            kept.extend(kept2)
        return kept


    # ---------------- 入库 / 保存 ----------------
    def add_scenarios(self, scenarios: list[dict]):
        # 按 name（小写去空格）去重后入库，避免重复
        for s in scenarios:
            name_lower = s["name"].lower().strip()
            if name_lower not in self.all_names:
                self.generated_scenarios.append(s)
                self.all_names.add(name_lower)
                # 增量落盘：每入一个场景就追加写一行到 output（中断/断网不丢已完成的）
                with open(self.config.output_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(s, ensure_ascii=False) + "\n")

    def save_progress(self):
        # 场景已通过 add_scenarios 增量落盘（追加写），这里只写统计报告
        all_scenarios = self.existing_scenarios + self.generated_scenarios
        report_path = str(Path(self.config.output_path).with_suffix(".report.json"))
        # 报告：各环节剔除统计 + 原有场景数 / 本次新生成数 / 最终总数
        report = dict(self.stats)
        report["original_scenarios"] = len(self.existing_scenarios)   # 原来已有（种子）场景数
        # 本次新生成的合格场景数 = 生成总数 + 重生成数 - 各环节剔除数 - 评审失败数
        report["generated_passed_scenarios"] = (
            self.stats["generated"]
            + self.stats["regenerated"]
            - self.stats["rule_rejected"]
            - self.stats["classification_rejected"]
            - self.stats["duplicate_rejected"]
            - self.stats["review_rejected"]
            - self.stats["review_failures"]
        )
        report["total_scenarios"] = len(all_scenarios)  # 最终场景总数（原有 + 新生成）
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        logger.info(f"场景已增量落盘，当前共 {len(all_scenarios)} 个；统计写入 {report_path}")

    # ---------------- 主循环 ----------------
    def run(self):
        # ① 加载场景（output 有产物 → 真断点续跑；无产物 → 读种子重新生成）
        self.load_existing()

        # ② 种子质量把关：未跳过分类 且 非真断点续跑时，对种子过四道关（规则 + 打分 + 去重 + 评审）
        if not self.config.skip_classification and not self._is_resuming:
            #需要进行场景分类，且不是真断点续跑，也就是对种子场景进行分类
            logger.info("Step 1: 种子质量把关（规则 + 打分 + 去重 + 评审）...")

            # 种子也过四道关：规则校验、suitability 打分（删 low/medium）、embedding 去重、LLM 评审（不合格丢弃，不重生成）
            self.existing_scenarios = self._quality_gate(self.existing_scenarios, allow_regenerate=False)
        # ③ 同步 few-shot 池 + 去重名单（low/medium 已在分类阶段删除）
        self.filter_by_suitability()

        if not self.config.skip_classification and not self._is_resuming:
            # 保存筛选后的种子（只含 high），供 resume 复用
            tools_jsonl_save(self.existing_scenarios, self.config.input_path)
            # 种子也作为 output 的初始内容（覆盖写）；后续生成的场景会由 add_scenarios 增量追加
            tools_jsonl_save(self.existing_scenarios, self.config.output_path)

        # ④ 初始化 embedding 矩阵（把已有场景编码，作为去重基准）
        self.initialize_embeddings()

        logger.info("Step 2: Self-Instruct 主循环...")
        # 当前场景总数 = 种子 + 已生成（注意：target_count 是「总数」不是「新增数」）
        current_count = len(self.existing_scenarios) + len(self.generated_scenarios)
        target = self.config.target_count

        #self.config.num_parallel_requests：每轮主循环里并行发送的请求数
        #self.config.scenarios_per_request：每个请求里要求LLM生成的场景数量
        #expected_per_iteration：每轮里期望生成的场景总数，是上面两个的乘积
        expected_per_iteration = self.config.num_parallel_requests * self.config.scenarios_per_request
        iteration = 0   # 已迭代轮数

        #暂存区，攒批次用
        pending = []

        #连续无进展计数
        stall_count = 0

        last_count = current_count

        # Self-Instruct 主循环：不断生成直到凑够 target 个场景
        while current_count < target:
            check_cancelled()
            iteration += 1
            # 迭代上限保护：超过最大轮数就退出，避免死循环/超预算
            if iteration > self.config.max_total_iterations:
                #如果达到最大迭代次数，即使生产的场景不足target个，也会退出循环，减少开销
                logger.error(f"Reached max iterations ({self.config.max_total_iterations}). Stopping.")
                break

            #连续max_stall_iterations轮无进展时，自动放宽两个要求
            if stall_count >= self.config.max_stall_iterations:
                #max_stall_iterations：连续无进展的最大迭代数
                #embedding_similarity_threshold：相似度阈值，超过这个值被认为重复，越高，代表卡的越松
                old = self.config.embedding_similarity_threshold
                self.config.embedding_similarity_threshold = min(0.95, old + 0.05)   # 阈值 +0.05（封顶 0.95）
                #max_per_category：每个类别最多的场景数
                self.config.max_per_category = int(self.config.max_per_category * 1.2)  # 类别上限 ×1.2
                logger.warning(
                    f"Stalled: relax threshold {old:.2f} -> {self.config.embedding_similarity_threshold:.2f}, "
                    f"max_per_category -> {self.config.max_per_category}"
                )
                stall_count = 0   # 放宽后重置计数

            # 并行生成一批候选场景
            new_batch = self._generate_batch()
            if not new_batch:
                # 没生成出任何结果：记一次停滞，继续下一轮
                logger.warning("Generation returned no results")
                stall_count += 1
                continue

            # 新生成的候选先进入暂存区（攒批后统一过四道关）
            pending.extend(new_batch)

            # 攒批触发：pending 攒满 diversity_check_batch，或剩余缺口 ≤ 每轮上限时，统一处理
            if len(pending) >= self.config.diversity_check_batch or (target - current_count) <= expected_per_iteration:
                # 四道质量把关：规则校验 → suitability 打分 → embedding 去重 → LLM 评审（不合格带意见重生成）
                kept = self._quality_gate(pending)
                if kept:
                    # 通过全部四道关的场景才真正入库，并更新 embedding 矩阵
                    self.add_scenarios(kept)
                    self.add_to_embedding_pool(kept)
                pending = []   # 清空暂存区

                # 更新进度，判断本轮是否有进展（用于 stall 统计）
                prev = current_count
                current_count = len(self.existing_scenarios) + len(self.generated_scenarios)
                if current_count == last_count:
                    # 总数没变 → 停滞 +1
                    stall_count += 1
                else:
                    # 有新增 → 重置停滞计数并更新基准
                    stall_count = 0
                    last_count = current_count
                if current_count != prev:
                    # 有进展就落盘（场景文件 + report.json）
                    self.save_progress()

            # 每 5 轮打印一次进度日志
            if iteration % 5 == 1:
                logger.info(f"Iteration {iteration}: {current_count}/{target}")

        # 收尾：最终落盘 + 打印总结
        self.save_progress()
        total = len(self.existing_scenarios) + len(self.generated_scenarios)
        logger.success(
            f"场景生成完成: {total} 个场景（原始 {len(self.existing_scenarios)} + 新生成 {len(self.generated_scenarios)}）"
        )
        logger.success(f"Output: {self.config.output_path}")
        logger.success(f"Report: {str(Path(self.config.output_path).with_suffix('.report.json'))}")


def run(config: Config):
    # 顶层入口：构建 3 个 agent → 实例化合成器 → 跑主循环
    agents = build_agents(config)
    synthesizer = ScenarioSynthesizer(config, agents)
    synthesizer.run()


def main():
    # 命令行入口：解析参数 → 开工前检查 → 运行
    from simpleArgParser import parse_args
    config = parse_args(Config)
    config.pre_process()
    run(config)


if __name__ == "__main__":
    run_cli(main)

