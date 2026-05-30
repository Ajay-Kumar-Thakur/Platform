from django.contrib import admin
from django.utils.html import format_html
from django.contrib import messages
import requests
from django.conf import settings

from .models import Movie, _pick_trailer


@admin.register(Movie)
class MovieAdmin(admin.ModelAdmin):
    list_display  = ('poster_preview', 'title', 'genre', 'year',
                     'duration', 'rating', 'tmdb_id', 'has_trailer', 'created_at')
    list_filter   = ('genre', 'year')
    search_fields = ('title', 'description')
    ordering      = ('-created_at',)
    actions       = ['refetch_tmdb_data']

    fieldsets = (
        ('🎬 Movie File', {
            'fields': ('video',),
        }),
        ('🔍 TMDB Auto-Fill', {
            'description': (
                'Enter a TMDB movie ID and save — title, description, genre, year, '
                'duration, rating, poster and trailer will be fetched automatically. '
                'Find IDs at <a href="https://www.themoviedb.org" target="_blank">themoviedb.org</a>.'
            ),
            'fields': ('tmdb_id',),
        }),
        ('📋 Metadata (auto-filled or manual)', {
            'fields': ('title', 'description', 'genre', 'year', 'duration', 'rating'),
        }),
        ('🖼️ Poster', {
            'fields': ('poster', 'poster_remote_url', 'poster_preview_large'),
            'description': 'Upload a local poster OR let TMDB fill the remote URL automatically.',
        }),
        ('🎥 Trailer', {
            'fields': ('trailer_url', 'trailer_preview'),
            'description': 'Auto-filled from TMDB. You can also paste a YouTube embed URL manually.',
        }),
    )

    readonly_fields = ('poster_preview_large', 'trailer_preview')

    # ── List: poster thumbnail ───────────────────────────────
    @admin.display(description='Poster')
    def poster_preview(self, obj):
        url = obj.poster.url if (obj.poster and obj.poster.name) else obj.poster_remote_url
        if url:
            return format_html(
                '<img src="{}" style="height:60px;border-radius:3px;'
                'box-shadow:0 2px 8px rgba(0,0,0,.4);">',
                url
            )
        return '—'

    # ── List: trailer indicator ──────────────────────────────
    @admin.display(description='Trailer', boolean=True)
    def has_trailer(self, obj):
        return bool(obj.trailer_url)

    # ── Detail: large poster preview ────────────────────────
    @admin.display(description='Current Poster Preview')
    def poster_preview_large(self, obj):
        url = obj.poster.url if (obj.poster and obj.poster.name) else obj.poster_remote_url
        if url:
            return format_html(
                '<img src="{}" style="max-height:240px;border-radius:4px;'
                'box-shadow:0 4px 16px rgba(0,0,0,.5);">',
                url
            )
        return 'No poster yet'

    # ── Detail: inline trailer preview ──────────────────────
    @admin.display(description='Trailer Preview')
    def trailer_preview(self, obj):
        if obj.trailer_url:
            return format_html(
                '<iframe src="{}" width="480" height="270" frameborder="0" '
                'allow="accelerometer; autoplay; clipboard-write; encrypted-media; '
                'gyroscope; picture-in-picture" allowfullscreen '
                'style="border-radius:4px;margin-top:8px;"></iframe>',
                obj.trailer_url
            )
        return 'No trailer yet'

    # ── Bulk action: re-fetch from TMDB ─────────────────────
    @admin.action(description='↻  Re-fetch metadata + trailer from TMDB')
    def refetch_tmdb_data(self, request, queryset):
        api_key = getattr(settings, 'TMDB_API_KEY', None)
        if not api_key:
            self.message_user(request,
                'TMDB_API_KEY is not set in settings.py.',
                level=messages.ERROR)
            return

        updated, skipped, failed = 0, 0, 0

        for movie in queryset:
            if not movie.tmdb_id:
                skipped += 1
                continue
            try:
                url    = f"https://api.themoviedb.org/3/movie/{movie.tmdb_id}"
                params = {
                    'api_key': api_key,
                    'language': 'en-US',
                    'append_to_response': 'videos',   # ✅ fetch trailers too
                }
                resp = requests.get(url, params=params, timeout=8)
                resp.raise_for_status()
                data = resp.json()

                genres       = ', '.join(g['name'] for g in data.get('genres', []))
                release_date = data.get('release_date', '')
                year         = int(release_date[:4]) if release_date else None
                runtime_min  = data.get('runtime') or 0
                if runtime_min:
                    h, m         = divmod(runtime_min, 60)
                    duration_str = f"{h}h {m}m" if h else f"{m}m"
                else:
                    duration_str = ''
                vote_avg    = data.get('vote_average')
                rating_val  = round(float(vote_avg), 1) if vote_avg else None
                poster_path = data.get('poster_path', '')
                poster_url  = f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else ''

                # ── Trailer ──────────────────────────────────
                videos      = data.get('videos', {}).get('results', [])
                trailer_url = _pick_trailer(videos)

                if data.get('title'):      movie.title             = data['title']
                if data.get('overview'):   movie.description       = data['overview']
                if genres:                 movie.genre             = genres
                if year:                   movie.year              = year
                if duration_str:           movie.duration          = duration_str
                if rating_val is not None: movie.rating            = rating_val
                if poster_url:             movie.poster_remote_url = poster_url
                movie.trailer_url       = trailer_url     # always overwrite
                movie._tmdb_id_fetched  = movie.tmdb_id
                movie.save()
                updated += 1

            except Exception as exc:
                failed += 1
                self.message_user(
                    request,
                    f'Failed "{movie.title}" (tmdb_id={movie.tmdb_id}): {exc}',
                    level=messages.WARNING,
                )

        parts = []
        if updated: parts.append(f'{updated} updated')
        if skipped: parts.append(f'{skipped} skipped (no TMDB ID)')
        if failed:  parts.append(f'{failed} failed')
        self.message_user(
            request,
            ' · '.join(parts) or 'Nothing to do.',
            level=messages.SUCCESS if not failed else messages.WARNING,
        )