"""Azure OpenAI 接続設定

環境変数から読み込む。

- 本番（Cloud Run）: Secret Manager のシークレットを `gcloud run deploy --set-secrets`
  で環境変数としてマウントする（README「Cloud Run へのデプロイ」参照）。
- ローカル開発: プロジェクトルートの .env（gitignore / dockerignore 対象）でも設定できる。
  既に環境変数がある場合はそちらが優先される。

.env の例:
    AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com/
    AZURE_OPENAI_API_KEY=xxxxxxxx
    AZURE_OPENAI_DEPLOYMENT=gpt-4o
    AZURE_OPENAI_API_VERSION=2024-10-21

4 変数のうち ENDPOINT / API_KEY / DEPLOYMENT が揃っていなければ未設定と判定し、
クライアントはモックモードで動作する。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_ENV_PATH = Path(__file__).parent.parent / ".env"

_DEFAULT_API_VERSION = "2024-10-21"


def _load_dotenv(path: Path = _ENV_PATH) -> None:
    """シンプルな .env ローダー（KEY=VALUE 形式のみ、既存の環境変数を優先）。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class AzureOpenAIConfig:
    endpoint: str
    api_key: str
    deployment: str
    api_version: str

    @property
    def is_configured(self) -> bool:
        return bool(self.endpoint and self.api_key and self.deployment)


def _env(key: str, default: str = "") -> str:
    # Secret Manager に `echo ... | gcloud secrets create` で登録すると末尾に改行が
    # 入りやすく、そのままだとエンドポイント URL / API キーが不正になるため除去する。
    return os.environ.get(key, default).strip() or default


def load_config() -> AzureOpenAIConfig:
    _load_dotenv()
    return AzureOpenAIConfig(
        endpoint=_env("AZURE_OPENAI_ENDPOINT"),
        api_key=_env("AZURE_OPENAI_API_KEY"),
        deployment=_env("AZURE_OPENAI_DEPLOYMENT"),
        api_version=_env("AZURE_OPENAI_API_VERSION", _DEFAULT_API_VERSION),
    )
