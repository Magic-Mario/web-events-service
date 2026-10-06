# web-events-service

Microservicio **interno** del juego de simulación de vida
(`002-simulacion-vida`): devuelve eventos jugables derivados de sucesos reales
de la época en la que se encuentra el personaje.

- **Rol gRPC**: es el **servidor** de `events.v1`; el cliente es
  `cloud-project-backend` (constitución, principio III — gRPC reservado a
  tráfico este-oeste interno).
- **Dónde corre**: como carga dentro del **clúster EKS `backend`** (el mismo
  que el backend), `Service` tipo `ClusterIP` en el puerto **50051**. **No**
  tiene clúster propio y **no** aparece en el ALB: el gRPC jamás cruza el
  borde. (research, Decisión 4)
- **Fuente**: `es.wikipedia.org` por MediaWiki Action API, **sin clave de API**
  y solo HTTPS (principio V y Decisión 1 de `research.md`).
- **Caché**: `${project}-events-cache` en DynamoDB, **propiedad exclusiva** de
  este servicio — el backend no puede tocarla (FR-003a, SC-010: cada año se
  consulta a internet **una sola vez**).
- **Fallback**: si la fuente externa falla o no hay red, responde con el
  catálogo semilla embebido y `origin = SEED`; la partida nunca se bloquea.

## Layout

- `server/` — servidor gRPC `events.v1`, puerto 50051
  - `_generated/` — stubs (**no** trackeados; ver `.gitignore`)
- `sources/wikipedia.py` — búsqueda en `es.wikipedia.org`
- `sources/classifier.py` — de suceso histórico a evento jugable
  (sección/palabras clave → tipo, rango de edad, opciones)
- `repository.py` — repositorio de `${project}-events-cache`
- `seed_events.json` — catálogo semilla de reserva
- `proto/events.proto` — vendorizado desde el tag `contracts/vX.Y.Z` del repo
  backend (el backend es el **dueño** del contrato)
- `contracts.version` — tag consumido (mismo mecanismo que el frontend)
- `k8s/` — `deployment.yaml` + `service.yaml` (`ClusterIP:50051`)
- `.github/workflows/deploy.yaml` — build → push a su ECR → apply de su `k8s/`

## Uso local

```bash
uv sync
bash tools/gen_proto.sh proto/events.proto   # stubs del servidor
uv run pytest                                # tests
uv run python -m server                      # gRPC en :50051
```

Ver `docs/LOCAL_DEV.md` en `cloud-project-infra` para el stack completo.
