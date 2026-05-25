import os
import subprocess
import hmac
import hashlib
from django.http import HttpResponse, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings

@csrf_exempt
def github_webhook(request):
    # 1. Проверка подписи (Безопасность)
    # Получаем подпись из заголовка GitHub
    # signature = request.headers.get('X-Hub-Signature-256')
    # if not signature:
    #     return HttpResponseForbidden('No signature')
    
    # Вычисляем нашу подпись
    secret = settings.GITHUB_WEBHOOK_SECRET
    payload = request.body
    expected_signature = 'sha256=' + hmac.new(
        secret.encode(), payload, hashlib.sha256
    ).hexdigest()
    
    # Сравниваем
    if not hmac.compare_digest(expected_signature):
        return HttpResponseForbidden('Invalid signature')
    
    # 2. Проверка события (чтобы не срабатывало на любые действия)
    event = request.headers.get('X-GitHub-Event')
    if event == 'ping':
        return HttpResponse('Pong')
    
    if event == 'push':
        # 3. Выполнение команд деплоя
        # Путь к папке проекта на сервере
        repo_dir = '/home/aiv/SNT/SNT-crm' 
        
        # Переменные окружения для Django (если используете .env)
        env = os.environ.copy()
        env['DJANGO_SETTINGS_MODULE'] = 'core.settings.production'
        
        try:
            # Забираем код
            subprocess.run(['git', 'pull'], cwd=repo_dir, env=env, check=True)
            # Устанавливаем/обновляем зависимости
            subprocess.run(['pip', 'install', '-r', 'requirements.txt'], cwd=repo_dir, env=env, check=True)
            # Применяем миграции
            subprocess.run(['python', 'manage.py', 'migrate'], cwd=repo_dir, env=env, check=True)
            # Собираем статику
            subprocess.run(['python', 'manage.py', 'collectstatic', '--noinput'], cwd=repo_dir, env=env, check=True)
            # Перезапускаем сервер (пример для systemd + Gunicorn)
            subprocess.run(['su -', 'systemctl', 'restart', 'crm-snt'], check=True)
            
            return HttpResponse('Deploy completed', status=200)
        except subprocess.CalledProcessError as e:
            # Логируйте ошибку
            return HttpResponse(f'Deploy failed: {str(e)}', status=500)
    
    return HttpResponse('OK')