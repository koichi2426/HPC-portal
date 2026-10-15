"""本人の起動引数から表示用の名前だけを抽出し、認証や接続先の判定には使わない。"""

import re
from pathlib import PurePath

import psutil


def _file_name(value, suffixes):
    """対応する拡張子のファイル名だけを取り出す。

    Args:
        value: 起動対象として指定されたパス。
        suffixes: 表示対象として認識する拡張子。

    Returns:
        表示可能なファイル名。判別できない場合は空文字列。
    """
    name = PurePath(value).name
    if PurePath(name).suffix not in suffixes or not name.isprintable():
        return ""
    return name


def command_display_name(process_name, arguments):
    """一般的なPython・Node.js・Javaの起動方法から対象名を判別する。

    Args:
        process_name: 判別できない場合に使用するOSのプロセス名。
        arguments: 一時的に読み取った起動引数。保存やログ出力はしない。

    Returns:
        ファイル名・モジュール名と実行環境、または元のプロセス名。
    """
    if not arguments:
        return process_name

    executable = PurePath(arguments[0]).name
    if re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", executable):
        language, suffixes = "Python", {".py", ".pyw", ".pyz"}
        value_options = {"-W", "-X"}
        switches = {"-u", "-B", "-E", "-I", "-O", "-OO", "-s", "-S", "-P", "-q", "-v"}
        prefixes = ("-W", "-X")
    elif executable in {"node", "nodejs"}:
        language, suffixes = "Node.js", {".js", ".mjs", ".cjs"}
        value_options = {"-r", "--require", "--import"}
        switches = {
            "--watch",
            "--watch-preserve-output",
            "--enable-source-maps",
            "--no-warnings",
            "--inspect",
            "--inspect-brk",
        }
        prefixes = ("--inspect=", "--inspect-brk=", "--require=", "--import=")
    elif executable == "java":
        language, suffixes = "Java", {".jar"}
        value_options = {
            "-cp",
            "-classpath",
            "--class-path",
            "-p",
            "--module-path",
            "--add-opens",
            "--add-exports",
            "--add-modules",
        }
        switches = {
            "-ea",
            "-enableassertions",
            "-da",
            "-disableassertions",
            "-server",
            "-client",
        }
        prefixes = (
            "-D",
            "-X",
            "--class-path=",
            "--module-path=",
            "--add-opens=",
            "--add-exports=",
            "--add-modules=",
        )
    else:
        return process_name

    index = 1
    while index < len(arguments):
        argument = arguments[index]
        if language == "Python" and argument == "-m":
            module = arguments[index + 1] if index + 1 < len(arguments) else ""
            if re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", module):
                return f"{module}（Python）"
            return process_name
        if language == "Java" and argument == "-jar":
            value = arguments[index + 1] if index + 1 < len(arguments) else ""
            name = _file_name(value, suffixes)
            return f"{name}（Java）" if name else process_name
        if argument in value_options:
            index += 2
            continue
        if argument in switches or argument.startswith(prefixes):
            index += 1
            continue
        if argument == "--" and language != "Java":
            index += 1
            argument = arguments[index] if index < len(arguments) else ""
        # 不明なオプションの値や、アプリへ渡す引数を起動ファイルと誤認しない。
        if argument.startswith("-") or language == "Java":
            return process_name
        name = _file_name(argument, suffixes)
        return f"{name}（{language}）" if name else process_name

    return process_name


def resolve_process_display_name(snapshot):
    """所有者と開始時刻が一致するプロセスだけから、表示用の名前を取得する。

    Args:
        snapshot: 検証済みのPID・UID・開始時刻・プロセス名を含む待受情報。

    Returns:
        起動対象の表示名。権限不足・プロセス交代・判別不能時は元のプロセス名。
    """
    fallback = snapshot["display_name"]
    try:
        process = psutil.Process(snapshot["pid"])
        owner = process.uids()
        if (
            owner.real != snapshot["uid"]
            or owner.effective != snapshot["uid"]
            or process.create_time() != snapshot["started_at"]
        ):
            return fallback

        arguments = process.cmdline()
        if not process.is_running():
            return fallback
        return command_display_name(fallback, arguments)
    except (psutil.Error, OSError):
        return fallback
