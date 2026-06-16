# cc-radar — Claude Code 最新情報レーダー

Claude Code の最新情報を毎朝自動収集し、**Gmail通知**＋**GitHub Pages静的サイト**で一覧にするツール。
標準ライブラリのみで動作（pip不要）。

## 構成

| ファイル | 役割 |
|---|---|
| `collect.py` | 本体（収集・分類・AI要約・スコア・HTML/JSON生成・メール送信） |
| `config.json` | 収集源・期間・興味プロフィール・AI・メール設定 |
| `setenv.example.bat` | 認証情報テンプレ → `setenv.bat` にコピーして使う（gitignore保護） |
| `run.bat` | 毎日実行バッチ |
| `register-schedule.ps1` | 毎朝7:30の自動実行をタスクスケジューラに登録 |
| `docs/index.html` | 公開サイト（自動生成・認証なし） |
| `docs/data.json` | サイトが読むデータ（自動生成） |
| `data/history.json` | 収集履歴（自動生成） |

## クイックスタート（2分）

```bat
rem 1) 動作確認（出力なし・収集結果だけ表示）
python collect.py --dry-run

rem 2) サイトだけ生成（メール送信なし）
python collect.py --no-mail

rem 3) docs/index.html をローカルで見る（fetchするので簡易サーバ経由で）
python -m http.server -d docs 8000
rem  → ブラウザで http://localhost:8000
```

## 認証情報（任意）

`setenv.example.bat` を `setenv.bat` にコピーし、値を書き換える。

- **AI翻訳・要約**：`ANTHROPIC_API_KEY`（無くても動く。英語記事はタイトルで判断）
- **Gmail通知**：`CC_RADAR_GMAIL_USER` / `CC_RADAR_GMAIL_PASS`（Googleの2段階認証→アプリパスワード）
- **社内ネット**：`CC_RADAR_INSECURE_SSL=1`（証明書がプロキシ差し替えされる環境のみ。自宅は不要）

`run.bat` が自動で `setenv.bat` を読み込む。

## GitHub Pages公開

1. このフォルダをGitHubリポジトリにpush
2. リポジトリ Settings → Pages → Source を `main` ブランチ・`/docs` フォルダに指定
3. 発行されたURL（`https://ユーザー名.github.io/リポジトリ名/`）を `config.json` の `public_url` に設定
4. 既読はブラウザのlocalStorageに端末ごと保存（自宅⇄会社では同期しない＝認証なし静的サイトのため）

## 毎朝の自動実行

```powershell
powershell -ExecutionPolicy Bypass -File .\register-schedule.ps1            # 毎朝7:30
powershell -ExecutionPolicy Bypass -File .\register-schedule.ps1 -Time 08:00 # 時刻変更
powershell -ExecutionPolicy Bypass -File .\register-schedule.ps1 -Unregister # 解除
```

## 起動引数

| 引数 | 動作 |
|---|---|
| （なし） | 全実行（収集→HTML/JSON生成→メール送信） |
| `--no-mail` | メール送信なし（HTML/JSONのみ） |
| `--dry-run` | 収集して表示するだけ（ファイル出力なし） |

## 設計メモ（再現の肝）

- **UA偽装必須**：独自UAだと全ソース403。Chrome UAを使う（`config.json`）
- **XML真偽判定**：`el is not None` で判定（空文字を真偽値で見ると（無題）化）
- **日付ソート**：published有無を第1キー、日付を第2キーで降順（日付不明は末尾）
- **既読同期**：おすすめ枠と全部枠に同一URLが両方→`data-url`で全カード同期
- **1ソース失敗で止めない**：X/RSSHub等の不安定ソースは自動スキップ
