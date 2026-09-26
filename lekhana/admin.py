from django.contrib import admin

from .models import (Act, Anchor, Ask, Candidate, Conversation, Idea, Membership, MethodRun,
                     MethodStep, Project, ReviewPoint, Turn)


class MembershipInline(admin.TabularInline):
    model = Membership
    fk_name = "project"
    extra = 0
    fields = ("user", "role", "granted_by", "created_at")
    readonly_fields = ("granted_by", "created_at")


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    """Grant access from the portal's ledger page so the grant is recorded in git.
    Changes made here bypass the record."""
    list_display = ("title", "slug", "created_by", "created_at")
    readonly_fields = ("slug", "created_by", "created_at")
    inlines = [MembershipInline]


class CandidateInline(admin.TabularInline):
    model = Candidate
    extra = 0
    can_delete = False
    readonly_fields = [f.name for f in Candidate._meta.fields]


@admin.register(Ask)
class AskAdmin(admin.ModelAdmin):
    list_display = ("project", "purpose", "unit_id", "device_code", "requested_by", "model", "status", "created_at")
    list_filter = ("purpose", "status", "device_code")
    inlines = [CandidateInline]
    readonly_fields = [f.name for f in Ask._meta.fields]


@admin.register(ReviewPoint)
class ReviewPointAdmin(admin.ModelAdmin):
    list_display = ("project", "unit_id", "kind", "author", "responded_by", "created_at")
    list_filter = ("kind",)
    readonly_fields = [f.name for f in ReviewPoint._meta.fields]


@admin.register(Act)
class ActAdmin(admin.ModelAdmin):
    """The record lives in git; this index is read-only here."""
    list_display = ("project", "kind", "agent", "role", "yukti", "unit_id", "commit_sha", "created_at")
    list_filter = ("kind", "role", "project")
    readonly_fields = [f.name for f in Act._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class TurnInline(admin.TabularInline):
    model = Turn
    extra = 0
    can_delete = False
    readonly_fields = [f.name for f in Turn._meta.fields]


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("project", "title", "stance", "created_by", "updated_at")
    list_filter = ("stance",)
    inlines = [TurnInline]
    readonly_fields = ("project", "created_by", "created_at", "updated_at")


@admin.register(Idea)
class IdeaAdmin(admin.ModelAdmin):
    """Attribution is confirmed in the portal, where the confirmation is an act."""
    list_display = ("project", "text", "kind", "origin", "confirmed_by", "corrected", "status")
    list_filter = ("kind", "status", "corrected")
    readonly_fields = [f.name for f in Idea._meta.fields]


class MethodStepInline(admin.TabularInline):
    model = MethodStep
    extra = 0
    readonly_fields = ("index", "label", "unit_id", "done_at")


@admin.register(MethodRun)
class MethodRunAdmin(admin.ModelAdmin):
    list_display = ("project", "code", "created_by", "created_at")
    inlines = [MethodStepInline]
    readonly_fields = ("project", "code", "created_by", "created_at")


@admin.register(Anchor)
class AnchorAdmin(admin.ModelAdmin):
    list_display = ("root_hash", "log", "created_at")
    readonly_fields = ("root_hash", "heads", "log", "proof", "created_at")
