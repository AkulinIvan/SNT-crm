# users/signals.py
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Owner
from organizations.models import OrganizationMembership
import threading
import logging

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Owner)
def auto_assign_membership(sender, instance, created, **kwargs):
    """
    При создании владельца автоматически создаем членство в СНТ текущего пользователя.
    """
    logger.info(f"Signal triggered: Owner {instance.full_name} created={created}")
    
    if created:
        # Пытаемся получить request из текущего потока
        request = getattr(threading.current_thread(), 'request', None)
        
        if request:
            logger.info(f"Request found, user: {request.user}, org: {getattr(request, 'current_organization', None)}")
            
            if hasattr(request, 'current_organization') and request.current_organization:
                org = request.current_organization
                logger.info(f"Creating membership for owner {instance.id} in org {org.id}")
                
                membership, created_membership = OrganizationMembership.objects.get_or_create(
                    owner=instance,
                    organization=org,
                    defaults={'status': 'active'}
                )
                
                if created_membership:
                    logger.info(f"Membership created: {membership.id}")
                else:
                    logger.info(f"Membership already exists: {membership.id}")
            else:
                logger.warning("No current_organization in request")
        else:
            logger.warning("No request found in current thread")