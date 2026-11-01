"""Exercise the real local WebUI in a browser; QQ itself is simulated."""
import argparse
import asyncio
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from playwright.async_api import async_playwright, expect
from tests.webui_fixture import PASSWORD, running_webui
from tests.https_proxy import https_proxy


async def main(channel=None):
    output = ROOT / ".cache" / "webui-browser"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as folder:
        async with running_webui(Path(folder)) as (url, runtime, peer):
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True, **({"channel": channel} if channel else {}))
                try:
                    page = await browser.new_page(viewport={"width": 1440, "height": 1080}, device_scale_factor=1)
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
                    await page.goto(url)
                    await expect(page.locator("#login-view")).to_be_visible()
                    await page.screenshot(path=output / "01-login.png", full_page=True)
                    await page.locator("#password").fill(PASSWORD)
                    await page.get_by_role("button", name="登录管理台").click()
                    await expect(page.locator("#app-view")).to_be_visible()
                    await expect(page.locator("#ready-title")).to_contain_text("还差一步")
                    await page.screenshot(path=output / "02-waiting.png", full_page=True)
                    await page.locator('[data-tab="qq"]').click()
                    await expect(page.locator("#qr-image")).to_be_visible()
                    await page.screenshot(path=output / "03-qr.png", full_page=True)
                    await page.locator("#refresh-qr").click()
                    await expect(page.locator("#qr-feedback")).to_contain_text("已刷新")
                    peer.logged_in = True
                    await runtime.poll()
                    await expect(page.locator("#qr-title")).to_contain_text("已连接", timeout=8000)
                    await page.locator('[data-tab="permissions"]').click()
                    await page.locator("#reload-groups").click()
                    await expect(page.get_by_text("测试文件群", exact=True)).to_be_visible()
                    await page.locator('#group-list input[value="20001"]').check()
                    await page.locator("#allowed-users").fill("10001\n0010001")
                    await page.locator("#save-permissions").click()
                    await expect(page.locator("#permissions-feedback")).to_contain_text("已生效")
                    assert runtime.store.get_permissions() == {
                        "allowed_users": ["10001"], "allowed_groups": ["20001"], "group_allow_all": True}
                    await page.screenshot(path=output / "04-permissions.png", full_page=True)
                    await page.locator("#allowed-users").fill("10001\n10002")
                    await page.wait_for_timeout(3200)
                    await expect(page.locator("#allowed-users")).to_have_value("10001\n10002")
                    await expect(page.locator("#unsaved")).to_be_visible()
                    await page.locator('[data-tab="overview"]').click()
                    await expect(page.locator("#header-state")).to_have_text("已就绪", timeout=8000)
                    await page.locator("#test-target").select_option("group:20001")
                    await page.locator("#test-submit").click()
                    await expect(page.locator("#test-feedback")).to_contain_text("已上传")
                    assert len(peer.uploads) == 1
                    assert not Path(peer.uploads[0]["file"]).exists()
                    await expect(page.locator("#result-status")).to_have_text("处理完成")
                    await page.screenshot(path=output / "05-ready.png", full_page=True)
                    peer.reject_upload = True
                    await page.locator("#test-submit").click()
                    await expect(page.locator("#test-feedback")).to_contain_text("结果未确认")
                    assert len(peer.uploads) == 2
                    assert Path(peer.uploads[1]["file"]).is_file()
                    await expect(page.locator("#result-status")).to_have_text("文件已保留")
                    await page.screenshot(path=output / "06-retained.png", full_page=True)
                    await page.set_viewport_size({"width": 390, "height": 844})
                    await page.locator('[data-tab="permissions"]').click()
                    await page.screenshot(path=output / "07-mobile.png", full_page=True)
                    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "mobile overflow"
                    await page.reload()
                    await expect(page.locator("#app-view")).to_be_visible()
                    await expect(page.locator("#allowed-users")).to_have_value("10001")
                    await page.locator("#logout").click()
                    await expect(page.locator("#login-view")).to_be_visible()
                    await page.screenshot(path=output / "08-logout.png", full_page=True)
                    assert not errors, "Browser errors: " + " | ".join(errors)
                    print("WebUI browser flow OK: login, QR, group permissions, unsaved edits, PDF ack/retention, mobile, session/logout; console errors=0")
                    print(f"Screenshots: {output}")
                    async with https_proxy(url, Path(folder)) as proxy_url:
                        context = await browser.new_context(ignore_https_errors=True)
                        try:
                            proxy_page = await context.new_page()
                            proxy_errors = []
                            proxy_page.on("pageerror", lambda error: proxy_errors.append(str(error)))
                            proxy_page.on("console", lambda message: proxy_errors.append(message.text) if message.type == "error" else None)
                            await proxy_page.goto(proxy_url + "/webui/web_login")
                            assert proxy_page.url.split("#", 1)[0] == proxy_url + "/"
                            await proxy_page.locator("#password").fill(PASSWORD)
                            await proxy_page.get_by_role("button", name="登录管理台").click()
                            await expect(proxy_page.locator("#app-view")).to_be_visible()
                            cookie = next(cookie for cookie in await context.cookies() if cookie["name"] == "jm_session")
                            assert cookie["secure"] and cookie["httpOnly"] and cookie["sameSite"] == "Strict"
                            await proxy_page.locator('[data-tab="permissions"]').click()
                            await expect(proxy_page.locator("#allowed-users")).to_have_value("10001")
                            await proxy_page.locator("#allowed-users").fill("10001\n10002")
                            await proxy_page.locator("#save-permissions").click()
                            await expect(proxy_page.locator("#permissions-feedback")).to_contain_text("已生效")
                            assert runtime.store.get_permissions()["allowed_users"] == ["10001", "10002"]
                            await proxy_page.screenshot(path=output / "09-https-proxy.png", full_page=True)
                            await proxy_page.reload()
                            await expect(proxy_page.locator("#app-view")).to_be_visible()
                            await proxy_page.locator("#logout").click()
                            await expect(proxy_page.locator("#login-view")).to_be_visible()
                            assert not proxy_errors, "HTTPS proxy browser errors: " + " | ".join(proxy_errors)
                            print("HTTPS-to-HTTP proxy browser flow OK: legacy URL redirect, login, Secure cookie, permission save, reload/logout; console errors=0")
                        finally:
                            await context.close()
                finally:
                    await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", help="Use an installed Chromium browser, e.g. msedge")
    args = parser.parse_args()
    asyncio.run(main(args.channel))
