"""
楼道消防安全隐患识别 —— 后端推理模块（week12 作业一：后端部分）。

需求来源：本目录 作业一.py 顶部注释
    手机拍照上传 → 后端用多模态模型判断图片是否拍摄于楼道，以及消防安全隐患等级：
    - 高风险：楼道中出现电动车、电瓶、飞线充电等可能起火的元素。
    - 中风险：大量堆积物严重影响通行，或堆放大量纸箱、木质家具等可燃堵塞物。
    - 低风险：存在楼道堆物现象但不严重。
    - 无风险：楼道干净，无堆放物品。
    - 非楼道：与楼道无关的图片。

两种调用方式：
    1. 命令行：python risk_analyzer.py 图片路径 [--provider local|remote]
    2. 被 server.py 作为库调用：analyzer = create_analyzer(); analyzer.analyze(image)

环境变量：
    RISK_PROVIDER          local | remote                默认 local
    RISK_MODEL_PATH        本地模型目录                  默认在仓库中自动查找 model/
    RISK_DEVICE_MAP        传给 device_map 的值           默认 auto（显存不足时自动卸载到 CPU）
    RISK_DTYPE             模型精度                     默认 auto
    RISK_LOAD_IN_4BIT      4-bit 量化加载                默认 auto（权重放不进显存且已安装 bitsandbytes 时启用）
    RISK_GPU_WEIGHTS_FRACTION  auto 模式下权重占空闲显存比例  默认 0.72
    RISK_MAX_IMAGE_SIDE    上传图片长边上限（像素）        默认 2048
    RISK_MAX_NEW_TOKENS    生成 token 上限                默认 512
    RISK_API_BASE          远程 OpenAI 兼容接口地址（remote 模式必填）
    RISK_API_KEY           远程接口 Key（remote 模式必填）
    RISK_API_MODEL         远程模型名，如 qwen-vl-max（remote 模式必填）

注意：本机 RTX 4070 SUPER 显存 12GB，7B 模型 bf16 约 16GB，device_map=auto
      会把部分层放到 CPU 内存，首次加载会慢一些，但可以正常运行。
"""
from __future__ import annotations

import ast
import base64
import io
import json
import logging
import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

# 显存碎片整理，必须在 torch 初始化 CUDA 之前设置（Windows 不支持该选项）
if os.name != "nt":
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

logger = logging.getLogger(__name__)

# 风险等级（按严重程度排序）与编码，供接口与前端配色使用
RISK_LEVELS = ("高风险", "中风险", "低风险", "无风险", "非楼道")
RISK_CODES = {
    "高风险": "high",
    "中风险": "medium",
    "低风险": "low",
    "无风险": "none",
    "非楼道": "not_hallway",
    "无法判断": "unknown",
}
RISK_SCORES = {
    "高风险": 3,
    "中风险": 2,
    "低风险": 1,
    "无风险": 0,
    "非楼道": None,
    "无法判断": None,
}

# 分级标准同时作为提示词的一部分，避免两处描述不一致
RISK_RULES = (
    "高风险：楼道中出现电动车、电瓶、飞线充电等可能起火的元素。\n"
    "中风险：楼道中存在大量堆积物严重影响通行，或堆放大量纸箱、木质家具等能造成火势蔓延的堵塞物。\n"
    "低风险：存在楼道堆物现象但不严重，基本不影响通行。\n"
    "无风险：楼道干净，无堆放物品。\n"
    "非楼道：与楼道（楼梯间、走廊、电梯厅等）无关的图片。"
)

RISK_PROMPT = f"""你是一名专业的消防安全检查员。请判断给定图片是否拍摄于居民楼楼道，并评估消防安全隐患等级。

分级标准：
{RISK_RULES}

要求：
1. 只输出一个 JSON 对象，不要输出 markdown 代码块，也不要输出任何额外解释。
2. risk_level 只能取以下值之一：高风险、中风险、低风险、无风险、非楼道。
   如果图片与楼道场景无关，必须选择“非楼道”，即使图片中没有火灾隐患。
3. hazards 列出图片中发现的隐患元素（如“电动车”“飞线充电”“大量纸箱”），没有则为空数组。
4. reason 用不超过 50 个字说明判断依据。

输出格式：
{{"risk_level": "高风险", "hazards": ["电动车"], "reason": "楼道内停放电动车，存在起火风险"}}
"""


class AnalyzerNotReady(RuntimeError):
    """模型尚未加载完成或加载失败。"""


# --------------------------------------------------------------------------- #
# 配置与模型目录
# --------------------------------------------------------------------------- #
def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def find_model_dir() -> Path:
    """自动查找本地 Qwen2.5-VL 模型目录。

    优先级：RISK_MODEL_PATH > 仓库内 model/ 或 model/Qwen2.5-VL-7B-Instruct/
    """
    env_path = os.getenv("RISK_MODEL_PATH", "").strip()
    if env_path:
        path = Path(env_path).expanduser()
        if not (path / "config.json").is_file():
            raise FileNotFoundError(f"RISK_MODEL_PATH 指向的目录不是模型目录：{path}")
        return path

    here = Path(__file__).resolve()
    for parent in here.parents:
        for candidate in (parent / "model", parent / "model" / "Qwen2.5-VL-7B-Instruct"):
            config_file = candidate / "config.json"
            if not config_file.is_file():
                continue
            try:
                model_type = json.loads(config_file.read_text(encoding="utf-8")).get("model_type", "")
            except (OSError, json.JSONDecodeError):
                continue
            if "qwen" in str(model_type).lower():
                return candidate

    raise FileNotFoundError(
        "未找到本地模型目录。请将 Qwen2.5-VL 模型放到仓库根目录 model/ 下，"
        "或设置环境变量 RISK_MODEL_PATH 指向模型目录，或改用 remote 模式。"
    )


# --------------------------------------------------------------------------- #
# 图片预处理
# --------------------------------------------------------------------------- #
def prepare_image(source: str | Path | bytes | Image.Image, max_side: int | None = None) -> Image.Image:
    """把上传内容规范化为 RGB 的 PIL 图片。

    - 处理手机照片的 EXIF 旋转
    - 透明背景铺白，避免转 RGB 后变黑
    - 长边超过 max_side 时等比缩小，控制视觉 token 数量
    """
    if isinstance(source, Image.Image):
        img = source.copy()
    elif isinstance(source, (bytes, bytearray)):
        img = Image.open(io.BytesIO(source))
        img.load()
    else:
        img = Image.open(source)
        img.load()

    img = ImageOps.exif_transpose(img)

    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        img = background
    elif img.mode != "RGB":
        img = img.convert("RGB")

    if max_side and max(img.size) > max_side:
        ratio = max_side / max(img.size)
        new_size = (max(1, round(img.width * ratio)), max(1, round(img.height * ratio)))
        img = img.resize(new_size, Image.LANCZOS)

    return img


# --------------------------------------------------------------------------- #
# 模型输出解析
# --------------------------------------------------------------------------- #
def _extract_json(text: str) -> dict[str, Any] | None:
    """从模型输出中尽力提取 JSON 对象（容忍代码块围栏、前后多余文本、单引号）。"""
    if not text:
        return None

    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.S | re.I)
    if fence:
        cleaned = fence.group(1).strip()

    candidates = [cleaned]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        candidates.append(cleaned[start : end + 1])

    for candidate in candidates:
        try:
            obj = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            return obj

    for candidate in candidates:
        try:
            obj = ast.literal_eval(candidate)
        except (ValueError, SyntaxError):
            continue
        if isinstance(obj, dict):
            return obj

    return None


def _as_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts: list[str] = re.split(r"[,，;；、\n]+", value)
    elif isinstance(value, (list, tuple, set)):
        parts = []
        for item in value:
            if isinstance(item, dict):
                item = item.get("name") or item.get("hazard") or json.dumps(item, ensure_ascii=False)
            parts.append(str(item))
    else:
        parts = [str(value)]

    result: list[str] = []
    for part in parts:
        part = part.strip().strip("-•*．.。 ")
        if part and part not in result:
            result.append(part)
    return result[:10]


_EN_LEVELS = {
    "high": "高风险",
    "highrisk": "高风险",
    "medium": "中风险",
    "moderate": "中风险",
    "mediumrisk": "中风险",
    "low": "低风险",
    "lowrisk": "低风险",
    "none": "无风险",
    "norisk": "无风险",
    "nohazard": "无风险",
    "nothallway": "非楼道",
    "notahallway": "非楼道",
    "irrelevant": "非楼道",
}


def normalize_level(value: Any) -> str | None:
    """把模型给出的任意风险等级描述归一化为五个标准等级之一。"""
    if value is None:
        return None
    text = str(value).strip().strip("\"'“”")
    if not text:
        return None

    if any(key in text for key in ("非楼道", "不是楼道", "与楼道无关", "非消防通道", "不属于楼道")):
        return "非楼道"
    for level in ("高风险", "中风险", "低风险"):
        if level in text:
            return level
    if any(
        key in text
        for key in (
            "无风险",
            "无隐患",
            "没有隐患",
            "无明显隐患",
            "未发现隐患",
            "无异常",
            "未见异常",
            "无堆放",
            "无堆积",
        )
    ):
        return "无风险"

    compact = re.sub(r"[\s_\-]+", "", text.lower())
    for key, level in _EN_LEVELS.items():
        if key in compact:
            return level

    # 最后兜底识别“高/中/低/无”单字
    match = re.search(r"(高|中|低|无)", text)
    if match:
        return {"高": "高风险", "中": "中风险", "低": "低风险", "无": "无风险"}[match.group(1)]
    return None


@dataclass
class RiskResult:
    """一次识别结果。"""

    risk_level: str
    hazards: list[str] = field(default_factory=list)
    reason: str = ""
    raw_output: str = ""
    inference_ms: int | None = None
    provider: str | None = None
    model: str | None = None

    @property
    def risk_code(self) -> str:
        return RISK_CODES.get(self.risk_level, "unknown")

    @property
    def risk_score(self) -> int | None:
        return RISK_SCORES.get(self.risk_level)

    def to_dict(self, include_raw: bool = True) -> dict[str, Any]:
        data = asdict(self)
        data["risk_code"] = self.risk_code
        data["risk_score"] = self.risk_score
        if not include_raw:
            data.pop("raw_output", None)
        return data


def parse_risk_output(raw: str) -> RiskResult:
    """把模型自由文本输出解析为结构化结果；解析失败时等级为“无法判断”。"""
    text = (raw or "").strip()
    data = _extract_json(text) or {}

    level = normalize_level(
        data.get("risk_level")
        or data.get("level")
        or data.get("风险等级")
        or data.get("risk")
    )
    hazards = _as_text_list(
        data.get("hazards") or data.get("hazard") or data.get("隐患") or data.get("risks")
    )
    reason = data.get("reason") or data.get("explanation") or data.get("理由") or data.get("说明") or ""
    if not isinstance(reason, str):
        reason = json.dumps(reason, ensure_ascii=False)
    reason = reason.strip()[:200]

    if level is None:
        level = normalize_level(text)

    # 模型偶尔把与楼道无关的图片判成“无风险”，判断依据里却写着与楼道无关，按依据纠正
    if level == "无风险" and any(
        key in reason
        for key in ("与楼道无关", "不是楼道", "不属于楼道", "非楼道", "并非楼道", "非消防通道")
    ):
        level = "非楼道"

    if level is None:
        return RiskResult(
            risk_level="无法判断",
            hazards=hazards,
            reason="模型输出无法解析为风险等级",
            raw_output=text,
        )

    return RiskResult(risk_level=level, hazards=hazards, reason=reason, raw_output=text)


# --------------------------------------------------------------------------- #
# 推理器
# --------------------------------------------------------------------------- #
class LocalQwenAnalyzer:
    """本地 Qwen2.5-VL 推理（transformers 实现）。"""

    provider = "local"

    def __init__(
        self,
        model_path: str | Path | None = None,
        device_map: str | None = None,
        dtype: str | None = None,
        max_new_tokens: int | None = None,
        max_image_side: int | None = None,
    ) -> None:
        self.model_path = Path(model_path) if model_path else find_model_dir()
        self.device_map = device_map or os.getenv("RISK_DEVICE_MAP", "").strip() or "auto"
        self.dtype = dtype or os.getenv("RISK_DTYPE", "").strip() or "auto"
        self.max_new_tokens = max_new_tokens or _env_int("RISK_MAX_NEW_TOKENS", 512)
        self.max_image_side = max_image_side or _env_int("RISK_MAX_IMAGE_SIDE", 2048)

        self.load_in_4bit = os.getenv("RISK_LOAD_IN_4BIT", "auto").strip().lower() or "auto"
        self.use_4bit = False
        self.ready = False
        self.load_error: str | None = None
        self.load_seconds: float | None = None
        self._model = None
        self._processor = None
        self._lock = threading.Lock()

    @property
    def model_name(self) -> str:
        return self.model_path.name

    def describe(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model_name,
            "model_path": str(self.model_path),
            "device_map": self.device_map,
            "dtype": self.dtype,
            "load_in_4bit": self.use_4bit,
            "ready": self.ready,
            "load_seconds": self.load_seconds,
            "load_error": self.load_error,
        }

    def _model_weight_bytes(self) -> int | None:
        index_file = self.model_path / "model.safetensors.index.json"
        try:
            total = json.loads(index_file.read_text(encoding="utf-8")).get("metadata", {}).get("total_size")
            if total:
                return int(total)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
        sizes = [f.stat().st_size for f in self.model_path.glob("*.safetensors")]
        return sum(sizes) if sizes else None

    def _should_use_4bit(self, torch) -> bool:
        setting = self.load_in_4bit
        forced = setting in ("1", "true", "on", "yes")
        if setting in ("0", "false", "off", "no") or not torch.cuda.is_available():
            return False
        try:
            import bitsandbytes  # noqa: F401
        except ImportError as exc:
            if forced:
                raise AnalyzerNotReady(
                    "RISK_LOAD_IN_4BIT=1 需要安装 bitsandbytes：pip install bitsandbytes"
                ) from exc
            return False
        if forced:
            return True
        # auto：bf16 权重明显放不进显存时改用 4-bit，避免 CPU 卸载拖慢推理
        try:
            weight_bytes = self._model_weight_bytes()
            total = torch.cuda.get_device_properties(0).total_memory
            return weight_bytes is not None and weight_bytes > 0.7 * total
        except Exception:
            return False

    @staticmethod
    def _max_memory(torch) -> dict[int | str, int | str] | None:
        """device_map=auto 时的显存预算。

        accelerate 默认把 90% 显存用于放权重，12GB 卡在长图推理时激活空间不足会 OOM。
        这里默认只用空闲显存的 72%，剩余部分留给视觉编码器和注意力激活。
        可用 RISK_MAX_MEMORY（如 "9GiB"）或 RISK_GPU_WEIGHTS_FRACTION 调整。
        """
        override = os.getenv("RISK_MAX_MEMORY", "").strip()
        if override:
            return {0: override}
        if not torch.cuda.is_available():
            return None
        free, total = torch.cuda.mem_get_info(0)
        fraction = min(max(_env_float("RISK_GPU_WEIGHTS_FRACTION", 0.72), 0.1), 0.95)
        limit = max(int(min(free, total) * fraction), 2 * 1024**3)
        return {0: limit}

    def load(self) -> None:
        """加载模型（幂等、线程安全）。失败时抛出 AnalyzerNotReady。"""
        if self.ready:
            return
        with self._lock:
            if self.ready:
                return
            if self.load_error:
                raise AnalyzerNotReady(self.load_error)

            try:
                import torch
                from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration

                torch_dtype: Any = "auto"
                if self.dtype.lower() != "auto":
                    dtype_map = {
                        "float16": torch.float16,
                        "fp16": torch.float16,
                        "bfloat16": torch.bfloat16,
                        "bf16": torch.bfloat16,
                        "float32": torch.float32,
                    }
                    if self.dtype.lower() not in dtype_map:
                        raise ValueError(f"不支持的 RISK_DTYPE：{self.dtype}")
                    torch_dtype = dtype_map[self.dtype.lower()]

                load_kwargs: dict[str, Any] = {
                    "torch_dtype": torch_dtype,
                    "device_map": self.device_map,
                }
                if self._should_use_4bit(torch):
                    from transformers import BitsAndBytesConfig

                    load_kwargs["quantization_config"] = BitsAndBytesConfig(
                        load_in_4bit=True,
                        bnb_4bit_quant_type="nf4",
                        bnb_4bit_compute_dtype=torch.bfloat16,
                        bnb_4bit_use_double_quant=True,
                    )
                    self.use_4bit = True
                    logger.info("启用 4-bit（nf4）量化加载，让模型完整放入显存")

                if self.device_map == "auto":
                    max_memory = self._max_memory(torch)
                    if max_memory:
                        load_kwargs["max_memory"] = max_memory
                        limit = max_memory[0]
                        if isinstance(limit, int):
                            logger.info(
                                "显存预算：权重最多使用 %.1f GiB，其余放到 CPU 内存",
                                limit / 1024**3,
                            )
                        else:
                            logger.info("显存预算：权重最多使用 %s，其余放到 CPU 内存", limit)

                logger.info("正在加载模型 %s（device_map=%s，dtype=%s）...", self.model_path, self.device_map, self.dtype)
                started = time.perf_counter()
                self._model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
                    str(self.model_path), **load_kwargs
                )
                self._model.eval()
                # 模型自带的 generation_config 是 temperature≈0 的采样配置，
                # 这里统一走贪心解码，避免 generate 时出现无效参数告警
                self._model.generation_config.do_sample = False
                self._model.generation_config.temperature = 1.0
                self._processor = AutoProcessor.from_pretrained(str(self.model_path))
                self.load_seconds = round(time.perf_counter() - started, 1)
                self.ready = True
                logger.info("模型加载完成，用时 %.1fs", self.load_seconds)
            except Exception as exc:  # 记录后转成业务异常，避免把栈信息暴露给前端
                self.load_error = f"模型加载失败：{exc}"
                logger.exception("模型加载失败")
                raise AnalyzerNotReady(self.load_error) from exc

    def analyze(self, image: Any, prompt: str = RISK_PROMPT) -> RiskResult:
        """对单张图片推理。image 可以是路径、bytes 或 PIL.Image。"""
        self.load()
        img = prepare_image(image, self.max_image_side)

        with self._lock:
            import torch
            from qwen_vl_utils import process_vision_info

            started = time.perf_counter()
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "image": img},
                        {"type": "text", "text": prompt},
                    ],
                }
            ]
            text = self._processor.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
            image_inputs, video_inputs = process_vision_info(messages)
            inputs = self._processor(
                text=[text],
                images=image_inputs,
                videos=video_inputs,
                padding=True,
                return_tensors="pt",
            )
            inputs = inputs.to(self._model.device)

            with torch.inference_mode():
                generated_ids = self._model.generate(
                    **inputs,
                    max_new_tokens=self.max_new_tokens,
                    do_sample=False,
                )
            generated_ids_trimmed = [
                out_ids[len(in_ids):]
                for in_ids, out_ids in zip(inputs.input_ids, generated_ids)
            ]
            raw_output = self._processor.batch_decode(
                generated_ids_trimmed,
                skip_special_tokens=True,
                clean_up_tokenization_spaces=False,
            )[0].strip()

        result = parse_risk_output(raw_output)
        result.inference_ms = int((time.perf_counter() - started) * 1000)
        result.provider = self.provider
        result.model = self.model_name
        return result


class RemoteVLAnalyzer:
    """远程 OpenAI 兼容视觉接口（如 DashScope 的 qwen-vl-max 等）。"""

    provider = "remote"

    def __init__(
        self,
        api_base: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        max_new_tokens: int | None = None,
        max_image_side: int | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.api_base = (api_base or os.getenv("RISK_API_BASE", "")).strip()
        self.api_key = (api_key or os.getenv("RISK_API_KEY", "")).strip()
        self.model_name = (model or os.getenv("RISK_API_MODEL", "")).strip()
        if not (self.api_base and self.api_key and self.model_name):
            raise ValueError(
                "remote 模式需要设置环境变量 RISK_API_BASE、RISK_API_KEY、RISK_API_MODEL"
            )
        self.max_new_tokens = max_new_tokens or _env_int("RISK_MAX_NEW_TOKENS", 512)
        self.max_image_side = max_image_side or _env_int("RISK_MAX_IMAGE_SIDE", 2048)
        self.timeout = timeout

        self.ready = False
        self.load_error: str | None = None
        self.load_seconds: float | None = None
        self._client = None
        self._lock = threading.Lock()

    def describe(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model_name,
            "api_base": self.api_base,
            "ready": self.ready,
            "load_error": self.load_error,
        }

    def load(self) -> None:
        if self.ready:
            return
        with self._lock:
            if self.ready:
                return
            try:
                from openai import OpenAI

                started = time.perf_counter()
                self._client = OpenAI(base_url=self.api_base, api_key=self.api_key, timeout=self.timeout)
                self.load_seconds = round(time.perf_counter() - started, 1)
                self.ready = True
            except Exception as exc:
                self.load_error = f"远程接口初始化失败：{exc}"
                logger.exception("远程接口初始化失败")
                raise AnalyzerNotReady(self.load_error) from exc

    def analyze(self, image: Any, prompt: str = RISK_PROMPT) -> RiskResult:
        self.load()
        img = prepare_image(image, self.max_image_side)

        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=92)
        data_url = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")

        started = time.perf_counter()
        with self._lock:
            completion = self._client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": data_url}},
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
                temperature=0.0,
                max_tokens=self.max_new_tokens,
            )
        raw_output = (completion.choices[0].message.content or "").strip()

        result = parse_risk_output(raw_output)
        result.inference_ms = int((time.perf_counter() - started) * 1000)
        result.provider = self.provider
        result.model = self.model_name
        return result


def create_analyzer(provider: str | None = None) -> LocalQwenAnalyzer | RemoteVLAnalyzer:
    """按 RISK_PROVIDER 创建推理器，默认使用本地 Qwen2.5-VL。"""
    provider = (provider or os.getenv("RISK_PROVIDER", "") or "local").strip().lower()
    if provider == "local":
        return LocalQwenAnalyzer()
    if provider in ("remote", "api", "openai"):
        return RemoteVLAnalyzer()
    raise ValueError(f"未知的 RISK_PROVIDER：{provider}（可选 local / remote）")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="对一张图片做楼道消防安全隐患识别")
    parser.add_argument("image", help="本地图片路径")
    parser.add_argument("--provider", choices=["local", "remote"], default=None, help="覆盖 RISK_PROVIDER")
    parser.add_argument("--output", help="可选：将完整结果写入 JSON 文件")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    analyzer = create_analyzer(args.provider)
    result = analyzer.analyze(args.image)
    payload = result.to_dict()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.output:
        Path(args.output).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )


if __name__ == "__main__":
    main()
