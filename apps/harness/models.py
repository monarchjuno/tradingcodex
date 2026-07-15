from django.db import models


class BuildTurnGrant(models.Model):
    SCOPE_BUILD = "build"
    SCOPE_BRAIN = "brain"
    SCOPE_STRATEGY = "strategy"
    SCOPE_CHOICES = (
        (SCOPE_BUILD, "Build"),
        (SCOPE_BRAIN, "Investment Brain"),
        (SCOPE_STRATEGY, "Strategy"),
    )
    STATUS_ACTIVE = "active"
    STATUS_RESERVED = "reserved"
    STATUS_REVOKED = "revoked"
    STATUS_EXPIRED = "expired"
    STATUS_CHOICES = (
        (STATUS_ACTIVE, "Active"),
        (STATUS_RESERVED, "Reserved"),
        (STATUS_REVOKED, "Revoked"),
        (STATUS_EXPIRED, "Expired"),
    )

    grant_id = models.CharField(max_length=80, unique=True)
    authority_scope = models.CharField(max_length=32, choices=SCOPE_CHOICES, default=SCOPE_BUILD)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    workspace_id = models.CharField(max_length=180)
    workspace_path_hash = models.CharField(max_length=64)
    session_id_hash = models.CharField(max_length=64)
    turn_id_hash = models.CharField(max_length=64)
    prompt_sha256 = models.CharField(max_length=64)
    issued_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    reserved_at = models.DateTimeField(null=True, blank=True)
    service_started_at = models.DateTimeField(null=True, blank=True)
    reservation_tool_use_id_hash = models.CharField(max_length=64, blank=True)
    reservation_tool_name = models.CharField(max_length=160, blank=True)
    reservation_arguments_hash = models.CharField(max_length=64, blank=True)
    reservation_proof_hash = models.CharField(max_length=64, blank=True)
    use_count = models.PositiveIntegerField(default=0)
    last_used_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-issued_at", "-id"]
        indexes = [
            models.Index(
                fields=["workspace_id", "workspace_path_hash", "session_id_hash", "status"],
                name="harness_build_session_idx",
            ),
            models.Index(fields=["expires_at", "status"], name="harness_build_expiry_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace_id", "workspace_path_hash", "session_id_hash", "turn_id_hash"],
                name="unique_build_grant_per_turn",
            ),
            models.UniqueConstraint(
                fields=["workspace_id", "workspace_path_hash", "session_id_hash"],
                condition=models.Q(status__in=["active", "reserved"]),
                name="unique_active_build_grant_session",
            ),
        ]
        verbose_name = "Workspace turn grant"
        verbose_name_plural = "Workspace turn grants"

    def __str__(self) -> str:
        return f"{self.status}:{self.grant_id}"


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
