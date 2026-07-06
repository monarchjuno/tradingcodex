from django.contrib import admin

from apps.mcp.models import (
    McpExternalPermissionRequest,
    McpExternalTool,
    McpExternalToolCall,
    McpExternalToolPermission,
    McpRouter,
    McpToolCall,
    McpToolDefinition,
)


admin.site.register([
    McpToolDefinition,
    McpToolCall,
    McpRouter,
    McpExternalTool,
    McpExternalToolPermission,
    McpExternalPermissionRequest,
    McpExternalToolCall,
])
