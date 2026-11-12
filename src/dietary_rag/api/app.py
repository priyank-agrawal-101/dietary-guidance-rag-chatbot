"""FastAPI transport around the shared, policy-gated and validated answering path."""

import json
import sysconfig
import threading
from pathlib import Path

from dietary_rag.api.context import remaining_seconds, request_context
from dietary_rag.api.contracts import (
    ERROR_HTTP_STATUS,
    ChatRequest,
    ChatResponse,
    DocumentListResponse,
    LivenessResponse,
    ReadinessResponse,
    ServiceError,
    service_error,
)
from dietary_rag.api.runtime import ActiveRuntime, build_answer_service, load_environment
from dietary_rag.corpus.registry import load_registry
from dietary_rag.operations.metrics import CURRENT_METRICS, Metrics
from dietary_rag.settings import load_settings


def create_app(
    *,
    project_root=None,
    config_path=Path("config/settings.yaml"),
    env_file=None,
    settings=None,
    registry=None,
    active_runtime=None,
    service=None,
    ledger=None,
    environ=None,
    offline=False,
):
    import anyio
    from fastapi import FastAPI, Request
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import FileResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles

    root = Path(project_root or Path.cwd()).resolve()
    env = environ if environ is not None else load_environment(root, env_file)
    settings = settings or load_settings(config_path, project_root=root, environ=env)
    registry = registry or load_registry(settings.source_registry)
    active_runtime = active_runtime or ActiveRuntime(settings, registry, root)
    if service is None:
        service, built_ledger = build_answer_service(
            settings, active_runtime, environ=env, offline=offline
        )
        ledger = ledger or built_ledger
    metrics = Metrics()
    app = FastAPI(title="Dietary Guidance RAG", version="0.1.0")
    web_directory = root / "web"
    if not (web_directory / "index.html").is_file():
        web_directory = Path(sysconfig.get_path("data")) / "share" / "dietary-rag" / "web"
    error_responses = {
        status: {"model": ServiceError} for status in set(ERROR_HTTP_STATUS.values())
    }
    # Slots remain held by workers even after an HTTP deadline cancels their waiter.
    slots = threading.BoundedSemaphore(settings.api.max_in_flight)
    status_slots = threading.BoundedSemaphore(2)

    def response(value):
        headers = {}
        if isinstance(value, ServiceError) and value.retry_after_seconds is not None:
            headers["Retry-After"] = str(value.retry_after_seconds)
        return JSONResponse(
            value.model_dump(mode="json") if hasattr(value, "model_dump") else value,
            status_code=value.http_status if isinstance(value, ServiceError) else 200,
            headers=headers,
        )

    async def bounded(call, capacity=slots):
        def run():
            if not capacity.acquire(blocking=False):
                from dietary_rag.api.context import CURRENT_REQUEST

                return service_error(CURRENT_REQUEST.get().request_id, "service_busy")
            try:
                remaining_seconds(settings.runtime.request_timeout_seconds)
                return call()
            finally:
                capacity.release()

        return await anyio.to_thread.run_sync(run, abandon_on_cancel=True)

    class RequestBoundary:
        # Pure ASGI keeps cancellation independent of BaseHTTPMiddleware task groups.
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                await self.app(scope, receive, send)
                return
            with request_context(settings.runtime.request_timeout_seconds) as context:
                started = False

                async def identified_send(message):
                    nonlocal started
                    if message["type"] == "http.response.start":
                        started = True
                        message = dict(message)
                        message["headers"] = list(message.get("headers", [])) + [
                            (b"x-request-id", context.request_id.encode("ascii")),
                            (b"x-content-type-options", b"nosniff"),
                            (b"referrer-policy", b"no-referrer"),
                        ]
                        if scope["path"] in {"/", "/diagnostics"} or scope["path"].startswith(
                            "/assets/"
                        ):
                            message["headers"].append(
                                (
                                    b"content-security-policy",
                                    b"default-src 'none'; script-src 'self'; style-src 'self'; "
                                    b"img-src 'self'; connect-src 'self'; base-uri 'none'; "
                                    b"form-action 'self'; frame-ancestors 'none'",
                                )
                            )
                    await send(message)

                try:
                    with anyio.fail_after(
                        remaining_seconds(settings.runtime.request_timeout_seconds)
                    ):
                        await self.app(scope, receive, identified_send)
                except TimeoutError:
                    if not started:
                        await response(service_error(context.request_id, "request_timeout"))(
                            scope, receive, identified_send
                        )
                except Exception:
                    if not started:
                        await response(service_error(context.request_id, "model_unavailable"))(
                            scope, receive, identified_send
                        )

    app.add_middleware(RequestBoundary)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        from dietary_rag.api.context import CURRENT_REQUEST

        return response(service_error(CURRENT_REQUEST.get().request_id, "invalid_request"))

    @app.get("/health/live", response_model=LivenessResponse)
    async def live():
        return {"status": "alive"}

    @app.get("/", include_in_schema=False)
    async def chat_ui():
        return FileResponse(web_directory / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/diagnostics", include_in_schema=False)
    async def diagnostics_ui():
        return FileResponse(
            web_directory / "diagnostics.html", headers={"Cache-Control": "no-cache"}
        )

    @app.get("/ui/config")
    async def ui_config():
        return {
            "question_max_characters": settings.runtime.question_max_characters,
            "selected_document_limit": settings.runtime.selected_document_limit,
            "request_timeout_seconds": settings.runtime.request_timeout_seconds,
        }

    def corpus():
        from dietary_rag.api.context import CURRENT_REQUEST

        try:
            return active_runtime.corpus_status()
        except TimeoutError:
            return service_error(CURRENT_REQUEST.get().request_id, "request_timeout")
        except Exception:
            return service_error(CURRENT_REQUEST.get().request_id, "corpus_unavailable")

    @app.get("/health/ready", response_model=ReadinessResponse | ServiceError)
    async def ready():
        state = await bounded(corpus, status_slots)
        if isinstance(state, ServiceError):
            return response(state)
        generator = service.generator
        if generator is None or getattr(generator, "configured", True) is False:
            from dietary_rag.api.context import CURRENT_REQUEST

            return response(service_error(CURRENT_REQUEST.get().request_id, "model_unavailable"))
        return {
            "status": "ready",
            "corpus_version": state["corpus_version"],
            "release_id": state["release_id"],
            "activation_generation": state["activation_generation"],
            "available_documents": len(state["documents"]),
            "generation": getattr(generator, "name", "configured"),
        }

    @app.get("/documents", response_model=DocumentListResponse | ServiceError)
    async def documents():
        from dietary_rag.api.context import CURRENT_REQUEST

        state = await bounded(corpus, status_slots)
        # An unavailable corpus still exposes reviewed metadata with every source unavailable.
        if isinstance(state, ServiceError) and state.code != "corpus_unavailable":
            return response(state)
        available = {} if isinstance(state, ServiceError) else state["documents"]
        entries = []
        for source in registry.sources:
            document = available.get(source.document_id)
            metadata = document or source
            entries.append(
                {
                    "document_id": source.document_id,
                    "document_name": metadata.document_name,
                    "publisher": metadata.publisher,
                    "source_url": str(metadata.source_url),
                    "format": metadata.format,
                    "source_tier": metadata.source_tier,
                    "jurisdiction": metadata.jurisdiction,
                    "target_audience": metadata.target_audience,
                    "content_role": metadata.content_role,
                    "dates": metadata.dates.model_dump(mode="json"),
                    "available": document is not None,
                    "availability_reason": "active"
                    if document
                    else "corpus_unavailable"
                    if isinstance(state, ServiceError)
                    else "not_in_active_corpus",
                }
            )
        result = {
            "request_id": CURRENT_REQUEST.get().request_id,
            "corpus_version": None if isinstance(state, ServiceError) else state["corpus_version"],
            "release_id": None if isinstance(state, ServiceError) else state["release_id"],
            "documents": entries,
            "error": state.model_dump(mode="json") if isinstance(state, ServiceError) else None,
        }
        return JSONResponse(
            DocumentListResponse.model_validate(result).model_dump(mode="json"),
            status_code=503 if isinstance(state, ServiceError) else 200,
        )

    @app.post(
        "/chat",
        response_model=ChatResponse | ServiceError,
        responses=error_responses,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {"schema": {"$ref": "#/components/schemas/ChatRequest"}}
                },
            }
        },
    )
    async def chat(request: Request):
        from dietary_rag.api.context import CURRENT_REQUEST

        try:
            data = bytearray()
            async for part in request.stream():
                data.extend(part)
                if len(data) > settings.api.max_body_bytes:
                    raise ValueError("request body exceeds limit")
            payload = json.loads(data)
        except (ValueError, TypeError):
            return response(service_error(CURRENT_REQUEST.get().request_id, "invalid_request"))

        def answer():
            token = CURRENT_METRICS.set(metrics)
            try:
                result = service.answer(payload)
                metrics.outcome(result)
                return result
            finally:
                CURRENT_METRICS.reset(token)

        return response(await bounded(answer))

    @app.get("/metrics")
    async def metrics_summary():
        return metrics.snapshot()

    @app.get("/usage")
    async def usage():
        from dietary_rag.api.context import CURRENT_REQUEST

        if ledger is None:
            return {"generation": "offline", "usage": None}

        def inspect():
            try:
                return ledger.summary()
            except Exception:
                return service_error(CURRENT_REQUEST.get().request_id, "model_unavailable")

        return response(await bounded(inspect))

    def openapi():
        # Body parsing stays bounded and manual; publish the shared request schema.
        if app.openapi_schema is None:
            from fastapi.openapi.utils import get_openapi

            schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
            request_schema = ChatRequest.model_json_schema(
                ref_template="#/components/schemas/{model}"
            )
            definitions = request_schema.pop("$defs", {})
            schema["components"]["schemas"].update(definitions)
            schema["components"]["schemas"]["ChatRequest"] = request_schema
            app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = openapi
    app.mount("/assets", StaticFiles(directory=web_directory / "assets", check_dir=False))
    return app
