# Web アプリの操作録画

`scripts/record_web.mjs` にシナリオファイルを渡して録画する。シナリオには操作の流れと注釈だけを書く。録画の開始と終了、カーソル表示、MP4 への変換、注釈の焼き込みはスクリプトが受け持つ。

## 手順

1. **アプリを起動する。** ローカル開発サーバーなら、バックグラウンドで起動してポートが応答するまで待つ（`curl -sf http://localhost:3000 >/dev/null` をループする）。録画が終わったら止める。
2. **画面を下見する。** 録画の前に、Playwright でスクリーンショットを 1 枚撮るか、DOM からセレクタを確認する。存在しないセレクタで録画が失敗する手戻りを防ぐため。セレクタは `getByRole` / `getByText` / `data-testid` のような、壊れにくいものを優先する。
3. **シナリオを書く**（下のテンプレート）。
4. **録画する。**
   ```bash
   node <skill>/scripts/record_web.mjs demo.scenario.mjs --out docs/demo/feature.mp4
   ```
   オプション: `--width 1280 --height 720`、`--keep-webm`（元の WebM も残す）、`--style style.json`（見た目の上書き。SKILL.md の「見た目は既定値」）
   シナリオが例外で止まった場合は、`<out>.error.png` にその時点の画面が保存され、終了コード 1 で終わる。

   注釈を 1 つでも使うと、次の 3 つが出力される。
   - `feature.mp4` — 注釈入りの完成版
   - `feature.plain.mp4` — 注釈なしの録画
   - `feature.annotations.json` — 注釈の時刻・文言・座標。テロップの文言やタイミングだけを直したいときは、これを編集して `python3 <skill>/scripts/annotate.py feature.plain.mp4 feature.annotations.json feature.mp4` を実行する（録り直し不要）
5. SKILL.md の「検証する」に戻る。

`record_web.mjs` に `.html` ファイルを直接渡すと motion モード（操作なしでページを再生するだけ）になる。ローカルの HTML アプリを操作して録画したいときは、シナリオの中で `file://` の URL を開く。シナリオ内の相対パスは、シナリオファイルの場所を基準にして解決する。

```js
import { fileURLToPath } from 'node:url';
const app = new URL('../app/index.html', import.meta.url).href;   // シナリオからの相対パス
export default async ({ page, demo }) => { await page.goto(app); demo.start(); /* ... */ };
```

## シナリオのテンプレート

```js
// demo.scenario.mjs
export default async ({ page, demo }) => {
  await page.goto('http://localhost:3000');
  await page.waitForLoadState('networkidle');
  demo.start();                                  // ここより前（読み込み中の白画面）はカットされる

  await demo.title('完了フィルタ', { sub: 'v1.4 の新機能', ms: 2000 });  // 冒頭のタイトル

  await demo.caption('タスクを追加する', 0);       // テロップを出し、待たずに操作へ（次の caption まで表示）
  await demo.zoomTo([page.getByPlaceholder('やること'), page.getByRole('button', { name: '追加' })]);  // 入力欄に寄る
  await demo.type(page.getByPlaceholder('やること'), '牛乳を買う');
  await demo.click(page.getByRole('button', { name: '追加' }));   // クリックエフェクトは自動
  await demo.zoomOut();                                            // 結果は全体で見せる
  await demo.pause(800);

  await demo.caption('完了にするとリストから消える', 0);
  await demo.click('li:has-text("牛乳を買う") input[type=checkbox]');
  await demo.box('#list', { label: '未完了だけ残る', ms: 1500 });   // 赤枠で強調
  await demo.callout('#count', '件数も更新される', { ms: 1500 });    // 吹き出し
  await demo.hideCaption();
  await demo.pause(500);
};
```

`demo` ヘルパー:

| 関数 | 動作 |
|---|---|
| `demo.start()` | 録画の実質的な開始点を記録する。これより前は MP4 からトリムされる |
| `demo.click(target, {pauseAfter, effect})` | カーソルを滑らかに移動してクリックする。クリックエフェクト（広がって消える白い輪）が付く。`effect: false` で消せる |
| `demo.type(target, text, {delay})` | 入力欄をクリックして 1 文字ずつ入力する（既定 70ms/文字）。最初のクリックにエフェクトが付く |
| `demo.zoomTo(target, {pad, ease, maxScale})` | 要素（配列で複数指定可）に滑らかにズームし、`zoomOut()` か次の `zoomTo()` まで保つ。ズームし終わるまで待つ。既定は余白 24px、0.6 秒、最大 2.5 倍 |
| `demo.zoomOut({ease})` | ズームを戻す |
| `demo.hover(target)` | カーソルを移動してとどまる（ツールチップを見せるときなど） |
| `demo.moveTo(x, y, ms)` | 座標を指定してカーソルを移動する |
| `demo.caption(text, ms, {position})` | テロップを出し、`ms` だけ待つ（`ms=0` なら待たずに次へ）。次の `caption` か `hideCaption` まで表示される。置き場所は空いている所に自動で決まる（下部中央を優先）。`{position: 'top'}` などで固定もできる |
| `demo.hideCaption()` | テロップを消す |
| `demo.title(text, {sub, ms})` | 画面を暗くして中央に大きなタイトルを出す |
| `demo.box(target, {label, ms})` | 要素を赤枠で囲む（ラベル付き可）。配列を渡すと全体を囲む（例: 表示中の行だけ `page.locator('li').all()`） |
| `demo.callout(target, text, {ms})` | 要素の横の空いた場所に吹き出しを出す。配列も可 |
| `demo.pause(ms)` | 待つ |

`target` には CSS セレクタ文字列か Playwright の Locator を渡す。

注釈と演出（`caption` / `title` / `box` / `callout` / クリックエフェクト / ズーム）はページには描かれない。呼んだ時刻と要素の座標が記録され、録画後に Pillow で焼き込まれる。置き場所と色は `annotate.py` が映像を見て決めるので、シナリオで位置を指定する必要はない（SKILL.md の「注釈と演出」）。気をつけること:
- `box`・`callout`・`zoomTo` の座標は呼んだ瞬間のもの。表示中にスクロールやレイアウト変更をすると枠がずれる。レイアウトが変わる操作の直後は `demo.pause(300)` を挟んでから呼ぶ。
- ズーム中は、ズーム範囲の外へカーソルを動かさない。範囲外の要素を操作するなら、先に `zoomOut()` する。
- ページ内で `document.body.style.zoom` を使っても座標は正しく取れる（確認済み）。

`page` は通常の Playwright Page なので、スクロール（`page.mouse.wheel`）、キー操作（`page.keyboard.press`）、ページ遷移の待機はそのまま書ける。

## 見やすくするコツ

- **間を取る。** 操作直後に次の操作へ進むと、見る側が追いつけない。結果が画面に出たら 0.8〜1.5 秒待つ。テロップは「文字数 × 0.1 秒 + 1 秒」程度を目安に表示する。
- **テロップは操作の直前に出す。** `caption(text, 0)` で出してすぐ操作すると、何をするかを読んでから動きを見られる。
- **テロップと UI が重なるなら** `{position: 'top'}` にするか、ページ側の余白を CSS で調整する。
- **`box` のラベルは枠の左上の外側に出る。** 枠のすぐ上に別の要素があると重なるので、`pad` で枠を広げるか、ラベルなしにして `callout` で説明する。
- **文字を大きくする。** 小さいアプリを 1280x720 で録ると文字が読めない。`page.evaluate(() => document.body.style.zoom = '1.3')` で拡大するか、ビューポートを小さくする（`--width 960 --height 540`）。ただし拡大しすぎて画面の下端までアプリが埋まると、テロップの置き場所がなくなり `[annotate] band`（映像を縮めて帯を作る）が毎回出る。画面の下に 120px 程度の空きが残る倍率にとどめ、細部はズーム（`zoomTo`）で見せる。
- **操作でレイアウトが動く場合**（フィルタで行数が変わる、など）は、クリックした直後にボタンが移動し、カーソルやクリックの輪が取り残されて見える。録画時だけ CSS でリストの `min-height` を固定するなどして、ボタンの位置を動かさない。
- **余計なものを映さない。** Cookie バナーや開発用オーバーレイ（Next.js のインジケータなど）は、`demo.start()` の前に閉じるか CSS で隠す。
- **ログインが必要なアプリ**では、ログイン操作を `demo.start()` の前に済ませて録画から外す。認証情報が必要ならユーザーに聞く。推測した値や本番の認証情報を入力しない。
- **データを用意する。** 空のリストを見せても伝わらない。録画前にシード用スクリプトや API でそれらしいデータを入れておく。実在の個人情報は使わない。

## うまくいかないとき

- **動画が想定より短い、または途中で止まって見える**: `record_web.mjs` は止まった画面でも録画が続くよう対策しているが、`page.waitForTimeout` を使わずに Node 側で `setTimeout` などで待つとずれることがある。待機には必ず `demo.pause` か `page.waitForTimeout` を使う。
- **ブラウザが起動しない**: Chrome が無い環境では `npx playwright install chromium` が必要（約 150MB。入れる前にユーザーに確認する）。
- **アニメーションがカクつく**: 録画は実時間の画面キャプチャなので、重い処理と重なるとフレームが落ちる。開発サーバーではなくビルド済みのものを使うと改善することが多い。
