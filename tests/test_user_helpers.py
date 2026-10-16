"""神秘人识别、用户字段辅助、lookup_user、get_room_id_by_douyin_id"""
import pytest
import requests

import static.Live_pb2 as Live_pb2
from tests.helpers import FakeResp, make_user


# ---------- is_real_mystery_user ----------

@pytest.mark.parametrize('kwargs, expected', [
    ({'display': '神秘人12345'}, True),        # 经典匿名
    ({'display': '神秘人'}, False),            # 只有前缀不算
    ({'display': 'dou123'}, True),             # dou 匿名（display）
    ({'display': 'dou12'}, False),             # 长度不够
    ({'nickname': 'dou998877'}, True),         # dou 匿名（nickname）
    ({'display': '张三', 'mystery_man': 2}, True),   # 深度匿名
    ({'display': '张三', 'mystery_man': 1}, False),
    ({'display': '张三', 'nickname': '张三'}, False),
])
def test_is_real_mystery_user(wl, kwargs, expected):
    assert wl.is_real_mystery_user(make_user(**kwargs))[0] is expected


def test_is_real_mystery_user_returns_stripped_names(wl):
    result = wl.is_real_mystery_user(make_user(display=' 神秘人001 ', nickname=' 真名 ', mystery_man=3))
    assert result == (True, '神秘人001', '真名', 3)


# ---------- gender / id / badge ----------

@pytest.mark.parametrize('g, s', [(0, '未设置'), (1, '男'), (2, '女'), (9, '未知')])
def test_gender_str(wl, g, s):
    assert wl.gender_str(g) == s


def test_user_id_str_prefers_display_id(wl):
    assert wl.user_id_str(make_user(display_id='dy_abc', short_id=123)) == 'dy_abc'
    assert wl.user_id_str(make_user(short_id=123)) == '123'
    assert wl.user_id_str(make_user()) == ''        # 空串，调用方才能回退到 extra.unique_id


def test_get_badge_level_reads_and_caches(wl):
    user = make_user(sec_uid='SEC_BADGE_0001')
    user.badge_image_list.add(content=Live_pb2.Content(level=0))
    user.badge_image_list.add(content=Live_pb2.Content(level=8))
    assert wl.get_badge_level(user) == 8
    # 之后同一用户没带徽章 → 用缓存
    assert wl.get_badge_level(make_user(sec_uid='SEC_BADGE_0001')) == 8
    assert wl.get_badge_level(make_user(sec_uid='SEC_OTHER_0001')) == 0


# ---------- lookup_user ----------

PROFILE = {'status_code': 0, 'user': {
    'nickname': 'RealAlice', 'unique_id': '', 'short_id': 'short9',
    'ip_location': 'IP属地：浙江', 'follower_count': 12, 'signature': 'x' * 150}}


def test_lookup_user_success_is_cached(wl, monkeypatch, no_sleep):
    calls = []
    monkeypatch.setattr(wl.requests, 'get', lambda *a, **k: calls.append(k) or FakeResp(PROFILE))
    info = wl.lookup_user('SEC_ALICE_0001')
    assert info['nickname'] == 'RealAlice'
    assert info['unique_id'] == 'short9'          # unique_id 为空时回退 short_id
    assert len(info['signature']) == 100
    assert calls[0]['params']['sec_user_id'] == 'SEC_ALICE_0001'
    assert calls[0]['cookies'] == {'ttwid': 'test_live_ttwid'}
    # 第二次直接走缓存
    assert wl.lookup_user('SEC_ALICE_0001') is info
    assert len(calls) == 1


def test_lookup_user_rejects_short_sec_uid(wl, monkeypatch):
    monkeypatch.setattr(wl.requests, 'get', lambda *a, **k: pytest.fail('不应请求'))
    assert wl.lookup_user('') == {}
    assert wl.lookup_user('short') == {}


def test_lookup_user_failure_retries_and_does_not_cache(wl, monkeypatch, no_sleep):
    calls = []
    monkeypatch.setattr(wl.requests, 'get',
                        lambda *a, **k: calls.append(1) or FakeResp({'status_code': 8}))
    assert wl.lookup_user('SEC_FAIL_00001') == {}
    assert len(calls) == 3
    assert 'SEC_FAIL_00001' not in wl._user_info_cache


# ---------- get_room_id_by_douyin_id ----------

def test_resolve_douyin_id(wl, monkeypatch):
    responses = iter([
        FakeResp({'status_code': 0, 'user_info': {'sec_uid': 'SEC_HOST_0001', 'nickname': '主播A'}}),
        FakeResp({'status_code': 0, 'user': {'room_id': 7000001, 'live_status': 1}}),
    ])
    monkeypatch.setattr(wl.requests, 'get', lambda *a, **k: next(responses))
    assert wl.get_room_id_by_douyin_id('host_a') == {
        'success': True, 'nickname': '主播A', 'room_id': '7000001',
        'live_status': 1, 'sec_uid': 'SEC_HOST_0001'}


@pytest.mark.parametrize('fake_get', [
    lambda *a, **k: FakeResp({'status_code': 2053}),
    lambda *a, **k: (_ for _ in ()).throw(requests.ConnectionError('boom')),
])
def test_resolve_douyin_id_failure(wl, monkeypatch, fake_get):
    monkeypatch.setattr(wl.requests, 'get', fake_get)
    result = wl.get_room_id_by_douyin_id('not_exist')
    assert result['success'] is False and '查询失败' in result['error']


def test_resolve_short_link_to_live_room(wl, monkeypatch):
    seen = {}
    def fake_head(url, **k):
        seen['url'] = url
        return FakeResp(url='https://live.douyin.com/123456789?from=share')
    monkeypatch.setattr(wl.requests, 'head', fake_head)
    result = wl.get_room_id_by_douyin_id('看直播 https://v.douyin.com/AbC123/ 复制打开')
    assert seen['url'] == 'https://v.douyin.com/AbC123'
    assert result == {'success': True, 'room_id': '123456789', 'type': 'live', 'live_status': 1}


def test_resolve_short_link_to_user_page(wl, monkeypatch):
    monkeypatch.setattr(wl.requests, 'head', lambda *a, **k: FakeResp(
        url='https://www.iesdouyin.com/share/user/?sec_uid=SEC_HOST_0002&x=1'))
    monkeypatch.setattr(wl.requests, 'get', lambda *a, **k: FakeResp(
        {'status_code': 0, 'user': {'nickname': '主播B', 'room_id': 0, 'live_status': 0}}))
    assert wl.get_room_id_by_douyin_id('https://v.douyin.com/XyZ/') == {
        'success': True, 'nickname': '主播B', 'room_id': '0', 'live_status': 0, 'sec_uid': 'SEC_HOST_0002'}


def test_resolve_short_link_user_page_profile_failure(wl, monkeypatch):
    monkeypatch.setattr(wl.requests, 'head', lambda *a, **k: FakeResp(
        url='https://www.iesdouyin.com/share/user/?sec_uid=SEC_HOST_0003'))
    monkeypatch.setattr(wl.requests, 'get', lambda *a, **k: FakeResp({'status_code': 1}))
    assert wl.get_room_id_by_douyin_id('https://v.douyin.com/XyZ/') == {
        'success': True, 'sec_uid': 'SEC_HOST_0003', 'live_status': 0, 'room_id': '0', 'nickname': ''}


def test_resolve_unparseable_input(wl):
    result = wl.get_room_id_by_douyin_id('https://example.com/whatever')
    assert result == {'success': False, 'error': '无法解析链接或抖音号'}
