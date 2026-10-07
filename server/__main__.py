"""Punto de entrada del microservicio: servidor gRPC `events.v1` (T047).

Escucha en el puerto **50051** dentro del clúster `backend` — un `ClusterIP`
al que sólo llega el backend (research Decisión 4): **el gRPC jamás cruza el
ALB** ni se expone fuera del clúster.

Arranque:

    uv run python -m server          # local
    python -m server                 # dentro de la imagen (T049)

Si DynamoDB o Wikipedia no están disponibles el servicer sigue respondiendo
con la semilla (Decisión 6): el proceso **no** debe caerse.
"""

from __future__ import annotations

import logging
import signal
import sys
from concurrent import futures

import grpc

from config import GRPC_PORT
from ._generated import events_pb2_grpc
from .servicer import build_servicer

#: Hilos de atención de peticiones gRPC.
MAX_WORKERS = 8

logger = logging.getLogger("web-events-service")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=MAX_WORKERS))
    events_pb2_grpc.add_EventsServiceServicer_to_server(build_servicer(), server)
    bound = server.add_insecure_port(f"[::]:{GRPC_PORT}")
    if bound == 0:
        logger.error("no se ha podido abrir el puerto %s", GRPC_PORT)
        return 1

    server.start()
    logger.info("events.v1 escuchando en el puerto %s", GRPC_PORT)

    def shutdown(signum, _frame) -> None:  # noqa: ANN001
        logger.info("señal %s: cerrando el servidor", signum)
        server.stop(grace=5)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    server.wait_for_termination()
    return 0


if __name__ == "__main__":
    sys.exit(main())
