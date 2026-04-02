from __future__ import annotations

import hashlib
import hmac
import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from selfix.signals.router import SignalRouter

logger = logging.getLogger(__name__)


class SelfixWebhookServer:
    """
    HTTP server that receives signals from monitoring, CI, or alerting systems
    and dispatches Selfix pipeline runs.

    Supported routes:
    - POST /signal/error      → ErrorSignal
    - POST /signal/metric     → MetricSignal
    - POST /signal/manual     → ManualSignal
    - POST /webhook/sentry    → Sentry issue webhook → ErrorSignal
    - POST /webhook/datadog   → Datadog monitor alert → MetricSignal
    - POST /webhook/github    → GitHub Actions failure → ErrorSignal
    """

    def __init__(self, router: "SignalRouter", secret: Optional[str] = None):
        self.router = router
        self.secret = secret

    def _build_app(self):
        from aiohttp import web

        app = web.Application()
        app.router.add_post("/signal/error",     self._handle_error_signal)
        app.router.add_post("/signal/metric",    self._handle_metric_signal)
        app.router.add_post("/signal/manual",    self._handle_manual_signal)
        app.router.add_post("/webhook/sentry",   self._handle_sentry)
        app.router.add_post("/webhook/datadog",  self._handle_datadog)
        app.router.add_post("/webhook/github",   self._handle_github_actions)
        return app

    async def run(self, host: str = "0.0.0.0", port: int = 8765) -> None:
        from aiohttp import web

        app = self._build_app()
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()
        logger.info("Selfix webhook server listening on %s:%d", host, port)

    # ── Direct signal endpoints ───────────────────────────────────────────────

    async def _handle_error_signal(self, request):
        from aiohttp import web
        from selfix.signals.error import ErrorSignal

        data = await request.json()
        signal = ErrorSignal(
            description=data["description"],
            stack_trace=data.get("stack_trace"),
            file_hint=data.get("file_hint"),
            line_hint=data.get("line_hint"),
            error_type=data.get("error_type"),
            frequency=data.get("frequency"),
            environment=data.get("environment"),
            scope_hint=data.get("scope_hint"),
        )
        await self.router.dispatch(signal)
        return web.Response(text="accepted", status=202)

    async def _handle_metric_signal(self, request):
        from aiohttp import web
        from selfix.signals.metric import MetricSignal

        data = await request.json()
        signal = MetricSignal(
            description=data["description"],
            metric_name=data.get("metric_name", ""),
            metric_path=data.get("metric_path"),
            current_value=data.get("current_value", 0.0),
            baseline_value=data.get("baseline_value"),
            threshold=data.get("threshold"),
            unit=data.get("unit", ""),
            direction=data.get("direction", "lower_is_better"),
            scope_hint=data.get("scope_hint"),
        )
        await self.router.dispatch(signal)
        return web.Response(text="accepted", status=202)

    async def _handle_manual_signal(self, request):
        from aiohttp import web
        from selfix.signals.manual import ManualSignal

        data = await request.json()
        signal = ManualSignal(
            description=data["description"],
            scope_hint=data.get("scope_hint"),
        )
        await self.router.dispatch(signal)
        return web.Response(text="accepted", status=202)

    # ── Third-party webhook adapters ──────────────────────────────────────────

    async def _handle_sentry(self, request):
        from aiohttp import web
        from selfix.signals.error import ErrorSignal

        if not await self._verify_sentry_signature(request):
            return web.Response(text="forbidden", status=403)

        payload = await request.json()
        issue = payload.get("data", {}).get("issue", {})

        signal = ErrorSignal(
            description=issue.get("title", "Sentry error"),
            stack_trace=issue.get("culprit"),
            error_type=issue.get("type"),
            frequency=issue.get("times_seen"),
            environment=issue.get("environment"),
        )
        await self.router.dispatch(signal)
        return web.Response(text="accepted", status=202)

    async def _handle_datadog(self, request):
        from aiohttp import web
        from selfix.signals.metric import MetricSignal

        payload = await request.json()

        # Datadog monitor alert payload (simplified)
        metric_name = payload.get("metric", payload.get("name", "unknown"))
        description = payload.get("title", payload.get("body", f"Datadog alert: {metric_name}"))

        signal = MetricSignal(
            description=description,
            metric_name=metric_name,
            current_value=float(payload.get("current_value", 0)),
            threshold=float(payload.get("threshold", 0)) if payload.get("threshold") else None,
            unit=payload.get("unit", ""),
        )
        await self.router.dispatch(signal)
        return web.Response(text="accepted", status=202)

    async def _handle_github_actions(self, request):
        from aiohttp import web
        from selfix.signals.error import ErrorSignal

        if not await self._verify_github_signature(request):
            return web.Response(text="forbidden", status=403)

        payload = await request.json()

        # GitHub Actions workflow_run failure
        run = payload.get("workflow_run", {})
        repo = payload.get("repository", {}).get("full_name", "unknown/repo")
        workflow = run.get("name", "unknown workflow")
        conclusion = run.get("conclusion", "")

        if conclusion != "failure":
            return web.Response(text="ignored", status=200)

        signal = ErrorSignal(
            description=f"GitHub Actions workflow '{workflow}' failed in {repo}",
            error_type="CI_FAILURE",
            environment=run.get("head_branch", "unknown"),
        )
        await self.router.dispatch(signal)
        return web.Response(text="accepted", status=202)

    # ── Signature verification ────────────────────────────────────────────────

    async def _verify_sentry_signature(self, request) -> bool:
        if not self.secret:
            return True
        sig = request.headers.get("sentry-hook-signature", "")
        body = await request.read()
        expected = hmac.new(
            self.secret.encode(), body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(sig, expected)

    async def _verify_github_signature(self, request) -> bool:
        if not self.secret:
            return True
        sig_header = request.headers.get("X-Hub-Signature-256", "")
        body = await request.read()
        expected = "sha256=" + hmac.new(
            self.secret.encode(), body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(sig_header, expected)
