# scribe relay — ライブ配信の公開中継

scribeの執筆を外から見られるようにする中継サーバー。ローカルのscribe_live.py /
scribe_watch.htmlをそのまま小さなクラウドサービスに載せる（原本は ~/quartz/scripts/、
このディレクトリのコピーは `./sync.sh` で更新する）。

```
Macのscribe(放送卓) ──wss + token──▶ クラウド上の中継 ──wss──▶ 世界中の /watch
```

## デプロイ先: Render無料枠

コード変更なしでそのまま動く。無料の代わりに、15分アクセスがないとスリープし、
次のアクセスで起動に30〜60秒かかる。この間watchページは既存の「Away from
Screen」表示にそのまま収まる(UI変更不要)。

1. https://render.com でアカウント作成（GitHubログイン可）
2. このディレクトリをGitリポジトリにしてpush（Renderはリポジトリからビルドする。
   まだGit管理していなければ `cd ~/rede/relay && git init && git add -A && git commit -m init`
   してからGitHub等に作成したリポジトリへpush）
3. Renderダッシュボードで "New +" → "Blueprint" → 上記リポジトリを選択
   （`render.yaml` を自動検出する）
4. 環境変数 `SCRIBE_PUB_TOKEN` をダッシュボードで設定。**ローカルと同じ値にする**こと
   （ローカルの値は `~/.scribe_live.json` の `token`。値の確認は
   `python3 -c "import json;print(json.load(open('$HOME/.scribe_live.json'))['token'])"`)
5. デプロイ完了後に発行されるURL（例: `https://rede-relay.onrender.com`）を控える
6. Macのscribeを中継に向ける: `~/.scribe_live.json` の `relay` を設定して、scribeサーバーを再起動

   ```json
   { "token": "(そのまま)", "relay": "wss://rede-relay.onrender.com" }
   ```

   ```sh
   pkill -f scribe_server.py   # その後Automatorアプリで起動し、scribeタブをリロード
   ```

7. 確認: 発行されたURLの `/watch` を開く（スマホ回線からでも見える。スリープ復帰直後は表示まで数十秒かかる）

### 更新のたびに

```sh
cd ~/rede/relay && ./sync.sh && git add -A && git commit -m sync && git push
```

Renderはpushを検知して自動的に再デプロイする。

## 備考

- 中継は保存を一切しない（最新スナップショット1枚をメモリに持つだけ）。日記データはクラウドの外（Mac）から出ない
- `relay` を `""` に戻してscribeを再起動すれば、いつでもローカル配信のみに戻る
- 遠隔の/watchでは、日記内の画像（Macのlocalhost相対パス）は表示されない。画像の公開はフェーズ2（保存のSupabase移行）で解決する話なので、ここでは扱わない
