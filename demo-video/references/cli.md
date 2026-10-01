# CLI・ターミナルの録画（VHS）

[VHS](https://github.com/charmbracelet/vhs) は、`.tape` ファイルに書いたキー入力を仮想ターミナルで再生して録画する。人が手で操作するのと違い、タイプミスや待ち時間のばらつきが入らず、何度でも同じ動画を作り直せる。

## 手順

1. **コマンドを実際に試す。** 録画の前に、見せたいコマンドを普通の Bash で実行し、出力と所要時間を把握する。出力が長すぎる、時間がかかりすぎる、環境依存のパスが出る、といった問題は録画前に見つけて対処する。
2. **`.tape` を書く**（下のテンプレート）。
3. **録画する。**
   ```bash
   vhs demo.tape
   ```
   `.tape` 内の `Output` に書いたパスに出力される。
4. **注釈を焼き込む**（テロップなどを入れる場合）。
   ```bash
   python3 <skill>/scripts/tape_annotations.py demo.tape --burn
   ```
   `.tape` の `# @caption` などの印から `demo.annotations.json` を作り、`demo.plain.mp4` に焼き込んで `demo.mp4` を出力する。GIF が必要なら、焼き込んだ後の MP4 から `to_gif.sh` で作る（VHS の GIF 出力は使わない。注釈が入らないため）。
5. SKILL.md の「検証する」に戻る。印の時刻は推定なので、`inspect_video.py --at` で各テロップの表示中の時刻を指定して確かめる。

## テンプレート

```tape
Output docs/demo/cli.plain.mp4

Set Shell "bash"
Set FontSize 22
Set Width 1280
Set Height 720
Set Padding 24
Set Theme "Catppuccin Mocha"
Set TypingSpeed 60ms
Set WindowBar Colorful

# 準備（録画に映さない）。履歴は必ず切る（下の「書き方のポイント」参照）
Hide
Type "cd /path/to/project && export PS1='$ ' HISTFILE=/dev/null && history -c && clear"
Enter
Show

# @title mytool | CSV を JSON に変換する CLI
Sleep 2s

# @caption まずはヘルプ
Type "mytool --help"
Sleep 400ms
Enter
Sleep 2.5s

# @caption CSV を JSON に変換
Type "mytool convert input.csv --format json | head -20"
Sleep 400ms
Enter
Sleep 3s
# @end
Sleep 500ms
```

## 注釈の印

VHS には画面に文字を重ねる機能がない。そこで、表示したい位置に印のコメントを書き、録画後に `tape_annotations.py` で Pillow を使って焼き込む。印は普通のコメントなので、VHS の動作には影響しない。

| 印 | 意味 |
|---|---|
| `# @caption テキスト` | テロップ。空いている場所に自動で置かれる（下部中央を優先）。次の `@caption`・`@title` か `@end` まで表示 |
| `# @caption-top テキスト` | 上部に固定したテロップ（自動配置を使わない） |
| `# @title タイトル \| サブタイトル` | 画面を暗くして中央に出すタイトル |
| `# @box X Y W H ラベル` | 画面上の矩形を赤枠で囲む（ラベルは省略可）。次の `@box` か `@end` まで表示 |
| `# @callout X Y W H テキスト` | 矩形の横の空いた場所に吹き出しを出す。次の `@callout` か `@end` まで表示 |
| `# @zoom X Y W H` | 矩形に滑らかにズームする。次の `@zoom` か `@zoom-out` まで保つ |
| `# @zoom-out` | ズームを戻す |
| `# @end` | 表示中のテロップ・タイトル・枠・吹き出しを消す（ズームはそのまま） |

座標は録画した動画のピクセル座標。いったん `vhs` で録画してから、方眼付きの画像で読み取る。
```bash
python3 <skill>/scripts/inspect_video.py demo.plain.mp4 --at 9.5 --grid   # 100px ごとの方眼入りの原寸画像
```
印を足しても VHS の動作は変わらないので、座標を書き込んだら録り直さずに `tape_annotations.py --burn` だけ実行すればよい。

ターミナルは文字が詰まっていて注釈の置き場所が少ない。長い出力の一部を読ませたいときは `@zoom` で寄ると、テロップや吹き出しの置き場所もできる。

時刻は `.tape` の記述から計算する（`Type` は「文字数 − 1」× TypingSpeed、`Sleep` は指定どおり、`Hide` 中は数えない）。実測との差は 0.1 秒程度。ただし `Wait` の所要時間は計算できない。`Wait` の後に印を置くと時刻が早めにずれるので、印の前は `Sleep` で区切る。

## 書き方のポイント

- **ユーザーのシェル履歴を使わない。** VHS のシェルは、環境変数 `HISTFILE` を通してユーザー本人の履歴ファイルを読むことがある。その状態で `Up` や `Ctrl+R` を使うと、本人の過去のコマンドが動画に映り、しかも**実行される**（テストで実際に起きた）。準備の `Hide` の中で必ず `export HISTFILE=/dev/null && history -c` を実行し、直前のコマンドを再利用する演出も `Up` ではなくコマンドを入力し直して作る。
- **`Output` のパス。** 相対パスは `vhs` を実行したディレクトリが基準になる。絶対パスは `Output "/abs/path/demo.plain.mp4"` のように引用符で囲む（囲まないと構文エラーになる）。
- **`Hide` / `Show` で準備を隠す。** `cd`、環境変数の設定、プロンプトの簡素化（`PS1='$ '`）、`clear` は `Hide` の中で行う。長いホームディレクトリのパスやユーザー名がプロンプトに映るのを防げる。
- **Enter の後には必ず `Sleep` を入れる。** 出力を読む時間が必要で、目安は「出力の行数 × 0.2 秒 + 1 秒」。処理時間が読めないコマンドは `Wait` でプロンプトの再表示を待ち、その後に `Sleep` で読む時間を取る。
- **テロップの置き場所を残す。** テロップは空いている場所に自動で置かれ、どこにも空きがなければ映像を少し縮めて下に帯を作る。帯が頻繁に出ると落ち着かないので、出力が画面の下端まで届かないようにする（`Set Height` を増やす、出力を短くする）。`# @caption-top` で上に固定すると、ターミナル最上部のコマンド行を隠しやすい。
- **1 画面に収まらない出力を見せる必要があるとき。** 出力の形（JSON 全体など）を見せたいなら `Set Height` を伸ばす（縦長の動画になるので README 用なら許容しやすい）。中身の一部を見せれば足りるなら、オプションで出力を絞る。`| head` で途中を切ると壊れた出力が映るので、構造のあるデータには使わない。
- **1 画面に入る行数の目安。** `FontSize 22`・`Height 720`・`Padding 24` でおよそ 24 行。テロップの分を引くと、出力に使えるのは 20 行程度。これを超える出力は、絞るか画面を縦に伸ばす。
- **出力を短くする。** 長い出力は `| head`、`--limit` などで絞る。スクロールして流れ去る出力は誰も読めない。
- **日本語**は問題なく表示できる（確認済み）。ただし `Type` での日本語入力は 1 文字ずつ打たれるので、長文なら `TypingSpeed` を短くする。
- **TUI アプリ**は、`Up` / `Down` / `Tab` / `Ctrl+C` / `Escape` などのキー指定で操作できる。
- **破壊的なコマンドを見せる場合**（削除、デプロイなど）は、一時ディレクトリやダミーデータで録画する。`.tape` は実際にそのコマンドを実行する。

## 主なコマンド

| コマンド | 意味 |
|---|---|
| `Type "..."` | 文字を入力する |
| `Enter` / `Tab` / `Backspace 3` / `Up` / `Ctrl+C` | キーを押す |
| `Sleep 1.5s` | 待つ |
| `Wait /regex/` | 画面の最終行が正規表現に一致するまで待つ（既定のタイムアウトは 15 秒。`Wait@60s /regex/` で延長できる）。後続の注釈の時刻がずれる点に注意 |
| `Hide` / `Show` | 録画を一時停止・再開する |
| `Screenshot path.png` | その時点の静止画を保存する |
| `Set ...` | 見た目の設定（`vhs themes` でテーマ一覧を表示） |

## うまくいかないとき

- **`Wait` がタイムアウトする**: プロンプトの形が正規表現と合っていない。`Hide` 内で `PS1` を単純化しておくと安定する。
- **コマンドが見つからない**: VHS は新しいシェルで動くので、エイリアスや `.zshrc` で通したパスは使えない。`Set Shell "bash"` に加え、`Hide` 内で `export PATH=...` するか、フルパスで呼ぶ。
- **色が出ない**: 一部のツールは TTY を検出して色を付ける。`FORCE_COLOR=1` や `--color=always` を付ける。
