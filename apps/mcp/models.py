from django.db import models


class McpToolDefinition(models.Model):
    name = models.CharField(max_length=160, unique=True)
    description = models.TextField(blank=True)
    category = models.CharField(max_length=64, default="general")
    capability_required = models.CharField(max_length=160, blank=True)
    input_schema = models.JSONField(default=dict, blank=True)
    risk_level = models.CharField(max_length=32, default="read")
    allowed_roles = models.JSONField(default=list, blank=True)
    requires_approval = models.BooleanField(default=False)
    audit_required = models.BooleanField(default=True)
    experimental = models.BooleanField(default=False)
    enabled = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "MCP tool definition"
        verbose_name_plural = "MCP tool definitions"

    def __str__(self) -> str:
        return self.name


class McpToolCall(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    tool_name = models.CharField(max_length=160)
    principal_id = models.CharField(max_length=128, default="unknown")
    status = models.CharField(max_length=32, default="recorded")
    request = models.JSONField(default=dict, blank=True)
    response = models.JSONField(default=dict, blank=True)
    workspace_context = models.JSONField(default=dict, blank=True)
    request_hash = models.CharField(max_length=64, blank=True)
    result_hash = models.CharField(max_length=64, blank=True)
    error = models.TextField(blank=True)
    duration_ms = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "MCP tool call"
        verbose_name_plural = "MCP tool calls"

    def __str__(self) -> str:
        return f"{self.tool_name} {self.status}"


class McpRouter(models.Model):
    name = models.CharField(max_length=160, unique=True)
    label = models.CharField(max_length=160, blank=True)
    transport = models.CharField(max_length=32, default="stdio")
    command = models.TextField(blank=True)
    url = models.URLField(blank=True)
    args = models.JSONField(default=list, blank=True)
    env = models.JSONField(default=dict, blank=True)
    credential_ref = models.CharField(max_length=255, blank=True)
    trust_level = models.CharField(max_length=32, default="unreviewed")
    enabled = models.BooleanField(default=False)
    last_status = models.CharField(max_length=32, default="not_checked")
    last_error = models.TextField(blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "MCP router"
        verbose_name_plural = "MCP routers"

    def __str__(self) -> str:
        return self.label or self.name


class McpExternalTool(models.Model):
    router = models.ForeignKey(McpRouter, on_delete=models.CASCADE, related_name="external_tools")
    primitive = models.CharField(max_length=32, default="tool")
    external_name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    input_schema = models.JSONField(default=dict, blank=True)
    output_schema = models.JSONField(default=dict, blank=True)
    schema_hash = models.CharField(max_length=64, blank=True)
    category = models.CharField(max_length=64, default="unknown")
    risk_level = models.CharField(max_length=32, default="unknown")
    sensitivity = models.CharField(max_length=32, default="unknown")
    canonical_capability = models.CharField(max_length=160, blank=True)
    proxy_mode = models.CharField(max_length=32, default="blocked")
    allowed_roles = models.JSONField(default=list, blank=True)
    conditions = models.JSONField(default=dict, blank=True)
    enabled = models.BooleanField(default=False)
    review_status = models.CharField(max_length=32, default="review_required")
    drift_detected = models.BooleanField(default=False)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["router__name", "primitive", "external_name"]
        constraints = [
            models.UniqueConstraint(fields=["router", "primitive", "external_name"], name="unique_external_mcp_primitive")
        ]
        verbose_name = "external MCP tool"
        verbose_name_plural = "external MCP tools"

    def __str__(self) -> str:
        return f"{self.router.name}:{self.external_name}"


class McpExternalToolPermission(models.Model):
    external_tool = models.ForeignKey(McpExternalTool, on_delete=models.CASCADE, related_name="permissions")
    principal_or_role = models.CharField(max_length=128)
    capability = models.CharField(max_length=160, blank=True)
    decision = models.CharField(max_length=16, default="allow")
    conditions = models.JSONField(default=dict, blank=True)
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["external_tool__external_name", "principal_or_role"]
        constraints = [
            models.UniqueConstraint(
                fields=["external_tool", "principal_or_role", "capability"],
                name="unique_external_mcp_permission",
            )
        ]
        verbose_name = "external MCP permission"
        verbose_name_plural = "external MCP permissions"

    def __str__(self) -> str:
        return f"{self.external_tool.external_name} {self.principal_or_role} {self.decision}"


class McpExternalPermissionRequest(models.Model):
    external_tool = models.ForeignKey(McpExternalTool, on_delete=models.SET_NULL, null=True, blank=True, related_name="permission_requests")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    router_name = models.CharField(max_length=160)
    external_name = models.CharField(max_length=200)
    principal_id = models.CharField(max_length=128, default="unknown")
    role = models.CharField(max_length=128, blank=True)
    workflow_run_id = models.CharField(max_length=160, blank=True)
    request_hash = models.CharField(max_length=64)
    arguments_summary = models.JSONField(default=dict, blank=True)
    approval_scope = models.CharField(max_length=32, default="single_call")
    status = models.CharField(max_length=32, default="pending")
    reasons = models.JSONField(default=list, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.CharField(max_length=128, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "external MCP permission request"
        verbose_name_plural = "external MCP permission requests"

    def __str__(self) -> str:
        return f"{self.router_name}:{self.external_name} {self.status}"


class McpExternalToolCall(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    external_tool = models.ForeignKey(McpExternalTool, on_delete=models.SET_NULL, null=True, blank=True, related_name="calls")
    router_name = models.CharField(max_length=160)
    external_name = models.CharField(max_length=200)
    principal_id = models.CharField(max_length=128, default="unknown")
    proxy_mode = models.CharField(max_length=32, default="blocked")
    decision = models.CharField(max_length=32, default="denied")
    reasons = models.JSONField(default=list, blank=True)
    request = models.JSONField(default=dict, blank=True)
    response = models.JSONField(default=dict, blank=True)
    request_hash = models.CharField(max_length=64, blank=True)
    result_hash = models.CharField(max_length=64, blank=True)
    workspace_context = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "external MCP tool call"
        verbose_name_plural = "external MCP tool calls"

    def __str__(self) -> str:
        return f"{self.router_name}:{self.external_name} {self.decision}"
