import os, time, json, requests
from datetime import datetime
from zoneinfo import ZoneInfo
from seleniumwire import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

EMAIL = os.getenv("GREATHOST_EMAIL", "")
PASSWORD = os.getenv("GREATHOST_PASSWORD", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
PROXY_URL = os.getenv("PROXY_URL", "") 
TARGET_NAME = os.getenv("TARGET_NAME", "translator-bot") 

STATUS_MAP = {
    "running": ["🟢", "Running"],
    "starting": ["🟡", "Starting"],
    "stopped": ["🔴", "Stopped"],
    "offline": ["⚪", "Offline"],
    "suspended": ["🚫", "Suspended"]
}

def now_shanghai():
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime('%Y/%m/%d %H:%M:%S')

def send_notice(kind, fields):
    titles = {
        "restart_success": "🔄 <b>GreatHost 重启指令已发送</b>",
        "restart_failed": "⚠️ <b>GreatHost 重启失败</b>",
        "error": "🚨 <b>GreatHost 重启脚本报错</b>"
    }
    body = "\n".join([f"{e} {k}: {v}" for e, k, v in fields])
    msg = f"{titles.get(kind, '📢 通知')}\n\n{body}\n📅 时间: {now_shanghai()}"
    
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        try:
            print("正在尝试发送 Telegram 通知...")
            proxies_config = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None
            response = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                data={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"},
                proxies=proxies_config, 
                timeout=15
            )
            if response.status_code != 200:
                print(f"❌ TG 发送失败! 状态码: {response.status_code}")
            else:
                print("✅ TG 通知发送成功")
        except Exception as e:
            print(f"❌ TG 发送发生网络异常: {e}")

class GH:
    def __init__(self):
        opts = Options()
        opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        proxy = {'proxy': {'http': PROXY_URL, 'https': PROXY_URL}} if PROXY_URL else None
        self.d = webdriver.Chrome(options=opts, seleniumwire_options=proxy)
        self.w = WebDriverWait(self.d, 25)

    def api(self, url, method="GET", payload=None):
        print(f"📡 API 调用 [{method}] {url}")
        if payload:
            # 如果有 payload，构建支持 POST JSON 的 JS 代码
            script = f"""
            return fetch('{url}', {{
                method: '{method}',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({json.dumps(payload)})
            }}).then(r=>r.json()).catch(e=>({{success:false,message:e.toString()}}))
            """
        else:
            script = f"return fetch('{url}',{{method:'{method}'}}).then(r=>r.json()).catch(e=>({{success:false,message:e.toString()}}))"
        return self.d.execute_script(script)

    def get_ip(self):
        try:
            self.d.get("https://api.ipify.org?format=json")
            return json.loads(self.d.find_element(By.TAG_NAME, "body").text).get("ip", "Unknown")
        except:
            return "Unknown"

    def login(self):
        print(f"🔑 正在登录: {EMAIL[:3]}***...")
        self.d.get("https://greathost.es/login")
        self.w.until(EC.presence_of_element_located((By.NAME, "email"))).send_keys(EMAIL)
        self.d.find_element(By.NAME, "password").send_keys(PASSWORD)
        self.d.find_element(By.CSS_SELECTOR, "button[type='submit']").click()
        self.w.until(EC.url_contains("/dashboard"))
        time.sleep(2) # 等待面板数据加载

    def get_server(self):
        data = self.api("/api/servers")
        #print(f"DEBUG: 获取服务器列表的原始返回 -> {data}") # 添加这行打印
        servers = data.get("servers", [])
        return next((s for s in servers if s.get("name") == TARGET_NAME), None)
    #def get_server(self):
        #servers = self.api("/api/servers").get("servers", [])
        #return next((s for s in servers if s.get("name") == TARGET_NAME), None)

    def get_status(self, sid):
        info = self.api(f"/api/servers/{sid}/information")
        st = info.get("status", "unknown").lower()
        icon, name = STATUS_MAP.get(st, ["❓", st])
        return icon, name

    def restart(self, sid):
        print("🚀 正在执行重启指令...")
        
        # 【重要修改点】：由于各大面板的重启 API 格式不同，这里假设了两种最常见的面板 API 结构。
        # 如果这个面板是基于 Pterodactyl (翼龙面板) 修改的，通常是发送电源信号：
        # url = f"/api/client/servers/{sid}/power"
        # payload = {"action": "start"}
        
        # 这里先尝试使用最符合 Greathost 上下文的通用 REST 格式：
        url = f"/api/servers/{sid}/power" # 请根据 F12 抓包结果修改此处 URL
        payload = {"action": "start"} # 如果抓包发现有 payload，在这里填入，例如 payload = {"action": "restart"}
        
        return self.api(url, method="POST", payload=payload)

    def close(self):
        self.d.quit()

def run():
    gh = GH()
    try:
        ip = gh.get_ip()
        gh.login()
        srv = gh.get_server()
        if not srv: raise Exception(f"未找到服务器 {TARGET_NAME}")
        sid = srv["id"]
        print(f"✅ 已锁定目标服务器: {TARGET_NAME} (ID: {sid})")

        # 重启前状态
        icon, stname = gh.get_status(sid)
        print(f"📋 当前状态: {icon} {stname}")

        # 发送重启请求
        res = gh.restart(sid)
        
        # 很多时候重启指令返回空字典或者只返回 HTTP 状态码
        # 这里放宽校验，只要没有显式返回 success: false 我们就当它请求成功了
        ok = res.get("success", True) if isinstance(res, dict) else True 
        msg = res.get("message", "重启指令已发出") if isinstance(res, dict) else str(res)

        if ok:
            # 可选：等待几秒后再查一次状态，看看是不是变成 starting 了
            time.sleep(5)
            new_icon, new_stname = gh.get_status(sid)
            
            send_notice("restart_success", [
                ("📛","服务器名称",TARGET_NAME),
                ("🆔","ID",f"<code>{sid}</code>"),
                ("🔄","操作前状态",f"{icon} {stname}"),
                ("🚀","操作后状态",f"{new_icon} {new_stname}"),
                ("🌐","落地 IP",f"<code>{ip}</code>")
            ])
        else:
            send_notice("restart_failed", [
                ("📛","服务器名称",TARGET_NAME),
                ("🆔","ID",f"<code>{sid}</code>"),
                ("💡","API返回信息",msg),
                ("🌐","落地 IP",f"<code>{ip}</code>")
            ])
            
    except Exception as e:
        print(f"🚨 运行异常: {e}")
        send_notice("error", [
            ("📛", "服务器名称", TARGET_NAME),
            ("❌", "故障", f"<code>{str(e)[:100]}</code>"),
        ])
    finally:
        if 'gh' in locals():
            try: gh.close()
            except: pass

if __name__ == "__main__":
    run()
