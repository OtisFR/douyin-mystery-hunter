"""纯工具函数：cookie 解析、URL 拼接、Params、DouyinAuth"""
from builder.auth import DouyinAuth
from builder.params import Params
from utils.dy_util import trans_cookies, splice_url, generate_msToken, generate_fake_webid


def test_trans_cookies_keeps_equals_in_value():
    assert trans_cookies('a=1; b=x=y; c=') == {'a': '1', 'b': 'x=y', 'c': ''}


def test_splice_url_quotes_values_and_blanks_none():
    assert splice_url({'k': 'a b', 'n': None, 'z': 1}) == 'k=a%20b&n=&z=1'


def test_generate_msToken_and_fake_webid_shape():
    assert len(generate_msToken()) == 107
    webid = generate_fake_webid()
    assert len(webid) == 19 and webid.isdigit()


def test_params_chain_and_to_string():
    p = Params().add_param('a', '1').add_param('b', '2')
    assert p.get() == {'a': '1', 'b': '2'}
    assert p.toString() == 'a=1&b=2'


def test_params_sort_puts_known_keys_first():
    p = Params().update_params({'zzz': '1', 'msToken': 't', 'aid': '6383', 'device_platform': 'webapp'})
    p.sort()
    assert list(p.get()) == ['device_platform', 'aid', 'msToken', 'zzz']


def test_auth_keeps_msToken_from_cookie():
    auth = DouyinAuth()
    auth.perepare_auth('ttwid=t; msToken=mine')
    assert auth.msToken == 'mine'
    assert auth.cookie == {'ttwid': 't', 'msToken': 'mine'}
    assert auth.cookie_str == 'ttwid=t; msToken=mine'


def test_auth_generates_msToken_when_missing():
    auth = DouyinAuth()
    auth.perepare_auth('ttwid=t')
    assert len(auth.msToken) == 107
    assert auth.cookie['msToken'] == auth.msToken
    assert auth.cookie_str.endswith(f'msToken={auth.msToken}')
