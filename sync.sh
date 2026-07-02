#!/bin/bash
# 中継のソースはローカルscribeと共通(~/quartz/scripts/が原本)。
# デプロイ前に必ずこれを実行して原本をコピーしてくる。
set -eu
cd "$(dirname "$0")"
cp ~/quartz/scripts/scribe_live.py .
cp ~/quartz/scripts/scribe_watch.html .
echo "synced: scribe_live.py, scribe_watch.html"
