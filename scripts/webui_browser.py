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
from tests.webui_proxy_fixture import SUBSCRIPTION, local_download


async def check_download_ui(page, runtime, peer, output):
    await page.locator('[data-tab="download"]').click()
    await expect(page.locator("#download-save")).to_be_enabled()
    await expect(page.locator("#download-mode")).to_have_value("direct")
    await expect(page.locator("#proxy-state")).to_contain_text("尚未导入")
    assert await page.locator(".download-advanced").get_attribute("open") is None
    assert await page.locator(".proxy-details").get_attribute("open") is None
    saved = runtime.store.get_download_settings()
    await page.locator(".download-advanced > summary").click()
    await page.locator("#download-concurrency").fill("4")
    await page.locator("#download-job_timeout").fill("31")
    await page.wait_for_timeout(3200)
    await expect(page.locator("#download-concurrency")).to_have_value("4")
    await expect(page.locator("#download-unsaved")).to_be_visible()
    assert runtime.store.get_download_settings() == saved
    await page.locator("#download-reset").click()
    await expect(page.locator("#download-job_timeout")).to_have_value("30")
    assert runtime.store.get_download_settings() == saved
    await page.locator("#download-job_timeout").fill("31")
    await page.locator("#download-save").click()
    await expect(page.locator("#download-feedback")).to_contain_text("已生效")
    assert runtime.store.get_download_settings()["job_timeout"] == 1860
    await page.locator("#download-reset").click()
    await page.locator("#download-save").click()
    await expect(page.locator("#download-feedback")).to_contain_text("已生效")
    assert runtime.store.get_download_settings() == saved
    await page.screenshot(path=output / "10-download-settings.png", full_page=True)

    async with local_download() as download:
        await page.locator(".proxy-details > summary").click()
        await page.locator("#proxy-file").set_input_files({
            "name": "local-nodes.yaml", "mimeType": "application/yaml", "buffer": SUBSCRIPTION.encode()})
        await page.locator("#proxy-import").click()
        await expect(page.locator("#proxy-state")).to_contain_text("连接正常")
        assert runtime.store.state["proxy"]["node"] == "AUTO"
        await expect(page.locator("#proxy-file")).to_have_value("")
        await page.locator("#proxy-url").fill(download.base_url + "/subscription.yaml")
        await page.locator("#proxy-import").click()
        await expect(page.locator("#proxy-url")).to_have_value("")
        await expect(page.locator("#proxy-import")).to_be_enabled()
        await page.locator("#proxy-refresh").click()
        await expect(page.locator("#proxy-feedback")).to_contain_text("已更新")
        await page.locator("#proxy-node").select_option("beta")
        await page.locator("#proxy-status-refresh").click()
        await expect(page.locator("#proxy-status-refresh")).to_be_enabled()
        await expect(page.locator("#proxy-node")).to_have_value("beta")
        await page.locator("#proxy-choose").click()
        await expect(page.locator("#proxy-feedback")).to_contain_text("已更新")
        assert runtime.store.state["proxy"]["node"] == "beta"
        await page.locator("#download-mode").select_option("mihomo")
        await page.locator("#download-save").click()
        await expect(page.locator("#download-feedback")).to_contain_text("已生效")
        assert runtime.store.get_download_settings()["mode"] == "mihomo"
        await expect(page.locator("#download-current-mode")).to_contain_text("NAS 独立代理")
        await page.screenshot(path=output / "11-proxy.png", full_page=True)
        await page.locator("#download-test-id").fill("90001")
        await page.locator("#download-detect").click()
        await expect(page.locator("#download-test-feedback")).to_contain_text("接口：通过；图片：通过")
        assert download.image_reads == 1
        assert download.sessions[0].settings["mode"] == "mihomo" and download.sessions[0].proxy_url
        await expect(page.locator("#download-detect")).to_be_enabled()
        await page.screenshot(path=output / "12-probe.png", full_page=True)

        await peer.emit({"post_type": "message", "message_type": "group", "group_id": 20001,
                         "user_id": 10001, "self_id": 99999, "message_id": 90002,
                         "message": [{"type": "text", "data": {"text": "/jm 90002"}}]})
        progress = page.locator("#tab-download [data-download-progress]")
        stop = page.locator("#tab-download [data-cancel-download]")
        await expect(progress).to_contain_text("1 / 1", timeout=8000)
        await expect(progress).to_contain_text("local-image-fixture")
        await expect(stop).to_be_enabled()
        for control in ["download-save", "download-detect", "proxy-import", "proxy-refresh", "proxy-choose", "proxy-node"]:
            await expect(page.locator("#" + control)).to_be_disabled()
        await page.locator("#download-concurrency").fill("5")
        uploads = len(peer.uploads)
        await stop.click()
        await expect(page.locator("#tab-download [data-cancel-feedback]")).to_contain_text("停止")
        async with asyncio.timeout(5):
            while not download.stop_seen.is_set():
                await asyncio.sleep(0.02)
        assert runtime.bot.busy, "cancel must not free slot before worker returns"
        await expect(progress).to_contain_text("用户停止下载")
        await page.screenshot(path=output / "13-stop.png", full_page=True)
        download.release_stop.set()
        async with asyncio.timeout(5):
            while runtime.bot.busy:
                await asyncio.sleep(0.02)
        await expect(page.locator("#download-save")).to_be_enabled(timeout=8000)
        await expect(stop).to_be_disabled()
        await expect(page.locator("#tab-download [data-cancel-feedback]")).to_be_hidden()
        await expect(page.locator("#download-concurrency")).to_have_value("5")
        await expect(page.locator("#download-unsaved")).to_be_visible()
        assert runtime.store.get_download_settings()["concurrency"] == 3
        assert len(peer.uploads) == uploads
        assert download.image_reads == 2
        assert not list(runtime.bot.data_dir.glob("job_*"))
        await page.set_viewport_size({"width": 390, "height": 844})
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "download mobile overflow"
        await page.screenshot(path=output / "14-mobile-download.png", full_page=True)
        await page.set_viewport_size({"width": 1440, "height": 1080})
    print("Download WebUI OK: persisted settings, polling-safe drafts, native YAML and URL import, refresh, node selection, local HTTP image probe, real bot cancellation and worker ownership, mobile")


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
                    await check_download_ui(page, runtime, peer, output)
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
