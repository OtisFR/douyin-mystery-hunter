"""SQLite 持久化：保存/合并/加载/公屏事件"""
import json
import time

import pytest

from tests.helpers import rows


def records(wl):
    return rows(wl._DB_PATH, 'SELECT * FROM mystery_records')


def displays(wl, sec_uid):
    return {r['display']: r for r in rows(
        wl._DB_PATH, 'SELECT * FROM display_names WHERE sec_uid=?', (sec_uid,))}


# ---------- _save_mystery_record ----------

def test_save_skips_user_without_sec_uid(wl):
    wl._save_mystery_record('r1', '', '神秘人001', '', {}, 'enter')
    assert records(wl) == []


def test_save_first_record(wl):
    wl._save_mystery_record('r1', 'SEC_A', '神秘人001', '', {'nickname': 'Alice'}, 'enter',
                            timestamp=1000, room_nickname='主播A')
    [r] = records(wl)
    assert r['real_name'] == 'Alice'                 # extra.nickname 补全 real_name
    assert r['last_room_id'] == 'r1' and r['seen_room_ids'] == 'r1'
    assert (r['enter_count'], r['chat_count'], r['gift_count']) == (1, 0, 0)
    assert r['nickname'] == '主播A'
    assert json.loads(r['extra']) == {'nickname': 'Alice', 'room_nickname': '主播A'}
    assert r['is_regular'] == 0 and r['last_seen'] == 1000
    assert displays(wl, 'SEC_A')['神秘人001']['seen_count'] == 1


def test_save_merges_same_sec_uid_across_rooms_and_displays(wl):
    wl._save_mystery_record('r2', 'SEC_A', '神秘人001', 'Alice', {'unique_id': 'alice01'}, 'enter', timestamp=1000)
    wl._save_mystery_record('r1', 'SEC_A', 'dou123456', '', {'ip_location': '浙江'}, 'chat', timestamp=2000)
    wl._save_mystery_record('r1', 'SEC_A', 'dou123456', '', {}, 'gift', timestamp=3000)
    [r] = records(wl)                                # 同一 sec_uid 只保留一行
    assert r['display'] == 'dou123456'
    assert r['real_name'] == 'Alice'                 # 旧的真实名不被马甲覆盖
    assert r['seen_room_ids'] == 'r1,r2' and r['last_room_id'] == 'r1'
    assert (r['enter_count'], r['chat_count'], r['gift_count']) == (1, 1, 1)
    assert json.loads(r['extra']) == {'unique_id': 'alice01', 'ip_location': '浙江'}
    assert r['last_seen'] == 3000
    d = displays(wl, 'SEC_A')
    assert d['神秘人001']['seen_count'] == 1
    assert d['dou123456']['seen_count'] == 2 and d['dou123456']['last_seen'] == 3000


def test_save_new_extra_values_win_over_old(wl):
    wl._save_mystery_record('r1', 'SEC_A', '神秘人001', '', {'ip_location': '浙江', 'follower_count': 1}, 'enter')
    wl._save_mystery_record('r1', 'SEC_A', '神秘人001', '', {'ip_location': '上海'}, 'enter')
    assert json.loads(records(wl)[0]['extra']) == {'ip_location': '上海', 'follower_count': 1}


def test_save_private_room_marks_regular_and_skips_display_names(wl):
    wl._save_mystery_record('r1', 'SEC_P', '神秘人001', 'Bob', {}, 'gift', is_private=True)
    assert records(wl)[0]['is_regular'] == 1
    assert displays(wl, 'SEC_P') == {}


@pytest.mark.xfail(strict=True, reason='BUG: 每次保存都先 DELETE 再 INSERT，first_seen 被重置为本次时间')
def test_save_preserves_first_seen(wl):
    wl._save_mystery_record('r1', 'SEC_A', '神秘人001', '', {}, 'enter', timestamp=1000)
    wl._save_mystery_record('r1', 'SEC_A', '神秘人001', '', {}, 'chat', timestamp=2000)
    assert records(wl)[0]['first_seen'] == 1000


# ---------- _save_interaction ----------

def test_save_interaction(wl):
    wl._save_interaction('', 'SEC_A', 'x', 'chat', 'hi')
    wl._save_interaction('r1', '', 'x', 'chat', 'hi')
    wl._save_interaction('r1', 'SEC_A', '神秘人001', 'gift', '小心心', gift_count=5, timestamp=1234)
    assert rows(wl._DB_PATH, 'SELECT room_id, sec_uid, display, type, content, gift_count, timestamp FROM interaction_log') == [
        {'room_id': 'r1', 'sec_uid': 'SEC_A', 'display': '神秘人001', 'type': 'gift',
         'content': '小心心', 'gift_count': 5, 'timestamp': 1234}]


# ---------- _load_room_history ----------

def test_load_room_history_filters_by_exact_room_and_mystery(wl):
    wl._save_mystery_record('123', 'SEC_A', '神秘人001', 'Alice', {}, 'enter', timestamp=1000, room_nickname='主播A')
    wl._save_mystery_record('123', 'SEC_A', '神秘人002', '', {}, 'enter', timestamp=2000)
    wl._save_mystery_record('123', 'SEC_B', '神秘人003', 'Bob', {}, 'enter', timestamp=3000)
    wl._save_mystery_record('12', 'SEC_C', '神秘人004', 'Carol', {}, 'enter', timestamp=4000)      # 子串房间号不能命中
    wl._save_mystery_record('123', 'SEC_D', '路人', 'Dan', {}, 'enter', timestamp=5000, is_regular=1)

    result = wl._load_room_history('123')
    assert [r['sec_uid'] for r in result] == ['SEC_B', 'SEC_A']   # last_seen 倒序
    alice = result[1]
    assert [d['display'] for d in alice['displays']] == ['神秘人002', '神秘人001']
    assert alice['extra'] == {'room_nickname': '主播A'}
    assert alice['room_nickname'] == '主播A'
    assert alice['is_current'] is False


def test_load_room_history_empty(wl):
    assert wl._load_room_history('nope') == []


# ---------- _load_history_by_nickname ----------

def test_load_history_by_nickname_case_and_space_insensitive(wl):
    wl._save_mystery_record('r1', 'SEC_A', '神秘人001', 'Alice', {}, 'enter', room_nickname='Lucky Star')
    wl._save_mystery_record('r2', 'SEC_B', '神秘人002', 'Bob', {}, 'enter', room_nickname='其他主播')
    wl._save_mystery_record('r1', 'SEC_C', '路人', 'Carol', {}, 'enter', room_nickname='Lucky Star', is_regular=1)
    assert [r['sec_uid'] for r in wl._load_history_by_nickname('luckystar')] == ['SEC_A']
    assert wl._load_history_by_nickname('不存在的主播') == []


@pytest.mark.xfail(strict=True, reason="BUG: 昵称为空的记录 `'' in clean_name` 恒为 True，会匹配任何查询")
def test_load_history_by_nickname_ignores_records_without_nickname(wl):
    wl._save_mystery_record('r1', 'SEC_A', '神秘人001', 'Alice', {}, 'enter')   # 无 room_nickname
    assert wl._load_history_by_nickname('LuckyStar') == []


# ---------- _build_feed_events ----------

def test_build_feed_events_merges_and_orders(wl):
    wl._save_mystery_record('r1', 'SEC_A', '神秘人001', 'Alice', {}, 'enter', timestamp=100)
    wl._save_mystery_record('r2', 'SEC_B', '神秘人002', 'Bob', {}, 'enter', timestamp=150)    # 其他房间
    wl._save_mystery_record('r1', 'SEC_C', '路人', '', {}, 'chat', timestamp=50)               # enter_count=0
    wl._save_interaction('r1', 'SEC_A', '神秘人001', 'chat', '你好', timestamp=300)
    wl._save_interaction('r1', 'SEC_A', '神秘人001', 'gift', '小心心', gift_count=3, timestamp=200)
    wl._save_interaction('r2', 'SEC_B', '神秘人002', 'chat', 'other', timestamp=250)

    events = wl._build_feed_events('r1')
    assert [(e['type'], e['timestamp']) for e in events] == [('enter', 100), ('gift', 200), ('chat', 300)]
    assert events[0]['real_name'] == 'Alice' and events[0]['count'] == 1
    assert events[1]['content'] == '小心心' and events[1]['count'] == 3


def test_build_feed_events_limit_keeps_latest_in_ascending_order(wl):
    for ts in range(1, 6):
        wl._save_interaction('r1', 'SEC_A', 'x', 'chat', str(ts), timestamp=ts)
    assert [e['content'] for e in wl._build_feed_events('r1', limit=2)] == ['4', '5']
