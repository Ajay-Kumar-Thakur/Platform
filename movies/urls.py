from django.urls import path
from . import views

urlpatterns = [

    # ── Template pages ──────────────────────────────────────
    path('',                views.home,          name='home'),
    path('movie/<int:pk>/', views.movie_detail,  name='movie_detail'),

    # ── REST API endpoints ──────────────────────────────────
    path('api/movies/',          views.api_movie_list,   name='api_movie_list'),
    path('api/movies/<int:pk>/', views.api_movie_detail, name='api_movie_detail'),
]