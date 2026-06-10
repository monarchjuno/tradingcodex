from django.db import models


class WorkspaceContext(models.Model):
    workspace_id = models.CharField(max_length=80, unique=True)
    path_hash = models.CharField(max_length=64, unique=True)
    project_name = models.CharField(max_length=180)
    path = models.CharField(max_length=1024)
    git_remote = models.CharField(max_length=512, blank=True)
    git_branch = models.CharField(max_length=180, blank=True)
    active_profile = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["project_name", "id"]
        verbose_name = "Workspace context"
        verbose_name_plural = "Workspace contexts"

    def __str__(self) -> str:
        return f"{self.project_name} {self.workspace_id}"


class RoleSkillAssignment(models.Model):
    role = models.CharField(max_length=128)
    skill = models.CharField(max_length=160)
    enabled = models.BooleanField(default=True)
    source = models.CharField(max_length=64, default="bootstrap")

    class Meta:
        unique_together = [("role", "skill")]
        verbose_name = "Role skill assignment"
        verbose_name_plural = "Role skill assignments"

    def __str__(self) -> str:
        return f"{self.role}: {self.skill}"


class SkillProposal(models.Model):
    proposal_id = models.CharField(max_length=220, unique=True)
    type = models.CharField(max_length=32)
    target = models.CharField(max_length=128)
    skill = models.CharField(max_length=160)
    status = models.CharField(max_length=32, default="proposed")
    approved_by = models.CharField(max_length=128, blank=True)
    execution_sensitive = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "Skill proposal"
        verbose_name_plural = "Skill proposals"

    def __str__(self) -> str:
        return self.proposal_id
