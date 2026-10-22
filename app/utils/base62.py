"""Base62 编解码：发号器方案，把全局唯一数字 ID 转成短码"""

ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
BASE = len(ALPHABET)


def encode(number: int) -> str:
    """数字 -> Base62 短码"""
    if number < 0:
        raise ValueError("不支持负数")
    if number == 0:
        return ALPHABET[0]
    chars = []
    while number:
        number, rem = divmod(number, BASE)
        chars.append(ALPHABET[rem])
    return "".join(reversed(chars))


def decode(code: str) -> int:
    """Base62 短码 -> 数字"""
    number = 0
    for ch in code:
        number = number * BASE + ALPHABET.index(ch)
    return number
