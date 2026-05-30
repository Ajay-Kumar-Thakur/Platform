from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
import requests
from django.conf import settings


class Movie(models.Model):
    title             = models.CharField(max_length=255, blank=True, default='')
    description       = models.TextField(blank=True, default='')
    poster            = models.ImageField(upload_to='posters/', blank=True, null=True)
    video             = models.FileField(upload_to='videos/', blank=True, null=True)
    tmdb_id           = models.PositiveIntegerField(null=True, blank=True, unique=True)
    genre             = models.CharField(max_length=200, blank=True, default='')
    year              = models.PositiveIntegerField(null=True, blank=True)
    duration          = models.CharField(max_length=20, blank=True, default='')
    rating            = models.DecimalField(max_digits=3, decimal_places=1, null=True, blank=True)
    poster_remote_url = models.URLField(max_length=500, blank=True, default='')
    trailer_url       = models.URLField(
                            max_length=500, blank=True, default='',
                            help_text="Auto-filled YouTube embed URL from TMDB."
                        )
    _tmdb_id_fetched  = models.PositiveIntegerField(null=True, blank=True)
    created_at        = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title or f"Movie #{self.pk}"


def _is_embeddable(key):
    """
    Check if a YouTube video allows embedding via the oEmbed API.
    Returns True if embeddable, False if blocked (Error 153).
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

    Uses youtube-nocookie.com:
    - Bypasses many embed restrictions
    - No user tracking cookies
    - Works on localhost without an 'origin' param
    """
    for check in [
        lambda v: v['site'] == 'YouTube' and v['type'] == 'Trailer' and v.get('official'),
        lambda v: v['site'] == 'YouTube' and v['type'] == 'Trailer',
        lambda v: v['site'] == 'YouTube' and v['type'] == 'Teaser',
        lambda v: v['site'] == 'YouTube',
    ]:
        hits = [v for v in videos if check(v)]
        if hits:
            # ✅ Try each key in this priority group — skip blocked ones
            for v in hits:
                key = v['key']
                if _is_embeddable(key):
                    return f"https://www.youtube-nocookie.com/embed/{key}?rel=0&modestbranding=1"
            # All keys in this group are blocked — try next priority group
            continue

    # Last resort: return first available key unverified
    for check in [
        lambda v: v['site'] == 'YouTube' and v['type'] == 'Trailer',
        lambda v: v['site'] == 'YouTube',
    ]:
        hits = [v for v in videos if check(v)]
        if hits:
            key = hits[0]['key']
            return f"https://www.youtube-nocookie.com/embed/{key}?rel=0&modestbranding=1"

    return ''


@receiver(post_save, sender=Movie)
def fetch_tmdb_metadata(sender, instance, created, **kwargs):
    api_key = getattr(settings, 'TMDB_API_KEY', None)
    if not api_key or not instance.tmdb_id:
        return
    # Skip if we already fetched for this tmdb_id
    if instance._tmdb_id_fetched == instance.tmdb_id:
        return

    try:
        url    = f"https://api.themoviedb.org/3/movie/{instance.tmdb_id}"
        params = {'api_key': api_key, 'language': 'en-US', 'append_to_response': 'videos'}
        resp   = requests.get(url, params=params, timeout=8)
        resp.raise_for_status()
        data   = resp.json()

        # ── Parse fields ──────────────────────────────────────
        genres = ', '.join(g['name'] for g in data.get('genres', []))

        rd   = data.get('release_date', '')
        year = int(rd[:4]) if rd else None

        rm = data.get('runtime') or 0
        if rm:
            h, m = divmod(rm, 60)
            dur = f"{h}h {m}m" if h else f"{m}m"
        else:
            dur = ''

        va     = data.get('vote_average')
        rating = round(float(va), 1) if va else None

        pp         = data.get('poster_path', '')
        poster_url = f"https://image.tmdb.org/t/p/w500{pp}" if pp else ''

        # ── Pick trailer ──────────────────────────────────────
        videos      = data.get('videos', {}).get('results', [])
        trailer_url = _pick_trailer(videos)

        # ── Build update list ─────────────────────────────────
        instance._tmdb_id_fetched = instance.tmdb_id
        upd = ['_tmdb_id_fetched']

        def _set(field, val):
            if val:
                setattr(instance, field, val)
                upd.append(field)

        if not instance.title:
            _set('title', data.get('title', ''))
        _set('description', data.get('overview', ''))
        _set('genre', genres)
        if year:
            instance.year = year
            upd.append('year')
        _set('duration', dur)
        if rating is not None:
            instance.rating = rating
            upd.append('rating')
        _set('poster_remote_url', poster_url)

        # Always write trailer_url (even empty string clears stale value)
        instance.trailer_url = trailer_url
        upd.append('trailer_url')

        # ── Save without re-triggering signal ─────────────────
        post_save.disconnect(fetch_tmdb_metadata, sender=Movie)
        try:
            instance.save(update_fields=upd)
        finally:
            post_save.connect(fetch_tmdb_metadata, sender=Movie)

    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning(
            "TMDB fetch failed pk=%s tmdb_id=%s: %s", instance.pk, instance.tmdb_id, exc
        )