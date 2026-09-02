from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import json

ROOT = Path(__file__).parent
TITLE = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 30)
CN = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 24)
MONO = ImageFont.truetype(r"C:\Windows\Fonts\consola.ttf", 23)


def terminal(title, lines, output):
    width, height = 1450, 105 + len(lines) * 36 + 30
    image = Image.new("RGB", (width, height), "#10151c")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width, 62), fill="#202a36")
    for i, color in enumerate(("#ff5f57", "#febc2e", "#28c840")):
        draw.ellipse((22 + i * 34, 20, 42 + i * 34, 40), fill=color)
    draw.text((145, 13), title, font=TITLE, fill="#f2f4f8")
    y = 82
    for text, color, use_cn in lines:
        draw.text((30, y), text, font=CN if use_cn else MONO, fill=color)
        y += 36
    image.save(output)


result_file = ROOT / "task1_bge" / "retrieval_result.json"
if result_file.exists():
    result = json.loads(result_file.read_text(encoding="utf-8"))
    lines = [
        ("> python bge_text_retrieval.py", "#8be9fd", False),
        (f"模型: {result['model']} | 设备: {result['device']}", "#f1fa8c", True),
        (f"待检索文本: {result['query']}", "#f8f8f2", True),
        ("检索结果（按余弦相似度降序）:", "#f8f8f2", True),
    ]
    for item in result["results"]:
        color = "#50fa7b" if item["rank"] == 1 else "#f8f8f2"
        lines.append((f"Top {item['rank']}: {item['text']} | score={item['cosine_similarity']:.6f}", color, True))
    terminal("作业1 - BGE 本地文本检索（无 ES）", lines, ROOT / "task1_bge" / "作业1_BGE检索运行截图.png")

ollama_result = ROOT / "task2_ollama" / "ollama_result.txt"
if ollama_result.exists():
    content = ollama_result.read_text(encoding="utf-8").splitlines()
    lines = [("> python ollama_openai_sdk.py", "#8be9fd", False)]
    lines.extend((line, "#50fa7b" if line.startswith("助手:") else "#f8f8f2", True) for line in content)
    terminal("作业2 - Ollama + Qwen3 0.6B SDK 调用", lines, ROOT / "task2_ollama" / "作业2_Ollama_SDK运行截图.png")
