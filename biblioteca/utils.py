from django.utils import timezone
from django.core.mail import send_mail
from django.conf import settings
from datetime import timedelta, date
from .models import Emprestimo, Usuario, HistoricoEmprestimo, Titulo, Exemplar


class EmprestimoService:
    """Serviço para gerenciar regras de negócio de empréstimos"""
    
    PRAZO_PADRAO_DIAS = 30
    LIMITE_LIVROS_POR_USUARIO = 3
    DIAS_NOTIFICACAO_ANTECIPADA = 7
    
    @classmethod
    def pode_emprestar(cls, usuario, exemplar):
        """
        Verifica se um usuário pode emprestar um exemplar específico
        Retorna (pode_emprestar: bool, motivo: str)
        """
        # Verificar se usuário está ativo
        if not usuario.is_active:
            return False, "Usuário inativo"
        
        # Verificar se usuário está suspenso
        if usuario.esta_suspenso():
            return False, f"Usuário suspenso até {usuario.data_suspensao.strftime('%d/%m/%Y')}"
        
        # Verificar limite de livros
        emprestimos_ativos = Emprestimo.objects.filter(
            usuario=usuario,
            data_devolucao__isnull=True
        ).count()
        
        if emprestimos_ativos >= cls.LIMITE_LIVROS_POR_USUARIO:
            return False, f"Limite de {cls.LIMITE_LIVROS_POR_USUARIO} livros atingido"
        
        # Verificar se exemplar está disponível
        if not exemplar.disponivel:
            return False, "Exemplar não disponível"
        
        # Verificar se usuário já tem este título emprestado
        titulo_ja_emprestado = Emprestimo.objects.filter(
            usuario=usuario,
            exemplar__titulo=exemplar.titulo,
            data_devolucao__isnull=True
        ).exists()
        
        if titulo_ja_emprestado:
            return False, "Usuário já possui um exemplar deste título"
        
        return True, "Pode emprestar"
    
    @classmethod
    def realizar_emprestimo(cls, usuario, exemplar, observacoes=""):
        """
        Realiza um empréstimo seguindo todas as regras de negócio
        Retorna (emprestimo: Emprestimo, sucesso: bool, mensagem: str)
        """
        pode_emprestar, motivo = cls.pode_emprestar(usuario, exemplar)
        
        if not pode_emprestar:
            return None, False, motivo
        
        # Criar empréstimo
        emprestimo = Emprestimo.objects.create(
            usuario=usuario,
            exemplar=exemplar,
            previsao_devolucao=timezone.now().date() + timedelta(days=cls.PRAZO_PADRAO_DIAS),
            observacoes=observacoes
        )
        
        # Criar histórico
        HistoricoEmprestimo.objects.create(emprestimo=emprestimo)
        
        # Enviar email de confirmação
        cls.enviar_email_confirmacao_emprestimo(emprestimo)
        
        return emprestimo, True, "Empréstimo realizado com sucesso"
    
    @classmethod
    def realizar_devolucao(cls, exemplar, observacoes=""):
        """
        Realiza a devolução de um exemplar
        Retorna (emprestimo: Emprestimo, sucesso: bool, mensagem: str)
        """
        try:
            emprestimo = Emprestimo.objects.get(
                exemplar=exemplar,
                data_devolucao__isnull=True
            )
        except Emprestimo.DoesNotExist:
            return None, False, "Exemplar não está emprestado"
        
        # Verificar se está atrasado
        dias_atraso = 0
        if emprestimo.esta_atrasado():
            dias_atraso = emprestimo.dias_atraso()
        
        # Realizar devolução
        emprestimo.data_devolucao = timezone.now()
        if observacoes:
            emprestimo.observacoes += f"\nDevolução: {observacoes}"
        
        # Aplicar suspensão se houver atraso
        if dias_atraso > 0:
            cls.aplicar_suspensao(emprestimo.usuario, dias_atraso)
        
        # Atualizar disponibilidade do exemplar
        exemplar.disponivel = True
        exemplar.save()
        emprestimo.save()
        
        mensagem = "Devolução realizada com sucesso"
        if dias_atraso > 0:
            mensagem += f". Usuário suspenso por {dias_atraso} dias devido ao atraso"
        
        return emprestimo, True, mensagem
    
    @classmethod
    def aplicar_suspensao(cls, usuario, dias_atraso):
        """Aplica suspensão ao usuário baseada nos dias de atraso"""
        data_suspensao = timezone.now().date() + timedelta(days=dias_atraso)
        
        # Se já tem suspensão, usar a data mais distante
        if usuario.data_suspensao:
            if data_suspensao > usuario.data_suspensao:
                usuario.data_suspensao = data_suspensao
        else:
            usuario.data_suspensao = data_suspensao
        
        usuario.save()
    
    @classmethod
    def renovar_emprestimo(cls, emprestimo, dias_renovacao=30):
        """
        Renova um empréstimo por mais dias
        Retorna (sucesso: bool, mensagem: str)
        """
        if emprestimo.data_devolucao:
            return False, "Empréstimo já foi devolvido"
        
        if emprestimo.esta_atrasado():
            return False, "Não é possível renovar empréstimo em atraso"
        
        # Verificar se já foi renovado muitas vezes
        historico, created = HistoricoEmprestimo.objects.get_or_create(
            emprestimo=emprestimo
        )
        
        if historico.renovacoes >= 2:
            return False, "Limite de renovações atingido (máximo 2)"
        
        # Renovar
        emprestimo.previsao_devolucao += timedelta(days=dias_renovacao)
        emprestimo.save()
        
        historico.renovacoes += 1
        historico.save()
        
        return True, f"Empréstimo renovado por {dias_renovacao} dias"
    
    @classmethod
    def enviar_email_confirmacao_emprestimo(cls, emprestimo):
        """Envia email de confirmação de empréstimo"""
        try:
            assunto = f"Confirmação de Empréstimo - {emprestimo.exemplar.titulo.titulo_da_obra}"
            mensagem = f"""
Olá {emprestimo.usuario.first_name},

Seu empréstimo foi realizado com sucesso!

Detalhes:
- Livro: {emprestimo.exemplar.titulo.titulo_da_obra}
- Autor: {emprestimo.exemplar.titulo.autor}
- Código do Exemplar: {emprestimo.exemplar.codigo_exemplar}
- Data do Empréstimo: {emprestimo.data_emprestimo.strftime('%d/%m/%Y às %H:%M')}
- Data de Devolução: {emprestimo.previsao_devolucao.strftime('%d/%m/%Y')}

Lembre-se de devolver o livro na data prevista para evitar suspensão.

Atenciosamente,
Sistema de Biblioteca
            """
            
            send_mail(
                assunto,
                mensagem,
                settings.DEFAULT_FROM_EMAIL,
                [emprestimo.usuario.email],
                fail_silently=True
            )
        except Exception as e:
            print(f"Erro ao enviar email: {e}")
    
    @classmethod
    def enviar_notificacoes_vencimento(cls):
        """
        Envia notificações para empréstimos que vencem em breve
        Deve ser executado diariamente via cron job
        """
        data_limite = timezone.now().date() + timedelta(days=cls.DIAS_NOTIFICACAO_ANTECIPADA)
        
        emprestimos_vencendo = Emprestimo.objects.filter(
            data_devolucao__isnull=True,
            previsao_devolucao__lte=data_limite,
            previsao_devolucao__gte=timezone.now().date()
        )
        
        for emprestimo in emprestimos_vencendo:
            # Verificar se já foi notificado hoje
            historico, created = HistoricoEmprestimo.objects.get_or_create(
                emprestimo=emprestimo
            )
            
            hoje = timezone.now().date()
            if historico.ultima_notificacao and historico.ultima_notificacao.date() == hoje:
                continue  # Já notificado hoje
            
            cls.enviar_email_vencimento(emprestimo)
            
            # Atualizar histórico
            historico.notificacoes_enviadas += 1
            historico.ultima_notificacao = timezone.now()
            historico.save()
    
    @classmethod
    def enviar_email_vencimento(cls, emprestimo):
        """Envia email de aviso de vencimento"""
        try:
            dias_restantes = emprestimo.dias_para_vencimento()
            
            if dias_restantes == 0:
                assunto = f"URGENTE: Devolução hoje - {emprestimo.exemplar.titulo.titulo_da_obra}"
                urgencia = "HOJE"
            elif dias_restantes == 1:
                assunto = f"URGENTE: Devolução amanhã - {emprestimo.exemplar.titulo.titulo_da_obra}"
                urgencia = "AMANHÃ"
            else:
                assunto = f"Lembrete: Devolução em {dias_restantes} dias - {emprestimo.exemplar.titulo.titulo_da_obra}"
                urgencia = f"em {dias_restantes} dias"
            
            mensagem = f"""
Olá {emprestimo.usuario.first_name},

Este é um lembrete de que você deve devolver o livro {urgencia}.

Detalhes do Empréstimo:
- Livro: {emprestimo.exemplar.titulo.titulo_da_obra}
- Autor: {emprestimo.exemplar.titulo.autor}
- Código do Exemplar: {emprestimo.exemplar.codigo_exemplar}
- Data de Devolução: {emprestimo.previsao_devolucao.strftime('%d/%m/%Y')}

IMPORTANTE: A devolução em atraso resultará em suspensão por período igual ao atraso.

Para devolver, compareça à biblioteca ou escaneie o QR Code do livro.

Atenciosamente,
Sistema de Biblioteca
            """
            
            send_mail(
                assunto,
                mensagem,
                settings.DEFAULT_FROM_EMAIL,
                [emprestimo.usuario.email],
                fail_silently=True
            )
        except Exception as e:
            print(f"Erro ao enviar email de vencimento: {e}")


class RelatorioService:
    """Serviço para gerar relatórios administrativos"""
    
    @classmethod
    def livros_mais_emprestados(cls, limite=10):
        """Retorna os livros mais emprestados"""
        from django.db.models import Count
        
        return Titulo.objects.annotate(
            total_emprestimos=Count('exemplares__emprestimos')
        ).order_by('-total_emprestimos')[:limite]
    
    @classmethod
    def usuarios_mais_ativos(cls, limite=10):
        """Retorna os usuários com mais empréstimos"""
        from django.db.models import Count
        
        return Usuario.objects.annotate(
            total_emprestimos=Count('emprestimos')
        ).order_by('-total_emprestimos')[:limite]
    
    @classmethod
    def emprestimos_atrasados(cls):
        """Retorna empréstimos em atraso"""
        return Emprestimo.objects.filter(
            data_devolucao__isnull=True,
            previsao_devolucao__lt=timezone.now().date()
        ).order_by('previsao_devolucao')
    
    @classmethod
    def estatisticas_gerais(cls):
        """Retorna estatísticas gerais do sistema"""
        from django.db.models import Count, Avg
        
        total_usuarios = Usuario.objects.count()
        usuarios_ativos = Usuario.objects.filter(is_active=True).count()
        usuarios_suspensos = Usuario.objects.filter(
            data_suspensao__gte=timezone.now().date()
        ).count()
        
        total_titulos = Titulo.objects.count()
        total_exemplares = Exemplar.objects.count()
        exemplares_disponiveis = Exemplar.objects.filter(disponivel=True).count()
        
        total_emprestimos = Emprestimo.objects.count()
        emprestimos_ativos = Emprestimo.objects.filter(data_devolucao__isnull=True).count()
        emprestimos_atrasados = Emprestimo.objects.filter(
            data_devolucao__isnull=True,
            previsao_devolucao__lt=timezone.now().date()
        ).count()
        
        return {
            'usuarios': {
                'total': total_usuarios,
                'ativos': usuarios_ativos,
                'suspensos': usuarios_suspensos,
                'taxa_atividade': (usuarios_ativos / total_usuarios * 100) if total_usuarios > 0 else 0
            },
            'acervo': {
                'total_titulos': total_titulos,
                'total_exemplares': total_exemplares,
                'exemplares_disponiveis': exemplares_disponiveis,
                'taxa_disponibilidade': (exemplares_disponiveis / total_exemplares * 100) if total_exemplares > 0 else 0
            },
            'emprestimos': {
                'total': total_emprestimos,
                'ativos': emprestimos_ativos,
                'atrasados': emprestimos_atrasados,
                'taxa_atraso': (emprestimos_atrasados / emprestimos_ativos * 100) if emprestimos_ativos > 0 else 0
            }
        }


class ValidacaoService:
    """Serviço para validações específicas do sistema"""
    
    @classmethod
    def validar_isbn(cls, isbn):
        """Valida formato de ISBN"""
        if not isbn:
            return True  # ISBN é opcional
        
        # Remover hífens e espaços
        isbn_limpo = isbn.replace('-', '').replace(' ', '')
        
        # Verificar se tem 10 ou 13 dígitos
        if len(isbn_limpo) not in [10, 13]:
            return False
        
        # Verificar se são todos dígitos (exceto último que pode ser X no ISBN-10)
        if len(isbn_limpo) == 10:
            return isbn_limpo[:-1].isdigit() and (isbn_limpo[-1].isdigit() or isbn_limpo[-1].upper() == 'X')
        else:
            return isbn_limpo.isdigit()
    
    @classmethod
    def validar_dre(cls, dre):
        """Valida formato de DRE"""
        if not dre:
            return False
        
        # DRE deve ter pelo menos 3 caracteres
        return len(dre.strip()) >= 3
    
    @classmethod
    def validar_telefone(cls, telefone):
        """Valida formato de telefone brasileiro"""
        import re
        
        if not telefone:
            return False
        
        # Padrão para telefone brasileiro
        padrao = r'^\+?55?[1-9]{2}9?[0-9]{8}$'
        telefone_limpo = re.sub(r'[^\d+]', '', telefone)
        
        return bool(re.match(padrao, telefone_limpo))
