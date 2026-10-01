# demo-video-skill

ソフトウェアの動きを見せる短いデモ動画（MP4・GIF）を作る、Claude Code用のプラグインです。

デモの対象に合わせて、Claudeが3つの作り方から1つを選びます。

| 対象 | 作り方 |
|---|---|
| ブラウザで動くUI | Playwrightで操作して録画する。ブラウザはインストール済みのChromeを使う |
| CLI・ターミナル・TUI | VHSの`.tape`で録画する |
| UIのないライブラリや仕組みの説明 | HTML/CSSのアニメーションを録画する |

テロップやズーム、クリックエフェクトは録画後に自動で入り、画面の空いている場所に配置されます。

## サンプル

このスキルで作った動画です。どれもClaude Codeに一言頼んだだけで、録画からテロップ、ズーム、仕上がりの確認までをClaudeが行いました。

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

Claude Codeで次のコマンドを実行してください。

```text
/plugin marketplace add hnegishi/demo-video-skill
/plugin install demo-video@demo-video-skill
```

更新するときは`claude plugin update demo-video@demo-video-skill`を実行し、Claude Codeを再起動してください。

どの方式でも、ffmpegとPython 3（Pillow）を使います。Webとモーション動画の録画にはNode.jsとGoogle Chromeが必要で、Chromeの代わりにPlaywrightのChromiumも使えます。CLIの録画にはVHSが必要です。足りないものがあると、Claudeはインストールしてよいかを確認してから入れます。

## 使い方

Claude Codeへの頼み方の例です。

- 「このPRで追加した検索フォームの動きをGIFにして、PRの説明に貼りたい」
- 「CLIの使い方をREADMEに載せる動画にして」
- 「このライブラリの仕組みを30秒くらいのアニメーションで説明したい」

クリックエフェクトや強調色などの見た目は初期設定の値で、「クリックエフェクトは黄色の円で」のように頼めば変えられます。

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
