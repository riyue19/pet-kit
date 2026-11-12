import pytest

from action.builtin import CODE_MAX_CHARS, CSS_MAX_CHARS, code_message, css_message


def test_code_message_normalizes_fields():
    assert code_message({"code": "x = 1", "title": "  标题  ", "language": "python"}) == {
        "type": "code", "title": "标题", "language": "python", "code": "x = 1"
    }
    m = code_message({"code": "x", "title": "t" * 200, "language": "no spaces allowed"})
    assert len(m["title"]) == 60 and m["language"] == ""
    assert code_message({"code": "x"})["title"] == "代码"


@pytest.mark.parametrize("args", [{}, {"code": ""}, {"code": "   \n"}, {"code": 5}, {"code": "x" * (CODE_MAX_CHARS + 1)}])
def test_code_message_refuses_bad_input(args):
    assert isinstance(code_message(args), str)


def test_css_message_accepts_plain_css_and_reset():
    assert css_message({"css": "  #bubble { --b-bg: red; }  "}) == {"type": "set_css", "css": "#bubble { --b-bg: red; }", "replace": False}
    assert css_message({"css": "", "replace": True}) == {"type": "set_css", "css": "", "replace": True}
    assert css_message({"css": "#a{}", "replace": "yes"})["replace"] is False  # only a real true counts


@pytest.mark.parametrize(
    "args",
    [
        {},
        {"css": ""},
        {"css": 3},
        {"css": "x" * (CSS_MAX_CHARS + 1)},
        {"css": "@import url(http://evil/x.css);"},
        {"css": "#a { background: url(http://evil/x.png) }"},
        {"css": "#a{} </style><script>alert(1)</script>"},
        {"css": "#a { width: expression(alert(1)) }"},
        {"css": "#a { background: URL ( x ) }"},
    ],
)
def test_css_message_refuses_risky_input(args):
    assert isinstance(css_message(args), str)
