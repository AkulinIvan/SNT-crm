from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    ConsolidatedAssessmentViewSet, PaymentCategoryViewSet, PaymentPeriodViewSet,
    AssessmentViewSet, PaymentViewSet,
    BankStatementViewSet, BankTransactionViewSet, ReceiptTemplateViewSet,
    QuickPaymentViewSet, bulk_update_assessments,
)

router = DefaultRouter()
router.register(r'payment-categories', PaymentCategoryViewSet)
router.register(r'payment-periods', PaymentPeriodViewSet)
router.register(r'assessments', AssessmentViewSet, basename='assessment')
router.register(r'payments', PaymentViewSet)
router.register(r'bank-statements', BankStatementViewSet)
router.register(r'bank-transactions', BankTransactionViewSet)
router.register(r'receipt-templates', ReceiptTemplateViewSet)
router.register(r'consolidated', ConsolidatedAssessmentViewSet, basename='consolidated')
router.register(r'quick-payment', QuickPaymentViewSet, basename='quick-payment')

urlpatterns = [
    path('', include(router.urls)),
    path('assessments/bulk-update-amounts/', bulk_update_assessments, name='bulk-update-assessments'),
]

# Список всех доступных эндпоинтов после добавления:
# 
# GET    /api/payment-categories/
# POST   /api/payment-categories/
# GET    /api/payment-categories/{id}/
# PUT    /api/payment-categories/{id}/
# PATCH  /api/payment-categories/{id}/
# DELETE /api/payment-categories/{id}/
#
# GET    /api/payment-periods/
# POST   /api/payment-periods/
# GET    /api/payment-periods/{id}/
# PUT    /api/payment-periods/{id}/
# PATCH  /api/payment-periods/{id}/
# DELETE /api/payment-periods/{id}/
#
# GET    /api/assessments/
# POST   /api/assessments/
# GET    /api/assessments/{id}/
# PUT    /api/assessments/{id}/
# PATCH  /api/assessments/{id}/
# DELETE /api/assessments/{id}/
# POST   /api/assessments/generate/           # Массовое создание для всех участков
# POST   /api/assessments/generate-for-owner/ # Массовое создание для конкретного владельца
# GET    /api/assessments/stats/              # Статистика по начислениям
# POST   /api/assessments/{id}/add-payment/   # Добавить платёж к начислению
# GET    /api/assessments/{id}/receipt/       # Получить данные квитанции (JSON)
# GET    /api/assessments/{id}/receipt-html/  # Получить HTML квитанцию
# GET    /api/assessments/{id}/receipt-pdf/   # Скачать PDF квитанцию
# GET    /api/assessments/owner-receipts/     # Квитанции владельца
#
# GET    /api/payments/
# POST   /api/payments/
# GET    /api/payments/{id}/
# PUT    /api/payments/{id}/
# PATCH  /api/payments/{id}/
# DELETE /api/payments/{id}/
#
# GET    /api/bank-statements/
# POST   /api/bank-statements/
# GET    /api/bank-statements/{id}/
# PUT    /api/bank-statements/{id}/
# PATCH  /api/bank-statements/{id}/
# DELETE /api/bank-statements/{id}/
# POST   /api/bank-statements/import/         # Импорт выписки из файла
#
# GET    /api/bank-transactions/
# POST   /api/bank-transactions/
# GET    /api/bank-transactions/{id}/
# PUT    /api/bank-transactions/{id}/
# PATCH  /api/bank-transactions/{id}/
# DELETE /api/bank-transactions/{id}/
#
# GET    /api/receipt-templates/
# POST   /api/receipt-templates/
# GET    /api/receipt-templates/{id}/
# PUT    /api/receipt-templates/{id}/
# PATCH  /api/receipt-templates/{id}/
# DELETE /api/receipt-templates/{id}/
#
# GET    /api/consolidated/
# POST   /api/consolidated/
# GET    /api/consolidated/{id}/
# PUT    /api/consolidated/{id}/
# PATCH  /api/consolidated/{id}/
# DELETE /api/consolidated/{id}/
# POST   /api/consolidated/generate-from-template/  # Создать из шаблона
# GET    /api/consolidated/{id}/receipt/            # Квитанция для составного начисления
#
# GET    /api/quick-payment/verify/{assessment_id}/  # Проверить статус оплаты
# POST   /api/quick-payment/match-payment/          # Ручное сопоставление платежа