"""语言参数校验。"""

from __future__ import annotations

import pytest

from app.core.languages import SUPPORTED_LANGUAGES, is_supported, normalize_language
from app.errors import InvalidLanguageError


@pytest.mark.parametrize("value", ["zh", "ZH", " zh ", "zh-CN", "zh_cn", "cn", "中文", "普通话"])
def test_chinese_aliases(value):
    assert normalize_language(value) == "zh"


@pytest.mark.parametrize("value", ["en", "EN", " en ", "en-US", "en_GB", "eng", "英文", "English"])
def test_english_aliases(value):
    assert normalize_language(value) == "en"


@pytest.mark.parametrize("value", ["fr", "ja", "de", "auto", "", "   ", "zh-tw "])
def test_invalid_values_raise(value):
    # 注意：zh-TW 不在支持列表内，应按非法处理
    with pytest.raises(InvalidLanguageError):
        normalize_language(value)


def test_none_raises():
    with pytest.raises(InvalidLanguageError):
        normalize_language(None)


def test_is_supported_helper():
    assert is_supported("zh")
    assert is_supported("en-US")
    assert not is_supported("fr")
    assert not is_supported(None)


def test_supported_languages_constant():
    assert SUPPORTED_LANGUAGES == ("zh", "en")


def test_api_rejects_unsupported_language(client, tone_wav):
    response = client.post(
        "/api/transcribe",
        files={"file": ("a.wav", tone_wav, "audio/wav")},
        data={"language": "ja"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_LANGUAGE"
