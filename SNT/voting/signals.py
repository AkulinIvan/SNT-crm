# SNT/voting/signals.py
import logging
import traceback
from django.db.models.signals import post_save, pre_save, pre_delete
from django.dispatch import receiver
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Q

logger = logging.getLogger(__name__)


@receiver(post_save, sender='accounts.User')
def add_voting_permissions_for_managers(sender, instance, created, **kwargs):
    """
    Автоматически добавляем права на голосование для менеджеров и админов.
    """
    logger.info(f"=== ADD_VOTING_PERMISSIONS SIGNAL ===")
    logger.info(f"User: {instance.email if hasattr(instance, 'email') else instance.username} (ID: {instance.id})")
    logger.info(f"Created: {created}")
    logger.info(f"Is manager: {getattr(instance, 'is_manager', False)}")
    logger.info(f"Is superuser: {getattr(instance, 'is_superuser', False)}")
    
    try:
        is_manager = getattr(instance, 'is_manager', False)
        is_superuser = getattr(instance, 'is_superuser', False)
        
        if is_manager or is_superuser:
            logger.info(f"User {instance.id} is manager or superuser, checking voting permissions")
            
            try:
                from django.apps import apps
                VotingSession = apps.get_model('voting', 'VotingSession')
                content_type = ContentType.objects.get_for_model(VotingSession)
                logger.debug(f"Content type for VotingSession: {content_type}")
            except Exception as e:
                logger.error(f"Error getting content type: {e}")
                content_type = None
            
            permissions_to_add = []
            
            can_vote_perm = Permission.objects.filter(
                codename='can_vote',
                content_type=content_type
            ).first()
            
            if can_vote_perm:
                permissions_to_add.append(can_vote_perm)
                logger.debug(f"Found permission: can_vote")
            else:
                logger.warning("Permission 'can_vote' not found")
            
            if is_manager or is_superuser:
                can_manage_perm = Permission.objects.filter(
                    codename='can_manage_voting',
                    content_type=content_type
                ).first()
                
                if can_manage_perm:
                    permissions_to_add.append(can_manage_perm)
                    logger.debug(f"Found permission: can_manage_voting")
                else:
                    logger.warning("Permission 'can_manage_voting' not found")
            
            added_count = 0
            with transaction.atomic():
                for perm in permissions_to_add:
                    if not instance.has_perm(f'voting.{perm.codename}'):
                        instance.user_permissions.add(perm)
                        added_count += 1
                        logger.info(f"Added permission '{perm.codename}' to user {instance.id}")
                    else:
                        logger.debug(f"User {instance.id} already has permission '{perm.codename}'")
            
            if added_count > 0:
                logger.info(f"Added {added_count} voting permissions to user {instance.id}")
            else:
                logger.debug(f"No new permissions added for user {instance.id}")
                
        else:
            logger.debug(f"User {instance.id} is not manager or superuser, skipping permission assignment")
            
    except Exception as e:
        logger.error(f"Error in add_voting_permissions_for_managers: {e}\n{traceback.format_exc()}")


@receiver(post_save, sender='voting.VotingSession')
def log_voting_session_created(sender, instance, created, **kwargs):
    """
    Логирование создания и изменения сессий голосования.
    """
    try:
        if created:
            logger.info(f"New voting session created: ID={instance.id}, title='{instance.title}', "
                       f"organization_id={instance.organization_id}, created_by={instance.created_by_id}")
        else:
            if hasattr(instance, 'tracker'):
                changes = []
                tracker = instance.tracker
                
                if tracker.has_changed('status'):
                    changes.append(f"status: {tracker.previous('status')} -> {instance.status}")
                if tracker.has_changed('title'):
                    changes.append(f"title: '{tracker.previous('title')}' -> '{instance.title}'")
                if tracker.has_changed('start_date'):
                    changes.append(f"start_date: {tracker.previous('start_date')} -> {instance.start_date}")
                if tracker.has_changed('end_date'):
                    changes.append(f"end_date: {tracker.previous('end_date')} -> {instance.end_date}")
                
                if changes:
                    logger.info(f"Voting session {instance.id} updated: {', '.join(changes)}")
                    
    except Exception as e:
        logger.error(f"Error in log_voting_session_created: {e}")


@receiver(pre_save, sender='voting.Ballot')
def validate_ballot_before_save(sender, instance, **kwargs):
    """
    Валидация бюллетеня перед сохранением.
    """
    logger.debug(f"Validating ballot {instance.id if instance.id else 'new'}")
    
    try:
        if instance.voting_session and not instance.voting_session.is_active:
            logger.warning(f"Cannot save ballot for inactive voting session {instance.voting_session_id}")
            raise ValueError("Нельзя сохранить бюллетень для неактивного голосования")
        
        if instance.status == 'submitted' and not instance.id:
            existing_ballot = instance.__class__.objects.filter(
                voting_session=instance.voting_session,
                owner=instance.owner,
                status='submitted'
            ).exists()
            
            if existing_ballot:
                logger.warning(f"Owner {instance.owner_id} already voted in session {instance.voting_session_id}")
                raise ValueError("Этот владелец уже проголосовал")
                
    except Exception as e:
        logger.error(f"Error validating ballot: {e}")
        raise


@receiver(post_save, sender='voting.Vote')
def update_question_stats(sender, instance, created, **kwargs):
    """
    Обновление статистики вопросов при создании голоса.
    """
    try:
        if created:
            question = instance.question
            question.total_votes = question.votes.filter(ballot__status='submitted').count()
            question.save(update_fields=['total_votes'])
            logger.debug(f"Updated question {question.id} total_votes to {question.total_votes}")
            
            if instance.option:
                instance.option.votes_count = instance.option.votes.filter(ballot__status='submitted').count()
                if question.total_votes > 0:
                    instance.option.percentage = (instance.option.votes_count / question.total_votes * 100)
                else:
                    instance.option.percentage = 0
                instance.option.save(update_fields=['votes_count', 'percentage'])
                logger.debug(f"Updated option {instance.option.id}: votes={instance.option.votes_count}, "
                           f"percentage={instance.option.percentage:.1f}%")
                
    except Exception as e:
        logger.error(f"Error updating question stats: {e}")


@receiver(post_save, sender='voting.Ballot')
def update_voting_session_stats(sender, instance, created, **kwargs):
    """Обновление статистики сессии при создании бюллетеня"""
    try:
        if instance.status == 'submitted':
            voting_session = instance.voting_session
            total_voted = voting_session.ballots.filter(status='submitted').count()
            
            if total_voted != voting_session.total_voted:
                voting_session.total_voted = total_voted
                voting_session.save(update_fields=['total_voted'])
                logger.debug(f"Updated session {voting_session.id} total_voted to {total_voted}")
                
    except Exception as e:
        logger.error(f"Error updating voting session stats: {e}")


@receiver(pre_delete, sender='voting.Ballot')
def cleanup_ballot_votes(sender, instance, **kwargs):
    """Очистка голосов при удалении бюллетеня"""
    try:
        votes_count = instance.votes.count()
        if votes_count > 0:
            logger.info(f"Deleting {votes_count} votes with ballot {instance.id}")
    except Exception as e:
        logger.error(f"Error cleaning up ballot votes: {e}")
        

@receiver(pre_delete, sender='voting.VotingSession')
def check_voting_session_before_delete(sender, instance, **kwargs):
    """
    Проверка перед удалением сессии голосования.
    """
    logger.info(f"Checking voting session {instance.id} before delete")
    
    try:
        ballots_count = instance.ballots.count()
        if ballots_count > 0:
            logger.warning(f"Cannot delete voting session {instance.id} - has {ballots_count} ballots")
            raise ValueError(f"Нельзя удалить голосование, в котором уже есть {ballots_count} бюллетеней")
        
        questions_count = instance.questions.count()
        if questions_count > 0:
            logger.info(f"Voting session {instance.id} has {questions_count} questions that will be deleted")
            
    except Exception as e:
        logger.error(f"Error checking voting session before delete: {e}")
        raise


@receiver(post_save, sender='voting.VotingInvitation')
def log_invitation_created(sender, instance, created, **kwargs):
    """
    Логирование создания приглашений.
    """
    try:
        if created:
            logger.info(f"New invitation created: ID={instance.id}, token={instance.unique_token[:8]}..., "
                       f"owner_id={instance.owner_id}, voting_session_id={instance.voting_session_id}")
        else:
            if instance.opened_at and hasattr(instance, 'tracker') and instance.tracker.has_changed('opened_at'):
                logger.info(f"Invitation {instance.id} opened at {instance.opened_at}")
                
    except Exception as e:
        logger.error(f"Error logging invitation: {e}")


# Функция для инициализации разрешений для существующих пользователей
def initialize_voting_permissions():
    """
    Инициализация разрешений для существующих менеджеров и админов.
    Запускается один раз при миграции.
    """
    logger.info("Initializing voting permissions for existing users")
    
    try:
        from django.contrib.auth import get_user_model
        User = get_user_model()
        
        from django.apps import apps
        VotingSession = apps.get_model('voting', 'VotingSession')
        content_type = ContentType.objects.get_for_model(VotingSession)
        
        can_vote_perm = Permission.objects.filter(
            codename='can_vote',
            content_type=content_type
        ).first()
        
        can_manage_perm = Permission.objects.filter(
            codename='can_manage_voting',
            content_type=content_type
        ).first()
        
        if not can_vote_perm:
            logger.warning("Permission 'can_vote' not found, creating...")
            can_vote_perm = Permission.objects.create(
                codename='can_vote',
                name='Can vote',
                content_type=content_type
            )
        
        if not can_manage_perm:
            logger.warning("Permission 'can_manage_voting' not found, creating...")
            can_manage_perm = Permission.objects.create(
                codename='can_manage_voting',
                name='Can manage voting',
                content_type=content_type
            )
        
        users = User.objects.filter(Q(is_manager=True) | Q(is_superuser=True))
        logger.info(f"Found {users.count()} managers/superusers")
        
        updated_count = 0
        for user in users:
            with transaction.atomic():
                added = False
                
                if not user.has_perm('voting.can_vote'):
                    user.user_permissions.add(can_vote_perm)
                    added = True
                
                if (user.is_manager or user.is_superuser) and not user.has_perm('voting.can_manage_voting'):
                    user.user_permissions.add(can_manage_perm)
                    added = True
                
                if added:
                    updated_count += 1
                    logger.info(f"Added permissions to user {user.id} ({user.email})")
        
        logger.info(f"Initialization complete: {updated_count} users updated")
        
    except Exception as e:
        logger.error(f"Error initializing voting permissions: {e}\n{traceback.format_exc()}")


# ============================================================
# ИСПРАВЛЕННЫЙ СИГНАЛ - правильная модель и отложенный импорт
# ============================================================
@receiver(post_save, sender='organizations.OrganizationMembership')
def update_voting_eligible_count(sender, instance, created, **kwargs):
    """
    При изменении членства в СНТ обновляем количество избирателей в активных голосованиях.
    Используем строковую ссылку на модель, чтобы избежать циклических импортов.
    """
    from django.apps import apps
    VotingSession = apps.get_model('voting', 'VotingSession')
    
    logger.debug(f"Membership changed: owner={instance.owner_id}, org={instance.organization_id}, status={instance.status}")
    
    try:
        # Обновляем для всех активных голосований в этой организации
        active_votings = VotingSession.objects.filter(
            organization=instance.organization,
            status='active'
        )
        
        for voting in active_votings:
            # Пересчитываем количество имеющих право голоса
            from users.models import Owner
            eligible_count = Owner.objects.filter(
                memberships__organization=voting.organization,
                memberships__status='active'
            ).count()
            
            if voting.total_eligible != eligible_count:
                voting.total_eligible = eligible_count
                voting.save(update_fields=['total_eligible'])
                logger.info(f"Updated eligible count for voting {voting.id}: {eligible_count}")
                
    except Exception as e:
        logger.error(f"Error updating voting eligible count: {e}\n{traceback.format_exc()}")