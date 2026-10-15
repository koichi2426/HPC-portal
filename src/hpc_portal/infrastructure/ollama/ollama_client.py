"""hpc-ollamaコマンドへの接続と応答の解釈。"""

import json
import re

_HPC_OLLAMA_MODEL_RE = re.compile(r"^[A-Za-z0-9_.:/-]{1,128}$")


class OllamaClient:
    def __init__(self, commands, policy, settings):
        """Ollamaの起動・停止とモデル操作に使う依存を保持する。

        Args:
            commands: OSコマンドを実行する接続先。
            policy: 共有Ollamaのリソース指定を検証・正規化するルール。
            settings: 共有OllamaのCPU・メモリ・GPUの既定値と上限。
        """
        self.commands = commands
        self.policy = policy
        self.settings = settings

    def gpu_count(self) -> int:
        """共有OllamaがGRESで予約するGPU数を返す。

        Returns:
            予約するGPU数。0ならGRES予約なし。
        """
        try:
            return max(0, int(self.settings.HPC_OLLAMA_GPUS))
        except (TypeError, ValueError):
            return 0

    def gpu_label(self) -> str:
        """共有OllamaのGPU割り当てを画面表示用の文言にする。

        Returns:
            GRES予約している場合は枚数、予約なしでGPUを共有する場合はその旨。
        """
        count = self.gpu_count()
        return f"{count} GPU" if count > 0 else "GPU 共有（予約なし）"

    def command(
        self,
        action: str,
        model: str | None = None,
        cpus: str | None = None,
        memory: str | None = None,
        parallel: str | None = None,
        max_loaded_models: str | None = None,
        context_length: str | None = None,
        kv_cache_type: str | None = None,
        keep_alive: str | None = None,
        max_queue: str | None = None,
        flash_attention: bool | None = None,
    ) -> tuple[dict | None, str | None]:
        """hpc-ollama管理コマンドを実行する。

        Args:
            action: start、stop、status、pull、deleteなどの操作名。
            model: pullまたはdelete対象のモデル名。
            cpus: start時のCPU数。
            memory: start時の要求メモリ。
            parallel: start時の同時処理数。
            max_loaded_models: start時の同時ロードモデル数。
            context_length: start時のコンテキスト長。
            kv_cache_type: start時のKVキャッシュ形式。
            keep_alive: start時のモデル保持時間。
            max_queue: start時の最大待機数。
            flash_attention: start時のFlash Attention設定。

        Returns:
            ``(JSON結果, エラー)``。
        """
        cmd = ["/usr/local/sbin/hpc-ollama", action]
        start_settings = None
        if action == "start":
            start_settings, err = self.policy.validate_ollama_start_settings(
                cpus,
                memory,
                parallel,
                max_loaded_models,
                context_length,
                kv_cache_type,
                keep_alive,
                max_queue,
                flash_attention,
            )
            if err:
                return None, err
            option_names = {
                "cpus": "--cpus",
                "memory": "--memory",
                "parallel": "--parallel",
                "max_loaded_models": "--max-loaded-models",
                "context_length": "--context-length",
                "kv_cache_type": "--kv-cache-type",
                "keep_alive": "--keep-alive",
                "max_queue": "--max-queue",
                "flash_attention": "--flash-attention",
            }
            for key, option in option_names.items():
                cmd.extend([option, start_settings[key]])
        if model:
            if not _HPC_OLLAMA_MODEL_RE.fullmatch(model):
                return None, "モデル名に使用できない文字が含まれています"
            cmd.append(model)
        result = self.commands.run(cmd)
        body = (result.stdout or "").strip()
        if result.returncode != 0:
            return None, (result.stderr or result.stdout or "hpc-ollama failed").strip()
        if action == "status" and body:
            try:
                return json.loads(body), None
            except json.JSONDecodeError:
                return {"raw": body}, None
        if action in {"tags", "show", "ps", "pull-status", "pull-cancel"} and body:
            try:
                return json.loads(body), None
            except json.JSONDecodeError:
                return {"raw": body}, None
        if action in {"start", "update", "update-check"} and body:
            try:
                data = json.loads(body)
                if action == "start":
                    for key, value in (start_settings or {}).items():
                        data.setdefault(key, value)
                    data.setdefault("gpus", self.settings.HPC_OLLAMA_GPUS)
                return data, None
            except json.JSONDecodeError:
                return {
                    "ok": True,
                    "output": body,
                    **(start_settings or {}),
                    "gpus": self.settings.HPC_OLLAMA_GPUS,
                }, None
        return {"ok": True, "output": body}, None

    def pull_progress(self, model: str | None = None) -> tuple[dict | None, str | None]:
        """バックグラウンドpullの最終進捗を表示用データに変換する。

        Args:
            model: 状態を確認するモデル名。省略時は実行中モデルを使う。

        Returns:
            ``(進捗情報, エラー)``。
        """
        data, err = self.command("pull-status", model)
        if err or data is None:
            return None, err or "pull 状態を取得できません"
        active = bool(data.get("active"))
        active_model = str(data.get("active_model") or "")
        selected_model = str(data.get("model") or model or active_model or "")
        last = str(data.get("last") or "")
        record = {}
        if last:
            try:
                parsed = json.loads(last)
                if isinstance(parsed, dict):
                    record = parsed
            except json.JSONDecodeError:
                record = {"error": last[:300]}
        total = record.get("total")
        completed = record.get("completed")
        try:
            total = max(0, int(total)) if total is not None else None
        except (TypeError, ValueError):
            total = None
        try:
            completed = max(0, int(completed)) if completed is not None else None
        except (TypeError, ValueError):
            completed = None
        state = "idle"
        if active:
            state = "pulling" if not model or active_model == model else "busy"
        result = str(data.get("result") or "")
        if not active and result == "cancelled":
            state = "cancelled"
        elif not active and result == "cancelled_cleanup_failed":
            state = "cancelled_cleanup_failed"
        elif result not in ("", "0"):
            state = "failed"
        elif str(record.get("status") or "").lower() == "success" or result == "0":
            state = "completed"
        return {
            "state": state,
            "model": selected_model,
            "active_model": active_model,
            "status": str(record.get("status") or ""),
            "completed": completed,
            "total": total,
            "error": str(record.get("error") or "")[:300],
        }, None

    def has_model(self, model: str) -> tuple[bool, str | None]:
        """指定モデルがOllamaに存在するか確認する。

        Args:
            model: 確認するOllamaモデル名。

        Returns:
            ``(存在するか, エラー)``。
        """
        if not _HPC_OLLAMA_MODEL_RE.fullmatch(str(model or "")):
            return False, "モデル名に使用できない文字が含まれています"
        tags, err = self.command("tags")
        if err:
            return False, err
        for item in (tags or {}).get("models", []) or []:
            if isinstance(item, dict) and str(item.get("name") or "") == model:
                return True, None
        return False, f"Ollamaにモデル {model} がありません"

    def model_names(self) -> tuple[list[str], str | None]:
        """Ollamaへインストール済みのモデル名を取得する。

        Returns:
            ``(重複を除いて並べ替えたモデル名, エラー)``。
        """
        tags, err = self.command("tags")
        if err:
            return [], err
        names = {
            str(item.get("name") or "").strip()
            for item in (tags or {}).get("models", []) or []
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        }
        return sorted(names), None

    def model_supports_tools(self, model: str) -> tuple[bool | None, str | None]:
        """Ollamaモデルがツール呼び出しに対応するか確認する。

        Args:
            model: 確認するOllamaモデル名。

        Returns:
            ``(tools capabilityの有無, エラー)``。取得失敗時の値はNone。
        """
        data, err = self.command("show", model)
        if err:
            return None, err
        capabilities = (data or {}).get("capabilities", [])
        if not isinstance(capabilities, list):
            capabilities = []
        return "tools" in {str(item).strip().lower() for item in capabilities}, None
