"""
意图识别工具 - 全局配置
包含：12个意图类别名、正则规则、模型权重路径、LLM API 配置
"""

import os

# ============================================================
# 正则规则：关键词 → 类别
# ============================================================
REGEX_RULE = {
    FilmTele-Play: [播放, 电视剧, 影视, 剧集, 剧, 电影],
    HomeAppliance-Control: [空调, 广播, 电视, 风扇, 灯光, 温度],
    Music-Play: [歌, 音乐, 播放, 单曲, 专辑, 唱歌],
    Weather-Query: [天气, 下雨, 下雪, 温度, 雾霾],
    Travel-Query: [回家, 导航, 票, 汽车, 火车, 飞机, 路线, 怎么走, 去哪里],
    Alarm-Update: [闹钟, 提醒, 日程, 几点, 设定, 定时],
    Radio-Listen: [电台, 广播, 收音机, 频道, 调频],
    Video-Play: [视频, 录像, 播放, 影视, 看],
}

# ============================================================
# 12个意图类别
# ============================================================
CATEGORY_NAME = [
    Travel-Query,
    Music-Play,
    FilmTele-Play,
    Video-Play,
    Radio-Listen,
    HomeAppliance-Control,
    Weather-Query,
    Alarm-Update,
    Calendar-Query,
    TVProgram-Play,
    Audio-Play,
    Other,
]

# ============================================================
# 类别中文说明（用于展示）
# ============================================================
CATEGORY_DESC = {
    Travel-Query: 出行查询,
    Music-Play: 音乐播放,
    FilmTele-Play: 影视播放,
    Video-Play: 视频播放,
    Radio-Listen: 电台收听,
    HomeAppliance-Control: 家电控制,
    Weather-Query: 天气查询,
    Alarm-Update: 闹钟提醒,
    Calendar-Query: 日历查询,
    TVProgram-Play: 电视节目,
    Audio-Play: 音频播放,
    Other: 其他,
}

# ============================================================
# LLM 配置（通过环境变量获取，不硬编码）
# ============================================================
LLM_OPENAI_SERVER_URL = os.getenv(LLM_BASE_URL, https://dashscope.aliyuncs.com/compatible-mode/v1)
LLM_OPENAI_API_KEY = os.getenv(LLM_API_KEY, ")
LLM_MODEL_NAME = os.getenv(LLM_MODEL_NAME, qwen-plus)
