# モーション動画（HTML/CSS アニメーションを録画）

見せる UI がない対象（ライブラリの API、内部処理、データの流れ、アーキテクチャ、ビフォー/アフター比較）は、1 枚の HTML にアニメーションを書き、`scripts/record_web.mjs` で録画する。Remotion などの動画フレームワークは使わない。HTML/CSS で十分に表現でき、依存が増えないため。

## 手順

1. **台本を書く。** シーンごとに「何を見せるか」と秒数を決める。モーション動画は情報を盛り込みすぎやすい。1 シーンで伝えることは 1 つにする。
2. **HTML を 1 ファイルで書く**（下のテンプレート）。外部ファイルや CDN に頼らない。フォントもシステムフォントを使う。
3. **静止画で下見する。** 録画の前に、各シーンの途中の時点の見た目を確認する。テンプレートの `?t=秒` パラメータを使うと、その時点まで早送りした状態を開ける。
   ```bash
   node -e "
   const {chromium}=require(require('os').homedir()+'/.cache/demo-video-skill/node_modules/playwright');
   (async()=>{const b=await chromium.launch({channel:'chrome'});const p=await b.newPage({viewport:{width:1280,height:720}});
   for (const t of [1,4,8]) { await p.goto('file://'+process.cwd()+'/motion.html?t='+t); await p.waitForTimeout(700); await p.screenshot({path:'preview-'+t+'.png'}); }
   await b.close();})()"
   ```
   （`motion.html` のあるディレクトリで実行する。`~/.cache/demo-video-skill` は `record_web.mjs` を一度実行すると作られる。700ms 待つのは、テンプレートのフェード（0.5 秒）が終わる前に撮ると半透明の画面が写るため。）レイアウトが崩れていないか、文字がはみ出していないかを Read で確認する。
4. **録画する。** HTML を渡すと、ページは `window.__demoDone = true` になるか `--duration` 秒（既定 60）が経つまで録画される。
   ```bash
   node <skill>/scripts/record_web.mjs motion.html --out docs/demo/overview.mp4 --duration 30
   ```
5. SKILL.md の「検証する」に戻る。

## テンプレート

シーンを `<section>` で並べ、`data-dur`（秒）の順に表示する。各シーンの中身は CSS のアニメーションで動かす。

テロップは HTML には描かない。各シーンの `data-caption` に書くと、テンプレートの script がそれを `window.__demoAnnotations` に変換し、`record_web.mjs` が録画後に Pillow で焼き込む（SKILL.md の「注釈と演出」）。テロップは図やコードに重ならない場所に自動で置かれる。シーン内の見出しや図のラベルは内容そのものなので、HTML で描いてよい。

強調やズームは、HTML 自体のアニメーション（`transform: scale` やハイライト）で表現するのが基本。録画後の Pillow 処理で足したい場合は、`window.__demoAnnotations` に `{type: 'zoom', start, end, x, y, w, h}` や `{type: 'box', ...}` を直接追加できる（書式は `scripts/annotate.py` の冒頭を参照）。

```html
<!doctype html>
<html lang="ja"><meta charset="utf-8">
<style>
  :root { --bg:#0f172a; --fg:#e2e8f0; --accent:#38bdf8; --muted:#64748b; }
  * { box-sizing: border-box; margin: 0; }
  html, body { width: 1280px; height: 720px; overflow: hidden; background: var(--bg); color: var(--fg);
    font-family: -apple-system, "Hiragino Sans", "Noto Sans JP", sans-serif; }
  .scene { position: absolute; inset: 0; display: grid; place-items: center; padding: 64px;
    opacity: 0; transition: opacity .5s; }
  .scene.on { opacity: 1; }
  h1 { font-size: 64px; } h2 { font-size: 44px; } p { font-size: 28px; color: var(--muted); }
  /* シーンが表示されたときだけ中の要素が動くようにする */
  .scene.on .rise { animation: rise .6s both; }
  .scene.on .rise:nth-child(2) { animation-delay: .25s; }
  .scene.on .rise:nth-child(3) { animation-delay: .5s; }
  @keyframes rise { from { opacity: 0; transform: translateY(24px); } }
</style>
<body>
  <section class="scene" data-dur="3"><div><h1 class="rise">タイトル</h1><p class="rise">サブタイトル</p></div></section>
  <section class="scene" data-dur="5" data-caption="テロップ：このシーンで伝えたいこと"><div><h2 class="rise">ポイント 1</h2><p class="rise">説明</p></div></section>
  <section class="scene" data-dur="4" data-caption="まとめのテロップ"><div><h2 class="rise">まとめ</h2></div></section>
<script>
  const scenes = [...document.querySelectorAll('.scene')];
  const seek = Number(new URLSearchParams(location.search).get('t') || 0);  // 下見用: ?t=秒
  let t0 = 0;
  const plan = scenes.map(s => { const start = t0; t0 += Number(s.dataset.dur); return { s, start, end: t0 }; });
  // テロップ: data-caption を Pillow で焼き込む注釈に変換する（シーン切り替えの 0.3 秒後から、終わりの 0.2 秒前まで）
  window.__demoAnnotations = plan.filter(p => p.s.dataset.caption).map(p => ({
    type: 'caption', text: p.s.dataset.caption,
    start: Math.max(0, p.start + 0.3 - seek), end: p.end - 0.2 - seek,
  }));
  const startedAt = performance.now() - seek * 1000;
  (function tick() {
    const t = (performance.now() - startedAt) / 1000;
    for (const { s, start, end } of plan) s.classList.toggle('on', t >= start && t < end);
    if (t >= t0) { plan.at(-1).s.classList.add('on'); window.__demoDone = true; return; }
    requestAnimationFrame(tick);
  })();
</script>
</html>
```

## 表現のパターン

| 伝えたいこと | 表現 |
|---|---|
| データの流れ、処理のパイプライン | 箱を横に並べ、矢印や点を順に移動させる（CSS の `offset-path` か `transform` のトランジション） |
| コードの使い方 | コードブロックを 1 行ずつ表示し、実行結果を隣に出す。コードのハイライトは手書きの `<span>` で付ける（CDN に頼らない） |
| ビフォー/アフター | 画面を左右に分け、数値はカウントアップで見せる |
| アーキテクチャ | 全体図を出してから、注目部分をハイライトやズーム（`transform: scale`）で示す |
| 手順 | 番号付きのステップを 1 つずつ強調する |

## 注意

- **ページサイズは録画サイズと一致させる。** `html, body` を 1280x720 固定にすると、スクロールバーや余白が映らない。
- **動きは控えめにする。** 回転や跳ねる動きを多用すると内容が頭に入らない。フェード、スライド、ハイライトで十分に伝わる。
- **文字は大きく、少なくする。** 1 シーンの本文は 2 行まで、本文は 28px 以上にする。
- **テロップの場所を空けておく。** テロップは画面下部（下端から約 44px、高さ約 60px）に出る。シーンの内容を下端まで詰めない。
- **下見の静止画にはテロップが入らない。** テロップの見た目とタイミングは、録画後に `inspect_video.py --at` で確かめる。
- **シーン全体の秒数の合計**を `--duration` より短くする。`__demoDone` が立てば録画はその時点で終わる。
