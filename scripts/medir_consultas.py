import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "biblioteca_sistema.settings")

import django

django.setup()

from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext


def medir(nome, cliente, rota):
    with CaptureQueriesContext(connection) as consultas:
        resposta = cliente.get(rota)

    print(
        f"{nome}: status={resposta.status_code}, "
        f"consultas={len(consultas)}"
    )


publico = Client()
administrador = Client()

if not administrador.login(username="admin", password="admin123"):
    raise SystemExit("Não foi possível autenticar o administrador.")

medir("acervo", publico, "/acervo/")
medir("dashboard", administrador, "/dashboard/")
medir("painel", administrador, "/painel/")
medir("titulos", administrador, "/painel/titulos/")
medir("exemplares", administrador, "/painel/exemplares/")
medir("emprestimos", administrador, "/painel/emprestimos/")
medir("usuarios", administrador, "/painel/usuarios/")
medir("relatorios", administrador, "/painel/relatorios/")