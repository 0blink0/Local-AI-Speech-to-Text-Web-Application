"""识别语言的校验与归一化。"""

from __future__ import annotations

from app.errors import InvalidLanguageError

# 输出给前端/API 的标准语言码
SUPPORTED_LANGUAGES: tuple[str, ...] = ("zh", "en")

# 常见别名 → 标准语言码，容错前端或调用方传入 "zh-CN" / "chinese" 之类的写法
_ALIASES: dict[str, str] = {
    "zh": "zh",
    "zh-cn": "zh",
    "zh_cn": "zh",
    "zh-hans": "zh",
    "chs": "zh",
    "cn": "zh",
    "cmn": "zh",
    "chinese": "zh",
    "mandarin": "zh",
    "中文": "zh",
    "汉语": "zh",
    "普通话": "zh",
    "en": "en",
    "en-us": "en",
    "en_us": "en",
    "en-gb": "en",
    "en_gb": "en",
    "eng": "en",
    "english": "en",
    "英文": "en",
    "英语": "en",
}


def normalize_language(value: str | None) -> str:
    """把用户输入的语言参数归一化为 ``zh`` 或 ``en``。

    Raises:
        InvalidLanguageError: 参数缺失或不在支持列表内。
    """
    if value is None:
        raise InvalidLanguageError("缺少 language 参数，请指定 'zh' 或 'en'。")

    key = value.strip().lower()
    if not key:
        raise InvalidLanguageError("language 参数不能为空，请指定 'zh' 或 'en'。")

    normalized = _ALIASES.get(key)
    if normalized is None:
        raise InvalidLanguageError(
            f"不支持的识别语言 {value!r}，仅支持 'zh'（中文）或 'en'（英文）。"
        )
    return normalized


def is_supported(value: str | None) -> bool:
    try:
        normalize_language(value)
    except InvalidLanguageError:
        return False
    return True
