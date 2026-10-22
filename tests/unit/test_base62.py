"""单元测试：Base62 编解码 —— 测试设计文档 §2.1（含边界值）"""

import pytest

from app.utils.base62 import decode, encode


class TestEncode:
    @pytest.mark.parametrize(
        ("number", "expected"),
        [
            (0, "0"),  # 边界：最小 ID
            (1, "1"),
            (61, "z"),
            (62, "10"),  # 边界：首次进位
            (3844, "100"),  # 62^2 进位
        ],
    )
    def test_known_values(self, number, expected):
        assert encode(number) == expected

    def test_negative_raises(self):
        with pytest.raises(ValueError):
            encode(-1)


class TestDecode:
    @pytest.mark.parametrize(
        ("code", "expected"),
        [
            ("0", 0),
            ("z", 61),
            ("10", 62),
            ("100", 3844),
            ("zz", 3843),
            ("1000", 238328),  # 62^3
        ],
    )
    def test_known_values(self, code, expected):
        assert decode(code) == expected


class TestRoundtrip:
    """编码后再解码的一致性：覆盖 0、常规值、最大 ID 值"""

    @pytest.mark.parametrize(
        "number",
        [
            0,
            1,
            61,
            62,
            63,
            3844,
            238327,
            2**53 - 1,
            10**15,
            2**62 - 1,  # 发号器长期运行也不会碰到的大 ID 上限
        ],
    )
    def test_consistency(self, number):
        assert decode(encode(number)) == number
