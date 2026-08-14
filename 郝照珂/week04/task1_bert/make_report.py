import json
from pathlib import Path
import matplotlib.pyplot as plt

root = Path(__file__).parent
results = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((root / "outputs").glob("result_*.json"))]
fig, axes = plt.subplots(1, 2, figsize=(13, 5))
for result in results:
    h = result["history"]
    label = f"{result['experiment']} (best={result['best_accuracy']:.1%})"
    axes[0].plot([x["epoch"] for x in h], [x["accuracy"] for x in h], marker="o", label=label)
    axes[1].plot([x["epoch"] for x in h], [x["eval_loss"] for x in h], marker="o", label=label)
axes[0].set(title="BERT Validation Accuracy", xlabel="Epoch", ylabel="Accuracy", ylim=(0, 1))
axes[1].set(title="BERT Validation Loss", xlabel="Epoch", ylabel="Loss")
for ax in axes: ax.grid(alpha=.3); ax.legend()
fig.tight_layout()
fig.savefig(root / "BERT超参数对比.png", dpi=180)

lines = ["# 作业1：BERT 文本分类微调实验", "", "固定条件：前500条数据、随机种子42、8:2分层划分、4 epochs、max_length=64。", "",
         "| 实验 | 学习率 | Batch size | 最佳测试准确率 | 耗时(s) |", "|---|---:|---:|---:|---:|"]
for r in results:
    h = r["history"][0]
    lines.append(f"| {r['experiment']} | {h['learning_rate']} | {h['batch_size']} | {r['best_accuracy']:.2%} | {r['seconds']} |")
lines += ["", "## 微调过程理解", "", "文本先经 BERT tokenizer 转为 input_ids 与 attention_mask；预训练 BERT 输出语义表示，分类头产生各类别 logits；交叉熵损失衡量预测与标签差异；反向传播同时更新分类头和 BERT 权重；每轮在固定测试集计算 accuracy，最终保存模型和实验指标。", "", "![超参数对比](BERT超参数对比.png)"]
(root / "实验报告.md").write_text("\n".join(lines), encoding="utf-8")
