# subscriptions/urls_web.py
from django.urls import path
from . import views

urlpatterns = [
    path('subscription/plans/', views.SubscriptionPlansView.as_view(), name='subscription_plans'),
    path('subscription/pay/<int:payment_id>/', views.SubscriptionPaymentView.as_view(), name='subscription_payment'),
]