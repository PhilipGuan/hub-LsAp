"""前端进程（命令行版）：用户输入 → 后端 → 打印结果"""
import requests

URL = "http://127.0.0.1:8000/chat"

print("情感陪伴机器人（输入 exit 退出）")
while True:
    text = input("\n你：").strip()
    if text in ("", "exit", "quit"):
        break
    try:
        r = requests.post(URL, json={"message": text}, timeout=120).json()
    except Exception as e:
        print("请求失败：", e)
        continue
    print(f"[情感识别] {r['sentiment']}")
    print(f"[回复] {r['reply']}")
