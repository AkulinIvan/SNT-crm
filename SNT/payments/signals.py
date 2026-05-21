# SNT/payments/signals.py (создайте новый файл)

from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Payment, Assessment

@receiver(post_save, sender=Payment)
def update_assessment_status_on_payment(sender, instance, created, **kwargs):
    """
    При создании нового платежа автоматически обновляем статус начисления.
    """
    if created and instance.status == Payment.STATUS_PROCESSED:
        assessment = instance.assessment
        assessment.refresh_from_db()
        
        # Если долг стал 0 - отмечаем как оплачено
        if assessment.debt == 0:
            assessment.status = Assessment.STATUS_PAID
        elif assessment.debt < assessment.amount:
            assessment.status = Assessment.STATUS_PARTIAL
        else:
            assessment.status = Assessment.STATUS_PENDING
        
        assessment.save(update_fields=['status', 'updated_at'])