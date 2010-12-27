import os

from django.conf import settings
from django.contrib.auth.models import User, Permission
from django.core import mail
from django.test import TestCase
from django.utils import simplejson
from djblets.siteconfig.models import SiteConfiguration
from djblets.webapi.errors import DOES_NOT_EXIST, INVALID_ATTRIBUTE, \
                                  INVALID_FORM_DATA, PERMISSION_DENIED

from reviewboard import initialize
from reviewboard.diffviewer.models import DiffSet
from reviewboard.notifications.tests import EmailTestHelper
from reviewboard.reviews.models import Group, ReviewRequest, \
                                       ReviewRequestDraft, Review, \
                                       Comment, Screenshot, ScreenshotComment
from reviewboard.scmtools.models import Repository, Tool
from reviewboard.site.urlresolvers import local_site_reverse
from reviewboard.site.models import LocalSite
from reviewboard.webapi.errors import INVALID_REPOSITORY


class BaseWebAPITestCase(TestCase, EmailTestHelper):
    fixtures = ['test_users', 'test_reviewrequests', 'test_scmtools',
                'test_site']
    local_site_name = 'local-site-1'

    def setUp(self):
        initialize()

        siteconfig = SiteConfiguration.objects.get_current()
        siteconfig.set("mail_send_review_mail", True)
        siteconfig.set("auth_require_sitewide_login", False)
        siteconfig.save()
        mail.outbox = []

        svn_repo_path = os.path.join(os.path.dirname(__file__),
                                     '../scmtools/testdata/svn_repo')
        self.repository = Repository(name='Subversion SVN',
                                     path='file://' + svn_repo_path,
                                     tool=Tool.objects.get(name='Subversion'))
        self.repository.save()

        self.client.login(username="grumpy", password="grumpy")
        self.user = User.objects.get(username="grumpy")

        self.base_url = 'http://testserver'

    def tearDown(self):
        self.client.logout()

    def api_func_wrapper(self, api_func, path, query, expected_status,
                         follow_redirects, expected_redirects):
        response = api_func(path, query, follow=follow_redirects)
        self.assertEqual(response.status_code, expected_status)

        if expected_redirects:
            self.assertEqual(len(response.redirect_chain),
                             len(expected_redirects))

            for redirect in expected_redirects:
                self.assertEqual(response.redirect_chain[0][0],
                                 self.base_url + expected_redirects[0])

        return response

    def apiGet(self, path, query={}, follow_redirects=False,
               expected_status=200, expected_redirects=[]):
        path = self._normalize_path(path)

        print 'GETing %s' % path
        print "Query data: %s" % query

        response = self.api_func_wrapper(self.client.get, path, query,
                                         expected_status, follow_redirects,
                                         expected_redirects)

        print "Raw response: %s" % response.content

        rsp = simplejson.loads(response.content)
        print "Response: %s" % rsp

        return rsp

    def api_post_with_response(self, path, query={}, expected_status=201):
        path = self._normalize_path(path)

        print 'POSTing to %s' % path
        print "Post data: %s" % query
        response = self.client.post(path, query)
        print "Raw response: %s" % response.content
        self.assertEqual(response.status_code, expected_status)

        return self._get_result(response, expected_status), response

    def apiPost(self, *args, **kwargs):
        rsp, result = self.api_post_with_response(*args, **kwargs)

        return rsp

    def apiPut(self, path, query={}, expected_status=200,
               follow_redirects=False, expected_redirects=[]):
        path = self._normalize_path(path)

        print 'PUTing to %s' % path
        print "Post data: %s" % query
        response = self.api_func_wrapper(self.client.put, path, query,
                                         expected_status, follow_redirects,
                                         expected_redirects)
        print "Raw response: %s" % response.content
        self.assertEqual(response.status_code, expected_status)

        return self._get_result(response, expected_status)

    def apiDelete(self, path, expected_status=204):
        path = self._normalize_path(path)

        print 'DELETEing %s' % path
        response = self.client.delete(path)
        print "Raw response: %s" % response.content
        self.assertEqual(response.status_code, expected_status)

        return self._get_result(response, expected_status)

    def _normalize_path(self, path):
        if path.startswith(self.base_url):
            return path[len(self.base_url):]
        else:
            return path

    def _get_result(self, response, expected_status):
        if expected_status == 204:
            self.assertEqual(response.content, '')
            rsp = None
        else:
            rsp = simplejson.loads(response.content)
            print "Response: %s" % rsp

        return rsp

    #
    # Some utility functions shared across test suites.
    #
    def _postNewReviewRequest(self, local_site_name=None,
                              repository=None):
        """Creates a review request and returns the payload response."""
        if not repository:
            repository = self.repository
        rsp = self.apiPost(
            ReviewRequestResourceTests.get_list_url(local_site_name),
            { 'repository': repository.path, })

        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(
            rsp['review_request']['links']['repository']['href'],
            self.base_url +
            RepositoryResourceTests.get_item_url(repository.id,
                                                 local_site_name))

        return rsp

    def _postNewReview(self, review_request, body_top="",
                       body_bottom=""):
        """Creates a review and returns the payload response."""
        if review_request.local_site:
            local_site_name = review_request.local_site.name
        else:
            local_site_name = None

        post_data = {
            'body_top': body_top,
            'body_bottom': body_bottom,
        }

        rsp = self.apiPost(ReviewResourceTests.get_list_url(review_request,
                                                            local_site_name),
                           post_data)

        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(rsp['review']['body_top'], body_top)
        self.assertEqual(rsp['review']['body_bottom'], body_bottom)

        return rsp

    def _postNewDiffComment(self, review_request, review_id, comment_text,
                            filediff_id=None, interfilediff_id=None,
                            first_line=10, num_lines=5, issue_opened=False):
        """Creates a diff comment and returns the payload response."""
        if filediff_id is None:
            diffset = review_request.diffset_history.diffsets.latest()
            filediff = diffset.files.all()[0]
            filediff_id = filediff.id

        data = {
            'filediff_id': filediff_id,
            'text': comment_text,
            'first_line': first_line,
            'num_lines': num_lines,
            'issue_opened': issue_opened,
        }

        if interfilediff_id is not None:
            data['interfilediff_id'] = interfilediff_id

        if review_request.local_site:
            local_site_name = review_request.local_site.name
        else:
            local_site_name = None

        review = Review.objects.get(pk=review_id)

        rsp = self.apiPost(
            ReviewCommentResourceTests.get_list_url(review, local_site_name),
            data)
        self.assertEqual(rsp['stat'], 'ok')

        return rsp

    def _postNewScreenshotComment(self, review_request, review_id, screenshot,
                                  comment_text, x, y, w, h, issue_opened):
        """Creates a screenshot comment and returns the payload response."""
        if review_request.local_site:
            local_site_name = review_request.local_site.name
        else:
            local_site_name = None

        post_data = {
            'screenshot_id': screenshot.id,
            'text': comment_text,
            'x': x,
            'y': y,
            'w': w,
            'h': h,
            'issue_opened': issue_opened,
        }

        review = Review.objects.get(pk=review_id)
        rsp = self.apiPost(
            DraftReviewScreenshotCommentResourceTests.get_list_url(
                review, local_site_name),
            post_data)

        self.assertEqual(rsp['stat'], 'ok')

        return rsp

    def _postNewScreenshot(self, review_request):
        """Creates a screenshot and returns the payload response."""
        if review_request.local_site:
            local_site_name = review_request.local_site.name
        else:
            local_site_name = None

        f = open(self._getTrophyFilename(), "r")
        self.assert_(f)

        post_data = {
            'path': f,
        }

        rsp = self.apiPost(
            ScreenshotResourceTests.get_list_url(review_request,
                                                 local_site_name),
            post_data)
        f.close()

        self.assertEqual(rsp['stat'], 'ok')

        return rsp

    def _postNewDiff(self, review_request):
        """Creates a diff and returns the payload response."""
        diff_filename = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "scmtools", "testdata", "svn_makefile.diff")

        f = open(diff_filename, "r")
        rsp = self.apiPost(DiffResourceTests.get_list_url(review_request), {
            'path': f,
            'basedir': "/trunk",
        })
        f.close()

        self.assertEqual(rsp['stat'], 'ok')

        return rsp

    def _getTrophyFilename(self):
        return os.path.join(settings.HTDOCS_ROOT,
                            "media", "rb", "images", "trophy.png")


class ServerInfoResourceTests(BaseWebAPITestCase):
    """Testing the ServerInfoResource APIs."""
    def test_get_server_info(self):
        """Testing the GET info/ API"""
        rsp = self.apiGet(self.get_url())
        self.assertEqual(rsp['stat'], 'ok')
        self.assertTrue('info' in rsp)
        self.assertTrue('product' in rsp['info'])
        self.assertTrue('site' in rsp['info'])

    def test_get_server_info_with_site(self):
        """Testing the GET info/ API with a local site"""
        self.client.logout()
        self.client.login(username="doc", password="doc")

        rsp = self.apiGet(self.get_url(self.local_site_name))
        self.assertEqual(rsp['stat'], 'ok')
        self.assertTrue('info' in rsp)
        self.assertTrue('product' in rsp['info'])
        self.assertTrue('site' in rsp['info'])

    def test_get_server_info_with_site_no_access(self):
        """Testing the GET info/ API with a local site and Permission Denied error"""
        self.apiGet(self.get_url(self.local_site_name),
                    expected_status=403)

    def get_url(self, local_site_name=None):
        return local_site_reverse('info-resource',
                                  local_site_name=local_site_name)


class SessionResourceTests(BaseWebAPITestCase):
    """Testing the SessionResource APIs."""
    def test_get_session_with_logged_in_user(self):
        """Testing the GET session/ API with logged in user"""
        rsp = self.apiGet(self.get_url())
        self.assertEqual(rsp['stat'], 'ok')
        self.assertTrue('session' in rsp)
        self.assertTrue(rsp['session']['authenticated'])
        self.assertEqual(rsp['session']['links']['user']['title'],
                         self.user.username)

    def test_get_session_with_anonymous_user(self):
        """Testing the GET session/ API with anonymous user"""
        self.client.logout()

        rsp = self.apiGet(self.get_url())
        self.assertEqual(rsp['stat'], 'ok')
        self.assertTrue('session' in rsp)
        self.assertFalse(rsp['session']['authenticated'])

    def test_get_session_with_site(self):
        """Testing the GET session/ API with a local site"""
        self.client.logout()
        self.client.login(username='doc', password='doc')

        rsp = self.apiGet(self.get_url(self.local_site_name))
        self.assertEqual(rsp['stat'], 'ok')
        self.assertTrue('session' in rsp)
        self.assertTrue(rsp['session']['authenticated'])
        self.assertEqual(rsp['session']['links']['user']['title'], 'doc')

    def test_get_session_with_site_no_access(self):
        """Testing the GET session/ API with a local site and Permission Denied error"""
        self.apiGet(self.get_url(self.local_site_name),
                    expected_status=403)

    def get_url(self, local_site_name=None):
        return local_site_reverse('session-resource',
                                  local_site_name=local_site_name)


class RepositoryResourceTests(BaseWebAPITestCase):
    """Testing the RepositoryResource APIs."""

    def test_get_repositories(self):
        """Testing the GET repositories/ API"""
        rsp = self.apiGet(self.get_list_url())
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['repositories']),
                         Repository.objects.accessible(self.user).count())

    def test_get_repositories_with_site(self):
        """Testing the GET repositories/ API with a local site"""
        self.client.logout()
        self.client.login(username='doc', password='doc')

        rsp = self.apiGet(self.get_list_url(self.local_site_name))
        self.assertEqual(len(rsp['repositories']),
                         Repository.objects.filter(
                             local_site__name=self.local_site_name).count())

    def test_get_repositories_with_site_no_access(self):
        """Testing the GET repositories/ API with a local site and Permission Denied error"""
        self.apiGet(self.get_list_url(self.local_site_name),
                    expected_status=403)

    def get_list_url(self, local_site_name=None):
        return local_site_reverse('repositories-resource',
                                  local_site_name=local_site_name)

    @classmethod
    def get_item_url(cls, repository_id, local_site_name=None):
        return local_site_reverse('repository-resource',
                                  local_site_name=local_site_name,
                                  kwargs={
                                      'repository_id': repository_id,
                                  })


class RepositoryInfoResourceTests(BaseWebAPITestCase):
    """Testing the RepositoryInfoResource APIs."""
    def test_get_repository_info(self):
        """Testing the GET repositories/<id>/info API"""
        rsp = self.apiGet(self.get_url(self.repository))
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(rsp['info'],
                         self.repository.get_scmtool().get_repository_info())

    def test_get_repository_info_with_site(self):
        """Testing the GET repositories/<id>/info API with a local site"""
        self.client.logout()
        self.client.login(username='doc', password='doc')

        repository = Repository.objects.get(name='V8 SVN')
        rsp = self.apiGet(self.get_url(repository, self.local_site_name))
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(rsp['info'],
                         repository.get_scmtool().get_repository_info())

    def test_get_repository_info_with_site_no_access(self):
        """Testing the GET repositories/<id>/info API with a local site and Permission Denied error"""
        repository = Repository.objects.get(name='V8 SVN')

        self.apiGet(self.get_url(self.repository, self.local_site_name),
                    expected_status=403)

    def get_url(self, repository, local_site_name=None):
        return local_site_reverse('info-resource',
                                  local_site_name=local_site_name,
                                  kwargs={
                                      'repository_id': repository.pk,
                                  })


class ReviewGroupResourceTests(BaseWebAPITestCase):
    """Testing the ReviewGroupResource APIs."""

    def test_get_groups(self):
        """Testing the GET groups/ API"""
        rsp = self.apiGet(self.get_list_url())
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['groups']),
                         Group.objects.accessible(self.user).count())
        self.assertEqual(len(rsp['groups']), 4)

    def test_get_groups_with_site(self):
        """Testing the GET groups/ API with a local site"""
        self.client.logout()
        self.client.login(username='doc', password='doc')

        local_site = LocalSite.objects.get(name=self.local_site_name)
        groups = Group.objects.accessible(self.user, local_site=local_site)

        rsp = self.apiGet(self.get_list_url(self.local_site_name))
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['groups']), groups.count())
        self.assertEqual(len(rsp['groups']), 1)

    def test_get_groups_with_site_no_access(self):
        """Testing the GET groups/ API with a local site and Permission Denied error"""
        self.apiGet(self.get_list_url(self.local_site_name),
                    expected_status=403)

    def test_get_groups_with_q(self):
        """Testing the GET groups/?q= API"""
        rsp = self.apiGet(self.get_list_url(), {'q': 'dev'})
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['groups']), 1) #devgroup

    def test_get_group_public(self):
        """Testing the GET groups/<id>/ API"""
        group = Group.objects.create(name='test-group')

        rsp = self.apiGet(self.get_item_url(group.name))
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(rsp['group']['name'], group.name)
        self.assertEqual(rsp['group']['display_name'], group.display_name)
        self.assertEqual(rsp['group']['invite_only'], False)

    def test_get_group_invite_only(self):
        """Testing the GET groups/<id>/ API with invite-only"""
        group = Group.objects.create(name='test-group', invite_only=True)
        group.users.add(self.user)

        rsp = self.apiGet(self.get_item_url(group.name))
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(rsp['group']['invite_only'], True)

    def test_get_group_invite_only_with_permission_denied_error(self):
        """Testing the GET groups/<id>/ API with invite-only and Permission Denied error"""
        group = Group.objects.create(name='test-group', invite_only=True)

        rsp = self.apiGet(self.get_item_url(group.name),
                          expected_status=403)
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], PERMISSION_DENIED.code)

    def test_get_group_with_site(self):
        """Testing the GET groups/<id>/ API with a local site"""
        self.client.logout()
        self.client.login(username='doc', password='doc')

        group = Group.objects.get(name='sitegroup')

        rsp = self.apiGet(self.get_item_url('sitegroup', self.local_site_name))
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(rsp['group']['name'], group.name)
        self.assertEqual(rsp['group']['display_name'], group.display_name)

    def test_get_group_with_site_no_access(self):
        """Testing the GET groups/<id>/ API with a local site and Permission Denied error"""
        self.apiGet(self.get_item_url('sitegroup', self.local_site_name),
                    expected_status=403)

    def get_list_url(self, local_site_name=None):
        return local_site_reverse('groups-resource',
                                  local_site_name=local_site_name)

    def get_item_url(self, group_name, local_site_name=None):
        return local_site_reverse('group-resource',
                                  local_site_name=local_site_name,
                                  kwargs={
                                      'group_name': group_name,
                                  })


class UserResourceTests(BaseWebAPITestCase):
    """Testing the UserResource API tests."""

    def test_get_users(self):
        """Testing the GET users/ API"""
        rsp = self.apiGet(self.get_list_url())
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['users']), User.objects.count())

    def test_get_users_with_q(self):
        """Testing the GET users/?q= API"""
        rsp = self.apiGet(self.get_list_url(), {'q': 'gru'})
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['users']), 1) # grumpy

    def test_get_users_with_site(self):
        """Testing the GET users/ API with a local site"""
        self.client.logout()
        self.client.login(username='doc', password='doc')

        local_site = LocalSite.objects.get(name=self.local_site_name)
        rsp = self.apiGet(self.get_list_url(self.local_site_name))
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['users']), local_site.users.count())

    def test_get_users_with_site_no_access(self):
        """Testing the GET users/ API with a local site and Permission Denied error"""
        self.apiGet(self.get_list_url(self.local_site_name),
                    expected_status=403)

    def test_get_user(self):
        """Testing the GET users/<username>/ API"""
        username = 'doc'
        user = User.objects.get(username=username)

        rsp = self.apiGet(self.get_item_url(username))
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(rsp['user']['username'], user.username)
        self.assertEqual(rsp['user']['first_name'], user.first_name)
        self.assertEqual(rsp['user']['last_name'], user.last_name)
        self.assertEqual(rsp['user']['id'], user.id)
        self.assertEqual(rsp['user']['email'], user.email)

    def test_get_user_with_site(self):
        """Testing the GET users/<username>/ API with a local site"""
        self.client.logout()
        self.client.login(username='doc', password='doc')

        username = 'doc'
        user = User.objects.get(username=username)

        rsp = self.apiGet(self.get_item_url(username, self.local_site_name))
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(rsp['user']['username'], user.username)
        self.assertEqual(rsp['user']['first_name'], user.first_name)
        self.assertEqual(rsp['user']['last_name'], user.last_name)
        self.assertEqual(rsp['user']['id'], user.id)
        self.assertEqual(rsp['user']['email'], user.email)

    def test_get_missing_user_with_site(self):
        """Testing the GET users/<username>/ API with a local site"""
        self.client.logout()
        self.client.login(username='doc', password='doc')

        rsp = self.apiGet(self.get_item_url('dopey', self.local_site_name),
                          expected_status=404)

    def test_get_user_with_site_no_access(self):
        """Testing the GET users/<username>/ API with a local site and Permission Denied error."""
        self.apiGet(self.get_item_url('doc', self.local_site_name),
                    expected_status=403)

    def get_list_url(self, local_site_name=None):
        return local_site_reverse('users-resource',
                                  local_site_name=local_site_name)

    @classmethod
    def get_item_url(cls, username, local_site_name=None):
        return local_site_reverse('user-resource',
                                  local_site_name=local_site_name,
                                  kwargs={
                                      'username': username,
                                  })


class WatchedReviewRequestResourceTests(BaseWebAPITestCase):
    """Testing the WatchedReviewRequestResource API tests."""

    def test_post_watched_review_request(self):
        """Testing the POST users/<username>/watched/review_request/ API"""
        review_request = ReviewRequest.objects.public()[0]
        rsp = self.apiPost(self.get_list_url(self.user.username), {
            'object_id': review_request.display_id,
        })
        self.assertEqual(rsp['stat'], 'ok')
        self.assert_(review_request in
                     self.user.get_profile().starred_review_requests.all())

    def test_post_watched_review_request_with_does_not_exist_error(self):
        """Testing the POST users/<username>/watched/review_request/ with Does Not Exist error"""
        rsp = self.apiPost(self.get_list_url(self.user.username), {
            'object_id': 999,
        }, expected_status=404)
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], DOES_NOT_EXIST.code)

    def test_post_watched_review_request_with_site(self):
        """Testing the POST users/<username>/watched/review_request/ API with a local site"""
        username = 'doc'
        user = User.objects.get(username=username)

        self.client.logout()
        self.client.login(username=username, password='doc')

        local_site = LocalSite.objects.get(name=self.local_site_name)
        review_request = ReviewRequest.objects.public(local_site=local_site)[0]

        rsp = self.apiPost(self.get_list_url(username, self.local_site_name),
                           { 'object_id': review_request.display_id, })
        self.assertEqual(rsp['stat'], 'ok')
        self.assertTrue(review_request in
                        user.get_profile().starred_review_requests.all())

    def test_post_watched_review_request_with_site_does_not_exist_error(self):
        """Testing the POST users/<username>/watched/review_request/ API with a local site and Does Not Exist error"""
        self.client.logout()
        self.client.login(username='doc', password='doc')

        rsp = self.apiPost(self.get_list_url('doc', self.local_site_name),
                           { 'object_id': 10, },
                           expected_status=404)
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], DOES_NOT_EXIST.code)

    def test_post_watched_review_request_with_site_no_access(self):
        """Testing the POST users/<username>/watched/review_request/ API with a local site and Permission Denied error"""
        rsp = self.apiPost(self.get_list_url('doc', self.local_site_name),
                           { 'object_id': 10, },
                           expected_status=403)
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], PERMISSION_DENIED.code)

    def test_delete_watched_review_request(self):
        """Testing the DELETE users/<username>/watched/review_request/ API"""
        # First, star it.
        self.test_post_watched_review_request()

        review_request = ReviewRequest.objects.public()[0]
        self.apiDelete(self.get_item_url(self.user.username,
                                          review_request.display_id))
        self.assertTrue(review_request not in
                        self.user.get_profile().starred_review_requests.all())

    def test_delete_watched_review_request_with_does_not_exist_error(self):
        """Testing the DELETE users/<username>/watched/review_request/ API with Does Not Exist error"""
        rsp = self.apiDelete(self.get_item_url(self.user.username, 999),
                             expected_status=404)
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], DOES_NOT_EXIST.code)

    def test_delete_watched_review_request_with_site(self):
        """Testing the DELETE users/<username>/watched/review_request/ API with a local site"""
        self.test_post_watched_review_request_with_site()

        user = User.objects.get(username='doc')
        review_request = ReviewRequest.objects.get(
            local_id=1, local_site__name=self.local_site_name)

        self.apiDelete(self.get_item_url(user.username,
                                          review_request.display_id,
                                          self.local_site_name))
        self.assertTrue(review_request not in
                        user.get_profile().starred_review_requests.all())

    def test_delete_watched_review_request_with_site_no_access(self):
        """Testing the DELETE users/<username>/watched/review_request/ API with a local site and Permission Denied error"""
        rsp = self.apiDelete(self.get_item_url(self.user.username, 1,
                                                self.local_site_name),
                             expected_status=403)
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], PERMISSION_DENIED.code)

    def test_get_watched_review_requests(self):
        """Testing the GET users/<username>/watched/review_request/ API"""
        self.test_post_watched_review_request()

        rsp = self.apiGet(self.get_list_url(self.user.username))
        self.assertEqual(rsp['stat'], 'ok')

        watched = self.user.get_profile().starred_review_requests.all()
        apiwatched = rsp['watched_review_requests']

        self.assertEqual(len(watched), len(apiwatched))
        for i in range(len(watched)):
            self.assertEqual(watched[i].id,
                             apiwatched[i]['watched_review_request']['id'])
            self.assertEqual(watched[i].summary,
                             apiwatched[i]['watched_review_request']['summary'])

    def test_get_watched_review_requests_with_site(self):
        """Testing the GET users/<username>/watched/review_request/ API with a local site"""
        username = 'doc'
        user = User.objects.get(username=username)

        self.test_post_watched_review_request_with_site()

        rsp = self.apiGet(self.get_list_url(username, self.local_site_name))

        watched = user.get_profile().starred_review_requests.filter(
            local_site__name=self.local_site_name)
        apiwatched = rsp['watched_review_requests']

        self.assertEqual(len(watched), len(apiwatched))
        for i in range(len(watched)):
            self.assertEqual(watched[i].display_id,
                             apiwatched[i]['watched_review_request']['id'])
            self.assertEqual(watched[i].summary,
                             apiwatched[i]['watched_review_request']['summary'])

    def test_get_watched_review_requests_with_site_no_access(self):
        """Testing the GET users/<username>/watched/review_request/ API with a local site and Permission Denied error"""
        rsp = self.apiGet(self.get_list_url(self.user.username,
                                             self.local_site_name),
                          expected_status=403)
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], PERMISSION_DENIED.code)

    def test_get_watched_review_requests_with_site_does_not_exist(self):
        """Testing the GET users/<username>/watched/review_request/ API with a local site and Does Not Exist error"""
        self.client.logout()
        self.client.login(username='doc', password='doc')

        rsp = self.apiGet(self.get_list_url(self.user.username,
                                             self.local_site_name),
                          expected_status=404)
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], DOES_NOT_EXIST.code)

    def get_list_url(self, username, local_site_name=None):
        return local_site_reverse('watched-review-requests-resource',
                                  local_site_name=local_site_name,
                                  kwargs={
                                      'username': username,
                                  })

    def get_item_url(self, username, object_id, local_site_name=None):
        return local_site_reverse('watched-review-request-resource',
                                  local_site_name=local_site_name,
                                  kwargs={
                                      'username': username,
                                      'watched_obj_id': object_id,
                                  })


class WatchedReviewGroupResourceTests(BaseWebAPITestCase):
    """Testing the WatchedReviewGroupResource API tests."""

    def test_post_watched_review_group(self):
        """Testing the POST users/<username>/watched/review-groups/ API"""
        group = Group.objects.get(name='devgroup', local_site=None)

        rsp = self.apiPost(self.get_list_url(self.user.username), {
            'object_id': group.name,
        })
        self.assertEqual(rsp['stat'], 'ok')
        self.assert_(group in self.user.get_profile().starred_groups.all())

    def test_post_watched_review_group_with_does_not_exist_error(self):
        """Testing the POST users/<username>/watched/review-groups/ API with Does Not Exist error"""
        rsp = self.apiPost(self.get_list_url(self.user.username), {
            'object_id': 'invalidgroup',
        }, expected_status=404)
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], DOES_NOT_EXIST.code)

    def test_post_watched_review_group_with_site(self):
        """Testing the POST users/<username>/watched/review-groups/ API with a local site"""
        username = 'doc'
        user = User.objects.get(username=username)

        self.client.logout()
        self.client.login(username=username, password='doc')

        group = Group.objects.get(name='sitegroup',
                                  local_site__name=self.local_site_name)

        rsp = self.apiPost(self.get_list_url(username, self.local_site_name),
                           { 'object_id': group.name, })
        self.assertEqual(rsp['stat'], 'ok')
        self.assertTrue(group in user.get_profile().starred_groups.all())

    def test_post_watched_review_group_with_site_does_not_exist_error(self):
        """Testing the POST users/<username>/watched/review-groups/ API with a local site and Does Not Exist error"""
        username = 'doc'

        self.client.logout()
        self.client.login(username=username, password='doc')

        rsp = self.apiPost(self.get_list_url(username, self.local_site_name),
                           { 'object_id': 'devgroup', },
                           expected_status=404)
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], DOES_NOT_EXIST.code)

    def test_post_watched_review_group_with_site_no_access(self):
        """Testing the POST users/<username>/watched/review-groups/ API with a local site and Permission Denied error"""
        rsp = self.apiPost(self.get_list_url(self.user.username,
                                              self.local_site_name),
                           { 'object_id': 'devgroup', },
                           expected_status=403)
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], PERMISSION_DENIED.code)


    def test_delete_watched_review_group(self):
        """Testing the DELETE users/<username>/watched/review-groups/<id>/ API"""
        # First, star it.
        self.test_post_watched_review_group()

        group = Group.objects.get(name='devgroup', local_site=None)

        self.apiDelete(self.get_item_url(self.user.username, group.name))
        self.assertFalse(group in
                         self.user.get_profile().starred_groups.all())

    def test_delete_watched_review_group_with_does_not_exist_error(self):
        """Testing the DELETE users/<username>/watched/review-groups/<id>/ API with Does Not Exist error"""
        rsp = self.apiDelete(self.get_item_url(self.user.username,
                                                'invalidgroup'),
                             expected_status=404)
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], DOES_NOT_EXIST.code)

    def test_delete_watched_review_group_with_site(self):
        """Testing the DELETE users/<username>/watched/review-groups/<id>/ API with a local site"""
        self.test_post_watched_review_group_with_site()

        user = User.objects.get(username='doc')
        group = Group.objects.get(name='sitegroup',
                                  local_site__name=self.local_site_name)

        self.apiDelete(self.get_item_url(user.username, group.name,
                                          self.local_site_name))
        self.assertFalse(group in user.get_profile().starred_groups.all())

    def test_delete_watched_review_group_with_site_no_access(self):
        """Testing the DELETE users/<username>/watched/review-groups/<id>/ API with a local site and Permission Denied error"""
        rsp = self.apiDelete(self.get_item_url(self.user.username, 'group',
                                                self.local_site_name),
                             expected_status=403)
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], PERMISSION_DENIED.code)

    def test_get_watched_review_groups(self):
        """Testing the GET users/<username>/watched/review-groups/ API"""
        self.test_post_watched_review_group()

        rsp = self.apiGet(self.get_list_url(self.user.username))
        self.assertEqual(rsp['stat'], 'ok')

        watched = self.user.get_profile().starred_groups.all()
        apigroups = rsp['watched_review_groups']

        self.assertEqual(len(apigroups), len(watched))

        for id in range(len(watched)):
            self.assertEqual(apigroups[id]['watched_review_group']['name'],
                             watched[id].name)

    def test_get_watched_review_groups_with_site(self):
        """Testing the GET users/<username>/watched/review-groups/ API with a local site"""
        self.test_post_watched_review_group_with_site()

        rsp = self.apiGet(self.get_list_url('doc', self.local_site_name))

        watched = self.user.get_profile().starred_groups.filter(
            local_site__name=self.local_site_name)
        apigroups = rsp['watched_review_groups']

        self.assertEqual(rsp['stat'], 'ok')

        for id in range(len(watched)):
            self.assertEqual(apigroups[id]['watched_review_group']['name'],
                             watched[id].name)

    def test_get_watched_review_groups_with_site_no_access(self):
        """Testing the GET users/<username>/watched/review-groups/ API with a local site and Permission Denied error"""
        watched_url = \
            local_site_reverse('watched-review-groups-resource',
                               local_site_name=self.local_site_name,
                               kwargs={ 'username': self.user.username })

        rsp = self.apiGet(self.get_list_url(self.user.username,
                                             self.local_site_name),
                          expected_status=403)
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], PERMISSION_DENIED.code)

    def get_list_url(self, username, local_site_name=None):
        return local_site_reverse('watched-review-groups-resource',
                                  local_site_name=local_site_name,
                                  kwargs={
                                      'username': username,
                                  })

    def get_item_url(self, username, object_id, local_site_name=None):
        return local_site_reverse('watched-review-group-resource',
                                  local_site_name=local_site_name,
                                  kwargs={
                                      'username': username,
                                      'watched_obj_id': object_id,
                                  })


class ReviewRequestResourceTests(BaseWebAPITestCase):
    """Testing the ReviewRequestResource API tests."""

    def test_get_reviewrequests(self):
        """Testing the GET review-requests/ API"""
        rsp = self.apiGet(self.get_list_url())
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['review_requests']),
                         ReviewRequest.objects.public().count())

    def test_get_reviewrequests_with_site(self):
        """Testing the GET review-requests/ API with a local site"""
        self.client.logout()
        self.client.login(username='doc', password='doc')
        local_site = LocalSite.objects.get(name=self.local_site_name)

        rsp = self.apiGet(self.get_list_url(self.local_site_name))
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['review_requests']),
                         ReviewRequest.objects.public(
                             local_site=local_site).count())

    def test_get_reviewrequests_with_site_no_access(self):
        """Testing the GET review-requests/ API with a local site and Permission Denied error"""
        self.apiGet(self.get_list_url(self.local_site_name),
                    expected_status=403)

    def test_get_reviewrequests_with_status(self):
        """Testing the GET review-requests/?status= API"""
        url = self.get_list_url()

        rsp = self.apiGet(url, {'status': 'submitted'})
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['review_requests']),
                         ReviewRequest.objects.public(status='S').count())

        rsp = self.apiGet(url, {'status': 'discarded'})
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['review_requests']),
                         ReviewRequest.objects.public(status='D').count())

        rsp = self.apiGet(url, {'status': 'all'})
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['review_requests']),
                         ReviewRequest.objects.public(status=None).count())

    def test_get_reviewrequests_with_counts_only(self):
        """Testing the GET review-requests/?counts-only=1 API"""
        rsp = self.apiGet(self.get_list_url(), {
            'counts-only': 1,
        })
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(rsp['count'], ReviewRequest.objects.public().count())

    def test_get_reviewrequests_with_to_groups(self):
        """Testing the GET review-requests/?to-groups= API"""
        rsp = self.apiGet(self.get_list_url(), {
            'to-groups': 'devgroup',
        })
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['review_requests']),
                         ReviewRequest.objects.to_group("devgroup",
                                                        None).count())

    def test_get_reviewrequests_with_to_groups_and_status(self):
        """Testing the GET review-requests/?to-groups=&status= API"""
        url = self.get_list_url()

        rsp = self.apiGet(url, {
            'status': 'submitted',
            'to-groups': 'devgroup',
        })
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['review_requests']),
            ReviewRequest.objects.to_group("devgroup", None,
                                           status='S').count())

        rsp = self.apiGet(url, {
            'status': 'discarded',
            'to-groups': 'devgroup',
        })
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['review_requests']),
            ReviewRequest.objects.to_group("devgroup", None,
                                           status='D').count())

    def test_get_reviewrequests_with_to_groups_and_counts_only(self):
        """Testing the GET review-requests/?to-groups=&counts-only=1 API"""
        rsp = self.apiGet(self.get_list_url(), {
            'to-groups': 'devgroup',
            'counts-only': 1,
        })
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(rsp['count'],
                         ReviewRequest.objects.to_group("devgroup",
                                                        None).count())

    def test_get_reviewrequests_with_to_users(self):
        """Testing the GET review-requests/?to-users= API"""
        rsp = self.apiGet(self.get_list_url(), {
            'to-users': 'grumpy',
        })
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['review_requests']),
                         ReviewRequest.objects.to_user("grumpy").count())

    def test_get_reviewrequests_with_to_users_and_status(self):
        """Testing the GET review-requests/?to-users=&status= API"""
        url = self.get_list_url()

        rsp = self.apiGet(url, {
            'status': 'submitted',
            'to-users': 'grumpy',
        })

        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['review_requests']),
            ReviewRequest.objects.to_user("grumpy", status='S').count())

        rsp = self.apiGet(url, {
            'status': 'discarded',
            'to-users': 'grumpy',
        })
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['review_requests']),
            ReviewRequest.objects.to_user("grumpy", status='D').count())

    def test_get_reviewrequests_with_to_users_and_counts_only(self):
        """Testing the GET review-requests/?to-users=&counts-only=1 API"""
        rsp = self.apiGet(self.get_list_url(), {
            'to-users': 'grumpy',
            'counts-only': 1,
        })
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(rsp['count'],
                         ReviewRequest.objects.to_user("grumpy").count())

    def test_get_reviewrequests_with_to_users_directly(self):
        """Testing the GET review-requests/?to-users-directly= API"""
        rsp = self.apiGet(self.get_list_url(), {
            'to-users-directly': 'doc',
        })
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['review_requests']),
                         ReviewRequest.objects.to_user_directly("doc").count())

    def test_get_reviewrequests_with_to_users_directly_and_status(self):
        """Testing the GET review-requests/?to-users-directly=&status= API"""
        url = self.get_list_url()

        rsp = self.apiGet(url, {
            'status': 'submitted',
            'to-users-directly': 'doc'
        })
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['review_requests']),
            ReviewRequest.objects.to_user_directly("doc", status='S').count())

        rsp = self.apiGet(url, {
            'status': 'discarded',
            'to-users-directly': 'doc'
        })
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['review_requests']),
            ReviewRequest.objects.to_user_directly("doc", status='D').count())

    def test_get_reviewrequests_with_to_users_directly_and_counts_only(self):
        """Testing the GET review-requests/?to-users-directly=&counts-only=1 API"""
        rsp = self.apiGet(self.get_list_url(), {
            'to-users-directly': 'doc',
            'counts-only': 1,
        })
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(rsp['count'],
                         ReviewRequest.objects.to_user_directly("doc").count())

    def test_get_reviewrequests_with_from_user(self):
        """Testing the GET review-requests/?from-user= API"""
        rsp = self.apiGet(self.get_list_url(), {
            'from-user': 'grumpy',
        })
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['review_requests']),
                         ReviewRequest.objects.from_user("grumpy").count())

    def test_get_reviewrequests_with_from_user_and_status(self):
        """Testing the GET review-requests/?from-user=&status= API"""
        url = self.get_list_url()

        rsp = self.apiGet(url, {
            'status': 'submitted',
            'from-user': 'grumpy',
        })
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['review_requests']),
            ReviewRequest.objects.from_user("grumpy", status='S').count())

        rsp = self.apiGet(url, {
            'status': 'discarded',
            'from-user': 'grumpy',
        })
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['review_requests']),
            ReviewRequest.objects.from_user("grumpy", status='D').count())

    def test_get_reviewrequests_with_from_user_and_counts_only(self):
        """Testing the GET review-requests/?from-user=&counts-only=1 API"""
        rsp = self.apiGet(self.get_list_url(), {
            'from-user': 'grumpy',
            'counts-only': 1,
        })
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(rsp['count'],
                         ReviewRequest.objects.from_user("grumpy").count())

    def test_get_reviewrequests_with_time_added_from(self):
        """Testing the GET review-requests/?time-added-from= API"""
        start_index = 3

        public_review_requests = \
            ReviewRequest.objects.public().order_by('time_added')
        r = public_review_requests[start_index]
        timestamp = r.time_added.isoformat()

        rsp = self.apiGet(self.get_list_url(), {
            'time-added-from': timestamp,
            'counts-only': 1,
        })
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(rsp['count'],
                         public_review_requests.count() - start_index)
        self.assertEqual(rsp['count'],
                         public_review_requests.filter(
                            time_added__gte=r.time_added).count())

    def test_get_reviewrequests_with_time_added_to(self):
        """Testing the GET review-requests/?time-added-to= API"""
        start_index = 3

        public_review_requests = \
            ReviewRequest.objects.public().order_by('time_added')
        r = public_review_requests[start_index]
        timestamp = r.time_added.isoformat()

        rsp = self.apiGet(self.get_list_url(), {
            'time-added-to': timestamp,
            'counts-only': 1,
        })
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(rsp['count'],
                         public_review_requests.count() - start_index + 1)
        self.assertEqual(rsp['count'],
                         public_review_requests.filter(
                             time_added__lt=r.time_added).count())

    def test_get_reviewrequests_with_last_updated_from(self):
        """Testing the GET review-requests/?last-updated-from= API"""
        start_index = 3

        public_review_requests = \
            ReviewRequest.objects.public().order_by('last_updated')
        r = public_review_requests[start_index]
        timestamp = r.last_updated.isoformat()

        rsp = self.apiGet(self.get_list_url(), {
            'last-updated-from': timestamp,
            'counts-only': 1,
        })
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(rsp['count'],
                         public_review_requests.count() - start_index)
        self.assertEqual(rsp['count'],
                         public_review_requests.filter(
                             last_updated__gte=r.last_updated).count())

    def test_get_reviewrequests_with_last_updated_to(self):
        """Testing the GET review-requests/?last-updated-to= API"""
        start_index = 3

        public_review_requests = \
            ReviewRequest.objects.public().order_by('last_updated')
        r = public_review_requests[start_index]
        timestamp = r.last_updated.isoformat()

        rsp = self.apiGet(self.get_list_url(), {
            'last-updated-to': timestamp,
            'counts-only': 1,
        })
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(rsp['count'],
                         public_review_requests.count() - start_index + 1)
        self.assertEqual(rsp['count'],
                         public_review_requests.filter(
                             last_updated__lt=r.last_updated).count())

    def test_post_reviewrequests(self):
        """Testing the POST review-requests/ API"""
        rsp = self.apiPost(self.get_list_url(), {
            'repository': self.repository.path,
        })
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(
            rsp['review_request']['links']['repository']['href'],
            self.base_url +
            RepositoryResourceTests.get_item_url(self.repository.id))

        # See if we can fetch this. Also return it for use in other
        # unit tests.
        return ReviewRequest.objects.get(pk=rsp['review_request']['id'])

    def test_post_reviewrequests_with_site(self):
        """Testing the POST review-requests/ API with a local site"""
        self.client.logout()
        self.client.login(username='doc', password='doc')

        repository = Repository.objects.filter(
            local_site__name=self.local_site_name)[0]

        rsp = self.apiPost(self.get_list_url(self.local_site_name),
                           { 'repository': repository.path, })
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(rsp['review_request']['links']['repository']['title'],
                         repository.name)

    def test_post_reviewrequests_with_site_no_access(self):
        """Testing the POST review-requests/ API with a local site and Permission Denied error"""
        repository = Repository.objects.filter(
            local_site__name=self.local_site_name)[0]

        self.apiPost(self.get_list_url(self.local_site_name),
                     { 'repository': repository.path, },
                     expected_status=403)

    def test_post_reviewrequests_with_site_invalid_repository_error(self):
        """Testing the POST review-requests/ API with a local site and Invalid Repository error"""
        self.client.logout()
        self.client.login(username='doc', password='doc')

        rsp = self.apiPost(self.get_list_url(self.local_site_name),
                           { 'repository': self.repository.path, },
                           expected_status=400)
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], INVALID_REPOSITORY.code)

    def test_post_reviewrequests_with_invalid_repository_error(self):
        """Testing the POST review-requests/ API with Invalid Repository error"""
        rsp = self.apiPost(self.get_list_url(), {
            'repository': 'gobbledygook',
        }, expected_status=400)
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], INVALID_REPOSITORY.code)

    def test_post_reviewrequests_with_no_site_invalid_repository_error(self):
        """Testing the POST review-requests/ API with Invalid Repository error from a site-local repository"""
        repository = Repository.objects.filter(
            local_site__name=self.local_site_name)[0]

        rsp = self.apiPost(self.get_list_url(), {
            'repository': repository.path,
        }, expected_status=400)
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], INVALID_REPOSITORY.code)

    def test_post_reviewrequests_with_submit_as(self):
        """Testing the POST review-requests/?submit_as= API"""
        self.user.is_superuser = True
        self.user.save()

        rsp = self.apiPost(self.get_list_url(), {
            'repository': self.repository.path,
            'submit_as': 'doc',
        })
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(
            rsp['review_request']['links']['repository']['href'],
            self.base_url +
            RepositoryResourceTests.get_item_url(self.repository.id))
        self.assertEqual(
            rsp['review_request']['links']['submitter']['href'],
            self.base_url +
            UserResourceTests.get_item_url('doc'))

        ReviewRequest.objects.get(pk=rsp['review_request']['id'])

    def test_post_reviewrequests_with_submit_as_and_permission_denied_error(self):
        """Testing the POST review-requests/?submit_as= API with Permission Denied error"""
        rsp = self.apiPost(self.get_list_url(), {
            'repository': self.repository.path,
            'submit_as': 'doc',
        }, expected_status=403)
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], PERMISSION_DENIED.code)

    def test_put_reviewrequest_status_discarded(self):
        """Testing the PUT review-requests/<id>/?status=discarded API"""
        r = ReviewRequest.objects.filter(public=True, status='P',
                                         submitter=self.user)[0]

        rsp = self.apiPut(self.get_item_url(r.display_id), {
            'status': 'discarded',
        })

        self.assertEqual(rsp['stat'], 'ok')

        r = ReviewRequest.objects.get(pk=r.id)
        self.assertEqual(r.status, 'D')

    def test_put_reviewrequest_status_pending(self):
        """Testing the PUT review-requests/<id>/?status=pending API"""
        r = ReviewRequest.objects.filter(public=True, status='P',
                                         submitter=self.user)[0]
        r.close(ReviewRequest.SUBMITTED)
        r.save()

        rsp = self.apiPut(self.get_item_url(r.display_id), {
            'status': 'pending',
        })

        self.assertEqual(rsp['stat'], 'ok')

        r = ReviewRequest.objects.get(pk=r.id)
        self.assertEqual(r.status, 'P')

    def test_put_reviewrequest_status_submitted(self):
        """Testing the PUT review-requests/<id>/?status=submitted API"""
        r = ReviewRequest.objects.filter(public=True, status='P',
                                         submitter=self.user)[0]

        rsp = self.apiPut(self.get_item_url(r.display_id), {
            'status': 'submitted',
        })

        self.assertEqual(rsp['stat'], 'ok')

        r = ReviewRequest.objects.get(pk=r.id)
        self.assertEqual(r.status, 'S')

    def test_put_reviewrequest_status_submitted_with_site(self):
        """Testing the PUT review-requests/<id>/?status=submitted API with a local site"""
        self.client.logout()
        self.client.login(username='doc', password='doc')

        r = ReviewRequest.objects.filter(public=True, status='P',
                                         submitter__username='doc',
                                         local_site__name=self.local_site_name)[0]

        rsp = self.apiPut(self.get_item_url(r.display_id,
                                            self.local_site_name),
                          { 'status': 'submitted' })
        self.assertEqual(rsp['stat'], 'ok')

        r = ReviewRequest.objects.get(pk=r.id)
        self.assertEqual(r.status, 'S')

    def test_put_reviewrequest_status_submitted_with_site_no_access(self):
        """Testing the PUT review-requests/<id>/?status=submitted API with a local site and Permission Denied error"""
        r = ReviewRequest.objects.filter(public=True, status='P',
                                         submitter__username='doc',
                                         local_site__name=self.local_site_name)[0]

        self.apiPut(self.get_item_url(r.display_id, self.local_site_name),
                    { 'status': 'submitted' },
                    expected_status=403)

    def test_get_reviewrequest(self):
        """Testing the GET review-requests/<id>/ API"""
        review_request = ReviewRequest.objects.public()[0]

        rsp = self.apiGet(self.get_item_url(review_request.display_id))
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(rsp['review_request']['id'], review_request.display_id)
        self.assertEqual(rsp['review_request']['summary'],
                         review_request.summary)

    def test_get_reviewrequest_with_site(self):
        """Testing the GET review-requests/<id>/ API with a local site"""
        self.client.logout()
        self.client.login(username='doc', password='doc')

        local_site = LocalSite.objects.get(name=self.local_site_name)
        review_request = ReviewRequest.objects.public(local_site=local_site)[0]

        rsp = self.apiGet(self.get_item_url(review_request.display_id,
                                            self.local_site_name))
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(rsp['review_request']['id'],
                         review_request.display_id)
        self.assertEqual(rsp['review_request']['summary'],
                         review_request.summary)

    def test_get_reviewrequest_with_site_no_access(self):
        """Testing the GET review-requests/<id>/ API with a local site and Permission Denied error"""
        local_site = LocalSite.objects.get(name=self.local_site_name)
        review_request = ReviewRequest.objects.public(local_site=local_site)[0]

        self.apiGet(self.get_item_url(review_request.display_id,
                                      self.local_site_name),
                    expected_status=403)

    def test_get_reviewrequest_with_non_public_and_permission_denied_error(self):
        """Testing the GET review-requests/<id>/ API with non-public and Permission Denied error"""
        review_request = ReviewRequest.objects.filter(public=False,
            local_site=None).exclude(submitter=self.user)[0]

        rsp = self.apiGet(self.get_item_url(review_request.display_id),
                          expected_status=403)
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], PERMISSION_DENIED.code)

    def test_get_reviewrequest_with_invite_only_group_and_permission_denied_error(self):
        """Testing the GET review-requests/<id>/ API with invite-only group and Permission Denied error"""
        review_request = ReviewRequest.objects.filter(public=True,
            local_site=None).exclude(submitter=self.user)[0]
        review_request.target_groups.clear()
        review_request.target_people.clear()

        group = Group(name='test-group', invite_only=True)
        group.save()

        review_request.target_groups.add(group)
        review_request.save()

        rsp = self.apiGet(self.get_item_url(review_request.display_id),
                          expected_status=403)
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], PERMISSION_DENIED.code)

    def test_get_reviewrequest_with_invite_only_group_and_target_user(self):
        """Testing the GET review-requests/<id>/ API with invite-only group and target user"""
        review_request = ReviewRequest.objects.filter(public=True,
            local_site=None).exclude(submitter=self.user)[0]
        review_request.target_groups.clear()
        review_request.target_people.clear()

        group = Group(name='test-group', invite_only=True)
        group.save()

        review_request.target_groups.add(group)
        review_request.target_people.add(self.user)
        review_request.save()

        rsp = self.apiGet(self.get_item_url(review_request.display_id))
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(rsp['review_request']['id'], review_request.display_id)
        self.assertEqual(rsp['review_request']['summary'],
                         review_request.summary)

    def test_get_reviewrequest_with_repository_and_changenum(self):
        """Testing the GET review-requests/?repository=&changenum= API"""
        review_request = \
            ReviewRequest.objects.filter(changenum__isnull=False)[0]

        rsp = self.apiGet(self.get_list_url(), {
            'repository': review_request.repository.id,
            'changenum': review_request.changenum,
        })
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['review_requests']), 1)
        self.assertEqual(rsp['review_requests'][0]['id'],
                         review_request.display_id)
        self.assertEqual(rsp['review_requests'][0]['summary'],
                         review_request.summary)
        self.assertEqual(rsp['review_requests'][0]['changenum'],
                         review_request.changenum)

    def test_delete_reviewrequest(self):
        """Testing the DELETE review-requests/<id>/ API"""
        self.user.user_permissions.add(
            Permission.objects.get(codename='delete_reviewrequest'))
        self.user.save()
        self.assert_(self.user.has_perm('reviews.delete_reviewrequest'))

        review_request = ReviewRequest.objects.from_user(self.user.username)[0]

        rsp = self.apiDelete(self.get_item_url(review_request.display_id))
        self.assertEqual(rsp, None)
        self.assertRaises(ReviewRequest.DoesNotExist,
                          ReviewRequest.objects.get,
                          pk=review_request.pk)

    def test_delete_reviewrequest_with_permission_denied_error(self):
        """Testing the DELETE review-requests/<id>/ API with Permission Denied error"""
        review_request = ReviewRequest.objects.filter(
            local_site=None).exclude(submitter=self.user)[0]

        rsp = self.apiDelete(self.get_item_url(review_request.display_id),
                             expected_status=403)
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], PERMISSION_DENIED.code)

    def test_delete_reviewrequest_with_does_not_exist_error(self):
        """Testing the DELETE review-requests/<id>/ API with Does Not Exist error"""
        self.user.user_permissions.add(
            Permission.objects.get(codename='delete_reviewrequest'))
        self.user.save()
        self.assert_(self.user.has_perm('reviews.delete_reviewrequest'))

        rsp = self.apiDelete(self.get_item_url(999), expected_status=404)
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], DOES_NOT_EXIST.code)

    def test_delete_reviewrequest_with_site(self):
        """Testing the DELETE review-requests/<id>/ API with a lotal site"""
        user = User.objects.get(username='doc')
        user.user_permissions.add(
            Permission.objects.get(codename='delete_reviewrequest'))
        user.save()

        self.client.logout()
        self.client.login(username='doc', password='doc')

        local_site = LocalSite.objects.get(name=self.local_site_name)
        review_request = ReviewRequest.objects.filter(local_site=local_site,
            submitter__username='doc')[0]

        rsp = self.apiDelete(self.get_item_url(review_request.display_id,
                                                self.local_site_name))
        self.assertEqual(rsp, None)
        self.assertRaises(ReviewRequest.DoesNotExist,
                          ReviewRequest.objects.get, pk=review_request.pk)

    @classmethod
    def get_list_url(cls, local_site_name=None):
        return local_site_reverse('review-requests-resource',
                                  local_site_name=local_site_name)

    def get_item_url(self, review_request_id, local_site_name=None):
        return local_site_reverse('review-request-resource',
                                  local_site_name=local_site_name,
                                  kwargs={
                                      'review_request_id': review_request_id,
                                  })


class ReviewRequestDraftResourceTests(BaseWebAPITestCase):
    """Testing the ReviewRequestDraftResource API tests."""

    def _create_update_review_request(self, apiFunc, expected_status,
                                      review_request=None,
                                      local_site_name=None):
        summary = "My Summary"
        description = "My Description"
        testing_done = "My Testing Done"
        branch = "My Branch"
        bugs = "#123,456"

        if review_request is None:
            review_request = \
                ReviewRequest.objects.from_user(self.user.username)[0]

        func_kwargs = {
            'summary': summary,
            'description': description,
            'testing_done': testing_done,
            'branch': branch,
            'bugs_closed': bugs,
        }

        rsp = apiFunc(self.get_url(review_request, local_site_name),
                      func_kwargs,
                      expected_status=expected_status)

        if expected_status >= 200 and expected_status < 300:
            self.assertEqual(rsp['stat'], 'ok')
            self.assertEqual(rsp['draft']['summary'], summary)
            self.assertEqual(rsp['draft']['description'], description)
            self.assertEqual(rsp['draft']['testing_done'], testing_done)
            self.assertEqual(rsp['draft']['branch'], branch)
            self.assertEqual(rsp['draft']['bugs_closed'], ['123', '456'])

            draft = ReviewRequestDraft.objects.get(pk=rsp['draft']['id'])
            self.assertEqual(draft.summary, summary)
            self.assertEqual(draft.description, description)
            self.assertEqual(draft.testing_done, testing_done)
            self.assertEqual(draft.branch, branch)
            self.assertEqual(draft.get_bug_list(), ['123', '456'])

        return rsp

    def _create_update_review_request_with_site(self, apiFunc, expected_status,
                                                relogin=True,
                                                review_request=None):
        if relogin:
            self.client.logout()
            self.client.login(username='doc', password='doc')

        if review_request is None:
            review_request = ReviewRequest.objects.from_user('doc',
                local_site=LocalSite.objects.get(name=self.local_site_name))[0]

        return self._create_update_review_request(
            apiFunc, expected_status, review_request, self.local_site_name)

    def test_put_reviewrequestdraft(self):
        """Testing the PUT review-requests/<id>/draft/ API"""
        self._create_update_review_request(self.apiPut, 200)

    def test_put_reviewrequestdraft_with_site(self):
        """Testing the PUT review-requests/<id>/draft/ API with a local site"""
        self._create_update_review_request_with_site(self.apiPut, 200)

    def test_put_reviewrequestdraft_with_site_no_access(self):
        """Testing the PUT review-requests/<id>/draft/ API with a local site and Permission Denied error"""
        rsp = self._create_update_review_request_with_site(
            self.apiPut, 403, relogin=False)
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], PERMISSION_DENIED.code)

    def test_post_reviewrequestdraft(self):
        """Testing the POST review-requests/<id>/draft/ API"""
        self._create_update_review_request(self.apiPost, 201)

    def test_post_reviewrequestdraft_with_site(self):
        """Testing the POST review-requests/<id>/draft/ API with a local site"""
        self._create_update_review_request_with_site(self.apiPost, 201)

    def test_post_reviewrequestdraft_with_site_no_access(self):
        """Testing the POST review-requests/<id>/draft/ API with a local site and Permission Denied error"""
        rsp = self._create_update_review_request_with_site(
            self.apiPost, 403, relogin=False)
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], PERMISSION_DENIED.code)

    def test_put_reviewrequestdraft_with_changedesc(self):
        """Testing the PUT review-requests/<id>/draft/ API with a change description"""
        changedesc = 'This is a test change description.'
        review_request = ReviewRequest.objects.create(self.user,
                                                      self.repository)
        review_request.publish(self.user)

        rsp = self.apiPost(self.get_url(review_request), {
            'changedescription': changedesc,
        })

        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(rsp['draft']['changedescription'], changedesc)

        draft = ReviewRequestDraft.objects.get(pk=rsp['draft']['id'])
        self.assertNotEqual(draft.changedesc, None)
        self.assertEqual(draft.changedesc.text, changedesc)

    def test_put_reviewrequestdraft_with_invalid_field_name(self):
        """Testing the PUT review-requests/<id>/draft/ API with Invalid Form Data error"""
        review_request = ReviewRequest.objects.from_user(self.user.username)[0]

        rsp = self.apiPut(self.get_url(review_request), {
            'foobar': 'foo',
        }, 400)

        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], INVALID_FORM_DATA.code)
        self.assertTrue('foobar' in rsp['fields'])

    def test_put_reviewrequestdraft_with_permission_denied_error(self):
        """Testing the PUT review-requests/<id>/draft/ API with Permission Denied error"""
        bugs_closed = '123,456'
        review_request = ReviewRequest.objects.from_user('admin')[0]

        rsp = self.apiPut(self.get_url(review_request), {
            'bugs_closed': bugs_closed,
        }, 403)

        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], PERMISSION_DENIED.code)

    def test_put_reviewrequestdraft_publish(self):
        """Testing the PUT review-requests/<id>/draft/?public=1 API"""
        # Set some data first.
        self.test_put_reviewrequestdraft()

        review_request = ReviewRequest.objects.from_user(self.user.username)[0]

        rsp = self.apiPut(self.get_url(review_request), {
            'public': True,
        })

        self.assertEqual(rsp['stat'], 'ok')

        review_request = ReviewRequest.objects.get(pk=review_request.id)
        self.assertEqual(review_request.summary, "My Summary")
        self.assertEqual(review_request.description, "My Description")
        self.assertEqual(review_request.testing_done, "My Testing Done")
        self.assertEqual(review_request.branch, "My Branch")
        self.assertTrue(review_request.public)

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "Review Request: My Summary")
        self.assertValidRecipients(["doc", "grumpy"], [])

    def test_put_reviewrequestdraft_publish_with_new_review_request(self):
        """Testing the PUT review-requests/<id>/draft/?public=1 API with a new review request"""
        # Set some data first.
        review_request = ReviewRequest.objects.create(self.user,
                                                      self.repository)
        review_request.target_people = [
            User.objects.get(username='doc')
        ]
        review_request.save()

        self._create_update_review_request(self.apiPut, 200, review_request)

        rsp = self.apiPut(self.get_url(review_request), {
            'public': True,
        })

        self.assertEqual(rsp['stat'], 'ok')

        review_request = ReviewRequest.objects.get(pk=review_request.id)
        self.assertEqual(review_request.summary, "My Summary")
        self.assertEqual(review_request.description, "My Description")
        self.assertEqual(review_request.testing_done, "My Testing Done")
        self.assertEqual(review_request.branch, "My Branch")
        self.assertTrue(review_request.public)

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "Review Request: My Summary")
        self.assertValidRecipients(["doc", "grumpy"], [])

    def test_delete_reviewrequestdraft(self):
        """Testing the DELETE review-requests/<id>/draft/ API"""
        review_request = ReviewRequest.objects.from_user(self.user.username)[0]
        summary = review_request.summary
        description = review_request.description

        # Set some data.
        self.test_put_reviewrequestdraft()

        self.apiDelete(self.get_url(review_request))

        review_request = ReviewRequest.objects.get(pk=review_request.id)
        self.assertEqual(review_request.summary, summary)
        self.assertEqual(review_request.description, description)

    def test_delete_reviewrequestdraft_with_site(self):
        """Testing the DELETE review-requests/<id>/draft/ API with a local site"""
        review_request = ReviewRequest.objects.from_user('doc',
            local_site=LocalSite.objects.get(name=self.local_site_name))[0]
        summary = review_request.summary
        description = review_request.description

        self.test_put_reviewrequestdraft_with_site()

        self.apiDelete(self.get_url(review_request, self.local_site_name))

        review_request = ReviewRequest.objects.get(pk=review_request.id)
        self.assertEqual(review_request.summary, summary)
        self.assertEqual(review_request.description, description)

    def test_delete_reviewrequestdraft_with_site_no_access(self):
        """Testing the DELETE review-requests/<id>/draft/ API with a local site and Permission Denied error"""
        review_request = ReviewRequest.objects.from_user('doc',
            local_site=LocalSite.objects.get(name=self.local_site_name))[0]
        rsp = self.apiDelete(
            self.get_url(review_request, self.local_site_name),
            expected_status=403)
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], PERMISSION_DENIED.code)

    def get_url(self, review_request, local_site_name=None):
        return local_site_reverse(
            'draft-resource',
            local_site_name=local_site_name,
            kwargs={
                'review_request_id': review_request.display_id,
            })


class ReviewResourceTests(BaseWebAPITestCase):
    """Testing the ReviewResource APIs."""

    def test_get_reviews(self):
        """Testing the GET review-requests/<id>/reviews/ API"""
        review_request = Review.objects.filter()[0].review_request
        rsp = self.apiGet(self.get_list_url(review_request))
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['reviews']), review_request.reviews.count())

    def test_get_reviews_with_site(self):
        """Testing the GET review-requests/<id>/reviews/ API with a local site"""
        self.test_post_reviews_with_site(public=True)

        local_site = LocalSite.objects.get(name=self.local_site_name)
        review_request = ReviewRequest.objects.public(local_site=local_site)[0]

        rsp = self.apiGet(self.get_list_url(review_request,
                                            self.local_site_name))
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['reviews']), review_request.reviews.count())

    def test_get_reviews_with_site_no_access(self):
        """Testing the GET review-requests/<id>/reviews/ API with a local site and Permission Denied error"""
        local_site = LocalSite.objects.get(name=self.local_site_name)
        review_request = ReviewRequest.objects.public(local_site=local_site)[0]
        rsp = self.apiGet(self.get_list_url(review_request,
                                            self.local_site_name),
                          expected_status=403)

        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], PERMISSION_DENIED.code)

    def test_get_reviews_with_counts_only(self):
        """Testing the GET review-requests/<id>/reviews/?counts-only=1 API"""
        review_request = Review.objects.all()[0].review_request
        rsp = self.apiGet(self.get_list_url(review_request), {
            'counts-only': 1,
        })
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(rsp['count'], review_request.reviews.count())

    def test_post_reviews(self):
        """Testing the POST review-requests/<id>/reviews/ API"""
        body_top = ""
        body_bottom = "My Body Bottom"
        ship_it = True

        # Clear out any reviews on the first review request we find.
        review_request = ReviewRequest.objects.public(local_site=None)[0]
        review_request.reviews = []
        review_request.save()

        rsp, response = self.api_post_with_response(
            self.get_list_url(review_request),
            {
                'ship_it': ship_it,
                'body_top': body_top,
                'body_bottom': body_bottom,
            })

        self.assertTrue('stat' in rsp)
        self.assertEqual(rsp['stat'], 'ok')
        self.assertTrue('Location' in response)

        reviews = review_request.reviews.filter(user=self.user)
        self.assertEqual(len(reviews), 1)
        review = reviews[0]

        self.assertEqual(response['Location'],
                         self.base_url +
                         self.get_item_url(review_request, review.id))

        self.assertEqual(review.ship_it, ship_it)
        self.assertEqual(review.body_top, body_top)
        self.assertEqual(review.body_bottom, body_bottom)
        self.assertEqual(review.public, False)

        self.assertEqual(len(mail.outbox), 0)

    def test_post_reviews_with_site(self, public=False):
        """Testing the POST review-requests/<id>/reviews/ API with a local site"""
        self.client.logout()
        self.client.login(username='doc', password='doc')

        body_top = ""
        body_bottom = "My Body Bottom"
        ship_it = True

        local_site = LocalSite.objects.get(name=self.local_site_name)

        # Clear out any reviews on the first review request we find.
        review_request = ReviewRequest.objects.public(local_site=local_site)[0]
        review_request.reviews = []
        review_request.save()

        post_data = {
            'ship_it': ship_it,
            'body_top': body_top,
            'body_bottom': body_bottom,
            'public': public,
        }

        rsp, response = self.api_post_with_response(
            self.get_list_url(review_request, self.local_site_name),
            post_data)

        self.assertTrue('stat' in rsp)
        self.assertEqual(rsp['stat'], 'ok')
        self.assertTrue('Location' in response)

        reviews = review_request.reviews.all()
        self.assertEqual(len(reviews), 1)
        review = reviews[0]

        self.assertEqual(rsp['review']['id'], review.id)

        self.assertEqual(review.ship_it, ship_it)
        self.assertEqual(review.body_top, body_top)
        self.assertEqual(review.body_bottom, body_bottom)
        self.assertEqual(review.public, public)

        if public:
            self.assertEqual(len(mail.outbox), 1)
        else:
            self.assertEqual(len(mail.outbox), 0)

    def test_post_reviews_with_site_no_access(self):
        """Testing the POST review-requests/<id>/reviews/ API with a local site and Permission Denied error"""
        local_site = LocalSite.objects.get(name=self.local_site_name)
        review_request = ReviewRequest.objects.public(local_site=local_site)[0]

        rsp = self.apiPost(self.get_list_url(review_request,
                                             self.local_site_name),
                           expected_status=403)
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], PERMISSION_DENIED.code)

    def test_put_review(self):
        """Testing the PUT review-requests/<id>/reviews/<id>/ API"""
        body_top = ""
        body_bottom = "My Body Bottom"
        ship_it = True

        # Clear out any reviews on the first review request we find.
        review_request = ReviewRequest.objects.public(local_site=None)[0]
        review_request.reviews = []
        review_request.save()

        rsp, response = self.api_post_with_response(
            self.get_list_url(review_request))

        self.assertTrue('stat' in rsp)
        self.assertEqual(rsp['stat'], 'ok')
        self.assertTrue('Location' in response)

        review_url = response['Location']

        rsp = self.apiPut(review_url, {
            'ship_it': ship_it,
            'body_top': body_top,
            'body_bottom': body_bottom,
        })

        reviews = review_request.reviews.filter(user=self.user)
        self.assertEqual(len(reviews), 1)
        review = reviews[0]

        self.assertEqual(review.ship_it, ship_it)
        self.assertEqual(review.body_top, body_top)
        self.assertEqual(review.body_bottom, body_bottom)
        self.assertEqual(review.public, False)

        self.assertEqual(len(mail.outbox), 0)

        # Make this easy to use in other tests.
        return review

    def test_put_review_with_site(self):
        """Testing the PUT review-requests/<id>/reviews/<id>/ API with a local site"""
        self.client.logout()
        self.client.login(username='doc', password='doc')

        body_top = ""
        body_bottom = "My Body Bottom"
        ship_it = True

        # Clear out any reviews on the first review request we find.
        local_site = LocalSite.objects.get(name=self.local_site_name)
        review_request = ReviewRequest.objects.public(local_site=local_site)[0]
        review_request.reviews = []
        review_request.save()

        rsp, response = self.api_post_with_response(
            self.get_list_url(review_request, self.local_site_name))

        self.assertTrue('stat' in rsp)
        self.assertEqual(rsp['stat'], 'ok')
        self.assertTrue('Location' in response)

        review_url = response['Location']

        rsp = self.apiPut(review_url, {
            'ship_it': ship_it,
            'body_top': body_top,
            'body_bottom': body_bottom,
        })

        reviews = review_request.reviews.filter(user__username='doc')
        self.assertEqual(len(reviews), 1)
        review = reviews[0]

        self.assertEqual(review.ship_it, ship_it)
        self.assertEqual(review.body_top, body_top)
        self.assertEqual(review.body_bottom, body_bottom)
        self.assertEqual(review.public, False)

        self.assertEqual(len(mail.outbox), 0)

        # Make this easy to use in other tests.
        return review

    def test_put_review_with_site_no_access(self):
        """Testing the PUT review-requests/<id>/reviews/<id>/ API with a local site and Permission Denied error"""
        local_site = LocalSite.objects.get(name=self.local_site_name)
        review_request = ReviewRequest.objects.public(local_site=local_site)[0]
        review = Review()
        review.review_request = review_request
        review.user = User.objects.get(username='doc')
        review.save()

        rsp = self.apiPut(self.get_item_url(review_request, review.id,
                                            self.local_site_name),
                          { 'ship_it': True, },
                          expected_status=403)
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], PERMISSION_DENIED.code)

    def test_put_review_with_published_review(self):
        """Testing the PUT review-requests/<id>/reviews/<id>/ API with pre-published review"""
        review = Review.objects.filter(user=self.user, public=True,
                                       base_reply_to__isnull=True)[0]

        self.apiPut(self.get_item_url(review.review_request, review.id), {
            'ship_it': True,
        }, expected_status=403)

    def test_put_review_publish(self):
        """Testing the PUT review-requests/<id>/reviews/<id>/?public=1 API"""
        body_top = "My Body Top"
        body_bottom = ""
        ship_it = True

        # Clear out any reviews on the first review request we find.
        review_request = ReviewRequest.objects.public()[0]
        review_request.reviews = []
        review_request.save()

        rsp, response = \
            self.api_post_with_response(self.get_list_url(review_request))

        self.assertTrue('stat' in rsp)
        self.assertEqual(rsp['stat'], 'ok')
        self.assertTrue('Location' in response)

        review_url = response['Location']

        rsp = self.apiPut(review_url, {
            'public': True,
            'ship_it': ship_it,
            'body_top': body_top,
            'body_bottom': body_bottom,
        })

        reviews = review_request.reviews.filter(user=self.user)
        self.assertEqual(len(reviews), 1)
        review = reviews[0]

        self.assertEqual(review.ship_it, ship_it)
        self.assertEqual(review.body_top, body_top)
        self.assertEqual(review.body_bottom, body_bottom)
        self.assertEqual(review.public, True)

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject,
                         "Re: Review Request: Interdiff Revision Test")
        self.assertValidRecipients(["admin", "grumpy"], [])

    def test_delete_review(self):
        """Testing the DELETE review-requests/<id>/reviews/<id>/ API"""
        # Set up the draft to delete.
        review = self.test_put_review()
        review_request = review.review_request

        self.apiDelete(self.get_item_url(review_request, review.id))
        self.assertEqual(review_request.reviews.count(), 0)

    def test_delete_review_with_permission_denied(self):
        """Testing the DELETE review-requests/<id>/reviews/<id>/ API with Permission Denied error"""
        # Set up the draft to delete.
        review = self.test_put_review()
        review.user = User.objects.get(username='doc')
        review.save()

        review_request = review.review_request
        old_count = review_request.reviews.count()

        self.apiDelete(self.get_item_url(review_request, review.id),
                       expected_status=403)
        self.assertEqual(review_request.reviews.count(), old_count)

    def test_delete_review_with_published_review(self):
        """Testing the DELETE review-requests/<id>/reviews/<id>/ API with pre-published review"""
        review = Review.objects.filter(user=self.user, public=True,
                                       base_reply_to__isnull=True)[0]
        review_request = review.review_request
        old_count = review_request.reviews.count()

        self.apiDelete(self.get_item_url(review_request, review.id),
                       expected_status=403)
        self.assertEqual(review_request.reviews.count(), old_count)

    def test_delete_review_with_does_not_exist(self):
        """Testing the DELETE review-requests/<id>/reviews/<id>/ API with Does Not Exist error"""
        review_request = ReviewRequest.objects.public()[0]
        rsp = self.apiDelete(self.get_item_url(review_request, 919239),
                             expected_status=404)
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], DOES_NOT_EXIST.code)

    def test_delete_review_with_local_site(self):
        """Testing the DELETE review-requests/<id>/reviews/<id>/ API with a local site"""
        review = self.test_put_review_with_site()

        local_site = LocalSite.objects.get(name=self.local_site_name)
        review_request = ReviewRequest.objects.public(local_site=local_site)[0]

        self.apiDelete(self.get_item_url(review_request, review.id,
                                          self.local_site_name))
        self.assertEqual(review_request.reviews.count(), 0)

    def test_delete_review_with_local_site_no_access(self):
        """Testing the DELETE review-requests/<id>/reviews/<id>/ API with a local site and Permission Denied error"""
        local_site = LocalSite.objects.get(name=self.local_site_name)
        review_request = ReviewRequest.objects.public(local_site=local_site)[0]
        review = Review()
        review.review_request = review_request
        review.user = User.objects.get(username='doc')
        review.save()

        rsp = self.apiDelete(self.get_item_url(review_request, review.id,
                                                self.local_site_name),
                             expected_status=403)
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], PERMISSION_DENIED.code)

    @classmethod
    def get_list_url(cls, review_request, local_site_name=None):
        return local_site_reverse(
            'reviews-resource',
            local_site_name=local_site_name,
            kwargs={
                'review_request_id': review_request.display_id,
            })

    def get_item_url(self, review_request, review_id, local_site_name=None):
        return local_site_reverse(
            'review-resource',
            local_site_name=local_site_name,
            kwargs={
                'review_request_id': review_request.display_id,
                'review_id': review_id,
            })


class ReviewCommentResourceTests(BaseWebAPITestCase):
    """Testing the ReviewCommentResource APIs."""
    def test_get_diff_comments(self):
        """Testing the GET review-requests/<id>/reviews/<id>/diff-comments/ API"""
        review = Review.objects.filter(comments__pk__gt=0)[0]

        rsp = self.apiGet(self.get_list_url(review))
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['diff_comments']), review.comments.count())

    def test_get_diff_comments_with_counts_only(self):
        """Testing the GET review-requests/<id>/reviews/<id>/diff-comments/?counts-only=1 API"""
        review = Review.objects.filter(comments__pk__gt=0)[0]

        rsp = self.apiGet(self.get_list_url(review), {
            'counts-only': 1,
        })
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(rsp['count'], review.comments.count())

    def test_get_diff_comments_with_site(self):
        """Testing the GET review-requests/<id>/reviews/<id>/diff-comments/ API with a local site"""
        review_id = self.test_post_diff_comments_with_site()
        review = Review.objects.get(pk=review_id)
        review_request = ReviewRequest.objects.filter(
            local_site__name=self.local_site_name)[0]

        rsp = self.apiGet(self.get_list_url(review, self.local_site_name))
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['diff_comments']), review.comments.count())

    def test_get_diff_comments_with_site_no_access(self):
        """Testing the GET review-requests/<id>/reviews/<id>/diff-comments/ API with a local site and Permission Denied error"""
        review_id = self.test_post_diff_comments_with_site()
        review = Review.objects.get(pk=review_id)
        review_request = ReviewRequest.objects.filter(
            local_site__name=self.local_site_name)[0]

        self.client.logout()
        self.client.login(username='grumpy', password='grumpy')

        rsp = self.apiGet(self.get_list_url(review, self.local_site_name),
                          expected_status=403)
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], PERMISSION_DENIED.code)

    def test_post_diff_comments(self):
        """Testing the POST review-requests/<id>/reviews/<id>/diff-comments/ API"""
        diff_comment_text = "Test diff comment"

        # Post the review request
        rsp = self._postNewReviewRequest()
        review_request = ReviewRequest.objects.get(
            pk=rsp['review_request']['id'])

        # Post the diff.
        rsp = self._postNewDiff(review_request)
        DiffSet.objects.get(pk=rsp['diff']['id'])

        # Make these public.
        review_request.publish(self.user)

        rsp = self.apiPost(ReviewResourceTests.get_list_url(review_request))
        self.assertEqual(rsp['stat'], 'ok')
        self.assertTrue('review' in rsp)
        review_id = rsp['review']['id']

        self._postNewDiffComment(review_request, review_id, diff_comment_text)
        review = Review.objects.get(pk=review_id)

        rsp = self.apiGet(self.get_list_url(review))
        self.assertEqual(rsp['stat'], 'ok')
        self.assertTrue('diff_comments' in rsp)
        self.assertEqual(len(rsp['diff_comments']), 1)
        self.assertEqual(rsp['diff_comments'][0]['text'], diff_comment_text)

    def test_post_diff_comments_with_site(self):
        """Testing the POST review-requests/<id>/reviews/<id>/diff-comments/ API with a local site"""
        diff_comment_text = "Test diff comment"
        review_request = ReviewRequest.objects.filter(
            local_site__name=self.local_site_name)[0]

        self.client.logout()
        self.client.login(username='doc', password='doc')

        rsp = self.apiPost(
            ReviewResourceTests.get_list_url(review_request,
                                             self.local_site_name))
        self.assertEqual(rsp['stat'], 'ok')
        self.assertTrue('review' in rsp)
        review_id = rsp['review']['id']

        self._postNewDiffComment(review_request, review_id, diff_comment_text)
        review = Review.objects.get(pk=review_id)

        rsp = self.apiGet(self.get_list_url(review, self.local_site_name))
        self.assertEqual(rsp['stat'], 'ok')
        self.assertTrue('diff_comments' in rsp)
        self.assertEqual(len(rsp['diff_comments']), 1)
        self.assertEqual(rsp['diff_comments'][0]['text'], diff_comment_text)

        return review_id

    def test_post_diff_comments_with_site_no_access(self):
        """Testing the POST review-requests/<id>/reviews/<id>/diff-comments/ API with a local site and Permission Denied error"""
        review_request = ReviewRequest.objects.filter(
            local_site__name=self.local_site_name)[0]

        review = Review()
        review.review_request = review_request
        review.user = User.objects.get(username='doc')
        review.save()

        rsp = self.apiPost(self.get_list_url(review, self.local_site_name),
                           {},
                           expected_status=403)
        self.assertEqual(rsp['stat'], 'fail')

    def test_post_diff_comments_with_interdiff(self):
        """Testing the POST review-requests/<id>/reviews/<id>/diff-comments/ API with interdiff"""
        comment_text = "Test diff comment"

        rsp, review_request_id, review_id, interdiff_revision = \
            self._common_post_interdiff_comments(comment_text)

        review_request = ReviewRequest.objects.get(pk=review_request_id)
        review = Review.objects.get(pk=review_id)

        rsp = self.apiGet(self.get_list_url(review))
        self.assertEqual(rsp['stat'], 'ok')
        self.assertTrue('diff_comments' in rsp)
        self.assertEqual(len(rsp['diff_comments']), 1)
        self.assertEqual(rsp['diff_comments'][0]['text'], comment_text)

    def test_get_diff_comments_with_interdiff(self):
        """Testing the GET review-requests/<id>/reviews/<id>/diff-comments/ API with interdiff"""
        comment_text = "Test diff comment"

        rsp, review_request_id, review_id, interdiff_revision = \
            self._common_post_interdiff_comments(comment_text)

        review_request = ReviewRequest.objects.get(pk=review_request_id)
        review = Review.objects.get(pk=review_id)

        rsp = self.apiGet(self.get_list_url(review), {
            'interdiff-revision': interdiff_revision,
        })
        self.assertEqual(rsp['stat'], 'ok')
        self.assertTrue('diff_comments' in rsp)
        self.assertEqual(len(rsp['diff_comments']), 1)
        self.assertEqual(rsp['diff_comments'][0]['text'], comment_text)

    def test_delete_diff_comment_with_interdiff(self):
        """Testing the DELETE review-requests/<id>/reviews/<id>/diff-comments/<id>/ API"""
        comment_text = "This is a test comment."

        rsp, review_request_id, review_id, interdiff_revision = \
            self._common_post_interdiff_comments(comment_text)

        rsp = self.apiDelete(rsp['diff_comment']['links']['self']['href'])

        review_request = ReviewRequest.objects.get(pk=review_request_id)
        review = Review.objects.get(pk=review_id)

        rsp = self.apiGet(self.get_list_url(review))
        self.assertEqual(rsp['stat'], 'ok')
        self.assertTrue('diff_comments' in rsp)
        self.assertEqual(len(rsp['diff_comments']), 0)

    def test_delete_diff_comment_with_site(self):
        """Testing the DELETE review-requests/<id>/reviews/<id>/diff-comments/<id>/ API with a local site"""
        review_id = self.test_post_diff_comments_with_site()
        review = Review.objects.get(pk=review_id)
        review_request = review.review_request
        comment = review.comments.all()[0]
        comment_count = review.comments.count()

        self.apiDelete(self.get_item_url(review, comment.id,
                                         self.local_site_name))

        self.assertEqual(review.comments.count(), comment_count - 1)

    def test_delete_diff_comment_with_site_no_access(self):
        """Testing the DELETE review-requests/<id>/reviews/<id>/diff-comments/<id>/ API with a local site and Permission Denied error"""
        review_id = self.test_post_diff_comments_with_site()
        review = Review.objects.get(pk=review_id)
        review_request = review.review_request
        comment = review.comments.all()[0]

        self.client.logout()
        self.client.login(username='grumpy', password='grumpy')

        rsp = self.apiDelete(
            self.get_item_url(review, comment.id, self.local_site_name),
            expected_status=403)
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], PERMISSION_DENIED.code)

    def _common_post_interdiff_comments(self, comment_text):
        # Post the review request
        rsp = self._postNewReviewRequest()
        review_request = ReviewRequest.objects.get(
            pk=rsp['review_request']['id'])

        # Post the diff.
        rsp = self._postNewDiff(review_request)
        review_request.publish(self.user)
        diffset = DiffSet.objects.get(pk=rsp['diff']['id'])
        filediff = diffset.files.all()[0]

        # Post the second diff.
        rsp = self._postNewDiff(review_request)
        review_request.publish(self.user)
        interdiffset = DiffSet.objects.get(pk=rsp['diff']['id'])
        interfilediff = interdiffset.files.all()[0]

        rsp = self.apiPost(ReviewResourceTests.get_list_url(review_request))
        self.assertEqual(rsp['stat'], 'ok')
        self.assertTrue('review' in rsp)
        review_id = rsp['review']['id']

        rsp = self._postNewDiffComment(review_request, review_id,
                                       comment_text,
                                       filediff_id=filediff.id,
                                       interfilediff_id=interfilediff.id)

        return rsp, review_request.id, review_id, interdiffset.revision

    @classmethod
    def get_list_url(cls, review, local_site_name=None):
        return local_site_reverse(
            'diff-comments-resource',
            local_site_name=local_site_name,
            kwargs={
                'review_request_id': review.review_request.display_id,
                'review_id': review.pk,
            })

    def get_item_url(self, review, comment_id, local_site_name=None):
        return local_site_reverse(
            'diff-comment-resource',
            local_site_name=local_site_name,
            kwargs={
                'review_request_id': review.review_request.display_id,
                'review_id': review.pk,
                'comment_id': comment_id,
            })


class DraftReviewScreenshotCommentResourceTests(BaseWebAPITestCase):
    """Testing the ReviewScreenshotCommentResource APIs."""
    def test_get_review_screenshot_comments(self):
        """Testing the GET review-requests/<id>/reviews/draft/screenshot-comments/ API"""
        screenshot_comment_text = "Test screenshot comment"
        x, y, w, h = 2, 2, 10, 10

        # Post the review request
        rsp = self._postNewReviewRequest()
        review_request = ReviewRequest.objects.get(
            pk=rsp['review_request']['id'])

        # Post the screenshot.
        rsp = self._postNewScreenshot(review_request)
        screenshot = Screenshot.objects.get(pk=rsp['screenshot']['id'])

        # Make these public.
        review_request.publish(self.user)

        rsp = self.apiPost(ReviewResourceTests.get_list_url(review_request))
        self.assertEqual(rsp['stat'], 'ok')
        self.assertTrue('review' in rsp)
        review_id = rsp['review']['id']
        review = Review.objects.get(pk=review_id)

        self._postNewScreenshotComment(review_request, review_id, screenshot,
                                       screenshot_comment_text, x, y, w, h)

        rsp = self.apiGet(self.get_list_url(review))
        self.assertEqual(rsp['stat'], 'ok')
        self.assertTrue('screenshot_comments' in rsp)
        self.assertEqual(len(rsp['screenshot_comments']), 1)
        self.assertEqual(rsp['screenshot_comments'][0]['text'],
                         screenshot_comment_text)

    def test_get_review_screenshot_comments_with_site(self):
        """Testing the GET review-requests/<id>/reviews/draft/screenshot-comments/ APIs with a local site"""
        screenshot_comment_text = "Test screenshot comment"
        x, y, w, h = 2, 2, 10, 10

        self.client.logout()
        self.client.login(username='doc', password='doc')

        review_request = ReviewRequest.objects.filter(
            local_site__name=self.local_site_name)[0]

        rsp = self._postNewScreenshot(review_request)
        screenshot = Screenshot.objects.get(pk=rsp['screenshot']['id'])
        review_request.publish(User.objects.get(username='doc'))

        rsp = self.apiPost(
            ReviewResourceTests.get_list_url(review_request,
                                             self.local_site_name))
        self.assertEqual(rsp['stat'], 'ok')
        self.assertTrue('review' in rsp)
        review_id = rsp['review']['id']
        review = Review.objects.get(pk=review_id)

        self._postNewScreenshotComment(review_request, review_id, screenshot,
                                       screenshot_comment_text, x, y, w, h)

        rsp = self.apiGet(self.get_list_url(review, self.local_site_name))
        self.assertEqual(rsp['stat'], 'ok')
        self.assertTrue('screenshot_comments' in rsp)
        self.assertEqual(len(rsp['screenshot_comments']), 1)
        self.assertEqual(rsp['screenshot_comments'][0]['text'],
                         screenshot_comment_text)

    @classmethod
    def get_list_url(self, review, local_site_name=None):
        return local_site_reverse(
            'screenshot-comments-resource',
            local_site_name=local_site_name,
            kwargs={
                'review_request_id': review.review_request.display_id,
                'review_id': review.pk,
            })

    def get_item_url(self, review, comment_id, local_site_name=None):
        return local_site_reverse(
            'screenshot-comment-resource',
            local_site_name=local_site_name,
            kwargs={
                'review_request_id': review.review_request.display_id,
                'review_id': review.pk,
                'comment_id': comment_id,
            })


class ReviewReplyResourceTests(BaseWebAPITestCase):
    """Testing the ReviewReplyResource APIs."""
    def test_get_replies(self):
        """Testing the GET review-requests/<id>/reviews/<id>/replies API"""
        review = \
            Review.objects.filter(base_reply_to__isnull=True, public=True)[0]
        self.test_put_reply()

        public_replies = review.public_replies()

        rsp = self.apiGet(self.get_list_url(review))
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['replies']), public_replies.count())

        for i in range(public_replies.count()):
            reply = public_replies[i]
            self.assertEqual(rsp['replies'][i]['id'], reply.id)
            self.assertEqual(rsp['replies'][i]['body_top'], reply.body_top)
            self.assertEqual(rsp['replies'][i]['body_bottom'],
                             reply.body_bottom)

    def test_get_replies_with_counts_only(self):
        """Testing the GET review-requests/<id>/reviews/<id>/replies/?counts-only=1 API"""
        review = \
            Review.objects.filter(base_reply_to__isnull=True, public=True)[0]
        self.test_put_reply()

        rsp = self.apiGet('%s?counts-only=1' % self.get_list_url(review))
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(rsp['count'], review.public_replies().count())

    def test_get_replies_with_site(self):
        """Testing the GET review-requests/<id>/reviews/<id>/replies/ API with a local site"""
        review_request = \
            ReviewRequest.objects.filter(local_site__name=self.local_site_name)[0]

        review = Review()
        review.review_request = review_request
        review.user = User.objects.get(username='doc')
        review.public = True
        review.save()

        reply = Review()
        reply.review_request = review_request
        reply.user = review.user
        reply.public = True
        reply.base_reply_to = review
        reply.save()

        self.client.logout()
        self.client.login(username='doc', password='doc')

        public_replies = review.public_replies()

        rsp = self.apiGet(self.get_list_url(review, self.local_site_name))
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['replies']), public_replies.count())

        for i in range(public_replies.count()):
            reply = public_replies[i]
            self.assertEqual(rsp['replies'][i]['id'], reply.id)
            self.assertEqual(rsp['replies'][i]['body_top'], reply.body_top)
            self.assertEqual(rsp['replies'][i]['body_bottom'],
                             reply.body_bottom)

    def test_get_replies_with_site_no_access(self):
        """Testing the GET review-requests/<id>/reviews/<id>/replies/ API with a local site and Permission Denied error"""
        review_request = \
            ReviewRequest.objects.filter(local_site__name=self.local_site_name)[0]

        review = Review()
        review.review_request = review_request
        review.user = User.objects.get(username='doc')
        review.public = True
        review.save()

        rsp = self.apiGet(self.get_list_url(review, self.local_site_name),
                          expected_status=403)
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], PERMISSION_DENIED.code)

    def test_post_replies(self):
        """Testing the POST review-requests/<id>/reviews/<id>/replies/ API"""
        review = \
            Review.objects.filter(base_reply_to__isnull=True, public=True)[0]

        rsp = self.apiPost(self.get_list_url(review), {
            'body_top': 'Test',
        })

        self.assertEqual(rsp['stat'], 'ok')

        self.assertEqual(len(mail.outbox), 0)

    def test_post_replies_with_site(self):
        """Testing the POST review-requsets/<id>/reviews/<id>/replies/ API with a local site"""
        review_request = \
            ReviewRequest.objects.filter(local_site__name=self.local_site_name)[0]

        review = Review()
        review.review_request = review_request
        review.user = User.objects.get(username='doc')
        review.public = True
        review.save()

        self.client.logout()
        self.client.login(username='doc', password='doc')

        rsp = self.apiPost(self.get_list_url(review, self.local_site_name),
                           { 'body_top': 'Test', })
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(mail.outbox), 0)

    def test_post_replies_with_site_no_access(self):
        """Testing the POST review-requests/<id>/reviews/<id>/replies/ API with a local site and Permission Denied error"""
        review_request = \
            ReviewRequest.objects.filter(local_site__name=self.local_site_name)[0]

        review = Review()
        review.review_request = review_request
        review.user = User.objects.get(username='doc')
        review.public = True
        review.save()

        rsp = self.apiPost(self.get_list_url(review, self.local_site_name),
                           { 'body_top': 'Test', },
                           expected_status=403)
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], PERMISSION_DENIED.code)

    def test_post_replies_with_body_top(self):
        """Testing the POST review-requests/<id>/reviews/<id>/replies/ API with body_top"""
        body_top = 'My Body Top'

        review = \
            Review.objects.filter(base_reply_to__isnull=True, public=True)[0]

        rsp = self.apiPost(self.get_list_url(review), {
            'body_top': body_top,
        })

        self.assertEqual(rsp['stat'], 'ok')

        reply = Review.objects.get(pk=rsp['reply']['id'])
        self.assertEqual(reply.body_top, body_top)

    def test_post_replies_with_body_bottom(self):
        """Testing the POST review-requests/<id>/reviews/<id>/replies/ API with body_bottom"""
        body_bottom = 'My Body Bottom'

        review = \
            Review.objects.filter(base_reply_to__isnull=True, public=True)[0]

        rsp = self.apiPost(self.get_list_url(review), {
            'body_bottom': body_bottom,
        })

        self.assertEqual(rsp['stat'], 'ok')

        reply = Review.objects.get(pk=rsp['reply']['id'])
        self.assertEqual(reply.body_bottom, body_bottom)

    def test_put_reply(self):
        """Testing the PUT review-requests/<id>/reviews/<id>/replies/<id>/ API"""
        review = \
            Review.objects.filter(base_reply_to__isnull=True, public=True)[0]

        rsp, response = self.api_post_with_response(self.get_list_url(review))

        self.assertTrue('Location' in response)
        self.assertTrue('stat' in rsp)
        self.assertEqual(rsp['stat'], 'ok')

        rsp = self.apiPut(response['Location'], {
            'body_top': 'Test',
        })

        self.assertEqual(rsp['stat'], 'ok')

    def test_put_reply_with_site(self):
        """Testing the PUT review-requests/<id>/reviews/<id>/replies/<id>/ API with a local site"""
        review_request = \
            ReviewRequest.objects.filter(local_site__name=self.local_site_name)[0]

        review = Review()
        review.review_request = review_request
        review.user = User.objects.get(username='doc')
        review.public = True
        review.save()

        self.client.logout()
        self.client.login(username='doc', password='doc')

        rsp, response = self.api_post_with_response(
            self.get_list_url(review, self.local_site_name))
        self.assertTrue('Location' in response)
        self.assertTrue('stat' in rsp)
        self.assertEqual(rsp['stat'], 'ok')

        rsp = self.apiPut(response['Location'],
                          { 'body_top': 'Test', })
        self.assertEqual(rsp['stat'], 'ok')

    def test_put_reply_with_site_no_access(self):
        """Testing the PUT review-requests/<id>/reviews/<id>/replies/<id>/ API with a local site and Permission Denied error"""
        review_request = \
            ReviewRequest.objects.filter(local_site__name=self.local_site_name)[0]

        review = Review()
        review.review_request = review_request
        review.user = User.objects.get(username='doc')
        review.public = True
        review.save()

        reply = Review()
        reply.review_request = review_request
        reply.user = review.user
        reply.public = True
        reply.base_reply_to = review
        reply.save()

        rsp = self.apiPut(self.get_item_url(review, reply.id,
                                            self.local_site_name),
                          expected_status=403)
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], PERMISSION_DENIED.code)

    def test_put_reply_publish(self):
        """Testing the PUT review-requests/<id>/reviews/<id>/replies/<id>/?public=1 API"""
        review = \
            Review.objects.filter(base_reply_to__isnull=True, public=True)[0]

        rsp, response = self.api_post_with_response(self.get_list_url(review))

        self.assertTrue('Location' in response)
        self.assertTrue('stat' in rsp)
        self.assertEqual(rsp['stat'], 'ok')

        rsp = self.apiPut(response['Location'], {
            'body_top': 'Test',
            'public': True,
        })

        self.assertEqual(rsp['stat'], 'ok')

        reply = Review.objects.get(pk=rsp['reply']['id'])
        self.assertEqual(reply.public, True)

        self.assertEqual(len(mail.outbox), 1)

    def test_delete_reply(self):
        """Testing the DELETE review-requests/<id>/reviews/<id>/replies/<id>/ API"""
        review = \
            Review.objects.filter(base_reply_to__isnull=True, public=True)[0]

        rsp = self.apiPost(self.get_list_url(review), {
            'body_top': 'Test',
        })

        self.assertEqual(rsp['stat'], 'ok')

        reply_id = rsp['reply']['id']
        rsp = self.apiDelete(rsp['reply']['links']['self']['href'])

        self.assertEqual(Review.objects.filter(pk=reply_id).count(), 0)

    def test_delete_reply_with_site(self):
        """Testing the DELETE review-requests/<id>/reviews/<id>/replies/<id>/ API with a local site"""
        review_request = \
            ReviewRequest.objects.filter(local_site__name=self.local_site_name)[0]

        review = Review()
        review.review_request = review_request
        review.user = User.objects.get(username='doc')
        review.public = True
        review.save()

        reply = Review()
        reply.review_request = review_request
        reply.user = review.user
        reply.public = False
        reply.base_reply_to = review
        reply.save()

        self.client.logout()
        self.client.login(username='doc', password='doc')

        self.apiDelete(self.get_item_url(review, reply.id,
                                         self.local_site_name))
        self.assertEqual(review.replies.count(), 0)

    def test_delete_reply_with_site_no_access(self):
        """Testing the DELETE review-requests/<id>/reviews/<id>/replies/<id>/ API with a local site and Permission Denied error"""
        review_request = \
            ReviewRequest.objects.filter(local_site__name=self.local_site_name)[0]

        review = Review()
        review.review_request = review_request
        review.user = User.objects.get(username='doc')
        review.public = True
        review.save()

        reply = Review()
        reply.review_request = review_request
        reply.user = review.user
        reply.public = False
        reply.base_reply_to = review
        reply.save()

        rsp = self.apiDelete(self.get_item_url(review, reply.id,
                                               self.local_site_name),
                             expected_status=403)
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], PERMISSION_DENIED.code)

    @classmethod
    def get_list_url(cls, review, local_site_name=None):
        return local_site_reverse(
            'replies-resource',
            local_site_name=local_site_name,
            kwargs={
                'review_request_id': review.review_request.display_id,
                'review_id': review.pk,
            })

    def get_item_url(self, review, reply_id, local_site_name=None):
        return local_site_reverse(
            'reply-resource',
            local_site_name=local_site_name,
            kwargs={
                'review_request_id': review.review_request.display_id,
                'review_id': review.pk,
                'reply_id': reply_id,
            })


class ReviewReplyDiffCommentResourceTests(BaseWebAPITestCase):
    """Testing the ReviewReplyDiffCommentResource APIs."""
    def test_post_reply_with_diff_comment(self):
        """Testing the POST review-requests/<id>/reviews/<id>/replies/<id>/diff-comments/ API"""
        comment_text = "My Comment Text"

        comment = Comment.objects.all()[0]
        review = comment.review.get()

        # Create the reply
        rsp = self.apiPost(ReviewReplyResourceTests.get_list_url(review))
        self.assertEqual(rsp['stat'], 'ok')

        self.assertTrue('reply' in rsp)
        self.assertNotEqual(rsp['reply'], None)
        self.assertTrue('links' in rsp['reply'])
        self.assertTrue('diff_comments' in rsp['reply']['links'])

        rsp = self.apiPost(rsp['reply']['links']['diff_comments']['href'], {
            'reply_to_id': comment.id,
            'text': comment_text,
        })
        self.assertEqual(rsp['stat'], 'ok')

        reply_comment = Comment.objects.get(pk=rsp['diff_comment']['id'])
        self.assertEqual(reply_comment.text, comment_text)

        return rsp

    def test_post_reply_with_diff_comment_and_local_site(self, badlogin=False):
        """Testing the POST review-requests/<id>/reviews/<id>/replies/<id>/diff-comments/ API with a local site"""
        comment_text = 'My Comment Text'

        review_request = ReviewRequest.objects.filter(
            local_site__name=self.local_site_name)[0]

        review = Review()
        review.review_request = review_request
        review.user = User.objects.get(username='doc')
        review.save()

        self.client.logout()
        self.client.login(username='doc', password='doc')

        rsp = self._postNewDiffComment(review_request, review.id, 'Comment')
        review = Review.objects.get(pk=review.id)
        review.public = True
        review.save()

        self.assertTrue('diff_comment' in rsp)
        self.assertTrue('id' in rsp['diff_comment'])
        comment_id = rsp['diff_comment']['id']

        rsp = self.apiPost(
            ReviewReplyResourceTests.get_list_url(review, self.local_site_name))
        self.assertEqual(rsp['stat'], 'ok')

        self.assertTrue('reply' in rsp)
        self.assertNotEqual(rsp['reply'], None)
        self.assertTrue('links' in rsp['reply'])
        self.assertTrue('diff_comments' in rsp['reply']['links'])

        post_data = {
            'reply_to_id': comment_id,
            'text': comment_text,
        }

        if badlogin:
            self.client.logout()
            self.client.login(username='grumpy', password='grumpy')
            rsp = self.apiPost(rsp['reply']['links']['diff_comments']['href'],
                               post_data,
                               expected_status=403)
            self.assertEqual(rsp['stat'], 'fail')
            self.assertEqual(rsp['err']['code'], PERMISSION_DENIED.code)
        else:
            rsp = self.apiPost(rsp['reply']['links']['diff_comments']['href'],
                               post_data)
            self.assertEqual(rsp['stat'], 'ok')

            reply_comment = Comment.objects.get(pk=rsp['diff_comment']['id'])
            self.assertEqual(reply_comment.text, comment_text)

            return rsp

    def test_post_reply_with_diff_comment_and_local_site_no_access(self):
        """Testing the POST review-requests/<id>/reviews/<id>/replies/<id>/diff-comments/ API with a local site and Permission Denied error"""
        self.test_post_reply_with_diff_comment_and_local_site(True)

    def test_put_reply_with_diff_comment(self):
        """Testing the PUT review-requests/<id>/reviews/<id>/replies/<id>/diff-comments/ API"""
        new_comment_text = 'My new comment text'

        # First, create a comment that we can update.
        rsp = self.test_post_reply_with_diff_comment()

        reply_comment = Comment.objects.get(pk=rsp['diff_comment']['id'])

        rsp = self.apiPut(rsp['diff_comment']['links']['self']['href'], {
            'text': new_comment_text,
        })
        self.assertEqual(rsp['stat'], 'ok')

        reply_comment = Comment.objects.get(pk=rsp['diff_comment']['id'])
        self.assertEqual(reply_comment.text, new_comment_text)

    def test_put_reply_with_diff_comment_and_local_site(self):
        """Testing the PUT review-requests/<id>/reviews/<id>/replies/<id>/diff-comments/ API with a local site"""
        new_comment_text = 'My new comment text'

        rsp = self.test_post_reply_with_diff_comment_and_local_site()

        reply_comment = Comment.objects.get(pk=rsp['diff_comment']['id'])

        rsp = self.apiPut(rsp['diff_comment']['links']['self']['href'],
                          { 'text': new_comment_text, })
        self.assertEqual(rsp['stat'], 'ok')

        reply_comment = Comment.objects.get(pk=rsp['diff_comment']['id'])
        self.assertEqual(reply_comment.text, new_comment_text)

    def test_put_reply_with_diff_comment_and_local_site_no_access(self):
        """Testing the PUT review-requests/<id>/reviews/<id>/replies/<id>/diff-comments/ API with a local site and Permission Denied error"""
        new_comment_text = 'My new comment text'

        rsp = self.test_post_reply_with_diff_comment_and_local_site()

        reply_comment = Comment.objects.get(pk=rsp['diff_comment']['id'])

        self.client.logout()
        self.client.login(username='grumpy', password='grumpy')

        rsp = self.apiPut(rsp['diff_comment']['links']['self']['href'],
                          { 'text': new_comment_text, },
                          expected_status=403)
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], PERMISSION_DENIED.code)


class ReviewReplyScreenshotCommentResourceTests(BaseWebAPITestCase):
    """Testing the ReviewReplyScreenshotCommentResource APIs."""
    def test_post_reply_with_screenshot_comment(self):
        """Testing the POST review-requests/<id>/reviews/<id>/replies/<id>/screenshot-comments/ API"""
        comment_text = "My Comment Text"
        x, y, w, h = 10, 10, 20, 20

        rsp = self._postNewReviewRequest()
        review_request = \
            ReviewRequest.objects.get(pk=rsp['review_request']['id'])

        rsp = self._postNewScreenshot(review_request)
        screenshot = Screenshot.objects.get(pk=rsp['screenshot']['id'])

        rsp = self._postNewReview(review_request)
        review = Review.objects.get(pk=rsp['review']['id'])
        replies_url = rsp['review']['links']['replies']['href']

        rsp = self._postNewScreenshotComment(review_request, review.id,
                                             screenshot, comment_text,
                                             x, y, w, h)

        self.assertTrue('screenshot_comment' in rsp)
        self.assertEqual(rsp['screenshot_comment']['text'], comment_text)
        self.assertEqual(rsp['screenshot_comment']['x'], x)
        self.assertEqual(rsp['screenshot_comment']['y'], y)
        self.assertEqual(rsp['screenshot_comment']['w'], w)
        self.assertEqual(rsp['screenshot_comment']['h'], h)

        comment = ScreenshotComment.objects.get(
            pk=rsp['screenshot_comment']['id'])

        rsp = self.apiPost(replies_url)
        self.assertEqual(rsp['stat'], 'ok')
        self.assertTrue('reply' in rsp)
        self.assertNotEqual(rsp['reply'], None)
        self.assertTrue('links' in rsp['reply'])
        self.assertTrue('screenshot_comments' in rsp['reply']['links'])

        screenshot_comments_url = \
            rsp['reply']['links']['screenshot_comments']['href']

        rsp = self.apiPost(screenshot_comments_url, {
            'reply_to_id': comment.id,
            'text': comment_text,
        })
        self.assertEqual(rsp['stat'], 'ok')

        reply_comment = ScreenshotComment.objects.get(
            pk=rsp['screenshot_comment']['id'])
        self.assertEqual(reply_comment.text, comment_text)
        self.assertEqual(reply_comment.reply_to, comment)

    def test_post_reply_with_screenshot_comment_and_local_site(self):
        """Testing the POST review-requests/<id>/reviews/<id>/replies/<id>/screenshot-comments/ API with a local site"""
        comment_text = "My Comment Text"
        x, y, w, h = 10, 10, 20, 20

        self.client.logout()
        self.client.login(username='doc', password='doc')

        review_request = ReviewRequest.objects.filter(
            local_site__name=self.local_site_name)[0]

        rsp = self._postNewScreenshot(review_request)
        screenshot = Screenshot.objects.get(pk=rsp['screenshot']['id'])

        rsp = self._postNewReview(review_request)
        review = Review.objects.get(pk=rsp['review']['id'])
        replies_url = rsp['review']['links']['replies']['href']

        rsp = self._postNewScreenshotComment(review_request, review.id,
                                             screenshot, comment_text,
                                             x, y, w, h)

        self.assertTrue('screenshot_comment' in rsp)
        self.assertEqual(rsp['screenshot_comment']['text'], comment_text)
        self.assertEqual(rsp['screenshot_comment']['x'], x)
        self.assertEqual(rsp['screenshot_comment']['y'], y)
        self.assertEqual(rsp['screenshot_comment']['w'], w)
        self.assertEqual(rsp['screenshot_comment']['h'], h)

        comment = ScreenshotComment.objects.get(
            pk=rsp['screenshot_comment']['id'])

        rsp = self.apiPost(replies_url)
        self.assertEqual(rsp['stat'], 'ok')
        self.assertTrue('reply' in rsp)
        self.assertNotEqual(rsp['reply'], None)
        self.assertTrue('links' in rsp['reply'])
        self.assertTrue('screenshot_comments' in rsp['reply']['links'])

        screenshot_comments_url = \
            rsp['reply']['links']['screenshot_comments']['href']

        post_data = {
            'reply_to_id': comment.id,
            'text': comment_text,
        }

        rsp = self.apiPost(screenshot_comments_url, post_data)
        self.assertEqual(rsp['stat'], 'ok')

        reply_comment = ScreenshotComment.objects.get(
            pk=rsp['screenshot_comment']['id'])
        self.assertEqual(reply_comment.text, comment_text)


class DiffResourceTests(BaseWebAPITestCase):
    """Testing the DiffResource APIs."""

    def test_post_diffs(self):
        """Testing the POST review-requests/<id>/diffs/ API"""
        rsp = self._postNewReviewRequest()
        self.assertEqual(rsp['stat'], 'ok')
        ReviewRequest.objects.get(pk=rsp['review_request']['id'])

        diff_filename = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "scmtools", "testdata", "svn_makefile.diff")
        f = open(diff_filename, "r")
        rsp = self.apiPost(rsp['review_request']['links']['diffs']['href'], {
            'path': f,
            'basedir': "/trunk",
        })
        f.close()

        self.assertEqual(rsp['stat'], 'ok')

    def test_post_diffs_with_missing_data(self):
        """Testing the POST review-requests/<id>/diffs/ API with Invalid Form Data"""
        rsp = self._postNewReviewRequest()
        self.assertEqual(rsp['stat'], 'ok')
        ReviewRequest.objects.get(pk=rsp['review_request']['id'])

        rsp = self.apiPost(rsp['review_request']['links']['diffs']['href'],
                           expected_status=400)
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], INVALID_FORM_DATA.code)
        self.assert_('path' in rsp['fields'])

        # Now test with a valid path and an invalid basedir.
        # This is necessary because basedir is "optional" as defined by
        # the resource, but may be required by the form that processes the
        # diff.
        rsp = self._postNewReviewRequest()
        self.assertEqual(rsp['stat'], 'ok')
        ReviewRequest.objects.get(pk=rsp['review_request']['id'])

        diff_filename = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "scmtools", "testdata", "svn_makefile.diff")
        f = open(diff_filename, "r")
        rsp = self.apiPost(rsp['review_request']['links']['diffs']['href'], {
            'path': f,
        }, expected_status=400)
        f.close()

        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], INVALID_FORM_DATA.code)
        self.assert_('basedir' in rsp['fields'])

    def test_post_diffs_with_site(self):
        """Testing the POST review-requests/<id>/diffs/ API with a local site"""
        self.client.logout()
        self.client.login(username='doc', password='doc')

        repo = Repository.objects.get(name='Review Board Git')
        rsp = self._postNewReviewRequest(local_site_name=self.local_site_name,
                                         repository=repo)

        self.assertEqual(rsp['stat'], 'ok')
        review_request = ReviewRequest.objects.get(
            local_id=rsp['review_request']['id'],
            local_site__name=self.local_site_name)

        diff_filename = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            'scmtools', 'testdata', 'git_deleted_file_indication.diff')
        f = open(diff_filename, 'r')
        rsp = self.apiPost(rsp['review_request']['links']['diffs']['href'],
                           { 'path': f, })
        f.close()

        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(rsp['diff']['name'],
                         'git_deleted_file_indication.diff')


    def test_get_diffs(self):
        """Testing the GET review-requests/<id>/diffs/ API"""
        review_request = ReviewRequest.objects.get(pk=2)
        rsp = self.apiGet(self.get_list_url(review_request))

        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(rsp['diffs'][0]['id'], 2)
        self.assertEqual(rsp['diffs'][0]['name'], 'cleaned_data.diff')

    def test_get_diffs_with_site(self):
        """Testing the GET review-requests/<id>/diffs API with a local site"""
        review_request = ReviewRequest.objects.filter(
            local_site__name=self.local_site_name)[0]
        self.client.logout()
        self.client.login(username='doc', password='doc')

        rsp = self.apiGet(self.get_list_url(review_request,
                                            self.local_site_name))
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(rsp['diffs'][0]['id'],
                         review_request.diffset_history.diffsets.latest().id)
        self.assertEqual(rsp['diffs'][0]['name'],
                         review_request.diffset_history.diffsets.latest().name)

    def test_get_diffs_with_site_no_access(self):
        """Testing the GET review-requests/<id>/diffs API with a local site and Permission Denied error"""
        review_request = ReviewRequest.objects.filter(
            local_site__name=self.local_site_name)[0]
        self.apiGet(self.get_list_url(review_request, self.local_site_name),
                    expected_status=403)

    def test_get_diff(self):
        """Testing the GET review-requests/<id>/diffs/<revision>/ API"""
        review_request = ReviewRequest.objects.get(pk=2)
        rsp = self.apiGet(self.get_item_url(review_request, 1))

        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(rsp['diff']['id'], 2)
        self.assertEqual(rsp['diff']['name'], 'cleaned_data.diff')

    def test_get_diff_with_site(self):
        """Testing the GET review-requests/<id>/diffs/<revision>/ API with a local site"""
        review_request = ReviewRequest.objects.filter(
            local_site__name=self.local_site_name)[0]
        diff = review_request.diffset_history.diffsets.latest()
        self.client.logout()
        self.client.login(username='doc', password='doc')

        rsp = self.apiGet(self.get_item_url(review_request, diff.revision,
                                            self.local_site_name))
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(rsp['diff']['id'], diff.id)
        self.assertEqual(rsp['diff']['name'], diff.name)

    def test_get_diff_with_site_no_access(self):
        """Testing the GET review-requests/<id>/diffs/<revision>/ API with a local site and Permission Denied error"""
        review_request = ReviewRequest.objects.filter(
            local_site__name=self.local_site_name)[0]
        diff = review_request.diffset_history.diffsets.latest()
        self.apiGet(self.get_item_url(review_request, diff.revision,
                                      self.local_site_name),
                    expected_status=403)

    @classmethod
    def get_list_url(cls, review_request, local_site_name=None):
        return local_site_reverse(
            'diffs-resource',
            local_site_name=local_site_name,
            kwargs={
                'review_request_id': review_request.display_id,
            })

    def get_item_url(self, review_request, diff_revision, local_site_name=None):
        return local_site_reverse(
            'diff-resource',
            local_site_name=local_site_name,
            kwargs={
                'review_request_id': review_request.display_id,
                'diff_revision': diff_revision,
            })


class ScreenshotDraftResourceTests(BaseWebAPITestCase):
    """Testing the ScreenshotDraftResource APIs."""
    def test_post_screenshots(self):
        """Testing the POST review-requests/<id>/draft/screenshots/ API"""
        rsp = self._postNewReviewRequest()
        self.assertEqual(rsp['stat'], 'ok')
        ReviewRequest.objects.get(pk=rsp['review_request']['id'])

        screenshots_url = rsp['review_request']['links']['screenshots']['href']

        f = open(self._getTrophyFilename(), "r")
        self.assertNotEqual(f, None)
        rsp = self.apiPost(screenshots_url, {
            'path': f,
        })
        f.close()

        self.assertEqual(rsp['stat'], 'ok')

    def test_post_screenshots_with_permission_denied_error(self):
        """Testing the POST review-requests/<id>/draft/screenshots/ API with Permission Denied error"""
        review_request = ReviewRequest.objects.filter(public=True,
            local_site=None).exclude(submitter=self.user)[0]

        f = open(self._getTrophyFilename(), "r")
        self.assert_(f)
        rsp = self.apiPost(self.get_list_url(review_request), {
            'caption': 'Trophy',
            'path': f,
        }, expected_status=403)
        f.close()

        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], PERMISSION_DENIED.code)

    def test_post_screenshots_with_site(self):
        """Testing the POST review-requests/<id>/draft/screenshots/ API with a local site"""
        self.client.logout()
        self.client.login(username='doc', password='doc')

        repo = Repository.objects.get(name='Review Board Git')
        rsp = self._postNewReviewRequest(local_site_name=self.local_site_name,
                                         repository=repo)
        self.assertEqual(rsp['stat'], 'ok')
        review_request = ReviewRequest.objects.get(
            local_site__name=self.local_site_name,
            local_id=rsp['review_request']['id'])

        f = open(self._getTrophyFilename(), 'r')
        self.assertNotEqual(f, None)

        post_data = {
            'path': f,
            'caption': 'Trophy',
        }

        rsp = self.apiPost(self.get_list_url(review_request,
                                             self.local_site_name),
                           post_data)
        f.close()

        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(rsp['draft_screenshot']['caption'], 'Trophy')

        draft = review_request.get_draft(User.objects.get(username='doc'))
        self.assertNotEqual(draft, None)

        return review_request, rsp['draft_screenshot']['id']

    def test_post_screenshots_with_site_no_access(self):
        """Testing the POST review-requests/<id>/draft/screenshots/ API with a local site and Permission Denied error"""
        review_request = ReviewRequest.objects.filter(
            local_site__name=self.local_site_name)[0]

        f = open(self._getTrophyFilename(), 'r')
        self.assertNotEqual(f, None)
        rsp = self.apiPost(self.get_list_url(review_request,
                                             self.local_site_name),
                           { 'path': f, },
                           expected_status=403)
        f.close()

        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], PERMISSION_DENIED.code)

    def test_put_screenshot(self):
        """Testing the PUT review-requests/<id>/draft/screenshots/<id>/ API"""
        draft_caption = 'The new caption'

        rsp = self._postNewReviewRequest()
        self.assertEqual(rsp['stat'], 'ok')
        review_request = \
            ReviewRequest.objects.get(pk=rsp['review_request']['id'])

        f = open(self._getTrophyFilename(), "r")
        self.assert_(f)
        rsp = self.apiPost(self.get_list_url(review_request), {
            'caption': 'Trophy',
            'path': f,
        })
        f.close()
        review_request.publish(self.user)

        screenshot = Screenshot.objects.get(pk=rsp['draft_screenshot']['id'])

        # Now modify the caption.
        rsp = self.apiPut(self.get_item_url(review_request, screenshot.id), {
            'caption': draft_caption,
        })

        self.assertEqual(rsp['stat'], 'ok')

        draft = review_request.get_draft(self.user)
        self.assertNotEqual(draft, None)

        screenshot = Screenshot.objects.get(pk=screenshot.id)
        self.assertEqual(screenshot.draft_caption, draft_caption)

    def test_put_screenshot_with_site(self):
        """Testing the PUT review-requests/<id>/draft/screenshots/<id>/ API with a local site"""
        draft_caption = 'The new caption'
        user = User.objects.get(username='doc')

        review_request, screenshot_id = self.test_post_screenshots_with_site()
        review_request.publish(user)

        rsp = self.apiPut(self.get_item_url(review_request, screenshot_id,
                                            self.local_site_name),
                          { 'caption': draft_caption, })
        self.assertEqual(rsp['stat'], 'ok')

        draft = review_request.get_draft(user)
        self.assertNotEqual(draft, None)

        screenshot = Screenshot.objects.get(pk=screenshot_id)
        self.assertEqual(screenshot.draft_caption, draft_caption)

    def test_put_screenshot_with_site_no_access(self):
        """Testing the PUT review-requests/<id>/draft/screenshots/<id>/ API with a local site and Permission Denied error"""
        review_request, screenshot_id = self.test_post_screenshots_with_site()
        review_request.publish(User.objects.get(username='doc'))

        self.client.logout()
        self.client.login(username='grumpy', password='grumpy')

        rsp = self.apiPut(self.get_item_url(review_request, screenshot_id,
                                            self.local_site_name),
                          { 'caption': 'test', },
                          expected_status=403)
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], PERMISSION_DENIED.code)

    def get_list_url(self, review_request, local_site_name=None):
        return local_site_reverse(
            'draft-screenshots-resource',
            local_site_name=local_site_name,
            kwargs={
                'review_request_id': review_request.display_id,
            })

    def get_item_url(self, review_request, screenshot_id, local_site_name=None):
        return local_site_reverse(
            'draft-screenshot-resource',
            local_site_name=local_site_name,
            kwargs={
                'review_request_id': review_request.display_id,
                'screenshot_id': screenshot_id,
            })


class ScreenshotResourceTests(BaseWebAPITestCase):
    """Testing the ScreenshotResource APIs."""
    def test_post_screenshots(self):
        """Testing the POST review-requests/<id>/screenshots/ API"""
        rsp = self._postNewReviewRequest()
        self.assertEqual(rsp['stat'], 'ok')
        ReviewRequest.objects.get(pk=rsp['review_request']['id'])

        screenshots_url = rsp['review_request']['links']['screenshots']['href']

        f = open(self._getTrophyFilename(), "r")
        self.assertNotEqual(f, None)
        rsp = self.apiPost(screenshots_url, {
            'path': f,
        })
        f.close()

        self.assertEqual(rsp['stat'], 'ok')

    def test_post_screenshots_with_permission_denied_error(self):
        """Testing the POST review-requests/<id>/screenshots/ API with Permission Denied error"""
        review_request = ReviewRequest.objects.filter(public=True,
            local_site=None).exclude(submitter=self.user)[0]

        f = open(self._getTrophyFilename(), "r")
        self.assert_(f)
        rsp = self.apiPost(self.get_list_url(review_request), {
            'caption': 'Trophy',
            'path': f,
        }, expected_status=403)
        f.close()

        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], PERMISSION_DENIED.code)

    def _test_review_request_with_site(self):
        self.client.logout()
        self.client.login(username='doc', password='doc')

        repo = Repository.objects.get(name='Review Board Git')
        rsp = self._postNewReviewRequest(local_site_name=self.local_site_name,
                                         repository=repo)
        self.assertEqual(rsp['stat'], 'ok')
        review_request = ReviewRequest.objects.get(
            local_site__name=self.local_site_name,
            local_id=rsp['review_request']['id'])

        return rsp['review_request']['links']['screenshots']['href']

    def test_post_screenshots_with_site(self):
        """Testing the POST review-requests/<id>/screenshots/ API with a local site"""
        screenshots_url = self._test_review_request_with_site()

        f = open(self._getTrophyFilename(), 'r')
        self.assertNotEqual(f, None)
        rsp = self.apiPost(screenshots_url, { 'path': f, })
        f.close()

        self.assertEqual(rsp['stat'], 'ok')

    def test_post_screenshots_with_site_no_access(self):
        """Testing the POST review-requests/<id>/screenshots/ API with a local site and Permission Denied error"""
        screenshots_url = self._test_review_request_with_site()
        self.client.logout()
        self.client.login(username='grumpy', password='grumpy')

        f = open(self._getTrophyFilename(), 'r')
        self.assertNotEqual(f, None)
        rsp = self.apiPost(screenshots_url,
                           { 'path': f, },
                           expected_status=403)
        f.close()

        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], PERMISSION_DENIED.code)

    @classmethod
    def get_list_url(cls, review_request, local_site_name=None):
        return local_site_reverse(
            'screenshots-resource',
            local_site_name=local_site_name,
            kwargs={
                'review_request_id': review_request.display_id,
            })


class FileDiffCommentResourceTests(BaseWebAPITestCase):
    """Testing the FileDiffCommentResource APIs."""
    def test_get_comments(self):
        """Testing the GET review-requests/<id>/diffs/<revision>/files/<id>/diff-comments/ API"""
        diff_comment_text = 'Sample comment.'

        review_request = ReviewRequest.objects.public()[0]
        diffset = review_request.diffset_history.diffsets.latest()
        filediff = diffset.files.all()[0]

        rsp = self.apiPost(ReviewResourceTests.get_list_url(review_request))
        self.assertEqual(rsp['stat'], 'ok')
        self.assertTrue('review' in rsp)
        review_id = rsp['review']['id']

        self._postNewDiffComment(review_request, review_id, diff_comment_text)

        rsp = self.apiGet(self.get_list_url(filediff))
        self.assertEqual(rsp['stat'], 'ok')

        comments = Comment.objects.filter(filediff=filediff)
        self.assertEqual(len(rsp['diff_comments']), comments.count())

        for i in range(0, len(rsp['diff_comments'])):
            self.assertEqual(rsp['diff_comments'][i]['text'], comments[i].text)

    def test_get_comments_with_site(self):
        """Testing the GET review-requests/<id>/diffs/<revision>/files/<id>/diff-comments/ API with a local site"""
        diff_comment_text = 'Sample comment.'

        self.client.logout()
        self.client.login(username='doc', password='doc')

        review_request = ReviewRequest.objects.filter(
            local_site__name=self.local_site_name)[0]
        diffset = review_request.diffset_history.diffsets.latest()
        filediff = diffset.files.all()[0]

        rsp = self.apiPost(
            ReviewResourceTests.get_list_url(review_request,
                                             self.local_site_name))
        self.assertEqual(rsp['stat'], 'ok')
        self.assertTrue('review' in rsp)
        review_id = rsp['review']['id']

        self._postNewDiffComment(review_request, review_id, diff_comment_text)

        rsp = self.apiGet(self.get_list_url(filediff, self.local_site_name))
        self.assertEqual(rsp['stat'], 'ok')

        comments = Comment.objects.filter(filediff=filediff)
        self.assertEqual(len(rsp['diff_comments']), comments.count())

        for i in range(0, len(rsp['diff_comments'])):
            self.assertEqual(rsp['diff_comments'][i]['text'], comments[i].text)

    def test_get_comments_with_site_no_access(self):
        """Testing the GET review-requests/<id>/diffs/<revision>/files/<id>/diff-comments/ API with a local site and Permission Denied error"""
        diff_comment_text = 'Sample comment.'

        self.client.logout()
        self.client.login(username='doc', password='doc')

        review_request = ReviewRequest.objects.filter(
            local_site__name=self.local_site_name)[0]
        diffset = review_request.diffset_history.diffsets.latest()
        filediff = diffset.files.all()[0]

        rsp = self.apiPost(
            ReviewResourceTests.get_list_url(review_request,
                                             self.local_site_name))
        self.assertEqual(rsp['stat'], 'ok')
        self.assertTrue('review' in rsp)
        review_id = rsp['review']['id']

        self._postNewDiffComment(review_request, review_id, diff_comment_text)

        self.client.logout()
        self.client.login(username='grumpy', password='grumpy')

        rsp = self.apiGet(self.get_list_url(filediff, self.local_site_name),
                          expected_status=403)
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], PERMISSION_DENIED.code)

    def test_get_comments_with_line(self):
        """Testing the GET review-requests/<id>/diffs/<revision>/files/<id>/diff-comments/?line= API"""
        diff_comment_text = 'Sample comment.'
        diff_comment_line = 10

        review_request = ReviewRequest.objects.public()[0]
        diffset = review_request.diffset_history.diffsets.latest()
        filediff = diffset.files.all()[0]

        rsp = self.apiPost(ReviewResourceTests.get_list_url(review_request))
        self.assertEqual(rsp['stat'], 'ok')
        self.assertTrue('review' in rsp)
        review_id = rsp['review']['id']

        self._postNewDiffComment(review_request, review_id, diff_comment_text,
                                 first_line=diff_comment_line)

        self._postNewDiffComment(review_request, review_id, diff_comment_text,
                                 first_line=diff_comment_line + 1)

        rsp = self.apiGet(self.get_list_url(filediff), {
            'line': diff_comment_line,
        })
        self.assertEqual(rsp['stat'], 'ok')

        comments = Comment.objects.filter(filediff=filediff,
                                          first_line=diff_comment_line)
        self.assertEqual(len(rsp['diff_comments']), comments.count())

        for i in range(0, len(rsp['diff_comments'])):
            self.assertEqual(rsp['diff_comments'][i]['text'], comments[i].text)
            self.assertEqual(rsp['diff_comments'][i]['first_line'],
                             comments[i].first_line)

    def get_list_url(self, filediff, local_site_name=None):
        diffset = filediff.diffset
        review_request = diffset.history.review_request.get()

        return local_site_reverse(
            'diff-comments-resource',
            local_site_name=local_site_name,
            kwargs={
                'review_request_id': review_request.display_id,
                'diff_revision': filediff.diffset.revision,
                'filediff_id': filediff.pk,
            })


class ScreenshotCommentResourceTests(BaseWebAPITestCase):
    """Testing the ScreenshotCommentResource APIs."""
    def test_get_screenshot_comments(self):
        """Testing the GET review-requests/<id>/screenshots/<id>/comments/ API"""
        comment_text = "This is a test comment."
        x, y, w, h = (2, 2, 10, 10)

        # Post the review request
        rsp = self._postNewReviewRequest()
        review_request = ReviewRequest.objects.get(
            pk=rsp['review_request']['id'])

        # Post the screenshot.
        rsp = self._postNewScreenshot(review_request)
        screenshot = Screenshot.objects.get(pk=rsp['screenshot']['id'])
        self.assertTrue('links' in rsp['screenshot'])
        self.assertTrue('screenshot_comments' in rsp['screenshot']['links'])
        comments_url = rsp['screenshot']['links']['screenshot_comments']['href']

        # Make these public.
        review_request.publish(self.user)

        # Post the review.
        rsp = self._postNewReview(review_request)
        review = Review.objects.get(pk=rsp['review']['id'])

        self._postNewScreenshotComment(review_request, review.id, screenshot,
                                      comment_text, x, y, w, h)

        rsp = self.apiGet(comments_url)
        self.assertEqual(rsp['stat'], 'ok')

        comments = ScreenshotComment.objects.filter(screenshot=screenshot)
        rsp_comments = rsp['screenshot_comments']
        self.assertEqual(len(rsp_comments), comments.count())

        for i in range(0, len(comments)):
            self.assertEqual(rsp_comments[i]['text'], comments[i].text)
            self.assertEqual(rsp_comments[i]['x'], comments[i].x)
            self.assertEqual(rsp_comments[i]['y'], comments[i].y)
            self.assertEqual(rsp_comments[i]['w'], comments[i].w)
            self.assertEqual(rsp_comments[i]['h'], comments[i].h)

    def test_get_screenshot_comments_with_site(self):
        """Testing the GET review-requests/<id>/screenshots/<id>/comments/ API with a local site"""
        comment_text = 'This is a test comment.'
        x, y, w, h = (2, 2, 10, 10)

        self.client.logout()
        self.client.login(username='doc', password='doc')

        # Post the review request.
        repo = Repository.objects.get(name='Review Board Git')
        rsp = self._postNewReviewRequest(local_site_name=self.local_site_name,
                                         repository=repo)
        self.assertEqual(rsp['stat'], 'ok')
        review_request = ReviewRequest.objects.get(
            local_site__name=self.local_site_name,
            local_id=rsp['review_request']['id'])

        # Post the screenshot.
        rsp = self._postNewScreenshot(review_request)
        screenshot = Screenshot.objects.get(pk=rsp['screenshot']['id'])
        self.assertTrue('links' in rsp['screenshot'])
        self.assertTrue('screenshot_comments' in rsp['screenshot']['links'])
        comments_url = rsp['screenshot']['links']['screenshot_comments']['href']

        # Make these public.
        review_request.publish(User.objects.get(username='doc'))

        # Post the review.
        rsp = self._postNewReview(review_request)
        review = Review.objects.get(pk=rsp['review']['id'])

        self._postNewScreenshotComment(review_request, review.id, screenshot,
                                       comment_text, x, y, w, h)

        rsp = self.apiGet(comments_url)
        self.assertEqual(rsp['stat'], 'ok')

        comments = ScreenshotComment.objects.filter(screenshot=screenshot)
        rsp_comments = rsp['screenshot_comments']
        self.assertEqual(len(rsp_comments), comments.count())

        for i in range(0, len(comments)):
            self.assertEqual(rsp_comments[i]['text'], comments[i].text)
            self.assertEqual(rsp_comments[i]['x'], comments[i].x)
            self.assertEqual(rsp_comments[i]['y'], comments[i].y)
            self.assertEqual(rsp_comments[i]['w'], comments[i].w)
            self.assertEqual(rsp_comments[i]['h'], comments[i].h)

    def test_get_screenshot_comments_with_site_no_access(self):
        """Testing the GET review-requests/<id>/screenshots/<id>/comments/ API with a local site and Permission Denied error"""
        comment_text = 'This is a test comment.'
        x, y, w, h = (2, 2, 10, 10)

        self.client.logout()
        self.client.login(username='doc', password='doc')

        # Post the review request.
        repo = Repository.objects.get(name='Review Board Git')
        rsp = self._postNewReviewRequest(local_site_name=self.local_site_name,
                                         repository=repo)
        self.assertEqual(rsp['stat'], 'ok')
        review_request = ReviewRequest.objects.get(
            local_site__name=self.local_site_name,
            local_id=rsp['review_request']['id'])

        # Post the screenshot.
        rsp = self._postNewScreenshot(review_request)
        screenshot = Screenshot.objects.get(pk=rsp['screenshot']['id'])
        self.assertTrue('links' in rsp['screenshot'])
        self.assertTrue('screenshot_comments' in rsp['screenshot']['links'])
        comments_url = rsp['screenshot']['links']['screenshot_comments']['href']

        # Make these public.
        review_request.publish(User.objects.get(username='doc'))

        # Post the review.
        rsp = self._postNewReview(review_request)
        review = Review.objects.get(pk=rsp['review']['id'])

        self._postNewScreenshotComment(review_request, review.id, screenshot,
                                       comment_text, x, y, w, h)

        self.client.logout()
        self.client.login(username='grumpy', password='grumpy')

        rsp = self.apiGet(comments_url, expected_status=403)
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], PERMISSION_DENIED.code)


class ReviewScreenshotCommentResourceTests(BaseWebAPITestCase):
    """Testing the ReviewScreenshotCommentResource APIs."""
    def test_post_screenshot_comments(self):
        """Testing the POST review-requests/<id>/reviews/<id>/screenshot-comments/ API"""
        comment_text = "This is a test comment."
        x, y, w, h = (2, 2, 10, 10)

        # Post the review request
        rsp = self._postNewReviewRequest()
        review_request = ReviewRequest.objects.get(
            pk=rsp['review_request']['id'])

        # Post the screenshot.
        rsp = self._postNewScreenshot(review_request)
        screenshot = Screenshot.objects.get(pk=rsp['screenshot']['id'])

        # Make these public.
        review_request.publish(self.user)

        # Post the review.
        rsp = self._postNewReview(review_request)
        review = Review.objects.get(pk=rsp['review']['id'])

        rsp = self._postNewScreenshotComment(review_request, review.id,
                                             screenshot, comment_text,
                                             x, y, w, h)

        self.assertEqual(rsp['screenshot_comment']['text'], comment_text)
        self.assertEqual(rsp['screenshot_comment']['x'], x)
        self.assertEqual(rsp['screenshot_comment']['y'], y)
        self.assertEqual(rsp['screenshot_comment']['w'], w)
        self.assertEqual(rsp['screenshot_comment']['h'], h)

    def test_post_screenshot_comments_with_site(self):
        """Testing the POST review-requests/<id>/reviews/<id>/screenshot-comments/ API with a local site"""
        comment_text = 'This is a test comment.'
        x, y, w, h = (2, 2, 10, 10)

        self.client.logout()
        self.client.login(username='doc', password='doc')

        # Post the review request
        repo = Repository.objects.get(name='Review Board Git')
        rsp = self._postNewReviewRequest(local_site_name=self.local_site_name,
                                         repository=repo)
        self.assertEqual(rsp['stat'], 'ok')
        review_request = ReviewRequest.objects.get(
            local_site__name=self.local_site_name,
            local_id=rsp['review_request']['id'])

        # Post the screenshot.
        rsp = self._postNewScreenshot(review_request)
        screenshot = Screenshot.objects.get(pk=rsp['screenshot']['id'])

        # Make these public.
        review_request.publish(User.objects.get(username='doc'))

        # Post the review.
        rsp = self._postNewReview(review_request)
        review = Review.objects.get(pk=rsp['review']['id'])

        rsp = self._postNewScreenshotComment(review_request, review.id,
                                             screenshot, comment_text,
                                             x, y, w, h)

        self.assertEqual(rsp['screenshot_comment']['text'], comment_text)
        self.assertEqual(rsp['screenshot_comment']['x'], x)
        self.assertEqual(rsp['screenshot_comment']['y'], y)
        self.assertEqual(rsp['screenshot_comment']['w'], w)
        self.assertEqual(rsp['screenshot_comment']['h'], h)

    def test_post_screenshot_comments_with_site_no_access(self):
        """Testing the POST review-requests/<id>/reviews/<id>/screenshot-comments/ API with a local site and Permission Denied error"""
        comment_text = 'This is a test comment.'
        x, y, w, h = (2, 2, 10, 10)

        self.client.logout()
        self.client.login(username='doc', password='doc')

        # Post the review request
        repo = Repository.objects.get(name='Review Board Git')
        rsp = self._postNewReviewRequest(local_site_name=self.local_site_name,
                                         repository=repo)
        self.assertEqual(rsp['stat'], 'ok')
        review_request = ReviewRequest.objects.get(
            local_site__name=self.local_site_name,
            local_id=rsp['review_request']['id'])

        # Post the screenshot.
        rsp = self._postNewScreenshot(review_request)
        screenshot = Screenshot.objects.get(pk=rsp['screenshot']['id'])

        # Make these public.
        review_request.publish(User.objects.get(username='doc'))

        # Post the review.
        rsp = self._postNewReview(review_request)
        review = Review.objects.get(pk=rsp['review']['id'])

        self.client.logout()
        self.client.login(username='grumpy', password='grumpy')

        rsp = self.apiPost(self.get_list_url(review, self.local_site_name),
                           { 'screenshot_id': screenshot.id, },
                           expected_status=403)
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], PERMISSION_DENIED.code)

    def test_delete_screenshot_comment(self):
        """Testing the DELETE review-requests/<id>/reviews/<id>/screenshot-comments/<id>/ API"""
        comment_text = "This is a test comment."
        x, y, w, h = (2, 2, 10, 10)

        # Post the review request
        rsp = self._postNewReviewRequest()
        review_request = ReviewRequest.objects.get(
            pk=rsp['review_request']['id'])

        # Post the screenshot.
        rsp = self._postNewScreenshot(review_request)
        screenshot = Screenshot.objects.get(pk=rsp['screenshot']['id'])

        # Make these public.
        review_request.publish(self.user)

        # Post the review.
        rsp = self._postNewReview(review_request)
        review = Review.objects.get(pk=rsp['review']['id'])
        screenshot_comments_url = \
            rsp['review']['links']['screenshot_comments']['href']

        rsp = self._postNewScreenshotComment(review_request, review.id,
                                             screenshot, comment_text,
                                             x, y, w, h)

        self.apiDelete(rsp['screenshot_comment']['links']['self']['href'])

        rsp = self.apiGet(screenshot_comments_url)
        self.assertEqual(rsp['stat'], 'ok')
        self.assertTrue('screenshot_comments' in rsp)
        self.assertEqual(len(rsp['screenshot_comments']), 0)

    def test_delete_screenshot_comment_with_local_site(self):
        """Testing the DELETE review-requests/<id>/reviews/<id>/screenshot-comments/<id> API with a local site"""
        comment_text = 'This is a test comment.'
        x, y, w, h = (2, 2, 10, 10)

        self.client.logout()
        self.client.login(username='doc', password='doc')

        # Post the review request
        repo = Repository.objects.get(name='Review Board Git')
        rsp = self._postNewReviewRequest(local_site_name=self.local_site_name,
                                         repository=repo)
        self.assertEqual(rsp['stat'], 'ok')
        review_request = ReviewRequest.objects.get(
            local_site__name=self.local_site_name,
            local_id=rsp['review_request']['id'])

        # Post the screenshot.
        rsp = self._postNewScreenshot(review_request)
        screenshot = Screenshot.objects.get(pk=rsp['screenshot']['id'])

        # Make these public.
        review_request.publish(User.objects.get(username='doc'))

        # Post the review.
        rsp = self._postNewReview(review_request)
        review = Review.objects.get(pk=rsp['review']['id'])

        screenshot_comments_url = \
            rsp['review']['links']['screenshot_comments']['href']

        rsp = self._postNewScreenshotComment(review_request, review.id,
                                             screenshot, comment_text,
                                             x, y, w, h)

        self.apiDelete(rsp['screenshot_comment']['links']['self']['href'])

        rsp = self.apiGet(screenshot_comments_url)
        self.assertEqual(rsp['stat'], 'ok')
        self.assertTrue('screenshot_comments' in rsp)
        self.assertEqual(len(rsp['screenshot_comments']), 0)

    def test_delete_screenshot_comment_with_local_site_no_access(self):
        """Testing the DELETE review-requests/<id>/reviews/<id>/screenshot-comments/<id> API with a local site and Permission Denied error"""
        comment_text = 'This is a test comment.'
        x, y, w, h = (2, 2, 10, 10)

        self.client.logout()
        self.client.login(username='doc', password='doc')

        # Post the review request
        repo = Repository.objects.get(name='Review Board Git')
        rsp = self._postNewReviewRequest(local_site_name=self.local_site_name,
                                         repository=repo)
        self.assertEqual(rsp['stat'], 'ok')
        review_request = ReviewRequest.objects.get(
            local_site__name=self.local_site_name,
            local_id=rsp['review_request']['id'])

        # Post the screenshot.
        rsp = self._postNewScreenshot(review_request)
        screenshot = Screenshot.objects.get(pk=rsp['screenshot']['id'])

        # Make these public.
        review_request.publish(User.objects.get(username='doc'))

        # Post the review.
        rsp = self._postNewReview(review_request)
        review = Review.objects.get(pk=rsp['review']['id'])

        screenshot_comments_url = \
            rsp['review']['links']['screenshot_comments']['href']

        rsp = self._postNewScreenshotComment(review_request, review.id,
                                             screenshot, comment_text,
                                             x, y, w, h)

        self.client.logout()
        self.client.login(username='grumpy', password='grumpy')

        rsp = self.apiDelete(rsp['screenshot_comment']['links']['self']['href'],
                             expected_status=403)
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], PERMISSION_DENIED.code)

    def test_delete_screenshot_comment_with_does_not_exist_error(self):
        """Testing the DELETE review-requests/<id>/reviews/<id>/screenshot-comments/<id>/ API with Does Not Exist error"""
        x, y, w, h = (2, 2, 10, 10)

        # Post the review request
        rsp = self._postNewReviewRequest()
        review_request = ReviewRequest.objects.get(
            pk=rsp['review_request']['id'])

        # Post the screenshot.
        rsp = self._postNewScreenshot(review_request)
        Screenshot.objects.get(pk=rsp['screenshot']['id'])

        # Make these public.
        review_request.publish(self.user)

        # Post the review.
        rsp = self._postNewReview(review_request)
        review = Review.objects.get(pk=rsp['review']['id'])

        self.apiDelete(self.get_item_url(review, 123), expected_status=404)

    @classmethod
    def get_list_url(cls, review, local_site_name=None):
        return local_site_reverse(
            'screenshot-comments-resource',
            local_site_name=local_site_name,
            kwargs={
                'review_request_id': review.review_request.display_id,
                'review_id': review.pk,
            })

    def get_item_url(cls, review, comment_id, local_site_name=None):
        return local_site_reverse(
            'screenshot-comment-resource',
            local_site_name=local_site_name,
            kwargs={
                'review_request_id': review.review_request.display_id,
                'review_id': review.pk,
                'comment_id': comment_id,
            })


class DeprecatedWebAPITests(TestCase, EmailTestHelper):
    """Testing the deprecated webapi support."""
    fixtures = ['test_users', 'test_reviewrequests', 'test_scmtools',
                'test_site']

    def setUp(self):
        initialize()

        siteconfig = SiteConfiguration.objects.get_current()
        siteconfig.set("mail_send_review_mail", True)
        siteconfig.save()
        mail.outbox = []

        svn_repo_path = os.path.join(os.path.dirname(__file__),
                                     '../scmtools/testdata/svn_repo')
        self.repository = Repository(name='Subversion SVN',
                                     path='file://' + svn_repo_path,
                                     tool=Tool.objects.get(name='Subversion'))
        self.repository.save()

        self.client.login(username="grumpy", password="grumpy")
        self.user = User.objects.get(username="grumpy")

    def tearDown(self):
        self.client.logout()

    def apiGet(self, path, query={}, expected_status=200):
        print "Getting /api/json/%s/" % path
        print "Query data: %s" % query
        response = self.client.get("/api/json/%s/" % path, query)
        self.assertEqual(response.status_code, expected_status)
        print "Raw response: %s" % response.content
        rsp = simplejson.loads(response.content)
        print "Response: %s" % rsp
        return rsp

    def apiPost(self, path, query={}, expected_status=200):
        print "Posting to /api/json/%s/" % path
        print "Post data: %s" % query
        response = self.client.post("/api/json/%s/" % path, query)
        self.assertEqual(response.status_code, expected_status)
        print "Raw response: %s" % response.content
        rsp = simplejson.loads(response.content)
        print "Response: %s" % rsp
        return rsp

    def testRepositoryList(self):
        """Testing the deprecated repositories API"""
        rsp = self.apiGet("repositories")
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['repositories']),
                         Repository.objects.accessible(self.user).count())

    def testUserList(self):
        """Testing the deprecated users API"""
        rsp = self.apiGet("users")
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['users']), User.objects.count())

    def testUserListQuery(self):
        """Testing the deprecated users API with custom query"""
        rsp = self.apiGet("users", {'query': 'gru'})
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['users']), 1) # grumpy

    def testGroupList(self):
        """Testing the deprecated groups API"""
        rsp = self.apiGet("groups")
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['groups']),
                         Group.objects.accessible(self.user).count())
        self.assertEqual(len(rsp['groups']), 4)

    def testGroupListQuery(self):
        """Testing the deprecated groups API with custom query"""
        rsp = self.apiGet("groups", {'query': 'dev'})
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['groups']), 1) #devgroup

    def testGroupStar(self):
        """Testing the deprecated groups/star API"""
        rsp = self.apiGet("groups/devgroup/star")
        self.assertEqual(rsp['stat'], 'ok')
        self.assert_(Group.objects.get(name="devgroup", local_site=None) in
                     self.user.get_profile().starred_groups.all())

    def testGroupStarDoesNotExist(self):
        """Testing the deprecated groups/star API with Does Not Exist error"""
        rsp = self.apiGet("groups/invalidgroup/star")
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], DOES_NOT_EXIST.code)

    def testGroupUnstar(self):
        """Testing the deprecated groups/unstar API"""
        # First, star it.
        self.testGroupStar()

        rsp = self.apiGet("groups/devgroup/unstar")
        self.assertEqual(rsp['stat'], 'ok')
        self.assert_(Group.objects.get(name="devgroup", local_site=None) not in
                     self.user.get_profile().starred_groups.all())

    def testGroupUnstarDoesNotExist(self):
        """Testing the deprecated groups/unstar API with Does Not Exist error"""
        rsp = self.apiGet("groups/invalidgroup/unstar")
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], DOES_NOT_EXIST.code)

    def testReviewRequestList(self):
        """Testing the deprecated reviewrequests/all API"""
        rsp = self.apiGet("reviewrequests/all")
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['review_requests']),
                         ReviewRequest.objects.public().count())

    def testReviewRequestListWithStatus(self):
        """Testing the deprecated reviewrequests/all API with custom status"""
        rsp = self.apiGet("reviewrequests/all", {'status': 'submitted'})
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['review_requests']),
                         ReviewRequest.objects.public(status='S').count())

        rsp = self.apiGet("reviewrequests/all", {'status': 'discarded'})
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['review_requests']),
                         ReviewRequest.objects.public(status='D').count())

        rsp = self.apiGet("reviewrequests/all", {'status': 'all'})
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['review_requests']),
                         ReviewRequest.objects.public(status=None).count())

    def testReviewRequestListCount(self):
        """Testing the deprecated reviewrequests/all/count API"""
        rsp = self.apiGet("reviewrequests/all/count")
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(rsp['count'], ReviewRequest.objects.public().count())

    def testReviewRequestsToGroup(self):
        """Testing the deprecated reviewrequests/to/group API"""
        rsp = self.apiGet("reviewrequests/to/group/devgroup")
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['review_requests']),
                         ReviewRequest.objects.to_group("devgroup",
                                                        None).count())

    def testReviewRequestsToGroupWithStatus(self):
        """Testing the deprecated reviewrequests/to/group API with custom status"""
        rsp = self.apiGet("reviewrequests/to/group/devgroup",
                          {'status': 'submitted'})
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['review_requests']),
            ReviewRequest.objects.to_group("devgroup", None,
                                           status='S').count())

        rsp = self.apiGet("reviewrequests/to/group/devgroup",
                          {'status': 'discarded'})
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['review_requests']),
            ReviewRequest.objects.to_group("devgroup", None,
                                           status='D').count())

    def testReviewRequestsToGroupCount(self):
        """Testing the deprecated reviewrequests/to/group/count API"""
        rsp = self.apiGet("reviewrequests/to/group/devgroup/count")
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(rsp['count'],
                         ReviewRequest.objects.to_group("devgroup",
                                                        None).count())

    def testReviewRequestsToUser(self):
        """Testing the deprecated reviewrequests/to/user API"""
        rsp = self.apiGet("reviewrequests/to/user/grumpy")
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['review_requests']),
                         ReviewRequest.objects.to_user("grumpy").count())

    def testReviewRequestsToUserWithStatus(self):
        """Testing the deprecated reviewrequests/to/user API with custom status"""
        rsp = self.apiGet("reviewrequests/to/user/grumpy",
                          {'status': 'submitted'})
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['review_requests']),
            ReviewRequest.objects.to_user("grumpy", status='S').count())

        rsp = self.apiGet("reviewrequests/to/user/grumpy",
                          {'status': 'discarded'})
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['review_requests']),
            ReviewRequest.objects.to_user("grumpy", status='D').count())

    def testReviewRequestsToUserCount(self):
        """Testing the deprecated reviewrequests/to/user/count API"""
        rsp = self.apiGet("reviewrequests/to/user/grumpy/count")
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(rsp['count'],
                         ReviewRequest.objects.to_user("grumpy").count())

    def testReviewRequestsToUserDirectly(self):
        """Testing the deprecated reviewrequests/to/user/directly API"""
        rsp = self.apiGet("reviewrequests/to/user/doc/directly")
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['review_requests']),
                         ReviewRequest.objects.to_user_directly("doc").count())

    def testReviewRequestsToUserDirectlyWithStatus(self):
        """Testing the deprecated reviewrequests/to/user/directly API with custom status"""
        rsp = self.apiGet("reviewrequests/to/user/doc/directly",
                          {'status': 'submitted'})
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['review_requests']),
            ReviewRequest.objects.to_user_directly("doc", status='S').count())

        rsp = self.apiGet("reviewrequests/to/user/doc/directly",
                          {'status': 'discarded'})
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['review_requests']),
            ReviewRequest.objects.to_user_directly("doc", status='D').count())

    def testReviewRequestsToUserDirectlyCount(self):
        """Testing the deprecated reviewrequests/to/user/directly/count API"""
        rsp = self.apiGet("reviewrequests/to/user/doc/directly/count")
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(rsp['count'],
                         ReviewRequest.objects.to_user_directly("doc").count())

    def testReviewRequestsFromUser(self):
        """Testing the deprecated reviewrequests/from/user API"""
        rsp = self.apiGet("reviewrequests/from/user/grumpy")
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['review_requests']),
                         ReviewRequest.objects.from_user("grumpy").count())

    def testReviewRequestsFromUserWithStatus(self):
        """Testing the deprecated reviewrequests/from/user API with custom status"""
        rsp = self.apiGet("reviewrequests/from/user/grumpy",
                          {'status': 'submitted'})
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['review_requests']),
            ReviewRequest.objects.from_user("grumpy", status='S').count())

        rsp = self.apiGet("reviewrequests/from/user/grumpy",
                          {'status': 'discarded'})
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['review_requests']),
            ReviewRequest.objects.from_user("grumpy", status='D').count())

    def testReviewRequestsFromUserCount(self):
        """Testing the deprecated reviewrequests/from/user/count API"""
        rsp = self.apiGet("reviewrequests/from/user/grumpy/count")
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(rsp['count'],
                         ReviewRequest.objects.from_user("grumpy").count())

    def testNewReviewRequest(self):
        """Testing the deprecated reviewrequests/new API"""
        rsp = self.apiPost("reviewrequests/new", {
            'repository_path': self.repository.path,
        })
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(rsp['review_request']['repository']['id'],
                         self.repository.id)

        # See if we can fetch this. Also return it for use in other
        # unit tests.
        return ReviewRequest.objects.get(pk=rsp['review_request']['id'])

    def testNewReviewRequestWithInvalidRepository(self):
        """Testing the deprecated reviewrequests/new API with Invalid Repository error"""
        rsp = self.apiPost("reviewrequests/new", {
            'repository_path': 'gobbledygook',
        })
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], INVALID_REPOSITORY.code)

    def testNewReviewRequestAsUser(self):
        """Testing the deprecated reviewrequests/new API with submit_as"""
        self.user.is_superuser = True
        self.user.save()

        rsp = self.apiPost("reviewrequests/new", {
            'repository_path': self.repository.path,
            'submit_as': 'doc',
        })
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(rsp['review_request']['repository']['id'],
                         self.repository.id)
        self.assertEqual(rsp['review_request']['submitter']['username'], 'doc')

        ReviewRequest.objects.get(pk=rsp['review_request']['id'])

    def testNewReviewRequestAsUserPermissionDenied(self):
        """Testing the deprecated reviewrequests/new API with submit_as and Permission Denied error"""
        rsp = self.apiPost("reviewrequests/new", {
            'repository_path': self.repository.path,
            'submit_as': 'doc',
        })
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], PERMISSION_DENIED.code)

    def testReviewRequest(self):
        """Testing the deprecated reviewrequests/<id> API"""
        review_request = ReviewRequest.objects.public()[0]
        rsp = self.apiGet("reviewrequests/%s" % review_request.id)
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(rsp['review_request']['id'], review_request.id)
        self.assertEqual(rsp['review_request']['summary'],
                         review_request.summary)

    def testReviewRequestPermissionDenied(self):
        """Testing the deprecated reviewrequests/<id> API with Permission Denied error"""
        review_request = ReviewRequest.objects.filter(public=False).\
            exclude(submitter=self.user)[0]
        rsp = self.apiGet("reviewrequests/%s" % review_request.id)
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], PERMISSION_DENIED.code)

    def testReviewRequestByChangenum(self):
        """Testing the deprecated reviewrequests/repository/changenum API"""
        review_request = \
            ReviewRequest.objects.filter(changenum__isnull=False)[0]
        rsp = self.apiGet("reviewrequests/repository/%s/changenum/%s" %
                          (review_request.repository.id,
                           review_request.changenum))
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(rsp['review_request']['id'], review_request.id)
        self.assertEqual(rsp['review_request']['summary'],
                         review_request.summary)
        self.assertEqual(rsp['review_request']['changenum'],
                         review_request.changenum)

    def testReviewRequestStar(self):
        """Testing the deprecated reviewrequests/star API"""
        review_request = ReviewRequest.objects.public()[0]
        rsp = self.apiGet("reviewrequests/%s/star" % review_request.id)
        self.assertEqual(rsp['stat'], 'ok')
        self.assert_(review_request in
                     self.user.get_profile().starred_review_requests.all())

    def testReviewRequestStarDoesNotExist(self):
        """Testing the deprecated reviewrequests/star API with Does Not Exist error"""
        rsp = self.apiGet("reviewrequests/999/star")
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], DOES_NOT_EXIST.code)

    def testReviewRequestUnstar(self):
        """Testing the deprecated reviewrequests/unstar API"""
        # First, star it.
        self.testReviewRequestStar()

        review_request = ReviewRequest.objects.public()[0]
        rsp = self.apiGet("reviewrequests/%s/unstar" % review_request.id)
        self.assertEqual(rsp['stat'], 'ok')
        self.assert_(review_request not in
                     self.user.get_profile().starred_review_requests.all())

    def testReviewRequestUnstarWithDoesNotExist(self):
        """Testing the deprecated reviewrequests/unstar API with Does Not Exist error"""
        rsp = self.apiGet("reviewrequests/999/unstar")
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], DOES_NOT_EXIST.code)

    def testReviewRequestDelete(self):
        """Testing the deprecated reviewrequests/delete API"""
        self.user.user_permissions.add(
            Permission.objects.get(codename='delete_reviewrequest'))
        self.user.save()
        self.assert_(self.user.has_perm('reviews.delete_reviewrequest'))

        review_request_id = \
            ReviewRequest.objects.from_user(self.user.username)[0].id
        rsp = self.apiGet("reviewrequests/%s/delete" % review_request_id)
        self.assertEqual(rsp['stat'], 'ok')
        self.assertRaises(ReviewRequest.DoesNotExist,
                          ReviewRequest.objects.get, pk=review_request_id)

    def testReviewRequestDeletePermissionDenied(self):
        """Testing the deprecated reviewrequests/delete API with Permission Denied error"""
        review_request_id = \
            ReviewRequest.objects.exclude(submitter=self.user)[0].id
        rsp = self.apiGet("reviewrequests/%s/delete" % review_request_id)
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], PERMISSION_DENIED.code)

    def testReviewRequestDeleteDoesNotExist(self):
        """Testing the deprecated reviewrequests/delete API with Does Not Exist error"""
        self.user.user_permissions.add(
            Permission.objects.get(codename='delete_reviewrequest'))
        self.user.save()
        self.assert_(self.user.has_perm('reviews.delete_reviewrequest'))

        rsp = self.apiGet("reviewrequests/999/delete")
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], DOES_NOT_EXIST.code)

    def testReviewRequestDraftSet(self):
        """Testing the deprecated reviewrequests/draft/set API"""
        summary = "My Summary"
        description = "My Description"
        testing_done = "My Testing Done"
        branch = "My Branch"
        bugs = ""

        review_request_id = \
            ReviewRequest.objects.from_user(self.user.username)[0].id
        rsp = self.apiPost("reviewrequests/%s/draft/set" % review_request_id, {
            'summary': summary,
            'description': description,
            'testing_done': testing_done,
            'branch': branch,
            'bugs_closed': bugs,
        })

        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(rsp['draft']['summary'], summary)
        self.assertEqual(rsp['draft']['description'], description)
        self.assertEqual(rsp['draft']['testing_done'], testing_done)
        self.assertEqual(rsp['draft']['branch'], branch)
        self.assertEqual(rsp['draft']['bugs_closed'], [])

        draft = ReviewRequestDraft.objects.get(pk=rsp['draft']['id'])
        self.assertEqual(draft.summary, summary)
        self.assertEqual(draft.description, description)
        self.assertEqual(draft.testing_done, testing_done)
        self.assertEqual(draft.branch, branch)
        self.assertEqual(draft.get_bug_list(), [])

    def testReviewRequestDraftSetField(self):
        """Testing the deprecated reviewrequests/draft/set/<field> API"""
        bugs_closed = '123,456'
        review_request_id = \
            ReviewRequest.objects.from_user(self.user.username)[0].id
        rsp = self.apiPost("reviewrequests/%s/draft/set/bugs_closed" %
                           review_request_id, {
            'value': bugs_closed,
        })

        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(rsp['bugs_closed'], bugs_closed.split(","))

    def testReviewRequestDraftSetFieldInvalidName(self):
        """Testing the deprecated reviewrequests/draft/set/<field> API with invalid name"""
        review_request_id = \
            ReviewRequest.objects.from_user(self.user.username)[0].id
        rsp = self.apiPost("reviewrequests/%s/draft/set/foobar" %
                           review_request_id, {
            'value': 'foo',
        })

        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], INVALID_ATTRIBUTE.code)
        self.assertEqual(rsp['attribute'], 'foobar')

    def testReviewRequestPublishSendsEmail(self):
        """Testing the deprecated reviewrequests/publish API"""
        # Set some data first.
        self.testReviewRequestDraftSet()

        review_request = ReviewRequest.objects.from_user(self.user.username)[0]

        rsp = self.apiPost("reviewrequests/%s/publish" % review_request.id)

        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(mail.outbox), 1)

    def testReviewRequestDraftSetFieldNoPermission(self):
        """Testing the deprecated reviewrequests/draft/set/<field> API without valid permissions"""
        bugs_closed = '123,456'
        review_request_id = ReviewRequest.objects.from_user('admin')[0].id
        rsp = self.apiPost("reviewrequests/%s/draft/set/bugs_closed" %
                           review_request_id, {
            'value': bugs_closed,
        })

        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], PERMISSION_DENIED.code)

    # draft/save is deprecated. Tests were copied to *DraftPublish*().
    # This is still here only to make sure we don't break backwards
    # compatibility.
    def testReviewRequestDraftSave(self):
        """Testing the deprecated reviewrequests/draft/save API"""
        # Set some data first.
        self.testReviewRequestDraftSet()

        review_request_id = \
            ReviewRequest.objects.from_user(self.user.username)[0].id
        rsp = self.apiPost("reviewrequests/%s/draft/save" % review_request_id)

        self.assertEqual(rsp['stat'], 'ok')

        review_request = ReviewRequest.objects.get(pk=review_request_id)
        self.assertEqual(review_request.summary, "My Summary")
        self.assertEqual(review_request.description, "My Description")
        self.assertEqual(review_request.testing_done, "My Testing Done")
        self.assertEqual(review_request.branch, "My Branch")

    def testReviewRequestDraftSaveDoesNotExist(self):
        """Testing the deprecated reviewrequests/draft/save API with Does Not Exist error"""
        review_request_id = \
            ReviewRequest.objects.from_user(self.user.username)[0].id
        rsp = self.apiPost("reviewrequests/%s/draft/save" % review_request_id)

        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], DOES_NOT_EXIST.code)

    def testReviewRequestDraftPublish(self):
        """Testing the deprecated reviewrequests/draft/publish API"""
        # Set some data first.
        self.testReviewRequestDraftSet()

        review_request_id = \
            ReviewRequest.objects.from_user(self.user.username)[0].id
        rsp = self.apiPost("reviewrequests/%s/draft/publish" % review_request_id)

        self.assertEqual(rsp['stat'], 'ok')

        review_request = ReviewRequest.objects.get(pk=review_request_id)
        self.assertEqual(review_request.summary, "My Summary")
        self.assertEqual(review_request.description, "My Description")
        self.assertEqual(review_request.testing_done, "My Testing Done")
        self.assertEqual(review_request.branch, "My Branch")

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "Review Request: My Summary")
        self.assertValidRecipients(["doc", "grumpy"], [])


    def testReviewRequestDraftPublishDoesNotExist(self):
        """Testing the deprecated reviewrequests/draft/publish API with Does Not Exist error"""
        review_request = ReviewRequest.objects.from_user(self.user.username)[0]
        rsp = self.apiPost("reviewrequests/%s/draft/publish" %
                           review_request.id)

        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], DOES_NOT_EXIST.code)

    def testReviewRequestDraftDiscard(self):
        """Testing the deprecated reviewrequests/draft/discard API"""
        review_request = ReviewRequest.objects.from_user(self.user.username)[0]
        summary = review_request.summary
        description = review_request.description

        # Set some data.
        self.testReviewRequestDraftSet()

        rsp = self.apiPost("reviewrequests/%s/draft/discard" %
                           review_request.id)
        self.assertEqual(rsp['stat'], 'ok')

        review_request = ReviewRequest.objects.get(pk=review_request.id)
        self.assertEqual(review_request.summary, summary)
        self.assertEqual(review_request.description, description)

    def testReviewDraftSave(self):
        """Testing the deprecated reviewrequests/reviews/draft/save API"""
        body_top = ""
        body_bottom = "My Body Bottom"
        ship_it = True

        # Clear out any reviews on the first review request we find.
        review_request = ReviewRequest.objects.public()[0]
        review_request.reviews = []
        review_request.save()

        self.apiPost("reviewrequests/%s/reviews/draft/save" %
                     review_request.id, {
            'shipit': ship_it,
            'body_top': body_top,
            'body_bottom': body_bottom,
        })

        reviews = review_request.reviews.filter(user=self.user)
        self.assertEqual(len(reviews), 1)
        review = reviews[0]

        self.assertEqual(review.ship_it, ship_it)
        self.assertEqual(review.body_top, body_top)
        self.assertEqual(review.body_bottom, body_bottom)
        self.assertEqual(review.public, False)

        self.assertEqual(len(mail.outbox), 0)

    def testReviewDraftPublish(self):
        """Testing the deprecated reviewrequests/reviews/draft/publish API"""
        body_top = "My Body Top"
        body_bottom = ""
        ship_it = True

        # Clear out any reviews on the first review request we find.
        review_request = ReviewRequest.objects.public()[0]
        review_request.reviews = []
        review_request.save()

        rsp = self.apiPost("reviewrequests/%s/reviews/draft/publish" %
                           review_request.id, {
            'shipit': ship_it,
            'body_top': body_top,
            'body_bottom': body_bottom,
        })

        self.assertEqual(rsp['stat'], 'ok')

        reviews = review_request.reviews.filter(user=self.user)
        self.assertEqual(len(reviews), 1)
        review = reviews[0]

        self.assertEqual(review.ship_it, ship_it)
        self.assertEqual(review.body_top, body_top)
        self.assertEqual(review.body_bottom, body_bottom)
        self.assertEqual(review.public, True)

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject,
                         "Re: Review Request: Interdiff Revision Test")
        self.assertValidRecipients(["admin", "grumpy"], [])


    def testReviewDraftDelete(self):
        """Testing the deprecated reviewrequests/reviews/draft/delete API"""
        # Set up the draft to delete.
        self.testReviewDraftSave()

        review_request = ReviewRequest.objects.public()[0]
        rsp = self.apiPost("reviewrequests/%s/reviews/draft/delete" %
                           review_request.id)
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(review_request.reviews.count(), 0)

    def testReviewDraftDeleteDoesNotExist(self):
        """Testing the deprecated reviewrequests/reviews/draft/delete API with Does Not Exist error"""
        # Set up the draft to delete
        self.testReviewDraftPublish()

        review_request = ReviewRequest.objects.public()[0]
        rsp = self.apiPost("reviewrequests/%s/reviews/draft/delete" %
                           review_request.id)
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], DOES_NOT_EXIST.code)

    def testReviewDraftComments(self):
        """Testing the deprecated reviewrequests/reviews/draft/comments API"""
        diff_comment_text = "Test diff comment"
        screenshot_comment_text = "Test screenshot comment"
        x, y, w, h = 2, 2, 10, 10

        screenshot = self.testNewScreenshot()
        review_request = screenshot.review_request.get()
        self.testNewDiff(review_request)
        rsp = self.apiPost("reviewrequests/%s/draft/save" % review_request.id)
        self.assertEqual(rsp['stat'], 'ok')

        self.postNewDiffComment(review_request, diff_comment_text)
        self.postNewScreenshotComment(review_request, screenshot,
                                      screenshot_comment_text, x, y, w, h)

        rsp = self.apiGet("reviewrequests/%s/reviews/draft/comments" %
                          review_request.id)
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['comments']), 1)
        self.assertEqual(len(rsp['screenshot_comments']), 1)
        self.assertEqual(rsp['comments'][0]['text'], diff_comment_text)
        self.assertEqual(rsp['screenshot_comments'][0]['text'],
                         screenshot_comment_text)

    def testReviewsList(self):
        """Testing the deprecated reviewrequests/reviews API"""
        review_request = Review.objects.all()[0].review_request
        rsp = self.apiGet("reviewrequests/%s/reviews" % review_request.id)
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['reviews']), review_request.reviews.count())

    def testReviewsListCount(self):
        """Testing the deprecated reviewrequests/reviews/count API"""
        review_request = Review.objects.all()[0].review_request
        rsp = self.apiGet("reviewrequests/%s/reviews/count" %
                          review_request.id)
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(rsp['reviews'], review_request.reviews.count())

    def testReviewCommentsList(self):
        """Testing the deprecated reviewrequests/reviews/comments API"""
        review = Review.objects.filter(comments__pk__gt=0)[0]

        rsp = self.apiGet("reviewrequests/%s/reviews/%s/comments" %
                          (review.review_request.id, review.id))
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['comments']), review.comments.count())

    def testReviewCommentsCount(self):
        """Testing the deprecated reviewrequests/reviews/comments/count API"""
        review = Review.objects.filter(comments__pk__gt=0)[0]

        rsp = self.apiGet("reviewrequests/%s/reviews/%s/comments/count" %
                          (review.review_request.id, review.id))
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(rsp['count'], review.comments.count())

    def testReplyDraftComment(self):
        """Testing the deprecated reviewrequests/reviews/replies/draft API with comment"""
        comment_text = "My Comment Text"

        comment = Comment.objects.all()[0]
        review = comment.review.get()

        rsp = self.apiPost("reviewrequests/%s/reviews/%s/replies/draft" %
                           (review.review_request.id, review.id), {
            'type': 'comment',
            'id': comment.id,
            'value': comment_text
        })

        self.assertEqual(rsp['stat'], 'ok')

        reply_comment = Comment.objects.get(pk=rsp['comment']['id'])
        self.assertEqual(reply_comment.text, comment_text)

    def testReplyDraftScreenshotComment(self):
        """Testing the deprecated reviewrequests/reviews/replies/draft API with screenshot_comment"""
        comment_text = "My Comment Text"

        comment = self.testScreenshotCommentsSet()
        review = comment.review.get()

        rsp = self.apiPost("reviewrequests/%s/reviews/%s/replies/draft" %
                           (review.review_request.id, review.id), {
            'type': 'screenshot_comment',
            'id': comment.id,
            'value': comment_text,
        })

        self.assertEqual(rsp['stat'], 'ok')

        reply_comment = ScreenshotComment.objects.get(
            pk=rsp['screenshot_comment']['id'])
        self.assertEqual(reply_comment.text, comment_text)

    def testReplyDraftBodyTop(self):
        """Testing the deprecated reviewrequests/reviews/replies/draft API with body_top"""
        body_top = 'My Body Top'

        review = \
            Review.objects.filter(base_reply_to__isnull=True, public=True)[0]

        rsp = self.apiPost("reviewrequests/%s/reviews/%s/replies/draft" %
                           (review.review_request.id, review.id), {
            'type': 'body_top',
            'value': body_top,
        })

        self.assertEqual(rsp['stat'], 'ok')

        reply = Review.objects.get(pk=rsp['reply']['id'])
        self.assertEqual(reply.body_top, body_top)

    def testReplyDraftBodyBottom(self):
        """Testing the deprecated reviewrequests/reviews/replies/draft API with body_bottom"""
        body_bottom = 'My Body Bottom'

        review = \
            Review.objects.filter(base_reply_to__isnull=True, public=True)[0]

        rsp = self.apiPost("reviewrequests/%s/reviews/%s/replies/draft" %
                           (review.review_request.id, review.id), {
            'type': 'body_bottom',
            'value': body_bottom,
        })

        self.assertEqual(rsp['stat'], 'ok')

        reply = Review.objects.get(pk=rsp['reply']['id'])
        self.assertEqual(reply.body_bottom, body_bottom)

    def testReplyDraftSave(self):
        """Testing the deprecated reviewrequests/reviews/replies/draft/save API"""
        review = \
            Review.objects.filter(base_reply_to__isnull=True, public=True)[0]

        rsp = self.apiPost("reviewrequests/%s/reviews/%s/replies/draft" %
                           (review.review_request.id, review.id), {
            'type': 'body_top',
            'value': 'Test',
        })

        self.assertEqual(rsp['stat'], 'ok')
        reply_id = rsp['reply']['id']

        rsp = self.apiPost("reviewrequests/%s/reviews/%s/replies/draft/save" %
                           (review.review_request.id, review.id))
        self.assertEqual(rsp['stat'], 'ok')

        reply = Review.objects.get(pk=reply_id)
        self.assertEqual(reply.public, True)

        self.assertEqual(len(mail.outbox), 1)

    def testReplyDraftDiscard(self):
        """Testing the deprecated reviewrequests/reviews/replies/draft/discard API"""
        review = \
            Review.objects.filter(base_reply_to__isnull=True, public=True)[0]

        rsp = self.apiPost("reviewrequests/%s/reviews/%s/replies/draft" %
                           (review.review_request.id, review.id), {
            'type': 'body_top',
            'value': 'Test',
        })

        self.assertEqual(rsp['stat'], 'ok')
        reply_id = rsp['reply']['id']

        rsp = self.apiPost(
            "reviewrequests/%s/reviews/%s/replies/draft/discard" %
            (review.review_request.id, review.id))
        self.assertEqual(rsp['stat'], 'ok')

        self.assertEqual(Review.objects.filter(pk=reply_id).count(), 0)

    def testRepliesList(self):
        """Testing the deprecated reviewrequests/reviews/replies API"""
        review = \
            Review.objects.filter(base_reply_to__isnull=True, public=True)[0]
        self.testReplyDraftSave()

        rsp = self.apiGet("reviewrequests/%s/reviews/%s/replies" %
                          (review.review_request.id, review.id))
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['replies']), len(review.public_replies()))

        for reply in review.public_replies():
            self.assertEqual(rsp['replies'][0]['id'], reply.id)
            self.assertEqual(rsp['replies'][0]['body_top'], reply.body_top)
            self.assertEqual(rsp['replies'][0]['body_bottom'],
                             reply.body_bottom)

    def testRepliesListCount(self):
        """Testing the deprecated reviewrequests/reviews/replies/count API"""
        review = \
            Review.objects.filter(base_reply_to__isnull=True, public=True)[0]
        self.testReplyDraftSave()

        rsp = self.apiGet("reviewrequests/%s/reviews/%s/replies/count" %
                          (review.review_request.id, review.id))
        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(rsp['count'], len(review.public_replies()))

    def testNewDiff(self, review_request=None):
        """Testing the deprecated reviewrequests/diff/new API"""

        if review_request is None:
            review_request = self.testNewReviewRequest()

        diff_filename = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "scmtools", "testdata", "svn_makefile.diff")
        f = open(diff_filename, "r")
        rsp = self.apiPost("reviewrequests/%s/diff/new" % review_request.id, {
            'path': f,
            'basedir': "/trunk",
        })
        f.close()

        self.assertEqual(rsp['stat'], 'ok')

        # Return this so it can be used in other tests.
        return DiffSet.objects.get(pk=rsp['diffset_id'])

    def testNewDiffInvalidFormData(self):
        """Testing the deprecated reviewrequests/diff/new API with Invalid Form Data"""
        review_request = self.testNewReviewRequest()

        rsp = self.apiPost("reviewrequests/%s/diff/new" % review_request.id)
        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], INVALID_FORM_DATA.code)
        self.assert_('path' in rsp['fields'])
        self.assert_('basedir' in rsp['fields'])

    def testNewScreenshot(self):
        """Testing the deprecated reviewrequests/screenshot/new API"""
        review_request = self.testNewReviewRequest()

        f = open(self.__getTrophyFilename(), "r")
        self.assert_(f)
        rsp = self.apiPost("reviewrequests/%s/screenshot/new" %
                           review_request.id, {
            'path': f,
        })
        f.close()

        self.assertEqual(rsp['stat'], 'ok')

        # Return the screenshot so we can use it in other tests.
        return Screenshot.objects.get(pk=rsp['screenshot_id'])

    def testNewScreenshotPermissionDenied(self):
        """Testing the deprecated reviewrequests/screenshot/new API with Permission Denied error"""
        review_request = ReviewRequest.objects.filter(public=True).\
            exclude(submitter=self.user)[0]

        f = open(self.__getTrophyFilename(), "r")
        self.assert_(f)
        rsp = self.apiPost("reviewrequests/%s/screenshot/new" %
                           review_request.id, {
            'caption': 'Trophy',
            'path': f,
        })
        f.close()

        self.assertEqual(rsp['stat'], 'fail')
        self.assertEqual(rsp['err']['code'], PERMISSION_DENIED.code)

    def postNewDiffComment(self, review_request, comment_text):
        """Utility function for posting a new diff comment."""
        diffset = review_request.diffset_history.diffsets.latest()
        filediff = diffset.files.all()[0]

        rsp = self.apiPost(
            "reviewrequests/%s/diff/%s/file/%s/line/%s/comments" %
            (review_request.id, diffset.revision, filediff.id, 10),
            {
                'action': 'set',
                'text': comment_text,
                'num_lines': 5,
            }
        )

        self.assertEqual(rsp['stat'], 'ok')

        return rsp

    def testReviewRequestDiffsets(self):
        """Testing the deprecated reviewrequests/diffsets API"""
        rsp = self.apiGet("reviewrequests/2/diff")

        self.assertEqual(rsp['diffsets'][0]["id"], 2)
        self.assertEqual(rsp['diffsets'][0]["name"], 'cleaned_data.diff')

    def testDiffCommentsSet(self):
        """Testing the deprecated reviewrequests/diff/file/line/comments set API"""
        comment_text = "This is a test comment."

        review_request = ReviewRequest.objects.public()[0]
        review_request.reviews = []

        rsp = self.postNewDiffComment(review_request, comment_text)

        self.assertEqual(len(rsp['comments']), 1)
        self.assertEqual(rsp['comments'][0]['text'], comment_text)

    def testDiffCommentsDelete(self):
        """Testing the deprecated reviewrequests/diff/file/line/comments delete API"""
        self.testDiffCommentsSet()

        review_request = ReviewRequest.objects.public()[0]
        diffset = review_request.diffset_history.diffsets.latest()
        filediff = diffset.files.all()[0]

        rsp = self.apiPost(
            "reviewrequests/%s/diff/%s/file/%s/line/%s/comments" %
            (review_request.id, diffset.revision, filediff.id, 10),
            {
                'action': 'delete',
                'num_lines': 5,
            }
        )

        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['comments']), 0)

    def testDiffCommentsList(self):
        """Testing the deprecated reviewrequests/diff/file/line/comments list API"""
        self.testDiffCommentsSet()

        review_request = ReviewRequest.objects.public()[0]
        diffset = review_request.diffset_history.diffsets.latest()
        filediff = diffset.files.all()[0]

        rsp = self.apiGet(
            "reviewrequests/%s/diff/%s/file/%s/line/%s/comments" %
            (review_request.id, diffset.revision, filediff.id, 10))

        self.assertEqual(rsp['stat'], 'ok')

        comments = Comment.objects.filter(filediff=filediff)
        self.assertEqual(len(rsp['comments']), comments.count())

        for i in range(0, len(rsp['comments'])):
            self.assertEqual(rsp['comments'][i]['text'], comments[i].text)


    def testInterDiffCommentsSet(self):
        """Testing the deprecated reviewrequests/diff/file/line/comments interdiff set API"""
        comment_text = "This is a test comment."

        # Create a review request for this test.
        review_request = self.testNewReviewRequest()

        # Upload the first diff and publish the draft.
        diffset_id = self.testNewDiff(review_request).id
        rsp = self.apiPost("reviewrequests/%s/draft/save" % review_request.id)
        self.assertEqual(rsp['stat'], 'ok')

        # Upload the second diff and publish the draft.
        interdiffset_id = self.testNewDiff(review_request).id
        rsp = self.apiPost("reviewrequests/%s/draft/save" % review_request.id)
        self.assertEqual(rsp['stat'], 'ok')

        # Reload the diffsets, now that they've been modified.
        diffset = DiffSet.objects.get(pk=diffset_id)
        interdiffset = DiffSet.objects.get(pk=interdiffset_id)

        # Get the interdiffs
        filediff = diffset.files.all()[0]
        interfilediff = interdiffset.files.all()[0]

        rsp = self.apiPost(
            "reviewrequests/%s/diff/%s-%s/file/%s-%s/line/%s/comments" %
            (review_request.id, diffset.revision, interdiffset.revision,
             filediff.id, interfilediff.id, 10),
            {
                'action': 'set',
                'text': comment_text,
                'num_lines': 5,
            }
        )

        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['comments']), 1)
        self.assertEqual(rsp['comments'][0]['text'], comment_text)

        # Return some information for use in other tests.
        return (review_request, diffset, interdiffset, filediff, interfilediff)

    def testInterDiffCommentsDelete(self):
        """Testing the deprecated reviewrequests/diff/file/line/comments interdiff delete API"""
        review_request, diffset, interdiffset, filediff, interfilediff = \
            self.testInterDiffCommentsSet()

        rsp = self.apiPost(
            "reviewrequests/%s/diff/%s-%s/file/%s-%s/line/%s/comments" %
            (review_request.id, diffset.revision, interdiffset.revision,
             filediff.id, interfilediff.id, 10),
            {
                'action': 'delete',
                'num_lines': 5,
            }
        )

        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['comments']), 0)

    def testInterDiffCommentsList(self):
        """Testing the deprecated reviewrequests/diff/file/line/comments interdiff list API"""
        review_request, diffset, interdiffset, filediff, interfilediff = \
            self.testInterDiffCommentsSet()

        rsp = self.apiGet(
            "reviewrequests/%s/diff/%s-%s/file/%s-%s/line/%s/comments" %
            (review_request.id, diffset.revision, interdiffset.revision,
             filediff.id, interfilediff.id, 10))

        self.assertEqual(rsp['stat'], 'ok')

        comments = Comment.objects.filter(filediff=filediff,
                                          interfilediff=interfilediff)
        self.assertEqual(len(rsp['comments']), comments.count())

        for i in range(0, len(rsp['comments'])):
            self.assertEqual(rsp['comments'][i]['text'], comments[i].text)

    def postNewScreenshotComment(self, review_request, screenshot,
                                 comment_text, x, y, w, h):
        """Utility function for posting a new screenshot comment."""
        rsp = self.apiPost(
            "reviewrequests/%s/s/%s/comments/%sx%s+%s+%s" %
            (review_request.id, screenshot.id, w, h, x, y),
            {
                'action': 'set',
                'text': comment_text,
            }
        )

        self.assertEqual(rsp['stat'], 'ok')
        return rsp

    def testScreenshotCommentsSet(self):
        """Testing the deprecated reviewrequests/s/comments set API"""
        comment_text = "This is a test comment."
        x, y, w, h = (2, 2, 10, 10)

        screenshot = self.testNewScreenshot()
        review_request = screenshot.review_request.get()

        rsp = self.postNewScreenshotComment(review_request, screenshot,
                                            comment_text, x, y, w, h)

        self.assertEqual(len(rsp['comments']), 1)
        self.assertEqual(rsp['comments'][0]['text'], comment_text)
        self.assertEqual(rsp['comments'][0]['x'], x)
        self.assertEqual(rsp['comments'][0]['y'], y)
        self.assertEqual(rsp['comments'][0]['w'], w)
        self.assertEqual(rsp['comments'][0]['h'], h)

        # Return this so it can be used in other tests.
        return ScreenshotComment.objects.get(pk=rsp['comments'][0]['id'])

    def testScreenshotCommentsDelete(self):
        """Testing the deprecated reviewrequests/s/comments delete API"""
        comment = self.testScreenshotCommentsSet()
        screenshot = comment.screenshot
        review_request = screenshot.review_request.get()

        rsp = self.apiPost(
            "reviewrequests/%s/s/%s/comments/%sx%s+%s+%s" %
            (review_request.id, screenshot.id, comment.w, comment.h,
             comment.x, comment.y),
            {
                'action': 'delete',
            }
        )

        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['comments']), 0)

    def testScreenshotCommentsDeleteNonExistant(self):
        """Testing the deprecated reviewrequests/s/comments delete API with non-existant comment"""
        comment = self.testScreenshotCommentsSet()
        screenshot = comment.screenshot
        review_request = screenshot.review_request.get()

        rsp = self.apiPost(
            "reviewrequests/%s/s/%s/comments/%sx%s+%s+%s" %
            (review_request.id, screenshot.id, 1, 2, 3, 4),
            {
                'action': 'delete',
            }
        )

        self.assertEqual(rsp['stat'], 'ok')
        self.assertEqual(len(rsp['comments']), 0)

    def testScreenshotCommentsList(self):
        """Testing the deprecated reviewrequests/s/comments list API"""
        comment = self.testScreenshotCommentsSet()
        screenshot = comment.screenshot
        review_request = screenshot.review_request.get()

        rsp = self.apiGet(
            "reviewrequests/%s/s/%s/comments/%sx%s+%s+%s" %
            (review_request.id, screenshot.id, comment.w, comment.h,
             comment.x, comment.y))

        self.assertEqual(rsp['stat'], 'ok')

        comments = ScreenshotComment.objects.filter(screenshot=screenshot)
        self.assertEqual(len(rsp['comments']), comments.count())

        for i in range(0, len(rsp['comments'])):
            self.assertEqual(rsp['comments'][i]['text'], comments[i].text)
            self.assertEqual(rsp['comments'][i]['x'], comments[i].x)
            self.assertEqual(rsp['comments'][i]['y'], comments[i].y)
            self.assertEqual(rsp['comments'][i]['w'], comments[i].w)
            self.assertEqual(rsp['comments'][i]['h'], comments[i].h)

    def __getTrophyFilename(self):
        return os.path.join(settings.HTDOCS_ROOT,
                            "media", "rb", "images", "trophy.png")
