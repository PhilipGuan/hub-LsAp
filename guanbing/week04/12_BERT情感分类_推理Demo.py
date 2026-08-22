"""
===============================================================
BERT 情感 8 分类 —— 推理 Demo
===============================================================
【功能】三种推理模式，任选一种：
   1. 交互模式 (默认)       ：终端输入一句 → 实时返回 Top-K 情感与概率
   2. 单条模式 (--text)      ：命令行给一句话，直接出结果
   3. 批量模式 (--csv)       ：给 CSV 文件的 text 列 → 追加 predict / probs 列导出

【模型来源】自动选择 emotion_bert_output 下 macro_f1 最佳的 checkpoint
   （即 training_args.load_best_model_at_end=True 自动恢复的那个权重）

【运行示例】
   # 模式1：交互聊天式
   .venv/bin/python Week4/Week04/12_BERT情感分类_推理Demo.py

   # 模式2：单条测试
   .venv/bin/python Week4/Week04/12_BERT情感分类_推理Demo.py --text "我今天终于写完了论文，真舒服！"

   # 模式3：批量预测 CSV (必须有 "text" 列)
   .venv/bin/python Week4/Week04/12_BERT情感分类_推理Demo.py \
       --csv Week4/Week04/Simplified_Chinese_Multi-Emotion_Dialogue_Dataset.csv \
       --csv_out Week4/Week04/emotion_prediction_result.csv

   # 其他可选参数：--ckpt（手动指定checkpoint）、--topk、--max_len、--device
"""

import os
import sys
import json
import argparse
import warnings
from typing import List, Dict, Any, Optional

import numpy as np
import pandas as pd
import torch
import torch.nn.functional as F

warnings.filterwarnings("ignore", category=FutureWarning)

# ============================================================
# 1. 命令行参数
# ============================================================
def parse_args():
    parser = argparse.ArgumentParser(
        description="BERT 中文情感 8 分类推理 Demo",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--ckpt_root",
        type=str,
        default="/Users/philipclaw/Downloads/padow-ai/Week4/Week04/emotion_bert_output",
        help="训练输出的根目录，内部有多个 checkpoint-xxx 子目录",
    )
    parser.add_argument(
        "--ckpt",
        type=str,
        default=None,
        help="手动指定 checkpoint 路径（若不填，自动选 best_metric 最大的）",
    )
    parser.add_argument("--text", type=str, default=None, help="单条预测：输入一句中文")
    parser.add_argument(
        "--csv", type=str, default=None,
        help="批量预测：输入 CSV 路径，需含 'text' 列"
    )
    parser.add_argument(
        "--csv_out", type=str, default=None,
        help="批量预测：输出 CSV 路径（不填则在输入名后加 _predicted）"
    )
    parser.add_argument("--topk", type=int, default=3, help="输出 Top-K 个情感与概率")
    parser.add_argument("--max_len", type=int, default=64, help="分词最大长度（与训练保持一致）")
    parser.add_argument(
        "--device", type=str, default="auto",
        help="auto/cpu/mps/cuda"
    )
    parser.add_argument(
        "--batch_size", type=int, default=32,
        help="批量预测时每个 batch 的样本数"
    )
    parser.add_argument(
        "--no_interactive",
        action="store_true",
        help="（不常用）禁用交互模式，脚本执行完就退出"
    )
    return parser.parse_args()


# ============================================================
# 2. 选择最佳 checkpoint（从 trainer_state.json 的 best_metric 找最大）
# ============================================================
def pick_best_checkpoint(ckpt_root: str, manual: Optional[str] = None) -> str:
    """
    优先用手动路径；否则扫描根目录下的 checkpoint-xxx，
    读取每个 trainer_state.json 的 best_metric（或 log_history 最后一个 eval_macro_f1），
    选最大的那个。
    """
    if manual:
        manual_path = os.path.abspath(manual)
        assert os.path.isdir(manual_path), f"手动 ckpt 路径不存在: {manual_path}"
        assert os.path.isfile(os.path.join(manual_path, "model.safetensors")), \
            f"手动 ckpt 目录下没有 model.safetensors: {manual_path}"
        print(f"📌 使用手动指定的 checkpoint：{manual_path}")
        return manual_path

    root_abs = os.path.abspath(ckpt_root)
    if not os.path.isdir(root_abs):
        raise FileNotFoundError(f"ckpt_root 不存在: {root_abs}，请先运行训练脚本 11_BERT情感分类_训练.py")

    ckpt_dirs = sorted([
        os.path.join(root_abs, d) for d in os.listdir(root_abs)
        if d.startswith("checkpoint-") and os.path.isdir(os.path.join(root_abs, d))
    ])
    if not ckpt_dirs:
        raise FileNotFoundError(f"{root_abs} 下没有 checkpoint-xxx 目录，请先完成训练。")

    best_path, best_score = None, -1.0
    for cd in ckpt_dirs:
        st_file = os.path.join(cd, "trainer_state.json")
        if not os.path.isfile(st_file):
            continue
        try:
            with open(st_file, "r", encoding="utf-8") as f:
                st = json.load(f)
            # 1) 如果有 best_metric 字段（保存时的全局 best）直接用
            score = float(st.get("best_metric", -1))
            # 2) 否则从 log_history 找最后一个带 eval_macro_f1 的条目
            if score <= 0 and "log_history" in st:
                evals = [x for x in st["log_history"] if "eval_macro_f1" in x]
                if evals:
                    score = float(evals[-1]["eval_macro_f1"])
            if score > best_score:
                best_score = score
                best_path = cd
        except Exception as e:
            print(f"   ⚠️  解析 {cd} 失败: {e}，跳过")

    if best_path is None:
        # fallback：选全局 step 最大的（目录名 checkpoint-NNN 最大）
        best_path = sorted(ckpt_dirs, key=lambda p: int(os.path.basename(p).split("-")[-1]))[-1]
        print(f"⚠️  所有 ckpt 都没有 best_metric，fallback 选 step 最大：{best_path}")
    else:
        print(f"📌 自动选择 macro_f1 最优 checkpoint：{os.path.basename(best_path)}  (val macro_f1={best_score:.4f})")
    return best_path


# ============================================================
# 3. 设备选择
# ============================================================
def resolve_device(desc: str) -> torch.device:
    desc = desc.lower()
    if desc == "cpu":
        print("💻 强制使用 CPU 推理")
        return torch.device("cpu")
    if desc == "cuda" and torch.cuda.is_available():
        print("🚀 使用 CUDA GPU 推理")
        return torch.device("cuda")
    if desc in ("mps", "auto") and torch.backends.mps.is_available():
        print("🍎 使用 Apple Silicon MPS 推理")
        return torch.device("mps")
    if desc in ("cuda", "auto") and torch.cuda.is_available():
        print("🚀 使用 CUDA GPU 推理")
        return torch.device("cuda")
    print("💻 回退至 CPU 推理")
    return torch.device("cpu")


# ============================================================
# 4. 预测器核心类（封装加载 + 单条 + 批量）
# ============================================================
class EmotionClassifier:
    def __init__(self, ckpt_path: str, device: torch.device, max_len: int = 64):
        from transformers import BertTokenizer, BertForSequenceClassification

        self.device = device
        self.max_len = max_len

        print(f"📥 加载 tokenizer & model 权重：{ckpt_path}")
        self.tokenizer = BertTokenizer.from_pretrained(ckpt_path)
        self.model = BertForSequenceClassification.from_pretrained(ckpt_path)
        self.model.to(device)
        self.model.eval()  # 切换评估模式，关闭 dropout / batchnorm 训练态

        # id ↔ label 映射
        self.id2label = self.model.config.id2label
        self.label2id = self.model.config.label2id
        self.num_labels = self.model.config.num_labels
        assert len(self.id2label) == 8, f"模型类别数应为 8，实际 {len(self.id2label)}"

        total_params = sum(p.numel() for p in self.model.parameters())
        print(f"✅ 加载完成，模型参数量 {total_params/1e6:.1f}M，类别：{list(self.id2label.values())}")
        print()

    # ----------------- 内部：tokens → logits → probs -----------------
    @torch.no_grad()
    def _forward(self, texts: List[str]) -> np.ndarray:
        """输入若干字符串 → 返回 shape [N, 8] 的概率矩阵（np.float32）"""
        enc = self.tokenizer(
            texts,
            truncation=True,
            padding=True,
            max_length=self.max_len,
            return_tensors="pt",
        )
        # 把 3 个张量搬到 device
        enc = {k: v.to(self.device) for k, v in enc.items()}
        logits = self.model(**enc).logits  # [B, 8]
        probs = F.softmax(logits, dim=-1).cpu().numpy()
        return probs

    # ----------------- 对外 API 1：单条 -----------------
    def predict_one(self, text: str, topk: int = 3) -> Dict[str, Any]:
        """返回：{ label, confidence, top: [(emotion, prob), ...], text }"""
        probs = self._forward([text])[0]            # [8]
        order = np.argsort(-probs)                  # 从大到小的索引
        topk = min(topk, self.num_labels)
        top = [(self.id2label[int(idx)], float(probs[idx])) for idx in order[:topk]]
        best_idx = order[0]
        return {
            "text": text,
            "prediction": self.id2label[int(best_idx)],
            "confidence": float(probs[best_idx]),
            "top": top,
        }

    # ----------------- 对外 API 2：批量 -----------------
    def predict_batch(self, texts: List[str], batch_size: int = 32,
                      verbose: bool = True) -> pd.DataFrame:
        """批量推理，返回 DataFrame，每行包含 text / prediction / confidence 以及每类的概率列"""
        from tqdm import tqdm
        all_probs = []
        iterator = range(0, len(texts), batch_size)
        if verbose:
            iterator = tqdm(iterator, desc="批量推理中", unit="batch")
        for i in iterator:
            batch = texts[i : i + batch_size]
            probs = self._forward(batch)          # [B, 8]
            all_probs.append(probs)
        probs_mat = np.concatenate(all_probs, axis=0)  # [N, 8]
        best_ids = probs_mat.argmax(axis=1)
        best_probs = probs_mat.max(axis=1)
        df = pd.DataFrame({
            "text": texts,
            "prediction": [self.id2label[int(i)] for i in best_ids],
            "confidence": best_probs.astype(float),
        })
        # 追加每类概率列（proba_伤心 proba_关心 ...）
        for i in range(self.num_labels):
            lab = self.id2label[i]
            df[f"proba_{lab}"] = probs_mat[:, i].astype(float)
        return df


# ============================================================
# 5. 三种入口模式
# ============================================================
def pretty_topk(result: Dict[str, Any]) -> str:
    """把 Top-K 结果打印成漂亮的彩色 bar 图（终端用，不依赖第三方库）"""
    lines = []
    lines.append(f"🧑‍💻 输入文本：{result['text']}")
    lines.append(f"🏷️   预测情感：{result['prediction']}   (置信度 {result['confidence']*100:.2f}%)")
    lines.append("-" * 60)
    max_width = 30
    for i, (emo, prob) in enumerate(result["top"]):
        bar_len = int(prob * max_width)
        bar = "█" * bar_len + "░" * (max_width - bar_len)
        lines.append(f"  {i+1}. {emo:<4}  {bar}  {prob*100:5.2f}%")
    return "\n".join(lines)


def mode_single(clf: EmotionClassifier, text: str, topk: int):
    res = clf.predict_one(text, topk=topk)
    print(pretty_topk(res))


def mode_batch(clf: EmotionClassifier, csv_in: str, csv_out: Optional[str], batch_size: int):
    in_path = os.path.abspath(csv_in)
    assert os.path.isfile(in_path), f"CSV 不存在: {in_path}"
    df = pd.read_csv(in_path)
    if "text" not in df.columns:
        raise KeyError(f"{in_path} 中必须有 'text' 列，实际列：{df.columns.tolist()}")
    # 允许 NaN 文本 → 转为 ''，预测后 confidence=NaN
    texts = df["text"].fillna("").astype(str).tolist()
    pred_df = clf.predict_batch(texts, batch_size=batch_size)

    # 合并（保留原 CSV 所有列，在右侧追加预测结果）
    # 去掉 pred_df 的 text 列避免重复
    pred_df = pred_df.drop(columns=["text"])
    merged = pd.concat([df.reset_index(drop=True), pred_df.reset_index(drop=True)], axis=1)

    if csv_out is None:
        root, ext = os.path.splitext(in_path)
        csv_out_path = f"{root}_predicted{ext if ext else '.csv'}"
    else:
        csv_out_path = os.path.abspath(csv_out)
        os.makedirs(os.path.dirname(csv_out_path), exist_ok=True)
    merged.to_csv(csv_out_path, index=False, encoding="utf-8-sig")

    # 顺带打印分布
    print("\n📊 预测类别分布：")
    print(merged["prediction"].value_counts().to_string())
    print(f"\n💾 批量结果已保存：{csv_out_path}  (共 {len(merged)} 行，{len(merged.columns)} 列)")


def mode_interactive(clf: EmotionClassifier, topk: int):
    banner = """
╔════════════════════════════════════════════════════════╗
║  BERT 中文情感 8 分类 —— 交互推理 Demo                 ║
║  类别：伤心 / 关心 / 厌恶 / 平静 / 开心 / 惊讶 / 生气 / 疑问 ║
║  输入：回车一句中文，然后回车得结果                       ║
║  退出：输入空行（直接按 Enter） 或  quit / q / exit     ║
╚════════════════════════════════════════════════════════╝
"""
    print(banner)
    while True:
        try:
            user_text = input("🧭 请输入一句中文（空行退出）> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n👋 已退出交互模式")
            return
        if not user_text or user_text.lower() in ("quit", "q", "exit"):
            print("👋 已退出交互模式")
            return
        res = clf.predict_one(user_text, topk=topk)
        print()
        print(pretty_topk(res))
        print()


# ============================================================
# 6. main 入口
# ============================================================
def main():
    args = parse_args()
    print("=" * 70)
    print("🤖 BERT 中文情感 8 分类推理 Demo")
    print("=" * 70)

    device = resolve_device(args.device)
    ckpt = pick_best_checkpoint(args.ckpt_root, args.ckpt)
    clf = EmotionClassifier(ckpt, device=device, max_len=args.max_len)

    # 优先级：单条 > 批量 > 交互
    if args.text:
        mode_single(clf, args.text, topk=args.topk)
        return
    if args.csv:
        mode_batch(clf, args.csv, args.csv_out, batch_size=args.batch_size)
        return
    if not args.no_interactive:
        mode_interactive(clf, topk=args.topk)
    else:
        print("⚠️  已禁用交互模式，也没有 --text / --csv，脚本什么都不做。")


if __name__ == "__main__":
    main()
