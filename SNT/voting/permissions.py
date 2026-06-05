import logging
import traceback
from rest_framework import permissions

logger = logging.getLogger(__name__)


class CanManageVoting(permissions.BasePermission):
    """
    Разрешение на управление голосованиями.
    
    Доступ имеют:
    - Суперпользователи
    - Администраторы
    - Пользователи с явным правом 'voting.can_manage_voting'
    - Менеджеры своей организации
    """
    
    def has_permission(self, request, view):
        """
        Проверка разрешения на уровне запроса (не на уровне объекта).
        """
        logger.debug(f"Checking CanManageVoting.has_permission for user {request.user.id if request.user.is_authenticated else 'Anonymous'}")
        
        try:
            # Проверка аутентификации
            if not request.user.is_authenticated:
                logger.warning("User not authenticated")
                return False
            
            # Суперпользователи и админы имеют полный доступ
            if request.user.is_superuser or request.user.is_admin:
                logger.debug(f"User {request.user.id} is superuser/admin, access granted")
                return True
            
            # Проверка наличия явного разрешения
            has_perm = request.user.has_perm('voting.can_manage_voting')
            if has_perm:
                logger.debug(f"User {request.user.id} has 'can_manage_voting' permission")
                return True
            
            # Проверка, является ли пользователь менеджером организации
            if hasattr(request.user, 'is_manager') and request.user.is_manager:
                logger.debug(f"User {request.user.id} is a manager")
                return True
            
            logger.warning(f"User {request.user.id} does not have manage voting permission")
            return False
            
        except AttributeError as e:
            logger.error(f"Attribute error in has_permission: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error in has_permission: {e}\n{traceback.format_exc()}")
            return False
    
    def has_object_permission(self, request, view, obj):
        """
        Проверка разрешения на уровне объекта.
        """
        logger.debug(f"Checking CanManageVoting.has_object_permission for user {request.user.id}")
        
        try:
            # Проверка аутентификации
            if not request.user.is_authenticated:
                logger.warning("User not authenticated")
                return False
            
            # Суперпользователи и админы имеют полный доступ
            if request.user.is_superuser or request.user.is_admin:
                logger.debug(f"User {request.user.id} is superuser/admin, access granted")
                return True
            
            # Проверка наличия явного разрешения
            if request.user.has_perm('voting.can_manage_voting'):
                logger.debug(f"User {request.user.id} has 'can_manage_voting' permission")
                return True
            
            # Менеджер может управлять голосованиями только в своей организации
            if hasattr(request.user, 'is_manager') and request.user.is_manager:
                # Получаем организацию пользователя
                user_org = None
                
                # Пытаемся получить организацию из разных источников
                if hasattr(request, 'current_organization') and request.current_organization:
                    user_org = request.current_organization
                    logger.debug(f"Organization from request: {user_org.id if user_org else None}")
                elif hasattr(request.user, 'organization') and request.user.organization:
                    user_org = request.user.organization
                    logger.debug(f"Organization from user: {user_org.id if user_org else None}")
                
                if user_org and hasattr(obj, 'organization'):
                    if obj.organization == user_org:
                        logger.debug(f"User {request.user.id} is manager of organization {user_org.id}, access granted")
                        return True
                    else:
                        logger.warning(f"User's organization {user_org.id} does not match object's organization {obj.organization.id}")
                        return False
            
            logger.warning(f"User {request.user.id} does not have object permission")
            return False
            
        except AttributeError as e:
            logger.error(f"Attribute error in has_object_permission: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error in has_object_permission: {e}\n{traceback.format_exc()}")
            return False


class CanVote(permissions.BasePermission):
    """
    Разрешение на голосование.
    
    Доступ имеют:
    - Все аутентифицированные пользователи (администраторы тоже могут голосовать)
    - Дополнительно проверяется, является ли пользователь членом СНТ
    """
    
    def has_permission(self, request, view):
        """
        Проверка разрешения на уровне запроса.
        """
        logger.debug(f"Checking CanVote.has_permission for user {request.user.id if request.user.is_authenticated else 'Anonymous'}")
        
        try:
            # Проверка аутентификации
            if not request.user.is_authenticated:
                logger.warning("User not authenticated")
                return False
            
            # Все аутентифицированные пользователи могут голосовать
            # (дополнительная проверка членства в СНТ будет в view)
            logger.debug(f"User {request.user.id} is authenticated, vote permission granted at permission level")
            return True
            
        except Exception as e:
            logger.error(f"Error in CanVote.has_permission: {e}\n{traceback.format_exc()}")
            return False
    
    def has_object_permission(self, request, view, obj):
        """
        Проверка разрешения на уровне объекта.
        Проверяет, может ли пользователь голосовать в конкретном голосовании.
        """
        logger.debug(f"Checking CanVote.has_object_permission for user {request.user.id}")
        
        try:
            # Проверка аутентификации
            if not request.user.is_authenticated:
                logger.warning("User not authenticated")
                return False
            
            # Проверка, является ли пользователь членом организации
            from users.models import Owner, ContactInfo
            
            # Получаем владельца, связанного с пользователем
            owner = None
            
            # Проверяем прямую связь
            if hasattr(request.user, 'owner_profile') and request.user.owner_profile:
                owner = request.user.owner_profile
                logger.debug(f"Found owner via direct relation: {owner.id}")
            
            # Ищем по email
            if not owner and request.user.email:
                contact = ContactInfo.objects.filter(
                    type='em',
                    value=request.user.email,
                    is_active=True
                ).first()
                if contact:
                    owner = contact.owner
                    logger.debug(f"Found owner via email: {owner.id}")
            
            # Ищем по телефону
            if not owner and hasattr(request.user, 'phone') and request.user.phone:
                contact = ContactInfo.objects.filter(
                    type='ph',
                    value=request.user.phone,
                    is_active=True
                ).first()
                if contact:
                    owner = contact.owner
                    logger.debug(f"Found owner via phone: {owner.id}")
            
            if not owner:
                logger.warning(f"No owner found for user {request.user.id}")
                return False
            
            # Проверяем, является ли владелец членом организации голосования
            if hasattr(obj, 'organization'):
                is_member = owner.memberships.filter(
                    organization=obj.organization,
                    status='active'
                ).exists()
                
                if is_member:
                    logger.debug(f"Owner {owner.id} is a member of organization {obj.organization.id}, vote allowed")
                    return True
                else:
                    logger.warning(f"Owner {owner.id} is not a member of organization {obj.organization.id}")
                    return False
            
            logger.debug(f"Cannot determine organization for object, vote denied")
            return False
            
        except Exception as e:
            logger.error(f"Error in CanVote.has_object_permission: {e}\n{traceback.format_exc()}")
            return False


class CanViewVotingResults(permissions.BasePermission):
    """
    Разрешение на просмотр результатов голосования.
    
    Доступ имеют:
    - Суперпользователи
    - Администраторы
    - Пользователи с правом 'voting.can_manage_voting'
    - Менеджеры своей организации
    - Обычные пользователи могут видеть результаты только после завершения голосования
    """
    
    def has_permission(self, request, view):
        """Проверка разрешения на уровне запроса"""
        logger.debug(f"Checking CanViewVotingResults.has_permission for user {request.user.id if request.user.is_authenticated else 'Anonymous'}")
        
        try:
            if not request.user.is_authenticated:
                return False
            
            # Администраторы и менеджеры всегда могут видеть результаты
            if request.user.is_superuser or request.user.is_admin:
                return True
            
            if hasattr(request.user, 'is_manager') and request.user.is_manager:
                return True
            
            if request.user.has_perm('voting.can_manage_voting'):
                return True
            
            # Обычные пользователи могут видеть результаты только после завершения
            # Эта проверка будет в has_object_permission
            return True
            
        except Exception as e:
            logger.error(f"Error in CanViewVotingResults.has_permission: {e}")
            return False
    
    def has_object_permission(self, request, view, obj):
        """Проверка разрешения на уровне объекта"""
        logger.debug(f"Checking CanViewVotingResults.has_object_permission for user {request.user.id}")
        
        try:
            if not request.user.is_authenticated:
                return False
            
            # Администраторы и менеджеры всегда могут видеть результаты
            if request.user.is_superuser or request.user.is_admin:
                return True
            
            if hasattr(request.user, 'is_manager') and request.user.is_manager:
                # Проверка, что менеджер управляет этой организацией
                user_org = getattr(request, 'current_organization', None)
                if user_org and hasattr(obj, 'organization') and obj.organization == user_org:
                    return True
            
            if request.user.has_perm('voting.can_manage_voting'):
                return True
            
            # Для обычных пользователей - результаты доступны только после закрытия
            if hasattr(obj, 'status'):
                if obj.status == 'closed':
                    logger.debug(f"Voting session is closed, results visible to regular user")
                    return True
                else:
                    logger.debug(f"Voting session is {obj.status}, results not visible to regular user")
                    return False
            
            return False
            
        except Exception as e:
            logger.error(f"Error in CanViewVotingResults.has_object_permission: {e}")
            return False


class IsOwnerOfBallot(permissions.BasePermission):
    """
    Разрешение на доступ к бюллетеню.
    Только владелец бюллетеня может его просматривать.
    """
    
    def has_object_permission(self, request, view, obj):
        """Проверка, что пользователь является владельцем бюллетеня"""
        logger.debug(f"Checking IsOwnerOfBallot.has_object_permission for user {request.user.id}")
        
        try:
            if not request.user.is_authenticated:
                return False
            
            # Администраторы могут просматривать любые бюллетени
            if request.user.is_superuser or request.user.is_admin:
                return True
            
            # Менеджеры могут просматривать бюллетени своей организации
            if hasattr(request.user, 'is_manager') and request.user.is_manager:
                if hasattr(obj, 'voting_session') and hasattr(obj.voting_session, 'organization'):
                    user_org = getattr(request, 'current_organization', None)
                    if user_org and obj.voting_session.organization == user_org:
                        return True
            
            # Проверка, что пользователь связан с владельцем бюллетеня
            from users.models import Owner, ContactInfo
            
            owner = None
            if hasattr(request.user, 'owner_profile') and request.user.owner_profile:
                owner = request.user.owner_profile
            
            if not owner and request.user.email:
                contact = ContactInfo.objects.filter(
                    type='em',
                    value=request.user.email,
                    is_active=True
                ).first()
                if contact:
                    owner = contact.owner
            
            if owner and hasattr(obj, 'owner') and obj.owner == owner:
                logger.debug(f"User {request.user.id} is the owner of ballot {obj.id}")
                return True
            
            logger.warning(f"User {request.user.id} is not the owner of ballot {obj.id}")
            return False
            
        except Exception as e:
            logger.error(f"Error in IsOwnerOfBallot.has_object_permission: {e}")
            return False