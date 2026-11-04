"""GW33 task 5.4 / Property 5: the single-authority AST gate."""
from __future__ import annotations

from pathlib import Path

import pytest

from lint.check_single_authority import ALLOW_LIST, main, scan_tree

_PKG = Path(__file__).resolve().parents[2] / "gateway_v2"
_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "single_authority"


def test_property_5_the_shipped_tree_is_clean() -> None:
    """Feature: gw33-output-parity, Property 5: Single authority. Validates 5.3."""
    # Feature: gw33-output-parity, Property 5: Single authority
    assert scan_tree(_PKG) == []
    assert main([str(_PKG)]) == 0


@pytest.mark.parametrize("fixture", sorted(p.name for p in _FIXTURES.glob("bad_*.py")))
def test_property_5_each_planted_violation_fails(fixture: str, tmp_path: Path) -> None:
    # Feature: gw33-output-parity, Property 5: Single authority
    (tmp_path / fixture).write_text((_FIXTURES / fixture).read_text(encoding="utf-8"), "utf-8")
    hits = scan_tree(tmp_path, check_stale=False)
    assert hits, fixture


def test_a_dynamic_kind_is_not_flagged(tmp_path: Path) -> None:
    (tmp_path / "ok.py").write_text((_FIXTURES / "clean_ok.py").read_text("utf-8"), "utf-8")
    assert scan_tree(tmp_path, check_stale=False) == []


def test_fixtures_cover_both_rules_and_every_mapping_shape() -> None:
    names = {p.name for p in _FIXTURES.glob("bad_*.py")}
    assert {
        "bad_rule_a_if_chain.py", "bad_rule_a_dict.py", "bad_rule_a_match.py",
        "bad_rule_a_module_dict.py", "bad_rule_b_keyword.py", "bad_rule_b_positional.py",
    } <= names


def test_the_resolver_and_domain_are_exempt(tmp_path: Path) -> None:
    for top in ("resolve", "domain"):
        target = tmp_path / top
        target.mkdir()
        (target / "m.py").write_text((_FIXTURES / "bad_rule_b_keyword.py").read_text("utf-8"), "utf-8")
    assert scan_tree(tmp_path, check_stale=False) == []


def test_a_stale_allow_list_entry_fails(tmp_path: Path) -> None:
    assert len(ALLOW_LIST) == 2 and all(reason for _p, _n, reason in ALLOW_LIST)
    hits = scan_tree(tmp_path)  # an empty tree: every allow-list entry is stale
    assert {h.symbol.split(": ", 1)[0] for h in hits} == {"stale allow-list entry"}
    assert len(hits) == len(ALLOW_LIST)


def test_an_allow_listed_site_is_permitted(tmp_path: Path) -> None:
    path = tmp_path / "x" / "m.py"
    path.parent.mkdir()
    path.write_text(
        "from gateway_v2.domain import Span, Transformation\n"
        "class R:\n"
        "    def decide(self):\n"
        "        return Transformation(kind='redact', span=Span(0, 1), replacement='x')\n",
        "utf-8",
    )
    assert scan_tree(tmp_path, check_stale=False)
    allow = (("x/m.py", "R.decide", "test"),)
    assert scan_tree(tmp_path, allow_list=allow, check_stale=False) == []
    assert scan_tree(tmp_path, allow_list=(("x/m.py", "R.gone", "t"),)) != []


_ITERATIONS = 120


def test_property_5_random_planted_violations_are_always_caught_and_clean_code_never() -> None:
    """Feature: gw33-output-parity, Property 5: Single authority. Validates 5.3."""
    # Feature: gw33-output-parity, Property 5: Single authority
    import random
    import tempfile

    rng = random.Random(33_005)
    members = ("REDACT", "BLOCK", "FLAG", "REWRITE", "ALLOW")
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        for index in range(_ITERATIONS):
            name = f"fn_{rng.randrange(10**6)}"
            a, b = rng.choice(members), rng.choice(members)
            kind = rng.choice(["if", "dict", "match", "edit_kw", "edit_pos"])
            source = "from gateway_v2.domain import Action, Disposition, Span, Transformation\n"
            if kind == "if":
                source += f"def {name}(x):\n    if x is Action.{a}:\n        return Disposition.{b}\n    return 0\n"
            elif kind == "dict":
                source += f"def {name}():\n    return {{Action.{a}: Disposition.{b}}}\n"
            elif kind == "match":
                source += (f"def {name}(x):\n    match x:\n        case Action.{a}:\n"
                           f"            return Disposition.{b}\n")
            elif kind == "edit_kw":
                source += (f"def {name}():\n    return Transformation(kind='{rng.choice(['rewrite', 'redact'])}',"
                           " span=Span(0, 1), replacement='x')\n")
            else:
                source += (f"def {name}():\n    return Transformation('{rng.choice(['rewrite', 'redact'])}',"
                           " Span(0, 1), 'x')\n")
            (root / f"bad_{index}.py").write_text(source, "utf-8")
            clean = f"from gateway_v2.domain import Action\ndef {name}(x):\n    return x is Action.{a}\n"
            (root / f"ok_{index}.py").write_text(clean, "utf-8")
        hits = {Path(h.path).name for h in scan_tree(root, check_stale=False)}
        assert {f"bad_{i}.py" for i in range(_ITERATIONS)} <= hits
        assert not any(name.startswith("ok_") for name in hits)
