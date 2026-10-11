"""Signed PWA login survives an application restart with the same secret."""
import unittest

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from musicload.web.auth import SignedSessionMiddleware, PERSISTENT_SESSION_MAX_AGE


def session_app(secret):
    app = FastAPI()
    app.add_middleware(SignedSessionMiddleware, secret_key=secret, https_only=True)

    @app.post('/sign-in')
    async def sign_in(request: Request):
        request.session['username'] = 'test-user'
        return {'ok': True}

    @app.get('/session')
    async def session(request: Request):
        return {'username': request.session.get('username')}

    @app.post('/sign-out')
    async def sign_out(request: Request):
        request.session.clear()
        return {'ok': True}

    return app


class SessionTests(unittest.TestCase):
    def test_restart_cookie_renewal_and_logout(self):
        secret = 'offline-test-secret-' * 3
        first = TestClient(session_app(secret), base_url='https://testserver')
        response = first.post('/sign-in')
        cookie = response.headers['set-cookie']
        for flag in ['HttpOnly', 'SameSite=Lax', 'Secure', f'Max-Age={PERSISTENT_SESSION_MAX_AGE}']:
            self.assertIn(flag, cookie)
        restarted = TestClient(session_app(secret), base_url='https://testserver', cookies=first.cookies)
        response = restarted.get('/session')
        self.assertEqual(response.json()['username'], 'test-user')
        self.assertIn('set-cookie', response.headers)
        restarted.post('/sign-out')
        self.assertIsNone(restarted.get('/session').json()['username'])

    def test_changed_key_and_tampered_cookie_require_login(self):
        first = TestClient(session_app('key-one'), base_url='https://testserver')
        first.post('/sign-in')
        changed = TestClient(session_app('key-two'), base_url='https://testserver', cookies=first.cookies)
        self.assertIsNone(changed.get('/session').json()['username'])
        first.cookies.set('musicload_session', 'tampered')
        self.assertIsNone(first.get('/session').json()['username'])
