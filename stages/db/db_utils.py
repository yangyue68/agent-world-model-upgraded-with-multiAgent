# ============================================================
# 本文件的职责：
#   根据"用户任务清单"，让大模型(LLM)自动设计出 SQLite 数据库的表结构，
#   然后在本地真正创建出 .db 数据库文件。
#   如果某个场景建表出错率太高，就把错误信息"喂回"给大模型，让它重试，
#   最后把成功的建表方案保存成 JSONL 文件。
# ============================================================

import sqlite3                 # Python 自带的小型数据库，用来真正创建/操作 .db 文件
import os                      # 操作系统相关工具：判断文件是否存在、拼接路径、删除文件等
from pathlib import Path       # 更现代的路径处理工具，用于创建文件夹
from lib.gpt import GPTClient   # 项目自己封装好的"调用大模型"的客户端
from stages.db.prompts import DATABASE_GENERATION_SYSTEM_PROMPT, DATABASE_GENERATION_USER_PROMPT  # 预先写好的提示词模板
from loguru import logger      # 日志工具，用来打印带颜色、分级别的运行信息
from dataclasses import dataclass  # 让"配置类"写起来更简洁
from lib.tools import tools_jsonl_save, tools_jsonl_load, tools_robust_json_loads, normalize_scenario_name, tools_token_count  # 项目自带的实用小工具

@dataclass
class Config:
    # 这是一个"配置类"，相当于开工前的设置单。dataclass 会自动帮我们生成构造方法。
    input: str # task file —— 输入：保存了任务清单的文件路径
    output: str # generated database schema file —— 输出：建表方案保存到哪里（要求用 .jsonl 结尾）
    database_dir: str = './outputs/databases'   # 数据库 .db 文件保存到哪个文件夹
    model: str = ""                              # 使用哪个大模型（为空则用默认/环境变量）
    allowed_scenarios: list[str] | None = None  # 可选：只对这几个指定的场景建库（逗号分隔的列表）
    max_retry: int = 4  # 最大重试次数（首次尝试之外，最多再重试这么多次）
    error_threshold: float = 0.1  # 失败率阈值：一个场景里失败的表超过这个比例(默认10%)就算不合格，需要重试
    

    def pre_process(self):
        # 开工前的"体检"：确认配置没问题，否则直接报错停下来。
        assert os.path.exists(self.input), f"Task file {self.input} not found"  # 输入的任务文件必须存在
        assert self.output is not None and self.output.endswith('.jsonl'), "Output path .jsonl is required"  # 输出路径必须以 .jsonl 结尾
        # 从环境变量读取模型名（与 task.py / spec.py / env.py 等其他模块保持一致）
        if os.environ.get('AWM_SYN_OVERRIDE_MODEL'):
            self.model = os.environ.get('AWM_SYN_OVERRIDE_MODEL')
        # 模型名不能为空：如果没有设置模型，大模型请求会因 model 为空而失败，
        # 从而得到空回答 -> db_schema 为空。这里提前报错，避免"静默地"生成一堆空结果。
        assert self.model, "Please set the model name (via --model or the AWM_SYN_OVERRIDE_MODEL environment variable)"
        if self.allowed_scenarios:
            # 如果指定了"只做哪些场景"，把它们的名字统一格式化（比如变小写、空格换成下划线）
            self.allowed_scenarios = {normalize_scenario_name(name) for name in self.allowed_scenarios}


def summarize_errors(args: Config, client: GPTClient, raw_error_msg_list: list[str]) -> list[str]:
    # 作用：把一堆原始报错信息交给一个"SQL 专家"角色的大模型，
    #       让它总结成简洁的"避错指南"，方便喂回给生成表结构的大模型，避免再犯同样的错。
    
    def create_sum_request(raw_error_message: str):
        # 内部函数：把一条原始报错包装成一次完整的"询问大模型"的请求。
        messages = [
            {
                "role": "system",
                "content": "You are an SQL expert that helps summarize SQL errors during creating a SQLite3 database  and give concise guidance to avoid generating the same errors again. Your generated content will be directly fed to another coding LLM to avoid generating the same errors again. You must directly respond plain text when user gives you the error messages."
                # 系统提示：告诉大模型"你是一个 SQL 专家，帮我把建库时的报错总结成简洁指导"
            },
            {
                "role": "user",
                "content": raw_error_message   # 用户这条消息，直接放原始报错内容
            }
        ]
        
        return {
            "messages": messages,
            "temperature": 1.0,        # 温度=1.0，让回答更有创造性（这里是总结性任务，影响不大）
            "max_tokens": 10_000,      # 最多输出 1 万个 token
            "model": args.model,       # 使用配置里指定的模型
        }

    logger.info(f"Summarizing {len(raw_error_msg_list)} error messages...")  # 打印：开始总结多少条错误
    requests = [create_sum_request(msg) for msg in raw_error_msg_list]      # 为每条错误生成一个请求
    responses = client.batch_chat_completion(requests, progress_bar=True)   # 批量发送给大模型，显示进度条

    summaries = []

    for raw_msg, response in zip(raw_error_msg_list, responses):
        # 把"原始错误"和"大模型返回的总结"一一配对处理
        if not response:
            # 如果大模型没返回内容（可能是超时/失败），就退回用原始错误信息兜底
            logger.error(f"Failed to summarize error message: Empty response")
            summaries.append(raw_msg)  # Fallback to raw message
        else:
            # 成功拿到总结，打印一下长度和 token 数，以及前 800 字符预览
            logger.info(f"Summarized error message: {len(response)} chars ({tools_token_count(response, args.model)} tokens)\nPreview: {response[:800]}...")
            summaries.append(response)
    
    return summaries


def create_sqlite_database(scenario_name: str, db_schema: dict, db_dir: str, db_filename: str | None = None) -> tuple[str, int, int, set[str]]:
    # 作用：真正动手在硬盘上创建 SQLite 数据库。
    # 输入：场景名、AI 给的表结构(dict)、存放文件夹。
    # 输出：一个元组 (数据库路径, 成功的表数, 失败的表数, 错误信息集合)。

    Path(db_dir).mkdir(parents=True, exist_ok=True)  # 先确保存放文件夹存在，不存在就创建
    
    if db_filename is None:
        # 默认：文件名 = 规范化后的场景名 + .db，比如 amazon.db
        db_filename = f"{normalize_scenario_name(scenario_name)}.db"
    db_path = os.path.join(db_dir, db_filename)                   # 拼出完整的文件路径
    
    if os.path.exists(db_path):
        # 如果同名数据库已存在，先删掉，保证每次都是从零开始、干净地建库
        os.remove(db_path)
        logger.debug(f"Removed existing database: {db_path}")
    
    conn = sqlite3.connect(db_path)   # 打开（没有则创建）这个数据库文件
    cursor = conn.cursor()            # 拿到一个"游标"，用来执行 SQL 语句
    
    logger.info(f"Creating database for {scenario_name}...")  # 打印：开始为某场景建库
    
    successful_tables = 0              # 记录成功建成的表数量
    failed_tables = 0                  # 记录建失败的表数量
    error_messages: set[str] = set()   # 用集合记录错误信息（自动去重）
    
    for table in db_schema.get("tables", []):
        # 遍历 AI 给的所有表定义，每张表是一个 dict
        table_name = table.get('name', 'unknown')  # 表名（拿不到就叫 unknown）
        try:
            cursor.execute(table["ddl"])  # 执行建表语句（DDL，例如 CREATE TABLE ...）
            
            for index_sql in table.get("indexes", []):
                cursor.execute(index_sql)  # 执行建索引语句（例如 CREATE INDEX ...）
            
            for example_sql in table.get("examples", []):
                cursor.execute(example_sql)  # 执行示例数据插入语句（如果 AI 提供了的话）
            
            successful_tables += 1  # 这张表的所有语句都执行成功，成功数 +1
            
        except Exception as e:
            # 只要这张表有任意一句 SQL 报错，就整体算这张表失败
            logger.error(f"Error creating table {table_name}: {e}")
            failed_tables += 1  # 失败数 +1
            error_messages.add(f"Table {table_name}: {str(e)[:3000]}")  # 记录错误信息（截断到3000字符防止过长）
    
    conn.commit()  # 提交事务，把前面执行的内容真正写进数据库
    
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")  # 查询 sqlite 系统表，看看最终建成了哪些表
    tables = cursor.fetchall()  # 取出所有表名
    logger.success(f"Database created with {len(tables)} tables: {[t[0] for t in tables]}")  # 打印成功日志，列出所有表名
    
    conn.close()  # 关闭数据库连接
    
    return db_path, successful_tables, failed_tables, error_messages  # 返回数据库路径、成功数、失败数、错误集合


def generate_all_databases(args: Config):
    # 主流程：读任务 -> 过滤 -> 让 AI 生成表结构 -> 建库 -> 检查失败率 -> 失败就重试 -> 保存结果。

    user_intentions = tools_jsonl_load(args.input)  # 读取输入文件（JSONL：每行一个 JSON，代表一个场景）
    
    logger.info(f"Preparing batch requests for {len(user_intentions)} scenarios...")
    
    # Filter scenarios —— 过滤场景：只保留允许的场景
    requested_intentions = []
    for data in user_intentions:
        scenario_name = normalize_scenario_name(data["scenario"])  # 规范化场景名
        if args.allowed_scenarios is not None and scenario_name not in args.allowed_scenarios:
            continue  # 如果不在允许名单里，跳过这个场景
        requested_intentions.append(data)
    
    def create_request(data, error_msg=None):
        # 内部函数：把一个场景（+ 可选的历史错误指导）包装成一次"问大模型要建表方案"的请求。
        scenario_name = data["scenario"]
        # 把该场景的所有任务拼成一行行列表，例如 "- 搜索笔记本\n- 加入购物车"
        user_intentions_str = '\n'.join([f"- {intention}" for intention in data["tasks"]])
        
        # 套用提示词模板，填进场景名、任务列表、任务数量
        user_content = DATABASE_GENERATION_USER_PROMPT.format(
            scenario_name=scenario_name,
            user_intentions=user_intentions_str,
            num_tasks=len(data["tasks"]),
        )
        
        if error_msg and error_msg != "":
            # 如果是重试请求，就在提示词末尾追加"必须避免上次错误"的指导
            user_content += f"\n\nAttention: You MUST avoid generating SQL codes that caused the following errors in previous attempts. You MUST refer the below guidelines to help you avoid generating errors:\n{error_msg}"
        
        messages = [
            {
                "role": "system",
                "content": DATABASE_GENERATION_SYSTEM_PROMPT  # 系统提示：你是一个数据库专家
            },
            {
                "role": "user",
                "content": user_content  # 用户提示：具体要为什么场景建什么库
            }
        ]
        
        return {
            "messages": messages,
            "temperature": 1.0,       # 温度=1.0，生成更有创造性
            # DashScope qwen-plus accepts at most 32,768 output tokens.
            "max_tokens": 32_768,
            "model": args.model,      # 使用指定模型
        }
    
    # Initial batch —— 第一轮：对过滤后的所有场景各构造一个请求
    current_items = requested_intentions
    current_requests = [create_request(item) for item in current_items]
    
    results = []  # 最终成功（或被放弃但选了最优）的结果会存到这里
    client = GPTClient(timeout=600)  # 创建大模型客户端，超时时间 600 秒
    max_retries = args.max_retry     # 最大重试次数
    error_threshold = args.error_threshold  # 失败率阈值
    
    # Track history for each scenario —— 为每个场景记录历史，方便重试和最终兜底
    history_error_messages: dict[str, set[str]] = {}  # 场景名 -> 累积的错误信息集合
    history_data_messages: dict[str, list[tuple[float, dict]]] = {}  # 场景名 -> 每次尝试的(失败率, 结果)列表

    for attempt in range(max_retries + 1):
        # 一共尝试 (max_retries + 1) 次：第 1 次 + 最多 max_retries 次重试
        if not current_requests:
            break  # 如果这一轮没有需要处理的请求，就提前结束循环
        
        logger.info(f"Sending batch requests (Attempt {attempt+1}/{max_retries+1}) count={len(current_requests)}...")
        responses = client.batch_chat_completion(current_requests, progress_bar=True)  # 批量发送，拿到所有场景的回答
        
        next_items = []      # 本轮失败、需要下一轮重试的场景数据
        next_requests = []   # 用于下一轮的"原始请求 + 错误信息"组合

        for data, response in zip(current_items, responses):
            # 对每个场景和它对应的回答分别处理
            scenario_name = data["scenario"]
            error_msg: set[str] = set()  # 本场景本轮产生的错误集合
            db_schema = {}               # 本场景的表结构（默认空）
            error_ratio = 1.0            # 默认失败率=100%（表示还没成功）
            
            if not response:
                # 回答为空（大模型没返回内容）
                logger.error(f"Failed to generate database schema for {scenario_name}: Empty response")
            else:
                try:
                    logger.info(f"Processing response for {scenario_name}: {len(response)} chars")
                    db_schema = tools_robust_json_loads(response)  # 把回答解析成 JSON（自带修复能力）
                    
                    db_data = {
                        "scenario": scenario_name,
                        "db_schema": db_schema,
                        "db_path": None   # 先占位，等真正建库后再填
                    }
                    
                    # 真正建库，拿到路径、成功数、失败数、错误集合
                    db_path, successful, failed, error_msg = create_sqlite_database(
                        scenario_name, db_schema, args.database_dir
                    )
                    db_data['db_path'] = db_path  # 填上数据库路径
                    
                    total_tables = successful + failed  # 总表数 = 成功 + 失败
                    if total_tables > 0:
                        error_ratio = failed / total_tables  # 正常情况：失败率 = 失败 / 总数
                    else:
                        error_ratio = 1.0  # 一张表都没有 => 视为完全失败
                    
                    tables = db_schema.get("tables", [])
                    logger.info(f"{scenario_name}: {len(tables)} tables, error ratio: {error_ratio:.2%}")
                    
                    if error_ratio > error_threshold:
                        # 失败率太高，标记为需要重试
                        logger.warning(f"Error ratio too high for {scenario_name}: {failed} / {total_tables} = {error_ratio:.2%} > {error_threshold}, scheduling retry...")
                    else:
                        # 失败率达标，记录成功结果，跳过这个场景继续处理下一个
                        results.append(db_data)
                        logger.success(f"{scenario_name}: Successfully processed with {error_ratio:.2%} error rate")
                        continue
                    
                except Exception as e:
                    # 解析 JSON 或建库过程中出现异常，记录错误并准备重试
                    logger.error(f"Failed to generate/create database for {scenario_name}: {e}")
                    logger.error(f"Response preview: {response[:200]}...")
            
            # 走到这里说明本场景本轮没成功，需要记录历史并安排重试
            temp = normalize_scenario_name(scenario_name)  # 规范化场景名，作为字典 key
            if temp not in history_data_messages:
                history_data_messages[temp] = []
            
            failed_result = {
                "scenario": scenario_name,
                "db_schema": db_schema,
                "db_path": f"{args.database_dir}/{normalize_scenario_name(scenario_name)}.db"
            }
            history_data_messages[temp].append((error_ratio, failed_result))  # 记录这次尝试的失败率和结果
            
            if attempt < max_retries:
                # 还有重试机会：累积错误信息，准备下一轮重试
                if temp not in history_error_messages:
                    history_error_messages[temp] = set()
                history_error_messages[temp] = history_error_messages[temp].union(error_msg)  # 合并历史错误
                
                # 把所有历史错误拼成一大段文字（用 '='*80 分隔），作为下次的"避错指导"
                combined_error_msg = f"\n{'='*80}\n".join(list(history_error_messages[temp]))
                if len(combined_error_msg) > 64_000:
                    # 如果太长了就截断，防止提示词超限（截取末尾部分并加省略说明）
                    combined_error_msg = combined_error_msg[-64_000:] + "... (truncated)"
                
                next_items.append(data)                      # 本场景加入下一轮
                next_requests.append((data, combined_error_msg))  # 带上累积的错误指导
            else:
                # 重试次数用完：放弃这个场景，从历史结果里挑"失败率最低"的那次作为最终结果
                min_error, min_error_result = sorted(history_data_messages[temp], key=lambda x: x[0])[0]
                results.append(min_error_result)
                logger.error(f"Given up on {scenario_name} after {max_retries+1} attempts. Selecting best result with error_ratio={min_error:.2%}")
        
        if next_requests:
            # 下一轮之前：先让"SQL 专家"把累积的错误总结成简洁避错指南
            summarized_errors = summarize_errors(args, client, [req[1] for req in next_requests])
            # 用总结后的指南重新构造请求，作为下一轮要发送的内容
            next_requests = [create_request(req[0], err_msg) for req, err_msg in zip(next_requests, summarized_errors)]
        
        current_items = next_items       # 更新：下一轮处理的就是本轮失败的那些
        current_requests = next_requests

    tools_jsonl_save(results, args.output)  # 把所有结果保存到输出文件（每一行一个 JSON）

    logger.info(f"\nDatabase schemas saved to {args.output}")  # 打印保存位置
    logger.info(f"Total scenarios processed: {len(results)}/{len(requested_intentions)}")  # 打印处理了多少场景
    return results



def run(config: Config):
    # 对外入口函数：拿到配置后，直接启动主流程。
    generate_all_databases(config)
