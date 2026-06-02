# Observability — logs structurés et tracing

Pipeline interne, sans dépendance externe (pas de Sentry, pas d'OTel
collector). Tout sort en JSON sur stdout — Scalingo agrège, et n'importe
quel log drain compatible (Logz.io, Datadog Logs, Better Stack) peut être
branché ensuite.

## Schéma de champs

Chaque ligne JSON porte **systématiquement** :

| Champ      | Source                       | Description                                  |
|------------|------------------------------|----------------------------------------------|
| `ts`       | logging                      | ISO 8601 UTC                                 |
| `level`    | logging                      | `INFO` / `WARNING` / `ERROR` / …             |
| `logger`   | logging                      | nom du logger (`app.request`, `app.sql`, …)  |
| `trace_id` | `ContextFilter`              | 32 hex — corrèle toute la requête / job      |
| `span_id`  | `ContextFilter`              | 16 hex — unique à la requête / job courant   |
| `request_id` | alias de `trace_id`         | conservé pour compat avec les anciens logs   |
| `user_id`  | `ContextFilter`              | `g.current_user.id` si auth, sinon `null`    |
| `ip`       | `ContextFilter`              | voir « Résolution de l'IP » plus bas         |

`user_id` et `ip` sont posés automatiquement sur **toute** ligne émise
pendant une requête HTTP — pas besoin de passer `extra=`. Hors requête
(CLI, worker Dramatiq), ils sont `null` à moins qu'un `bind()` les
fournisse explicitement.

Sur la ligne `event=http_request` (un log par requête, émis par
`@app.after_request`) :

| Champ              | Exemple              | Notes                                       |
|--------------------|----------------------|---------------------------------------------|
| `event`            | `http_request`       |                                             |
| `http.method`      | `POST`               | convention OTel / Datadog Standard Attrs    |
| `http.url`         | `/caterer/requests/…`|                                             |
| `http.status_code` | `200`                | nommé ainsi pour éviter le clash avec le `status` réservé Datadog (sévérité) |
| `http.useragent`   | …                    |                                             |
| `http.referer`     | …                    | peut être `null`                            |
| `endpoint`         | `caterer.requests.…` | nom Flask, plus stable que `http.url`       |
| `duration_ms`      | `42.18`              | unité dans le nom, vendor-neutral           |
| `req_bytes`        | `1024`               | `Content-Length` entrant                    |
| `sql_queries`      | `7`                  | agrégat via SQLAlchemy events               |
| `sql_ms`           | `13.4`               | total cumulé des requêtes SQL               |
| `user_id`          | `uuid`               | seulement si authentifié (stampé par filter)|
| `user_role`        | `client_admin`       |                                             |
| `company_id`       | `uuid`               | si applicable                               |

Sur erreur non gérée, `event=unhandled_exception` ajoute `exc_type` et le
stack trace via `logger.exception` (signal `got_request_exception`).

Sur requête SQL dépassant `LOG_SLOW_QUERY_MS` (défaut **500 ms**) :
`event=slow_query` avec `duration_ms` et `statement` (tronqué à 500
caractères).

## Résolution de l'IP

L'extraction de `ip` est **gardée par `settings.trust_proxy_headers`** (même
flag qui active `ProxyFix`). C'est délibéré — `X-Forwarded-For` et
`X-Real-IP` sont des en-têtes contrôlés par le client, et les lire sans
proxy de confiance en amont permettrait à n'importe qui de forger l'IP
loguée (cf. `.env.example`).

| `trust_proxy_headers` | Source de `ip`                                     |
|-----------------------|----------------------------------------------------|
| `True` (prod Scalingo)| `X-Forwarded-For` (1er hop) → `X-Real-IP` → `remote_addr` |
| `False` (dev, par déf.)| `request.remote_addr` (peer TCP, non spoofable)   |

Sur Scalingo le routeur réécrit XFF lui-même, donc la valeur est fiable.
En self-host, ne mettez `TRUST_PROXY_HEADERS=1` que si un proxy
(Caddy/nginx) en front réécrit XFF — sinon vos logs IP sont mensongers.

## Propagation de la trace

- **HTTP entrant** : `traceparent` (W3C) est honoré en priorité ; à
  défaut, `X-Request-Id` si hex ; sinon génération locale.
- **HTTP sortant** : `X-Request-Id` + `X-Trace-Id` ajoutés à la réponse.
- **Dramatiq** : `TraceMiddleware` (`services/billing_tasks.py`) injecte
  `trace_id` + `parent_span_id` dans `message.options` côté producteur,
  les rétablit côté worker. Un job sans trace amont reçoit un trace_id
  frais.

## Enrichir un log

```python
from logging_config import bind
import logging

logger = logging.getLogger(__name__)

def accept_quote(quote_id):
    bind(quote_id=str(quote_id), action="accept_quote")
    # Tous les logs émis dans ce contexte porteront ces champs.
    logger.info("quote_accepted")
```

`bind()` est cumulatif et scopé au `ContextVar` courant — il n'affecte
pas les autres requêtes/jobs concurrents.

## Configuration runtime

| Variable             | Défaut  | Effet                                        |
|----------------------|---------|----------------------------------------------|
| `LOG_LEVEL`          | `INFO`  | Niveau root                                  |
| `LOG_SLOW_QUERY_MS`  | `500`   | Seuil pour `event=slow_query`                |

## Côté Scalingo

Les logs JSON sortent sur stdout, captés par `scalingo logs`. Pour les
exploiter, brancher un add-on log drain (Logtail, Papertrail, Datadog) —
ils savent tous parser du JSON et indexer `trace_id`.

Pour filtrer une requête particulière :

```sh
scalingo --app traiteurs-prod logs | grep '"trace_id": "abc123…"'
```

Tous les logs émis pendant cette requête (web + worker s'il y a un
enqueue) partagent le même `trace_id`.
