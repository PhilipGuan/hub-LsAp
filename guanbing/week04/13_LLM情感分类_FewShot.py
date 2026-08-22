"""
===============================================================
LLM Few-Shot 中文情感 8 分类 —— 基于 DeepSeek API
===============================================================
【定位】BERT 微调的"对标参照"：用通用大模型 + 24 条 few-shot 样本做分类，
       对比前面 BERT 有监督微调的 accuracy / F1 / 成本。

【核心设计】：
   ✅ 标签强约束：JSON Mode + Pydantic + 白名单校验，确保 label 一定是 8 类之一
   ✅ 可靠性：Tenacity 指数退避重试（429/5xx 5 次） + 令牌桶 8req/s
   ✅ 成本可控：断点续跑缓存（同一句话绝不重复计费）+ dry-run 模式先估价
   ✅ 可审计：每条响应保存原始 JSON / 解析错误、LLM回复原文，随时抽检
   ✅ 易用：3 种运行模式 + 单条 / 批量 CSV / 自动评估模式

【运行前置】：
   1) cp .env.example .env && 编辑 .env 填 DEEPSEEK_API_KEY
   2) 已生 Week4/Week04/fewshot_samples.json（本脚本已准备好）

【运行示例】：
   # 模式 A：先 dry-run 估价（不调用 API，不花钱）看 prompt+成本
   .venv/bin/python Week4/Week04/13_LLM情感分类_FewShot.py \
       --text "今天论文终于写完了太开心了" --dry_run

   # 模式 B：真实单条
   .venv/bin/python Week4/Week04/13_LLM情感分类_FewShot.py \
       --text "陪伴十年的小狗昨天走了，很难过"

   # 模式 C：真实批量 CSV（text 列 → 追加预测结果）
   .venv/bin/python Week4/Week04/13_LLM情感分类_FewShot.py \
       --csv Week4/Week04/Simplified_Chinese_Multi-Emotion_Dialogue_Dataset.csv \
       --csv_out Week4/Week04/_llm预测结果.csv

   # 模式 D：自动评估（在数据集上抽 N 条测 accuracy/F1 对比 BERT）
   .venv/bin/python Week4/Week04/13_LLM情感分类_FewShot.py \
       --evaluate --eval_n 200 --dry_run    # 先估价再跑
"""

import os
import sys
import re
import json
import time
import hashlib
import argparse
import warnings
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime

import numpy as np
import pandas as pd

# ——————————————————————————————————————————————————————————
# 0. 环境 & 依赖加载（.env 必须放在项目根目录
from dotenv import load_dotenv, find_dotenv

# ——————————————————————————————————————————————————————————
warnings.filterwarnings("ignore")
PROJECT_ROOT = Path(__file__).resolve().parents[2]   # padow-ai 根
ENV_PATH = PROJECT_ROOT / ".env"
FEWSHOT_JSON = Path(__file__).parent / "fewshot_samples.json"
DATASET_CSV = Path(__file__).parent / "Simplified_Chinese_Multi-Emotion_Dialogue_Dataset.csv"
CACHE_PATH = Path(__file__).parent / "llm_classify_cache.json"
LABEL_LIST = ["伤心", "关心", "厌恶", "平静", "开心", "惊讶", "生气", "疑问"]

# ============================================================
# 1. CLI 参数
# ============================================================
def parse_args():
    parser = argparse.ArgumentParser(
        description="LLM Few-Shot 中文情感 8 分类（DeepSeek API）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    g = parser.add_argument_group("运行模式（三选一）")
    g.add_argument("--text", type=str, default=None, help="单条：直接输入一句中文")
    g.add_argument("--csv", type=str, default=None, help="批量：输入 CSV 路径（必须有 text 列）")
    g.add_argument("--evaluate", dest="evaluate", action="store_true", help="评估模式：在数据集抽 eval_n 条上测 accuracy/F1")
    g.add_argument("--eval_n", type=int, default=200, help="评估模式抽样数量")
    g.add_argument("--csv_out", type=str, default=None, help="批量模式：输出 CSV 路径")

    g2 = parser.add_argument_group("可靠性 & 成本")
    g2.add_argument("--dry_run", action="store_true", help="🔒 估价模式：只打印 prompt + 成本估算，不调用 API")
    g2.add_argument("--cache_file", type=str, default=str(CACHE_PATH), help="断点续跑缓存路径")
    g2.add_argument("--n_fewshot_per_class", type=int, default=3, help="每类 few-shot 条数（1/2/3）")
    g2.add_argument("--seed", type=int, default=42, help="评估模式抽样随机种子")

    args = parser.parse_args()
    # 模式选择互斥校验
    modes = int(bool(args.text)) + int(bool(args.csv)) + int(bool(args.evaluate))
    if modes == 0 and not args.dry_run:
        parser.error("必须指定一种运行模式：--text / --csv / --evaluate（或加 --dry_run）")
    if modes > 1:
        parser.error("--text / --csv / --evaluate 只能三选一")
    return args


# ============================================================
# 2. 加载 .env + 参数校验（绝不容忍空 Key 或占位符 Key）
# ============================================================
def load_env_and_validate(dry_run: bool = False) -> Dict[str, Any]:
    """
    安全规范：
      1) 若 .env 不存在 → 报错并提示用户 cp .env.example .env
          （dry_run 模式下 warn 但不退出，用默认值预览估价）
      2) 若 DEEPSEEK_API_KEY 为空 / 仍为 "sk-xxx… 占位符 → 报错并引导
          （dry_run 模式下 warn 但不退出）
    """
    print("=" * 70)
    print("🛡️  环境变量校验" + ("  🔒 DRY-RUN：无 Key 仅预览，不调用 API" if dry_run else ""))
    print("=" * 70)
    env_missing_msg = [
        f".env 文件不存在：{ENV_PATH}",
        f"请先在项目根目录执行：",
        f"  cp {PROJECT_ROOT}/.env.example {PROJECT_ROOT}/.env",
        f"然后编辑 .env，把 DEEPSEEK_API_KEY=sk-xxxx 替换成真实 Key（https://platform.deepseek.com/ 申请）",
    ]
    if not ENV_PATH.exists():
        if dry_run:
            for m in env_missing_msg:
                print(f"   ⚠️  {m}")
            print("   （dry_run 模式允许继续，不调用 API）\n")
        else:
            print(f"❌ {env_missing_msg[0]}")
            for m in env_missing_msg[1:]:
                print(f"   {m}")
            sys.exit(2)

    if ENV_PATH.exists():
        ok_env = load_dotenv(dotenv_path=str(ENV_PATH), override=False, verbose=False)
        if not ok_env:
            print(f"⚠️  加载 .env 返回 False（但文件存在），继续尝试读环境变量")

    cfg = {
        "api_key": os.getenv("DEEPSEEK_API_KEY", "").strip(),
        "base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1").rstrip("/"),
        "model": os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        "temperature": float(os.getenv("LLM_TEMPERATURE", "0.0")),
        "max_tokens": int(os.getenv("LLM_MAX_TOKENS", "200")),
        "max_retries": int(os.getenv("LLM_MAX_RETRIES", "5")),
        "max_rps": int(os.getenv("LLM_MAX_REQUESTS_PER_SECOND", "8")),
        "safety_max_input_tokens": int(os.getenv("SAFETY_MAX_TOTAL_INPUT_TOKENS", "500000")),
    }

    # API Key 安全性：必须不是 sk-xxxxx 或全是 x / 空 / 含 xxxx 占位符
    placeholder_pattern = re.compile(r"^sk-?[xX_-]{8,}$")
    key_ok = bool(cfg["api_key"]) and not placeholder_pattern.match(cfg["api_key"]) and "xxxx" not in cfg["api_key"]

    key_bad_msg = [
        "DEEPSEEK_API_KEY 仍是占位符或未填写",
        f"请编辑文件：{ENV_PATH}",
        "到 https://platform.deepseek.com/ → API Keys → 创建 Key，填入 DEEPSEEK_API_KEY=sk-真实值",
    ]
    if not key_ok:
        if dry_run:
            for m in key_bad_msg:
                print(f"   ⚠️  {m}")
            print("   （dry_run 模式允许继续，不调用 API）\n")
            cfg["api_key"] = ""
        else:
            print(f"❌ {key_bad_msg[0]}")
            for m in key_bad_msg[1:]:
                print(f"   {m}")
            sys.exit(3)
    if key_ok:
        print(f"   ✅ .env 加载成功，API Key 前 6 位：{cfg['api_key'][:6]}…")
    print(f"   模型：{cfg['model']}  base_url：{cfg['base_url']}")
    print(f"   温度：{cfg['temperature']}  输出 ≤ {cfg['max_tokens']} tokens   安全阈值 input ≤ {cfg['safety_max_input_tokens']//1000}k tokens")
    print()
    return cfg


# ============================================================
# 3. 加载 few-shot 样本并按类整理
# ============================================================
def load_fewshot_samples(n_per_class: int) -> Dict[str, List[Dict[str, Any]]]:
    if not FEWSHOT_JSON.exists():
        print(f"❌ 找不到 few-shot 样本：{FEWSHOT_JSON}")
        print("   请先运行 _pick_fewshot.py（之前已生成过，不应该缺失。请查目录。")
        sys.exit(4)
    with open(FEWSHOT_JSON, "r", encoding="utf-8") as f:
        samples = json.load(f)
    # 按 label 分组，并截断每类 n_per_class 条（按簇 id 取前 N 个）
    by_label: Dict[str, List[Dict]] = {lab: [] for lab in LABEL_LIST}
    for s in samples:
        by_label.setdefault(s["label"], []).append(s)
    # 按 cluster_id 升序，然后取前 n_per_class
    result: Dict[str, List[Dict]] = {}
    for lab in LABEL_LIST:
        slist = sorted(by_label.get(lab, []), key=lambda d: d["cluster_id"])
        result[lab] = slist[:n_per_class]
        if len(result[lab]) < n_per_class:
            print(f"⚠️  {lab} 类样本只有 {len(result[lab])} 条，少于要求的 {n_per_class} 条")
    total = sum(len(v) for v in result.values())
    print(f"📚 Few-Shot 样本就绪：{len(LABEL_LIST)}类 × {n_per_class} = {total} 条样本")
    print()
    return result


# ============================================================
# 4. Pydantic 响应模型 & 响应校验（强约束 label ∈ 8 类之一
# ============================================================
from pydantic import BaseModel, Field, field_validator, ValidationError

class EmotionClassifyResponse(BaseModel):
    """DeepSeek JSON Mode 的强制结构（100% 返回此结构 + reasons 作为可解释性。"""
    label: str = Field(..., description="情感分类标签，必须是 8 类之一")
    confidence: float = Field(..., ge=0.0, le=1.0, description="模型自信度 0~1")
    reasons: List[str] = Field(default_factory=list,
                           description="支持该分类的简短中文理由 1~3 条关键词或短语")

    @field_validator("label")
    @classmethod
    def label_must_be_in_whitelist(cls, v: str) -> str:
        vv = v.strip()
        if vv not in LABEL_LIST:
            # 让 pydantic 抛异常，驱动外层重试（外层会自动重试不合法响应，避免白嫖失败
            raise ValueError(f"label 必须是 {LABEL_LIST} 之一，实际是 '{v}'")
        return vv


# ============================================================
# 5. Prompt 构造（System + Few-shot 示例块 + User）
# ============================================================
def build_prompt_messages(fewshot_by_label: Dict[str, List[Dict[str, Any]]],
                            user_text: str) -> List[Dict[str, str]]:
    """
    messages 格式：
      - system：任务 + 标签定义 + JSON Schema
      - user：第一条 few-shot 示例块（8 类 × N 条）
      - assistant：示例 示例块的示例
      - 最新 user：待分类文本
    （说明：我们把所有的少量示例直接放在 system prompt 的末尾，而不是用多轮 user/assistant 对，省 token 更省；
       并在最后明确要求回复时，response_format=json_object 保证结构）
    """
    # —————————————————— system prompt 基础 ——————————————————
    system_parts = []
    system_parts.append("你是专业的中文文本情感 8 分类专家。请根据给定的 8 个类别，精准地为用户输入的一句话进行分类。")
    system_parts.append("")
    system_parts.append("📌 标签定义（8 类，互斥，只允许输出这 8 个词之一）：")
    definitions = [
        ("伤心", "难过、悲伤、失落、痛苦、心碎、怀旧惋惜"),
        ("关心", "对他人的关怀、问候、担心、慰问、体贴"),
        ("厌恶", "厌烦、嫌弃、不满、反感、恶心、讨厌"),
        ("平静", "中性、客观描述、无明显情绪、日常对话"),
        ("开心", "喜悦、高兴、满足、惊喜、兴奋、幸福"),
        ("惊讶", "意外、震惊、出乎意料、吃惊"),
        ("生气", "愤怒、不满、愤慨、恼火、斥责、抓狂"),
        ("疑问", "提问、怀疑、不理解、咨询好奇"),
    ]
    for lab, desc in definitions:
        system_parts.append(f"   - {lab}：{desc}")
    system_parts.append("")
    system_parts.append("📌 下面是 24 个『示例文本 → 正确标签的 few-shot 样本：")
    system_parts.append("")
    # few-shot 每类 N 条
    idx = 1
    for lab in LABEL_LIST:
        for s in fewshot_by_label[lab]:
            # 用 % 格式避免 f-string 里的 []/"" 嵌套歧义
            line1 = "  [{:02d}] 文本: \"{}\"".format(idx, s["text"])
            system_parts.append(line1)
            system_parts.append(f"       标签: {lab}")
            keywords = s.get("keywords", "") or ""
            system_parts.append(f"       关键词参考: {keywords}")
            idx += 1
    system_parts.append("")
    system_parts.append("📌 输出格式：严格 JSON 格式，包含以下 3 个键：")
    system_parts.append("  · label  : 字符串，必须是上面 8 个标签词之一；")
    system_parts.append("  · confidence : 0~1 浮点数，表示模型自信度；")
    system_parts.append("  · reasons: 列表，支持该分类的 1~3 个中文关键词或短语。")

    messages = [
        {"role": "system", "content": "\n".join(system_parts)},
        {
            "role": "user",
            "content": "请给下面这句话分类。严格输出 JSON 对象（不要多余的文字、解释或 Markdown 代码块），键名 label / confidence / reasons 必须严格使用英文双引号：\n\n「{}」".format(user_text),
        },
    ]
    return messages


# ============================================================
# 6. Token 计数 & 成本估算（DeepSeek V3 2025 Q3 官方价：¥0.14 / 1M input + ¥0.28 / 1M output）
# ============================================================
def count_tokens(texts: List[str]) -> int:
    """用 tiktoken cl100k_base 计数（中文约 1 字 ≈ 1.3 token；DeepSeek V3 用类似）"""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
    except Exception:
        # Fallback：1 汉字 ≈ 1.5 token，英文 1 词 = 1.2
        def rough(ts):
            total = 0
            for t in ts:
                total += int(len(t) * 1.4)
            return total
        return rough(texts)
    n = 0
    for t in texts:
        n += len(enc.encode(t, disallowed_special=()))
    return n


def estimate_cost(input_tokens: int, output_tokens_per_call: int, n_calls: int,
                   cfg: Dict[str, Any]) -> Dict[str, float]:
    price_in_per_m = 0.14 if cfg["model"] == "deepseek-chat" else 4.0   # ¥ / 1M
    price_out_per_m = 0.28 if cfg["model"] == "deepseek-chat" else 16.0
    total_in = input_tokens
    total_out = output_tokens_per_call * n_calls
    cost_in = total_in / 1_000_000 * price_in_per_m
    cost_out = total_out / 1_000_000 * price_out_per_m
    return {
        "input_tokens": total_in,
        "output_tokens_est": total_out,
        "cost_in_rmb": cost_in,
        "cost_out_rmb": cost_out,
        "cost_total_rmb": cost_in + cost_out,
    }


# ============================================================
# 7. 缓存：断点续跑（同 text → LLM 结果，key = text 内容的 sha256
# ============================================================
class PredictCache:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data: Dict[str, Dict[str, Any]] = {}
        self.load()

    def load(self):
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
                print(f"🗃️  缓存加载：{len(self.data)} 条 → {self.path.name}")
            except Exception:
                self.data = {}

    @staticmethod
    def key(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def has(self, text: str) -> bool:
        return self.key(text) in self.data

    def get(self, text: str) -> Optional[Dict[str, Any]]:
        return self.data.get(self.key(text))

    def put(self, text: str, val: Dict[str, Any]):
        self.data[self.key(text)] = val

    def flush(self):
        """周期 flush，避免崩溃丢缓存：写入临时文件然后原子 rename"""
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)


# ============================================================
# 8. LLM 分类器（客户端 + 重试 + 速率限制 + 响应解析
# ============================================================
from openai import OpenAI, APIError, APIConnectionError, RateLimitError, APITimeoutError, InternalServerError
from tenacity import retry, stop_after_attempt, wait_exponential_jitter, retry_if_exception_type, retry_if_result


class LLMClassifier:
    def __init__(self, cfg: Dict[str, Any], fewshot_by_label: Dict[str, List[Dict]]):
        self.cfg = cfg
        self.fewshot_by_label = fewshot_by_label
        self.client = OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"])
        # 令牌桶：self._last_req_ts + 间隔 = 1/max_rps
        self._min_interval = 1.0 / cfg["max_rps"]
        self._last_req_ts = 0.0
        # 累计 token & 成本计数
        self.total_in_tokens = 0
        self.total_out_tokens = 0
        self.n_calls = 0
        self.n_retries_total = 0

    # —————————————— 速率限制：每次调用前保证 ≥ _min_interval ——————————————
    def _rate_limit_wait(self):
        elapsed = time.perf_counter() - self._last_req_ts
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed + 0.002)
        self._last_req_ts = time.perf_counter()

    # —————————————— 真实 API 调用（带重试）——————————————
    @retry(
        stop=stop_after_attempt(6),   # 最多 6 次（1 次首调 + 5 次重试）
        # initial=1, exp_base=2 → 等待序列约 1s → 2s → 4s → 8s → 16s（+ jitter）
        wait=wait_exponential_jitter(initial=1, max=16, exp_base=2, jitter=1),
        retry=(
            retry_if_exception_type((RateLimitError, InternalServerError, APITimeoutError,
                                     APIConnectionError, APIError, ValidationError))
            | retry_if_result(lambda r: r is False)   # 解析错 False → 触发重试
        ),
        reraise=True,
    )
    def _call_api_once(self, messages: List[Dict[str, str]]) -> Tuple[Optional[EmotionClassifyResponse], bool, int, int, str]:
        self._rate_limit_wait()
        self.n_calls += 1
        # ————— 调用 —————
        chat = self.client.chat.completions.create(
            model=self.cfg["model"],
            messages=messages,
            temperature=self.cfg["temperature"],
            max_tokens=self.cfg["max_tokens"],
            response_format={"type": "json_object"},   # 🔥 DeepSeek 支持，强制输出 JSON
            timeout=30,
        )
        msg = chat.choices[0].message
        content = (msg.content or "").strip()
        in_tok = chat.usage.prompt_tokens
        out_tok = chat.usage.completion_tokens

        # ————— 解析 —————
        try:
            obj = json.loads(content)
            resp = EmotionClassifyResponse(**obj)
            return resp, True, in_tok, out_tok, content
        except (json.JSONDecodeError, ValidationError) as e:
            print(f"   ⚠️  [{datetime.now():%H:%M:%S}] 响应解析失败：{type(e).__name__}: {e}")
            print(f"      LLM 原文：{content[:200]}")
            self.n_retries_total += 1
            raise   # tenacity 捕获 ValidationError 会重试
        return None, False, in_tok, out_tok, content

    # —————————————— 对外 API：predict_one（含缓存命中直接回）——————————————
    def predict_one(self, text: str, cache: Optional[PredictCache] = None,
                    use_cache: bool = True) -> Dict[str, Any]:
        """返回 {label, confidence, reasons, from_cache, in_tokens, out_tokens, raw, llm_error?}"""
        # 0) 缓存命中
        if use_cache and cache and cache.has(text):
            hit = cache.get(text)
            return {**hit, "from_cache": True}

        # 1) 构造 messages
        messages = build_prompt_messages(self.fewshot_by_label, text)

        # 2) 调用 API（带重试）
        result: Dict[str, Any] = {"text": text, "from_cache": False}
        try:
            resp, ok, in_tok, out_tok, raw = self._call_api_once(messages)
            self.total_in_tokens += in_tok
            self.total_out_tokens += out_tok
            result.update({
                "label": resp.label,
                "confidence": round(resp.confidence, 4),
                "reasons": resp.reasons,
                "in_tokens": in_tok,
                "out_tokens": out_tok,
                "raw_response": raw,
                "parse_ok": ok,
            })
        except Exception as e:
            # 所有重试都耗尽仍失败：记录错误，返回 label=None
            result.update({
                "label": None, "confidence": None, "reasons": [],
                "llm_error": f"{type(e).__name__}: {str(e)[:200]}",
                "parse_ok": False,
            })

        # 3) 写入缓存（即使失败也写入 → 避免下次再花钱请求同样失败的响应）
        if cache is not None:
            cache.put(text, {k: v for k, v in result.items() if k != "from_cache"})
        return result


# ============================================================
# 9. 三种运行模式实现
# ============================================================

# ————— 9A. dry-run：只打印 1 条 messages 预览 + 成本估算 —————
def mode_dry_run(args, cfg, fewshot_by_label, texts_sample: List[str]):
    print("🔒 DRY-RUN 模式：仅预览 prompt 构造 + 估价（不调用 API，零成本）")
    print("-" * 70)
    # (a) 打印一条样本的 messages 预览（system 前 30 行 + user）
    demo_messages = build_prompt_messages(fewshot_by_label, texts_sample[0] if texts_sample else "今天天气真不错啊～")
    print(f"\n📄 Messages 预览（共 {len(demo_messages)} 条）：")
    for i, m in enumerate(demo_messages):
        lines = m["content"].splitlines()
        print(f"━━━ 【{i+1}/{len(demo_messages)}】role={m['role']}，共 {len(lines)} 行，{len(m['content'])} 字 ━━━")
        preview_n = min(12, len(lines))
        for ln in lines[:preview_n]:
            print(f"   {ln}")
        if len(lines) > preview_n:
            print(f"   ……（省略 {len(lines) - preview_n} 行）")

    # (b) token & 成本估算：每条样本大概 input_tokens
    n = max(1, len(texts_sample))
    one_msg = [m["content"] for m in build_prompt_messages(fewshot_by_label, texts_sample[0])] if texts_sample else ["test"]
    per_call_in = count_tokens(one_msg) + 80   # 加 80 是每条的系统计数误差 + 对话模板误差 + 消息开销
    out_tok_per_call = 120
    est = estimate_cost(per_call_in * n, out_tok_per_call, n, cfg)
    print()
    print("=" * 70)
    print(f"💰 本次任务共需调用 LLM {n} 次 估算：")
    print(f"   每次 input tokens 合计：{est['input_tokens']:>8,} ≈ {est['input_tokens']/1000:.1f} k tokens 千tokenstokens 平均每次 {per_call_in}")
    print(f"   每次 output tokens 约 ：{out_tok_per_call} × {n} = {est['output_tokens_est']:,}")
    print(f"   input  成本 ≈ ¥ {est['cost_in_rmb']:.3f}")
    print(f"   output 成本 ≈ ¥ {est['cost_out_rmb']:.3f}")
    print(f"   💰 TOTAL    ≈ ¥ {est['cost_total_rmb']:.3f}  （{cfg['model']} 汇率 ¥0.14/¥0.28 每 1M")
    print("=" * 70)
    if est["input_tokens"] > cfg["safety_max_input_tokens"]:
        print(f"⚠️  超过安全阈值 {cfg['safety_max_input_tokens']/1000:.0f}k → 请确认运行，或减少 n_eval_n / 调小 n_fewshot_per_class")
    else:
        print("✅ 成本极低（几毛钱）")
    print()
    print("👉 去掉 --dry_run 参数开始真实推理")


# ————— 9B. 单条预测 —————
def mode_single(clf: LLMClassifier, text: str, cache: PredictCache):
    print(f"🤖 单条分类开始：{text}")
    r = clf.predict_one(text, cache=cache, use_cache=True)
    cache.flush()
    print()
    print(f"🏷️   label     : {r.get('label')}  置信度 {r.get('confidence')}")
    print(f"💡 理由关键词: {r.get('reasons')}")
    if r.get("from_cache"):
        print(f"🗃️   (来自缓存，没有调用 API")
    else:
        print(f"🔢 tokens: in={r.get('in_tokens')}  out={r.get('out_tokens')}")
    if r.get("llm_error"):
        print(f"❌ LLM 错误：{r['llm_error']}")


# ————— 9C. 批量 CSV 预测 —————
def mode_batch_csv(clf: LLMClassifier, csv_in: str, csv_out: Optional[str], cache: PredictCache):
    from tqdm import tqdm
    in_path = Path(csv_in).resolve()
    csv_out_path = (
        Path(csv_out).resolve()
        if csv_out
        else in_path.with_name(f"{in_path.stem}_llm_predicted{in_path.suffix}")
    )
    csv_out_path.parent.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(in_path)
    if "text" not in df.columns:
        print(f"❌ CSV 列缺少 text 列：{df.columns.tolist()}")
        sys.exit(5)
    texts = df["text"].fillna("").astype(str).tolist()
    N = len(texts)
    print(f"📦 批量 CSV：共 {N} 条 → 输出：{csv_out_path}")
    hits = sum(1 for t in texts if cache.has(t))
    print(f"🗃️  缓存命中：{hits}/{N} = {hits/N*100:.1f}%  待调用 LLM：{N - hits} 次")

    results: List[Dict] = []
    for i, t in enumerate(tqdm(texts, desc="LLM 批量推理中")):
        r = clf.predict_one(t, cache=cache, use_cache=True)
        results.append(r)
        # 每 20 条 flush 一次
        if i % 20 == 0:
            cache.flush()
    cache.flush()

    # 合并到原表
    out_df = pd.DataFrame({
        "llm_label": [r.get("label") for r in results],
        "llm_confidence": [r.get("confidence") for r in results],
        "llm_reasons": [" | ".join(r.get("reasons") or []) for r in results],
        "llm_from_cache": [1 if r.get("from_cache") else 0 for r in results],
        "llm_in_tok": [r.get("in_tokens", np.nan) for r in results],
        "llm_out_tok": [r.get("out_tokens", np.nan) for r in results],
        "llm_error": [r.get("llm_error", "") for r in results],
    })
    merged = pd.concat([df.reset_index(drop=True), out_df.reset_index(drop=True)], axis=1)
    merged.to_csv(csv_out_path, index=False, encoding="utf-8-sig")
    print(f"\n💾 已保存：{csv_out_path}（{len(merged)} 行 × {len(merged.columns)} 列")

    # 分布
    print("\n📊 LLM 预测分布：")
    vc = merged["llm_label"].value_counts(dropna=False)
    print(vc.to_string())

    # 如果原文件有 label 列，顺便打印 accuracy 对比
    if "label" in merged.columns:
        from sklearn.metrics import accuracy_score, f1_score
        mask = merged["llm_label"].notna() & merged["label"].notna()
        if mask.any():
            y_true = merged.loc[mask, "label"]
            y_pred = merged.loc[mask, "llm_label"]
            acc = accuracy_score(y_true, y_pred)
            mf1 = f1_score(y_true, y_pred, average="macro")
            print(f"\n🎯 与原文件 label 列对比：准确率 = {acc*100:.2f}%   Macro-F1 = {mf1*100:.2f}%")


# ————— 9D. 自动评估模式：在数据集抽 N 条，对比 LLM vs 真实标签 —————
def mode_evaluate(clf: LLMClassifier, n: int, seed: int, cache: PredictCache):
    from tqdm import tqdm
    from sklearn.metrics import accuracy_score, f1_score, classification_report, confusion_matrix
    if not DATASET_CSV.exists():
        print(f"❌ 找不到数据集：{DATASET_CSV}")
        sys.exit(6)
    df = pd.read_csv(DATASET_CSV)
    # 分层抽样 n 条（8 类分层，保证比例）
    from sklearn.model_selection import train_test_split
    _, df_sample, _, _ = train_test_split(df, df["label"], test_size=n, stratify=df["label"], random_state=seed)
    print(f"🔬 评估样本：{len(df_sample)} 条（分层抽样，seed={seed}）")

    texts = df_sample["text"].astype(str).tolist()
    labels_true = df_sample["label"].tolist()
    hits = sum(1 for t in texts if cache.has(t))
    print(f"🗃️  缓存命中：{hits}/{len(texts)}")

    preds: List[Optional[str]] = []
    for i, t in enumerate(tqdm(texts, desc="LLM 评估推理中")):
        r = clf.predict_one(t, cache=cache)
        preds.append(r.get("label"))
        if i % 20 == 0:
            cache.flush()
    cache.flush()

    # 指标
    mask_ok = [p is not None for p in preds]
    if not any(mask_ok):
        print("❌ 所有请求全部失败！")
        return
    y_true = [lb for lb, ok in zip(labels_true, mask_ok) if ok]
    y_pred = [p for p, ok in zip(preds, mask_ok) if ok]
    acc = accuracy_score(y_true, y_pred)
    macro = f1_score(y_true, y_pred, average="macro")
    weighted = f1_score(y_true, y_pred, average="weighted")
    print("\n" + "=" * 70)
    print(f"🎯 LLM Few-Shot 评估结果（{sum(mask_ok)} 条有效）：")
    print(f"   Accuracy  = {acc*100:.2f}%   ← 对比 BERT 微调 87.8%")
    print(f"   Macro-F1 = {macro*100:.2f}%   ← 对比 BERT 微调 87.78%")
    print(f"   Weighted-F1 = {weighted*100:.2f}%")
    print("=" * 70)
    print("\n📑 分类报告：")
    print(classification_report(y_true, y_pred, labels=LABEL_LIST, zero_division=0))
    print("\n🔲 混淆矩阵（行=真实，列=预测）：")
    cm = confusion_matrix(y_true, y_pred, labels=LABEL_LIST)
    hdr = f"{'真实/预测':>8}"
    for lab in LABEL_LIST:
        hdr += f"{lab:>8}"
    print(hdr)
    for i, lab in enumerate(LABEL_LIST):
        line = f"{lab:>8}"
        for j in range(len(LABEL_LIST)):
            line += f"{cm[i][j]:>8}"
        print(line)

    # 成本统计
    print(f"\n💸 实际成本：")
    print(f"   API 调用次数：{clf.n_calls}（重试 {clf.n_retries_total} 次")
    print(f"   input tokens：{clf.total_in_tokens:,}")
    print(f"   output tokens：{clf.total_out_tokens:,}")


# ============================================================
# 10. MAIN
# ============================================================
def main():
    args = parse_args()
    cfg = load_env_and_validate(dry_run=args.dry_run)
    fewshot_by_label = load_fewshot_samples(args.n_fewshot_per_class)
    cache = PredictCache(args.cache_file)

    # —— dry-run 模式 ——
    if args.dry_run:
        sample_texts: List[str] = []
        if args.csv:
            df = pd.read_csv(args.csv)
            sample_texts = df["text"].fillna("").astype(str).tolist()
        elif args.evaluate:
            # 评估模式会抽 eval_n 条：这里给一个 token 估算的占位样本列表（长度 ~ eval_n）
            sample_texts = ["占位文本用于 token 成本估算"] * max(1, args.eval_n)
        elif args.text:
            sample_texts = [args.text]
        mode_dry_run(args, cfg, fewshot_by_label, sample_texts)
        return

    # —— 实例化 LLMClassifier
    clf = LLMClassifier(cfg, fewshot_by_label)

    try:
        if args.text:
            mode_single(clf, args.text, cache)
        elif args.csv:
            mode_batch_csv(clf, args.csv, args.csv_out, cache)
        elif args.evaluate:
            mode_evaluate(clf, args.eval_n, args.seed, cache)
    finally:
        # Always flush cache when exiting
        cache.flush()
        # Print total cost
        est = estimate_cost(clf.total_in_tokens, 120, 1, cfg)
        actual_cost_rmb = (
            clf.total_in_tokens / 1_000_000 * (0.14 if cfg["model"] == "deepseek-chat" else 4.0) +
            clf.total_out_tokens / 1_000_000 * (0.28 if cfg["model"] == "deepseek-chat" else 16.0)
        )
        print(f"\n💸 本次真实花费：¥ {actual_cost_rmb:.3f}")
        print(f"   input tokens：{clf.total_in_tokens:,}   output tokens：{clf.total_out_tokens:,}   retries：{clf.n_retries_total}")

if __name__ == "__main__":
    main()
