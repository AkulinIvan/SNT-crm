def user_organization(request):
    """
    Контекстный процессор для передачи организации пользователя в шаблоны.
    """
    context = {}
    if request.user.is_authenticated:
        # Получаем организацию пользователя
        if hasattr(request.user, 'organization') and request.user.organization:
            context['user_organization'] = request.user.organization
            context['user_organization_name'] = request.user.organization.short_name
        else:
            context['user_organization'] = None
            context['user_organization_name'] = None
    else:
        context['user_organization'] = None
        context['user_organization_name'] = None
    
    return context