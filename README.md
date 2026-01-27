# OpenAPI History Tracker API

Сервис для автоматической генерации документации API на основе истории изменений в GitLab MR.

## Быстрый старт

```bash
# 1. Установка зависимостей
pip install -r requirements.txt

# 2. Настройка .env
cp .env.example .env  # Отредактируйте переменные

# 3. Запуск сервера
python run_api.py --port 8000
```

## API Endpoints

### Cache

#### POST `/api/v1/cache/warm`
Прогревает кеш - загружает OpenAPI спецификации из GitLab и предвычисляет историю изменений.

```bash
curl -X POST 'http://localhost:8000/api/v1/cache/warm' \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: YOUR_API_KEY' \
  -d '{
    "gitlab_token": "glpat-xxxxxxxxxxxxxxxxxxxx",
    "project_path": "logistic/retail/storefront-api",
    "mr_limit": 1000
}'
```

**Параметры:**
| Параметр | Тип | Описание |
|----------|-----|----------|
| `gitlab_token` | string | **Обязательный.** GitLab personal access token |
| `project_path` | string | **Обязательный.** Путь к проекту в GitLab |
| `mr_limit` | int | Максимум MR для обработки (default: 1000) |
| `since_date` | date | Обрабатывать MR только после этой даты (YYYY-MM-DD) |
| `compute_history` | bool | Предвычислять историю эндпоинтов (default: true) |

---

#### GET `/api/v1/cache/status`
Возвращает статистику кеша.

```bash
curl 'http://localhost:8000/api/v1/cache/status'
```

---

#### DELETE `/api/v1/cache/clear`
Очищает кеш для проекта.

```bash
curl -X DELETE 'http://localhost:8000/api/v1/cache/clear' \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: YOUR_API_KEY' \
  -d '{
    "project_path": "logistic/retail/storefront-api",
    "confirm": true
}'
```

---

### Documentation

#### POST `/api/v1/documentation/generate`
Генерирует документацию для API эндпоинта и публикует в Confluence.

```bash
curl -X POST 'http://localhost:8000/api/v1/documentation/generate' \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: YOUR_API_KEY' \
  -d '{
    "target_endpoint": "POST /offices/products/list",
    "project_path": "logistic/retail/storefront-api",
    "gitlab_token": "glpat-xxxxxxxxxxxxxxxxxxxx",
    "confluence_parent_page_id": "301467109"
}'
```

**Параметры:**
| Параметр | Тип | Описание |
|----------|-----|----------|
| `target_endpoint` | string | **Обязательный.** Эндпоинт в формате "METHOD /path" |
| `project_path` | string | **Обязательный.** Путь к проекту в GitLab |
| `gitlab_token` | string | GitLab token (или из .env) |
| `confluence_token` | string | Confluence token (или из .env) |
| `confluence_space_key` | string | Ключ пространства (или из .env) |
| `confluence_parent_page_id` | string | ID родительской страницы |

---

## CLI

```bash
# Прогрев кеша
python cli.py warm-cache --project logistic/retail/storefront-api --full

# Генерация документации  
python cli.py generate --endpoint "POST /offices/products/list" \
  --project logistic/retail/storefront-api
```

---

## Переменные окружения (.env)

```env
# GitLab
GITLAB_URL=https://gitlab.example.com
GITLAB_TOKEN=glpat-xxxxxxxxxxxxxxxxxxxx

# Confluence
CONFLUENCE_BASE_URL=https://confluence.example.com
CONFLUENCE_TOKEN=your_token
CONFLUENCE_SPACE_KEY=DOCRMS

# API
API_KEY=your-api-key
```

---

## Swagger UI

После запуска сервера документация доступна:

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
