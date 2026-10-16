"""Flask 路由：监听管理、历史、搜索记录、公屏、SSE"""
import json
import sqlite3
import time

import pytest

from tests.helpers import drain


class FakeWS:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


@pytest.fixture
def no_thread(wl, monkeypatch):
    """RoomListener.start 不开线程，只标记 running"""
    def start(self):
        self.running = True
        return self
    monkeypatch.setattr(wl.RoomListener, 'start', start)


def add_listener(wl, room_id, nickname='', running=True):
    listener = wl.RoomListener(room_id, nickname)
    listener.running = running
    wl.listeners[room_id] = listener
    return listener


def sse_events(body):
    return [json.loads(line[len('data: '):]) for line in body.split('\n') if line.startswith('data: ')]


def test_index_renders(client):
    resp = client.get('/')
    assert resp.status_code == 200 and b'<html' in resp.data.lower()


# ---------- /api/resolve ----------

def test_resolve_requires_input(client):
    assert client.post('/api/resolve', json={}).get_json() == {'success': False, 'error': '请输入抖音号或链接'}


def test_resolve_strips_input(client, wl, monkeypatch):
    monkeypatch.setattr(wl, 'get_room_id_by_douyin_id', lambda s: {'success': True, 'got': s})
    assert client.post('/api/resolve', json={'input': '  host_a '}).get_json() == {'success': True, 'got': 'host_a'}


# ---------- /api/start /stop /stop_all /status ----------

def test_start_requires_room_id(client):
    assert client.post('/api/start', json={}).get_json()['error'] == '缺少room_id'


def test_start_replays_room_history(client, wl, no_thread):
    wl._save_mystery_record('r1', 'SEC_A', '神秘人001', 'Alice', {'unique_id': 'alice01'}, 'enter', timestamp=100)
    wl._save_mystery_record('r1', 'SEC_B', '神秘人002', '', {}, 'enter', timestamp=200)
    wl._save_mystery_record('r2', 'SEC_C', '神秘人003', 'Carol', {}, 'enter', timestamp=300)
    wl._save_mystery_record('r1', 'SEC_D', '路人', 'Dan', {}, 'enter', is_regular=1)

    resp = client.post('/api/start', json={'room_id': 'r1', 'nickname': '主播A'}).get_json()
    assert resp == {'success': True, 'room_id': 'r1', 'active': 1, 'max': 3}

    listener = wl.listeners['r1']
    assert listener.running and listener.mystery_count == 2
    events = drain(listener.events)
    assert [e['type'] for e in events] == ['mystery_enter', 'mystery_enter']
    first = events[0]['data']
    assert first['sec_uid'] == 'SEC_A' and first['real_name'] == 'Alice'
    assert first['unique_id'] == 'alice01' and first['mystery_seq'] == 1
    assert first['room_nickname'] == '主播A'
    assert events[1]['data']['real_name'] == '神秘人002'      # 无真实名时回退 display


def test_start_replay_filters_room_before_limit(client, wl, no_thread):
    # 其他房间 >100 条且 first_seen 更早：旧实现先全局 LIMIT 100 再过滤，目标房间会被挤掉
    for i in range(120):
        wl._save_mystery_record('r2', f'SEC_X{i}', f'神秘人X{i}', '', {}, 'enter', timestamp=i + 1)
    wl._save_mystery_record('r12', 'SEC_SUB', '神秘人S', '', {}, 'enter', timestamp=500)   # 'r1' 不能子串命中 'r12'
    wl._save_mystery_record('r2', 'SEC_B', '神秘人B', '', {}, 'enter', timestamp=1001)
    wl._save_mystery_record('r1', 'SEC_B', '神秘人B', '', {}, 'enter', timestamp=1002)    # 跨房间用户
    wl._save_mystery_record('r1', 'SEC_A', '神秘人A', '', {}, 'enter', timestamp=1000)
    wl._save_mystery_record('r1', 'SEC_C', '神秘人C', '', {}, 'enter', timestamp=1003)

    client.post('/api/start', json={'room_id': 'r1'})
    listener = wl.listeners['r1']
    data = [e['data'] for e in drain(listener.events)]
    assert [d['sec_uid'] for d in data] == ['SEC_A', 'SEC_B', 'SEC_C']   # 按 first_seen 升序
    assert [d['mystery_seq'] for d in data] == [1, 2, 3]
    assert listener.mystery_count == 3


def test_start_replay_keeps_most_recent_100(client, wl, no_thread):
    for i in range(102):
        wl._save_mystery_record('r1', f'SEC_{i}', f'神秘人{i}', '', {}, 'enter', timestamp=i + 1)
    wl._save_mystery_record('r1', 'SEC_0', '神秘人0', '', {}, 'enter', timestamp=500)    # 老用户回访：first_seen 不变

    client.post('/api/start', json={'room_id': 'r1'})
    listener = wl.listeners['r1']
    uids = [e['data']['sec_uid'] for e in drain(listener.events)]
    # 按 last_seen 保留最近活跃的 100 人，输出仍按 first_seen 升序
    assert uids == ['SEC_0'] + [f'SEC_{i}' for i in range(3, 102)]
    assert listener.mystery_count == 100


def test_start_already_running(client, wl, no_thread):
    add_listener(wl, 'r1')
    assert client.post('/api/start', json={'room_id': 'r1'}).get_json() == {
        'success': True, 'room_id': 'r1', 'already': True}


def test_start_enforces_max_rooms(client, wl, no_thread):
    for rid in ('a', 'b', 'c'):
        add_listener(wl, rid)
    add_listener(wl, 'stopped', running=False)               # 已停止的不计数
    resp = client.post('/api/start', json={'room_id': 'd'}).get_json()
    assert resp['success'] is False and '最多同时监听3个' in resp['error']
    assert 'd' not in wl.listeners


def test_stop(client, wl):
    listener = add_listener(wl, 'r1')
    listener.ws = FakeWS()
    assert client.post('/api/stop', json={'room_id': 'r1'}).get_json() == {'success': True, 'room_id': 'r1'}
    assert listener.running is False and listener.ws.closed
    assert 'r1' not in wl.listeners
    assert client.post('/api/stop', json={'room_id': 'r1'}).get_json()['success'] is False


def test_stop_all(client, wl):
    a, b = add_listener(wl, 'a'), add_listener(wl, 'b')
    assert client.post('/api/stop_all').get_json() == {'success': True}
    assert wl.listeners == {} and not a.running and not b.running


def test_status(client, wl):
    l1 = add_listener(wl, 'r1', '主播A')
    l1.mystery_count = 3
    l1.recent_mysteries = [{'sec_uid': 'A'}, {'sec_uid': 'A'}, {'sec_uid': 'B'}, {'sec_uid': ''}]
    add_listener(wl, 'r2', running=False)
    assert client.get('/api/status').get_json() == {
        'active': [{'room_id': 'r1', 'nickname': '主播A', 'mystery_count': 3, 'unique_count': 2}],
        'count': 1, 'max': 3}


# ---------- 录制开关 / 缓存 ----------

def test_toggle_record_all(client, wl):
    assert client.post('/api/toggle_record_all', json={'enabled': False}).get_json() == {
        'success': True, 'record_all': False}
    assert wl._record_all_enabled is False
    assert client.get('/api/record_all_status').get_json() == {'record_all': False}


def test_real_names(client, wl):
    wl._user_info_cache['SEC_A'] = {'nickname': 'Alice'}
    wl._private_name_cache['r1:神秘人001'] = {'nickname': 'Bob'}
    assert client.get('/api/real_names').get_json() == {
        'success': True, 'users': {'SEC_A': {'nickname': 'Alice'}},
        'private_names': {'r1:神秘人001': {'nickname': 'Bob'}}, 'count': 1}


# ---------- /api/history_rooms ----------

def test_history_rooms(client, wl):
    wl._save_mystery_record('r1', 'SEC_A', '神秘人001', '', {}, 'enter', timestamp=100, room_nickname='主播A')
    wl._save_mystery_record('r2', 'SEC_A', '神秘人001', '', {}, 'enter', timestamp=300)
    wl._save_mystery_record('r1', 'SEC_B', '神秘人002', '', {}, 'enter', timestamp=200)
    wl._save_mystery_record('r3', 'SEC_C', '路人', '', {}, 'enter', timestamp=900, is_regular=1)

    rooms = client.get('/api/history_rooms').get_json()['rooms']
    # SEC_A 同时出现在 r1/r2，两个房间 last_seen 都取它的 300
    assert sorted((r['room_id'], r['mystery_count'], r['last_seen']) for r in rooms) == [('r1', 2, 300), ('r2', 1, 300)]
    assert all(r['nickname'] == '主播A' for r in rooms)    # SEC_A 的 extra 带 room_nickname


# ---------- /api/search_history ----------

def test_search_history_crud(client, wl):
    assert client.post('/api/search_history/save', json={'input': ' '}).get_json()['success'] is False
    assert client.post('/api/search_history/save',
                       json={'input': ' host_a ', 'nickname': '主播A', 'room_id': 'r1'}).get_json() == {'success': True}
    client.post('/api/search_history/save', json={'input': 'host_a', 'nickname': '主播A改', 'room_id': 'r1'})
    data = client.get('/api/search_history/list').get_json()['data']
    assert len(data) == 1 and data[0]['input_text'] == 'host_a' and data[0]['nickname'] == '主播A改'

    assert client.post('/api/search_history/delete', json={}).get_json()['success'] is False
    assert client.post('/api/search_history/delete', json={'input': 'host_a'}).get_json() == {'success': True}
    assert client.get('/api/search_history/list').get_json()['data'] == []


def test_search_history_list_newest_first_limit_20(client, wl):
    conn = sqlite3.connect(wl._DB_PATH)
    conn.executemany('INSERT INTO room_search_history VALUES (?, ?, ?, ?)',
                     [(f'in{i}', '', '', i) for i in range(25)])
    conn.commit()
    conn.close()
    data = client.get('/api/search_history/list').get_json()['data']
    assert len(data) == 20 and data[0]['input_text'] == 'in24' and data[-1]['input_text'] == 'in5'


# ---------- 历史页：马甲有效性标记 ----------

def seed_display_history(wl, room='r1'):
    now = int(time.time())
    old = now - 3 * 86400
    # SEC_A：今天出现的神秘人马甲 + 今天两个不同 dou → 都有效
    wl._save_mystery_record(room, 'SEC_A', '神秘人OLD', '', {}, 'enter', timestamp=old)
    wl._save_mystery_record(room, 'SEC_A', '神秘人NEW', '', {}, 'enter', timestamp=now)
    wl._save_mystery_record(room, 'SEC_A', 'dou111111', '', {}, 'enter', timestamp=now)
    wl._save_mystery_record(room, 'SEC_A', 'dou222222', '', {'nickname': 'Alice', 'room_nickname': '主播A'},
                            'enter', timestamp=now)
    # SEC_B：只有很久以前的神秘人马甲 + 一个其他昵称 → 失效，其他昵称被丢弃
    wl._save_mystery_record(room, 'SEC_B', '神秘人BBB', 'Bob', {}, 'enter', timestamp=old)
    wl._save_mystery_record(room, 'SEC_B', '普通昵称', 'Bob', {}, 'enter', timestamp=old + 1)
    # SEC_C：今天只有一个 dou → 仅供参考
    wl._save_mystery_record(room, 'SEC_C', 'dou333333', 'Carol', {}, 'enter', timestamp=now - 1)
    return now, old


def by_sec(records):
    return {r['sec_uid']: r for r in records}


def check_display_marks(recs):
    a, b, c = recs['SEC_A'], recs['SEC_B'], recs['SEC_C']
    # 每类只留最新一个：神秘人NEW + 其中一个 dou（同一时间戳）
    names = sorted(d['display'] for d in a['displays'])
    assert len(names) == 2 and names[0].startswith('dou') and names[1] == '神秘人NEW'
    assert all(d['is_current'] for d in a['displays']) and a['is_current'] is True
    assert a['real_name'] == 'Alice'
    assert [d['display'] for d in b['displays']] == ['神秘人BBB']
    assert b['displays'][0]['is_current'] is False and b['is_current'] is False
    assert [d['display'] for d in c['displays']] == ['dou333333']
    assert c['is_current'] is False


def test_history_all_requires_room_id(client):
    assert client.get('/api/history_all').get_json()['success'] is False


def test_history_all_marks_displays(client, wl):
    seed_display_history(wl)
    resp = client.get('/api/history_all?room_id=r1').get_json()
    assert resp['count'] == 3
    recs = by_sec(resp['records'])
    check_display_marks(recs)
    assert recs['SEC_A']['room_nickname'] == '主播A' and recs['SEC_A']['nickname'] == '主播A'


def test_history_by_nickname(client, wl):
    assert client.get('/api/history_by_nickname').get_json()['success'] is False
    wl._save_mystery_record('r9', 'SEC_Z', '神秘人ZZZ', 'Zed', {}, 'enter', room_nickname='主播A')
    wl._save_mystery_record('r8', 'SEC_Y', '神秘人YYY', 'Yan', {}, 'enter', room_nickname='主播B')
    resp = client.get('/api/history_by_nickname?name=主播A').get_json()
    assert [r['sec_uid'] for r in resp['records']] == ['SEC_Z']
    assert resp['records'][0]['displays'][0]['is_current'] is True


def test_history_all_all(client, wl):
    seed_display_history(wl, room='r1')
    wl._save_mystery_record('r5', 'SEC_R', '路人', 'R', {}, 'enter', is_regular=1)
    resp = client.get('/api/history_all_all').get_json()
    assert resp['count'] == 3
    check_display_marks(by_sec(resp['records']))


def test_history_all_all_falls_back_to_record_display(client, wl):
    wl._save_mystery_record('r1', 'SEC_P', '神秘人PPP', 'P', {}, 'enter')
    conn = sqlite3.connect(wl._DB_PATH)
    conn.execute('DELETE FROM display_names')
    conn.commit()
    conn.close()
    [rec] = client.get('/api/history_all_all').get_json()['records']
    assert [d['display'] for d in rec['displays']] == ['神秘人PPP'] and rec['is_current'] is True


# ---------- /api/history/<room_id> ----------

def test_history_of_live_listener(client, wl):
    assert client.get('/api/history/r1').get_json()['success'] is False
    listener = add_listener(wl, 'r1')
    listener.mystery_count = 60
    listener.recent_mysteries = [{'i': i} for i in range(60)]
    resp = client.get('/api/history/r1').get_json()
    assert resp['mystery_count'] == 60 and len(resp['history']) == 50 and resp['history'][0] == {'i': 10}


# ---------- /api/all_records/<room_id> ----------

def test_all_records(client, wl):
    now = int(time.time())
    wl._save_mystery_record('r1', 'SEC_A', '神秘人001', 'Alice', {}, 'enter', timestamp=now - 100)
    wl._save_mystery_record('r1', 'SEC_A', '神秘人002', 'Alice', {}, 'enter', timestamp=now - 50)
    wl._save_mystery_record('r1', 'SEC_A', '阿丽', 'Alice', {'room_nickname': '主播A'}, 'chat', timestamp=now)
    wl._save_mystery_record('r1', 'SEC_R', '路人', 'R', {}, 'enter', timestamp=now - 10, is_regular=1)
    wl._save_mystery_record('r1', 'SEC_OLD', '神秘人OLD', 'O', {}, 'enter', timestamp=now - 5 * 3600)
    wl._save_mystery_record('r2', 'SEC_X', '神秘人X', 'X', {}, 'enter', timestamp=now)

    recs = client.get('/api/all_records/r1').get_json()['records']
    assert [r['sec_uid'] for r in recs] == ['SEC_A', 'SEC_R', 'SEC_OLD']
    a = recs[0]
    assert [d['display'] for d in a['displays']] == ['神秘人002', '阿丽']   # 每类只保留最新
    assert a['display'] == '阿丽'
    assert a['room_id'] == 'r1' and a['room_nickname'] == '主播A'

    recent = client.get('/api/all_records/r1?hours=1').get_json()['records']
    assert [r['sec_uid'] for r in recent] == ['SEC_A', 'SEC_R']


# ---------- 互动 / 公屏 / 表情 ----------

def test_interactions(client, wl):
    wl._save_interaction('r1', 'SEC_A', 'x', 'chat', 'first', timestamp=1)
    wl._save_interaction('r1', 'SEC_A', 'x', 'gift', '玫瑰', gift_count=2, timestamp=2)
    wl._save_interaction('r2', 'SEC_A', 'x', 'chat', 'other room', timestamp=3)
    assert client.get('/api/interactions/r1/SEC_A').get_json()['interactions'] == [
        {'type': 'gift', 'content': '玫瑰', 'gift_count': 2, 'timestamp': 2},
        {'type': 'chat', 'content': 'first', 'gift_count': 1, 'timestamp': 1}]


def test_feed(client, wl):
    for ts in range(1, 4):
        wl._save_interaction('r1', 'SEC_A', 'x', 'chat', str(ts), timestamp=ts)
    resp = client.get('/api/feed/r1?limit=2').get_json()
    assert resp['count'] == 2 and [e['content'] for e in resp['events']] == ['2', '3']


def test_emoji_map(client):
    resp = client.get('/api/emoji_map').get_json()
    assert resp['success'] is True and isinstance(resp['map'], dict) and resp['map']


# ---------- /stream/<room_id> (SSE) ----------

def test_stream_without_listener(client):
    events = sse_events(client.get('/stream/r1').get_data(as_text=True))
    assert events == [{'type': 'error', 'data': {'error': '未找到监听器'}}]


def test_stream_sends_init_then_drains_queue(client, wl):
    wl._save_interaction('r1', 'SEC_A', '神秘人001', 'chat', 'hello', timestamp=1)
    listener = add_listener(wl, 'r1', '主播A', running=False)
    listener.mystery_count = 1
    listener.recent_mysteries = [{'sec_uid': 'SEC_A'}]
    listener.send_event('mystery_chat', {'content': 'live'})

    resp = client.get('/stream/r1')
    assert resp.mimetype == 'text/event-stream'
    init, ev = sse_events(resp.get_data(as_text=True))
    assert init['type'] == 'init'
    assert init['data']['nickname'] == '主播A' and init['data']['is_anonymous'] is False
    assert init['data']['history'] == [{'sec_uid': 'SEC_A'}]
    assert [e['content'] for e in init['data']['feed']] == ['hello']
    assert ev['type'] == 'mystery_chat' and ev['data'] == {'content': 'live'}


def test_stream_increments_generation(client, wl):
    client.get('/stream/r1').get_data()
    client.get('/stream/r1').get_data()
    assert wl._sse_generations['r1'] == 2
