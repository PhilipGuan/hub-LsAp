"""Topic → 3-7 searchable sub-questions."""
from .llm import LLM
from .models import SubQuestion

PLANNER_SYSTEM = """You are a research planner.
Break the user's research topic into 3-7 focused, independently searchable sub-questions
that, answered together, produce a thorough understanding of the topic.
Return JSON: {"questions": [{"question": "...", "rationale": "..."}, ...]}
No prose, no markdown fence."""


def plan_sub_questions(topic: str, llm: LLM) -> list[SubQuestion]:
    parsed = llm.complete_json(f"Topic: {topic}", system=PLANNER_SYSTEM)
    return [SubQuestion(q["question"], q.get("rationale", "")) for q in parsed["questions"]]