from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class RotasBasicasTests(TestCase):
    def setUp(self):
        Usuario = get_user_model()

        self.usuario = Usuario.objects.create_user(
            username="usuario_teste",
            password="Senha123!",
            first_name="Usuario",
            last_name="Teste",
            telefone="21999999999",
            dre="TESTE001",
        )

        self.administrador = Usuario.objects.create_user(
            username="admin_teste",
            password="Senha123!",
            first_name="Administrador",
            last_name="Teste",
            telefone="21988888888",
            dre="ADMIN001",
            is_staff=True,
            is_superuser=True,
            is_administrador=True,
        )

    def test_paginas_publicas_carregam(self):
        paginas = ["home", "sobre", "contato", "acervo", "login"]

        for pagina in paginas:
            with self.subTest(pagina=pagina):
                resposta = self.client.get(reverse(pagina))
                self.assertEqual(resposta.status_code, 200)

    def test_paginas_protegidas_exigem_login(self):
        paginas = ["dashboard", "perfil", "admin_dashboard", "relatorios"]

        for pagina in paginas:
            with self.subTest(pagina=pagina):
                resposta = self.client.get(reverse(pagina))
                self.assertEqual(resposta.status_code, 302)

    def test_usuario_acessa_dashboard(self):
        self.client.login(
            username="usuario_teste",
            password="Senha123!",
        )

        resposta = self.client.get(reverse("dashboard"))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Olá")

    def test_administrador_acessa_painel_e_relatorios(self):
        self.client.login(
            username="admin_teste",
            password="Senha123!",
        )

        painel = self.client.get(reverse("admin_dashboard"))
        relatorios = self.client.get(reverse("relatorios"))

        self.assertEqual(painel.status_code, 200)
        self.assertEqual(relatorios.status_code, 200)