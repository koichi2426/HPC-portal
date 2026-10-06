"""API公開に必要な外部処理の契約。"""

from typing import Protocol


class RecordStore(Protocol):
    def get(self, kind: str, name: str) -> dict | None:
        """暗号化レコードを読み込み、保存キーとの一致を確認して復号する。

        Args:
            kind: credentials・publications・guardsなどのレコード種別。
            name: 種類ごとに一意なレコードの保存キー。

        Returns:
            復号したレコード。未登録ならNone。

        Raises:
            ValueError: 復号した内容とレコードの種類・保存キーが一致しない場合。
        """
        ...

    def put(self, kind: str, name: str, record: dict) -> None:
        """種類と名前も暗号文へ含め、レコードを保存または置き換える。

        Args:
            kind: credentials・publications・guardsなどのレコード種別。
            name: 種類ごとに一意なレコードの保存キー。
            record: 暗号化して保存するJSON互換のレコード。
        """
        ...

    def delete(self, kind: str, name: str) -> None:
        """指定した種類・名前のレコードを削除する。

        Args:
            kind: credentials・publications・guardsなどのレコード種別。
            name: 種類ごとに一意なレコードの保存キー。
        """
        ...

    def names(self, kind: str) -> list[str]:
        """指定した種類の保存キーを名前順で取得する。

        Args:
            kind: credentials・publications・guardsなどのレコード種別。

        Returns:
            保存されているレコード名の一覧。
        """
        ...


class CloudflareAccess(Protocol):
    async def issue(self, name: str) -> dict:
        """管理名に対応するService Tokenを発行し、未確認の発行は再発行で回復する。

        Args:
            name: ポータルが管理対象を照合する一意の管理名。

        Returns:
            ID・Client ID・Client Secretを含む発行結果。

        Raises:
            CloudflareError: 管理APIへの接続・更新に失敗した場合、または管理対象を一意に確認できない場合。
        """
        ...

    async def rotate(self, token_id: str) -> dict:
        """既存Service Tokenの秘密値を再発行する。

        Args:
            token_id: 更新・失効させるトークンの識別子。

        Returns:
            新しいClient Secretを含む再発行結果。

        Raises:
            CloudflareError: 管理APIへの接続・更新に失敗した場合、または管理対象を一意に確認できない場合。
        """
        ...

    async def remove(self, token_id: str) -> None:
        """Service Tokenを削除し、既に存在しない場合も完了として扱う。

        Args:
            token_id: 更新・失効させるトークンの識別子。

        Raises:
            CloudflareError: 管理APIへの接続・更新に失敗した場合、または管理対象を一意に確認できない場合。
        """
        ...

    async def app(
        self, name: str, domains: list, token_ids: list, existing_id=None
    ) -> dict:
        """本人のService Tokenだけを許可するAccessアプリを作成・更新する。

        Args:
            name: ポータルが管理対象を照合する一意の管理名。
            domains: 公開するホスト名とパスの一覧。
            token_ids: 公開先への接続を許可するService TokenのID一覧。
            existing_id: 更新・削除対象の既存AccessアプリID。未指定時は管理名で検索する。

        Returns:
            作成・更新したAccessアプリのidとaud。

        Raises:
            CloudflareError: 管理APIへの接続・更新に失敗した場合、または管理対象を一意に確認できない場合。
        """
        ...

    async def remove_app(self, name: str, app_id=None) -> None:
        """保存IDまたは管理名でAccessアプリを照合して削除する。

        Args:
            name: ポータルが管理対象を照合する一意の管理名。
            app_id: 削除対象の既存AccessアプリID。

        Raises:
            CloudflareError: 管理APIへの接続・更新に失敗した場合、または管理対象を一意に確認できない場合。
        """
        ...


class HubTokens(Protocol):
    async def user(self, username: str):
        """Linuxユーザー名に対応するHubユーザーを取得し、未登録なら準備する。

        Args:
            username: 対象のLinuxユーザー名。

        Returns:
            既定ロールを付与したHubユーザー。
        """
        ...

    def issue(self, user) -> dict:
        """本人の登録APIだけを呼び出せる専用Hubトークンを発行する。

        Args:
            user: 操作対象のJupyterHubユーザー。

        Returns:
            平文のhub_tokenと失効用のhub_token_id。
        """
        ...

    def revoke(self, token_id) -> None:
        """指定したHubトークンを削除し、存在しない場合は何もしない。

        Args:
            token_id: 更新・失効させるトークンの識別子。
        """
        ...

    def valid(self, record: dict, user) -> bool:
        """保存済みHubトークンのIDと所有者が現在も一致するか確認する。

        Args:
            record: 保存済みのHubトークンIDと所有者情報を含む認証情報レコード。
            user: 操作対象のJupyterHubユーザー。

        Returns:
            本人の有効なトークンとして確認できた場合はTrue。
        """
        ...

    def revoke_orphans(self, username: str, keep_id=None) -> None:
        """ユーザーに残る外部API用Hubトークンを、指定したID以外すべて削除する。

        Args:
            username: 対象のLinuxユーザー名。
            keep_id: 失効させずに残すトークンID。
        """
        ...


class ListenerInventory(Protocol):
    def choose(self, uid: int, candidate: str = "", port=None) -> dict:
        """候補IDまたはポートで待受を絞り、競合する所有者がいないか確認する。

        Args:
            uid: 照合するLinuxユーザーのUID。
            candidate: 一覧で検出した待受プロセスの識別子。
            port: 対象の待受ポート番号。

        Returns:
            一意に確認できた本人の待受候補。

        Raises:
            ValueError: 候補を一意に特定できない、終了している、またはポートの所有者が不明・競合する場合。
        """
        ...

    def validate(self, target: dict):
        """登録時のプロセスとソケットが、現在も同じ所有者で待ち受けているか確認する。

        Args:
            target: 登録時の所有者・プロセス・ソケットを含む接続先情報。

        Returns:
            同じプロセスとソケットであることを確認した接続先情報。

        Raises:
            ValueError: プロセスが終了・交代した、または登録時のソケットを確認できない場合。
        """
        ...

    def ports(self, uid: int) -> dict:
        """使用中・予約済みポートを除き、bind可能な候補を返す。予約は行わない。

        Args:
            uid: 照合するLinuxユーザーのUID。

        Returns:
            確認時刻・候補範囲・予約ポート・空き候補・本人の待受一覧。

        Raises:
            ValueError: 非特権ポートの開始番号を取得できない場合。
        """
        ...


class PortGuard(Protocol):
    async def protect(self, target: dict) -> None:
        """接続先の同一性と既存所有者を確認し、ポートの直接接続を制限する。

        Args:
            target: 登録時の所有者・プロセス・ソケットを含む接続先情報。

        Raises:
            ValueError: 接続先や保護設定の所有者が不一致、またはルールを反映できない場合。
        """
        ...

    async def check(self, target: dict) -> None:
        """指定候補の保護設定が保存され、nftablesへ反映できるか確認する。

        Args:
            target: 登録時の所有者・プロセス・ソケットを含む接続先情報。

        Raises:
            ValueError: 接続先の保護記録がない、またはルールを確認・反映できない場合。
        """
        ...

    async def reconcile(self) -> None:
        """終了した待受の保護記録を整理し、現在の対象をnftablesへ反映する。"""
        ...


class AccessVerifier(Protocol):
    async def verify(self, assertion: str, app: dict, credentials: dict):
        """JWTの署名・期限・公開先・Service Tokenの所有者を照合する。

        Args:
            assertion: Cloudflare Accessが発行したJWT。
            app: 検証対象のAccessアプリのaudを含むAPI公開レコード。
            credentials: 本人のService TokenのClient IDを含む認証情報レコード。

        Raises:
            ValueError: JWTの署名・期限・発行元・aud・トークン所有者を確認できない場合。
        """
        ...


class HttpRelay(Protocol):
    def request(
        self, inventory, target, method, path, headers=None, body=None, *, timeout
    ):
        """接続先の同一性を検証しながらHTTPリクエストを転送する。

        Args:
            inventory: 公開対象の待受プロセスが登録時と同一か確認する接続先。
            target: 登録時の所有者・プロセス・ソケットを含む接続先情報。
            method: 使用するHTTPメソッド。
            path: 公開先APIへ転送する、/から始まる相対パス。
            headers: 転送元または転送先のHTTPヘッダー。
            body: 転送するリクエスト本文。
            timeout: 処理完了を待つ上限時間。単位は秒。

        Returns:
            接続の開始・終了を管理し、HTTP応答を提供する非同期コンテキストマネージャー。
        """
        ...


class ApiHealth(Protocol):
    async def check(self, record: dict) -> None:
        """ポート保護と接続先を確認し、指定した確認パスの応答を検証する。

        Args:
            record: 接続先と動作確認パスを含むAPI公開の保存レコード。

        Raises:
            ValueError: 保護や接続先を確認できない場合、または確認パスが成功応答を返さない場合。
        """
        ...


class ApiConfiguration(Protocol):
    public_host: str
    max_apps: int
    body_limit: int
    timeout: int
    concurrency: int
