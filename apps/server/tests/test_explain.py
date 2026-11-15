"""mock 讲解：按题干分类、参数取题目系数，不再是一段固定文案。"""

from __future__ import annotations

import asyncio

from app.providers import fixtures, llm

AXIS_STEM = r"求 $y = x^{2} - 4x + 3$ 的对称轴。"
VERTEX_STEM = r"求 $y = x^{2} - 4x + 3$ 的顶点坐标。"
INTERSECTION_STEM = r"$y = x^{2} - 4x + 3$ 与 $x$ 轴的交点个数是？"
SQUARE_STEM = r"把 $y = x^{2} - 4x + 3$ 化为顶点式。"
VALUE_STEM = r"若 $y = x^{2} - 4x + 3$ 在 $x = 2$ 处的函数值为 $m$，则 $m$ 等于？"
ALL_STEMS = [AXIS_STEM, VERTEX_STEM, INTERSECTION_STEM, SQUARE_STEM, VALUE_STEM]


def _explain(stem: str, user_answer: str = "3") -> dict:
    return fixtures.mock_explain(
        {"stem_latex": stem, "qtype": "fill", "answer": ""}, user_answer
    )


def test_each_topic_gets_its_own_script():
    scripts = [_explain(stem)["script"] for stem in ALL_STEMS]
    assert len(set(scripts)) == len(ALL_STEMS)


def test_axis_script_matches_stem():
    script = _explain(AXIS_STEM)["script"]
    assert "对称轴" in script
    assert "x = -b/(2a)" in script
    assert "= 2" in script


def test_vertex_script_points_at_the_question_vertex():
    script = _explain(VERTEX_STEM)["script"]
    assert "(2, -1)" in script


def test_intersection_script_uses_discriminant():
    script = _explain(INTERSECTION_STEM)["script"]
    assert "Δ" in script
    assert "= 4" in script
    assert "两个交点" in script


def test_complete_square_script_shows_vertex_form():
    script = _explain(SQUARE_STEM)["script"]
    assert "配方" in script
    assert "(x - 2)² - 1" in script


def test_value_script_walks_through_substitution():
    script = _explain(VALUE_STEM)["script"]
    assert "x = 2" in script
    assert "-1" in script


def test_params_follow_question_coefficients():
    explain = _explain(r"求 $y = 2x^{2} + 4x - 6$ 的对称轴。")
    params = explain["params"]

    assert (params["a"], params["b"], params["c"]) == (2.0, 4.0, -6.0)
    # 对称轴 -b/2a = -1，画图窗口要把它包进去
    assert params["x_min"] < -1.0 < params["x_max"]


def test_params_fall_back_to_sample_coefficients():
    params = _explain("题干里没有函数式")["params"]
    assert (params["a"], params["b"], params["c"]) == (1.0, -4.0, 3.0)


def test_explain_shape_stays_compatible_with_frontend():
    explain = _explain(AXIS_STEM)

    assert set(explain) == {"template", "title", "params", "script"}
    assert set(explain["params"]) == {"a", "b", "c", "x_min", "x_max"}
    assert explain["template"] == "function_slider"
    assert all(isinstance(value, float) for value in explain["params"].values())


def test_llm_mock_routes_question_into_explain():
    result = asyncio.run(
        llm.chat_json(
            "explain",
            {"question": {"stem_latex": AXIS_STEM}, "user_answer": "3"},
        )
    )
    assert "对称轴" in result["script"]
    assert "你填的是「3」" in result["script"]


def test_user_answer_is_quoted_only_when_present():
    assert "你填的是「-2」" in _explain(AXIS_STEM, "-2")["script"]
    assert "你填的是" not in _explain(AXIS_STEM, "")["script"]
