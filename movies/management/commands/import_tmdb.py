"""
Management command: bulk import movies from TMDB by ID.

Usage:
    # Import specific TMDB IDs
    python manage.py import_tmdb --ids 27205 550 157336 tt0110912

    # Import TMDB popular movies (first N pages)
    python manage.py import_tmdb --popular --pages 3

    # Import top-rated movies
    python manage.py import_tmdb --top-rated --pages 2

    # Import trending movies (this week)
    python manage.py import_tmdb --trending

    # Skip already-imported movies
    python manage.py import_tmdb --popular --skip-existing
"""

import requests
import logging
from django.core.management.base import BaseCommand
from django.conf import settings
from django.db.models.signals import post_save

logger = logging.getLogger(__name__)

TMDB_BASE = "https://api.themoviedb.org/3"
POSTER_BASE = "https://image.tmdb.org/t/p/w500"


def fetch_movie_data(tmdb_id, api_key):
    """Fetch full movie data from TMDB. Returns dict or None."""
    try:
        url = f"{TMDB_BASE}/movie/{tmdb_id}"
        params = {'api_key': api_key, 'language': 'en-US'}
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.HTTPError as e:
        return None
    except Exception as e:
        logger.warning("TMDB fetch error for id %s: %s", tmdb_id, e)
        return None


def parse_movie(data):
    """Parse TMDB API response into model-ready dict."""
    genres = ', '.join(g['name'] for g in data.get('genres', []))

    release_date = data.get('release_date', '')
    year = int(release_date[:4]) if release_date else None

    runtime_min = data.get('runtime') or 0
    if runtime_min:
        h, m = divmod(runtime_min, 60)
        duration_str = f"{h}h {m}m" if h else f"{m}m"
    else:
        duration_str = ''

    vote_avg = data.get('vote_average')
    rating_val = round(float(vote_avg), 1) if vote_avg else None

    poster_path = data.get('poster_path', '')
    poster_url = f"{POSTER_BASE}{poster_path}" if poster_path else ''

    return {
        'tmdb_id':          data['id'],
        'title':            data.get('title', '') or '',
        'description':      data.get('overview', '') or '',
        'genre':            genres,
        'year':             year,
        'duration':         duration_str,
        'rating':           rating_val,
        'poster_remote_url': poster_url,
    }


def import_single(tmdb_id, api_key, skip_existing=False, stdout=None):
    """Import a single movie by TMDB ID. Returns status string."""
    from movies.models import Movie
    from movies.models import fetch_tmdb_metadata

    # Check existing
    existing = Movie.objects.filter(tmdb_id=tmdb_id).first()
    if existing and skip_existing:
        return 'skip', f"[SKIP] tmdb_id={tmdb_id} already exists: '{existing.title}'"

    data = fetch_movie_data(tmdb_id, api_key)
    if not data:
        return 'fail', f"[FAIL] tmdb_id={tmdb_id} — could not fetch from TMDB (check the ID)"

    parsed = parse_movie(data)

    # Disconnect signal to avoid double-fetch
    post_save.disconnect(fetch_tmdb_metadata, sender=Movie)
    try:
        if existing:
            # Update all fields
            for field, value in parsed.items():
                setattr(existing, field, value)
            existing._tmdb_id_fetched = tmdb_id
            existing.save()
            msg = f"[UPDATE] '{parsed['title']}' (tmdb={tmdb_id}) — updated"
            return 'ok', msg
        else:
            # Create new — video field is blank (user adds video later via admin)
            movie = Movie(
                video='',  # blank — add video via admin later
                _tmdb_id_fetched=tmdb_id,
                **parsed
            )
            movie.save()
            msg = (
                f"[OK]  '{parsed['title']}' (tmdb={tmdb_id})\n"
                f"      Genre: {parsed['genre']} | Year: {parsed['year']} | "
                f"Rating: {parsed['rating']} | Duration: {parsed['duration']}"
            )
            return 'ok', msg
    finally:
        post_save.connect(fetch_tmdb_metadata, sender=Movie)


def fetch_list(endpoint, api_key, pages=1):
    """Fetch a list of TMDB IDs from a list endpoint (popular, top_rated, trending)."""
    ids = []
    for page in range(1, pages + 1):
        try:
            params = {'api_key': api_key, 'language': 'en-US', 'page': page}
            resp = requests.get(f"{TMDB_BASE}/{endpoint}", params=params, timeout=10)
            resp.raise_for_status()
            results = resp.json().get('results', [])
            ids.extend(r['id'] for r in results)
        except Exception as e:
            logger.warning("Error fetching list page %s: %s", page, e)
    return ids


class Command(BaseCommand):
    help = 'Import movies from TMDB into CineVault (metadata only — add video files via admin)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--ids', nargs='+',
            help='TMDB movie IDs to import (e.g. --ids 27205 550 157336)'
        )
        parser.add_argument(
            '--popular', action='store_true',
            help='Import currently popular movies from TMDB'
        )
        parser.add_argument(
            '--top-rated', action='store_true',
            help='Import top-rated movies from TMDB'
        )
        parser.add_argument(
            '--trending', action='store_true',
            help='Import trending movies this week from TMDB'
        )
        parser.add_argument(
            '--pages', type=int, default=1,
            help='Number of pages to fetch for --popular/--top-rated (20 movies per page, default: 1)'
        )
        parser.add_argument(
            '--skip-existing', action='store_true',
            help='Skip movies that are already in the database'
        )

    def handle(self, *args, **options):
        api_key = getattr(settings, 'TMDB_API_KEY', None)
        if not api_key:
            self.stderr.write(self.style.ERROR('TMDB_API_KEY not set in settings.py'))
            return

        tmdb_ids = []

        # ── Collect IDs from all sources ─────────────────────
        if options.get('ids'):
            tmdb_ids += [int(i) for i in options['ids']]

        if options.get('popular'):
            self.stdout.write(f"Fetching popular movies ({options['pages']} page(s))...")
            ids = fetch_list('movie/popular', api_key, options['pages'])
            self.stdout.write(f"  Found {len(ids)} popular movies")
            tmdb_ids += ids

        if options.get('top_rated'):
            self.stdout.write(f"Fetching top-rated movies ({options['pages']} page(s))...")
            ids = fetch_list('movie/top_rated', api_key, options['pages'])
            self.stdout.write(f"  Found {len(ids)} top-rated movies")
            tmdb_ids += ids

        if options.get('trending'):
            self.stdout.write("Fetching trending movies this week...")
            ids = fetch_list('trending/movie/week', api_key, options['pages'])
            self.stdout.write(f"  Found {len(ids)} trending movies")
            tmdb_ids += ids

        # Deduplicate
        tmdb_ids = list(dict.fromkeys(tmdb_ids))

        if not tmdb_ids:
            self.stdout.write(self.style.WARNING(
                'No IDs provided. Use --ids, --popular, --top-rated, or --trending.\n'
                'Example: python manage.py import_tmdb --popular --pages 2'
            ))
            return

        self.stdout.write(f'\nImporting {len(tmdb_ids)} movie(s) from TMDB...\n')

        ok, fail, skip, update = 0, 0, 0, 0

        for tmdb_id in tmdb_ids:
            status, msg = import_single(
                tmdb_id, api_key,
                skip_existing=options.get('skip_existing', False),
                stdout=self.stdout
            )
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
        parts = []
        if ok:     parts.append(self.style.SUCCESS(f'{ok} imported'))
        if skip:   parts.append(self.style.WARNING(f'{skip} skipped'))
        if fail:   parts.append(self.style.ERROR(f'{fail} failed'))
        self.stdout.write(' · '.join(parts) or 'Nothing imported.')
        self.stdout.write('')
        self.stdout.write('NOTE: Movies imported without video files.')
        self.stdout.write('      Go to Admin → Movies → select a movie → upload a video file.')