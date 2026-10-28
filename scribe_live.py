"""
scribe_live.py

scribeのライブ配信(REDEフェーズ1スパイク)。
書き手(scribe編集画面)が /ws/pub に送った内容スナップショットを、
閲覧者(/watch)の /ws/sub 接続へそのまま中継する。

一方向性はこのファイルの構造で担保する:
  - pub接続から読んだフレームだけが broadcast() に入る
  - sub接続から読んだデータは、接続維持に必要なping/close処理を除き
    すべて破棄する
  つまり「閲覧者→書き手」へデータが届く経路がコード上存在しない。

WebSocket(RFC 6455)は標準ライブラリのみで実装する(scribe本体と同じ判断)。
サーバー→クライアントの送信フレームはマスクなし、クライアント→サーバーの
受信フレームはマスクありという仕様の非対称性に注意。
"""

import base64
import hashlib
import hmac
import json
import os
import struct
import threading
from urllib.parse import urlparse, parse_qs

_WS_GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'

# 1フレームの上限。全量スナップショット方式でも1日分は数百KBに収まる想定で、
# 公開時にpubを装った巨大フレームでメモリを食い潰されるのを防ぐ。
_MAX_FRAME = 4 * 1024 * 1024

# 接続数の上限(2026-10-28)。**無制限だと第三者が繋ぎ続けるだけで無料枠のVMが詰まり、
# ライブ配信が止まる。** 想定は個人の番組で同時数人なので、50でも桁違いの余裕がある。
# 書き手は本人だけだが、タブや端末の重複を見込んで4。環境変数で上げ下げできる。
_MAX_SUBS = int(os.environ.get('SCRIBE_MAX_SUBS', '50'))
_MAX_PUBS = int(os.environ.get('SCRIBE_MAX_PUBS', '4'))
# 認証フレームを送ってこない接続を居座らせない
_AUTH_TIMEOUT = 10

_subs = set()          # 閲覧者の生ソケット
_last_frame = None     # 最後に配信した送信用フレーム(途中参加の閲覧者へ即送るため)
_pubs = 0              # 書き手の接続数(0なら「Away from Screen」)
_pub_token = None      # /ws/pub の共有シークレット。未設定ならpubを全拒否(fail closed)
_lock = threading.Lock()


def set_pub_token(token):
    """書き込み口(/ws/pub)の共有シークレットを設定する。起動時に必ず呼ぶこと。"""
    global _pub_token
    _pub_token = token


def _pub_authorized(handler):
    """旧方式: ?token= で渡す。**URLはプロキシやログに残る**ので新規はこれを使わない。
    切り替えの谷間で放送が止まらないよう当面は受ける(書き手側の移行を確認したら消す)。"""
    qs = parse_qs(urlparse(handler.path).query)
    supplied = (qs.get('token') or [''])[0]
    return bool(_pub_token) and hmac.compare_digest(supplied, _pub_token)


def _first_message_authorized(msg):
    """新方式(2026-10-28): 接続直後の最初のテキストフレーム {"auth": "..."} で渡す。
    ブラウザはWebSocketに独自ヘッダを付けられないため、URLに載せない方法はこれになる。
    **このフレームは放送しない。**"""
    try:
        data = json.loads(msg.decode('utf-8'))
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    supplied = data.get('auth')
    return (bool(_pub_token) and isinstance(supplied, str)
            and hmac.compare_digest(supplied, _pub_token))


# ---- ハンドシェイク ----

def _handshake(handler):
    key = handler.headers.get('Sec-WebSocket-Key')
    if not key:
        handler.send_error(400, 'WebSocket handshake required')
        return False
    accept = base64.b64encode(
        hashlib.sha1((key + _WS_GUID).encode('ascii')).digest()
    ).decode('ascii')
    # BaseHTTPRequestHandlerのデフォルトはHTTP/1.0で101応答に使えないため、
    # ステータスラインから直接書く。このソケットはもうHTTPには戻らないので
    # close_connectionを立てて、ハンドラのkeep-aliveループから抜けさせる。
    handler.close_connection = True
    handler.wfile.write((
        'HTTP/1.1 101 Switching Protocols\r\n'
        'Upgrade: websocket\r\n'
        'Connection: Upgrade\r\n'
        f'Sec-WebSocket-Accept: {accept}\r\n'
        '\r\n'
    ).encode('ascii'))
    handler.wfile.flush()
    return True


# ---- フレームの読み書き ----

def _read_exact(rfile, n):
    data = b''
    while len(data) < n:
        chunk = rfile.read(n - len(data))
        if not chunk:
            return None
        data += chunk
    return data


def _read_frame(rfile):
    """1フレーム読む。返り値は (fin, opcode, payload)。切断時はNone。"""
    head = _read_exact(rfile, 2)
    if head is None:
        return None
    b1, b2 = head
    fin = bool(b1 & 0x80)
    opcode = b1 & 0x0f
    masked = bool(b2 & 0x80)
    length = b2 & 0x7f
    if length == 126:
        ext = _read_exact(rfile, 2)
        if ext is None:
            return None
        length = struct.unpack('>H', ext)[0]
    elif length == 127:
        ext = _read_exact(rfile, 8)
        if ext is None:
            return None
        length = struct.unpack('>Q', ext)[0]
    if length > _MAX_FRAME:
        return None   # 異常な巨大フレームは接続ごと落とす
    mask = b''
    if masked:
        mask = _read_exact(rfile, 4)
        if mask is None:
            return None
    payload = _read_exact(rfile, length) if length else b''
    if payload is None:
        return None
    if masked and payload:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return fin, opcode, payload


def _make_frame(opcode, payload):
    """サーバー→クライアントの送信フレーム(マスクなし)を組み立てる。"""
    length = len(payload)
    head = bytes([0x80 | opcode])
    if length < 126:
        head += bytes([length])
    elif length < 65536:
        head += b'\x7e' + struct.pack('>H', length)
    else:
        head += b'\x7f' + struct.pack('>Q', length)
    return head + payload


# ---- 配信 ----

def _fanout(frame):
    """組み立て済みフレームを全閲覧者へ送る。送れなかった接続は破棄する。"""
    with _lock:
        dead = []
        for conn in _subs:
            try:
                conn.sendall(frame)
            except OSError:
                dead.append(conn)
        for conn in dead:
            _subs.discard(conn)


def broadcast(payload):
    """pubから受け取ったスナップショットを全閲覧者へ送る。"""
    global _last_frame
    frame = _make_frame(0x1, payload)
    with _lock:
        _last_frame = frame
    _fanout(frame)


def _presence_frame():
    """書き手の在席状態を伝えるメッセージ。スナップショットとはpresenceキーで区別する。"""
    with _lock:
        writing = _pubs > 0
    payload = json.dumps({'presence': 'live' if writing else 'away'}).encode('utf-8')
    return _make_frame(0x1, payload)


# ---- 接続ハンドラ(scribe_server.pyのdo_GETから呼ばれる) ----

def handle_pub(handler):
    """書き手の接続。受信したテキストフレームを閲覧者へ中継し続ける。
    認証は**接続直後の最初のフレーム**({"auth": "..."})で行う(2026-10-28)。
    旧来の ?token= も当面は受ける。認証できるまでは在席にも数えず、放送もしない。"""
    global _pubs
    authed = _pub_authorized(handler)      # 旧方式。移行が済んだら消す
    with _lock:
        if _pubs >= _MAX_PUBS:
            handler.send_error(503, 'too many writers')
            return
    if not _handshake(handler):
        return
    conn = handler.connection
    rfile = handler.rfile
    counted = False

    def _count_in():
        nonlocal counted
        global _pubs
        with _lock:
            _pubs += 1
        counted = True
        _fanout(_presence_frame())

    try:
        if authed:
            _count_in()
        fragments = b''
        while True:
            if not authed:
                # 認証フレームを送らない接続を居座らせない
                conn.settimeout(_AUTH_TIMEOUT)
            frame = _read_frame(rfile)
            if not authed:
                conn.settimeout(None)
            if frame is None:
                return
            fin, opcode, payload = frame
            if opcode == 0x8:    # close
                return
            if opcode == 0x9:    # ping -> pong
                try:
                    conn.sendall(_make_frame(0xA, payload))
                except OSError:
                    return
                continue
            if opcode in (0x0, 0x1, 0x2):   # continuation / text / binary
                fragments += payload
                if not fin:
                    continue
                msg, fragments = fragments, b''
                if not authed:
                    if not _first_message_authorized(msg):
                        return          # 認証できなければ黙って閉じる
                    authed = True
                    _count_in()
                    continue            # **認証フレームは放送しない**
                broadcast(msg)
    except OSError:
        # settimeoutによる時間切れもここに来る(Python 3.10以降 socket.timeout は OSError)
        return
    finally:
        if counted:
            with _lock:
                _pubs -= 1
            _fanout(_presence_frame())


def handle_sub(handler):
    """閲覧者の接続。登録して送りっぱなしにする。受信データは破棄する。
    **接続数に上限を置く**(2026-10-28)。無制限だと繋ぎ続けるだけで無料枠のVMが詰まる。"""
    with _lock:
        if len(_subs) >= _MAX_SUBS:
            handler.send_error(503, 'too many viewers')
            return
    if not _handshake(handler):
        return
    conn = handler.connection
    with _lock:
        _subs.add(conn)
        last = _last_frame
    try:
        if last:
            conn.sendall(last)
        conn.sendall(_presence_frame())  # 現在の在席状態を初期表示用に送る
        # 読み取りループは切断検知とping応答のためだけに回す。
        # それ以外の受信データは一切処理しない(一方向性の担保)。
        while True:
            frame = _read_frame(handler.rfile)
            if frame is None:
                return
            fin, opcode, payload = frame
            if opcode == 0x8:    # close
                return
            if opcode == 0x9:    # ping -> pong
                conn.sendall(_make_frame(0xA, payload))
    except OSError:
        return
    finally:
        with _lock:
            _subs.discard(conn)
