import time
from django.test import TestCase, RequestFactory
from django.contrib.auth.models import User
from django.contrib.sessions.middleware import SessionMiddleware
from django.utils import timezone

from bookmarks.models import BookmarkSearch, UserProfile
from bookmarks.queries import query_bookmarks


class RandomSortTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpass')
        self.profile = UserProfile.objects.get(user=self.user)
        self.factory = RequestFactory()
        
        # Create some test bookmarks
        self.bookmarks = []
        for i in range(10):
            bookmark = self.user.bookmark_set.create(
                url=f'http://example{i}.com',
                title=f'Bookmark {i}',
                date_added=timezone.now(),
                date_modified=timezone.now()
            )
            self.bookmarks.append(bookmark)

    def add_session_to_request(self, request):
        """Add session middleware to request"""
        middleware = SessionMiddleware(lambda req: None)
        middleware.process_request(request)
        request.session.save()

    def test_random_sort_with_same_seed_produces_same_order(self):
        """Test that random sort with the same seed produces the same order"""
        # Create two requests with the same seed
        request1 = self.factory.get('/')
        request1.user = self.user
        self.add_session_to_request(request1)
        request1.session['random_sort_seed'] = 12345

        request2 = self.factory.get('/')
        request2.user = self.user
        self.add_session_to_request(request2)
        request2.session['random_sort_seed'] = 12345

        # Create search objects
        search1 = BookmarkSearch(sort=BookmarkSearch.SORT_RANDOM, request=request1)
        search2 = BookmarkSearch(sort=BookmarkSearch.SORT_RANDOM, request=request2)

        # Query bookmarks
        query1 = query_bookmarks(self.user, self.profile, search1)
        query2 = query_bookmarks(self.user, self.profile, search2)

        # Convert to lists
        result1 = list(query1)
        result2 = list(query2)

        # Should have same order
        self.assertEqual(result1, result2)

    def test_random_sort_with_different_seeds_produces_different_order(self):
        """Test that random sort with different seeds produces different orders"""
        # Create two requests with different seeds
        request1 = self.factory.get('/')
        request1.user = self.user
        self.add_session_to_request(request1)
        request1.session['random_sort_seed'] = 12345

        request2 = self.factory.get('/')
        request2.user = self.user
        self.add_session_to_request(request2)
        request2.session['random_sort_seed'] = 67890

        # Create search objects
        search1 = BookmarkSearch(sort=BookmarkSearch.SORT_RANDOM, request=request1)
        search2 = BookmarkSearch(sort=BookmarkSearch.SORT_RANDOM, request=request2)

        # Query bookmarks
        query1 = query_bookmarks(self.user, self.profile, search1)
        query2 = query_bookmarks(self.user, self.profile, search2)

        # Convert to lists
        result1 = list(query1)
        result2 = list(query2)

        # Should have different orders (very unlikely to be the same)
        self.assertNotEqual(result1, result2)

    def test_random_sort_returns_all_bookmarks(self):
        """Test that random sort returns all bookmarks"""
        request = self.factory.get('/')
        request.user = self.user
        self.add_session_to_request(request)
        request.session['random_sort_seed'] = 12345

        search = BookmarkSearch(sort=BookmarkSearch.SORT_RANDOM, request=request)
        query = query_bookmarks(self.user, self.profile, search)
        result = list(query)

        # Should return all bookmarks
        self.assertEqual(len(result), len(self.bookmarks))
        self.assertCountEqual(result, self.bookmarks)

    def test_random_sort_without_seed_uses_timestamp(self):
        """Test that random sort without seed uses current timestamp"""
        request = self.factory.get('/')
        request.user = self.user
        self.add_session_to_request(request)
        # Don't set seed in session

        search = BookmarkSearch(sort=BookmarkSearch.SORT_RANDOM, request=request)
        query = query_bookmarks(self.user, self.profile, search)
        result = list(query)

        # Should return all bookmarks
        self.assertEqual(len(result), len(self.bookmarks))
        self.assertCountEqual(result, self.bookmarks) 