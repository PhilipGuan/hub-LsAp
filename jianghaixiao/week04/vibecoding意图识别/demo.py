"""
意图识别工具 - 演示脚本

用法:
    python demo.py
"""

from intent_recognizer import IntentRecognizer

def main():
    rec = IntentRecognizer()

    samples = [
        "帮我播放周杰伦的歌曲",
        "从这里怎么回家",
        "明天北京天气怎么样",
        "打开空调",
        "设个明天早上七点的闹钟",
        "我想看和平精英的游戏视频",
        "随便播放一首专辑阁楼里的佛里的歌",
        "还有双鸭山到淮阴的汽车票吗",
        "给看一下墓王之王嘛",
        "播放一段相声",
        "明早七点提醒我开会",
        "随便聊聊天",
    ]

    print("=" * 60)
    print("意图识别工具演示")
    print("=" * 60)

    methods = ["regex", "tfidf"]

    for method in methods:
        print(f"\n--- 方法: {method} ---")
        for text in samples:
            result = rec.classify(text, method=method, verbose=True)

    # 批量分类演示
    print("\n--- 批量分类 (tfidf) ---")
    results = rec.classify_batch(samples, method="tfidf")
    for text, r in zip(samples, results):
        print(f"{text:<30s} -> {r['intent']:<25s} ({r['time']:.4f}s)")

    print("\n演示完成。")


if __name__ == "__main__":
    main()
