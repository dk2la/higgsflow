# higgsflow — CLI для Higgsfield API: финальный дизайн

Репозиторий: `higgsflow`. Команда: `higgs`. Неофициальный клиент, не связан с Higgsfield.

Директория `~/startups/fable5/higgsflow-web` никак не относится к этому проекту: это не его
веб-часть (в т.ч. не этап 2), код и документацию оттуда не использовать и не менять.

Статус: дизайн согласован 2026-10-30, реализация не начата.
Документация API: https://docs.higgsfield.ai/docs (полный дамп: `/docs/llms-full.txt`).

## 1. Цель

Локальный CLI `higgs`, покрывающий весь документированный Higgsfield API, с проектами,
в которых всё (входные файлы, референсы, промпты, генерации, финальные видео) лежит
в одном месте с понятной структурой и именованием.

Этап 1 — CLI. Этап 2 — локальный веб-интерфейс (FastAPI + лёгкий фронт) поверх того же
ядра и тех же папок проектов. Веб-кода на этапе 1 нет, только архитектура под него.

## 2. Объём

**Входит (весь документированный API):**
- Видео: 67 эндпоинтов в 22 семействах — Seedance 2.0/2.5, Kling 2.5 Turbo/2.6/3.0/3.0 Turbo/O3/Omni,
  Kling Motion Control 2.6/3.0, Cinema Studio 4.0, Genjutsu (motion transfer, object swap, restyle),
  Happy Horse 1.0/1.1, Wan 2.6/2.7/3.0/3.0 Prime, LTX-2.5, MiniMax H3, Hailuo 2.3, PixVerse V6,
  Grok Imagine Video 1.5. Все режимы: text-to-video, image-to-video (start/end frame),
  reference-to-video, video-edit, video-extend, motion control/transfer, restyle и т.д.
- Изображения: 17 эндпоинтов (SOUL/V2/Cinema, Marketing Studio Image, Grok Image 2.0,
  Recraft V4.1, Qwen Image 3, Ideogram v4, Z-Image Turbo и др.) — в т.ч. для референсов.
- Обучение Soul ID (`/v1/custom-references`), каталоги пресетов (Soul styles, Genjutsu Restyle
  presets, Marketing Studio presets), Ads Studio.
- Служебное: upload файлов, estimate цены, status, cancel, delete, usage.

**Не входит:**
- Генерация промптов через Claude (ни `prompt`/`--enhance`, ни CLAUDE.md/skill в проектах).
  Промпты пишет пользователь сам.
- Webhooks (у локального CLI нет публичного HTTPS-адреса) — только polling.
- Локальное редактирование видео (ffmpeg: trim/concat/frames).
- Legacy-эндпоинты `/v1/speak`, `/v1/image2video/dop`, `/v1/motions` — отсутствуют в текущих доках.
- Веб-интерфейс — этап 2.

## 3. Стек и окружение

- Python 3.12, Typer (команды), Rich (таблицы, прогресс), httpx (HTTP), Pydantic (модели/схемы).
- Тесты: pytest + respx.
- Два пакета:
  - `hfcore` — ядро: каталог, API-клиент, проекты, задачи, загрузки, скачивание. Ничего не знает
    о терминале, возвращает Pydantic-модели, прогресс отдаёт через callback. Его же будет
    вызывать будущий FastAPI-сервер.
  - `hfcli` — тонкий слой Typer/Rich поверх ядра. У каждой команды есть `--json`
    (для скриптов и для Claude Code).
- Официальный SDK `higgsfield-client` (0.2.0, pre-1.0) не обязателен: API — простой REST,
  свой клиент на httpx даёт полный контроль (idempotency, retry, estimate, upload).
- **Изолированное окружение — Docker, только Docker Desktop** (`/usr/local/bin/docker --context desktop-linux`).
  Rancher Desktop не используется (его `~/.rd/bin/docker` стоит первым в PATH — поэтому путь
  к docker и контекст указываются явно).
  - Образ собирается локально из `Dockerfile` в репе.
  - На хосте только обёртка `~/.local/bin/higgs`: пересобирает образ при изменениях, делает
    `docker run`, монтирует корень проектов `~/hf-projects` по тому же абсолютному пути
    (чтобы относительные пути работали) и `~/.config/higgs/` read-only (ключи).
  - Разработка и тесты тоже в контейнере.
- Репозиторий: локальный git в этой папке, без публикации. Проекты с видео — вне репы.

## 4. Аутентификация

- Ключ Higgsfield `KEY_ID:KEY_SECRET` из console.higgsfield.ai.
- Заголовок: `Authorization: Key <KEY_ID>:<KEY_SECRET>`.
- Порядок поиска: env (`HF_KEY="id:secret"` или `HF_API_KEY` + `HF_API_SECRET` — совместимо
  с официальными SDK) → `~/.config/higgs/credentials.toml` (chmod 600), заполняется `higgs auth login`.
- Keychain не используется.

## 5. Проекты и хранение

Корень проектов по умолчанию `~/hf-projects` (настраивается). `higgs init <name>` создаёт:

```
~/hf-projects/<name>/
├── project.toml        название, дефолты (модель, aspect ratio, качество…), budget (опц.)
├── inputs/             свои фото/видео/аудио
├── references/         референсы (свои + сохранённые генерации); обращение как @name
├── prompts/            промпты пользователя (.md), для --prompt-file
├── generations/
│   └── YYYY-MM-DD/     результаты + .json-метаданные рядом
├── final/              отобранное через `higgs pick`
└── .hf/
    ├── jobs/           <request_id>.json — пишется сразу при отправке задачи
    └── uploads.json    кэш загрузок: sha256 файла → public_url + срок (7 дней)
```

**БД нет.** Источник правды — JSON-файлы:
- при отправке задачи сразу создаётся `.hf/jobs/<request_id>.json` (ID не теряется при обрыве);
- после скачивания рядом с файлом — `<имя>.mp4.json`: эндпоинт, все параметры, промпт,
  входные файлы/референсы, request_id, цена, ссылки, время;
- `list`/`status` сканируют файлы. Индекс поверх можно добавить позже (например, для веба).

Глобально: `~/.config/higgs/` — credentials, config (корень проектов), `models.local.toml`.

**Именование генераций:**
`YYYYMMDD-HHMM_<model-slug>_<mode>_<slug>_vNN.<ext>`,
например `20261030-1412_kling-3-0_i2v_sunset-beach_v02.mp4`.
`<slug>` — из `--name` или из первых слов промпта; `vNN` — номер версии при совпадении.

## 6. Каталог моделей

- Эндпоинта со списком моделей в доках нет. Каталог парсится из JSON-схем в
  `docs.higgsfield.ai/docs/llms-full.txt`, хранится в репе как `catalog.json`,
  обновляется `higgs models sync`.
- По каждому эндпоинту: id (= путь = строка модели в SDK), семейство, режим, параметры
  (типы, enum, min/max, default, required), ограничения промпта (длина, negative prompt,
  токены вида `<<<image_1>>>`), ориентировочная цена «от $/сек» со страницы прайсинга.
- Каждый запрос валидируется по схеме до отправки.
- **Модели, которых нет в доках** (напр. Kling 4.0, Genjutsu Light со страницы цен):
  1. сырой вызов `higgs gen <endpoint-id> -p k=v --no-validate` — работает для любого пути,
     т.к. API единообразен (`POST /{endpoint}` → `request_id` → тот же polling);
     ID нельзя угадать по маркетинговому названию — брать из Console/доков/прайсинга;
     неверный ID → 404 без списания;
  2. ручное описание в `~/.config/higgs/models.local.toml` (путь, параметры, алиас) —
     модель появляется в `models list` и в удобных командах с валидацией;
  3. `models sync` пробует недокументированный `GET /models` с ключом и мержит результат,
     если он что-то вернёт (код терпим к любому формату ответа).

## 7. Команды

| Команда | Назначение |
|---|---|
| `higgs init <name>` / `higgs projects` | создать проект / список проектов |
| `higgs auth login` | сохранить ключ |
| `higgs models list [--mode i2v] [--family kling]` | модели, режимы, цены «от» |
| `higgs models show <model>` | параметры, допустимые качество/длительность/aspect, ограничения промпта |
| `higgs models sync` | обновить каталог |
| `higgs video t2v\|i2v\|ref\|edit\|extend\|motion ...` | удобный слой для видео |
| `higgs image ...` | удобный слой для изображений |
| `higgs gen <endpoint-id> -p k=v ... [--no-validate]` | универсальный вызов любого эндпоинта |
| `higgs status [<id>]` / `higgs list [фильтры]` | статус задач / история проекта |
| `higgs sync` | докачать завершённые задачи |
| `higgs download <id>` | скачать результат повторно (пока ссылка жива) |
| `higgs cancel <id>` / `higgs delete <id>` | отмена (только queued) / удаление на стороне API |
| `higgs pick <generation>` | скопировать в `final/` |
| `higgs ref save <generation> <name>` | сохранить генерацию как референс |
| `higgs usage` | траты проекта (локально) + сводка аккаунта (`GET /models/usage`) |

Общие флаги удобного слоя: `--model`, `--quality`, `--duration`, `--aspect`, `--image`,
`--end-image`, `--ref`, `--video`, `--audio`, `--prompt` / `--prompt-file`, `--name`,
`--seed`, `--yes`, `--no-wait`, `--json`. CLI сам выбирает эндпоинт по модели + режиму
и маппит общие флаги в параметры конкретной модели (`resolution` / `mode=std|pro|4k` и т.п.).

Стиль: всё задаётся флагами; если обязательного параметра нет — интерактивный вопрос
(выбор модели, режима, качества, длительности). `--yes` / `--no-input` — для скриптов.

## 8. Жизненный цикл генерации

1. Валидация параметров по схеме каталога.
2. Локальные файлы (`inputs/a.jpg`, `@hero`) автоматически загружаются:
   `POST /files/generate-upload-url` → `PUT` с `upload_headers` → `public_url`.
   Кэш по sha256 на 7 дней (API удаляет загрузки через 7 дней).
3. Точная цена: `POST /estimate/{endpoint}` с тем же телом → `{"usd": "..."}`.
   Показ, подтверждение (`--yes` пропускает), проверка `budget` проекта (предупреждение при превышении).
4. Отправка `POST /{endpoint}` с `Idempotency-Key` (UUID); сразу запись `.hf/jobs/<request_id>.json`.
5. Polling `GET /requests/{id}/status`: 2 с → backoff до 10 с + jitter.
   `429 account_queue_full` → ждать `Retry-After`; `503` → retry с backoff.
6. Терминальные статусы: `completed`, `failed`, `nsfw`, `canceled` (failed/nsfw/canceled не тарифицируются).
7. Автоскачивание результата (ссылки живут ~7 дней) + запись `.json` рядом.
8. `--no-wait`: вернуть request_id сразу; `higgs sync` потом докачает.

## 9. Справка по API (на 2026-10-30)

- Base URL `https://api.higgsfield.ai`. Ответы содержат `X-Correlation-ID`.
- Submit → `{status:"queued", request_id, status_url, cancel_url, expires_after}`.
- Output: `video:{url}`, `images:[{url}]`, `audio:{url}`; у некоторых операций доп. артефакты.
- Cancel: `POST /requests/{id}/cancel` (202, или 400 если уже в работе).
  Delete: `DELETE /requests/{id}` (409 пока queued/in_progress).
- Лимиты — по concurrency; сверх лимита задачи встают в очередь, переполнение очереди → 429.
- Биллинг в USD (отдельно от кредитов подписки higgsfield.ai), видео — за секунду результата.
  Эндпоинта баланса нет (только Console). Эндпоинта истории генераций нет.
- Ошибки: 401 неверный ключ; 403 `api_key_expired`/`api_key_disabled`/недостаточно средств;
  404/423/503 — модель недоступна; 422 — конфликт idempotency.
- Допустимые типы загрузки: jpeg/png/webp/gif, wav, mp4.

**Не подтверждено:** формат и назначение `GET /models`; реальная concurrency по умолчанию
(2 или 20); точные цены по всем конфигурациям (кроме `/estimate`); глобальные лимиты размера файлов;
эндпоинты Kling 4.0 и Genjutsu Light.

## 10. Процесс разработки

- TDD (red → green → refactor), тесты через публичный интерфейс: вызов CLI-команды
  + подмена HTTP на уровне транспорта (respx). Внутренние функции не мокаются.
- Фикстуры ответов — из примеров документации; парсер каталога — на снапшоте `llms-full.txt` в репе.
- Тесты запускаются в Docker-контейнере.
- Живой smoke-тест (upload → submit → poll → download на реальном API) делает пользователь.

## 11. Этап 2 (не сейчас)

Локальный веб: FastAPI + лёгкий фронт в том же Docker-образе, поверх `hfcore` и тех же папок
проектов — галерея проектов/генераций, превью видео, формы по JSON-схемам моделей,
сравнение вариантов, скачивание. При необходимости — индекс (SQLite) поверх JSON-файлов.
Веб живёт в этой же репе; `~/startups/fable5/higgsflow-web` к нему не относится.
