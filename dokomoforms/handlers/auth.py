"""Authentication handlers."""

from collections import OrderedDict
from datetime import datetime, timedelta
import urllib.parse
import uuid

from sqlalchemy.orm.exc import NoResultFound

from tornado.escape import json_decode, json_encode
import tornado.concurrent
import tornado.web
import tornado.gen
import tornado.httpclient

from passlib.hash import bcrypt_sha256

from dokomoforms.options import options
from dokomoforms.handlers.util import BaseHandler
from dokomoforms.models import User, Email


class Login(BaseHandler):

    """POST here to verify the assertion generated by Mozilla Persona."""

    def _async_post(self,
                    http_client, url, input_data) -> tornado.concurrent.Future:
        """Asynchronously POSTs input_data to the url using http_client.fetch.

        :param http_client: the HTTP client
        :param url: the URL for POSTing
        :param input_data: the data to POST
        :return: a tornado.concurrent.Future that will contain the response
        """
        return tornado.gen.Task(
            http_client.fetch,
            url,
            method='POST',
            body=urllib.parse.urlencode(input_data),
        )

    @tornado.web.asynchronous
    @tornado.gen.engine
    def post(self):
        """POST to Mozilla's verifier service.

        Accepts:
        { "assertion": <assertion> }

        Then, POSTS to https://verifier.login.persona.org/verify to verify that
        the assertion is valid. If so, attempts to log the user in by e-mail.

        Responds with:
        200 OK
        { "email": <e-mail address> }

        :raise tornado.web.HTTPError: 400 Bad Request if the assertion is not
                                      verified
                                      422 Unprocessable Entity if the e-mail
                                      address is not associated with a user
                                      account.
        """
        assertion = self.get_argument('assertion')
        http_client = tornado.httpclient.AsyncHTTPClient()
        url = 'https://verifier.login.persona.org/verify'
        input_data = {'assertion': assertion, 'audience': self.request.host}
        response = yield self._async_post(http_client, url, input_data)
        data = json_decode(response.body)
        if data['status'] != 'okay':
            raise tornado.web.HTTPError(400, 'Failed assertion test')

        try:
            user = (
                self.session
                .query(User.id, User.name)
                .join(Email)
                .filter(Email.address == data['email'])
                .one()
            )
            cookie_options = {
                'httponly': True,
            }
            if options.https:
                cookie_options['secure'] = True
            self.set_secure_cookie(
                'user',
                json_encode({'user_id': user.id, 'user_name': user.name}),
                **cookie_options
            )
            self.write({'email': data['email']})
            self.finish()
        except NoResultFound:
            _ = self.locale.translate
            raise tornado.web.HTTPError(
                422,
                reason=_(
                    'There is no account associated with the e-mail'
                    ' address {}'.format(data['email'])
                ),
            )


class Logout(BaseHandler):

    """POST here to log out."""

    def post(self):
        """Delete the "user" cookie.

        Note that this can't be done in JavaScript because the user cookie is
        httponly.
        """
        self.clear_cookie('user')


class GenerateToken(BaseHandler):  # We should probably do this in JS

    """GET your token here. GETting twice resets the token."""

    @tornado.web.authenticated
    def get(self):
        """Set a new token for the logged in user and return the token."""
        token = (
            ''.join(char for char in str(uuid.uuid4()) if char.isalnum())
        )
        user = self.current_user_model
        with self.session.begin():
            user.token = bcrypt_sha256.encrypt(token).encode()
            user.token_expiration = datetime.now() + timedelta(days=60)
            self.session.add(user)
        self.write(OrderedDict((
            ('token', token),
            ('expires_on', user.token_expiration.isoformat()),
        )))
