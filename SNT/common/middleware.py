import threading


class RequestMiddleware:
    """
    Middleware для сохранения request в текущем потоке.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Сохраняем request в текущем потоке
        threading.current_thread().request = request
        response = self.get_response(request)
        # Очищаем после обработки
        if hasattr(threading.current_thread(), 'request'):
            del threading.current_thread().request
        return response