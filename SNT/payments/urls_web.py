from django.urls import path
from .views import PaymentsDashboardView, AssessmentsListView, BankImportView, generate_combined_pdf
from django.views.generic import TemplateView

urlpatterns = [
    path('payments/', PaymentsDashboardView.as_view(), name='payments-dashboard'),
    path('payments/assessments/', AssessmentsListView.as_view(), name='assessments-list'),
    path('payments/import/', BankImportView.as_view(), name='bank-import'),
    path('generate-combined-pdf/', generate_combined_pdf, name='generate-combined-pdf'),
    path('payments/categories/', TemplateView.as_view(template_name='payments/manage_categories.html'), name='manage-categories'),
]