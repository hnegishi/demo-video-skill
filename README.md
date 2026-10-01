# demo-video-skill

ソフトウェアの動きを見せる短いデモ動画（MP4 / GIF）を作る Claude Code 用のプラグインです。

デモ対象に合わせて、作り方を 3 つから選びます。

| 対象 | 作り方 |
|---|---|
| ブラウザで動く UI | Playwright で操作して録画（インストール済みの Chrome を使う） |
| CLI・ターミナル・TUI | VHS の `.tape` で録画 |
| UI のないライブラリや仕組みの説明 | HTML/CSS アニメーションを録画 |

テロップ・タイトル・強調枠・吹き出し・クリックエフェクト・ズームは、録画した後に Pillow で焼き込みます。置き場所と下地の色は実際の映像を見て自動で決め、コンテンツや強調対象には重ねません。日本語は OS に標準で入っているフォントで描きます。

## サンプル

このスキルで作った動画です。どれも Claude Code に一言頼むだけで、録画・テロップ・ズーム・確認までを自動で行っています。題材は `evals/files/` にあります。

<!--
  動画の差し込み方:
  1. GitHub でこのファイルの編集画面を開き、MP4 を本文のどこかにドラッグ＆ドロップする
  2. 挿入された https://github.com/user-attachments/assets/... の URL を切り取り、
     下の VIDEO_URL_WEB / VIDEO_URL_CLI / VIDEO_URL_MOTION と置き換える
-->

<table>
  <tr>
    <th width="33%">Web アプリの操作録画</th>
    <th width="33%">CLI の録画</th>
    <th width="33%">仕組みの説明（モーション動画）</th>
  </tr>
  <tr>
    <td><video src=https://github.com/user-attachments/assets/0772ca97-b38a-4843-af45-11208c868195 controls muted playsinline width="100%"></video></td>
    <td><video src=https://github.com/user-attachments/assets/b990cb11-9df8-4a5b-aef9-8be9041fcb47 controls muted playsinline width="100%"></video></td>
    <td><video src=https://github.com/user-attachments/assets/27444de0-a19c-47fe-979f-e122a188ae7b controls muted playsinline width="100%"></video></td>
  </tr>
</table>

## インストール

Claude Code で、このリポジトリをマーケットプレイスとして追加し、プラグインを入れます。

```text
/plugin marketplace add hnegishi/demo-video-skill
/plugin install demo-video@demo-video-skill
```

ターミナルから入れる場合は次のとおりです。

```bash
claude plugin marketplace add https://github.com/hnegishi/demo-video-skill
claude plugin install demo-video@demo-video-skill
```

更新と削除:

```bash
claude plugin marketplace update demo-video-skill   # マーケットプレイスの情報を更新
claude plugin update demo-video@demo-video-skill        # 反映には Claude Code の再起動が必要
claude plugin uninstall demo-video@demo-video-skill
```

インストール後は、スキル `demo-video:demo-video` として使えます。デモ動画を頼めば自動で読み込まれます。明示的に呼ぶ場合は `/demo-video:demo-video` を使います。

プラグインを使わず、スキルだけを入れることもできます。その場合は `demo-video/skills/demo-video/` を `~/.claude/skills/demo-video/`（または各リポジトリの `.claude/skills/demo-video/`）にコピーします。

## 必要なもの

| 道具 | 用途 |
|---|---|
| ffmpeg / ffprobe | 変換・検証（必須） |
| Python 3 と Pillow | 注釈の描画・検証用の画像 |
| Node.js | Web とモーション動画の録画。Playwright は初回に `~/.cache/demo-video-skill` へ自動で入る |
| Google Chrome | Web とモーション動画の録画。無ければ Playwright の Chromium を使う |
| VHS | CLI の録画のみ（`brew install vhs`） |

足りない道具がある場合、Claude はインストールしてよいかを確認してから入れます。

## 使い方

Claude Code で、たとえば次のように頼みます。

- 「この PR で追加した検索フォームの動きを GIF にして、PR の説明に貼りたい」
- 「CLI の使い方を README に載せる動画にして」
- 「このライブラリの仕組みを 30 秒くらいのアニメーションで説明したい」

成果物の隣には、録り直し用のスクリプト、注釈の時刻と文言（`*.annotations.json`）、注釈なしの録画（`*.plain.mp4`）が残ります。テロップだけを直したいときは、録り直さずに焼き込み直せます。

```bash
S=~/.claude/plugins/cache/demo-video-skill/demo-video/<version>/skills/demo-video/scripts
python3 $S/annotate.py demo.plain.mp4 demo.annotations.json --check   # 配置の確認（数秒）
python3 $S/annotate.py demo.plain.mp4 demo.annotations.json demo.mp4  # 焼き込み
```

## 見た目のカスタマイズ

クリックエフェクト（既定は広がって消える白い輪）、強調色、角の丸み、文字の大きさ、下地の色は既定値です。好みに合わせて上書きできます。

```bash
python3 $S/annotate.py --default-style > my-style.json   # 既定値の一覧
```

- 1 本の動画だけ変える: `annotations.json` に `"style": {...}` を書く。Claude に「クリックエフェクトは黄色の円で」のように頼んでもよい
- いつも同じ見た目にする: スタイルの JSON を置き、環境変数 `DEMO_VIDEO_STYLE` でそのパスを指定する

上書きは差分だけ書けば足ります。

```json
{
  "click": { "style": "disc", "color": [255, 196, 0] },
  "accent": [40, 110, 255],
  "corner_radius": 3
}
```

クリックエフェクトは `"ring"`（既定）・`"disc"`・`"none"` から選べます。

## リポジトリの構成

```text
.claude-plugin/marketplace.json     マーケットプレイスの定義
demo-video/                         プラグイン本体
  .claude-plugin/plugin.json
  skills/demo-video/
    SKILL.md                        作り方の選び方、注釈、検証、仕上げ
    references/                     方式ごとの手順（web.md / cli.md / motion.md）
    scripts/                        録画・注釈・検証のスクリプト
evals/                              スキルの評価用のテストケースと題材
```

公開前の確認には `claude plugin validate .` と `claude plugin validate ./demo-video` を使います。プラグインの内容を変えたら、`plugin.json` と `marketplace.json` の `version` を上げてください。
