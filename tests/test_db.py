"""SQLite 持久化：保存/合并/加载/公屏事件"""
import json
import sqlite3

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


def test_save_preserves_first_seen(wl):
    wl._save_mystery_record('r1', 'SEC_A', '神秘人001', '', {}, 'enter', timestamp=1000)
    wl._save_mystery_record('r2', 'SEC_A', 'dou123456', '', {}, 'chat', timestamp=2000)
    [r] = records(wl)
    assert (r['first_seen'], r['last_seen']) == (1000, 2000)


def test_save_preserves_first_seen_in_private_room(wl):
    # 私密房不写 display_names，只能靠 mystery_records 自身的 first_seen
    wl._save_mystery_record('r1', 'SEC_P', '神秘人001', '', {}, 'gift', timestamp=1000, is_private=True)
    wl._save_mystery_record('r1', 'SEC_P', '神秘人001', '', {}, 'gift', timestamp=2000, is_private=True)
    assert records(wl)[0]['first_seen'] == 1000


def test_save_repairs_reset_first_seen_from_display_names(wl):
    """旧版 bug 已把 first_seen 重置过的数据，下次保存时从 display_names 修正"""
    wl._save_mystery_record('r1', 'SEC_A', '神秘人001', '', {}, 'enter', timestamp=1000)
    conn = sqlite3.connect(wl._DB_PATH)
    conn.execute('UPDATE mystery_records SET first_seen = 1500')
    conn.commit()
    conn.close()
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


def test_load_history_by_nickname_ignores_records_without_nickname(wl):
    wl._save_mystery_record('r1', 'SEC_A', '神秘人001', 'Alice', {}, 'enter')   # 无 room_nickname
    wl._save_mystery_record('r2', 'SEC_B', '神秘人002', 'Bob', {}, 'enter', room_nickname='LuckyStar')
    assert [r['sec_uid'] for r in wl._load_history_by_nickname('LuckyStar')] == ['SEC_B']


def test_load_history_by_nickname_matches_extra_room_nickname_only(wl):
    # 只有 extra.room_nickname、nickname 列为空（旧数据）也能命中
    wl._save_mystery_record('r1', 'SEC_A', '神秘人001', 'Alice', {}, 'enter')
    conn = sqlite3.connect(wl._DB_PATH)
    conn.execute("""UPDATE mystery_records SET extra = '{"room_nickname": "LuckyStar"}'""")
    conn.commit()
    conn.close()
    assert [r['sec_uid'] for r in wl._load_history_by_nickname('lucky star')] == ['SEC_A']


def test_load_history_by_nickname_blank_query(wl):
    wl._save_mystery_record('r1', 'SEC_A', '神秘人001', 'Alice', {}, 'enter', room_nickname='LuckyStar')
    assert wl._load_history_by_nickname('   ') == []


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


def test_build_feed_events_returning_user_enter_uses_last_seen(wl):
    wl._save_mystery_record('r1', 'SEC_A', '神秘人001', 'Alice', {}, 'enter', timestamp=1000)
    for ts in range(2000, 2250):
        wl._save_interaction('r1', 'SEC_B', 'x', 'chat', 'spam', timestamp=ts)
    wl._save_mystery_record('r1', 'SEC_A', '神秘人001', 'Alice', {}, 'enter', timestamp=3000)
    events = wl._build_feed_events('r1')            # 默认 limit=200
    assert events[-1] == {'type': 'enter', 'display': '神秘人001', 'real_name': 'Alice', 'content': '',
                          'count': 2, 'timestamp': 3000, 'sec_uid': 'SEC_A'}


def test_build_feed_events_limit_keeps_latest_in_ascending_order(wl):
    for ts in range(1, 6):
        wl._save_interaction('r1', 'SEC_A', 'x', 'chat', str(ts), timestamp=ts)
    assert [e['content'] for e in wl._build_feed_events('r1', limit=2)] == ['4', '5']
