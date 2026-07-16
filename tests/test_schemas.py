"""管理APIのJSON入力Schemaを外部から見えるエラーと正規化で検証する。"""

import pytest

from hpc_portal.schemas import (
    HpcAdminUsersRequest,
    HpcLlmApiRequest,
    HpcPasswordChangeRequest,
    HpcRequestValidationError,
    parse_json_request,
)


def test_admin_request_normalizes_text_and_ignores_unknown_fields():
    request = parse_json_request(
        '{"action":" CREATE ","username":" User_01 ","display_name":"  研究 🚀  ","sudo":true,"ignored":"value"}'.encode(),
        HpcAdminUsersRequest,
    )

    assert request.action == "create"
    assert request.username == "user_01"
    assert request.display_name == "研究 🚀"
    assert request.sudo is True
    assert not hasattr(request, "ignored")


@pytest.mark.parametrize("raw", [b"", b"{}", b'{"username":"user01"}'])
def test_admin_request_requires_known_action(raw):
    with pytest.raises(HpcRequestValidationError, match="不明な action"):
        parse_json_request(raw, HpcAdminUsersRequest)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (b"not-json", "JSON形式"),
        (b"\xff", "JSON形式"),
        (b"[]", "JSONオブジェクト"),
        (b'{"action":"unknown"}', "不明な action"),
        (b'{"action":"create","sudo":"true"}', "sudo設定はtrueまたはfalse"),
        (b'{"action":"create","username":1}', "ユーザー名は文字列"),
    ],
)
def test_invalid_request_returns_safe_japanese_message(raw, message):
    with pytest.raises(HpcRequestValidationError, match=message) as exc_info:
        parse_json_request(raw, HpcAdminUsersRequest)

    assert "test-only-master-key" not in str(exc_info.value)
    decoded_input = raw.decode("utf-8", errors="ignore")
    if decoded_input:
        assert decoded_input not in str(exc_info.value)


def test_null_optional_text_is_normalized_to_empty_string():
    request = parse_json_request(
        b'{"action":"ollama_pull","model":null}', HpcAdminUsersRequest
    )

    assert request.model == ""


def test_llm_api_request_normalizes_regenerate_action():
    request = parse_json_request(b'{"action":" REGENERATE "}', HpcLlmApiRequest)

    assert request.action == "regenerate"


def test_password_request_rejects_missing_and_non_string_values():
    with pytest.raises(HpcRequestValidationError, match="確認用パスワードが必要"):
        parse_json_request(
            b'{"current_password":"old","new_password":"new"}',
            HpcPasswordChangeRequest,
        )
    with pytest.raises(HpcRequestValidationError, match="現在のパスワードは文字列"):
        parse_json_request(
            b'{"current_password":1,"new_password":"new","confirm_password":"new"}',
            HpcPasswordChangeRequest,
        )
