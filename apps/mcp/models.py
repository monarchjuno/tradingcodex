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
