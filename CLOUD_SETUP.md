# cc-radar クラウド化手順（GitHub Actions）

PCの電源状態に依存せず、毎朝7:30に**GitHubのクラウドで自動実行**する構成。
ワークフローは `.github/workflows/cc-radar.yml`（作成済み）。

## これで何が変わるか
- ノートPCが**電源オフ／スリープ／バッテリーでも関係なく**毎朝動く
- 結果は **GitHub Pages（一覧サイト）** と **メール** で受け取れる
- ローカルのタスクスケジューラは不要になる（後述の手順で解除）

## あなたの操作が必要な手順（私が代行できない認証部分）

### 1. リポジトリを作って push する
- GitHubアカウントが必要（無ければ作成）
- このフォルダ（cc-radar）を**リポジトリのルート**として push
- 例（GitHubで空リポジトリ `cc-radar` を作成後、このフォルダで）:
  ```
  git branch -M main
  git remote add origin https://github.com/<ユーザー名>/cc-radar.git
  git push -u origin main
  ```
- ※ 会社ネット等でpushがSSLエラーになる場合は、githubに繋がる回線（自宅等）か、GitHub Desktop／Webアップロードを使う

### 2. 公開/非公開を選ぶ（推奨：Public）
| | Public | Private |
|---|---|---|
| Actions無料枠 | **無制限** | 月2000分（本件は月~60分で十分） |
| Pages | そのまま公開URL | サイトは結局公開URL（中身に秘密はなし） |
| 中身の露出 | 収集したニュース一覧と興味キーワードのみ（**個人情報・認証情報は一切含まない**） | 同左 |

→ 秘密情報はすべて下記Secretsに入り**リポジトリには載らない**ため、**Publicが最もシンプル**でおすすめ。

### 3. Secrets（認証情報）を登録
リポジトリ → Settings → Secrets and variables → Actions → New repository secret で、使うものだけ登録：
- `CC_RADAR_GMAIL_USER` … 送信元Gmailアドレス
- `CC_RADAR_GMAIL_PASS` … Gmailの**アプリパスワード**（2段階認証→アプリパスワード発行）
- `ANTHROPIC_API_KEY` … AI翻訳・要約を使う場合のみ

※ 1つも登録しなければ、その機能は自動スキップ（サイト生成だけ動く）。

### 4. Pagesを有効化（メールのみ運用ならスキップ）
リポジトリ → Settings → Pages → Build and deployment → Source = **GitHub Actions**

### 5. 公開URLを設定
`config.json` の `public_url` を、発行された `https://<ユーザー名>.github.io/cc-radar/` に書き換えて commit/push。
（メール内「すべての記事を見る」ボタンのリンク先になる）

### 6. 動作確認
リポジトリ → Actions → 「cc-radar daily」→ **Run workflow**（手動実行）で即テスト。
緑チェックになり、Pages URLにサイトが出れば成功。

## 運用メモ（注意点）
- **時刻**：cronは `40 21 * * *`（=06:40 JST）。狙いは「7:00前後に受信箱へ着地」。GitHubの混雑で**数分～十数分ずれることあり**のため、7:00ちょうどではなく少し前倒しで発火させている（正時:00・30分:30の混雑帯は回避）。
- **60日ルール**：スケジュール実行は、リポジトリが60日間“人による更新”ゼロだと自動停止する仕様。たまに手動Runするか、月1回でも何かcommitすれば回避。
- **メールのみで運用したい**：`.github/workflows/cc-radar.yml` の「Pages成果物をアップロード」「GitHub Pagesへデプロイ」2ステップと、先頭の `permissions:` / `environment:` を削除。リポジトリはPrivateでOK。
- **ローカルのタスクは解除**（二重実行・二重メール防止）：
  ```
  powershell -ExecutionPolicy Bypass -File .\register-schedule.ps1 -Unregister
  ```
  ※ クラウドが安定稼働するまでは残しておいても可（その場合ノートPCはAC接続時のみ動く）。
