# goit-cs-hw-06 — Вебдодаток без фреймворку + Socket-сервер + MongoDB

Простий вебдодаток на чистому Python (без вебфреймворку), який приймає дані
форми, пересилає їх Socket-серверу, а той зберігає їх у MongoDB.

## Архітектура

```
Браузер ──HTTP :3000──► HTTP-сервер ──UDP :5000──► Socket-сервер ──► MongoDB
          (форма)        (процес 1)                  (процес 2)
```

* **HTTP-сервер** (`http.server`, порт `3000`) — маршрутизація сторінок
  `index.html`, `message.html`, статичні ресурси `style.css`, `logo.png`;
  для неіснуючих адрес повертає `error.html` зі статусом `404 Not Found`.
* **Socket-сервер** (UDP, порт `5000`) — отримує байт-рядок форми, перетворює
  його на словник і додає документ до MongoDB.
* Обидва сервери запускаються з одного файлу `main.py` у **різних процесах**
  (`multiprocessing.Process`).

## Структура проєкту

```
goit-cs-hw-06/
├── main.py                # HTTP-сервер + Socket-сервер
├── front-init/            # html-сторінки та статичні ресурси
│   ├── index.html
│   ├── message.html
│   ├── error.html
│   ├── style.css
│   └── logo.png
├── Dockerfile
├── docker-compose.yaml
├── requirements.txt
└── README.md
```

## Формат документа в MongoDB

База `messages_db`, колекція `messages`:

```json
{
  "date": "2022-10-29 20:20:58.020261",
  "username": "krabaton",
  "message": "First message"
}
```

`date` — час отримання повідомлення Socket-сервером (`str(datetime.now())`).

## Запуск через Docker Compose

```powershell
docker-compose up --build
```

* Вебдодаток: http://localhost:3000
* Сторінка форми: http://localhost:3000/message
* Дані MongoDB зберігаються у named volume `mongo_data`
  (`/data/db` у контейнері `mongo`), тому **не втрачаються** після
  `docker-compose down` / перезапуску контейнерів.

Зупинка: `docker-compose down` (без `-v`, щоб зберегти дані).

### Перевірка збережених повідомлень

```powershell
docker exec -it goit-cs-hw-06-mongo mongosh messages_db --eval "db.messages.find().pretty()"
```

## Локальний запуск (без Docker)

Потрібна запущена MongoDB на `localhost:27017`.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

Змінні оточення (необов'язкові): `MONGO_URI`, `MONGO_DB`, `MONGO_COLLECTION`,
`SOCKET_HOST`.

## Ключові рішення

* **UDP** обрано для передачі даних між серверами — для одного невеликого
  повідомлення форми цього достатньо, а код простіший, ніж для TCP.
* Форма обробляється за схемою **Post/Redirect/Get**: після `POST /message`
  сервер відповідає `303` і перенаправляє на `/message?sent=1`, тож оновлення
  сторінки не дублює повідомлення.
* Байт-рядок форми розбирається через `urllib.parse.parse_qsl`, тому символи
  `&`, `=`, кирилиця та пробіли в тексті повідомлення зберігаються коректно.
* Socket-сервер повторює спроби підключення до MongoDB під час старту —
  контейнер БД може підніматися довше за контейнер додатку.
* Статичні файли віддаються лише з теки `front-init` (захист від
  path traversal, наприклад `/../main.py` → 404).
* Сигнал `SIGTERM` (`docker stop`) обробляється: обидва процеси
  завершуються коректно.
