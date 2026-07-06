from django.contrib import admin
from django.templatetags.static import static
from django.urls import path
from django.views.generic import RedirectView

import tradingcodex_service.admin
from tradingcodex_service.api import api
from tradingcodex_service import web

urlpatterns = [
    path("", web.dashboard, name="web-dashboard"),
    path("harness/", web.harness, name="web-harness"),
    path("harness/agents/", web.agents_index, name="web-agents"),
    path("harness/agents/<str:role>/instructions/update/", web.agent_instruction_update, name="web-agent-instruction-update"),
    path("harness/agents/<str:role>/optional-skills/create/", web.optional_skill_create, name="web-optional-skill-create"),
    path("harness/agents/<str:role>/optional-skills/<str:name>/update/", web.optional_skill_update, name="web-optional-skill-update"),
    path("harness/agents/<str:role>/optional-skills/<str:name>/activate/", web.optional_skill_activate, name="web-optional-skill-activate"),
    path("harness/agents/<str:role>/optional-skills/<str:name>/archive/", web.optional_skill_archive, name="web-optional-skill-archive"),
    path("harness/agents/<str:role>/optional-skills/<str:name>/delete/", web.optional_skill_delete, name="web-optional-skill-delete"),
    path("harness/strategies/", web.strategies_index, name="web-strategies"),
    path("harness/roles/<str:role>/", web.role_inspector, name="web-role-inspector"),
    path("workspaces/open/", web.workspace_open, name="web-workspace-open"),
    path("workspaces/create/", web.workspace_create, name="web-workspace-create"),
    path("workspaces/browse/", web.workspace_browse, name="web-workspace-browse"),
    path("workspaces/<str:workspace_id>/remove/", web.workspace_remove, name="web-workspace-remove"),
    path("decisions/", web.decisions, name="web-decisions"),
    path("research/", web.research, name="web-research"),
    path("brokers/", web.broker_center, name="web-brokers"),
    path("brokers/connect/", web.broker_connect, name="web-broker-connect"),
    path("brokers/add-paper/", web.broker_add_paper, name="web-broker-add-paper"),
    path("brokers/add-mcp/", web.broker_add_mcp, name="web-broker-add-mcp"),
    path("brokers/<str:broker_id>/sync/", web.broker_sync, name="web-broker-sync"),
    path("portfolio/", web.portfolio, name="web-portfolio"),
    path("portfolio/sync/", web.portfolio_sync, name="web-portfolio-sync"),
    path("orders/", web.orders, name="web-orders"),
    path("orders/tickets/create/", web.order_ticket_create, name="web-order-ticket-create"),
    path("orders/tickets/<str:ticket_id>/checks/", web.order_ticket_checks, name="web-order-ticket-checks"),
    path("policy/", web.policy, name="web-policy"),
    path("activity/", web.activity, name="web-activity"),
    path("integrations/mcp/", web.mcp_router, name="web-mcp-router"),
    path("integrations/mcp/routers/create/", web.mcp_router_create, name="web-mcp-router-create"),
    path("integrations/mcp/routers/<int:router_id>/check/", web.mcp_router_check, name="web-mcp-router-check"),
    path("integrations/mcp/routers/<int:router_id>/discover/", web.mcp_router_discover, name="web-mcp-router-discover"),
    path("integrations/mcp/routers/<int:router_id>/import/", web.mcp_router_import, name="web-mcp-router-import"),
    path("integrations/mcp/tools/<int:tool_id>/update/", web.mcp_external_tool_update, name="web-mcp-external-tool-update"),
    path("integrations/mcp/tools/<int:tool_id>/check/", web.mcp_external_tool_check, name="web-mcp-external-tool-check"),
    path("build/", web.build_center, name="web-build-center"),
    path("build/codex-mcp/import/", web.build_codex_mcp_import, name="web-build-codex-mcp-import"),
    path("build/codex-mcp/add/", web.build_codex_mcp_add, name="web-build-codex-mcp-add"),
    path("build/permissions/<int:request_id>/<str:decision>/", web.build_permission_decide, name="web-build-permission-decide"),
    path("workflow/starter-prompt/", web.starter_prompt, name="web-starter-prompt"),
    path("workflow/starter-prompt/preview/", web.starter_prompt_fragment, name="web-starter-prompt-preview"),
    path("workflow/starter-prompt/profile/", web.starter_profile_update, name="web-starter-profile-update"),
    path("favicon.ico", RedirectView.as_view(url=static("tradingcodex_admin/favicon.svg"), permanent=False)),
    path("admin/", admin.site.urls),
    path("api/", api.urls),
]
