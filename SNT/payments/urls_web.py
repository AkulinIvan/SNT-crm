from django.urls import path
from .views import PaymentsDashboardView, AssessmentsListView, BankImportView

urlpatterns = [
    path('payments/', PaymentsDashboardView.as_view(), name='payments-dashboard'),
    path('payments/assessments/', AssessmentsListView.as_view(), name='assessments-list'),
    path('payments/import/', BankImportView.as_view(), name='bank-import'),
]