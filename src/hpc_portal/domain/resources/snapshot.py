"""容量の単位表記と、空きリソースの判断基準。"""


def format_storage_bytes(value: int) -> str:
    """バイト数を、単位付きの短いストレージ使用量へ整える。"""
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
    """0〜100の空き率を、画面表示用の混雑度へ変換する。"""
    if available_pct >= 50:
        return "余裕あり"
    if available_pct >= 25:
        return "やや混雑"
    return "逼迫"


def memory_display_label(memory: str) -> str:
    """Slurmの32G・32GB表記を、画面用の32 GB表記へ整える。"""
    normalized = str(memory).strip().upper()
    if normalized.endswith("GB"):
        normalized = normalized[:-2]
    elif normalized.endswith("G"):
        normalized = normalized[:-1]
    return f"{normalized} GB"
