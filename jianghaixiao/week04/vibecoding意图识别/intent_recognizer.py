"""
意图识别工具 - 核心推理模块
支持 4 种分类方法：正则 / TFIDF+ML / BERT / LLM-FewShot

用法:
    from intent_recognizer import IntentRecognizer
    rec = IntentRecognizer()
    result = rec.classify("帮我播放周杰伦的歌曲", method="tfidf")
    print(result)  # Music-Play
"""

import os, re, time
from typing import Union, List, Optional
from config import (
    REGEX_RULE, CATEGORY_NAME, CATEGORY_DESC,
    LLM_OPENAI_SERVER_URL, LLM_OPENAI_API_KEY, LLM_MODEL_NAME,
)

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_WEIGHTS_DIR = os.path.join(_BASE_DIR, "weights")
TFIDF_PKL_PATH = os.path.join(_WEIGHTS_DIR, "tfidf_ml.pkl")
BERT_WEIGHTS_PATH = os.path.join(_WEIGHTS_DIR, "bert.pt")
BERT_PRETRAINED_PATH = os.path.join(_WEIGHTS_DIR, "bert-base-chinese")


class IntentRecognizer:
    """意图识别器，统一入口"""

    def __init__(self):
        self._regex_compiled = {}
        for cat, words in REGEX_RULE.items():
            self._regex_compiled[cat] = re.compile("|".join(words))

    # ---- 方法1: 正则 -------------------------------------------------
    def classify_regex(self, text: str) -> str:
        """基于正则关键词匹配，速度最快，精度有限"""
        for cat, pattern in self._regex_compiled.items():
            if pattern.findall(text):
                return cat
        return "Other"

    # ---- 方法2: TFIDF + LinearSVC -----------------------------------
    def classify_tfidf(self, text: str) -> str:
        """基于 TFIDF + 传统机器学习，速度快，精度中等"""
        from joblib import load
        import jieba, pandas as pd
        stop_path = os.path.join(_BASE_DIR, "data", "baidu_stopwords.txt")
        stops = set(pd.read_csv(stop_path, header=None)[0].values)
        tokens = " ".join([w for w in jieba.lcut(text) if w not in stops])
        tfidf, model = load(TFIDF_PKL_PATH)
        return model.predict(tfidf.transform([tokens]))[0]

    # ---- 方法3: BERT -------------------------------------------------
    def classify_bert(self, text: str) -> str:
        """基于 BERT 微调模型，精度高，需要 GPU"""
        try:
            import torch, numpy as np
            from transformers import AutoTokenizer, BertForSequenceClassification
        except ImportError:
            raise RuntimeError("BERT requires transformers and torch")
        tok = AutoTokenizer.from_pretrained(BERT_PRETRAINED_PATH)
        model = BertForSequenceClassification.from_pretrained(
            BERT_PRETRAINED_PATH, num_labels=len(CATEGORY_NAME)
        )
        model.load_state_dict(torch.load(BERT_WEIGHTS_PATH, map_location="cpu"))
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        model.to(device); model.eval()
        enc = tok([text], truncation=True, padding=True, max_length=30, return_tensors="pt")
        with torch.no_grad():
            outputs = model(enc["input_ids"].to(device), attention_mask=enc["attention_mask"].to(device))
        pred = int(np.argmax(outputs.logits.cpu().numpy(), axis=1)[0])
        return CATEGORY_NAME[pred]

    # ---- 方法4: LLM + Few-Shot ----------------------------------------
    def classify_llm(self, text: str) -> str:
        """基于大语言模型 + TFIDF 检索 Few-Shot 参考样本"""
        if not LLM_OPENAI_API_KEY:
            raise RuntimeError("请设置环境变量 LLM_API_KEY")
        import openai, numpy as np, pandas as pd
        from joblib import load
        tfidf, _ = load(TFIDF_PKL_PATH)
        train_csv = os.path.join(_BASE_DIR, "data", "dataset.csv")
        train_data = pd.read_csv(train_csv, sep="	", header=None)
        train_vecs = tfidf.transform(train_data[0])
        cur_vec = tfidf.transform([text])
        scores = np.dot(cur_vec, train_vecs.T)
        top10 = scores.toarray()[0].argsort()[::-1][:10]
        examples = "".join(f"{train_data.iloc[i][0]} -> {train_data.iloc[i][1]}
" for i in top10)
        cats = "/".join(CATEGORY_NAME)
        prompt = f"""你是一个意图识别的专家，请结合待选类别和参考例子进行意图分类。
待选类别：{cats}

历史参考例子如下：
{examples}

待识别的文本为：{text}
只需要输出意图类别（从待选类别中选一个），不要其他输出。"""
        client = openai.Client(base_url=LLM_OPENAI_SERVER_URL, api_key=LLM_OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model=LLM_MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=64,
        )
        return resp.choices[0].message.content.strip()

    # ---- 统一入口 ----------------------------------------------------
    def classify(self, text: str, method: str = "tfidf", verbose: bool = False) -> dict:
        """
        统一分类入口。

        参数:
            text:   待分类文本
            method: 分类方法，可选 "regex" / "tfidf" / "bert" / "llm"
            verbose: 是否打印耗时

        返回:
            {"intent": str, "desc": str, "method": str, "time": float}
        """
        start = time.perf_counter()
        if method == "regex":
            result = self.classify_regex(text)
        elif method == "tfidf":
            result = self.classify_tfidf(text)
        elif method == "bert":
            result = self.classify_bert(text)
        elif method == "llm":
            result = self.classify_llm(text)
        else:
            raise ValueError(f"不支持的方法: {method}，可选 regex/tfidf/bert/llm")
        elapsed = round(time.perf_counter() - start, 4)
        if verbose:
            print(f"[{method}] {text!r}  ->  {result}  ({elapsed}s)")
        return {"intent": result, "desc": CATEGORY_DESC.get(result, "未知"), "method": method, "time": elapsed}

    # ---- 批量分类 ----------------------------------------------------
    def classify_batch(self, texts: List[str], method: str = "tfidf", verbose: bool = False) -> List[dict]:
        """批量分类"""
        return [self.classify(t, method, verbose) for t in texts]

    # ---- LangChain 兼容工具接口 --------------------------------------
    def as_langchain_tool(self, method: str = "tfidf"):
        """返回一个 LangChain @tool 兼容的函数"""
        rec = self
        def tool_fn(text: str) -> str:
            """识别用户输入文本的意图类别"""
            return rec.classify(text, method=method)["intent"]
        tool_fn.__name__ = f"intent_classify_{method}"
        return tool_fn


# 模块级单例
_default: Optional[IntentRecognizer] = None

def classify(text: str, method: str = "tfidf") -> str:
    """快速调用：直接返回意图类别字符串"""
    global _default
    if _default is None:
        _default = IntentRecognizer()
    return _default.classify(text, method=method)["intent"]
