"""Configuración del microservicio de eventos por variables de entorno."""

from __future__ import annotations

import os

# Prefijo de los recursos de infraestructura — `variable "project"` de
# `cloud-project-infra/variables.tf`. De aquí sale el nombre de la tabla
# `${PROJECT}-events-cache`, de **dueño único** este servicio (principio III):
# ni el backend ni nadie más puede tocarla.
PROJECT = os.getenv("PROJECT", "life-simulator-mvp")
EVENTS_TABLE = f"{PROJECT}-events-cache"

# DynamoDB. El defecto apunta a Floci en local (principio II, "local-first");
# en el clúster se sobreescribe con la variable de entorno del deployment.
DYNAMODB_ENDPOINT = os.getenv("DYNAMODB_ENDPOINT", "http://localhost:4566")
DYNAMODB_REGION = os.getenv("AWS_REGION", "us-east-1")

# Puerto del servidor gRPC `events.v1` (T047). El Service es ClusterIP en
# 50051: el gRPC jamás cruza el ALB (research, Decisión 4).
GRPC_PORT = int(os.getenv("GRPC_PORT", "50051"))

# MediaWiki Action API sobre `es.wikipedia.org`, **sin clave** y solo HTTPS
# (principio V, research Decisión 1). Timeout corto: si no contesta, se
# responde con la semilla en vez de bloquear la partida (Decisión 6).
WIKIPEDIA_TIMEOUT = float(os.getenv("WIKIPEDIA_TIMEOUT", "3.0"))
WIKIPEDIA_BASE_URL = os.getenv(
    "WIKIPEDIA_BASE_URL", "https://es.wikipedia.org/w/api.php"
)
WIKIPEDIA_USER_AGENT = os.getenv(
    "WIKIPEDIA_USER_AGENT",
    "web-events-service/0.1 (juego de simulacion de vida; sin clave de API)",
)
