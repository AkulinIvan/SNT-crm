from rest_framework import serializers
from django.utils import timezone
from .models import (
    VotingSession, Question, AnswerOption, 
    Ballot, Vote, VotingInvitation
)


class AnswerOptionSerializer(serializers.ModelSerializer):
    """Сериализатор варианта ответа"""
    
    class Meta:
        model = AnswerOption
        fields = ['id', 'text', 'order', 'votes_count', 'percentage']


class QuestionSerializer(serializers.ModelSerializer):
    """Сериализатор вопроса"""
    options = AnswerOptionSerializer(many=True, read_only=True)
    
    class Meta:
        model = Question
        fields = ['id', 'title', 'description', 'question_type', 'order', 'total_votes', 'options']


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
    
    def create(self, validated_data):
        options_data = validated_data.pop('options', [])
        question = Question.objects.create(**validated_data)
        
        for idx, option_text in enumerate(options_data):
            AnswerOption.objects.create(
                question=question,
                text=option_text,
                order=idx
            )
        
        return question


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
        if obj.total_eligible == 0:
            return 0
        return int((obj.total_eligible * obj.quorum_percent) / 100)


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
        """Валидация дат"""
        if 'start_date' in data and 'end_date' in data:
            if data['start_date'] >= data['end_date']:
                raise serializers.ValidationError({
                    'end_date': 'Дата окончания должна быть позже даты начала'
                })
            
            if data['start_date'] < timezone.now():
                raise serializers.ValidationError({
                    'start_date': 'Дата начала не может быть в прошлом'
                })
        return data
    
    def create(self, validated_data):
        questions_data = validated_data.pop('questions', [])
        voting_session = VotingSession.objects.create(**validated_data)
        
        for idx, q_data in enumerate(questions_data):
            q_data['order'] = idx
            options_data = q_data.pop('options', [])
            question = Question.objects.create(voting_session=voting_session, **q_data)
            
            for opt_idx, opt_text in enumerate(options_data):
                AnswerOption.objects.create(
                    question=question,
                    text=opt_text,
                    order=opt_idx
                )
        
        return voting_session
    
    def update(self, instance, validated_data):
        """Обновление голосования с вопросами"""
        questions_data = validated_data.pop('questions', None)
        
        # Обновляем основные поля
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        
        # Если переданы вопросы - обновляем их
        if questions_data is not None:
            # Удаляем старые вопросы и варианты ответов
            instance.questions.all().delete()
            
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
        
        return instance


class VoteSubmitSerializer(serializers.Serializer):
    """Сериализатор для подачи голоса"""
    question_id = serializers.IntegerField()
    option_id = serializers.IntegerField(required=False, allow_null=True)
    rating_value = serializers.IntegerField(min_value=1, max_value=10, required=False, allow_null=True)
    text_answer = serializers.CharField(required=False, allow_blank=True)
    
    def validate(self, data):
        question_id = data.get('question_id')
        option_id = data.get('option_id')
        rating_value = data.get('rating_value')
        text_answer = data.get('text_answer')
        
        try:
            question = Question.objects.get(id=question_id)
        except Question.DoesNotExist:
            raise serializers.ValidationError({'question_id': 'Вопрос не найден'})
        
        if question.question_type == 'single':
            if not option_id:
                raise serializers.ValidationError({'option_id': 'Необходимо выбрать вариант ответа'})
            
            try:
                option = AnswerOption.objects.get(id=option_id, question=question)
            except AnswerOption.DoesNotExist:
                raise serializers.ValidationError({'option_id': 'Вариант ответа не найден'})
        
        elif question.question_type == 'multiple':
            # Для множественного выбора используем отдельный endpoint
            pass
        
        elif question.question_type == 'rating':
            if not rating_value:
                raise serializers.ValidationError({'rating_value': 'Необходимо указать оценку'})
        
        return data


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


class VoteResultsSerializer(serializers.Serializer):
    """Сериализатор результатов голосования"""
    question = QuestionSerializer()
    total_votes = serializers.IntegerField()
    options_results = serializers.ListField()
    participation_rate = serializers.FloatField()