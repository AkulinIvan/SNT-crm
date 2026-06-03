from django.apps import AppConfig


class VotingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'voting'
    verbose_name = 'Голосования и бюллетени'
    
    def ready(self):
        import voting.signals