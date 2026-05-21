from rest_framework import serializers
from .models import (
    ConsolidatedAssessment, ConsolidatedAssessmentLine, PaymentCategory, PaymentPeriod, Assessment,
    Payment, BankStatement, BankTransaction, ReceiptTemplate, ReceiptTemplateLine
)


class PaymentCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentCategory
        fields = '__all__'


class PaymentPeriodSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentPeriod
        fields = '__all__'


class AssessmentListSerializer(serializers.ModelSerializer):
    owner_name = serializers.CharField(source='owner.full_name', read_only=True)
    owner_email = serializers.SerializerMethodField(read_only=True)
    plot_number = serializers.CharField(source='land_plot.plot_number', read_only=True)
    category_name = serializers.CharField(source='category.name', read_only=True)
    period_display = serializers.CharField(source='period.__str__', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    debt = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    
    def get_owner_email(self, obj):
        """Получить активный email владельца"""
        email = obj.owner.contacts.filter(type='em', is_active=True).first()
        return email.value if email else None
    
    class Meta:
        model = Assessment
        fields = [
            'id', 'owner', 'owner_name', 'owner_email', 'land_plot', 'plot_number',
            'category', 'category_name', 'period', 'period_display',
            'amount', 'paid_amount', 'debt', 'penalty_amount',
            'status', 'status_display', 'created_at',
        ]


class AssessmentDetailSerializer(serializers.ModelSerializer):
    owner_name = serializers.CharField(source='owner.full_name', read_only=True)
    plot_number = serializers.CharField(source='land_plot.plot_number', read_only=True)
    category_name = serializers.CharField(source='category.name', read_only=True)
    period_display = serializers.CharField(source='period.__str__', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    debt = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    payments = serializers.SerializerMethodField()

    class Meta:
        model = Assessment
        fields = '__all__'

    def get_payments(self, obj):
        from .serializers import PaymentSerializer
        payments = obj.payments.all()
        return PaymentSerializer(payments, many=True).data


class PaymentSerializer(serializers.ModelSerializer):
    owner_name = serializers.CharField(
        source='assessment.owner.full_name', read_only=True
    )
    plot_number = serializers.CharField(
        source='assessment.land_plot.plot_number', read_only=True
    )
    category_name = serializers.CharField(
        source='assessment.category.name', read_only=True
    )

    class Meta:
        model = Payment
        fields = [
            'id', 'assessment', 'amount', 'payment_date', 'payment_method',
            'status', 'bank_name', 'bank_account', 'transaction_id',
            'payment_purpose', 'receipt_file', 'notes',
            'owner_name', 'plot_number', 'category_name',  
            'created_at', 'updated_at',
        ]
        read_only_fields = ['created_at', 'updated_at']


class BankStatementSerializer(serializers.ModelSerializer):
    transactions_count = serializers.SerializerMethodField()

    class Meta:
        model = BankStatement
        fields = '__all__'

    def get_transactions_count(self, obj):
        return obj.transactions.count()


class BankTransactionSerializer(serializers.ModelSerializer):
    matched_owner_name = serializers.CharField(
        source='matched_owner.full_name', read_only=True
    )

    class Meta:
        model = BankTransaction
        fields = '__all__'
        
class AssessmentCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Assessment
        fields = ['owner', 'land_plot', 'category', 'period', 'amount', 'notes']
        

class ReceiptTemplateLineSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)

    class Meta:
        model = ReceiptTemplateLine
        fields = ['id', 'category', 'category_name', 'calc_type', 'amount', 
                  'auto_quantity', 'manual_quantity', 'order']


class ReceiptTemplateSerializer(serializers.ModelSerializer):
    lines = ReceiptTemplateLineSerializer(many=True, read_only=True)

    class Meta:
        model = ReceiptTemplate
        fields = ['id', 'name', 'description', 'is_active', 'lines', 'created_at']


class ConsolidatedAssessmentLineSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)

    class Meta:
        model = ConsolidatedAssessmentLine
        fields = ['id', 'category', 'category_name', 'description', 
                  'quantity', 'unit', 'rate', 'amount', 'order']


class ConsolidatedAssessmentSerializer(serializers.ModelSerializer):
    owner_name = serializers.CharField(source='owner.full_name', read_only=True)
    plot_number = serializers.CharField(source='land_plot.plot_number', read_only=True)
    lines = ConsolidatedAssessmentLineSerializer(many=True, read_only=True)
    debt = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)

    class Meta:
        model = ConsolidatedAssessment
        fields = ['id', 'owner', 'owner_name', 'land_plot', 'plot_number',
                  'period', 'total_amount', 'paid_amount', 'debt', 'status',
                  'payment_uid', 'lines', 'created_at']