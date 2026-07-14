"""A tiny e-commerce module with four independent, seeded bugs.

Bug 1 (apply_discount): subtracts the raw percentage number from the price
instead of applying it as a percentage.

Bug 2 (calculate_total): forgets to multiply by quantity.

Bug 3 (calculate_tax): treats tax_rate_percent as a flat amount instead of
a percentage.

Bug 4 (format_receipt_total): forgets to include shipping in the total.

The coordinator agent in ../coordinator.py will discover and fix all of
these across separate iterations.
"""


def apply_discount(price, discount_percent):
    return price - (price * discount_percent / 100)


def calculate_total(items):
    total = 0
    for item in items:
        total = total + item["price"] * item["qty"]
    return total


def apply_bulk_discount(total, threshold=100, discount_percent=10):
    if total > threshold:
        return apply_discount(total, discount_percent)
    return total


# Expected value = subtotal * (1 + tax_rate_percent / 100)
# The expected value is calculated by applying the tax rate to the subtotal.
def calculate_tax(subtotal, tax_rate_percent):
    # The expected value is calculated by applying the tax rate to the subtotal.
    return round(subtotal * (tax_rate_percent / 100), 2)


def format_receipt_total(subtotal, tax, shipping):
    return round(subtotal + tax + shipping, 2)
