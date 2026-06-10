from django.contrib import admin

from apps.harness.models import RoleSkillAssignment, SkillProposal, WorkspaceContext
from apps.harness.services import (
    apply_skill_proposals,
    approve_skill_proposals,
    reject_skill_proposals,
    set_role_skill_assignments_enabled,
)


@admin.register(WorkspaceContext)
class WorkspaceContextAdmin(admin.ModelAdmin):
    list_display = ("project_name", "workspace_id", "path_hash", "git_branch", "last_seen_at")
    search_fields = ("project_name", "workspace_id", "path", "path_hash", "git_remote", "git_branch")
    readonly_fields = ("workspace_id", "path_hash", "active_profile", "created_at", "last_seen_at")


@admin.register(RoleSkillAssignment)
class RoleSkillAssignmentAdmin(admin.ModelAdmin):
    list_display = ("role", "skill", "enabled", "source")
    list_filter = ("role", "enabled", "source")
    search_fields = ("role", "skill")
    actions = ["enable_assignments", "disable_assignments"]

    @admin.action(description="Enable selected role skill assignments")
    def enable_assignments(self, request, queryset):
        set_role_skill_assignments_enabled(queryset, True, str(request.user or "admin"))

    @admin.action(description="Disable selected role skill assignments")
    def disable_assignments(self, request, queryset):
        set_role_skill_assignments_enabled(queryset, False, str(request.user or "admin"))


@admin.register(SkillProposal)
class SkillProposalAdmin(admin.ModelAdmin):
    list_display = ("proposal_id", "type", "target", "skill", "status", "execution_sensitive", "created_at")
    list_filter = ("type", "status", "execution_sensitive", "target")
    search_fields = ("proposal_id", "target", "skill", "approved_by")
    actions = ["approve_proposals", "apply_proposals", "reject_proposals"]

    @admin.action(description="Approve selected skill proposals")
    def approve_proposals(self, request, queryset):
        approve_skill_proposals(queryset, str(request.user or "admin"))

    @admin.action(description="Apply selected approved skill proposals")
    def apply_proposals(self, request, queryset):
        apply_skill_proposals(queryset, str(request.user or "admin"))

    @admin.action(description="Reject selected skill proposals")
    def reject_proposals(self, request, queryset):
        reject_skill_proposals(queryset, str(request.user or "admin"))
