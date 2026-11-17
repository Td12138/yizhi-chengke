"""模型输出 JSON 的容错解析：围栏、尾随解释、LaTeX 反斜杠、尾随逗号。"""

import pytest

from app.providers.llm import _loads


def test_plain_json():
    assert _loads('{"a": 1}') == {"a": 1}


def test_ignores_trailing_prose():
    text = '{"questions": [{"stem": "x"}]}\n以上是 5 道题，请查收。'
    assert _loads(text)["questions"][0]["stem"] == "x"


def test_code_fence_and_prose():
    text = "```json\n{\"a\": [1, 2]}\n```\n说明：已按需求出题。"
    assert _loads(text) == {"a": [1, 2]}


def test_latex_single_backslash():
    # JSON 里写 \frac 是非法的，模型经常这么干
    text = '{"solution": "$\\frac{1}{2}$"}'
    assert _loads(text)["solution"] == "$\\frac{1}{2}$"


def test_latex_backslash_in_streamed_text():
    text = '{"solution": "\\\\frac{1}{2}", "stem": "$x^2$"}'
    assert _loads(text)["solution"] == "\\frac{1}{2}"


def test_trailing_comma():
    text = '{"a": 1, "b": [1, 2,],}'
    assert _loads(text) == {"a": 1, "b": [1, 2]}


def test_braces_inside_string_do_not_confuse_scanner():
    text = '{"stem": "集合 {1,2} 的子集", "n": 2}'
    assert _loads(text)["n"] == 2


def test_rejects_non_json():
    with pytest.raises(ValueError):
        _loads("抱歉，我无法完成这个请求。")
