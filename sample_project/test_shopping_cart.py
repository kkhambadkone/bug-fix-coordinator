from shopping_cart import (
    apply_discount,
    calculate_total,
    apply_bulk_discount,
    calculate_tax,
    format_receipt_total,
)


def test_apply_discount():
    assert apply_discount(200, 10) == 180


def test_calculate_total():
    items = [{"price": 10, "qty": 2}, {"price": 5, "qty": 3}]
    assert calculate_total(items) == 35


def test_bulk_discount():
    assert apply_bulk_discount(150) == 135
    assert apply_bulk_discount(50) == 50


def test_calculate_tax():
    assert calculate_tax(200, 8) == 16


def test_format_receipt_total():
    assert format_receipt_total(100, 8, 5) == 113
