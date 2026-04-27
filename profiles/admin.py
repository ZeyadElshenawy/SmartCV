from django.contrib import admin

from .models import (
    DiscoveredTarget,
    JobProfileSnapshot,
    OutreachAction,
    OutreachActionEvent,
    OutreachCampaign,
    UserProfile,
)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'full_name', 'input_method', 'updated_at')
    search_fields = ('user__email', 'full_name')
    readonly_fields = ('id', 'created_at', 'updated_at')


@admin.register(OutreachCampaign)
class OutreachCampaignAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'job', 'status', 'daily_invite_cap', 'created_at')
    list_filter = ('status',)
    search_fields = ('user__email', 'job__title', 'job__company')
    readonly_fields = ('id', 'created_at', 'updated_at')


@admin.register(OutreachAction)
class OutreachActionAdmin(admin.ModelAdmin):
    list_display = ('target_name', 'target_handle', 'campaign', 'kind', 'status', 'attempts', 'queued_at')
    list_filter = ('status', 'kind')
    search_fields = ('target_handle', 'target_name', 'campaign__user__email')
    readonly_fields = ('id', 'queued_at', 'completed_at')


@admin.register(OutreachActionEvent)
class OutreachActionEventAdmin(admin.ModelAdmin):
    """Append-only audit trail. Read-only in the admin so nobody clobbers
    history while debugging — events are written from dispatcher hooks."""
    list_display = ('created_at', 'action', 'from_status', 'to_status', 'actor', 'reason')
    list_filter = ('actor', 'to_status', 'reason')
    search_fields = ('action__target_handle', 'action__target_name', 'detail')
    readonly_fields = (
        'id', 'action', 'from_status', 'to_status', 'actor',
        'reason', 'detail', 'attempts_after', 'created_at',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(DiscoveredTarget)
class DiscoveredTargetAdmin(admin.ModelAdmin):
    list_display = ('handle', 'name', 'role', 'source', 'job', 'discovered_at')
    list_filter = ('source',)
    search_fields = ('handle', 'name', 'job__company')
    readonly_fields = ('id', 'discovered_at')


@admin.register(JobProfileSnapshot)
class JobProfileSnapshotAdmin(admin.ModelAdmin):
    list_display = ('id', 'profile', 'job', 'created_at')
    readonly_fields = ('id', 'created_at')
