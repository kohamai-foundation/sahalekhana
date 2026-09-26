from django.urls import path

from . import views

app_name = "lekhana"

urlpatterns = [
    path("", views.index, name="index"),
    path("<slug:slug>/", views.project, name="project"),
    path("<slug:slug>/save/", views.save, name="save"),
    path("<slug:slug>/ask/", views.ask_view, name="ask"),
    path("<slug:slug>/candidates/<int:pk>/", views.candidate, name="candidate"),
    # the discussion, the ideas it turns up, and the writer's chosen method
    path("<slug:slug>/discuss/", views.discuss, name="discuss"),
    path("<slug:slug>/discuss/new/", views.discuss_new, name="discuss_new"),
    path("<slug:slug>/discuss/<int:pk>/send/", views.discuss_send, name="discuss_send"),
    path("<slug:slug>/discuss/<int:pk>/role/", views.discuss_stance, name="discuss_stance"),
    path("<slug:slug>/ideas/<int:pk>/attribute/", views.idea_attribute, name="idea_attribute"),
    path("<slug:slug>/methods/start/", views.method_start, name="method_start"),
    path("<slug:slug>/methods/steps/<int:pk>/", views.method_step, name="method_step"),
    # how the text arrived (typed, pasted, and from where)
    path("<slug:slug>/compose/", views.compose, name="compose"),
    path("<slug:slug>/tracking/", views.tracking, name="tracking"),
    # out of the portal
    path("<slug:slug>/pdf/", views.pdf, name="pdf"),
    path("<slug:slug>/export/", views.export, name="export"),
    # reviewing
    path("<slug:slug>/review/", views.review, name="review"),
    path("<slug:slug>/review/ask/", views.review_ask_view, name="review_ask"),
    path("<slug:slug>/review/points/", views.point_view, name="point"),
    path("<slug:slug>/review/points/<int:pk>/respond/", views.respond_view, name="respond"),
    path("<slug:slug>/ledger/", views.ledger_view, name="ledger"),
    path("<slug:slug>/ledger/grant/", views.grant_view, name="grant"),
]
