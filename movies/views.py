from django.shortcuts import render, get_object_or_404

from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import Movie
from .serializers import MovieListSerializer, MovieDetailSerializer


# ─────────────────────────────────────────────
#  REST API ENDPOINTS
# ─────────────────────────────────────────────

@api_view(['GET'])
def api_movie_list(request):
    """
    GET /api/movies/
    Returns all movies (lightweight — no video URLs).
    """
    movies     = Movie.objects.all()
    serializer = MovieListSerializer(movies, many=True, context={'request': request})
    return Response(serializer.data)


@api_view(['GET'])
def api_movie_detail(request, pk):
    """
    GET /api/movies/<pk>/
    Returns a single movie with full detail including video & trailer URLs.
    """
    movie      = get_object_or_404(Movie, pk=pk)
    serializer = MovieDetailSerializer(movie, context={'request': request})
    return Response(serializer.data)


# ─────────────────────────────────────────────
#  TEMPLATE VIEWS  (HTML shells)
# ─────────────────────────────────────────────

def home(request):
    """Renders the home shell — JS fetches /api/movies/ on load."""
    return render(request, 'home.html')


def movie_detail(request, pk):
    """
    Renders the detail shell.
    Passes movie_id into the template so JS doesn't need to parse the URL.
    """
    return render(request, 'detail.html', {'movie_id': pk})