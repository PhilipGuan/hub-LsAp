"""pipeline 工厂（设计文档 §5）：按 PIPELINE_ENGINE 返回对应的 run_pipeline。

两个实现共用同一契约 `async run_pipeline(text: str) -> PipelineResult`，
后端 main.py 只面向此工厂，切换引擎对前端零改动（设计文档 D3）。
延迟导入：仅加载被选中的框架，未安装另一框架不影响运行。
"""

from config import PIPELINE_ENGINE


def get_pipeline():
    if PIPELINE_ENGINE == "langgraph":
        from pipelines.langgraph_impl import run_pipeline
    elif PIPELINE_ENGINE == "openai-agents":
        from pipelines.agents_impl import run_pipeline
    else:
        raise RuntimeError(
            f"PIPELINE_ENGINE 取值非法：{PIPELINE_ENGINE!r}，可选 ('langgraph', 'openai-agents')"
        )
    return run_pipeline
