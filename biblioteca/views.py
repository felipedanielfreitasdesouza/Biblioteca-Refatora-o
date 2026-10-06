from django.views.decorators.http import require_http_methods

@require_http_methods(["GET"])
def api_usuario_status(request, usuario_id):
    """Retorna status e dados do usuário para AJAX do formulário de empréstimo"""
    usuario = get_object_or_404(Usuario, id=usuario_id)
    emprestimos_ativos = Emprestimo.objects.filter(usuario=usuario, data_devolucao__isnull=True).count()
    status = "Ativo"
    if usuario.esta_suspenso():
        if usuario.data_suspensao and usuario.data_suspensao >= timezone.now().date():
            status = f"Suspenso até {usuario.data_suspensao.strftime('%d/%m/%Y')}"
        elif usuario.tem_emprestimo_atrasado():
            status = "Suspenso por atraso"
        else:
            status = "Suspenso"
    pode_emprestar = usuario.pode_emprestar()
    return JsonResponse({
        "status": status,
        "emprestimos_ativos": emprestimos_ativos,
        "pode_emprestar": pode_emprestar,
        "nome": f"{usuario.first_name} {usuario.last_name}",
        "dre": usuario.dre,
    })
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, authenticate, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import (
    BooleanField,
    Case,
    Count,
    Exists,
    OuterRef,
    Prefetch,
    Q,
    Value,
    When,
)
from django.http import JsonResponse, HttpResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from datetime import timedelta
import json

from .models import Usuario, Titulo, Exemplar, Emprestimo, HistoricoEmprestimo
from .forms import (
    UsuarioRegistroForm, LoginForm, TituloForm, ExemplarForm, 
    EmprestimoForm, BuscaAcervoForm, DevolucaoForm, PerfilUsuarioForm
)


def is_administrador(user):
    """Verifica se o usuário é administrador"""
    return user.is_authenticated and user.is_administrador


# Views públicas
def home(request):
    """Página inicial"""
    context = {
        'total_titulos': Titulo.objects.count(),
        'total_exemplares': Exemplar.objects.count(),
        'exemplares_disponiveis': Exemplar.objects.filter(disponivel=True).count(),
        'emprestimos_ativos': Emprestimo.objects.filter(data_devolucao__isnull=True).count(),
    }
    return render(request, 'biblioteca/home.html', context)


def sobre(request):
    """Página sobre nós"""
    return render(request, 'biblioteca/sobre.html')


def contato(request):
    """Página de contato"""
    return render(request, 'biblioteca/contato.html')


def registro(request):
    """Registro de novos usuários com validação de email"""
    if request.method == 'POST':
        form = UsuarioRegistroForm(request.POST)
        if form.is_valid():
            # Salvar usuário como inativo
            user = form.save(commit=False)
            user.is_active = False
            user.email_verificado = False
            
            # Gerar token de ativação
            import secrets
            user.token_ativacao = secrets.token_urlsafe(50)
            user.save()
            
            # Enviar email de ativação
            enviar_email_ativacao(request, user)
            
            messages.success(
                request, 
                f'Conta criada para {user.first_name}! '
                f'Verifique seu email ({user.email}) para ativar sua conta.'
            )
            return redirect('login')
    else:
        form = UsuarioRegistroForm()
    return render(request, 'registration/registro.html', {'form': form})


def enviar_email_ativacao(request, user):
    """Envia email de ativação para o usuário"""
    from django.core.mail import send_mail
    from django.template.loader import render_to_string
    from django.utils.http import urlsafe_base64_encode
    from django.utils.encoding import force_bytes
    from django.contrib.sites.shortcuts import get_current_site
    
    # Gerar link de ativação
    current_site = get_current_site(request)
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = user.token_ativacao
    
    # Renderizar template do email
    subject = 'Ative sua conta no Sistema de Biblioteca'
    message = render_to_string('registration/email_ativacao.html', {
        'user': user,
        'domain': current_site.domain,
        'uid': uid,
        'token': token,
        'protocol': 'https' if request.is_secure() else 'http',
    })
    
    # Enviar email
    try:
        send_mail(
            subject,
            message,
            None,  # Usar DEFAULT_FROM_EMAIL
            [user.email],
            html_message=message,
            fail_silently=False,
        )
        return True
    except Exception as e:
        print(f"Erro ao enviar email: {e}")
        return False


def ativar_conta(request, uidb64, token):
    """Ativa a conta do usuário via link do email"""
    from django.utils.http import urlsafe_base64_decode
    from django.utils.encoding import force_str
    
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = Usuario.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, Usuario.DoesNotExist):
        user = None
    
    if user is not None and user.token_ativacao == token:
        # Verificar se o token não expirou (7 dias)
        from datetime import timedelta
        if user.date_joined + timedelta(days=7) >= timezone.now():
            user.is_active = True
            user.email_verificado = True
            user.token_ativacao = None  # Limpar token
            user.save()
            
            messages.success(request, 'Sua conta foi ativada com sucesso! Você já pode fazer login.')
            return render(request, 'registration/ativacao_confirmada.html', {'user': user})
        else:
            messages.error(request, 'Link de ativação expirado. Solicite um novo cadastro.')
            return redirect('registro')
    else:
        messages.error(request, 'Link de ativação inválido.')
        return redirect('registro')


def reenviar_ativacao(request):
    """Reenviar email de ativação"""
    if request.method == 'POST':
        email = request.POST.get('email')
        try:
            user = Usuario.objects.get(email=email, is_active=False, email_verificado=False)
            
            # Gerar novo token
            import secrets
            user.token_ativacao = secrets.token_urlsafe(50)
            user.save()
            
            # Reenviar email
            if enviar_email_ativacao(request, user):
                messages.success(
                    request, 
                    f'Email de ativação reenviado para {email}. Verifique sua caixa de entrada.'
                )
            else:
                messages.error(request, 'Erro ao enviar email. Tente novamente.')
                
        except Usuario.DoesNotExist:
            messages.error(
                request, 
                'Email não encontrado ou conta já ativada. Verifique o email informado.'
            )
        
        return redirect('login')
    
    return render(request, 'registration/reenviar_ativacao.html')


def logout_view(request):
    """Logout customizado"""
    if request.method == 'POST':
        # Logout via POST (mais seguro)
        if request.user.is_authenticated:
            username = request.user.first_name or request.user.username
            logout(request)
            messages.success(request, f'Você foi desconectado com sucesso, {username}!')
        return redirect('home')
    elif request.method == 'GET':
        # Logout via GET (compatibilidade)
        if request.user.is_authenticated:
            username = request.user.first_name or request.user.username
            logout(request)
            messages.success(request, f'Você foi desconectado com sucesso, {username}!')
        return redirect('home')
    else:
        return redirect('home')


def acervo(request):
    """Consulta pública do acervo"""
    form = BuscaAcervoForm(request.GET)
    titulos = Titulo.objects.annotate(
    total_exemplares_calc=Count(
        'exemplares',
        distinct=True
    ),
    exemplares_disponiveis_calc=Count(
        'exemplares',
        filter=Q(exemplares__disponivel=True),
        distinct=True
    )
).prefetch_related(
    Prefetch(
        'exemplares',
        queryset=Exemplar.objects.all(),
        to_attr='exemplares_carregados'
    )
).order_by('titulo_da_obra')
    
    if form.is_valid():
        termo_busca = form.cleaned_data.get('termo_busca')
        tipo_busca = form.cleaned_data.get('tipo_busca')
        editora = form.cleaned_data.get('editora')
        ano_inicio = form.cleaned_data.get('ano_inicio')
        ano_fim = form.cleaned_data.get('ano_fim')
        
        if termo_busca:
            if tipo_busca == 'titulo':
                titulos = titulos.filter(titulo_da_obra__icontains=termo_busca)
            elif tipo_busca == 'autor':
                titulos = titulos.filter(autor__icontains=termo_busca)
            else:  # todos
                titulos = titulos.filter(
                    Q(titulo_da_obra__icontains=termo_busca) | 
                    Q(autor__icontains=termo_busca)
                )
        
        if editora:
            titulos = titulos.filter(editora__icontains=editora)
        
        if ano_inicio:
            titulos = titulos.filter(ano_publicacao__gte=ano_inicio)
        
        if ano_fim:
            titulos = titulos.filter(ano_publicacao__lte=ano_fim)
    
    # Paginação
    paginator = Paginator(titulos, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'form': form,
        'page_obj': page_obj,
        'total_resultados': titulos.count()
    }
    return render(request, 'biblioteca/acervo.html', context)


def exemplar_detail(request, exemplar_id):
    exemplar = get_object_or_404(
        Exemplar.objects.select_related(
            'titulo'
        ).prefetch_related(
            'titulo__exemplares'
        ),
        id=exemplar_id
    )

    try:
        emprestimo_atual = Emprestimo.objects.select_related(
            'usuario'
        ).get(
            exemplar=exemplar,
            data_devolucao__isnull=True
        )
    except Emprestimo.DoesNotExist:
        emprestimo_atual = None

    context = {
        'exemplar': exemplar,
        'pode_emprestar': (
            request.user.is_authenticated and
            request.user.pode_emprestar()
        ),
        'emprestimo_atual': emprestimo_atual,
    }

    return render(
        request,
        'biblioteca/exemplar_detail.html',
        context
    )


# Views de usuário autenticado
@login_required
def dashboard(request):
    """Dashboard do usuário"""
    user = request.user
    emprestimos_ativos = Emprestimo.objects.filter(
    usuario=user,
    data_devolucao__isnull=True
).select_related(
    'exemplar__titulo'
).order_by('previsao_devolucao')
    
    historico_emprestimos = Emprestimo.objects.filter(
    usuario=user,
    data_devolucao__isnull=False
).select_related(
    'exemplar__titulo'
).order_by('-data_devolucao')[:5]
    # Normalizar cálculos para histórico: data_devolucao é DateTime, previsao_devolucao é Date
    # Precalcular atributos usados nas templates para evitar comparações datetime x date
    for emprestimo in historico_emprestimos:
        if emprestimo.data_devolucao and emprestimo.previsao_devolucao:
            try:
                devolucao_date = emprestimo.data_devolucao.date()
            except Exception:
                devolucao_date = emprestimo.data_devolucao
            emprestimo.devolucao_atrasada = devolucao_date > emprestimo.previsao_devolucao
            emprestimo.dias_atraso_calc = max(0, (devolucao_date - emprestimo.previsao_devolucao).days)
        else:
            emprestimo.devolucao_atrasada = False
            emprestimo.dias_atraso_calc = 0
    
    context = {
        'emprestimos_ativos': emprestimos_ativos,
        'historico_emprestimos': historico_emprestimos,
        'pode_emprestar': user.pode_emprestar(),
        'esta_suspenso': user.esta_suspenso(),
        'data_suspensao': user.data_suspensao if user.esta_suspenso() else None
    }
    return render(request, 'biblioteca/dashboard.html', context)


@login_required
def perfil(request):
    """Edição do perfil do usuário"""
    if request.method == 'POST':
        form = PerfilUsuarioForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, 'Perfil atualizado com sucesso!')
            return redirect('perfil')
    else:
        form = PerfilUsuarioForm(instance=request.user)
    
    return render(request, 'biblioteca/perfil.html', {'form': form})


@login_required
def emprestar_exemplar(request, exemplar_id):
    """Realizar empréstimo de um exemplar específico"""
    exemplar = get_object_or_404(Exemplar, id=exemplar_id)
    
    if request.user.tem_emprestimo_atrasado():
        messages.error(request, 'Você possui empréstimos atrasados e não pode realizar novos empréstimos.')
    elif not request.user.pode_emprestar():
        messages.error(request, 'Você atingiu o limite de 3 empréstimos ou está suspenso.')
        return redirect('exemplar_detail', exemplar_id=exemplar_id)
    
    if not exemplar.disponivel:
        messages.error(request, 'Este exemplar não está disponível.')
        return redirect('exemplar_detail', exemplar_id=exemplar_id)
    
    # Criar empréstimo
    emprestimo = Emprestimo.objects.create(
        usuario=request.user,
        exemplar=exemplar
    )
    
    messages.success(request, f'Empréstimo realizado com sucesso! Devolução prevista para {emprestimo.previsao_devolucao.strftime("%d/%m/%Y")}')
    return redirect('dashboard')


@login_required
def devolver_exemplar(request, exemplar_id):
    """Realizar devolução de um exemplar específico"""
    exemplar = get_object_or_404(Exemplar, id=exemplar_id)
    
    # Buscar empréstimo ativo
    try:
        emprestimo = Emprestimo.objects.get(
            exemplar=exemplar,
            data_devolucao__isnull=True
        )
    except Emprestimo.DoesNotExist:
        messages.error(request, 'Este exemplar não está emprestado.')
        return redirect('exemplar_detail', exemplar_id=exemplar_id)
    
    # Verificar permissões
    if not (request.user == emprestimo.usuario or request.user.is_administrador):
        messages.error(request, 'Você não tem permissão para devolver este exemplar.')
        return redirect('exemplar_detail', exemplar_id=exemplar_id)
    
    # Processar devolução
    emprestimo.devolver()
    
    dias_atraso = emprestimo.dias_atraso()
    print("dias_atraso:", dias_atraso)

    # Mensagem de feedback
    if dias_atraso > 0:
        messages.warning(
            request,
            f'Devolução realizada com {dias_atraso} dia(s) de atraso. '
            f'Você ficará suspenso por {dias_atraso} dia(s) a partir de hoje.'
        )
    else:
        messages.success(request, 'Devolução realizada com sucesso!')
    
    # Redirecionar para a URL especificada ou para a dashboard apropriada
    next_url = request.GET.get('next')
    if next_url and next_url.startswith('/'):  # Verificar se é uma URL interna válida
        return redirect(next_url)
    elif request.user.is_administrador:
        return redirect('admin_dashboard')
    else:
        return redirect('dashboard')


@user_passes_test(is_administrador)
def admin_dashboard(request):
    hoje = timezone.now().date()

    emprestimo_atrasado = Emprestimo.objects.filter(
        usuario=OuterRef('pk'),
        data_devolucao__isnull=True,
        previsao_devolucao__lt=hoje
    )

    usuarios_suspensos = Usuario.objects.filter(
        Q(data_suspensao__gte=hoje) |
        Exists(emprestimo_atrasado)
    ).count()

    context = {
        'total_usuarios': Usuario.objects.count(),
        'total_titulos': Titulo.objects.count(),
        'total_exemplares': Exemplar.objects.count(),
        'emprestimos_ativos': Emprestimo.objects.filter(
            data_devolucao__isnull=True
        ).count(),
        'emprestimos_atrasados': Emprestimo.objects.filter(
            data_devolucao__isnull=True,
            previsao_devolucao__lt=hoje
        ).count(),
        'usuarios_suspensos': usuarios_suspensos,
    }

    return render(request, 'biblioteca/admin/dashboard.html', context)
@user_passes_test(is_administrador)
def titulo_list(request):
    """Lista de títulos para administradores"""
    titulos = Titulo.objects.annotate(
    total_exemplares_calc=Count(
        'exemplares',
        distinct=True
    ),
    exemplares_disponiveis_calc=Count(
        'exemplares',
        filter=Q(exemplares__disponivel=True),
        distinct=True
    )
).order_by('titulo_da_obra')
    
    # Busca
    busca = request.GET.get('busca', '')
    if busca:
        titulos = titulos.filter(
            Q(titulo_da_obra__icontains=busca) |
            Q(autor__icontains=busca) |
            Q(lombada__icontains=busca)
        )
    
    paginator = Paginator(titulos, 15)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    return render(request, 'biblioteca/admin/titulo_list.html', {
        'page_obj': page_obj,
        'busca': busca
    })

@user_passes_test(is_administrador)
def titulo_create(request):
    """Criar novo título"""
    if request.method == 'POST':
        form = TituloForm(request.POST)
        if form.is_valid():
            titulo = form.save()
            messages.success(request, f'Título "{titulo.titulo_da_obra}" criado com sucesso!')
            return redirect('titulo_list')
    else:
        form = TituloForm()
    
    return render(request, 'biblioteca/admin/titulo_form.html', {
        'form': form,
        'action': 'Criar'
    })

@user_passes_test(is_administrador)
def titulo_edit(request, titulo_id):
    """Editar título"""
    titulo = get_object_or_404(Titulo, id=titulo_id)
    
    if request.method == 'POST':
        form = TituloForm(request.POST, instance=titulo)
        if form.is_valid():
            form.save()
            messages.success(request, f'Título "{titulo.titulo_da_obra}" atualizado com sucesso!')
            return redirect('titulo_list')
    else:
        form = TituloForm(instance=titulo)
    
    return render(request, 'biblioteca/admin/titulo_form.html', {
        'form': form,
        'titulo': titulo,
        'action': 'Editar'
    })

@user_passes_test(is_administrador)
def titulo_delete(request, titulo_id):
    """Excluir título"""
    titulo = get_object_or_404(Titulo, id=titulo_id)
    
    if request.method == 'POST':
        nome_titulo = titulo.titulo_da_obra
        titulo.delete()
        messages.success(request, f'Título "{nome_titulo}" excluído com sucesso!')
        return redirect('titulo_list')
    
    # Passar também 'object' para compatibilidade com templates que usam esse nome
    return render(request, 'biblioteca/admin/titulo_confirm_delete.html', {
        'titulo': titulo,
        'object': titulo,
    })

@user_passes_test(is_administrador)
def exemplar_list(request):
    """Lista de exemplares"""
    exemplares = Exemplar.objects.select_related('titulo').all()
    
    # Filtros
    titulo_id = request.GET.get('titulo')
    disponivel = request.GET.get('disponivel')
    busca = request.GET.get('busca', '')
    
    if titulo_id:
        exemplares = exemplares.filter(titulo_id=titulo_id)
    
    if disponivel == 'sim':
        exemplares = exemplares.filter(disponivel=True)
    elif disponivel == 'nao':
        exemplares = exemplares.filter(disponivel=False)

    # Busca por código, título, autor ou lombada
    if busca:
        exemplares = exemplares.filter(
            Q(titulo__titulo_da_obra__icontains=busca) |
            Q(titulo__autor__icontains=busca) |
            Q(codigo_exemplar__icontains=busca) |
            Q(titulo__lombada__icontains=busca)
        )
    
    paginator = Paginator(exemplares, 15)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    # Estatísticas
    total_exemplares = Exemplar.objects.count()
    disponiveis = Exemplar.objects.filter(disponivel=True).count()
    emprestados = total_exemplares - disponiveis
    titulos_diferentes = Titulo.objects.count()

    # Para o filtro de títulos
    titulos = Titulo.objects.all().order_by("titulo_da_obra")
    
    context = {
        'page_obj': page_obj,
        'titulos': titulos,
        'titulo_selecionado': titulo_id,
        'disponivel_selecionado': disponivel,
        'busca': busca,
        # manter nomes que os templates esperam
        'titulo_filtro': titulo_id,
        'disponivel': disponivel,
        'total_exemplares': total_exemplares,
        'disponiveis': disponiveis,
        'emprestados': emprestados,
        'titulos_diferentes': titulos_diferentes,
    }
    return render(request, 'biblioteca/admin/exemplar_list.html', context)

@user_passes_test(is_administrador)
def exemplar_create(request):
    """Criar novo exemplar"""
    if request.method == 'POST':
        form = ExemplarForm(request.POST)
        if form.is_valid():
            exemplar = form.save()
            messages.success(request, f'Exemplar "{exemplar.codigo_exemplar}" criado com sucesso!')
            return redirect('exemplar_list')
    else:
        form = ExemplarForm()
    
    return render(request, 'biblioteca/admin/exemplar_form.html', {
        'form': form,
        'action': 'Criar'
    })


@user_passes_test(is_administrador)
def exemplar_delete(request, exemplar_id):
    """Excluir exemplar"""
    exemplar = get_object_or_404(Exemplar, id=exemplar_id)

    if request.method == 'POST':
        codigo = exemplar.codigo_exemplar
        exemplar.delete()
        messages.success(request, f'Exemplar "{codigo}" excluído com sucesso!')
        return redirect('exemplar_list')

    # Compatibilidade com templates que usam 'object'
    return render(request, 'biblioteca/admin/exemplar_confirm_delete.html', {
        'exemplar': exemplar,
        'object': exemplar,
    })

@user_passes_test(is_administrador)
def emprestimo_list(request):
    """Lista de empréstimos"""
    emprestimos = Emprestimo.objects.select_related('usuario', 'exemplar__titulo').all()
    
    # Filtros
    status = request.GET.get('status')
    busca = request.GET.get('busca', '')
    periodo = request.GET.get('periodo', '')
    ordem = request.GET.get('ordem', 'recente')

    # Filtrar por status
    if status == 'ativo':
        emprestimos = emprestimos.filter(data_devolucao__isnull=True)
    elif status == 'devolvido':
        emprestimos = emprestimos.filter(data_devolucao__isnull=False)
    elif status == 'atrasado':
        emprestimos = emprestimos.filter(
            data_devolucao__isnull=True,
            previsao_devolucao__lt=hoje
        )

    # Busca por usuário, livro ou código
    if busca:
        emprestimos = emprestimos.filter(
            Q(usuario__first_name__icontains=busca) |
            Q(usuario__last_name__icontains=busca) |
            Q(usuario__dre__icontains=busca) |
            Q(exemplar__titulo__titulo_da_obra__icontains=busca) |
            Q(exemplar__codigo_exemplar__icontains=busca)
        )

    # Período: hoje / semana / mês
    hoje = timezone.now().date()
    if periodo == 'hoje':
        emprestimos = emprestimos.filter(data_emprestimo__date=hoje)
    elif periodo == 'semana':
        # semana corrente: desde o início da semana (segunda-feira)
        start_of_week = hoje - timedelta(days=hoje.weekday())
        emprestimos = emprestimos.filter(data_emprestimo__date__gte=start_of_week)
    elif periodo == 'mes':
        emprestimos = emprestimos.filter(
            data_emprestimo__year=hoje.year,
            data_emprestimo__month=hoje.month
        )

    # Ordenação
    if ordem == 'recente':
        emprestimos = emprestimos.order_by('-data_emprestimo')
    elif ordem == 'antigo':
        emprestimos = emprestimos.order_by('data_emprestimo')
    elif ordem == 'vencimento':
        emprestimos = emprestimos.order_by('previsao_devolucao')
    elif ordem == 'usuario':
        emprestimos = emprestimos.order_by('usuario__first_name', 'usuario__last_name')
    
    paginator = Paginator(emprestimos, 15)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    # Estatísticas rápidas
    total_emprestimos = Emprestimo.objects.count()
    emprestimos_ativos = Emprestimo.objects.filter(data_devolucao__isnull=True).count()
    emprestimos_atrasados = Emprestimo.objects.filter(
        data_devolucao__isnull=True,
        previsao_devolucao__lt=hoje
    ).count()
    emprestimos_devolvidos = Emprestimo.objects.filter(data_devolucao__isnull=False).count()

    return render(request, 'biblioteca/admin/emprestimo_list.html', {
        'page_obj': page_obj,
        'status_selecionado': status,
        'busca': busca,
        'status': status,
        'periodo': periodo,
        'ordem': ordem,
        'total_emprestimos': total_emprestimos,
        'emprestimos_ativos': emprestimos_ativos,
        'emprestimos_atrasados': emprestimos_atrasados,
        'emprestimos_devolvidos': emprestimos_devolvidos,
    })


@user_passes_test(is_administrador)
def emprestimo_create(request):
    """Criar novo empréstimo"""
    if request.method == 'POST':
        form = EmprestimoForm(request.POST)
        if form.is_valid():
            emprestimo = form.save()
            messages.success(request, f'Empréstimo realizado para {emprestimo.usuario.first_name}!')
            return redirect('emprestimo_list')
    else:
        form = EmprestimoForm()
    
    return render(request, 'biblioteca/admin/emprestimo_form.html', {
        'form': form,
        'action': 'Criar'
    })

@user_passes_test(is_administrador)
def emprestimo_detail(request, emprestimo_id):
    """Página de detalhes do empréstimo"""
    emprestimo = get_object_or_404(Emprestimo, id=emprestimo_id)
    
    # Calcular informações adicionais
    dias_emprestado = (timezone.now().date() - emprestimo.data_emprestimo.date()).days
    
    if emprestimo.data_devolucao:
        dias_total = (emprestimo.data_devolucao - emprestimo.data_emprestimo).days
        status_devolucao = "devolvido"
        if emprestimo.data_devolucao.date() > emprestimo.previsao_devolucao:
            dias_atraso = (emprestimo.data_devolucao.date() - emprestimo.previsao_devolucao).days
        else:
            dias_atraso = 0
    else:
        dias_total = dias_emprestado
        if emprestimo.previsao_devolucao < timezone.now().date():
            status_devolucao = "atrasado"
            dias_atraso = (timezone.now().date() - emprestimo.previsao_devolucao).days
        else:
            status_devolucao = "ativo"
            dias_atraso = 0
    
    # Buscar outros empréstimos do mesmo usuário
    outros_emprestimos = Emprestimo.objects.filter(
        usuario=emprestimo.usuario
    ).exclude(id=emprestimo_id).select_related('exemplar__titulo').order_by('-data_emprestimo')[:5]
    
    # Buscar outros empréstimos do mesmo livro
    outros_emprestimos_livro = Emprestimo.objects.filter(
        exemplar__titulo=emprestimo.exemplar.titulo
    ).exclude(id=emprestimo_id).select_related('usuario').order_by('-data_emprestimo')[:5]
    
    context = {
        'emprestimo': emprestimo,
        'dias_emprestado': dias_emprestado,
        'dias_total': dias_total,
        'dias_atraso': dias_atraso,
        'status_devolucao': status_devolucao,
        'outros_emprestimos': outros_emprestimos,
        'outros_emprestimos_livro': outros_emprestimos_livro,
    }
    
    return render(request, 'biblioteca/admin/emprestimo_detail.html', context)

@user_passes_test(is_administrador)
def devolucao(request):
    """Realizar devolução"""
    emprestimo_encontrado = None
    
    if request.method == 'POST':
        form = DevolucaoForm(request.POST)
        if form.is_valid():
            codigo_exemplar = form.cleaned_data['codigo_exemplar']
            
            try:
                exemplar = Exemplar.objects.get(codigo_exemplar=codigo_exemplar)
                emprestimo = Emprestimo.objects.filter(
                    exemplar=exemplar,
                    data_devolucao__isnull=True
                ).first()
                
                if emprestimo:
                    emprestimo_encontrado = emprestimo
                else:
                    messages.error(request, 'Este exemplar não está emprestado.')
                    
            except Exemplar.DoesNotExist:
                messages.error(request, 'Exemplar não encontrado.')
    else:
        form = DevolucaoForm()
    
    # Dados para o template
    emprestimos_ativos = Emprestimo.objects.filter(
        data_devolucao__isnull=True
    ).select_related('usuario', 'exemplar__titulo').order_by('-data_emprestimo')[:10]
    
    context = {
        'form': form,
        'emprestimo_encontrado': emprestimo_encontrado,
        'emprestimos_ativos': emprestimos_ativos,
        'emprestimos_hoje': Emprestimo.objects.filter(
            data_emprestimo__date=timezone.now().date()
        ).count(),
        'devolucoes_hoje': Emprestimo.objects.filter(
            data_devolucao__date=timezone.now().date()
        ).count(),
        'total_ativos': Emprestimo.objects.filter(data_devolucao__isnull=True).count(),
        'total_atrasados': Emprestimo.objects.filter(
            data_devolucao__isnull=True,
            previsao_devolucao__lt=hoje
        ).count(),
    }
    
    return render(request, 'biblioteca/admin/devolucao.html', context)


@user_passes_test(is_administrador)
def usuario_list(request):
    hoje = timezone.now().date()

    emprestimo_atrasado = Emprestimo.objects.filter(
        usuario=OuterRef('pk'),
        data_devolucao__isnull=True,
        previsao_devolucao__lt=hoje
    )

    usuarios = Usuario.objects.annotate(
        total_emprestimos=Count('emprestimos'),
        emprestimos_ativos=Count(
            'emprestimos',
            filter=Q(emprestimos__data_devolucao__isnull=True)
        ),
        tem_atraso_calc=Exists(emprestimo_atrasado),
    ).annotate(
        esta_suspenso_calc=Case(
            When(
                Q(data_suspensao__gte=hoje) |
                Q(tem_atraso_calc=True),
                then=Value(True)
            ),
            default=Value(False),
            output_field=BooleanField()
        )
    ).order_by('first_name', 'last_name')

    status = request.GET.get('status')
    busca = request.GET.get('busca')
    ordem = request.GET.get('ordem', 'nome')

    if status == 'suspenso':
        usuarios = usuarios.filter(esta_suspenso_calc=True)
    elif status == 'ativo':
        usuarios = usuarios.filter(esta_suspenso_calc=False)
    elif status == 'admin':
        usuarios = usuarios.filter(is_administrador=True)

    if busca:
        usuarios = usuarios.filter(
            Q(first_name__icontains=busca) |
            Q(last_name__icontains=busca) |
            Q(dre__icontains=busca) |
            Q(email__icontains=busca)
        )

    if ordem == 'data':
        usuarios = usuarios.order_by('-date_joined')
    elif ordem == 'emprestimos':
        usuarios = usuarios.order_by('-total_emprestimos')
    else:
        usuarios = usuarios.order_by('first_name', 'last_name')

    paginator = Paginator(usuarios, 15)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    todos_os_usuarios = Usuario.objects.annotate(
        tem_atraso_calc=Exists(emprestimo_atrasado),
    ).annotate(
        esta_suspenso_calc=Case(
            When(
                Q(data_suspensao__gte=hoje) |
                Q(tem_atraso_calc=True),
                then=Value(True)
            ),
            default=Value(False),
            output_field=BooleanField()
        )
    )

    total_usuarios = todos_os_usuarios.count()
    total_suspensos = todos_os_usuarios.filter(
        esta_suspenso_calc=True
    ).count()
    total_ativos = total_usuarios - total_suspensos
    administradores = Usuario.objects.filter(
        is_administrador=True
    ).count()

    return render(request, 'biblioteca/admin/usuario_list.html', {
        'page_obj': page_obj,
        'status': status,
        'busca': busca,
        'ordem': ordem,
        'total_usuarios': total_usuarios,
        'total_ativos': total_ativos,
        'total_suspensos': total_suspensos,
        'administradores': administradores,
    })


@user_passes_test(is_administrador)
def suspender_usuario(request, usuario_id):
    """Suspender usuário"""
    if request.method == 'POST':
        try:
            usuario = get_object_or_404(Usuario, id=usuario_id)
            
            # Não permitir suspender administradores
            if usuario.is_administrador:
                return JsonResponse({
                    'success': False,
                    'error': 'Não é possível suspender um administrador'
                })
            
            # Suspender por 30 dias
            usuario.suspender(30)
            
            return JsonResponse({
                'success': True,
                'message': f'Usuário {usuario.first_name} suspenso por 30 dias'
            })
            
        except Exception as e:
            return JsonResponse({
                'success': False,
                'error': str(e)
            })
    
    return JsonResponse({'success': False, 'error': 'Método não permitido'})


@user_passes_test(is_administrador)
def reativar_usuario(request, usuario_id):
    """Reativar usuário suspenso"""
    if request.method == 'POST':
        try:
            usuario = get_object_or_404(Usuario, id=usuario_id)
            
            # Reativar usuário
            usuario.reativar()
            
            return JsonResponse({
                'success': True,
                'message': f'Usuário {usuario.first_name} reativado com sucesso'
            })
            
        except Exception as e:
            return JsonResponse({
                'success': False,
                'error': str(e)
            })
    
    return JsonResponse({'success': False, 'error': 'Método não permitido'})


@user_passes_test(is_administrador)
def usuario_detail(request, usuario_id):
    """Página de detalhes do usuário"""
    usuario = get_object_or_404(Usuario, id=usuario_id)
    
    # Buscar empréstimos recentes
    emprestimos_recentes = Emprestimo.objects.filter(
        usuario=usuario
    ).select_related('exemplar__titulo').order_by('-data_emprestimo')[:10]
    
    # Estatísticas do usuário
    total_emprestimos = Emprestimo.objects.filter(usuario=usuario).count()
    emprestimos_ativos = Emprestimo.objects.filter(
        usuario=usuario,
        data_devolucao__isnull=True
    ).count()
    emprestimos_atrasados = Emprestimo.objects.filter(
        usuario=usuario,
        data_devolucao__isnull=True,
        previsao_devolucao__lt=hoje
    ).count()
    
    context = {
        'usuario': usuario,
        'emprestimos_recentes': emprestimos_recentes,
        'total_emprestimos': total_emprestimos,
        'emprestimos_ativos': emprestimos_ativos,
        'emprestimos_atrasados': emprestimos_atrasados,
    }
    
    return render(request, 'biblioteca/admin/usuario_detail.html', context)

@user_passes_test(is_administrador)
def relatorios(request):
    """Página de relatórios administrativos"""
    from django.db.models.functions import TruncMonth

    # Estatísticas gerais
    hoje = timezone.now().date()

    emprestimo_atrasado = Emprestimo.objects.filter(
        usuario=OuterRef('pk'),
        data_devolucao__isnull=True,
        previsao_devolucao__lt=hoje
    )

    todos_os_usuarios = Usuario.objects.annotate(
        tem_atraso_relatorio=Exists(emprestimo_atrasado)
    ).annotate(
        esta_suspenso_calc=Case(
            When(
                Q(data_suspensao__gte=hoje) |
                Q(tem_atraso_relatorio=True),
                then=Value(True)
            ),
            default=Value(False),
            output_field=BooleanField()
        )
    )

    total_usuarios = todos_os_usuarios.count()
    total_suspensos = todos_os_usuarios.filter(
        esta_suspenso_calc=True
    ).count()
    total_ativos = total_usuarios - total_suspensos
    total_titulos = Titulo.objects.count()
    total_exemplares = Exemplar.objects.count()
    total_emprestimos = Emprestimo.objects.count()

    # Status dos empréstimos
    emprestimos_ativos = Emprestimo.objects.filter(data_devolucao__isnull=True).count()
    emprestimos_atrasados_count = Emprestimo.objects.filter(
        data_devolucao__isnull=True, 
        previsao_devolucao__lt=hoje
    ).count()
    emprestimos_devolvidos = Emprestimo.objects.filter(data_devolucao__isnull=False).count()

    # Percentuais para o gráfico de status
    if total_emprestimos > 0:
        percentual_ativos = (emprestimos_ativos / total_emprestimos) * 100
        percentual_atrasados = (emprestimos_atrasados_count / total_emprestimos) * 100
        percentual_devolvidos = (emprestimos_devolvidos / total_emprestimos) * 100
    else:
        percentual_ativos = percentual_atrasados = percentual_devolvidos = 0

    # Livros mais emprestados
    livros_mais_emprestados = Emprestimo.objects.values('exemplar__titulo__titulo_da_obra', 'exemplar__titulo__autor')\
        .annotate(total=Count('exemplar__titulo'))\
        .order_by('-total')[:10]

    # Usuários mais ativos
    usuarios_mais_ativos = Emprestimo.objects.values('usuario__first_name', 'usuario__last_name', 'usuario__dre')\
        .annotate(total=Count('usuario'))\
        .order_by('-total')[:10]

    # Empréstimos por mês (últimos 12 meses)
    emprestimos_por_mes = Emprestimo.objects.filter(
        data_emprestimo__gte=timezone.now() - timedelta(days=365)
    ).annotate(mes=TruncMonth('data_emprestimo'))\
        .values('mes').annotate(total=Count('id')).order_by('mes')

    # Lista de empréstimos atrasados
    emprestimos_atrasados_lista = Emprestimo.objects.filter(
        data_devolucao__isnull=True,
        previsao_devolucao__lt=hoje
    ).select_related(
        'usuario',
        'exemplar__titulo'
    ).order_by('previsao_devolucao')

    context = {
        # Estatísticas gerais
        'total_usuarios': total_usuarios,
        'total_titulos': total_titulos,
        'total_exemplares': total_exemplares,
        'total_emprestimos': total_emprestimos,

        # Status dos empréstimos
        'emprestimos_ativos': emprestimos_ativos,
        'emprestimos_atrasados': emprestimos_atrasados_count,
        'emprestimos_devolvidos': emprestimos_devolvidos,
        'percentual_ativos': percentual_ativos,
        'percentual_atrasados': percentual_atrasados,
        'percentual_devolvidos': percentual_devolvidos,

        # Rankings
        'livros_mais_emprestados': livros_mais_emprestados,
        'usuarios_mais_ativos': usuarios_mais_ativos,

        # Gráfico
        'emprestimos_por_mes': list(emprestimos_por_mes),

        # Lista de atrasos
        'emprestimos_atrasados_lista': emprestimos_atrasados_lista,
    }
    
    return render(request, 'biblioteca/admin/relatorios.html', context)


# API Views para AJAX
@login_required
@require_http_methods(["GET"])
def api_exemplares_titulo(request, titulo_id):
    """API para buscar exemplares de um título específico"""
    exemplares = Exemplar.objects.filter(
        titulo_id=titulo_id,
        disponivel=True
    ).values('id', 'codigo_exemplar')
    
    return JsonResponse(list(exemplares), safe=False)


@login_required
@require_http_methods(["POST"])
def api_verificar_disponibilidade(request):
    """API para verificar disponibilidade de exemplar"""
    data = json.loads(request.body)
    codigo_exemplar = data.get('codigo_exemplar')
    
    try:
        exemplar = Exemplar.objects.get(codigo_exemplar=codigo_exemplar)
        return JsonResponse({
            'disponivel': exemplar.disponivel,
            'titulo': exemplar.titulo.titulo_da_obra,
            'autor': exemplar.titulo.autor
        })
    except Exemplar.DoesNotExist:
        return JsonResponse({'error': 'Exemplar não encontrado'}, status=404)
