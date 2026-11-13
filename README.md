# 離島工学データベース

離島の地域課題、研究・実践、技術、人物・組織、記事を関連付けて管理する知識基盤です。FastAPIとRDF（Turtle形式）を使い、ブラウザから情報の閲覧・編集・検索ができます。AIによる探索はローカルのOllamaに接続します。

## 起動

### Dockerで起動（Mac間で同じ手順を使う場合）

[Docker Desktop for Mac](https://docs.docker.com/desktop/setup/install/mac-install/)をインストールして起動します。Mac側にPythonやPythonライブラリをインストールする必要はありません。Apple Silicon / Intelの各Macで、そのMac向けのイメージをビルドします。

```sh
git clone https://github.com/Rucky14brdg/R8-Remote_Island_Engineering.git
cd R8-Remote_Island_Engineering
docker compose up -d --build
```

ブラウザで <http://127.0.0.1:8000> を開きます。APIドキュメントは <http://127.0.0.1:8000/docs> です。初回ビルドにはインターネット接続が必要です。以降の起動は `docker compose up -d`、コードや依存ライブラリの更新後は `docker compose up -d --build` を実行します。

```sh
docker compose logs -f app  # ログを確認（Ctrl+Cでログ表示を終了）
docker compose ps           # 起動・ヘルスチェックの状態を確認
docker compose down         # 停止（data/の内容は残ります）
```

8000番ポートが使用中なら `APP_PORT=8001 docker compose up -d --build` で変更できます。変更したポートは以降の起動でも同じ値を指定してください。

`data/` はMac側のフォルダをコンテナに接続するため、記事・画像などはコンテナを作り直しても残ります。既存の `data/` があればそのまま使います。実データや `.env`、認証情報はビルド対象から除外しています。別のMacへの移行は、両方のアプリを停止して `data/` をコピーし、移行先で起動してください。`data/schema.json` も必要なので、コピー先で削除しないでください。

#### AIもDockerで起動する場合

Ollamaのインストールも省く場合は、追加のComposeファイルを使います。

```sh
docker compose -f compose.yaml -f compose.ollama.yaml up -d --build
docker compose -f compose.yaml -f compose.ollama.yaml exec ollama ollama pull qwen3.5:4b
```

モデルの取得が完了するとAI機能を利用できます。Ollamaが起動途中で接続できない場合は、少し待ってモデル取得を再実行してください。モデルはDockerの名前付きボリューム `ollama-models` に保存され、再起動時の取得は不要です。別のMacではモデルを再取得してください。

この構成の起動・停止・ログ確認では、毎回 `docker compose -f compose.yaml -f compose.ollama.yaml` に続けて `up -d` / `down` / `logs -f` を指定します。`down -v` はモデルの保存ボリュームも削除するため、モデルを消したい場合だけ使います。

MacでのOllamaコンテナはCPUで推論します（[Ollama公式FAQ](https://github.com/ollama/ollama/blob/main/docs/faq.mdx)）。Apple SiliconのGPUを使いたい場合はMac版Ollamaを利用してください。

#### Mac版Ollamaを利用する場合

Mac版Ollamaを起動し、Macのターミナルで `ollama pull qwen3.5:4b` を実行してから、通常の `docker compose up -d --build` で起動します。AIのコンテナ構成から切り替える場合は、先に追加ファイル付きの `down` で停止してください。

コンテナからMacへの接続にはDocker Desktopの [`host.docker.internal`](https://docs.docker.com/desktop/features/networking/networking-how-tos/) を使用します。別のOllamaサーバーに接続する場合は `OLLAMA_BASE_URL=http://接続先:11434 docker compose up -d` で変更できます。

### Pythonで直接起動

Python 3.10以上を用意し、リポジトリのルートで実行します。

```sh
git clone https://github.com/Rucky14brdg/R8-Remote_Island_Engineering.git
cd R8-Remote_Island_Engineering
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

ブラウザで <http://127.0.0.1:8000> を開きます。APIドキュメントは <http://127.0.0.1:8000/docs> です。初回のデータ読み込み時に、ローカル保存用ディレクトリと初期データが生成されます。既存の研究データはリポジトリに含まれません。

AI機能を使う場合は、別途Ollamaをインストールし、起動した状態で生成モデルを取得します。

```sh
ollama pull qwen3.5:4b
```

Pythonで直接起動した場合の接続先は `http://127.0.0.1:11434`（環境変数 `OLLAMA_BASE_URL` で変更可能）、既定モデルは `qwen3.5:4b` です（`llm_agent.py`）。AI以外の閲覧・編集機能はOllamaなしで利用できます。

## 構成

| 場所 | 内容 |
| --- | --- |
| `app.py` | FastAPIの画面・API |
| `rdf_manager.py` | RDFの保存・取得とスキーマ検証 |
| `articles.py` / `activity_records.py` | 記事編集・活動記録 |
| `portfolio.py` / `issues.py` | ポートフォリオ・地域課題 |
| `knowledge_*.py` / `sparql_search.py` / `llm_agent.py` | 検索・AI探索 |
| `templates/` / `static/` | 画面、JavaScript、CSS、同梱ライブラリ |
| `data/schema.json` | 共有するデータ設計 |
| `docs/` | 設計・機能の説明 |
| `tests/` | PythonとJavaScriptのテスト |

## データの保管

Gitではコードとスキーマを管理します。以下は `.gitignore` で除外しているため、GitHubに送信されません。

- 実データ：`data/knowledge.ttl`、`data/wiki/`、`data/articles/`
- 画像・履歴・バックアップ：`data/media/`、`data/article_history/`、`data/backups/`
- 実データを含む評価結果：`docs/evaluations/*.json`
- 仮想環境、一時ファイル、環境設定・認証情報

既存環境を別のPCへ引き継ぐ場合は、アプリを停止して `data/` を別途コピーしてください。GitHubはこれらのデータのバックアップにはならないため、`data/` は別途バックアップしてください。

## テスト

仮想環境を有効化して、ルートから実行します。JavaScriptのテストにはNode.jsが必要です。

```sh
python -m unittest discover -s tests -v
node tests/markdown.cjs
node tests/frontend_public_home.cjs
node tests/organization_affiliations.cjs
node tests/issue_map_selection.cjs
node tests/island_ai.cjs
```

## 変更をGitHubへ保存

このフォルダで編集したあと、次を実行します。コミット前に差分と追加ファイルを確認してください。

```sh
git status
git diff
git add .
git diff --cached --stat
git commit -m "変更内容を書く"
git push
```

別のPCで更新した内容を取り込む際は、手元の変更をコミットしてから `git pull --ff-only` を実行します。

## 同梱ライブラリ

`static/vendor/` のmarkdown-itとLeafletには、それぞれのLICENSEファイルを同梱しています。
