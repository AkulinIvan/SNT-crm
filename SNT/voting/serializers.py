import logging
import traceback
from rest_framework import serializers
from django.utils import timezone
from .models import (
    VotingSession, Question, AnswerOption, 
    Ballot, Vote, VotingInvitation
)

logger = logging.getLogger(__name__)


class AnswerOptionSerializer(serializers.ModelSerializer):
    """Сериализатор варианта ответа"""
    
    class Meta:
        model = AnswerOption
        fields = ['id', 'text', 'order', 'votes_count', 'percentage']

    def validate(self, data):
        """Валидация варианта ответа"""
        logger.debug(f"Validating answer option: {data.get('text', '')[:50]}")
        
        try:
            if 'text' in data and not data['text'].strip():
                raise serializers.ValidationError({'text': 'Текст варианта ответа не может быть пустым'})
            
            if 'order' in data and data['order'] < 0:
                raise serializers.ValidationError({'order': 'Порядок должен быть неотрицательным числом'})
            
            return data
        except Exception as e:
            logger.error(f"Error validating answer option: {e}")
            raise

    def create(self, validated_data):
        """Создание варианта ответа с логированием"""
        logger.info(f"Creating answer option: {validated_data.get('text', '')[:50]}")
        
        try:
            option = super().create(validated_data)
            logger.info(f"Answer option created: ID={option.id}, question_id={option.question_id}")
            return option
        except Exception as e:
            logger.error(f"Error creating answer option: {e}\n{traceback.format_exc()}")
            raise


class QuestionSerializer(serializers.ModelSerializer):
    """Сериализатор вопроса"""
    options = AnswerOptionSerializer(many=True, read_only=True)
    
    class Meta:
        model = Question
        fields = ['id', 'title', 'description', 'question_type', 'order', 'total_votes', 'options']

    def validate(self, data):
        """Валидация вопроса"""
        logger.debug(f"Validating question: {data.get('title', '')[:50]}")
        
        try:
            if 'title' in data and not data['title'].strip():
                raise serializers.ValidationError({'title': 'Заголовок вопроса не может быть пустым'})
            
            if 'question_type' in data:
                valid_types = ['single', 'multiple', 'rating', 'text']
                if data['question_type'] not in valid_types:
                    raise serializers.ValidationError({
                        'question_type': f'Неверный тип вопроса. Допустимые значения: {", ".join(valid_types)}'
                    })
            
            return data
        except Exception as e:
            logger.error(f"Error validating question: {e}")
            raise

    def create(self, validated_data):
        """Создание вопроса с логированием"""
        logger.info(f"Creating question: {validated_data.get('title', '')[:50]}")
        
        try:
            question = super().create(validated_data)
            logger.info(f"Question created: ID={question.id}, voting_session_id={question.voting_session_id}")
            return question
        except Exception as e:
            logger.error(f"Error creating question: {e}\n{traceback.format_exc()}")
            raise


class QuestionCreateSerializer(serializers.ModelSerializer):
    """Сериализатор для создания вопроса с вариантами"""
    options = serializers.ListField(
        child=serializers.CharField(max_length=200),
        write_only=True,
        required=False
    )
    
    class Meta:
        model = Question
        fields = ['title', 'description', 'question_type', 'order', 'options']
    
    def validate(self, data):
        """Валидация вопроса с вариантами ответов"""
        logger.debug(f"Validating question creation: {data.get('title', '')[:50]}")
        
        try:
            if 'title' in data and not data['title'].strip():
                raise serializers.ValidationError({'title': 'Заголовок вопроса не может быть пустым'})
            
            question_type = data.get('question_type')
            options = data.get('options', [])
            
            # Для типов 'single' и 'multiple' должны быть варианты ответов
            if question_type in ['single', 'multiple'] and not options:
                raise serializers.ValidationError({
                    'options': f'Для типа вопроса "{question_type}" необходимо указать варианты ответов'
                })
            
            # Для типа 'rating' варианты не нужны
            if question_type == 'rating' and options:
                logger.warning(f"Rating question has {len(options)} options, ignoring")
                data['options'] = []
            
            # Проверка уникальности вариантов
            if options and len(options) != len(set(options)):
                raise serializers.ValidationError({
                    'options': 'Варианты ответов должны быть уникальными'
                })
            
            return data
        except Exception as e:
            logger.error(f"Error validating question creation: {e}")
            raise
    
    def create(self, validated_data):
        """Создание вопроса с вариантами ответов"""
        logger.info(f"Creating question with {len(validated_data.get('options', []))} options")
        
        try:
            options_data = validated_data.pop('options', [])
            question = Question.objects.create(**validated_data)
            logger.debug(f"Question created: ID={question.id}")
            
            for idx, option_text in enumerate(options_data):
                try:
                    option = AnswerOption.objects.create(
                        question=question,
                        text=option_text,
                        order=idx
                    )
                    logger.debug(f"Option created: ID={option.id}, text={option_text[:30]}")
                except Exception as e:
                    logger.error(f"Error creating option {idx}: {e}")
                    raise
            
            logger.info(f"Question {question.id} created with {len(options_data)} options")
            return question
            
        except Exception as e:
            logger.error(f"Error in QuestionCreateSerializer.create: {e}\n{traceback.format_exc()}")
            raise


class VotingSessionListSerializer(serializers.ModelSerializer):
    """Краткий сериализатор для списка голосований"""
    organization_name = serializers.CharField(source='organization.short_name', read_only=True)
    quorum_reached = serializers.BooleanField(read_only=True)
    days_remaining = serializers.IntegerField(read_only=True)
    is_active = serializers.BooleanField(read_only=True)
    
    class Meta:
        model = VotingSession
        fields = [
            'id', 'title', 'organization_name', 'session_type', 'status',
            'start_date', 'end_date', 'total_eligible', 'total_voted',
            'quorum_reached', 'days_remaining', 'is_active', 'created_at'
        ]

    def to_representation(self, instance):
        """Кастомное представление с дополнительной информацией"""
        try:
            data = super().to_representation(instance)
            
            # Добавляем процент участия
            if instance.total_eligible > 0:
                data['participation_percent'] = round(
                    (instance.total_voted / instance.total_eligible) * 100, 1
                )
            else:
                data['participation_percent'] = 0
            
            # Добавляем статус кворума текстом
            if instance.quorum_reached:
                data['quorum_status'] = 'Достигнут'
            else:
                required = int((instance.total_eligible * instance.quorum_percent) / 100) if instance.total_eligible > 0 else 0
                remaining = max(0, required - instance.total_voted)
                data['quorum_status'] = f'Не достигнут (осталось {remaining})'
            
            return data
        except Exception as e:
            logger.error(f"Error in to_representation: {e}")
            return data


class VotingSessionDetailSerializer(serializers.ModelSerializer):
    """Полный сериализатор голосования"""
    questions = QuestionSerializer(many=True, read_only=True)
    organization_name = serializers.CharField(source='organization.short_name', read_only=True)
    created_by_name = serializers.CharField(source='created_by.full_name', read_only=True)
    quorum_reached = serializers.BooleanField(read_only=True)
    quorum_required = serializers.SerializerMethodField()
    
    class Meta:
        model = VotingSession
        fields = [
            'id', 'organization', 'organization_name', 'title', 'description',
            'session_type', 'status', 'start_date', 'end_date',
            'quorum_percent', 'quorum_required', 'quorum_reached',
            'total_eligible', 'total_voted', 'meeting_place',
            'protocol_number', 'protocol_date', 'questions',
            'created_by', 'created_by_name', 'created_at', 'updated_at',
            'days_remaining', 'is_active', 'is_closed'
        ]
        read_only_fields = ['created_at', 'updated_at', 'created_by']
    
    def get_quorum_required(self, obj):
        """Необходимое количество голосов для кворума"""
        try:
            if obj.total_eligible == 0:
                return 0
            return int((obj.total_eligible * obj.quorum_percent) / 100)
        except Exception as e:
            logger.error(f"Error calculating quorum required: {e}")
            return 0

    def to_representation(self, instance):
        """Кастомное представление с дополнительными вычислениями"""
        try:
            data = super().to_representation(instance)
            
            # Добавляем прогресс по каждому вопросу
            for question_data in data.get('questions', []):
                question = instance.questions.filter(id=question_data['id']).first()
                if question:
                    answered = question.votes.filter(ballot__status='submitted').count()
                    question_data['answered_count'] = answered
                    question_data['answered_percent'] = round(
                        (answered / instance.total_voted * 100) if instance.total_voted > 0 else 0, 1
                    )
            
            return data
        except Exception as e:
            logger.error(f"Error in VotingSessionDetailSerializer.to_representation: {e}")
            return data


class VotingSessionCreateSerializer(serializers.ModelSerializer):
    """Сериализатор для создания и обновления голосования"""
    questions = QuestionCreateSerializer(many=True, required=False)
    
    class Meta:
        model = VotingSession
        fields = [
            'organization', 'title', 'description', 'session_type',
            'start_date', 'end_date', 'quorum_percent', 'meeting_place',
            'questions'
        ]
    
    def validate(self, data):
        """Валидация дат и параметров голосования"""
        logger.debug(f"Validating voting session creation: {data.get('title', '')[:50]}")
        
        try:
            # Валидация дат
            if 'start_date' in data and 'end_date' in data:
                start_date = data['start_date']
                end_date = data['end_date']
                
                if start_date >= end_date:
                    raise serializers.ValidationError({
                        'end_date': 'Дата окончания должна быть позже даты начала'
                    })
                
                if start_date < timezone.now():
                    raise serializers.ValidationError({
                        'start_date': 'Дата начала не может быть в прошлом'
                    })
            
            # Валидация процента кворума
            if 'quorum_percent' in data:
                quorum = data['quorum_percent']
                if quorum < 0 or quorum > 100:
                    raise serializers.ValidationError({
                        'quorum_percent': 'Процент кворума должен быть от 0 до 100'
                    })
            
            # Проверка вопросов
            questions = data.get('questions', [])
            if not questions:
                raise serializers.ValidationError({
                    'questions': 'Необходимо добавить хотя бы один вопрос'
                })
            
            return data
            
        except Exception as e:
            logger.error(f"Error validating voting session: {e}")
            raise
    
    def create(self, validated_data):
        """Создание голосования с вопросами"""
        logger.info(f"Creating voting session with {len(validated_data.get('questions', []))} questions")
        
        try:
            questions_data = validated_data.pop('questions', [])
            voting_session = VotingSession.objects.create(**validated_data)
            logger.info(f"Voting session created: ID={voting_session.id}")
            
            for idx, q_data in enumerate(questions_data):
                try:
                    q_data['order'] = idx
                    options_data = q_data.pop('options', [])
                    question = Question.objects.create(voting_session=voting_session, **q_data)
                    logger.debug(f"Question created: ID={question.id}, order={idx}")
                    
                    for opt_idx, opt_text in enumerate(options_data):
                        option = AnswerOption.objects.create(
                            question=question,
                            text=opt_text,
                            order=opt_idx
                        )
                        logger.debug(f"Option created: ID={option.id}, text={opt_text[:30]}")
                        
                except Exception as e:
                    logger.error(f"Error creating question {idx}: {e}")
                    raise
            
            logger.info(f"Voting session {voting_session.id} created with {len(questions_data)} questions")
            return voting_session
            
        except Exception as e:
            logger.error(f"Error creating voting session: {e}\n{traceback.format_exc()}")
            raise
    
    def update(self, instance, validated_data):
        """Обновление голосования с вопросами"""
        logger.info(f"Updating voting session {instance.id}")
        
        try:
            questions_data = validated_data.pop('questions', None)
            
            # Обновляем основные поля
            for attr, value in validated_data.items():
                setattr(instance, attr, value)
            instance.save()
            logger.debug(f"Base fields updated for voting session {instance.id}")
            
            # Если переданы вопросы - обновляем их
            if questions_data is not None:
                # Считаем, сколько вопросов было удалено
                old_questions_count = instance.questions.count()
                instance.questions.all().delete()
                logger.debug(f"Deleted {old_questions_count} old questions")
                
                # Создаём новые вопросы
                for idx, q_data in enumerate(questions_data):
                    q_data['order'] = idx
                    options_data = q_data.pop('options', [])
                    question = Question.objects.create(voting_session=instance, **q_data)
                    
                    for opt_idx, opt_text in enumerate(options_data):
                        AnswerOption.objects.create(
                            question=question,
                            text=opt_text,
                            order=opt_idx
                        )
                
                logger.info(f"Replaced with {len(questions_data)} new questions")
            
            logger.info(f"Voting session {instance.id} updated successfully")
            return instance
            
        except Exception as e:
            logger.error(f"Error updating voting session: {e}\n{traceback.format_exc()}")
            raise


class VoteSubmitSerializer(serializers.Serializer):
    """Сериализатор для подачи голоса"""
    question_id = serializers.IntegerField()
    option_id = serializers.IntegerField(required=False, allow_null=True)
    rating_value = serializers.IntegerField(min_value=1, max_value=10, required=False, allow_null=True)
    text_answer = serializers.CharField(required=False, allow_blank=True)
    
    def validate(self, data):
        """Валидация голоса"""
        logger.debug(f"Validating vote submission for question {data.get('question_id')}")
        
        try:
            question_id = data.get('question_id')
            option_id = data.get('option_id')
            rating_value = data.get('rating_value')
            text_answer = data.get('text_answer')
            
            try:
                question = Question.objects.get(id=question_id)
            except Question.DoesNotExist:
                logger.warning(f"Question {question_id} not found")
                raise serializers.ValidationError({'question_id': 'Вопрос не найден'})
            
            # Валидация в зависимости от типа вопроса
            if question.question_type == 'single':
                if not option_id:
                    logger.warning(f"Single choice question {question_id} has no option_id")
                    raise serializers.ValidationError({'option_id': 'Необходимо выбрать вариант ответа'})
                
                try:
                    option = AnswerOption.objects.get(id=option_id, question=question)
                    logger.debug(f"Option validated: ID={option.id}")
                except AnswerOption.DoesNotExist:
                    logger.warning(f"Option {option_id} not found for question {question_id}")
                    raise serializers.ValidationError({'option_id': 'Вариант ответа не найден'})
            
            elif question.question_type == 'multiple':
                # Для множественного выбора используем отдельный endpoint
                # Здесь просто проверяем, что option_id указан
                if not option_id:
                    logger.warning(f"Multiple choice question {question_id} has no option_id")
                    raise serializers.ValidationError({'option_id': 'Необходимо указать вариант ответа'})
            
            elif question.question_type == 'rating':
                if not rating_value:
                    logger.warning(f"Rating question {question_id} has no rating_value")
                    raise serializers.ValidationError({'rating_value': 'Необходимо указать оценку'})
                
                if rating_value < 1 or rating_value > 10:
                    logger.warning(f"Invalid rating value: {rating_value}")
                    raise serializers.ValidationError({'rating_value': 'Оценка должна быть от 1 до 10'})
            
            elif question.question_type == 'text':
                if not text_answer or not text_answer.strip():
                    logger.warning(f"Text question {question_id} has empty answer")
                    raise serializers.ValidationError({'text_answer': 'Необходимо ввести текст ответа'})
            
            return data
            
        except Exception as e:
            logger.error(f"Error validating vote: {e}")
            raise


class BallotSerializer(serializers.ModelSerializer):
    """Сериализатор бюллетеня"""
    owner_name = serializers.CharField(source='owner.full_name', read_only=True)
    submitted_by_name = serializers.CharField(source='submitted_by.full_name', read_only=True, default='')
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    
    class Meta:
        model = Ballot
        fields = [
            'id', 'voting_session', 'owner', 'owner_name', 'status', 'status_display',
            'submitted_by', 'submitted_by_name', 'representative_name',
            'representative_document', 'submitted_at', 'is_valid'
        ]
        read_only_fields = ['submitted_at']

    def to_representation(self, instance):
        """Кастомное представление с дополнительной информацией"""
        try:
            data = super().to_representation(instance)
            
            # Добавляем количество голосов в бюллетене
            votes_count = instance.votes.count()
            data['votes_count'] = votes_count
            
            # Добавляем информацию о представителе, если есть
            if instance.representative_name:
                data['is_representative_vote'] = True
            else:
                data['is_representative_vote'] = False
            
            return data
        except Exception as e:
            logger.error(f"Error in BallotSerializer.to_representation: {e}")
            return data


class VotingInvitationSerializer(serializers.ModelSerializer):
    """Сериализатор приглашения"""
    owner_name = serializers.CharField(source='owner.full_name', read_only=True)
    invitation_type_display = serializers.CharField(source='get_invitation_type_display', read_only=True)
    
    class Meta:
        model = VotingInvitation
        fields = [
            'id', 'voting_session', 'owner', 'owner_name', 'invitation_type',
            'invitation_type_display', 'contact_value', 'sent_at', 'opened_at'
        ]

    def to_representation(self, instance):
        """Кастомное представление с дополнительной информацией"""
        try:
            data = super().to_representation(instance)
            
            # Добавляем статус приглашения
            if instance.opened_at:
                data['status'] = 'opened'
                data['opened_days_ago'] = (timezone.now() - instance.opened_at).days
            elif instance.sent_at:
                data['status'] = 'sent'
            else:
                data['status'] = 'created'
            
            return data
        except Exception as e:
            logger.error(f"Error in VotingInvitationSerializer.to_representation: {e}")
            return data


class VoteResultsSerializer(serializers.Serializer):
    """Сериализатор результатов голосования"""
    question = QuestionSerializer()
    total_votes = serializers.IntegerField()
    options_results = serializers.ListField()
    participation_rate = serializers.FloatField()