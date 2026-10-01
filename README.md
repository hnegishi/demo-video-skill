# demo-video-skill

ソフトウェアの動きを見せる短いデモ動画（MP4 / GIF）を作る Claude Code 用のスキル。

デモ対象に合わせて、作り方を 3 つから選ぶ。

| 対象 | 作り方 |
|---|---|
| ブラウザで動く UI | Playwright で操作して録画（インストール済みの Chrome を使う） |
| CLI・ターミナル・TUI | VHS の `.tape` で録画 |
| UI のないライブラリや仕組みの説明 | HTML/CSS アニメーションを録画 |

テロップ・タイトル・強調枠・吹き出し・クリックエフェクト・ズームは、録画した後に Pillow で焼き込む。置き場所と下地の色は、実際の映像を見て自動で決める（コンテンツや強調対象に重ねない）。日本語は OS に標準で入っているフォントで描く。

## 構成

```
demo-video/              スキル本体（このディレクトリを ~/.claude/skills/ などに置く）
  SKILL.md               作り方の選び方、注釈、検証、仕上げ
  references/            方式ごとの手順（web.md / cli.md / motion.md）
  scripts/               録画・注釈・検証のスクリプト
evals/                   スキルの評価用のテストケースと題材
```

## 必要なもの

- ffmpeg / ffprobe
- Python 3 と Pillow
- Node.js（Web とモーション動画。Playwright は初回に `~/.cache/demo-video-skill` へ自動で入る）
- Google Chrome（無ければ Playwright の Chromium）
- VHS（CLI の録画のみ）

## 使い方

Claude Code で「この機能のデモ動画を作って」「CLI の使い方を GIF にして README に貼りたい」のように頼む。

注釈だけを直すときは録り直さずに済む。

```bash
python3 demo-video/scripts/annotate.py demo.plain.mp4 demo.annotations.json --check   # 配置の確認（数秒）
python3 demo-video/scripts/annotate.py demo.plain.mp4 demo.annotations.json demo.mp4  # 焼き込み
```
