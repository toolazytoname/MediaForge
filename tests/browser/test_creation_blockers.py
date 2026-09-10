"""Opt-in browser regressions against a running UI; external writes are intercepted."""
import os
import pytest

BASE = os.environ.get('MEDIAFORGE_BROWSER_URL')
pytestmark = pytest.mark.skipif(not BASE, reason='set MEDIAFORGE_BROWSER_URL for browser verification')

@pytest.fixture
def page():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=['--no-sandbox'])
        page = browser.new_page()
        yield page
        browser.close()

@pytest.mark.parametrize('status,code', [(404, 'account_not_bound'), (500, 'server_error')])
def test_binding_error_keeps_editor(page, status, code):
    page.route('**/account-binding', lambda r: r.fulfill(status=status, json={'detail': {'error': {'code': code, 'message': 'test binding response'}}}))
    page.goto(BASE + '/projects/prj_cfc95b8c')
    page.get_by_text('确认访谈', exact=True).wait_for()
    page.get_by_text('保存为新版本', exact=True).wait_for()
    if status == 500:
        page.get_by_role('button', name='重试读取账号').wait_for()
    else:
        page.get_by_text('请选择公众号账号，绑定前仍可写稿和配图。', exact=True).wait_for()


def test_idea_only_creation(page):
    requests = []
    def create(route):
        requests.append(route.request.post_data_json)
        route.fulfill(status=400, json={'detail': {'error': {'code': 'test_stop', 'message': 'no real write'}}})
    page.route('**/api/v1/projects', create)
    page.goto(BASE + '/projects/new')
    page.get_by_role('button', name='创建项目并进入工作台').click()
    page.get_by_text('请先写下一句想法或材料。', exact=True).wait_for()
    assert not requests
    page.get_by_placeholder('一句想法、链接，或一段你已经写下来的材料').fill('浏览器验收：事实核对比快速起草更重要')
    page.get_by_role('button', name='创建项目并进入工作台').click()
    page.get_by_text('test_stop: no real write', exact=True).wait_for()
    assert len(requests) == 1
    assert requests[0]['title'] == '浏览器验收：事实核对比快速起草更重要'
    assert requests[0]['audience'] == '待确认'

@pytest.mark.parametrize('width', [390, 768, 1440])
def test_sidebar_buttons_stay_inside_sidebar(page, width):
    page.set_viewport_size({'width': width, 'height': 844})
    page.goto(BASE + '/')
    side = page.locator('.app-sider').bounding_box()
    for name in ['开始创作', '设置']:
        button = page.locator('.sidebar-actions').get_by_role('button', name=name, exact=True)
        box = button.bounding_box()
        assert box['x'] >= side['x']
        assert box['x'] + box['width'] <= side['x'] + side['width'] + 1


def test_failed_save_keeps_unsaved_body(page):
    page.goto(BASE + '/projects/prj_cfc95b8c')
    page.get_by_text('请选择公众号账号，绑定前仍可写稿和配图。', exact=True).wait_for()
    page.route('**/master', lambda r: r.fulfill(status=500, json={'detail': {'error': {'code': 'save_failed', 'message': 'test'}}}) if r.request.method == 'PUT' else r.continue_())
    body = page.get_by_placeholder('从空白开始，或把已有的想法写下来。')
    body.fill('验收中的未保存正文，不应因为请求失败消失。')
    page.get_by_role('button', name='保存为新版本', exact=True).click()
    page.get_by_text('save_failed: test', exact=True).wait_for()
    assert body.input_value() == '验收中的未保存正文，不应因为请求失败消失。'


def test_binding_failure_retains_confirmed_account(page):
    page.route('**/account-profiles', lambda r: r.fulfill(json={'items': [
        {'id': 'acc_testA', 'display_name': '测试公众号 A', 'platform': 'wechat_mp'},
        {'id': 'acc_testB', 'display_name': '测试公众号 B', 'platform': 'wechat_mp'}]}))
    page.route('**/account-binding', lambda r: r.fulfill(status=500, json={'detail': {'error': {'code': 'save_failed', 'message': 'test'}}}) if r.request.method == 'PUT' else r.fulfill(json={'items': [{'platform': 'wechat_mp', 'account_id': 'acc_testA'}]}))
    page.goto(BASE + '/projects/prj_cfc95b8c')
    panel = page.get_by_role('region', name='公众号账号归属')
    panel.get_by_text('测试公众号 A', exact=True).wait_for()
    panel.locator('.ant-select-selector').click()
    page.locator('.ant-select-item-option-content').get_by_text('测试公众号 B', exact=True).click()
    page.get_by_text('绑定未确认，原选择已保留，请重新核对：save_failed: test', exact=True).wait_for()
    panel.get_by_text('测试公众号 A', exact=True).wait_for()
    assert panel.get_by_role('combobox').is_disabled()
    page.get_by_role('button', name='保存为新版本', exact=True).wait_for()


def test_delayed_old_binding_cannot_replace_new_project_account(page):
    held = []
    page.route('**/account-profiles', lambda r: r.fulfill(json={'items': [
        {'id': 'acc_testA', 'display_name': '测试公众号 A', 'platform': 'wechat_mp'},
        {'id': 'acc_testB', 'display_name': '测试公众号 B', 'platform': 'wechat_mp'}]}))
    def binding(route):
        if 'prj_cfc95b8c' in route.request.url:
            held.append(route)
        else:
            route.fulfill(json={'items': [{'platform': 'wechat_mp', 'account_id': 'acc_testB'}]})
    page.route('**/account-binding', binding)
    page.goto(BASE + '/projects/prj_cfc95b8c')
    for _ in range(100):
        if held: break
        page.wait_for_timeout(50)
    assert held
    page.evaluate("history.pushState({}, '', '/projects/prj_a63f79b2'); window.dispatchEvent(new PopStateEvent('popstate'))")
    panel = page.get_by_role('region', name='公众号账号归属')
    panel.get_by_text('测试公众号 B', exact=True).wait_for()
    held[0].fulfill(json={'items': [{'platform': 'wechat_mp', 'account_id': 'acc_testA'}]})
    page.wait_for_timeout(100)
    assert panel.get_by_text('测试公众号 B', exact=True).is_visible()


def test_pending_creation_cannot_submit_twice(page):
    held = []
    page.route('**/api/v1/projects', lambda r: held.append(r))
    page.goto(BASE + '/projects/new')
    page.get_by_placeholder('一句想法、链接，或一段你已经写下来的材料').fill('测试重复提交')
    button = page.get_by_role('button', name='创建项目并进入工作台')
    button.click()
    button.dispatch_event('click')
    page.wait_for_timeout(100)
    assert len(held) == 1
    held[0].fulfill(status=400, json={'detail': {'error': {'code': 'test_stop', 'message': 'no real write'}}})
