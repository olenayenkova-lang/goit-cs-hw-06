"""
Простий вебдодаток без вебфреймворку.

Запускає у двох окремих процесах:
  * HTTP-сервер (порт 3000) — віддає html-сторінки та статичні ресурси,
    приймає дані форми зі сторінки message.html і пересилає їх Socket-серверу;
  * Socket-сервер (UDP, порт 5000) — перетворює отриманий байт-рядок
    на словник і зберігає його у MongoDB.
"""

import logging
import mimetypes
import os
import signal
import socket
import time
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from multiprocessing import Process
from pathlib import Path

from pymongo import MongoClient
from pymongo.errors import PyMongoError

BASE_DIR = Path(__file__).parent.resolve()
FRONT_DIR = BASE_DIR / "front-init"

HTTP_HOST = "0.0.0.0"
HTTP_PORT = 3000

SOCKET_HOST = os.getenv("SOCKET_HOST", "127.0.0.1")  # куди HTTP-сервер шле дані
SOCKET_BIND_HOST = "0.0.0.0"                          # де слухає Socket-сервер
SOCKET_PORT = 5000

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "messages_db")
MONGO_COLLECTION = os.getenv("MONGO_COLLECTION", "messages")

ROUTES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/message": "message.html",
    "/message.html": "message.html",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(processName)s] %(levelname)s: %(message)s",
)


# --------------------------------------------------------------------------
# HTTP-сервер
# --------------------------------------------------------------------------
class HttpHandler(BaseHTTPRequestHandler):
    """Обробник HTTP-запитів з простою маршрутизацією."""

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path

        if path in ROUTES:
            self.send_file(FRONT_DIR / ROUTES[path])
            return

        # Статичні ресурси (style.css, logo.png ...) — лише з теки front-init
        static_file = (FRONT_DIR / path.lstrip("/")).resolve()
        if static_file.is_file() and static_file.parent == FRONT_DIR:
            self.send_file(static_file)
        else:
            self.send_file(FRONT_DIR / "error.html", status=404)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path

        if path not in ("/message", "/message.html"):
            self.send_file(FRONT_DIR / "error.html", status=404)
            return

        length = int(self.headers.get("Content-Length", 0))
        data = self.rfile.read(length)  # байт-рядок на кшталт b"username=...&message=..."

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.sendto(data, (SOCKET_HOST, SOCKET_PORT))
        except OSError as exc:
            logging.error("Не вдалося надіслати дані Socket-серверу: %s", exc)
            self.send_error(502, "Socket server is unavailable")
            return

        # Post/Redirect/Get — щоб оновлення сторінки не дублювало повідомлення
        self.send_response(303)
        self.send_header("Location", "/message?sent=1")
        self.end_headers()

    def send_file(self, file_path: Path, status: int = 200):
        mime_type, _ = mimetypes.guess_type(file_path)
        content = file_path.read_bytes()

        self.send_response(status)
        self.send_header("Content-Type", mime_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format, *args):
        logging.info("%s - %s", self.address_string(), format % args)


def run_http_server():
    server = HTTPServer((HTTP_HOST, HTTP_PORT), HttpHandler)
    logging.info("HTTP-сервер запущено: http://localhost:%s", HTTP_PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        logging.info("HTTP-сервер зупинено")


# --------------------------------------------------------------------------
# Socket-сервер
# --------------------------------------------------------------------------
def connect_to_mongo(retries: int = 30, delay: float = 2.0) -> MongoClient:
    """Підключення до MongoDB з повторними спробами (контейнер БД може стартувати довше)."""
    for attempt in range(1, retries + 1):
        try:
            client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
            client.admin.command("ping")
            logging.info("Підключено до MongoDB")
            return client
        except PyMongoError as exc:
            logging.warning("MongoDB недоступна (спроба %s/%s): %s", attempt, retries, exc)
            time.sleep(delay)
    raise RuntimeError("Не вдалося підключитися до MongoDB")


def parse_message(raw: bytes) -> dict:
    """Перетворює байт-рядок форми (urlencoded) на словник."""
    return dict(urllib.parse.parse_qsl(raw.decode("utf-8"), keep_blank_values=True))


def run_socket_server():
    client = connect_to_mongo()
    collection = client[MONGO_DB][MONGO_COLLECTION]

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((SOCKET_BIND_HOST, SOCKET_PORT))
    logging.info("Socket-сервер (UDP) слухає порт %s", SOCKET_PORT)

    try:
        while True:
            raw, addr = sock.recvfrom(65535)
            try:
                payload = parse_message(raw)
                document = {
                    "date": str(datetime.now()),
                    "username": payload.get("username", ""),
                    "message": payload.get("message", ""),
                }
                collection.insert_one(document)
                logging.info("Збережено повідомлення від %s: %s", addr[0], document)
            except (UnicodeDecodeError, PyMongoError) as exc:
                logging.error("Помилка обробки повідомлення: %s", exc)
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
        client.close()
        logging.info("Socket-сервер зупинено")


# --------------------------------------------------------------------------
# Точка входу
# --------------------------------------------------------------------------
def _raise_keyboard_interrupt(signum, frame):
    raise KeyboardInterrupt


if __name__ == "__main__":
    # docker stop надсилає SIGTERM — обробляємо його так само, як Ctrl+C
    signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)

    processes = [
        Process(target=run_http_server, name="http-server"),
        Process(target=run_socket_server, name="socket-server"),
    ]
    for process in processes:
        process.start()

    try:
        for process in processes:
            process.join()
    except KeyboardInterrupt:
        logging.info("Зупинка додатку...")
        for process in processes:
            process.terminate()
        for process in processes:
            process.join()
