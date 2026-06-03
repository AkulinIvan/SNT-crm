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
        if self.action in ('create', 'update', 'partial_update', 'destroy'):
            return [permissions.IsAuthenticated(), CanManageVoting()]
        if self.action in ('vote', 'my_ballot'):
            return [permissions.IsAuthenticated(), CanVote()]
        return [permissions.IsAuthenticated()]
    
    def get_serializer_class(self):
        if self.action == 'list':
            return VotingSessionListSerializer
        if self.action == 'create':
            return VotingSessionCreateSerializer
        if self.action in ('update', 'partial_update'):
            return VotingSessionCreateSerializer
        return VotingSessionDetailSerializer
    
    def get_queryset(self):
        queryset = VotingSession.objects.select_related(
            'organization', 'created_by'
        ).prefetch_related(
            'questions__options'
        )
        
        # Фильтрация по организации
        if self.request.current_organization:
            queryset = queryset.filter(organization=self.request.current_organization)
        
        # Фильтрация по статусу для обычных пользователей
        user = self.request.user
        if not (user.is_superuser or user.is_admin or user.has_perm('voting.can_manage_voting')):
            # Обычные пользователи видят только активные и завершённые
            queryset = queryset.filter(status__in=['active', 'closed'])
        
        return queryset
    
    def perform_create(self, serializer):
        voting_session = serializer.save(created_by=self.request.user)
        UserActionLog.objects.create(
            user=self.request.user,
            action='create',
            model_name='VotingSession',
            object_id=voting_session.id,
            details=f'Создано голосование: {voting_session.title}',
            ip_address=self._get_client_ip(self.request),  # ← Исправлено: передаём request
        )
    
    @action(detail=True, methods=['post'], url_path='activate')
    def activate(self, request, pk=None):
        """Активировать голосование"""
        voting_session = self.get_object()
        
        if voting_session.status != 'draft':
            return Response(
                {'detail': f'Нельзя активировать голосование со статусом {voting_session.status}'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Проверяем, что есть вопросы
        if not voting_session.questions.exists():
            return Response(
                {'detail': 'Нельзя активировать голосование без вопросов'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Рассчитываем количество имеющих право голоса
        from users.models import Owner
        eligible_owners = Owner.objects.filter(
            memberships__organization=voting_session.organization,
            memberships__status='active'
        ).count()
        
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
        
        return Response({'detail': 'Голосование активировано'})
    
    @action(detail=True, methods=['post'], url_path='close')
    def close(self, request, pk=None):
        """Закрыть голосование и рассчитать результаты"""
        voting_session = self.get_object()
        
        if voting_session.status not in ['active', 'draft']:
            return Response(
                {'detail': f'Нельзя закрыть голосование со статусом {voting_session.status}'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        voting_session.status = 'closed'
        voting_session.save()
        
        # Рассчитываем результаты
        for question in voting_session.questions.all():
            question.total_votes = Vote.objects.filter(
                question=question, 
                ballot__status='submitted'
            ).count()
            question.save()
            
            for option in question.options.all():
                option.votes_count = option.votes.filter(ballot__status='submitted').count()
                option.percentage = (option.votes_count / question.total_votes * 100) if question.total_votes > 0 else 0
                option.save()
        
        UserActionLog.objects.create(
            user=request.user,
            action='update',
            model_name='VotingSession',
            object_id=voting_session.id,
            details=f'Закрыто голосование: {voting_session.title}',
            ip_address=self._get_client_ip(request),
        )
        
        return Response({'detail': 'Голосование закрыто, результаты рассчитаны'})
    
    @action(detail=True, methods=['get'], url_path='results')
    def results(self, request, pk=None):
        """Получить результаты голосования"""
        voting_session = self.get_object()
        
        if voting_session.status not in ['closed', 'cancelled']:
            return Response(
                {'detail': 'Результаты доступны только после завершения голосования'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
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
                'participation_rate': (voting_session.total_voted / voting_session.total_eligible * 100) 
                                       if voting_session.total_eligible > 0 else 0
            })
        
        return Response(results_data)
    
    @action(detail=True, methods=['get'], url_path='ballots')
    def ballots_list(self, request, pk=None):
        """Список бюллетеней (для администраторов)"""
        voting_session = self.get_object()
        
        if not (request.user.is_superuser or request.user.is_admin or 
                request.user.has_perm('voting.can_manage_voting')):
            return Response(
                {'detail': 'Недостаточно прав'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        ballots = voting_session.ballots.select_related('owner', 'submitted_by')
        
        status_filter = request.query_params.get('status')
        if status_filter:
            ballots = ballots.filter(status=status_filter)
        
        page = self.paginate_queryset(ballots)
        if page is not None:
            serializer = BallotSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        
        serializer = BallotSerializer(ballots, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'], url_path='vote')
    def vote(self, request, pk=None):
        """Проголосовать"""
        voting_session = self.get_object()
        
        # Проверяем, активно ли голосование
        if not voting_session.is_active:
            return Response(
                {'detail': 'Голосование не активно или уже завершено'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Получаем владельца (через организацию пользователя)
        from users.models import Owner
        
        owner = None
        if hasattr(request.user, 'owner_profile'):
            owner = request.user.owner_profile
        else:
            # Ищем владельца по email или телефону пользователя
            if request.user.email:
                from users.models import ContactInfo
                contact = ContactInfo.objects.filter(
                    type='em',
                    value=request.user.email,
                    is_active=True
                ).first()
                if contact:
                    owner = contact.owner
            
            if not owner and request.user.phone:
                contact = ContactInfo.objects.filter(
                    type='ph',
                    value=request.user.phone,
                    is_active=True
                ).first()
                if contact:
                    owner = contact.owner
        
        if not owner:
            return Response(
                {'detail': 'Вы не найдены в реестре владельцев СНТ'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Проверяем, является ли владелец членом этого СНТ
        is_member = owner.memberships.filter(
            organization=voting_session.organization,
            status='active'
        ).exists()
        
        if not is_member:
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
            return Response(
                {'detail': 'Вы уже проголосовали в этом голосовании'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        votes_data = request.data.get('votes', [])
        representative_name = request.data.get('representative_name', '')
        representative_document = request.data.get('representative_document', '')
        
        # Создаём или обновляем бюллетень
        if existing_ballot:
            ballot = existing_ballot
            # Удаляем старые голоса
            ballot.votes.all().delete()
            ballot.status = 'submitted'
            ballot.representative_name = representative_name
            ballot.representative_document = representative_document
            ballot.save()
        else:
            ballot = Ballot.objects.create(
                voting_session=voting_session,
                owner=owner,
                submitted_by=request.user,
                representative_name=representative_name,
                representative_document=representative_document,
                ip_address=self._get_client_ip(request),
                user_agent=request.META.get('HTTP_USER_AGENT', ''),
                status='submitted'
            )
        
        # Обрабатываем голоса по каждому вопросу
        for vote_item in votes_data:
            question_id = vote_item.get('question_id')
            option_id = vote_item.get('option_id')
            rating_value = vote_item.get('rating_value')
            text_answer = vote_item.get('text_answer', '')
            
            try:
                question = Question.objects.get(id=question_id, voting_session=voting_session)
            except Question.DoesNotExist:
                continue
            
            if question.question_type == 'single' and option_id:
                try:
                    option = AnswerOption.objects.get(id=option_id, question=question)
                    Vote.objects.create(
                        ballot=ballot,
                        question=question,
                        option=option
                    )
                except AnswerOption.DoesNotExist:
                    pass
            
            elif question.question_type == 'rating' and rating_value:
                Vote.objects.create(
                    ballot=ballot,
                    question=question,
                    rating_value=rating_value
                )
            
            elif question.question_type == 'single' and text_answer:
                Vote.objects.create(
                    ballot=ballot,
                    question=question,
                    text_answer=text_answer
                )
        
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
        
        return Response({'detail': 'Ваш голос учтён'})
    
    @action(detail=True, methods=['get'], url_path='my-ballot')
    def my_ballot(self, request, pk=None):
        """Получить мой бюллетень"""
        voting_session = self.get_object()
        
        from users.models import Owner
        
        owner = None
        if hasattr(request.user, 'owner_profile'):
            owner = request.user.owner_profile
        else:
            if request.user.email:
                from users.models import ContactInfo
                contact = ContactInfo.objects.filter(
                    type='em',
                    value=request.user.email,
                    is_active=True
                ).first()
                if contact:
                    owner = contact.owner
        
        if not owner:
            return Response(
                {'detail': 'Владелец не найден'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        ballot = Ballot.objects.filter(
            voting_session=voting_session,
            owner=owner
        ).first()
        
        if not ballot:
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
        
        return Response({
            'has_voted': ballot.status == 'submitted',
            'status': ballot.status,
            'submitted_at': ballot.submitted_at,
            'representative_name': ballot.representative_name,
            'votes': votes_data
        })
    
    @action(detail=True, methods=['post'], url_path='send-invitations')
    def send_invitations(self, request, pk=None):
        """Отправить приглашения на голосование"""
        voting_session = self.get_object()
        
        if not (request.user.is_superuser or request.user.is_admin or 
                request.user.has_perm('voting.can_manage_voting')):
            return Response(
                {'detail': 'Недостаточно прав'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        from users.models import Owner
        
        invitation_type = request.data.get('invitation_type', 'email')
        
        # Получаем всех активных членов СНТ
        owners = Owner.objects.filter(
            memberships__organization=voting_session.organization,
            memberships__status='active'
        )
        
        invitations_created = 0
        
        for owner in owners:
            if invitation_type == 'email' and owner.primary_email:
                contact_value = owner.primary_email
            elif invitation_type == 'sms' and owner.primary_phone:
                contact_value = owner.primary_phone
            else:
                continue
            
            invitation, created = VotingInvitation.objects.get_or_create(
                voting_session=voting_session,
                owner=owner,
                invitation_type=invitation_type,
                defaults={'contact_value': contact_value}
            )
            
            if created:
                invitations_created += 1
                # Здесь можно добавить реальную отправку email/SMS
                # send_invitation_email(invitation)
        
        return Response({
            'detail': f'Создано {invitations_created} приглашений',
            'total': owners.count()
        })
    
    @action(detail=False, methods=['get'], url_path='public/(?P<token>[^/.]+)')
    def public_vote(self, request, token=None):
        """Публичная страница голосования по токену"""
        try:
            invitation = VotingInvitation.objects.get(unique_token=token)
        except VotingInvitation.DoesNotExist:
            return Response(
                {'detail': 'Недействительная ссылка'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        voting_session = invitation.voting_session
        
        if not voting_session.is_active:
            return Response(
                {'detail': 'Голосование не активно'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Отмечаем, что приглашение открыто
        if not invitation.opened_at:
            invitation.opened_at = timezone.now()
            invitation.save()
        
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
    
    def _get_client_ip(self, request):
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR')


class QuestionViewSet(viewsets.ModelViewSet):
    """ViewSet для управления вопросами"""
    serializer_class = QuestionSerializer
    permission_classes = [permissions.IsAuthenticated, CanManageVoting]
    
    def get_queryset(self):
        if self.request.current_organization:
            return Question.objects.filter(
                voting_session__organization=self.request.current_organization
            )
        return Question.objects.none()


class AnswerOptionViewSet(viewsets.ModelViewSet):
    """ViewSet для управления вариантами ответов"""
    serializer_class = AnswerOptionSerializer
    permission_classes = [permissions.IsAuthenticated, CanManageVoting]
    
    def get_queryset(self):
        if self.request.current_organization:
            return AnswerOption.objects.filter(
                question__voting_session__organization=self.request.current_organization
            )
        return AnswerOption.objects.none()