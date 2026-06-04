# CRM СНТ — Система управления садоводческим некоммерческим товариществом

[![Django](https://img.shields.io/badge/Django-4.2-green.svg)](https://www.djangoproject.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15-blue.svg)](https://www.postgresql.org/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## О проекте

CRM СНТ — это полнофункциональная веб-платформа для автоматизации деятельности садоводческих некоммерческих товариществ. Система позволяет управлять владельцами участков, земельными участками, финансовыми операциями, проводить онлайн-голосования и многое другое.

### Основные возможности

| Модуль | Функционал |
|--------|------------|
| Владельцы | Управление реестром собственников, контактная информация, история членства |
| Участки | Кадастровый учёт, привязка владельцев, отображение на карте, границы из Росреестра |
| Платежи | Начисление взносов, приём платежей, импорт банковских выписок, генерация квитанций с QR-кодом |
| Голосования | Создание собраний, заочное голосование, подсчёт результатов, приглашения по email/SMS |
| Карта | Визуализация участков на карте, отображение границ, поиск по координатам |
| Тарифы | Гибкая система подписки, ограничение функционала в зависимости от тарифа |
| Права доступа | Ролевая модель: администратор, председатель, бухгалтер, наблюдатель |

## Технологический стек

### Backend
- Django 4.2 — основной фреймворк
- Django REST Framework — построение API
- PostgreSQL — основная база данных
- Celery + Redis — фоновые задачи (рассылки, импорт)
- WebSocket -уведомления

### Frontend
- HTML5/CSS3 — адаптивный интерфейс
- JavaScript (Vanilla) — динамические компоненты
- Leaflet — интерактивные карты
- Chart.js — статистика и графики

### Интеграции
- Платежные системы — YooKassa, Tinkoff, Сбербанк
- Импорт выписок — CSV, Excel, PDF (Сбербанк, Тинькофф, Альфа-Банк)
- Росреестр — получение границ участков по кадастровому номеру
- Email — рассылка квитанций через SMTP
- SMS — уведомления (интеграция с провайдерами)

## Быстрый старт

### Требования
- Python 3.10+
- PostgreSQL 15+
- Redis (опционально, для фоновых задач)
- pip и virtualenv

### Установка

1. Клонирование репозитория
```bash
git clone https://github.com/your-org/snt-crm.git
cd snt-crm
Создание виртуального окружения

python -m venv venv
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate     # Windows
Установка зависимостей

pip install -r requirements.txt
Настройка переменных окружения

Создайте файл .env в корне проекта:

env
# Django
SECRET_KEY=your-secret-key-here
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1

# Database
DB_NAME=snt_crm
DB_USER=snt_user
DB_PASSWORD=your_secure_password
DB_HOST=localhost
DB_PORT=5432

# Email
EMAIL_HOST_USER=your-email@yandex.ru
EMAIL_HOST_PASSWORD=your-email-password

# Payment systems (опционально)
YOKASSA_SHOP_ID=your-shop-id
YOKASSA_SECRET_KEY=your-secret-key
Настройка базы данных

# Создание базы данных PostgreSQL
sudo -u postgres psql
CREATE DATABASE snt_crm;
CREATE USER snt_user WITH PASSWORD 'your_secure_password';
ALTER ROLE snt_user SET client_encoding TO 'utf8';
ALTER ROLE snt_user SET default_transaction_isolation TO 'read committed';
GRANT ALL PRIVILEGES ON DATABASE snt_crm TO snt_user;
\q

# Применение миграций
python manage.py makemigrations
python manage.py migrate
Загрузка начальных данных

# Загрузка тарифных планов
python manage.py loaddata fixtures/tariffs.json

# Загрузка тестовых данных (опционально)
python manage.py loaddata fixtures/test_data.json
Создание суперпользователя

python manage.py createsuperuser
Запуск сервера разработки

python manage.py runserver
Откройте браузер и перейдите по адресу http://localhost:8000

Структура проекта

SNT/
├── accounts/          # Управление пользователями и правами доступа
├── calls/             # Модуль работы с звонками (Asterisk интеграция)
├── common/            # Общие утилиты, миксины, middleware
├── land/              # Управление земельными участками и картой
├── organizations/     # Управление СНТ и членством
├── payments/          # Финансовый модуль (начисления, платежи, квитанции)
├── subscriptions/     # Тарифы и подписки
├── users/             # Управление владельцами и их контактами
├── voting/            # Модуль голосований
├── templates/         # Базовые шаблоны
├── static/            # Статические файлы (CSS, JS, изображения)
├── media/             # Загруженные пользователем файлы
└── SNT/               # Настройки проекта (settings, urls)
Основные модули системы
1. Управление владельцами (users)
Полный CRUD для владельцев

Множественные контакты (телефон, email)

Привязка к участкам через Ownership с долями

История членства в СНТ

Поиск и фильтрация с пагинацией

Экспорт в CSV/Excel

2. Управление участками (land)
Кадастровый учёт с валидацией формата

Геопозиционирование на карте (Leaflet)

Загрузка границ из Росреестра

Массовый импорт из Excel

Поиск соседних участков

Экспорт данных

3. Финансовый модуль (payments)
Гибкая система категорий взносов:

Фиксированная сумма

Расчёт по площади (₽/сотка)

Расчёт по потреблению (₽/кВт·ч)

Автоматическая генерация уникальных UID для квитанций

QR-коды по стандарту ГОСТ Р 56042-2014

Импорт банковских выписок:

CSV, Excel, PDF

Автоматическое сопоставление платежей по UID

Поддержка Сбербанка, Тинькофф, Альфа-Банка

Email-рассылка квитанций с PDF-вложением

Статистика задолженностей

4. Голосования (voting)
Типы голосований: очное, заочное, смешанное

Множественные вопросы с вариантами ответов

Расчёт кворума

Приглашения по email/SMS с уникальными токенами

Публичные страницы голосования

Детальная статистика результатов

5. Тарифы и подписки (subscriptions)
Гибкая система тарифов (Базовый, Стандарт, Премиум)

Помесячная и годовая оплата (скидка 17% за год)

Интеграция с платёжными системами

Ограничения по количеству:

Владельцев (50/500/∞)

Участков (50/500/∞)

Пользователей (1/5/∞)

Пробный период 30 дней

API Эндпоинты
Система предоставляет REST API для интеграции с другими сервисами:

# Аутентификация
POST   /api/auth/login/
POST   /api/auth/logout/
GET    /api/auth/me/
POST   /api/auth/change-password/

# Владельцы
GET    /api/owners/
POST   /api/owners/
GET    /api/owners/{id}/
PUT    /api/owners/{id}/
DELETE /api/owners/{id}/
POST   /api/owners/{id}/add-plot/
POST   /api/owners/{id}/add-contact/

# Участки
GET    /api/plots/
POST   /api/plots/
GET    /api/plots/geo/          # Данные для карты
POST   /api/plots/import-excel/ # Импорт из Excel

# Начисления
GET    /api/assessments/
POST   /api/assessments/generate/
POST   /api/assessments/mass-generate/
GET    /api/assessments/{id}/receipt-html/
GET    /api/assessments/{id}/receipt-pdf/

# Банковские выписки
POST   /api/bank-statements/import/
GET    /api/bank-statements/{id}/transactions/

# Голосования
GET    /api/voting/sessions/
POST   /api/voting/sessions/{id}/vote/
GET    /api/voting/sessions/{id}/results/
Полная документация API доступна по адресу /api/docs/ после запуска сервера (требуется настройка drf-yasg или аналогичного инструмента).

Интерфейс пользователя
Дашборд
Главная страница с ключевыми метриками:

Количество владельцев, участков, СНТ

Общая задолженность

Последние добавленные владельцы

Последние начисления и платежи

Личный кабинет
Просмотр профиля и смена пароля

Управление подпиской

Просмотр прав доступа

Административная панель
Django Admin с расширенным функционалом:

Управление тарифами и категориями взносов

Просмотр логов действий пользователей

Массовые операции с начислениями

Мониторинг очередей задач (при использовании Celery)

Права доступа
Роль	Описание	Доступные модули
Администратор	Полный доступ ко всем функциям	Все модули, управление пользователями, лог действий
Председатель	Управление СНТ и финансами	Владельцы, участки, платежи, голосования, начисления
Бухгалтер	Финансовые операции	Платежи, начисления, импорт выписок, отчёты
Наблюдатель	Только просмотр	Владельцы, участки, результаты голосований
Развёртывание в production
Вариант 1: Docker Compose (рекомендуемый)
# Клонирование и настройка
git clone https://github.com/your-org/snt-crm.git
cd snt-crm
cp .env.example .env
# Отредактируйте .env файл

# Запуск всех сервисов
docker-compose up -d

# Применение миграций
docker-compose exec web python manage.py migrate
docker-compose exec web python manage.py collectstatic --noinput

# Создание суперпользователя
docker-compose exec web python manage.py createsuperuser
Вариант 2: Ручная установка
# Установка зависимостей системы
sudo apt update
sudo apt install nginx postgresql redis-server supervisor

# Настройка PostgreSQL и Redis (см. официальную документацию)

# Клонирование и установка приложения
git clone https://github.com/your-org/snt-crm.git
cd snt-crm
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install gunicorn psycopg2-binary

# Настройка Gunicorn
gunicorn --bind 127.0.0.1:8000 SNT.wsgi:application

# Настройка Nginx (пример конфигурации)
sudo nano /etc/nginx/sites-available/snt-crm
Пример конфигурации Nginx:

nginx
server {
    listen 80;
    server_name your-domain.com;
    
    location /static/ {
        alias /path/to/SNT/staticfiles/;
    }
    
    location /media/ {
        alias /path/to/SNT/media/;
    }
    
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
Тестирование
# Запуск всех тестов
python manage.py test

# Запуск тестов конкретного приложения
python manage.py test payments
python manage.py test voting

# С отчётом о покрытии (требуется coverage)
coverage run manage.py test
coverage report
coverage html  # Генерация HTML-отчёта
Лицензия
Проект распространяется под лицензией MIT. Подробнее в файле LICENSE.

Авторы и поддержка
Разработчик — Иван Акулинин

Email — akuliniwan@yandex.ru

Telegram — @akuliniwan

Как внести вклад
Форкните репозиторий

Создайте ветку для новой функции (git checkout -b feature/amazing-feature)

Зафиксируйте изменения (git commit -m 'Add amazing feature')

Отправьте изменения в ваш форк (git push origin feature/amazing-feature)

Откройте Pull Request

Известные проблемы и TODO
Оптимизация N+1 запросов в отчётах

Добавление кэширования для карты и статистики

Реализация WebSocket для实时 уведомлений

Интеграция с сервисами СМС-уведомлений (SMS.ru, Twilio)

Мобильное приложение (React Native)

Поддержка электронной подписи для квитанций

Интеграция с ГИС ЖКХ

Дополнительная документация
Пользовательская документация

API документация

Руководство администратора

План миграции с Excel

Разработано с ❤️ для садоводов и председателей СНТ

## Файлы для добавления в репозиторий

### requirements.txt

```txt
Django==4.2.7
djangorestframework==3.14.0
django-filter==23.5
psycopg2-binary==2.9.9
celery==5.3.4
redis==5.0.1
openpyxl==3.1.2
Pillow==10.1.0
weasyprint==60.2
qrcode==7.4.2
phonenumbers==8.13.29
python-dotenv==1.0.0
gunicorn==21.2.0
.env.example
env
SECRET_KEY=your-secret-key-here
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1

DB_NAME=snt_crm
DB_USER=snt_user
DB_PASSWORD=your_secure_password
DB_HOST=localhost
DB_PORT=5432

EMAIL_HOST_USER=your-email@yandex.ru
EMAIL_HOST_PASSWORD=your-email-password
DEFAULT_FROM_EMAIL=your-email@yandex.ru

REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/0
docker-compose.yml
yaml
version: '3.8'

services:
  db:
    image: postgres:15
    environment:
      POSTGRES_DB: snt_crm
      POSTGRES_USER: snt_user
      POSTGRES_PASSWORD: snt_secure_password_2024
    volumes:
      - postgres_data:/var/lib/postgresql/data
    ports:
      - "5432:5432"

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"

  web:
    build: .
    command: python manage.py runserver 0.0.0.0:8000
    volumes:
      - .:/app
    ports:
      - "8000:8000"
    depends_on:
      - db
      - redis
    environment:
      - DB_HOST=db
      - REDIS_HOST=redis

volumes:
  postgres_data: