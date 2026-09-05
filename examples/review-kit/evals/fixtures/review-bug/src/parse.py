"""Small fixture with a reviewable edge case."""


def parse_ratio(value):
    left, right = value.split(":")
    return int(left) / int(right)
