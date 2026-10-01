# demo-video-skill

ソフトウェアの動きを見せる短いデモ動画（MP4・GIF）を作る、Claude Code用のプラグインです。

デモの対象に合わせて、Claudeが3つの作り方から1つを選びます。

| 対象 | 作り方 |
|---|---|
| ブラウザで動くUI | Playwrightで操作して録画する。ブラウザはインストール済みのChromeを使う |
| CLI・ターミナル・TUI | VHSの`.tape`で録画する |
| UIのないライブラリや仕組みの説明 | HTML/CSSのアニメーションを録画する |

テロップ、タイトル、強調枠、吹き出し、クリックエフェクト、ズームは、録画した後にPillowで動画へ焼き込みます。注釈の位置と下地の色は、実際のフレームを解析して決めるため、画面の文字や強調する対象には重なりません。日本語は、OSに標準で入っているフォントで描画します。

## サンプル

このスキルで作った動画です。どれもClaude Codeに一言頼んだだけで、録画からテロップ、ズーム、仕上がりの確認までをClaudeが行いました。

<!--
  動画の差し込み方
  1. GitHubでこのファイルの編集画面を開き、MP4を本文のどこかにドラッグ＆ドロップする
  2. 挿入された https://github.com/user-attachments/assets/... のURLを切り取り、
     下のvideoタグのsrcと置き換える
-->

<table>
  <tr>
    <th width="33%">Webアプリの操作録画</th>
    <th width="33%">CLIの録画</th>
    <th width="33%">仕組みの説明（モーション動画）</th>
  </tr>
  <tr>
    <td><video src=https://github.com/user-attachments/assets/0772ca97-b38a-4843-af45-11208c868195 controls muted playsinline width="100%"></video></td>
    <td><video src=https://github.com/user-attachments/assets/b990cb11-9df8-4a5b-aef9-8be9041fcb47 controls muted playsinline width="100%"></video></td>
    <td><video src=https://github.com/user-attachments/assets/27444de0-a19c-47fe-979f-e122a188ae7b controls muted playsinline width="100%"></video></td>
  </tr>
</table>

## インストール

Claude Codeでこのリポジトリをマーケットプレイスとして追加し、プラグインをインストールします。

```text
/plugin marketplace add hnegishi/demo-video-skill
/plugin install demo-video@demo-video-skill
```

ターミナルからインストールする場合は、次のコマンドを実行してください。

```bash
claude plugin marketplace add https://github.com/hnegishi/demo-video-skill
claude plugin install demo-video@demo-video-skill
```

更新と削除には、次のコマンドを使います。

```bash
claude plugin marketplace update demo-video-skill   # マーケットプレイスの情報を更新
claude plugin update demo-video@demo-video-skill    # 反映にはClaude Codeの再起動が必要
claude plugin uninstall demo-video@demo-video-skill
```

インストールすると、スキル`demo-video:demo-video`が使えるようになります。デモ動画を頼むとClaudeが自動で読み込み、明示的に呼び出すときは`/demo-video:demo-video`と入力してください。

プラグインを使わずに、スキルだけを入れることもできます。`demo-video/skills/demo-video/`を`~/.claude/skills/demo-video/`か、各リポジトリの`.claude/skills/demo-video/`にコピーしてください。

## 必要なソフトウェア

| ソフトウェア | 用途 |
|---|---|
| ffmpeg・ffprobe | 動画の変換と検証。すべての方式で必須 |
| Python 3とPillow | 注釈の描画と、検証用の画像の生成 |
| Node.js | Webとモーション動画の録画。Playwrightは初回の実行時に`~/.cache/demo-video-skill`へ自動でインストールされる |
| Google Chrome | Webとモーション動画の録画。Chromeがなければ、PlaywrightのChromiumを使う |
| VHS | CLIの録画だけで使う。`brew install vhs`でインストールできる |

足りないソフトウェアがあると、Claudeはインストールしてよいかを確認してから入れます。

## 使い方

Claude Codeへの頼み方の例です。

- 「このPRで追加した検索フォームの動きをGIFにして、PRの説明に貼りたい」
- 「CLIの使い方をREADMEに載せる動画にして」
- 「このライブラリの仕組みを30秒くらいのアニメーションで説明したい」

完成した動画の隣には、録り直し用のスクリプト、注釈の時刻と文言を記録した`*.annotations.json`、注釈を入れる前の`*.plain.mp4`が保存されます。テロップの文言やタイミングだけを直すなら、録り直さずに焼き込み直せます。

```bash
S=~/.claude/plugins/cache/demo-video-skill/demo-video/<version>/skills/demo-video/scripts
python3 $S/annotate.py demo.plain.mp4 demo.annotations.json --check   # 配置の確認（数秒）
python3 $S/annotate.py demo.plain.mp4 demo.annotations.json demo.mp4  # 焼き込み
```

## 見た目のカスタマイズ

クリックエフェクト、強調色、角の丸み、文字の大きさ、下地の色は、どれも初期設定の値です。クリックエフェクトは初期設定で「広がって消える白い輪」になっており、`"ring"`・`"disc"`・`"none"`の3種類から選べます。初期設定の一覧は次のコマンドで確認できます。

```bash
python3 $S/annotate.py --default-style > my-style.json
```

1本の動画だけ見た目を変えるときは、`annotations.json`に`"style"`を書き足してください。「クリックエフェクトは黄色の円で」のようにClaudeへ頼むこともできます。いつも同じ見た目で作りたい場合は、スタイルを書いたJSONファイルを用意し、環境変数`DEMO_VIDEO_STYLE`にそのパスを設定してください。どちらの方法でも、変えたい項目だけを書けば十分です。

```json
{
  "click": { "style": "disc", "color": [255, 196, 0] },
  "accent": [40, 110, 255],
  "corner_radius": 3
}
```

## リポジトリの構成

```text
.claude-plugin/marketplace.json     マーケットプレイスの定義
demo-video/                         プラグイン本体
  .claude-plugin/plugin.json
  skills/demo-video/
    SKILL.md                        作り方の選び方、注釈、検証、仕上げの手順
    references/                     方式ごとの手順（web.md・cli.md・motion.md）
    scripts/                        録画、注釈、検証のスクリプト
evals/                              スキルの評価に使うテストケースと題材
```

公開する前に、`claude plugin validate .`と`claude plugin validate ./demo-video`で定義ファイルを検証してください。プラグインの内容を変えたときは、`plugin.json`と`marketplace.json`の`version`を上げる必要があります。上げないと、インストール済みの環境に更新が届きません。
