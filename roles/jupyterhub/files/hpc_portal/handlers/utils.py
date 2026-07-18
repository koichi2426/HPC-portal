"""画面Handler間で共有する表示用ユーティリティを提供する。"""

def _hpc_format_storage_bytes(value: int) -> str:
    """ストレージ使用量を管理画面向けの短い表記にする。

    Args:
        value: ストレージ使用量のバイト数。

    Returns:
        単位を付けて整形した使用量。
    """
    size = float(max(value, 0))
    units = ("B", "KB", "MB", "GB", "TB", "PB")
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"

