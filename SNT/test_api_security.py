# test_api_security.py - финальная версия
import requests
import json
import time

BASE_URL = 'http://localhost:8000'
ADMIN_USERNAME = 'admin'  # Замените на реального администратора
ADMIN_PASSWORD = 'MSJw7_jT5-'  # Замените на реальный пароль

session = requests.Session()


def get_csrf_token():
    """Получение CSRF токена"""
    try:
        response = session.get(f'{BASE_URL}/api/auth/login/')
        return session.cookies.get('csrftoken')
    except:
        return None


def login():
    """Логин в систему"""
    csrf_token = get_csrf_token()
    headers = {'X-CSRFToken': csrf_token} if csrf_token else {}
    
    response = session.post(f'{BASE_URL}/api/auth/login/', 
                           json={'username': ADMIN_USERNAME, 'password': ADMIN_PASSWORD},
                           headers=headers)
    
    if response.status_code == 200:
        print(f"✅ Logged in as {ADMIN_USERNAME}")
        return True
    else:
        print(f"❌ Login failed: {response.status_code}")
        print(f"Response: {response.text}")
        return False


def test_health_check():
    """Тест health check"""
    print("\n=== Testing Health Check ===")
    
    try:
        response = requests.get(f'{BASE_URL}/api/health/', timeout=5)
        
        if response.status_code == 200:
            print("✅ Health check working")
            data = response.json()
            print(f"   Status: {data.get('status')}")
            print(f"   Maintenance mode: {data.get('maintenance_mode')}")
            return True
        else:
            print(f"❌ Health check failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def test_rate_limit():
    """Тест ограничения частоты запросов"""
    print("\n=== Testing Rate Limit ===")
    
    if not login():
        return False
    
    print("Making 65 requests to /api/plots/...")
    blocked = False
    
    for i in range(65):
        response = session.get(f'{BASE_URL}/api/plots/')
        
        if response.status_code == 429:
            print(f"✅ Rate limit triggered at request {i+1}")
            print(f"   Response: {response.json()}")
            blocked = True
            break
        elif response.status_code == 200:
            if (i + 1) % 10 == 0:
                print(f"   Request {i+1}: OK")
    
    if not blocked:
        print("❌ Rate limit not triggered after 65 requests")
        return False
    
    return True


def test_maintenance_mode():
    """Тест режима обслуживания"""
    print("\n=== Testing Maintenance Mode ===")
    
    if not login():
        return False
    
    # Включаем режим обслуживания
    response = session.post(f'{BASE_URL}/api/security/maintenance/toggle/', 
                           json={'enabled': True, 'message': 'Test'})
    
    if response.status_code == 200:
        print("✅ Maintenance mode enabled")
    else:
        print(f"❌ Failed to enable: {response.status_code}")
        if response.status_code == 403:
            print("   You might not have admin permissions")
        return False
    
    # Проверяем статус
    status_response = session.get(f'{BASE_URL}/api/security/maintenance/status/')
    if status_response.status_code == 200:
        print(f"   Status: {status_response.json()}")
    
    # Выключаем
    response = session.post(f'{BASE_URL}/api/security/maintenance/toggle/', 
                           json={'enabled': False})
    
    if response.status_code == 200:
        print("✅ Maintenance mode disabled")
    
    return True


def test_ip_block():
    """Тест блокировки IP"""
    print("\n=== Testing IP Block ===")
    
    if not login():
        return False
    
    test_ip = '127.0.0.1'
    
    # Получаем текущие заблокированные IP
    list_response = session.get(f'{BASE_URL}/api/security/ip/list/')
    if list_response.status_code == 200:
        print(f"   Current blocked: {list_response.json().get('blocked_ips', [])}")
    
    # Блокируем IP
    block_response = session.post(f'{BASE_URL}/api/security/ip/block/', 
                                 json={'ip': test_ip, 'reason': 'Test'})
    
    if block_response.status_code == 200:
        print(f"✅ IP {test_ip} blocked")
    else:
        print(f"❌ Failed to block: {block_response.status_code}")
        return False
    
    # Разблокируем
    unblock_response = session.post(f'{BASE_URL}/api/security/ip/unblock/', 
                                   json={'ip': test_ip})
    
    if unblock_response.status_code == 200:
        print(f"✅ IP {test_ip} unblocked")
    
    return True


def test_security_stats():
    """Тест статистики безопасности"""
    print("\n=== Testing Security Stats ===")
    
    if not login():
        return False
    
    response = session.get(f'{BASE_URL}/api/security/stats/')
    
    if response.status_code == 200:
        print("✅ Security stats available")
        stats = response.json()
        print(f"   Maintenance mode: {stats.get('maintenance_mode')}")
        print(f"   Blocked IPs: {stats.get('blocked_ips_count')}")
        print(f"   Rate limits: {stats.get('rate_limits')}")
        return True
    else:
        print(f"❌ Failed: {response.status_code}")
        return False


def test_protected_endpoints():
    """Тест защищённых эндпоинтов"""
    print("\n=== Testing Protected Endpoints ===")
    
    # Очищаем сессию
    session.cookies.clear()
    
    protected = [
        '/api/plots/',
        '/api/owners/',
        '/api/users/',
    ]
    
    all_protected = True
    for endpoint in protected:
        response = session.get(f'{BASE_URL}{endpoint}')
        if response.status_code in [401, 403]:
            print(f"✅ {endpoint} - protected (status {response.status_code})")
        else:
            print(f"❌ {endpoint} - NOT protected (status {response.status_code})")
            all_protected = False
    
    return all_protected


if __name__ == '__main__':
    print("=" * 50)
    print("API Security Tests")
    print("=" * 50)
    
    # Базовые тесты
    test_health_check()
    test_protected_endpoints()
    test_rate_limit()
    
    # Админ тесты
    print("\n" + "=" * 30)
    print("Admin Tests")
    print("=" * 30)
    
    test_maintenance_mode()
    test_ip_block()
    test_security_stats()
    
    print("\n" + "=" * 50)
    print("Tests completed")
    print("=" * 50)