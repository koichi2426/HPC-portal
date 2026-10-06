"""容量の単位表記と、空きリソースの判断基準。"""


def format_storage_bytes(value: int) -> str:
    """バイト数を、単位付きの短いストレージ使用量へ整える。

    Args:
        value: 表示するストレージ使用量。単位はバイト。

    Returns:
        B・KB・MBなどの単位付き使用量。
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
    """0〜100の空き率を、画面表示用の混雑度へ変換する。

    Args:
        available_pct: リソースの空き率。範囲は0〜100。

    Returns:
        余裕あり・やや混雑・逼迫のいずれか。
    """
    if available_pct >= 50:
        return "余裕あり"
    if available_pct >= 25:
        return "やや混雑"
    return "逼迫"


def memory_display_label(memory: str) -> str:
    """Slurmの32G・32GB表記を、画面用の32 GB表記へ整える。

    Args:
        memory: 表示するメモリ量。数値またはG・GB表記を使う。

    Returns:
        GB単位の表示文字列。
    """
    normalized = str(memory).strip().upper()
    if normalized.endswith("GB"):
        normalized = normalized[:-2]
    elif normalized.endswith("G"):
        normalized = normalized[:-1]
    return f"{normalized} GB"
