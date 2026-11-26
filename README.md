# 離島工学データベース

離島の地域課題、研究・実践、技術、人物・組織、記事を関連付けて管理する知識基盤です。FastAPIとRDF（Turtle形式）を使い、ブラウザから情報の閲覧・編集・検索ができます。AIによる探索はローカルのOllamaに接続します。

## 起動

Python 3.10以上を用意し、リポジトリのルートで実行します。

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

起動するたびに、リポジトリのルートで実行します。

```sh
source .venv/bin/activate
python app.py
```

サーバーを停止するには、起動したターミナルで Ctrl+C を押します。

管理者キーを `.env` に設定している場合は、起動前に `RITOU_ADMIN_TOKEN` を環境変数として読み込みます。`.env` がシェルで読み込める形式であれば、`set -a`、`source .env`、`set +a` の順に実行します。

ブラウザで <http://127.0.0.1:8000> を開きます。APIドキュメントは <http://127.0.0.1:8000/docs> です。初回のデータ読み込み時に、ローカル保存用ディレクトリと初期データが生成されます。既存の研究データはリポジトリに含まれません。

AI機能を使う場合は、別途Ollamaをインストールし、起動した状態で生成モデルを取得します。

```sh
ollama pull qwen3.5:4b
```

Ollamaの接続先は `http://127.0.0.1:11434`（環境変数 `OLLAMA_BASE_URL` で変更可能）、既定モデルは `qwen3.5:4b` です（`llm_agent.py`）。AI以外の閲覧・編集機能はOllamaなしで利用できます。

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
| `docs/` | 設計・機能の説明 |大丈夫です
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

## 組織・所属関係の管理

組織・研究室の新規登録、基本情報の編集・削除、所属先・下位組織・所属メンバーの変更は管理者に限定しています。組織編集フォームでは登録済みの人物・組織を選択します。研究・活動への参加、記事の著者や関連先としての参照、通常のプロフィール編集、概念の登録は引き続き利用できます。詳細編集のTurtle一括上書きには管理者認証が必要です。

管理者キーは、固定の研究者ID `石橋治樹` に対応させています。運用担当者が `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'` でキーを生成し、サーバー起動時の環境変数 `RITOU_ADMIN_TOKEN` に設定して、石橋治樹本人にだけ共有してください。systemdでは、`LoadCredential=ritou-admin-token:/保護されたキーのパス` でも設定できます。どちらも未設定の場合、管理者の操作は無効です。キーはソースやブラウザの永続ストレージに保存しません。管理画面では「組織・研究室」から「管理者として認証」を選びます。ブラウザの再読み込みでも管理者モードは終了します。

認証できる接続は、サーバー端末上のループバックHTTP（Pythonで直接起動する場合の `http://127.0.0.1:8000/`）かHTTPSです。LAN内の別端末やプロキシ経由で管理する場合はHTTPSを構成し、HTTPS終端と転送ヘッダーを信頼できるプロキシだけに限定してください。

このキーを持つ人は管理者として操作できます。個人ごとのログインや所有権の検証は未実装で、一般投稿の著者・研究者の選択は本人確認を伴いません。また、Turtleファイルを共有するため、サーバーは単一ワーカー・単一インスタンスで運用してください。
