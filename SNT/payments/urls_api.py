from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    PaymentCategoryViewSet, PaymentPeriodViewSet,
    AssessmentViewSet, PaymentViewSet,
    BankStatementViewSet, BankTransactionViewSet,
    QuickPaymentViewSet,
)

router = DefaultRouter()
router.register(r'payment-categories', PaymentCategoryViewSet)
router.register(r'payment-periods', PaymentPeriodViewSet)
router.register(r'assessments', AssessmentViewSet, basename='assessment')
router.register(r'payments', PaymentViewSet)
router.register(r'bank-statements', BankStatementViewSet)
router.register(r'bank-transactions', BankTransactionViewSet)
router.register(r'quick-payment', QuickPaymentViewSet, basename='quick-payment')

urlpatterns = [
    path('', include(router.urls)),
]