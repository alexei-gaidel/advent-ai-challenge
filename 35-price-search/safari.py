"""Минимальный клиент WebDriver для Safari — на stdlib.

Ozon и Wildberries не отдают поиск скрипту: Ozon уводит на антибот-проверку, WB отвечает
403. Настоящий браузер эту проверку проходит, а safaridriver встроен в macOS и говорит
по WebDriver — это обычный HTTP с JSON, так что хватает urllib.

Один раз нужно разрешение пользователя:
    Safari → Настройки → Дополнения → «Показывать функции для веб-разработчиков»
    Safari → Настройки → Разработчик → «Разрешить удалённую автоматизацию»
    sudo safaridriver --enable

safaridriver запускается подпроцессом на своём порту и гасится по своему PID — чужие
процессы не трогаем. Сессия одна на приложение, вызовы под замком: окно автоматизации
у Safari тоже одно.

    python3 safari.py --selftest
"""

import atexit
import json
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

PORT = 4435
HINT = ("Safari не разрешает автоматизацию. Включи: Safari → Настройки → Дополнения → "
        "«Показывать функции для веб-разработчиков», затем Разработчик → «Разрешить удалённую "
        "автоматизацию», и выполни в терминале: sudo safaridriver --enable")


class SafariError(RuntimeError):
    pass


class Safari:
    def __init__(self, port=PORT):
        self.port = port
        self.process = None
        self.session = None
        self.lock = threading.RLock()
        atexit.register(self.quit)

    # --- протокол --------------------------------------------------------------

    def _call(self, method, path, body=None, timeout=60):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data,
                                         method=method,
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response).get("value")
        except urllib.error.HTTPError as error:
            try:
                value = json.load(error).get("value", {})
            except (json.JSONDecodeError, ValueError):
                value = {}
            message = value.get("message") or str(error)
            if "remote automation" in message.lower():
                raise SafariError(HINT) from error
            raise SafariError(f"WebDriver: {value.get('error', error.code)}: {message[:300]}") from error
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            raise SafariError(f"safaridriver не отвечает: {error}") from error

    def _port_open(self):
        with socket.socket() as sock:
            sock.settimeout(0.3)
            return sock.connect_ex(("127.0.0.1", self.port)) == 0

    def start(self):
        """Поднять safaridriver и сессию, если их ещё нет."""
        with self.lock:
            if self.session:
                return self
            if not self._port_open():
                try:
                    self.process = subprocess.Popen(["safaridriver", "-p", str(self.port)],
                                                    stdout=subprocess.DEVNULL,
                                                    stderr=subprocess.DEVNULL)
                except FileNotFoundError as error:
                    raise SafariError("safaridriver не найден — нужен macOS с Safari") from error
                for _ in range(50):
                    if self._port_open():
                        break
                    time.sleep(0.1)
            value = self._call("POST", "/session",
                               {"capabilities": {"alwaysMatch": {"browserName": "safari"}}})
            self.session = value["sessionId"]
            return self

    def quit(self):
        """Закрыть окно автоматизации и погасить СВОЙ safaridriver."""
        with self.lock:
            if self.session:
                try:
                    self._call("DELETE", f"/session/{self.session}", timeout=10)
                except SafariError:
                    pass
                self.session = None
            if self.process and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
            self.process = None

    # --- действия ----------------------------------------------------------------

    def open(self, url, settle=1.5, timeout=30):
        """Открыть страницу и дождаться document.readyState == complete."""
        with self.lock:
            self.start()
            self._call("POST", f"/session/{self.session}/url", {"url": url}, timeout=timeout + 10)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if self.run("return document.readyState") == "complete":
                    break
                time.sleep(0.3)
            time.sleep(settle)       # антибот-скрипты ставят куки уже после загрузки
            return self.run("return location.href")

    def run(self, script, *args, timeout=60):
        """Синхронный JS в странице, результат — JSON-совместимое значение."""
        with self.lock:
            self.start()
            return self._call("POST", f"/session/{self.session}/execute/sync",
                              {"script": script, "args": list(args)}, timeout=timeout)

    def run_async(self, script, *args, timeout=60):
        """Асинхронный JS: последний аргумент — callback (для fetch из контекста страницы)."""
        with self.lock:
            self.start()
            return self._call("POST", f"/session/{self.session}/execute/async",
                              {"script": script, "args": list(args)}, timeout=timeout)

    def fetch_json(self, url, timeout=30):
        """fetch из контекста открытой страницы: с её куками и заголовками. → (status, text)."""
        script = """
            const [url, done] = [arguments[0], arguments[arguments.length - 1]];
            fetch(url, {credentials: "include", headers: {"Accept": "application/json"}})
              .then(r => r.text().then(t => done([r.status, t])))
              .catch(e => done([0, String(e)]));
        """
        status, text = self.run_async(script, url, timeout=timeout)
        return status, text


BROWSER = Safari()


def selftest():
    started = time.monotonic()
    try:
        href = BROWSER.open("https://www.wildberries.ru/")
        title = BROWSER.run("return document.title")
        print(f"WB: {href} · «{title}» · {time.monotonic() - started:.1f} с")
        href = BROWSER.open("https://www.ozon.ru/")
        print(f"Ozon: {href} · «{BROWSER.run('return document.title')}»")
    except SafariError as error:
        print(f"ошибка: {error}")
        return 1
    finally:
        BROWSER.quit()
    print("Safari закрыт, safaridriver погашен")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
