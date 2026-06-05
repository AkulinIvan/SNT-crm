# SNT/payments/signals.py

import logging
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from .models import Payment, Assessment

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Payment)
def update_assessment_status_on_payment(sender, instance, created, **kwargs):
    """
    При создании нового платежа автоматически обновляем статус начисления.
    """
    try:
        if created and instance.status == Payment.STATUS_PROCESSED:
            logger.info(f"Processing payment {instance.id} for assessment {instance.assessment_id}")
            
            assessment = instance.assessment
            old_status = assessment.status
            old_debt = assessment.debt
            
            assessment.refresh_from_db()
            
            # Если долг стал 0 - отмечаем как оплачено
            if assessment.debt == 0:
                assessment.status = Assessment.STATUS_PAID
                logger.info(f"Assessment {assessment.id} fully paid")
            elif assessment.debt < assessment.amount:
                assessment.status = Assessment.STATUS_PARTIAL
                logger.info(f"Assessment {assessment.id} partially paid, remaining debt: {assessment.debt}")
            else:
                assessment.status = Assessment.STATUS_PENDING
            
            if old_status != assessment.status:
                logger.info(f"Assessment {assessment.id} status changed: {old_status} -> {assessment.status}")
            
            assessment.save(update_fields=['status', 'updated_at'])
            
    except Exception as e:
        logger.error(f"Error in payment signal: {e}", exc_info=True)


@receiver(pre_save, sender=Assessment)
def log_assessment_change(sender, instance, **kwargs):
    """Логирование изменений начисления до сохранения"""
    if instance.pk:
        try:
            old = sender.objects.get(pk=instance.pk)
            changes = []
            
            if old.amount != instance.amount:
                changes.append(f"amount: {old.amount} -> {instance.amount}")
            if old.status != instance.status:
                changes.append(f"status: {old.status} -> {instance.status}")
            if old.paid_amount != instance.paid_amount:
                changes.append(f"paid_amount: {old.paid_amount} -> {instance.paid_amount}")
            
            if changes:
                logger.info(f"Assessment {instance.id} changes: {', '.join(changes)}")
        except sender.DoesNotExist:
            logger.info(f"Creating new assessment {instance.id}")