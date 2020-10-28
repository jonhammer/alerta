import json
import unittest

from alerta.app import create_app


class LDAPIntegrationTestCase(unittest.TestCase):

    def setUp(self):

        test_config = {
            'TESTING': True,
            'DEBUG': True,
            'AUTH_REQUIRED': False,

            'AUTH_PROVIDER': 'ldap',
            'ALLOWED_EMAIL_DOMAINS': ['planetexpress.com'],
            'LDAP_URL': 'ldap://localhost:389',
            'LDAP_BASEDN': 'ou=people,dc=planetexpress,dc=com',
            'LDAP_BIND_USERNAME': 'cn=admin,dc=planetexpress,dc=com',
            'LDAP_BIND_PASSWORD': 'GoodNewsEveryone',

            'LDAP_USER_BASEDN': 'cn=%s,ou=people,dc=planetexpress,dc=com',
            # 'LDAP_USER_BASEDN': 'ou=people,dc=planetexpress,dc=com',
            # 'LDAP_SEARCH_QUERY': 'uid={username}',
            'LDAP_USER_ATTR': 'uid',  # sAMAccountName, cn, uid

            'LDAP_GROUP_BASEDN': 'ou=Groups,ou=people,dc=planetexpress,dc=com',
            'LDAP_SEARCH_GROUP': '(&(member={username})(objectClass=group))',
            'LDAP_GROUP_ATTR': 'memberOf', # memberOf or cn

            'LDAP_DEFAULT_DOMAIN': 'planetexpress.com'
        }
        self.app = create_app(test_config)
        self.client = self.app.test_client()

    def test_login(self):

        payload = {
            # 'email': 'bender@planetexpress.com',
            'username': 'Bender Bending Rodríguez',
            'password': 'bender'
        }

        response = self.client.post('/auth/login', data=json.dumps(payload), content_type='application/json')
        # self.assertEqual(response.status_code, 200)
        data = json.loads(response.data.decode('utf-8'))
        self.assertIn('token', data)

        token = data['token']

    # def test_login_with_ldap_domain(self):
    #
    #     payload = {
    #         'username': 'planetexpress.com\bender',
    #         'password': 'bender'
    #     }
    #
    #     response = self.client.post('/auth/login', data=json.dumps(payload), content_type='application/json')
    #     # self.assertEqual(response.status_code, 200)
    #     data = json.loads(response.data.decode('utf-8'))
    #     self.assertIn('token', data)
    #
    #     token = data['token']
    #
    # def test_login_with_no_domain(self):
    #
    #     payload = {
    #         'username': 'leela',
    #         'password': 'leela'
    #     }
    #
    #     response = self.client.post('/auth/login', data=json.dumps(payload), content_type='application/json')
    #     # self.assertEqual(response.status_code, 200)
    #     data = json.loads(response.data.decode('utf-8'))
    #     self.assertIn('token', data)
    #
    #     token = data['token']

    # def test_login_with_multi_valued_dn(self):
    #
    #     payload = {
    #         'username': 'amy',
    #         'password': 'amy'
    #     }
    #
    #     response = self.client.post('/auth/login', data=json.dumps(payload), content_type='application/json')
    #     # self.assertEqual(response.status_code, 200)
    #     data = json.loads(response.data.decode('utf-8'))
    #     self.assertIn('token', data)
    #
    #     token = data['token']
