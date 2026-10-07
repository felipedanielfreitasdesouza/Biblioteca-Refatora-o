from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch


ROOT = Path(os.environ.get("BIBLIOTECA_ROOT", Path(__file__).resolve().parents[1])).resolve()
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "biblioteca_sistema.settings")

import django

django.setup()

from django.core.management import call_command
from django.db import connection, transaction
from django.test import Client
from django.test.utils import CaptureQueriesContext, setup_test_environment, teardown_test_environment
from django.urls import reverse
from django.utils import timezone

from biblioteca import views
from biblioteca.models import Emprestimo, Exemplar, Titulo, Usuario
from biblioteca.utils import EmprestimoService


def seed_database(size):
    hoje = timezone.now().date()
    admin = Usuario.objects.create_user(
        username="admin_benchmark",
        password="Senha123!",
        first_name="Admin",
        last_name="Benchmark",
        telefone="21988888888",
        dre="ADMINBENCH",
        is_staff=True,
        is_superuser=True,
        is_administrador=True,
    )
    normal = Usuario.objects.create_user(
        username="usuario_benchmark",
        password="Senha123!",
        first_name="Usuario",
        last_name="Benchmark",
        telefone="21999999999",
        dre="USERBENCH",
    )

    usuarios = Usuario.objects.bulk_create([
        Usuario(
            username=f"usuario_{index}",
            first_name="Usuario",
            last_name=f"{index}",
            telefone="21999999999",
            dre=f"DRE{index:08d}",
            is_active=True,
        )
        for index in range(size)
    ])

    titulos = Titulo.objects.bulk_create([
        Titulo(
            lombada=f"LB{index:08d}",
            autor=f"Autor {index}",
            titulo_da_obra=f"Titulo de Benchmark {index}",
            editora="Editora Benchmark",
            ano_publicacao=2020,
            local_publicacao="Macae",
        )
        for index in range(size)
    ])

    exemplares = Exemplar.objects.bulk_create([
        Exemplar(
            titulo=titulo,
            data_aquisicao=hoje,
            disponivel=False,
            codigo_exemplar=f"EX{index:08d}",
        )
        for index, titulo in enumerate(titulos)
    ])

    usuarios_emprestimos = [normal] + usuarios
    emprestimos = Emprestimo.objects.bulk_create([
        Emprestimo(
            usuario=usuarios_emprestimos[index],
            exemplar=exemplar,
            data_emprestimo=timezone.now(),
            previsao_devolucao=hoje + timedelta(days=3),
        )
        for index, exemplar in enumerate(exemplares)
    ])

    return admin, normal, exemplares[0], emprestimos


def measure_request(client, name, path, repetitions):
    client.get(path)
    durations = []
    query_count = None
    status = None

    try:
        for _ in range(repetitions):
            with CaptureQueriesContext(connection) as queries:
                started = time.perf_counter()
                response = client.get(path)
                durations.append((time.perf_counter() - started) * 1000)
            status = response.status_code
            if query_count is None:
                query_count = len(queries)
    except Exception as error:
        return {
            "funcao": name,
            "status": status,
            "consultas": query_count,
            "tempo_medio_ms": None,
            "erro": f"{type(error).__name__}: {error}",
        }

    return {
        "funcao": name,
        "status": status,
        "consultas": query_count,
        "tempo_medio_ms": round(sum(durations) / len(durations), 4),
        "tempo_minimo_ms": round(min(durations), 4),
        "erro": None,
    }


def measure_notifications(name, repetitions):
    durations = []
    query_count = None

    try:
        for _ in range(repetitions):
            with transaction.atomic():
                with patch.object(EmprestimoService, "enviar_email_vencimento", return_value=None):
                    with CaptureQueriesContext(connection) as queries:
                        started = time.perf_counter()
                        EmprestimoService.enviar_notificacoes_vencimento()
                        durations.append((time.perf_counter() - started) * 1000)
                if query_count is None:
                    query_count = len(queries)
                transaction.set_rollback(True)
    except Exception as error:
        return {
            "funcao": name,
            "status": None,
            "consultas": query_count,
            "tempo_medio_ms": None,
            "erro": f"{type(error).__name__}: {error}",
        }

    return {
        "funcao": name,
        "status": 200,
        "consultas": query_count,
        "tempo_medio_ms": round(sum(durations) / len(durations), 4),
        "tempo_minimo_ms": round(min(durations), 4),
        "erro": None,
    }


def measure_size(size, repetitions):
    call_command("flush", verbosity=0, interactive=False)
    admin, normal, exemplar, _ = seed_database(size)

    public_client = Client()
    user_client = Client()
    admin_client = Client()
    user_client.force_login(normal)
    admin_client.force_login(admin)

    original_paginator = views.Paginator

    class SizedPaginator(original_paginator):
        def __init__(self, object_list, per_page, *args, **kwargs):
            super().__init__(object_list, size, *args, **kwargs)

    views.Paginator = SizedPaginator
    try:
        requests = [
            (public_client, "acervo", reverse("acervo")),
            (admin_client, "titulo_list", reverse("titulo_list")),
            (admin_client, "admin_dashboard", reverse("admin_dashboard")),
            (admin_client, "usuario_list", reverse("usuario_list")),
            (admin_client, "relatorios", reverse("relatorios")),
            (user_client, "dashboard", reverse("dashboard")),
            (public_client, "exemplar_detail", reverse("exemplar_detail", args=[exemplar.pk])),
        ]
        results = [
            measure_request(client, name, path, repetitions)
            for client, name, path in requests
        ]
    finally:
        views.Paginator = original_paginator

    results.append(measure_notifications("enviar_notificacoes_vencimento", repetitions))
    return results


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True, choices=["before", "after"])
    parser.add_argument("--sizes", nargs="+", type=int, default=[10, 100, 1000])
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main():
    args = parse_args()
    output = args.output or ROOT / "benchmarks" / f"complexidade_{args.label}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    old_name = None
    environment_ready = False

    try:
        old_name = connection.creation.create_test_db(
            verbosity=0,
            autoclobber=True,
            serialize=False,
        )
        setup_test_environment()
        environment_ready = True
        results = []
        for size in args.sizes:
            for result in measure_size(size, args.repetitions):
                results.append({"n": size, **result})
            print(f"concluido: n={size}")

        payload = {
            "rotulo": args.label,
            "tamanhos": args.sizes,
            "repeticoes": args.repetitions,
            "resultados": results,
        }
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"resultado salvo em: {output}")
    finally:
        if environment_ready:
            teardown_test_environment()
        if old_name is not None:
            connection.creation.destroy_test_db(old_name, verbosity=0)


if __name__ == "__main__":
    main()
