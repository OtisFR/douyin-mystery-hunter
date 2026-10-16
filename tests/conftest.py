"""pytest 公共配置：在导入 web_listener 前隔离数据库与 cookie，避免触碰线上数据。"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 必须在 import web_listener 之前设置：模块导入时就会 load_env() + _init_db()
os.environ['MYSTERY_DB_PATH'] = os.path.join(tempfile.mkdtemp(prefix='mystery-test-'), 'import.db')
os.environ['DY_COOKIES'] = 'ttwid=test_ttwid; uid=10001'
os.environ['DY_LIVE_COOKIES'] = 'ttwid=test_live_ttwid; uid=20002'

import pytest

import web_listener


@pytest.fixture
def wl(tmp_path, monkeypatch):
    """每个测试一个全新的 SQLite 文件 + 清空的全局状态"""
    monkeypatch.setattr(web_listener, '_DB_PATH', str(tmp_path / 'test.db'))
    web_listener._init_db()
    monkeypatch.setattr(web_listener, '_user_info_cache', {})
    monkeypatch.setattr(web_listener, '_level_cache', {})
    monkeypatch.setattr(web_listener, '_private_name_cache', {})
    monkeypatch.setattr(web_listener, '_sse_generations', {})
    monkeypatch.setattr(web_listener, '_record_all_enabled', True)
    monkeypatch.setattr(web_listener, '_last_api_call', 0)
    monkeypatch.setattr(web_listener, 'listeners', {})
    yield web_listener
    for listener in web_listener.listeners.values():
        listener.running = False


@pytest.fixture
def client(wl):
    wl.app.config['TESTING'] = True
    return wl.app.test_client()


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(web_listener.time, 'sleep', lambda s: None)
