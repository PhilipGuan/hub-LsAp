from __future__ import annotations

import evaluate


def test_value_matches_numeric_tolerance() -> None:
    assert evaluate.value_matches(35.64, 35.65, 0.01, 0.001)
    assert evaluate.value_matches(1000.0, 1000.5, 0.01, 0.001)
    assert not evaluate.value_matches(35.64, 36.0, 0.01, 0.001)
    assert evaluate.value_matches(None, None, 0.01, 0.001)
    assert not evaluate.value_matches(None, 0, 0.01, 0.001)


def test_rows_match_unordered() -> None:
    agent = [(2, "b"), (1, "a")]
    gold = [(1, "a"), (2, "b")]
    assert evaluate.rows_match(agent, gold, ordered=False, tol_abs=0.01, tol_rel=0.001)
    assert not evaluate.rows_match(
        agent, gold, ordered=True, tol_abs=0.01, tol_rel=0.001
    )


def test_rows_match_ordered_ties() -> None:
    agent = [
        ("Rock", 10),
        ("Jazz", 10),
        ("Pop", 5),
    ]
    gold = [
        ("Jazz", 10),
        ("Rock", 10),
        ("Pop", 5),
    ]
    assert evaluate.rows_match(agent, gold, ordered=True, tol_abs=0.01, tol_rel=0.001)


def test_ordered_ties_cannot_cross_groups() -> None:
    agent = [("Pop", 5), ("Jazz", 10), ("Rock", 10)]
    gold = [("Jazz", 10), ("Rock", 10), ("Pop", 5)]
    assert not evaluate.rows_match(
        agent, gold, ordered=True, tol_abs=0.01, tol_rel=0.001
    )


def test_column_mapping_extra_id() -> None:
    mapping = evaluate.column_mapping(
        ["ArtistId", "artist_name", "album_count"], ["artist", "album_count"]
    )
    assert mapping == [1, 2]


def test_column_mapping_unknown_column() -> None:
    assert evaluate.column_mapping(["foo"], ["artist", "album_count"]) is None


def test_selection_scores() -> None:
    class Result:
        tables = ["invoices", "customers"]

    item = {
        "required_tables": ["invoices"],
        "allowed_extra_tables": ["customers"],
        "forbidden_tables": [],
    }
    precision, recall, flags = evaluate.selection_scores(
        item, Result(), {"invoices", "customers"}
    )
    assert precision == 1.0
    assert recall == 1.0
    assert not flags["forbidden"]

    item["forbidden_tables"] = ["customers"]
    _, _, flags = evaluate.selection_scores(item, Result(), {"invoices", "customers"})
    assert flags["forbidden"]


def test_metric_ok_family() -> None:
    class Result:
        metric = {"id": "revenue_line", "variant_of": "revenue"}

    assert evaluate.metric_ok({"expected_metric": "revenue"}, Result())
    assert evaluate.metric_ok({"expected_metric": "revenue_line"}, Result())
    assert not evaluate.metric_ok({"expected_metric": "sales_volume"}, Result())


def test_summarize_cases_median() -> None:
    class Run:
        def __init__(self, correct: bool) -> None:
            self.correct = correct

    class Case:
        def __init__(self, item: dict, runs: list[Run]) -> None:
            self.item = item
            self.runs = runs

        @property
        def correct(self) -> bool:
            return sum(1 for run in self.runs if run.correct) * 2 >= len(self.runs)

    cases = [
        Case({"id": "a"}, [Run(True), Run(True), Run(False)]),
        Case({"id": "b"}, [Run(True), Run(True), Run(True)]),
    ]
    summary = evaluate.summarize_cases(cases, runs=3)
    assert summary["cases"] == 2
    assert summary["run_correct"] == [2, 2, 1]
    assert summary["median_run_correct"] == 2
    assert summary["run_span"] == 1


def test_verify_gold_offline(catalog) -> None:
    gold_path = evaluate.PROJECT_DIR / "eval" / "gold.jsonl"
    problems = evaluate.verify_gold(
        evaluate.load_gold(gold_path), catalog, verbose=False
    )
    assert problems == []
