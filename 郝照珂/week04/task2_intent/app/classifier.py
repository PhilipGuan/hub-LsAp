import re
from dataclasses import dataclass

@dataclass
class Result:
    intent: str
    confidence: float
    method: str = "regex"

class IntentClassifier:
    """易扩展的规则基线；可替换为 TF-IDF 或微调 BERT。"""
    RULES = {
        "Music-Play": [r"播放.*(歌|音乐)", r"听.*(歌|音乐)", r"周杰伦"],
        "Weather-Query": [r"天气", r"下雨", r"温度"],
        "Travel-Query": [r"导航", r"路线", r"怎么去", r"加油站"],
        "HomeAppliance-Control": [r"空调", r"打开.*灯", r"关闭.*灯", r"温度.*度"],
        "Alarm-Update": [r"闹钟", r"提醒我"],
        "FilmTele-Play": [r"播放.*(电影|电视剧)", r"看.*(电影|电视剧)"],
    }

    def predict(self, text: str) -> Result:
        normalized = re.sub(r"\s+", "", text)
        for intent, patterns in self.RULES.items():
            if any(re.search(pattern, normalized, re.I) for pattern in patterns):
                return Result(intent, 0.95)
        return Result("Other", 0.50)
