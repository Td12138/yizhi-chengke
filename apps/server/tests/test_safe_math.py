"""安全边界：模型给的 verify 只能当数学表达式，不能当 Python 执行。"""

from __future__ import annotations

import pytest

from app import safe_math
from app.agents.verify import check_question

# 评审给出的两个实锤 payload，外加常见同类逃逸写法
MALICIOUS_EXPRS = [
    "__import__('os').getcwd()",
    "open('pwned.txt', 'w').write('x')",
    "eval('1+1')",
    "exec('x = 1')",
    "getattr(__builtins__, 'eval')('1')",
    "().__class__.__bases__[0].__subclasses__()",
    "[x for x in range(3)]",
    "{'a': 1}",
    "x.__dict__",
    "os.system('echo hi')",
]


def test_import_payload_is_rejected():
    passed, detail = check_question(
        {"verify": {"expr": "__import__('os').getcwd()", "subs": {}, "expect": 0}}
    )

    assert passed is False
    assert "非法字符" in detail or "未知标识符" in detail


def _workdir(tmp_path):
    """独立的 cwd：tmp_path 里还有测试库文件，不能拿它当「没创建文件」的判据。"""
    workdir = tmp_path / "cwd"
    workdir.mkdir(exist_ok=True)
    return workdir


def test_open_payload_creates_no_file(tmp_path):
    target = tmp_path / "pwned.txt"
    expr = f"open({str(target)!r}, 'w').write('x')"

    passed, _ = check_question({"verify": {"expr": expr, "subs": {}, "expect": 0}})

    assert passed is False
    assert not target.exists()


@pytest.mark.parametrize("expr", MALICIOUS_EXPRS)
def test_malicious_expressions_are_rejected_without_side_effects(expr, tmp_path, monkeypatch):
    workdir = _workdir(tmp_path)
    monkeypatch.chdir(workdir)

    passed, _ = check_question({"verify": {"expr": expr, "subs": {}, "expect": 0}})

    assert passed is False
    assert list(workdir.iterdir()) == []


@pytest.mark.parametrize(
    "subs",
    [
        {"x": "__import__('os').getcwd()"},
        {"x": "1+1"},
        {"x": "open('pwned.txt','w')"},
        {"x1": 2},
        {"xy": 2},
        {"x": [1, 2]},
        {"x": {"a": 1}},
        {"x": True},
    ],
)
def test_subs_reject_non_numeric_values(subs, tmp_path, monkeypatch):
    workdir = _workdir(tmp_path)
    monkeypatch.chdir(workdir)

    passed, detail = check_question(
        {"verify": {"expr": "x", "subs": subs, "expect": 1}}
    )

    assert passed is False
    assert "subs" in detail or "无法解析" in detail
    assert list(workdir.iterdir()) == []


def test_subs_accept_numeric_strings():
    passed, _ = check_question(
        {"verify": {"expr": "x + 1", "subs": {"x": "2"}, "expect": 3}}
    )

    assert passed is True


@pytest.mark.parametrize("expect", ["C", None, [1], {"a": 1}])
def test_non_numeric_expect_is_rejected_not_raised(expect):
    passed, detail = check_question(
        {"verify": {"expr": "1 + 1", "subs": {}, "expect": expect}}
    )

    assert passed is False
    assert "期望值不是数值" in detail


def test_normal_expression_still_passes_with_same_value():
    passed, detail = check_question(
        {"verify": {"expr": "x**2 - 4*x + 3", "subs": {"x": 2}, "expect": -1.0}}
    )

    assert passed is True
    assert "= -1" in detail


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        ("sqrt(9)", 3.0),
        ("abs(-2)", 2.0),
        ("sin(0)", 0.0),
        ("cos(0)", 1.0),
        ("tan(0)", 0.0),
        ("exp(0)", 1.0),
        ("log(1)", 0.0),
        ("ln(1)", 0.0),
        ("x^2 + 1", 5.0),
    ],
)
def test_whitelisted_functions_and_power(expr, expected):
    parsed = safe_math.parse_expression(expr)
    value = float(parsed.subs({safe_math.sp.Symbol("x"): 2}))
    assert value == pytest.approx(expected)


def test_safe_math_rejects_empty_and_overlong():
    with pytest.raises(safe_math.UnsafeInput):
        safe_math.parse_expression("")
    with pytest.raises(safe_math.UnsafeInput):
        safe_math.parse_expression("1 + " * (safe_math.MAX_EXPR_CHARS))
