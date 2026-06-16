@echo off
rem ===========================================================================
rem  cc-radar  認証情報テンプレート
rem  使い方:  このファイルを setenv.bat にコピーして、値を自分のものに書き換える。
rem           setenv.bat は .gitignore で除外されるので公開リポジトリに載らない。
rem ===========================================================================

rem --- AI翻訳・要約（任意。無くても動く＝英語記事はタイトルで判断） ---
set ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

rem --- Gmail通知（任意。無ければメール送信スキップ） ---
rem  ※ Googleアカウントで2段階認証を有効化 → 「アプリパスワード」を発行して貼る
set CC_RADAR_GMAIL_USER=your_name@gmail.com
set CC_RADAR_GMAIL_PASS=xxxxxxxxxxxxxxxx

rem --- 社内ネット等で証明書がプロキシ差し替えされる環境のみ 1 にする（自宅は不要） ---
rem set CC_RADAR_INSECURE_SSL=1
