"""RoomListener：用伪造的 WebSocket 驱动 _run()，喂入真实 protobuf 帧"""
import pytest

import static.Live_pb2 as Live_pb2
from tests.helpers import FakeResp, build_frame, drain, make_user, rows

ROOM = '7000001'


def member(user):
    return 'WebcastMemberMessage', Live_pb2.MemberMessage(user=user)


def chat(user, content):
    return 'WebcastChatMessage', Live_pb2.ChatMessage(user=user, content=content)


def gift(user, name, count):
    return 'WebcastGiftMessage', Live_pb2.GiftMessage(user=user, comboCount=count,
                                                      gift=Live_pb2.GiftStruct(name=name))


@pytest.fixture
def run_room(wl, monkeypatch):
    """返回 run(frames, page_html='', profiles=None) → (listener, events, ws)"""
    def run(frames, page_html='', profiles=None):
        profiles = profiles or {}
        state = {}

        class FakeWebSocketApp:
            def __init__(self, url, header, cookie, on_message, on_open, on_error, on_close):
                self.url, self.on_message, self.sent = url, on_message, []
                state['ws'] = self

            def run_forever(self, origin=None):
                for f in frames:
                    self.on_message(self, f)
                listener.running = False

            def send(self, data, opcode=None):
                self.sent.append(data)

            def close(self):
                pass

        def fail_detail(*a, **k):
            raise RuntimeError('offline')

        monkeypatch.setattr(wl.requests, 'get', lambda *a, **k: FakeResp(text=page_html))
        monkeypatch.setattr(wl.DouyinAPI, 'get_webcast_detail', staticmethod(fail_detail))
        monkeypatch.setattr(wl, 'generate_signature', lambda *a: 'SIG')
        monkeypatch.setattr(wl, 'WebSocketApp', FakeWebSocketApp)
        monkeypatch.setattr(wl, 'lookup_user', lambda sec_uid: dict(profiles.get(sec_uid, {})))

        listener = wl.RoomListener(ROOM, '主播A')
        listener.running = True
        listener._run()
        return listener, drain(listener.events), state.get('ws')
    return run


def db(wl, sql, params=()):
    return rows(wl._DB_PATH, sql, params)


# ---------- 基础方法 ----------

def test_send_event_and_stop(wl):
    listener = wl.RoomListener(ROOM)
    listener.send_event('connected', {'a': 1})
    [ev] = drain(listener.events)
    assert ev['type'] == 'connected' and ev['data'] == {'a': 1} and ev['time'] > 0

    class BrokenWS:
        def close(self):
            raise OSError('already closed')
    listener.running, listener.ws = True, BrokenWS()
    listener.stop()                                   # 异常被吞掉
    assert listener.running is False


def test_write_all_user_saves_regular_record(wl):
    listener = wl.RoomListener(ROOM, '主播A')
    listener._write_all_user({'sec_uid': 'SEC_R', 'display': '路人', 'unique_id': 'r01',
                              'event_type': 'chat', 'extra': {}})
    [r] = db(wl, 'SELECT * FROM mystery_records')
    assert r['is_regular'] == 1 and r['chat_count'] == 1 and r['nickname'] == '主播A'
    assert '"unique_id": "r01"' in r['extra']


# ---------- 连接参数 ----------

def test_ws_url_contains_room_and_signature(run_room):
    _, events, ws = run_room([])
    assert 'room_id=' + ROOM in ws.url and 'signature=SIG' in ws.url and 'cursor=-1' in ws.url
    assert events == []


def test_need_ack_sends_ack_frame(run_room):
    _, _, ws = run_room([build_frame(need_ack=True, log_id=99)])
    [raw] = ws.sent
    ack = Live_pb2.PushFrame()
    ack.ParseFromString(raw)
    assert ack.payloadType == 'ack' and ack.logId == 99 and ack.payload == b'ext-1'


# ---------- 进入 ----------

def test_mystery_enter_is_resolved_and_saved(wl, run_room):
    user = make_user(display='神秘人12345', nickname='神秘人12345', sec_uid='SEC_ALICE_0001', display_id='alice01')
    listener, events, _ = run_room([build_frame(member(user))],
                                   profiles={'SEC_ALICE_0001': {'nickname': 'RealAlice'}})
    [ev] = events
    assert ev['type'] == 'mystery_enter'
    info = ev['data']
    assert info['real_name'] == 'RealAlice' and info['unique_id'] == 'alice01'
    assert info['is_regular'] is False and info['mystery_seq'] == 1
    assert info['room_id'] == ROOM and info['room_nickname'] == '主播A'
    assert listener.mystery_count == 1 and listener.recent_mysteries == [info]
    [r] = db(wl, 'SELECT * FROM mystery_records')
    assert (r['sec_uid'], r['real_name'], r['is_regular'], r['enter_count']) == ('SEC_ALICE_0001', 'RealAlice', 0, 1)


@pytest.mark.xfail(strict=True, reason="BUG: user_id_str 无 id 时返回 '?'（真值），"
                                       "`user_id_str(user) or extra['unique_id']` 的回退永远走不到")
def test_mystery_enter_falls_back_to_profile_unique_id(run_room):
    user = make_user(display='神秘人12345', sec_uid='SEC_ALICE_0001')
    _, events, _ = run_room([build_frame(member(user))],
                            profiles={'SEC_ALICE_0001': {'nickname': 'RealAlice', 'unique_id': 'alice01'}})
    assert events[0]['data']['unique_id'] == 'alice01'


def test_mystery_whose_profile_name_equals_display_is_regular(wl, run_room):
    user = make_user(display='dou987654', nickname='dou987654', sec_uid='SEC_DOU_00001')
    listener, events, _ = run_room([build_frame(member(user))],
                                   profiles={'SEC_DOU_00001': {'nickname': 'dou987654'}})
    [ev] = events
    assert ev['data']['is_regular'] is True and listener.mystery_count == 0
    assert db(wl, 'SELECT is_regular FROM mystery_records') == [{'is_regular': 1}]


# ---------- 弹幕 ----------

def test_regular_chat_recorded_when_record_all(wl, run_room):
    user = make_user(display='张三', nickname='张三', sec_uid='SEC_ZHANG_001')
    _, events, _ = run_room([build_frame(chat(user, '你好'))])
    [ev] = events
    assert ev['type'] == 'mystery_chat' and ev['data']['content'] == '你好' and ev['data']['is_regular'] is True
    assert db(wl, 'SELECT type, content FROM interaction_log') == [{'type': 'chat', 'content': '你好'}]
    assert db(wl, 'SELECT is_regular, chat_count FROM mystery_records') == [{'is_regular': 1, 'chat_count': 1}]


def test_regular_chat_ignored_when_record_all_off(wl, run_room, monkeypatch):
    monkeypatch.setattr(wl, '_record_all_enabled', False)
    user = make_user(display='张三', nickname='张三', sec_uid='SEC_ZHANG_001')
    _, events, _ = run_room([build_frame(chat(user, '你好'))])
    assert events == []
    assert db(wl, 'SELECT * FROM interaction_log') == []


def test_mystery_chat(wl, run_room):
    user = make_user(display='神秘人777', nickname='神秘人777', sec_uid='SEC_M7_000001', mystery_man=2)
    _, events, _ = run_room([build_frame(chat(user, '在吗'))],
                            profiles={'SEC_M7_000001': {'nickname': 'Seven'}})
    [ev] = events
    assert ev['data']['real_name'] == 'Seven' and ev['data']['is_regular'] is False
    assert db(wl, 'SELECT display, content FROM interaction_log') == [{'display': '神秘人777', 'content': '在吗'}]


# ---------- 礼物 ----------

def test_mystery_gift(wl, run_room):
    user = make_user(display='神秘人888', nickname='神秘人888', sec_uid='SEC_M8_000001')
    _, events, _ = run_room([build_frame(gift(user, '小心心', 10))],
                            profiles={'SEC_M8_000001': {'nickname': 'Eight'}})
    [ev] = events
    assert ev['type'] == 'mystery_gift'
    assert (ev['data']['gift_name'], ev['data']['count'], ev['data']['real_name']) == ('小心心', 10, 'Eight')
    assert db(wl, 'SELECT type, content, gift_count FROM interaction_log') == [
        {'type': 'gift', 'content': '小心心', 'gift_count': 10}]
    assert db(wl, 'SELECT gift_count, is_regular FROM mystery_records') == [{'gift_count': 1, 'is_regular': 0}]


# ---------- 私密 / 匿名直播间 ----------

def test_member_without_sec_uid_marks_room_private(wl, run_room):
    anon = make_user(display='神秘人999', nickname='神秘人999')
    later = make_user(display='张三', nickname='张三', sec_uid='SEC_ZHANG_001')
    listener, events, _ = run_room([build_frame(member(anon), chat(later, 'hi'))])
    assert listener.is_private is True
    assert events == []                              # 无身份不推送；之后的弹幕被跳过
    assert db(wl, 'SELECT * FROM mystery_records') == []


def test_private_gift_caches_real_name_for_display(wl, run_room):
    anon = make_user(display='神秘人999', nickname='神秘人999')
    giver = make_user(display='神秘人999', nickname='神秘人999', sec_uid='SEC_GIVER_001')
    listener, events, _ = run_room([build_frame(member(anon), gift(giver, '玫瑰', 1))],
                                   profiles={'SEC_GIVER_001': {'nickname': 'Giver'}})
    assert wl._private_name_cache[f'{ROOM}:神秘人999'] == {'nickname': 'Giver', 'sec_uid': 'SEC_GIVER_001'}
    [ev] = events
    assert ev['type'] == 'mystery_gift' and ev['data']['real_name'] == 'Giver'
    # 私密房：不进历史页（is_regular=1）、不记马甲
    assert db(wl, 'SELECT is_regular FROM mystery_records') == [{'is_regular': 1}]
    assert db(wl, 'SELECT * FROM display_names') == []


def test_anonymous_room_detected_from_page(wl, run_room):
    user = make_user(display='神秘人12345', sec_uid='SEC_ALICE_0001')
    listener, events, _ = run_room([build_frame(member(user))],
                                   page_html='{"live_room_mode":1,"x":2}')
    assert listener.is_private is True
    assert [e['type'] for e in events] == ['room_anonymous']   # 进入事件被跳过


def test_malformed_frame_is_ignored(run_room):
    _, events, _ = run_room([b'not a protobuf frame'])
    assert events == []
