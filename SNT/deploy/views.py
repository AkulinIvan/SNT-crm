# deploy/views.py
import json
import logging
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

logger = logging.getLogger(__name__)

@csrf_exempt
@require_http_methods(["POST"])
def github_webhook(request):
    """Минимальный тестовый обработчик"""
    
    # 1. Логируем ВСЁ, что пришло
    logger.info("=== Webhook received ===")
    logger.info(f"Headers: {dict(request.headers)}")
    logger.info(f"Body: {request.body[:500]}")  # Первые 500 байт
    
    # 2. Простой ответ без всякой логики
    return HttpResponse(
        json.dumps({
            "status": "ok",
            "message": "Webhook received successfully",
            "headers_count": len(request.headers)
        }),
        content_type="application/json",
        status=200
    )