import logging
import traceback
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter

from common.mixins import OrganizationMixin
from accounts.permissions import IsManagerOrAbove
from accounts.models import UserActionLog
from .models import (
    VotingSession, Question, AnswerOption, 
    Ballot, Vote, VotingInvitation
)
from .serializers import (
    VotingSessionListSerializer, VotingSessionDetailSerializer,
    VotingSessionCreateSerializer, QuestionSerializer,
    AnswerOptionSerializer, BallotSerializer, VoteSubmitSerializer,
    VotingInvitationSerializer
)
from .permissions import CanManageVoting, CanVote

logger = logging.getLogger(__name__)


class VotingSessionViewSet(OrganizationMixin, viewsets.ModelViewSet):
    """
    ViewSet для управления сессиями голосования
    """
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['status', 'session_type', 'organization']
    search_fields = ['title', 'description']
    ordering_fields = ['created_at', 'start_date', 'end_date', 'status']
    ordering = ['-created_at']
    
    def get_permissions(self):
        try:
            if self.action in ('create', 'update', 'partial_update', 'destroy'):
                return [permissions.IsAuthenticated(), CanManageVoting()]
            if self.action in ('vote', 'my_ballot'):
                return [permissions.IsAuthenticated(), CanVote()]
            return [permissions.IsAuthenticated()]
        except Exception as e:
            logger.error(f"Error in get_permissions: {e}")
            return [permissions.IsAuthenticated()]
    
    def get_serializer_class(self):
        try:
            if self.action == 'list':
                return VotingSessionListSerializer
            if self.action == 'create':
                return VotingSessionCreateSerializer
            if self.action in ('update', 'partial_update'):
                return VotingSessionCreateSerializer
            return VotingSessionDetailSerializer
        except Exception as e:
            logger.error(f"Error in get_serializer_class: {e}")
            return VotingSessionDetailSerializer
    
    def get_queryset(self):
        try:
            logger.info("=" * 50)
            logger.info("VotingSessionViewSet.get_queryset START")
            logger.info(f"User: {self.request.user} (ID: {self.request.user.id if self.request.user.is_authenticated else 'Anonymous'})")
            
            # Базовый запрос
            queryset = VotingSession.objects.select_related(
                'organization', 'created_by'
            ).prefetch_related(
                'questions__options'
            )
            
            logger.debug(f"Base queryset count: {queryset.count()}")
            
            # Фильтрация по организации
            if hasattr(self.request, 'current_organization') and self.request.current_organization:
                org = self.request.current_organization
                queryset = queryset.filter(organization=org)
                logger.info(f"Filtered by organization {org.id}: {queryset.count()} sessions")
            else:
                logger.warning("No current_organization in request")
                return VotingSession.objects.none()
            
            # Фильтрация по статусу для обычных пользователей
            user = self.request.user
            if user.is_authenticated:
                is_admin = user.is_superuser or user.is_admin or user.has_perm('voting.can_manage_voting')
                logger.info(f"Is admin/manager: {is_admin}")
                
                if not is_admin:
                    queryset = queryset.filter(status__in=['active', 'closed'])
                    logger.info(f"After status filter (regular user): {queryset.count()} sessions")
            
            logger.info(f"Final queryset count: {queryset.count()}")
            return queryset
            
        except Exception as e:
            logger.error(f"ERROR in get_queryset: {str(e)}\n{traceback.format_exc()}")
            return VotingSession.objects.none()
    
    def perform_create(self, serializer):
        try:
            logger.info(f"User {self.request.user.id} creating voting session")
            voting_session = serializer.save(created_by=self.request.user)
            
            UserActionLog.objects.create(
                user=self.request.user,
                action='create',
                model_name='VotingSession',
                object_id=voting_session.id,
                details=f'Создано голосование: {voting_session.title}',
                ip_address=self._get_client_ip(self.request),
            )
            logger.info(f"Voting session created: ID={voting_session.id}, title={voting_session.title}")
        except Exception as e:
            logger.error(f"Error in perform_create: {e}\n{traceback.format_exc()}")
            raise
    
    @action(detail=True, methods=['post'], url_path='activate')
    def activate(self, request, pk=None):
        """Активировать голосование"""
        logger.info(f"User {request.user.id} activating voting session {pk}")
        
        try:
            voting_session = self.get_object()
            
            if voting_session.status != 'draft':
                logger.warning(f"Cannot activate voting session with status {voting_session.status}")
                return Response(
                    {'detail': f'Нельзя активировать голосование со статусом {voting_session.status}'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Проверяем, что есть вопросы
            questions_count = voting_session.questions.count()
            if questions_count == 0:
                logger.warning(f"Cannot activate voting session without questions")
                return Response(
                    {'detail': 'Нельзя активировать голосование без вопросов'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            logger.debug(f"Voting session has {questions_count} questions")
            
            # Рассчитываем количество имеющих право голоса
            from users.models import Owner
            eligible_owners = Owner.objects.filter(
                memberships__organization=voting_session.organization,
                memberships__status='active'
            ).count()
            
            logger.info(f"Eligible owners count: {eligible_owners}")
            
            voting_session.total_eligible = eligible_owners
            voting_session.status = 'active'
            voting_session.save()
            
            UserActionLog.objects.create(
                user=request.user,
                action='update',
                model_name='VotingSession',
                object_id=voting_session.id,
                details=f'Активировано голосование: {voting_session.title}',
                ip_address=self._get_client_ip(request),
            )
            
            logger.info(f"Voting session {pk} activated successfully")
            return Response({'detail': 'Голосование активировано'})
            
        except Exception as e:
            logger.error(f"Error activating voting session: {e}\n{traceback.format_exc()}")
            return Response(
                {'detail': f'Ошибка активации: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['post'], url_path='close')
    def close(self, request, pk=None):
        """Закрыть голосование и рассчитать результаты"""
        logger.info(f"User {request.user.id} closing voting session {pk}")
        
        try:
            voting_session = self.get_object()
            
            if voting_session.status not in ['active', 'draft']:
                logger.warning(f"Cannot close voting session with status {voting_session.status}")
                return Response(
                    {'detail': f'Нельзя закрыть голосование со статусом {voting_session.status}'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            voting_session.status = 'closed'
            voting_session.save()
            logger.info(f"Voting session status changed to 'closed'")
            
            # Рассчитываем результаты
            for question in voting_session.questions.all():
                total_votes = Vote.objects.filter(
                    question=question, 
                    ballot__status='submitted'
                ).count()
                question.total_votes = total_votes
                question.save()
                logger.debug(f"Question {question.id}: total_votes={total_votes}")
                
                for option in question.options.all():
                    option.votes_count = option.votes.filter(ballot__status='submitted').count()
                    option.percentage = (option.votes_count / total_votes * 100) if total_votes > 0 else 0
                    option.save()
                    logger.debug(f"Option {option.id}: votes={option.votes_count}, percentage={option.percentage:.1f}%")
            
            UserActionLog.objects.create(
                user=request.user,
                action='update',
                model_name='VotingSession',
                object_id=voting_session.id,
                details=f'Закрыто голосование: {voting_session.title}',
                ip_address=self._get_client_ip(request),
            )
            
            logger.info(f"Voting session {pk} closed successfully")
            return Response({'detail': 'Голосование закрыто, результаты рассчитаны'})
            
        except Exception as e:
            logger.error(f"Error closing voting session: {e}\n{traceback.format_exc()}")
            return Response(
                {'detail': f'Ошибка закрытия: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['get'], url_path='results')
    def results(self, request, pk=None):
        """Получить результаты голосования"""
        logger.info(f"User {request.user.id} requesting results for voting session {pk}")
        
        try:
            voting_session = self.get_object()
            
            if voting_session.status not in ['closed', 'cancelled']:
                logger.warning(f"Results not available for status {voting_session.status}")
                return Response(
                    {'detail': 'Результаты доступны только после завершения голосования'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            participation_rate = 0
            if voting_session.total_eligible > 0:
                participation_rate = (voting_session.total_voted / voting_session.total_eligible * 100)
            
            results_data = {
                'voting_session': VotingSessionDetailSerializer(voting_session).data,
                'questions': []
            }
            
            for question in voting_session.questions.all():
                options_results = []
                for option in question.options.all():
                    options_results.append({
                        'id': option.id,
                        'text': option.text,
                        'votes_count': option.votes_count,
                        'percentage': float(option.percentage),
                        'order': option.order
                    })
                
                results_data['questions'].append({
                    'id': question.id,
                    'title': question.title,
                    'description': question.description,
                    'question_type': question.question_type,
                    'total_votes': question.total_votes,
                    'options': options_results,
                    'participation_rate': participation_rate
                })
            
            logger.info(f"Results retrieved for voting session {pk}")
            return Response(results_data)
            
        except Exception as e:
            logger.error(f"Error getting results: {e}\n{traceback.format_exc()}")
            return Response(
                {'detail': f'Ошибка получения результатов: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['get'], url_path='ballots')
    def ballots_list(self, request, pk=None):
        """Список бюллетеней (для администраторов)"""
        logger.info(f"User {request.user.id} requesting ballots for voting session {pk}")
        
        try:
            voting_session = self.get_object()
            
            # Проверка прав
            if not (request.user.is_superuser or request.user.is_admin or 
                    request.user.has_perm('voting.can_manage_voting')):
                logger.warning(f"User {request.user.id} has no permission to view ballots")
                return Response(
                    {'detail': 'Недостаточно прав'},
                    status=status.HTTP_403_FORBIDDEN
                )
            
            ballots = voting_session.ballots.select_related('owner', 'submitted_by')
            
            status_filter = request.query_params.get('status')
            if status_filter:
                ballots = ballots.filter(status=status_filter)
                logger.debug(f"Filtered by status: {status_filter}")
            
            ballots_count = ballots.count()
            logger.info(f"Found {ballots_count} ballots")
            
            page = self.paginate_queryset(ballots)
            if page is not None:
                serializer = BallotSerializer(page, many=True)
                return self.get_paginated_response(serializer.data)
            
            serializer = BallotSerializer(ballots, many=True)
            return Response(serializer.data)
            
        except Exception as e:
            logger.error(f"Error getting ballots list: {e}\n{traceback.format_exc()}")
            return Response(
                {'detail': f'Ошибка получения списка бюллетеней: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['post'], url_path='vote')
    def vote(self, request, pk=None):
        """Проголосовать"""
        logger.info(f"User {request.user.id} voting in session {pk}")
        
        try:
            voting_session = self.get_object()
            
            # Проверяем, активно ли голосование
            if not voting_session.is_active:
                logger.warning(f"Voting session {pk} is not active")
                return Response(
                    {'detail': 'Голосование не активно или уже завершено'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Получаем владельца
            owner = self._get_owner_from_user(request.user, voting_session.organization)
            
            if not owner:
                logger.warning(f"User {request.user.id} not found in owners registry")
                return Response(
                    {'detail': 'Вы не найдены в реестре владельцев СНТ'},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            logger.debug(f"Owner found: ID={owner.id}, name={owner.full_name}")
            
            # Проверяем, является ли владелец членом этого СНТ
            is_member = owner.memberships.filter(
                organization=voting_session.organization,
                status='active'
            ).exists()
            
            if not is_member:
                logger.warning(f"Owner {owner.id} is not a member of organization {voting_session.organization.id}")
                return Response(
                    {'detail': 'Вы не являетесь членом этого СНТ'},
                    status=status.HTTP_403_FORBIDDEN
                )
            
            # Проверяем, не голосовал ли уже
            existing_ballot = Ballot.objects.filter(
                voting_session=voting_session,
                owner=owner
            ).first()
            
            if existing_ballot and existing_ballot.status == 'submitted':
                logger.warning(f"Owner {owner.id} has already voted")
                return Response(
                    {'detail': 'Вы уже проголосовали в этом голосовании'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            votes_data = request.data.get('votes', [])
            representative_name = request.data.get('representative_name', '')
            representative_document = request.data.get('representative_document', '')
            
            logger.debug(f"Votes data count: {len(votes_data)}")
            if representative_name or representative_document:
                # Проверить, что представитель выбран общим собранием
                # Или загрузить документ-доверенность
                if not representative_document:
                    return Response(
                        {'detail': 'Для голосования через представителя нужен документ'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            # Создаём или обновляем бюллетень
            if existing_ballot:
                ballot = existing_ballot
                # Удаляем старые голоса
                ballot.votes.all().delete()
                ballot.status = 'submitted'
                ballot.representative_name = representative_name
                ballot.representative_document = representative_document
                ballot.save()
                logger.info(f"Updated existing ballot {ballot.id}")
            else:
                ballot = Ballot.objects.create(
                    voting_session=voting_session,
                    owner=owner,
                    submitted_by=request.user,
                    representative_name=representative_name,
                    representative_document=representative_document,
                    ip_address=self._get_client_ip(request),
                    user_agent=request.META.get('HTTP_USER_AGENT', '')[:255],
                    status='submitted'
                )
                logger.info(f"Created new ballot {ballot.id}")
            
            # Обрабатываем голоса по каждому вопросу
            votes_created = 0
            for vote_item in votes_data:
                try:
                    question_id = vote_item.get('question_id')
                    option_id = vote_item.get('option_id')
                    rating_value = vote_item.get('rating_value')
                    text_answer = vote_item.get('text_answer', '')
                    
                    try:
                        question = Question.objects.get(id=question_id, voting_session=voting_session)
                    except Question.DoesNotExist:
                        logger.warning(f"Question {question_id} not found in voting session {pk}")
                        continue
                    
                    if question.question_type == 'single' and option_id:
                        try:
                            option = AnswerOption.objects.get(id=option_id, question=question)
                            Vote.objects.create(
                                ballot=ballot,
                                question=question,
                                option=option
                            )
                            votes_created += 1
                        except AnswerOption.DoesNotExist:
                            logger.warning(f"Option {option_id} not found for question {question_id}")
                    
                    elif question.question_type == 'rating' and rating_value:
                        Vote.objects.create(
                            ballot=ballot,
                            question=question,
                            rating_value=rating_value
                        )
                        votes_created += 1
                    
                    elif question.question_type == 'single' and text_answer:
                        Vote.objects.create(
                            ballot=ballot,
                            question=question,
                            text_answer=text_answer
                        )
                        votes_created += 1
                        
                except Exception as e:
                    logger.error(f"Error processing vote item: {e}")
                    continue
            
            logger.info(f"Created {votes_created} votes for ballot {ballot.id}")
            
            # Обновляем статистику
            voting_session.total_voted = Ballot.objects.filter(
                voting_session=voting_session,
                status='submitted'
            ).count()
            voting_session.save()
            
            # Обновляем количество голосов по вопросам
            for question in voting_session.questions.all():
                question.total_votes = Vote.objects.filter(question=question, ballot__status='submitted').count()
                question.save()
            
            UserActionLog.objects.create(
                user=request.user,
                action='create',
                model_name='Ballot',
                object_id=ballot.id,
                details=f'Голосование в "{voting_session.title}" от {owner.full_name}',
                ip_address=self._get_client_ip(request),
            )
            
            logger.info(f"Vote completed successfully for user {request.user.id}")
            return Response({'detail': 'Ваш голос учтён'})
            
        except Exception as e:
            logger.error(f"Error in vote: {e}\n{traceback.format_exc()}")
            return Response(
                {'detail': f'Ошибка при голосовании: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['get'], url_path='my-ballot')
    def my_ballot(self, request, pk=None):
        """Получить мой бюллетень"""
        logger.info(f"User {request.user.id} requesting my ballot for session {pk}")
        
        try:
            voting_session = self.get_object()
            
            owner = self._get_owner_from_user(request.user, voting_session.organization)
            
            if not owner:
                logger.warning(f"Owner not found for user {request.user.id}")
                return Response(
                    {'detail': 'Владелец не найден'},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            ballot = Ballot.objects.filter(
                voting_session=voting_session,
                owner=owner
            ).first()
            
            if not ballot:
                logger.info(f"No ballot found for owner {owner.id}")
                return Response({'has_voted': False})
            
            # Получаем голоса пользователя
            votes_data = []
            for vote in ballot.votes.select_related('question', 'option'):
                votes_data.append({
                    'question_id': vote.question.id,
                    'question_title': vote.question.title,
                    'option_id': vote.option.id if vote.option else None,
                    'option_text': vote.option.text if vote.option else None,
                    'rating_value': vote.rating_value,
                    'text_answer': vote.text_answer
                })
            
            logger.info(f"Returning ballot {ballot.id} for owner {owner.id}")
            return Response({
                'has_voted': ballot.status == 'submitted',
                'status': ballot.status,
                'submitted_at': ballot.submitted_at,
                'representative_name': ballot.representative_name,
                'votes': votes_data
            })
            
        except Exception as e:
            logger.error(f"Error getting my ballot: {e}\n{traceback.format_exc()}")
            return Response(
                {'detail': f'Ошибка получения бюллетеня: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['post'], url_path='send-invitations')
    def send_invitations(self, request, pk=None):
        """Отправить приглашения на голосование"""
        logger.info(f"User {request.user.id} sending invitations for session {pk}")
        
        try:
            voting_session = self.get_object()
            
            # Проверка прав
            if not (request.user.is_superuser or request.user.is_admin or 
                    request.user.has_perm('voting.can_manage_voting')):
                logger.warning(f"User {request.user.id} has no permission to send invitations")
                return Response(
                    {'detail': 'Недостаточно прав'},
                    status=status.HTTP_403_FORBIDDEN
                )
            
            from users.models import Owner
            
            invitation_type = request.data.get('invitation_type', 'email')
            logger.debug(f"Invitation type: {invitation_type}")
            
            # Получаем всех активных членов СНТ
            owners = Owner.objects.filter(
                memberships__organization=voting_session.organization,
                memberships__status='active'
            )
            
            owners_count = owners.count()
            logger.info(f"Found {owners_count} eligible owners")
            
            invitations_created = 0
            errors = []
            
            for owner in owners:
                try:
                    if invitation_type == 'email' and owner.primary_email:
                        contact_value = owner.primary_email
                    elif invitation_type == 'sms' and owner.primary_phone:
                        contact_value = owner.primary_phone
                    else:
                        logger.debug(f"Owner {owner.id} has no {invitation_type} contact")
                        continue
                    
                    invitation, created = VotingInvitation.objects.get_or_create(
                        voting_session=voting_session,
                        owner=owner,
                        invitation_type=invitation_type,
                        defaults={'contact_value': contact_value}
                    )
                    
                    if created:
                        invitations_created += 1
                        logger.debug(f"Created invitation for owner {owner.id}")
                        
                        # Здесь можно добавить реальную отправку email/SMS
                        # self._send_invitation(invitation)
                        
                except Exception as e:
                    logger.error(f"Error creating invitation for owner {owner.id}: {e}")
                    errors.append(f"Owner {owner.id}: {str(e)}")
            
            response_data = {
                'detail': f'Создано {invitations_created} приглашений из {owners_count}',
                'created': invitations_created,
                'total': owners_count
            }
            
            if errors:
                response_data['errors'] = errors[:10]  # Первые 10 ошибок
            
            logger.info(f"Invitations created: {invitations_created}/{owners_count}")
            return Response(response_data)
            
        except Exception as e:
            logger.error(f"Error sending invitations: {e}\n{traceback.format_exc()}")
            return Response(
                {'detail': f'Ошибка отправки приглашений: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['get'], url_path='public/(?P<token>[^/.]+)')
    def public_vote(self, request, token=None):
        """Публичная страница голосования по токену"""
        logger.info(f"Public vote access with token: {token}")
        
        try:
            invitation = VotingInvitation.objects.get(unique_token=token)
            logger.debug(f"Invitation found for owner {invitation.owner.id}")
            
        except VotingInvitation.DoesNotExist:
            logger.warning(f"Invalid invitation token: {token}")
            return Response(
                {'detail': 'Недействительная ссылка'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        try:
            voting_session = invitation.voting_session
            
            if not voting_session.is_active:
                logger.warning(f"Voting session {voting_session.id} is not active")
                return Response(
                    {'detail': 'Голосование не активно'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Отмечаем, что приглашение открыто
            if not invitation.opened_at:
                invitation.opened_at = timezone.now()
                invitation.save()
                logger.debug(f"Invitation opened at {invitation.opened_at}")
            
            # Проверяем, не голосовал ли уже
            has_voted = Ballot.objects.filter(
                voting_session=voting_session,
                owner=invitation.owner,
                status='submitted'
            ).exists()
            
            questions_data = QuestionSerializer(
                voting_session.questions.all(),
                many=True
            ).data
            
            logger.info(f"Public vote data returned for token {token}")
            
            return Response({
                'voting_session': {
                    'id': voting_session.id,
                    'title': voting_session.title,
                    'description': voting_session.description,
                    'end_date': voting_session.end_date,
                    'organization': voting_session.organization.short_name
                },
                'owner': {
                    'id': invitation.owner.id,
                    'full_name': invitation.owner.full_name
                },
                'questions': questions_data,
                'has_voted': has_voted,
                'token': token
            })
            
        except Exception as e:
            logger.error(f"Error in public_vote: {e}\n{traceback.format_exc()}")
            return Response(
                {'detail': f'Ошибка: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    def _get_client_ip(self, request):
        """Получение IP адреса клиента"""
        try:
            x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
            if x_forwarded_for:
                return x_forwarded_for.split(',')[0].strip()
            return request.META.get('REMOTE_ADDR', '')
        except Exception:
            return ''
    
    def _get_owner_from_user(self, user, organization=None):
        """Получение владельца из пользователя"""
        try:
            from users.models import Owner, ContactInfo
            
            # Проверяем, есть ли прямая связь
            if hasattr(user, 'owner_profile') and user.owner_profile:
                return user.owner_profile
            
            # Ищем по email
            if user.email:
                contact = ContactInfo.objects.filter(
                    type='em',
                    value=user.email,
                    is_active=True
                ).first()
                if contact:
                    return contact.owner
            
            # Ищем по телефону
            if hasattr(user, 'phone') and user.phone:
                contact = ContactInfo.objects.filter(
                    type='ph',
                    value=user.phone,
                    is_active=True
                ).first()
                if contact:
                    return contact.owner
            
            return None
            
        except Exception as e:
            logger.error(f"Error getting owner from user: {e}")
            return None


class QuestionViewSet(viewsets.ModelViewSet):
    """ViewSet для управления вопросами"""
    serializer_class = QuestionSerializer
    permission_classes = [permissions.IsAuthenticated, CanManageVoting]
    
    def get_queryset(self):
        try:
            if self.request.current_organization:
                return Question.objects.filter(
                    voting_session__organization=self.request.current_organization
                )
            return Question.objects.none()
        except Exception as e:
            logger.error(f"Error in QuestionViewSet.get_queryset: {e}")
            return Question.objects.none()


class AnswerOptionViewSet(viewsets.ModelViewSet):
    """ViewSet для управления вариантами ответов"""
    serializer_class = AnswerOptionSerializer
    permission_classes = [permissions.IsAuthenticated, CanManageVoting]
    
    def get_queryset(self):
        try:
            if self.request.current_organization:
                return AnswerOption.objects.filter(
                    question__voting_session__organization=self.request.current_organization
                )
            return AnswerOption.objects.none()
        except Exception as e:
            logger.error(f"Error in AnswerOptionViewSet.get_queryset: {e}")
            return AnswerOption.objects.none()