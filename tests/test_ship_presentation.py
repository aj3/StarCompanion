import pytest

from starcompanion.ship_presentation import (
    ShipPrefix,
    parse_ship_prefix,
    set_ship_favorite,
    set_ship_order,
)
from starcompanion.user_edits import UserEditError


def test_favorite_and_order_compose_without_damaging_numeric_ship_names():
    assert parse_ship_prefix("300i") == ShipPrefix("300i")
    favorite = set_ship_favorite("300i", None, True)
    assert favorite == "*300i"
    ordered = set_ship_order("300i", favorite, 5)
    assert ordered == "*05-300i"
    assert parse_ship_prefix(ordered) == ShipPrefix("300i", True, 5)


def test_actions_preserve_custom_names_and_collapse_back_to_source():
    assert set_ship_favorite("Avenger", "My Avenger", True) == "*My Avenger"
    assert set_ship_order("Avenger", "*My Avenger", 3) == "*03-My Avenger"
    assert set_ship_favorite("Avenger", "*03-Avenger", False) == "03-Avenger"
    assert set_ship_order("Avenger", "03-Avenger", None) is None


def test_order_and_prefix_validation_fail_closed():
    for value in (0, 100, True):
        with pytest.raises(UserEditError):
            set_ship_order("Avenger", None, value)
    for prefix in ("", "ab", " ", "1", "-"):
        with pytest.raises(UserEditError):
            set_ship_favorite("Avenger", None, True, prefix=prefix)
    with pytest.raises(UserEditError):
        parse_ship_prefix("*01-")
