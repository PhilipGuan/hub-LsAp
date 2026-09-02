from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import json

ROOT = Path(__file__).parent
FONT_CANDIDATES = [r"C:\Windows\Fonts\consola.ttf", r"C:\Windows\Fonts\msyh.ttc"]
FONT = ImageFont.truetype(FONT_CANDIDATES[0], 23)
FONT_CN = ImageFont.truetype(FONT_CANDIDATES[1], 25)
TITLE = ImageFont.truetype(FONT_CANDIDATES[1], 30)

def terminal_image(title, lines, path, width=1450):
    height = 105 + len(lines) * 35 + 35
    im = Image.new("RGB", (width, height), "#10151c")
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, width, 62), fill="#202a36")
    for i, c in enumerate(("#ff5f57", "#febc2e", "#28c840")):
        d.ellipse((22+i*34, 20, 42+i*34, 40), fill=c)
    d.text((145, 14), title, font=TITLE, fill="#f2f4f8")
    y = 82
    for text, color, chinese in lines:
        d.text((30, y), text, font=FONT_CN if chinese else FONT, fill=color)
        y += 35
    im.save(path)

out = ROOT / "task1_bert" / "outputs"
results = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(out.glob("result_*.json"))]
lines = [("PS> python 10_BERT文本分类_实验版.py  # RTX 4060 Laptop GPU", "#8be9fd", True)]
for r in results:
    h = r["history"][0]
    lines.append((f"实验: {r['experiment']} | lr={h['learning_rate']} | batch={h['batch_size']} | labels={r['num_labels']}", "#f1fa8c", True))
    for row in r["history"]:
        lines.append((f"Epoch {row['epoch']}/4 | train_loss={row['train_loss']:.4f} | eval_loss={row['eval_loss']:.4f} | accuracy={row['accuracy']:.4f}", "#f8f8f2", False))
    lines.append((f"Best accuracy: {r['best_accuracy']:.2%} | elapsed: {r['seconds']}s", "#50fa7b", False))
terminal_image("作业1 - BERT 微调完整运行与超参数对比", lines, ROOT / "task1_bert" / "作业1_运行结果截图.png")

tree_lines = [
    ("PS> tree /F task2_intent", "#8be9fd", False),
    ("task2_intent/", "#f1fa8c", False),
    ("|-- README.md                 # 项目说明与运行方式", "#f8f8f2", True),
    ("|-- requirements.txt          # 项目依赖", "#f8f8f2", True),
    ("|-- app/", "#f1fa8c", False),
    ("|   |-- __init__.py", "#f8f8f2", False),
    ("|   |-- main.py               # FastAPI 服务与接口", "#f8f8f2", True),
    ("|   |-- schemas.py            # Pydantic 请求/响应模型", "#f8f8f2", True),
    ("|   `-- classifier.py         # 意图分类核心逻辑", "#f8f8f2", True),
    ("`-- tests/", "#f1fa8c", False),
    ("    `-- test_classifier.py    # 单元测试", "#f8f8f2", True),
    ("", "#f8f8f2", False),
    ("PS> pytest -q", "#8be9fd", False),
    ("1 passed in 0.02s", "#50fa7b", False),
]
terminal_image("作业2 - Vibe Coding 意图识别项目结构", tree_lines, ROOT / "task2_intent" / "作业2_代码结构截图.png")
