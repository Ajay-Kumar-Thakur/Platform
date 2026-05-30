"""
Management command: force re-fetch TMDB metadata (including trailer) for all movies.

Usage:
    python manage.py refetch_tmdb              # refetch all movies
    python manage.py refetch_tmdb --id 1 2 3   # refetch specific PKs
    python manage.py refetch_tmdb --clear      # wipe fields before fetching
"""

import requests
from django.core.management.base import BaseCommand
from django.conf import settings
from django.db.models.signals import post_save

TMDB_BASE   = "https://api.themoviedb.org/3"
POSTER_BASE = "https://image.tmdb.org/t/p/w500"


def _is_embeddable(key):
    """
    Check if a YouTube video allows embedding via the oEmbed API.
    Returns True if embeddable, False if blocked (would show Error 153).
    """
    try:
        resp = requests.get(
            "https://www.youtube.com/oembed",
            params={
                "url": f"https://www.youtube.com/watch?v={key}",
                "format": "json"
            },
            timeout=5
        )
        return resp.status_code == 200
    except Exception:
        return False


def _pick_trailer(videos):
    """
    Return a working YouTube embed URL from a TMDB videos list.

    Priority: official trailer → any trailer → teaser → any YouTube video.
    Within each priority group, tries ALL candidates and skips any that
    have embedding disabled (would cause Error 153).

    Uses youtube-nocookie.com — bypasses embed restrictions, no tracking,
    works on localhost without needing an 'origin' param.
    """
    for check in [
        lambda v: v['site'] == 'YouTube' and v['type'] == 'Trailer' and v.get('official'),
        lambda v: v['site'] == 'YouTube' and v['type'] == 'Trailer',
        lambda v: v['site'] == 'YouTube' and v['type'] == 'Teaser',
        lambda v: v['site'] == 'YouTube',
    ]:
        hits = [v for v in videos if check(v)]
        if hits:
            # ✅ Try each key — skip ones with embedding disabled
            for v in hits:
                key = v['key']
                if _is_embeddable(key):
                    return f"https://www.youtube-nocookie.com/embed/{key}?rel=0&modestbranding=1"
            continue  # all keys in this group blocked, try next priority

    # Last resort: return first available unverified
    for check in [
        lambda v: v['site'] == 'YouTube' and v['type'] == 'Trailer',
        lambda v: v['site'] == 'YouTube',
    ]:
        hits = [v for v in videos if check(v)]
        if hits:
            key = hits[0]['key']
            return f"https://www.youtube-nocookie.com/embed/{key}?rel=0&modestbranding=1"

    return ''


def fetch_and_apply(movie, api_key, clear_first=False):
    if not movie.tmdb_id:
        return 'skip', f"[SKIP] '{movie.title or movie.pk}' — no tmdb_id"

    if clear_first:
        from movies.models import Movie
        Movie.objects.filter(pk=movie.pk).update(
            description='', genre='', year=None, duration='',
            rating=None, poster_remote_url='', trailer_url='',
            _tmdb_id_fetched=None
        )
        movie.refresh_from_db()

    try:
        url    = f"{TMDB_BASE}/movie/{movie.tmdb_id}"
        params = {
            'api_key': api_key,
            'language': 'en-US',
            'append_to_response': 'videos',
        }
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        genres = ', '.join(g['name'] for g in data.get('genres', []))
        rd     = data.get('release_date', '')
        year   = int(rd[:4]) if rd else None
        rm     = data.get('runtime') or 0
        dur    = (f"{rm//60}h {rm%60}m" if rm // 60 else f"{rm%60}m") if rm else ''
        va     = data.get('vote_average')
        rating = round(float(va), 1) if va else None
        pp     = data.get('poster_path', '')
        poster = f"{POSTER_BASE}{pp}" if pp else ''

        videos  = data.get('videos', {}).get('results', [])
        trailer = _pick_trailer(videos)

        movie.title             = data.get('title') or movie.title
        movie.description       = data.get('overview', '') or movie.description
        movie.genre             = genres or movie.genre
        movie.year              = year   or movie.year
        movie.duration          = dur    or movie.duration
        movie.rating            = rating or movie.rating
        movie.poster_remote_url = poster or movie.poster_remote_url
        movie.trailer_url       = trailer
        movie._tmdb_id_fetched  = movie.tmdb_id

        from movies.models import Movie, fetch_tmdb_metadata
        post_save.disconnect(fetch_tmdb_metadata, sender=Movie)
        try:
            movie.save()
        finally:
            post_save.connect(fetch_tmdb_metadata, sender=Movie)

        trailer_status = "✓ trailer" if trailer else "✗ no trailer"
        return 'ok', (
            f"[OK]  '{movie.title}' (tmdb={movie.tmdb_id}) — "
            f"{genres} | {year} | ★{rating} | {trailer_status}"
        )

    except requests.HTTPError as e:
        return 'fail', f"[FAIL] pk={movie.pk} tmdb_id={movie.tmdb_id} — HTTP {e.response.status_code}"
    except Exception as e:
        return 'fail', f"[FAIL] pk={movie.pk} — {e}"


class Command(BaseCommand):
    help = 'Force re-fetch TMDB metadata + trailers for movies'

    def add_arguments(self, parser):
        parser.add_argument('--id',    nargs='+', type=int, help='Specific movie PKs')
        parser.add_argument('--clear', action='store_true', help='Wipe fields before fetching')

    def handle(self, *args, **options):
        from movies.models import Movie

        api_key = getattr(settings, 'TMDB_API_KEY', None)
        if not api_key:
            self.stderr.write(self.style.ERROR('TMDB_API_KEY not set in settings.py'))
            return

        pks    = options.get('id')
        movies = Movie.objects.filter(pk__in=pks) if pks else Movie.objects.filter(tmdb_id__isnull=False)
        total  = movies.count()

        if not total:
            self.stdout.write(self.style.WARNING('No movies with a tmdb_id found.'))
            return

        self.stdout.write(f'\nRefetching {total} movie(s) from TMDB (with trailers)...')
        if options.get('clear'):
            self.stdout.write(self.style.WARNING('  --clear: wiping existing fields first\n'))

        ok = fail = skip = 0
        for movie in movies:
            status, msg = fetch_and_apply(movie, api_key, options.get('clear', False))
            if status == 'ok':
                ok += 1
                self.stdout.write(self.style.SUCCESS(msg))
            elif status == 'skip':
                skip += 1
                self.stdout.write(self.style.WARNING(msg))
            else:
                fail += 1
                self.stdout.write(self.style.ERROR(msg))

        self.stdout.write('\n' + '─' * 55)
        self.stdout.write(
            self.style.SUCCESS(f'Done: {ok} updated') + ' · ' +
            (self.style.WARNING(f'{skip} skipped') if skip else '0 skipped') + ' · ' +
            (self.style.ERROR(f'{fail} failed') if fail else '0 failed')
        )