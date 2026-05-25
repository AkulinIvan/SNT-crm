# deploy/views.py
import subprocess
import hmac
import hashlib
from django.http import HttpResponse, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings

@csrf_exempt
def github_webhook(request):
    # Проверка метода
    if request.method != 'POST':
        return HttpResponse('Method not allowed', status=405)
    
    # Проверка подписи (опционально, но рекомендую)
    signature = request.headers.get('X-Hub-Signature-256')
    if not signature or signature != expected_signature:
        return HttpResponseForbidden('Invalid signature')
    
    secret = settings.GITHUB_WEBHOOK_SECRET  # Убедитесь, что переменная определена
    payload = request.body
    expected_signature = 'sha256=' + hmac.new(
        secret.encode('utf-8'),
        payload,
        hashlib.sha256
    ).hexdigest()
    
    if not hmac.compare_digest(expected_signature, signature):
        return HttpResponseForbidden('Invalid signature')
    
    # Путь к вашему проекту
    repo_dir = '/home/aiv/SNT/SNT-crm/SNT'  # Измените на ваш путь
    
    try:
        # 1. Обновляем код
        subprocess.run(['git', 'pull'], cwd=repo_dir, check=True)
        
        # 2. Обновляем зависимости
        subprocess.run(['pip', 'install', '-r', 'requirements.txt'], cwd=repo_dir, check=True)
        
        # 3. Применяем миграции (БЕЗОПАСНО для данных)
        subprocess.run(['python', 'manage.py', 'migrate'], cwd=repo_dir, check=True)
        
        # 4. Собираем статику
        subprocess.run(['python', 'manage.py', 'collectstatic', '--noinput'], cwd=repo_dir, check=True)
        
        # 5. Перезапускаем Gunicorn
        subprocess.run(['sudo', 'systemctl', 'restart', 'gunicorn'], check=True)
        
        return HttpResponse('Deploy completed successfully', status=200)
        
    except subprocess.CalledProcessError as e:
        # Логируем ошибку
        with open('/tmp/webhook_error.log', 'a') as f:
            f.write(f"Deploy failed: {str(e)}\n")
        return HttpResponse(f'Deploy failed: {str(e)}', status=500)