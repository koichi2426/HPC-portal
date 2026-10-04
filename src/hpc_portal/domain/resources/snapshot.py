"""容量の単位表記と、空きリソースの判断基準。"""


def format_storage_bytes(value: int) -> str:
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


def resource_status(available_pct):
    """空き率を画面表示用の混雑度へ変換する。

    Args:
        available_pct: 0〜100の空き率。

    Returns:
        余裕あり、やや混雑、逼迫のいずれか。
    """
    if available_pct >= 50:
        return "余裕あり"
    if available_pct >= 25:
        return "やや混雑"
    return "逼迫"


def memory_display_label(memory: str) -> str:
    """Slurm形式のメモリ値を画面表示用のGB表記へ変換する。

    Args:
        memory: ``32G``または``32GB``形式のメモリ値。

    Returns:
        ``32 GB``形式の表示値。
    """
    normalized = str(memory).strip().upper()
    if normalized.endswith("GB"):
        normalized = normalized[:-2]
    elif normalized.endswith("G"):
        normalized = normalized[:-1]
    return f"{normalized} GB"
