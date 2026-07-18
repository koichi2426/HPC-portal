"""PydanticによるAPI入力検証の共通処理を提供する。"""

import json
from typing import TypeVar

from pydantic import BaseModel, ValidationError

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class HpcRequestValidationError(ValueError):
    """利用者へ返せる安全なAPI入力エラー。"""


def _validation_error_message(error: ValidationError) -> str:
    """Pydanticの検証エラーを秘密値を含まない日本語へ変換する。

    Args:
        error: Pydanticが生成した入力検証エラー。

    Returns:
        API利用者へ返す日本語メッセージ。
    """
    first = error.errors(include_input=False, include_url=False)[0]
    field = str(first.get("loc", ("入力",))[-1])
    error_type = str(first.get("type", ""))
    labels = {
        "action": "操作",
        "username": "ユーザー名",
        "display_name": "表示名",
        "sudo": "sudo設定",
        "model": "モデル名",
        "cpus": "CPU割り当て",
        "memory": "メモリ割り当て",
        "current_password": "現在のパスワード",
        "new_password": "新しいパスワード",
        "confirm_password": "確認用パスワード",
    }
    label = labels.get(field, field)
    if field == "action" and error_type in {"literal_error", "missing"}:
        return "不明な action です"
    if error_type == "missing":
        return f"{label}が必要です"
    if error_type in {"bool_type", "bool_parsing"}:
        return f"{label}はtrueまたはfalseで指定してください"
    if error_type in {"string_type", "string_sub_type"}:
        return f"{label}は文字列で指定してください"
    return f"{label}の形式が不正です"


def parse_json_request(raw_body: bytes, schema: type[SchemaT]) -> SchemaT:
    """JSONリクエスト本文を指定Schemaで検証する。

    Args:
        raw_body: HTTPリクエストの本文。
        schema: 検証に利用するPydantic Model。

    Returns:
        検証・正規化済みのModel。

    Raises:
        HpcRequestValidationError: JSONまたは入力項目が不正な場合。
    """
    try:
        value = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HpcRequestValidationError("JSON形式のリクエストを送信してください") from exc
    if not isinstance(value, dict):
        raise HpcRequestValidationError("JSONオブジェクトを送信してください")
    try:
        return schema.model_validate(value)
    except ValidationError as exc:
        raise HpcRequestValidationError(_validation_error_message(exc)) from exc
