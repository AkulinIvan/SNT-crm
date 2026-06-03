from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import Permission


@receiver(post_save, sender='accounts.User')
def add_voting_permissions_for_managers(sender, instance, created, **kwargs):
    """Автоматически добавляем права на голосование для менеджеров"""
    if instance.is_manager or instance.is_superuser:
        perm = Permission.objects.filter(codename='can_vote').first()
        if perm and not instance.has_perm('voting.can_vote'):
            instance.user_permissions.add(perm)