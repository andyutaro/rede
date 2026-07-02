"""
scribe_relay.py

scribeライブ配信の公開中継サーバー(Fly.io等の小VM用)。
ローカルのscribe_server.pyから配信機能だけを切り出した独立版。

- /            : /watchと同じ閲覧ページ
- /watch       : 閲覧ページ
- /healthz     : 死活監視
- /ws/pub      : 書き手(要 ?token=)。SCRIBE_PUB_TOKEN 環境変数と一致した接続のみ
- /ws/sub      : 閲覧者(公開)

保存・画像アップロード等のCMS機能は一切持たない。中継だけ。
標準ライブラリのみで動作する(scribe本体と同じ判断)。
"""

import http.server
import os
import socketserver

import scribe_live

PORT = int(os.environ.get('PORT', '8080'))
WATCH_HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scribe_watch.html')

_token = os.environ.get('SCRIBE_PUB_TOKEN')
if not _token:
    raise SystemExit('SCRIBE_PUB_TOKEN が未設定。fly secrets set SCRIBE_PUB_TOKEN=... を実行すること')
scribe_live.set_pub_token(_token)


class RelayHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ('/', '/watch'):
            self._serve_watch()
        elif self.path == '/healthz':
            body = b'ok'
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith('/ws/pub'):
            scribe_live.handle_pub(self)
        elif self.path == '/ws/sub':
            scribe_live.handle_sub(self)
        else:
            self.send_error(404)

    def _serve_watch(self):
        try:
            with open(WATCH_HTML_PATH, 'rb') as f:
                body = f.read()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self.send_error(500, 'scribe_watch.html が見つかりません')

    def log_message(self, format, *args):
        pass


class RelayServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    with RelayServer(('0.0.0.0', PORT), RelayHandler) as httpd:
        print(f'scribe relay listening on :{PORT}')
        httpd.serve_forever()


if __name__ == '__main__':
    main()
