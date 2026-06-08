# SNT/voting/apps.py
from django.apps import AppConfig
import logging

logger = logging.getLogger(__name__)


class VotingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'voting'
    verbose_name = 'Голосования'

    def ready(self):
        """
        При запуске приложения регистрируем сигналы и создаём разрешения
        """
        try:
            # Импортируем сигналы
            import voting.signals
            logger.info("Voting signals loaded")
            
            # Создаём разрешения для голосования
            self._create_voting_permissions()
            
        except Exception as e:
            logger.warning(f"Error loading voting signals: {e}")
    
    def _create_voting_permissions(self):
        """Создание разрешений для голосования"""
        try:
            from django.contrib.auth.models import Permission
            from django.contrib.contenttypes.models import ContentType
            
            # Получаем модель VotingSession
            from voting.models import VotingSession
            content_type = ContentType.objects.get_for_model(VotingSession)
            
            permissions = [
                ('can_vote', 'Может голосовать'),
                ('can_manage_voting', 'Может управлять голосованиями'),
            ]
            
            for codename, name in permissions:
                perm, created = Permission.objects.get_or_create(
                    codename=codename,
                    content_type=content_type,
                    defaults={'name': name}
                )
                if created:
                    logger.info(f"Created permission: {codename}")
                    
        except Exception as e:
            logger.warning(f"Could not create voting permissions: {e}")