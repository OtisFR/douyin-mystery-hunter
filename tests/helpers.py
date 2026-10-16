"""测试辅助：伪造 HTTP 响应、protobuf 用户、DB 读取"""
import gzip
import sqlite3

import static.Live_pb2 as Live_pb2


class FakeResp:
    def __init__(self, json_data=None, url='', text=''):
        self._json = json_data if json_data is not None else {}
        self.url = url
        self.text = text

    def json(self):
        return self._json


def make_user(display='', nickname='', sec_uid='', mystery_man=0, **kw):
    return Live_pb2.User(desensitized_nickname=display, nickname=nickname,
                         sec_uid=sec_uid, mystery_man=mystery_man, **kw)


def build_frame(*messages, need_ack=False, log_id=7):
    """messages: (method, protobuf_msg) 列表 → 序列化后的 PushFrame（gzip 负载）"""
    resp = Live_pb2.LiveResponse(needAck=need_ack, internalExt='ext-1')
    for method, msg in messages:
        resp.messagesList.add(method=method, payload=msg.SerializeToString())
    frame = Live_pb2.PushFrame(logId=log_id, payload=gzip.compress(resp.SerializeToString()))
    return frame.SerializeToString()


def rows(db_path, sql, params=()):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out
