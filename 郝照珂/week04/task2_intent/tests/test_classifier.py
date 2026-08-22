from app.classifier import IntentClassifier

def test_typical_intents():
    model = IntentClassifier()
    cases = {"帮我播放周杰伦的歌曲": "Music-Play", "明天会下雨吗": "Weather-Query",
             "导航到最近的加油站": "Travel-Query", "把空调调到26度": "HomeAppliance-Control",
             "量子纠缠是什么": "Other"}
    for text, expected in cases.items():
        assert model.predict(text).intent == expected
