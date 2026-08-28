from fastapi import FastAPI
from .classifier import IntentClassifier
from .schemas import IntentRequest, IntentResponse

app = FastAPI(title="车载意图识别 API", version="1.0.0")
classifier = IntentClassifier()

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/v1/intent", response_model=IntentResponse)
def classify(request: IntentRequest):
    result = classifier.predict(request.text)
    return IntentResponse(request_id=request.request_id, text=request.text,
                          intent=result.intent, confidence=result.confidence, method=result.method)
