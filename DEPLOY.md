# 🚀 Инструкция по развертыванию

Выберите один из двух вариантов развертывания.

---

## Вариант 1: Простой (Git Clone)
*Подходит, если у вас есть доступ к Git на сервере и вы хотите собирать проект прямо там.*

1. **На сервере** склонируйте репозиторий:
   ```bash
   git clone https://gitlab.com/your-repo/openapi_history.git
   cd openapi_history
   ```

2. **Создайте файлы конфигурации** (их нет в git):
   * Создайте `.env` и вставьте туда ключи (Service API Key).
   * Проверьте `projects.yaml`.

3. **Запустите**:
   ```bash
   docker-compose up -d --build
   ```

---

## Вариант 2: Профессиональный (Docker Image)
*Подходит для CI/CD и чистых серверов без исходного кода.*

### Шаг 1: Сборка и отправка образа (на локальной машине)
Нужно собрать образ и отправить его в реестр (Docker Hub или GitLab Container Registry).

1. **Соберите образ**:
   ```bash
   # Замените 'your-user' на ваш логин
   docker build -t your-user/openapi-history:latest .
   ```

2. **Загрузите в реестр**:
   ```bash
   docker login
   docker push your-user/openapi-history:latest
   ```

### Шаг 2: Настройка сервера
На сервере вам нужны **только 3 файла**:

1. `docker-compose.prod.yml` (переименуйте в `docker-compose.yml`)
2. `projects.yaml`
3. `.env` (с вашими секретами)

**Структура папок на сервере:**
```text
/opt/openapi-history/
├── docker-compose.yml
├── projects.yaml
├── .env
├── logs/        (создастся автоматически)
└── cache_data/  (создастся автоматически)
```

### Шаг 3: Запуск на сервере
В папке с файлами выполните:

```bash
# Если вы не переименовывали файл:
docker-compose -f docker-compose.prod.yml up -d

# Или если переименовали в docker-compose.yml:
docker-compose up -d
```

---

## 🛠 Полезные команды

**Посмотреть логи:**
```bash
docker-compose logs -f --tail=100
```

**Обновить версию (для Варианта 2):**
```bash
docker-compose pull
docker-compose up -d
```

**Перезагрузить:**
```bash
docker-compose restart
```
