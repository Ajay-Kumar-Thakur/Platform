from rest_framework import serializers
from .models import Movie


class MovieListSerializer(serializers.ModelSerializer):
    poster_url = serializers.SerializerMethodField()

    class Meta:
        model  = Movie
        fields = ['id', 'title', 'description', 'poster_url',
                  'genre', 'year', 'duration', 'rating']

    def get_poster_url(self, obj):
        request = self.context.get('request')
        if obj.poster and obj.poster.name:
            return request.build_absolute_uri(obj.poster.url) if request else obj.poster.url
        return obj.poster_remote_url or None


class MovieDetailSerializer(serializers.ModelSerializer):
    poster_url  = serializers.SerializerMethodField()
    video_url   = serializers.SerializerMethodField()
    trailer_url = serializers.SerializerMethodField()

    class Meta:
        model  = Movie
        fields = [
            'id', 'title', 'description',
            'poster_url', 'video_url', 'trailer_url',
            'genre', 'year', 'duration', 'rating', 'created_at',
        ]

    def get_poster_url(self, obj):
        request = self.context.get('request')
        if obj.poster and obj.poster.name:
            return request.build_absolute_uri(obj.poster.url) if request else obj.poster.url
        return obj.poster_remote_url or None

    def get_video_url(self, obj):
        request = self.context.get('request')
        if obj.video and obj.video.name and request:
            return request.build_absolute_uri(obj.video.url)
        return None

    def get_trailer_url(self, obj):
        return obj.trailer_url or None